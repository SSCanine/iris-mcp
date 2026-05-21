"""Semantic layer: UIA queries and pattern invocation.

Asks Windows directly what controls a window has, instead of looking at pixels.
Falls back gracefully (returns empty) for windows with no usable UIA tree.
"""
from __future__ import annotations
import time
from dataclasses import dataclass
from typing import Optional

from iris.geometry import Rect

try:
    import uiautomation as auto
    HAS_UIA = True
except ImportError:
    HAS_UIA = False


@dataclass(frozen=True)
class UIAControl:
    name: str
    role: str               # ControlTypeName, e.g. "ButtonControl"
    automation_id: str
    class_name: str
    bounds: Rect            # screen-absolute
    enabled: bool
    is_offscreen: bool
    raw: object = None      # the underlying uiautomation.Control (not serialized)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "role": self.role,
            "automation_id": self.automation_id,
            "class_name": self.class_name,
            "bounds": self.bounds.to_dict(),
            "enabled": self.enabled,
            "is_offscreen": self.is_offscreen,
        }


def _control_to_uia(control) -> Optional[UIAControl]:
    if control is None:
        return None
    try:
        rect = control.BoundingRectangle
        bounds = Rect.from_ltrb(int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
        return UIAControl(
            name=str(control.Name or ""),
            role=str(control.ControlTypeName or ""),
            automation_id=str(control.AutomationId or ""),
            class_name=str(control.ClassName or ""),
            bounds=bounds,
            enabled=bool(control.IsEnabled),
            is_offscreen=bool(control.IsOffscreen),
            raw=control,
        )
    except Exception:
        return None


# ---------------------------------------------------------------------------
# UIA-support cache (per-pid)
# ---------------------------------------------------------------------------
_UIA_SUPPORT_CACHE: dict[int, bool] = {}


def reset_uia_support_cache() -> None:
    _UIA_SUPPORT_CACHE.clear()


def control_for_hwnd(hwnd: int):
    if not HAS_UIA:
        return None
    try:
        return auto.ControlFromHandle(hwnd)
    except Exception:
        return None


def supports_uia(hwnd: int, pid: int) -> bool:
    """Probe whether this window/pid has any usable UIA children. Cached per pid."""
    if not HAS_UIA:
        return False
    cached = _UIA_SUPPORT_CACHE.get(pid)
    if cached is not None:
        return cached
    root = control_for_hwnd(hwnd)
    if root is None:
        _UIA_SUPPORT_CACHE[pid] = False
        return False
    try:
        children = root.GetChildren()
        ok = bool(children)
    except Exception:
        ok = False
    _UIA_SUPPORT_CACHE[pid] = ok
    return ok


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------
def query(
    hwnd: int,
    *,
    name: Optional[str] = None,
    role: Optional[str] = None,
    automation_id: Optional[str] = None,
    class_name: Optional[str] = None,
    max_depth: int = 8,
    max_results: int = 50,
) -> list[UIAControl]:
    """Walk the UIA tree of hwnd and return matching controls.

    Matching: any provided filter must match (case-insensitive substring for name,
    exact for role/class/automation_id).
    """
    if not HAS_UIA:
        return []
    root = control_for_hwnd(hwnd)
    if root is None:
        return []

    name_l = name.lower() if name else None
    out: list[UIAControl] = []

    def matches(c: UIAControl) -> bool:
        if name_l is not None and name_l not in c.name.lower():
            return False
        if role is not None and c.role != role:
            return False
        if automation_id is not None and c.automation_id != automation_id:
            return False
        if class_name is not None and c.class_name != class_name:
            return False
        return True

    def walk(node, depth: int):
        if depth > max_depth or len(out) >= max_results:
            return
        c = _control_to_uia(node)
        if c is not None and matches(c):
            out.append(c)
            if len(out) >= max_results:
                return
        try:
            children = node.GetChildren()
        except Exception:
            return
        for child in children:
            if len(out) >= max_results:
                break
            walk(child, depth + 1)

    walk(root, 0)
    return out


def walk_tree(hwnd: int, *, max_depth: int = 6, max_nodes: int = 500) -> list[dict]:
    """Full hierarchical UIA dump for discover(). Returns flattened list with depth."""
    if not HAS_UIA:
        return []
    root = control_for_hwnd(hwnd)
    if root is None:
        return []
    out: list[dict] = []

    def walk(node, depth: int):
        if depth > max_depth or len(out) >= max_nodes:
            return
        c = _control_to_uia(node)
        if c is not None:
            d = c.to_dict()
            d["depth"] = depth
            out.append(d)
        try:
            children = node.GetChildren()
        except Exception:
            return
        for child in children:
            if len(out) >= max_nodes:
                break
            walk(child, depth + 1)

    walk(root, 0)
    return out


# ---------------------------------------------------------------------------
# Invocation
# ---------------------------------------------------------------------------
def invoke(control: UIAControl, action: str = "click", value: Optional[str] = None) -> dict:
    """Invoke an action on a control using the right UIA pattern.

    actions:
        click   -> InvokePattern, fallback to TogglePattern, fallback to bbox click via pyautogui
        toggle  -> TogglePattern
        set     -> ValuePattern.SetValue(value)
        expand  -> ExpandCollapsePattern.Expand()
        select  -> SelectionItemPattern.Select()
    """
    if control is None or control.raw is None:
        return {"ok": False, "reason": "no_control"}
    raw = control.raw
    try:
        if action == "click":
            # Try Invoke first
            if hasattr(raw, "GetInvokePattern"):
                p = raw.GetInvokePattern()
                if p is not None:
                    p.Invoke()
                    return {"ok": True, "pattern": "invoke"}
            if hasattr(raw, "GetTogglePattern"):
                p = raw.GetTogglePattern()
                if p is not None:
                    p.Toggle()
                    return {"ok": True, "pattern": "toggle"}
            # Fallback: positional click via center of bbox
            try:
                import pyautogui
                cx, cy = control.bounds.center
                pyautogui.click(cx, cy)
                return {"ok": True, "pattern": "positional"}
            except Exception:
                return {"ok": False, "reason": "no_invoke_or_toggle_pattern"}
        elif action == "toggle":
            p = raw.GetTogglePattern()
            if p is None:
                return {"ok": False, "reason": "no_toggle_pattern"}
            p.Toggle()
            return {"ok": True, "pattern": "toggle"}
        elif action == "set":
            p = raw.GetValuePattern()
            if p is None:
                return {"ok": False, "reason": "no_value_pattern"}
            p.SetValue(value or "")
            return {"ok": True, "pattern": "value", "value": value}
        elif action == "expand":
            p = raw.GetExpandCollapsePattern()
            if p is None:
                return {"ok": False, "reason": "no_expand_collapse_pattern"}
            p.Expand()
            return {"ok": True, "pattern": "expand_collapse"}
        elif action == "select":
            p = raw.GetSelectionItemPattern()
            if p is None:
                return {"ok": False, "reason": "no_selection_item_pattern"}
            p.Select()
            return {"ok": True, "pattern": "selection_item"}
        else:
            return {"ok": False, "reason": f"unknown_action:{action}"}
    except Exception as e:
        return {"ok": False, "reason": f"exception:{type(e).__name__}:{e}"}
