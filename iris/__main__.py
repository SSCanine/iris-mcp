"""Iris MCP server entry point.

Run via:
    python -m iris        # uses this module
    iris-mcp              # console script (preferred; matches pyproject)

Both paths land here. server.py at the repo root is a thin shim kept for
people running directly from a source checkout (`python server.py`); pip
installs use this module.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# DPI awareness MUST be set BEFORE pyautogui, win32gui, mss, uiautomation,
# or anything else asks Windows for a coordinate. Once any thread has touched
# a Win32 coord API, the process's DPI mode is locked.
#
# PER_MONITOR_AWARE_V2 (-4) reports physical pixels per monitor, which is the
# only mode that works correctly on mixed-DPI multi-monitor setups (e.g. one
# display at 100%, another at 125%, a third at 150%). Older modes (System
# Aware, PMA_v1) fall back to virtualized coords on the non-primary monitors
# and synthesized clicks miss the visible target by the DPI scale factor.
# ---------------------------------------------------------------------------
def _set_dpi_awareness() -> str:
    import ctypes

    try:
        ctx = ctypes.c_void_p(-4)  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctx):
            return "per_monitor_v2"
    except (AttributeError, OSError):
        pass
    try:
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:
            return "per_monitor_v1"
    except (AttributeError, OSError):
        pass
    try:
        if ctypes.windll.user32.SetProcessDPIAware():
            return "system_aware"
    except (AttributeError, OSError):
        pass
    return "unaware"


_DPI_MODE = _set_dpi_awareness()

import pyautogui
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp import Image as MCPImage

# When running as a module inside the iris package (pip install or `python -m
# iris`), the package is already importable. The sys.path manipulation that
# the legacy server.py shim performs is unnecessary here.
_HERE = Path(__file__).parent

from iris import fingerprint as fingerprint_mod
from iris import input as input_mod
from iris import launcher as launcher_mod
from iris import panels as panels_mod
from iris import recipes as recipes_mod
from iris import resolver as resolver_mod
from iris import semantic as semantic_mod
from iris import spatial as spatial_mod
from iris import system as system_mod
from iris import verify as verify_mod
from iris import vision as vision_mod
from iris.geometry import Rect
from iris.self_test import run_self_test as run_self_test_impl
from iris.tokens import (
    FocusToken,
    default_registry,
)
from iris.tokens import (
    inspect as token_inspect,
)
from iris.tokens import (
    revalidate as token_revalidate,
)

pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.0


# ---------------------------------------------------------------------------
# Logging - file only. STDOUT/STDERR MUST stay clean for MCP stdio transport.
#
# Log location priority: $IRIS_LOG_DIR -> user data dir (when platformdirs is
# available) -> <repo>/logs (when running from a checkout). This keeps
# pip-installed instances out of site-packages but lets a develop-from-source
# checkout keep its logs co-located with the code.
# ---------------------------------------------------------------------------
def _resolve_log_dir() -> Path:
    env = os.environ.get("IRIS_LOG_DIR")
    if env:
        return Path(env)
    repo_logs = _HERE / "logs"
    if repo_logs.exists() or (_HERE / "iris").exists():
        # Running from a source checkout: keep logs alongside the code.
        return repo_logs
    try:
        from platformdirs import user_log_dir

        return Path(user_log_dir("iris-mcp"))
    except ImportError:
        return repo_logs


LOG_DIR = _resolve_log_dir()
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_DIR / "iris.log", encoding="utf-8")],
)
log = logging.getLogger("iris")


mcp = FastMCP("iris")
registry = default_registry()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _resolve_token(token_id: str) -> FocusToken:
    tk = registry.get(token_id)
    if tk is None:
        raise ValueError(f"unknown_token:{token_id}")
    return tk


def _point_on_any_monitor(x: int, y: int) -> bool:
    """True if (x, y) is inside any physical monitor's bounds.

    Used as a sanity gate before a click. Catches clicks that would land in
    the dead zone between monitors (the user's right monitor is offset 274px
    lower than primary, so (3500, 100) is on no display) or wildly off-screen.
    """
    try:
        for m in spatial_mod.list_monitors():
            if m.contains_point(x, y):
                return True
    except Exception:
        return True  # if we can't enumerate monitors, don't refuse the click
    return False


def _make_token_for_window(info) -> FocusToken:
    monitor = max(spatial_mod.get_monitor_for_window(info.bounds), 0)
    fp = None
    if semantic_mod.HAS_UIA and semantic_mod.supports_uia(info.hwnd, info.pid):
        try:
            dump = semantic_mod.walk_tree(info.hwnd, max_depth=4, max_nodes=80)
            fp = fingerprint_mod.compute_fingerprint(dump)
        except Exception:
            fp = None
    tk = FocusToken.create(
        hwnd=info.hwnd,
        pid=info.pid,
        exe_name=info.exe_name,
        title=info.title,
        monitor_index=monitor,
        bounds=info.bounds,
        fingerprint=fp,
    )
    registry.store(tk)
    return tk


# ===========================================================================
#  V1 BACKWARDS COMPAT TOOLS (keep these stable!)
# ===========================================================================
@mcp.tool()
def screenshot(
    monitor: int = 0, region: dict[str, int] | None = None, quality: int = 60
) -> MCPImage:
    """Capture the screen, token-optimized (~1590 tokens). Default tool for seeing the desktop.

    Args:
        monitor: 0 = all monitors, 1 = primary, 2 = secondary.
        region: Optional {x, y, width, height} to capture a specific area.
        quality: JPEG quality 1-100 (default 60).
    """
    bounds = None
    if region:
        bounds = Rect(
            int(region.get("x", 0)),
            int(region.get("y", 0)),
            int(region.get("width", 800)),
            int(region.get("height", 600)),
        )
    img = vision_mod.capture(bounds=bounds, monitor=monitor)
    data, w, h = vision_mod.encode_jpeg(img, quality=quality, optimize_tokens=True)
    log.info("screenshot %dx%d", w, h)
    return MCPImage(data=data, format="jpeg")


@mcp.tool()
def screenshot_full(
    monitor: int = 0, region: dict[str, int] | None = None, quality: int = 85
) -> MCPImage:
    """Full-resolution screenshot. WARNING: 6K-44K+ tokens. Use only for tiny text.

    Args:
        monitor: 0 = all monitors, 1 = primary, 2 = secondary.
        region: Optional {x, y, width, height}.
        quality: JPEG quality 1-100 (default 85).
    """
    bounds = None
    if region:
        bounds = Rect(
            int(region.get("x", 0)),
            int(region.get("y", 0)),
            int(region.get("width", 800)),
            int(region.get("height", 600)),
        )
    img = vision_mod.capture(bounds=bounds, monitor=monitor)
    data, w, h = vision_mod.encode_jpeg(img, quality=quality, optimize_tokens=False)
    log.info("screenshot_full %dx%d", w, h)
    return MCPImage(data=data, format="jpeg")


@mcp.tool()
def screenshot_window(quality: int = 60) -> MCPImage:
    """Capture only the currently-focused window. Huge token savings vs full screen.

    Uses Win32 PrintWindow so the result is the window's actual contents even
    if the window is partially covered by other windows.
    """
    info = spatial_mod.get_foreground_window_info()
    if info is None:
        raise RuntimeError("No foreground window")
    try:
        img = vision_mod.capture_window(info.hwnd)
    except Exception as e:
        log.warning("PrintWindow failed for hwnd=%d (%s), falling back to bounds", info.hwnd, e)
        img = vision_mod.capture(bounds=info.bounds)
    data, w, h = vision_mod.encode_jpeg(img, quality=quality, optimize_tokens=True)
    log.info("screenshot_window '%s' %dx%d", info.title, w, h)
    return MCPImage(data=data, format="jpeg")


@mcp.tool()
def screen_info() -> dict[str, Any]:
    """Get monitor dimensions, count, and bounding boxes."""
    monitors = spatial_mod.list_monitors()
    primary = monitors[0] if monitors else Rect(0, 0, 0, 0)
    return {
        "primary_width": primary.width,
        "primary_height": primary.height,
        "monitor_count": len(monitors),
        "monitors": [m.to_dict() for m in monitors],
        "primary_index": 0,
    }


@mcp.tool()
def mouse_pos() -> dict[str, int]:
    """Get current cursor position (physical pixels)."""
    x, y = input_mod.position()
    return {"x": int(x), "y": int(y)}


@mcp.tool()
def mouse_move(x: int, y: int, duration: float = 0.0) -> dict[str, Any]:
    """Move cursor to (x, y). duration is honored only when > 0."""
    if duration and float(duration) > 0:
        pyautogui.moveTo(int(x), int(y), duration=float(duration))
    else:
        input_mod.move(int(x), int(y))
    return {"ok": True, "x": int(x), "y": int(y)}


@mcp.tool()
def mouse_click(
    x: int | None = None, y: int | None = None, button: str = "left", clicks: int = 1
) -> dict[str, Any]:
    """Click the mouse at (x, y) or current position. Atomic SendInput delivery."""
    return input_mod.click(
        x=int(x) if x is not None else None,
        y=int(y) if y is not None else None,
        button=button,
        clicks=int(clicks),
    )


@mcp.tool()
def mouse_drag(
    start_x: int, start_y: int, end_x: int, end_y: int, button: str = "left", duration: float = 0.3
) -> dict[str, Any]:
    """Drag the mouse from start to end. Stepped motion so apps detect a drag."""
    return input_mod.drag(
        int(start_x),
        int(start_y),
        int(end_x),
        int(end_y),
        button=button,
        duration_ms=int(max(0.0, float(duration)) * 1000),
    )


@mcp.tool()
def mouse_scroll(amount: int, x: int | None = None, y: int | None = None) -> dict[str, Any]:
    """Scroll the mouse wheel. Positive = up, negative = down."""
    return input_mod.scroll(
        int(amount),
        x=int(x) if x is not None else None,
        y=int(y) if y is not None else None,
    )


@mcp.tool()
def type_text(text: str, interval: float = 0.0) -> dict[str, Any]:
    """Type text as keyboard input."""
    pyautogui.write(str(text), interval=max(0.0, float(interval)))
    return {"ok": True, "chars": len(text)}


@mcp.tool()
def press_key(key: str, modifiers: list[str] | None = None) -> dict[str, Any]:
    """Press a single key, optionally with modifiers held."""
    mods = [m.lower() for m in (modifiers or [])]
    if mods:
        pyautogui.hotkey(*mods, str(key).lower())
    else:
        pyautogui.press(str(key).lower())
    return {"ok": True, "key": key, "modifiers": mods}


@mcp.tool()
def hotkey(keys: list[str]) -> dict[str, Any]:
    """Press a keyboard combination (all keys at once)."""
    if not keys:
        raise ValueError("keys must be non-empty")
    normalized = [str(k).lower() for k in keys]
    pyautogui.hotkey(*normalized)
    return {"ok": True, "keys": normalized}


# ===========================================================================
#  V2 NEW TOOLS
# ===========================================================================
@mcp.tool()
def list_windows(
    filter: dict | None = None, visible_only: bool = True, titled_only: bool = True
) -> dict[str, Any]:
    """List top-level windows. Optionally filter by spec dict.

    filter spec: {process, title_contains, title_regex, hwnd, pid, title}
    """
    pool = spatial_mod.enumerate_windows(visible_only=visible_only, titled_only=titled_only)
    if filter:
        pool = spatial_mod.match_window(filter, candidates=pool)
    return {"count": len(pool), "windows": [w.to_dict() for w in pool[:50]]}


@mcp.tool()
def find_window(match: dict) -> dict[str, Any]:
    """Find a window by spec. Returns first match (or none)."""
    wins = spatial_mod.match_window(match)
    if not wins:
        return {"found": False, "match": match}
    w = wins[0]
    return {"found": True, "window": w.to_dict(), "all_matches": len(wins)}


@mcp.tool()
def focus(match: dict, raise_window: bool = False) -> dict[str, Any]:
    """Create a focus token for a window. Subsequent see/find/click can use it.

    Args:
        match: spec dict, e.g. {"process": "obs64.exe", "title_contains": "OBS"}
        raise_window: bring the window to front first
    """
    wins = spatial_mod.match_window(match)
    if not wins:
        return {"ok": False, "error": "no_window_matched", "match": match}
    w = wins[0]
    if raise_window:
        spatial_mod.bring_to_front(w.hwnd)
        # bring_to_front is partly async on Win32 (paint/move from -32000,-32000
        # lags). Poll for fresh bounds so the token reflects post-restore state.
        fresh = spatial_mod.wait_for_window_visible(w.hwnd, timeout_ms=500)
        if fresh is not None:
            w = fresh
    tk = _make_token_for_window(w)
    return {
        "ok": True,
        "token": tk.id,
        "monitor": tk.monitor_index,
        "bounds": tk.bounds_at_creation.to_dict(),
        "title": tk.title_at_creation,
        "exe": tk.exe_name,
        "pid": tk.pid,
        "fingerprint": tk.fingerprint,
        "uia_supported": semantic_mod.supports_uia(tk.hwnd, tk.pid)
        if semantic_mod.HAS_UIA
        else False,
    }


@mcp.tool()
def release(token: str) -> dict[str, Any]:
    """Release a focus token (cleanup)."""
    registry.remove(token)
    vision_mod.clear_ocr_cache(token)
    return {"ok": True, "released": token}


@mcp.tool()
def inspect(token: str) -> dict[str, Any]:
    """Inspect token state: alive, current bounds, monitor, occluded, popups."""
    tk = _resolve_token(token)
    return token_inspect(tk)


def _capture_token_or_bounds(tk):
    """Capture the actual window via PrintWindow; bounds fallback if it fails."""
    try:
        return vision_mod.capture_window(tk.hwnd)
    except Exception as e:
        log.warning("capture_window fallback for hwnd=%d: %s", tk.hwnd, e)
        return vision_mod.capture(bounds=tk.bounds_at_creation)


@mcp.tool()
def see(token: str | None = None, quality: int = 60) -> MCPImage:
    """See: token-optimized capture. With token = window only. Without = whole desktop.

    With a token, uses Win32 PrintWindow so the capture is the actual window
    contents even when the window is occluded by other windows.
    """
    if token is None:
        img = vision_mod.capture(monitor=0)
    else:
        tk = _resolve_token(token)
        token_revalidate(tk)
        img = _capture_token_or_bounds(tk)
    data, w, h = vision_mod.encode_jpeg(img, quality=quality, optimize_tokens=True)
    return MCPImage(data=data, format="jpeg")


@mcp.tool()
def see_full(token: str | None = None, quality: int = 85) -> MCPImage:
    """Full-resolution capture. With token = window. Without = whole desktop.

    With a token, uses Win32 PrintWindow so the capture is the actual window
    contents even when the window is occluded by other windows.
    """
    if token is None:
        img = vision_mod.capture(monitor=0)
    else:
        tk = _resolve_token(token)
        token_revalidate(tk)
        img = _capture_token_or_bounds(tk)
    data, w, h = vision_mod.encode_jpeg(img, quality=quality, optimize_tokens=False)
    return MCPImage(data=data, format="jpeg")


@mcp.tool()
def find(token: str, target: str, fuzzy: bool = True, threshold: float = 0.6) -> dict[str, Any]:
    """Find target inside the focused window. Tries UIA, then OCR, then vision handoff.

    Returns hits with backend used. If not found, includes nearest_matches and screenshot.
    """
    tk = _resolve_token(token)
    if not token_revalidate(tk):
        return {"found": False, "error": "token_dead", "token": token}
    result = resolver_mod.find(tk, target, fuzzy=fuzzy, threshold=threshold)
    return result.to_dict()


@mcp.tool()
def click(
    token: str | None = None,
    target: str | None = None,
    x: int | None = None,
    y: int | None = None,
    button: str = "left",
    clicks: int = 1,
    verify: bool = False,
    verify_text: str | None = None,
    verify_text_disappears: bool = False,
    verify_timeout_ms: int = 2000,
    prefer_invoke: bool = True,
) -> dict[str, Any]:
    """Click. Three call modes:
    - click(x, y) - direct screen coords
    - click(token, target='Save') - find target in focused window then click center
    - click(token) - click center of the focused window

    When prefer_invoke=True (default) and the resolved hit is a UIA control
    that exposes Invoke/Toggle/Select/Expand, the click is delivered via UIA
    pattern instead of moving the mouse. Pattern clicks bypass coord math,
    DPI scaling, occlusion, and animation timing. Set prefer_invoke=False
    to force a geometric mouse click (useful for testing or for apps that
    react to real input events but not synthesized UIA invokes).

    Closed-loop verification (when verify=True and a token is provided):
    - If verify_text is given AND verify_text_disappears=True, wait for that
      text to vanish (e.g. click 'Stop Recording' then wait for 'Stop
      Recording' to disappear).
    - Else if verify_text is given, wait for that text to appear (default for
      verify_text path).
    - Else, wait for the window's UIA fingerprint to change. This catches
      ANY structural effect of the click (button disappeared, label changed,
      modal closed, etc.) without the caller needing to name a specific
      post-state.

    The result includes `verified: {...}` with the poll outcome. Verification
    failure does NOT mark the click as failed (we still clicked), it just
    surfaces the timeout so the caller can decide.
    """
    if button not in ("left", "right", "middle"):
        raise ValueError("button must be left/right/middle")

    target_x, target_y = None, None
    backend = "direct"
    tk: FocusToken | None = None
    invoke_control: object | None = None  # populated when UIA invoke is viable

    if x is not None and y is not None:
        target_x, target_y = int(x), int(y)
        if token is not None:
            tk = _resolve_token(token)  # keep token for verify, but coords are raw
    elif token is not None:
        tk = _resolve_token(token)
        if not token_revalidate(tk):
            return {"ok": False, "error": "token_dead"}
        if target is not None:
            r = resolver_mod.find(tk, target)
            if not r.found:
                return {"ok": False, "error": "target_not_found", "find_result": r.to_dict()}
            top = r.hits[0]
            b = top.get("bbox") or top.get("bounds")
            target_x = b["x"] + b["width"] // 2
            target_y = b["y"] + b["height"] // 2
            backend = r.backend
            # Only meaningful for single-click left-clicks; right-click and
            # double-click semantics aren't captured by UIA patterns.
            if prefer_invoke and button == "left" and clicks == 1 and r.controls:
                ctrl = r.controls[0]
                if ctrl is not None and semantic_mod.is_invoke_trusted(ctrl):
                    invoke_control = ctrl
        else:
            live = tk.current_bounds() or tk.bounds_at_creation
            cx, cy = live.center
            target_x, target_y = cx, cy
    else:
        raise ValueError("must provide x+y, or token (with optional target)")

    # Pre-flight clamp / sanity check. A click computed from a stale token or
    # a multi-monitor coord bug will land somewhere unexpected. Refuse rather
    # than fire blind. When the caller passed token + target, the click MUST
    # land inside the token's current window; that's the whole contract.
    if tk is not None and target is not None:
        live = tk.current_bounds()
        if live is None:
            return {
                "ok": False,
                "error": "window_disappeared",
                "x": target_x,
                "y": target_y,
            }
        if not live.contains_point(target_x, target_y):
            return {
                "ok": False,
                "error": "click_outside_window",
                "x": target_x,
                "y": target_y,
                "window_bounds": live.to_dict(),
                "backend": backend,
                "hint": "target resolved to coords outside the token's current "
                "window. Window may have moved or resized since focus. "
                "Try focus() then click() again.",
            }
    # For raw click(x, y) and click(token) (no target), don't refuse but warn
    # if obviously off-screen (outside virtual desktop).
    if invoke_control is None and not _point_on_any_monitor(target_x, target_y):
        return {
            "ok": False,
            "error": "click_off_screen",
            "x": target_x,
            "y": target_y,
            "hint": "coords are outside the virtual desktop (no monitor covers this point).",
        }

    # UIA invoke fast path: no mouse motion, no pixel math. The most reliable
    # click we can deliver. If the pattern call fails, fall through to the
    # geometric click as a safety net.
    out: dict[str, Any]
    invoke_result: dict | None = None
    if invoke_control is not None:
        invoke_result = semantic_mod.try_pattern_click(invoke_control)
        if invoke_result.get("ok"):
            out = {
                "ok": True,
                "x": target_x,
                "y": target_y,
                "backend": f"{backend}+invoke",
                "click_method": "uia_pattern",
                "pattern": invoke_result["pattern"],
            }
        else:
            log.info("uia_invoke_failed,falling_back_to_mouse: %s", invoke_result)
            input_mod.click(x=target_x, y=target_y, button=button, clicks=int(clicks))
            out = {
                "ok": True,
                "x": target_x,
                "y": target_y,
                "backend": backend,
                "click_method": "mouse_after_invoke_failed",
                "invoke_attempt": invoke_result,
            }
    else:
        input_mod.click(x=target_x, y=target_y, button=button, clicks=int(clicks))
        out = {
            "ok": True,
            "x": target_x,
            "y": target_y,
            "backend": backend,
            "click_method": "mouse",
        }
    if verify and token is not None:
        tk = _resolve_token(token)
        if verify_text:
            if verify_text_disappears:
                out["verified"] = verify_mod.wait_for_no_text(
                    tk,
                    verify_text,
                    timeout_ms=verify_timeout_ms,
                )
                out["verify_mode"] = "text_disappears"
            else:
                out["verified"] = verify_mod.wait_for_text(
                    tk,
                    verify_text,
                    timeout_ms=verify_timeout_ms,
                )
                out["verify_mode"] = "text_appears"
        else:
            out["verified"] = verify_mod.wait_for_drift(
                tk,
                timeout_ms=verify_timeout_ms,
            )
            out["verify_mode"] = "fingerprint_drift"
    return out


@mcp.tool()
def launch(app: str, wait_seconds: float = 5.0) -> dict[str, Any]:
    """Launch an app from apps.yaml registry. Returns its window info if it opens."""
    return launcher_mod.launch(app, wait_seconds=wait_seconds)


@mcp.tool()
def list_apps() -> dict[str, Any]:
    """List apps registered in apps.yaml."""
    return launcher_mod.list_apps()


@mcp.tool()
def discover(token: str) -> dict[str, Any]:
    """Full ground-truth dump of a window: UIA tree + OCR text + screenshot."""
    tk = _resolve_token(token)
    if not token_revalidate(tk):
        return {"error": "token_dead"}
    out: dict[str, Any] = {"window": token_inspect(tk)}
    # UIA tree
    if semantic_mod.HAS_UIA:
        try:
            tree = semantic_mod.walk_tree(tk.hwnd, max_depth=6, max_nodes=300)
            out["uia_tree"] = tree
            out["fingerprint"] = fingerprint_mod.compute_fingerprint(tree)
        except Exception as e:
            out["uia_error"] = str(e)
    # OCR (PrintWindow capture so occluded windows still produce real text)
    img = _capture_token_or_bounds(tk)
    if vision_mod._TESSERACT_OK:
        try:
            words = vision_mod.cached_ocr(tk.id, img)
            out["ocr_text"] = [w.to_dict() for w in words]
        except Exception as e:
            out["ocr_error"] = str(e)
    # Screenshot
    try:
        data, w, h = vision_mod.encode_jpeg(img, quality=70, optimize_tokens=True)
        out["screenshot_bytes_len"] = len(data)
        out["screenshot_dims"] = [w, h]
    except Exception as e:
        out["screenshot_error"] = str(e)
    return out


@mcp.tool()
def suggest_alternatives(token: str, target: str, top_n: int = 10) -> dict[str, Any]:
    """When a target wasn't found, get ranked candidates that might be what you meant."""
    tk = _resolve_token(token)
    if not token_revalidate(tk):
        return {"error": "token_dead"}
    return resolver_mod.suggest_alternatives(tk, target, top_n=top_n)


@mcp.tool()
def discover_panels(token: str) -> dict[str, Any]:
    """List dock/panel containers in the focused window with bounds + confidence.

    Generic across Qt (QDockWidget, OBSDock), Win32, and Electron. Each panel
    reports id, name, automation_id, bounds, confidence, hidden, tabbed.
    """
    tk = _resolve_token(token)
    if not token_revalidate(tk):
        return {"error": "token_dead"}
    tree: list[dict] = []
    if semantic_mod.HAS_UIA:
        try:
            tree = semantic_mod.walk_tree(tk.hwnd, max_depth=6, max_nodes=300)
        except Exception as e:
            return {"error": f"uia_walk_failed:{e}"}
    ocr_words: list[dict] = []
    if vision_mod._TESSERACT_OK:
        try:
            img = _capture_token_or_bounds(tk)
            words = vision_mod.cached_ocr(tk.id, img)
            ocr_words = [w.to_dict() for w in words]
        except Exception:
            ocr_words = []
    win_bounds = tk.bounds_at_creation.to_dict()
    panels = panels_mod.discover_panels(tree, ocr_words, win_bounds)
    return {"panels": panels, "count": len(panels)}


@mcp.tool()
def discover_panel_items(token: str, panel: str) -> dict[str, Any]:
    """List actionable items inside a named panel (audio sources, scenes, controls...).

    panel: id, automation_id, or display name (substring match).
    """
    tk = _resolve_token(token)
    if not token_revalidate(tk):
        return {"error": "token_dead"}
    if not semantic_mod.HAS_UIA:
        return {"found": False, "reason": "uia_unavailable"}
    try:
        tree = semantic_mod.walk_tree(tk.hwnd, max_depth=8, max_nodes=600)
    except Exception as e:
        return {"error": f"uia_walk_failed:{e}"}
    return panels_mod.discover_panel_items(tree, panel)


@mcp.tool()
def wait_for(token: str, target: str, timeout_ms: int = 3000) -> dict[str, Any]:
    """Wait until target appears in focused window. Polls with backoff."""
    tk = _resolve_token(token)
    return verify_mod.wait_for_text(tk, target, timeout_ms=timeout_ms)


@mcp.tool()
def iris_status() -> dict[str, Any]:
    """Diagnostics: backend availability, OCR, UIA, cache stats, version."""
    from iris._version import __version__
    from iris.tesseract_bootstrap import locate_tesseract

    tess = locate_tesseract()
    return {
        "version": __version__,
        "win32": spatial_mod.HAS_WIN32,
        "uia": semantic_mod.HAS_UIA,
        "tesseract_ok": vision_mod._TESSERACT_OK,
        "tesseract_path": str(tess) if tess else None,
        "active_tokens": len(registry.all()),
        "ocr_cache": vision_mod.ocr_cache_stats(),
        "dpi_mode": _DPI_MODE,
    }


@mcp.tool()
def self_test() -> dict[str, Any]:
    """Spawn the test harness, run a battery of checks, return a structured report."""
    return run_self_test_impl()


# ===========================================================================
#  SYSTEM TOOLS (clipboard, processes, notifications, window state, registry)
# ===========================================================================
@mcp.tool()
def clipboard_get() -> dict[str, Any]:
    """Read text from the Windows clipboard. Returns {ok, text}."""
    return system_mod.clipboard_get()


@mcp.tool()
def clipboard_set(text: str) -> dict[str, Any]:
    """Write text to the Windows clipboard. Replaces existing contents."""
    return system_mod.clipboard_set(text)


@mcp.tool()
def list_processes(name_contains: str | None = None, limit: int = 200) -> dict[str, Any]:
    """List running processes. Filter by case-insensitive name substring."""
    return system_mod.list_processes(name_contains=name_contains, limit=int(limit))


@mcp.tool()
def find_process(name: str) -> dict[str, Any]:
    """Find processes by exact (case-insensitive) name. Useful before kill."""
    return system_mod.find_process(name)


@mcp.tool()
def kill_process(pid: int, force: bool = False) -> dict[str, Any]:
    """Terminate a process by PID. Requires force=True for safety. Refuses
    to touch kernel/system PIDs (0, 4) even with force=True."""
    return system_mod.kill_process(int(pid), force=bool(force))


@mcp.tool()
def notify(title: str, body: str = "", duration_seconds: float = 5.0) -> dict[str, Any]:
    """Show a Windows toast notification. Requires the `winsdk` package
    for the modern Toast backend."""
    return system_mod.notify(title, body, duration_seconds=float(duration_seconds))


@mcp.tool()
def window_minimize(hwnd: int) -> dict[str, Any]:
    """Minimize a window by hwnd."""
    return system_mod.window_minimize(int(hwnd))


@mcp.tool()
def window_maximize(hwnd: int) -> dict[str, Any]:
    """Maximize a window by hwnd."""
    return system_mod.window_maximize(int(hwnd))


@mcp.tool()
def window_restore(hwnd: int) -> dict[str, Any]:
    """Restore a window to normal size."""
    return system_mod.window_restore(int(hwnd))


@mcp.tool()
def window_close(hwnd: int) -> dict[str, Any]:
    """Politely close a window via WM_CLOSE. App may prompt for save."""
    return system_mod.window_close(int(hwnd))


@mcp.tool()
def registry_read(hive: str, key_path: str, value_name: str = "") -> dict[str, Any]:
    """Read a Windows registry value. hive: HKLM/HKCU/HKCR/HKU/HKCC."""
    return system_mod.registry_read(hive, key_path, value_name)


@mcp.tool()
def registry_list_values(hive: str, key_path: str) -> dict[str, Any]:
    """Enumerate all values under a registry key."""
    return system_mod.registry_list_values(hive, key_path)


@mcp.tool()
def registry_write(
    hive: str,
    key_path: str,
    value_name: str,
    value: Any,
    value_type: str = "REG_SZ",
    confirm: bool = False,
) -> dict[str, Any]:
    """Write a Windows registry value. Requires confirm=True for safety."""
    return system_mod.registry_write(
        hive,
        key_path,
        value_name,
        value,
        value_type=value_type,
        confirm=bool(confirm),
    )


@mcp.tool()
def registry_delete_value(
    hive: str, key_path: str, value_name: str, confirm: bool = False
) -> dict[str, Any]:
    """Delete a Windows registry value. Requires confirm=True."""
    return system_mod.registry_delete_value(
        hive,
        key_path,
        value_name,
        confirm=bool(confirm),
    )


# ---------------------------------------------------------------------------
# Recipes: named workflows that chain primitives. See iris/recipes.py.
# Every @mcp.tool() above is auto-registered as a recipe action below so any
# YAML in iris/recipes/ can call them by name.
# ---------------------------------------------------------------------------
def _register_actions_for_recipes() -> None:
    """Expose every @mcp.tool function in this module to the recipe engine."""
    for _name, _obj in list(globals().items()):
        if _name.startswith("_") or _name in ("main", "mcp", "log", "registry"):
            continue
        _fn = getattr(_obj, "fn", None)
        if callable(_fn):
            recipes_mod.register_action(_name, _fn)


_register_actions_for_recipes()


@mcp.tool()
def list_recipes() -> dict[str, Any]:
    """List all available recipes in iris/recipes/."""
    return {"recipes": recipes_mod.list_recipes()}


@mcp.tool()
def run_recipe(name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    """Execute a named recipe (e.g. 'obs.start_recording', 'chrome.open_url').

    Args:
        name: recipe name. Resolves to recipes/{name}.yaml or by the recipe's
              declared `name:` field.
        args: input args the recipe accepts (see each recipe's `inputs:` list).

    Returns a structured trace of each step's result, plus a top-level `ok`
    indicator. On failure, includes the failing step index, action, and error.
    """
    return recipes_mod.run_recipe(name, args=args or {})


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
def main() -> None:
    log.info("Iris v2 MCP server starting (pid=%d)", os.getpid())
    log.info("status: %s", iris_status.fn() if hasattr(iris_status, "fn") else "available")
    try:
        mcp.run()
    except Exception:
        log.exception("Iris crashed")
        raise
    finally:
        log.info("Iris stopped")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        import json

        print(json.dumps(run_self_test_impl(), indent=2))
        sys.exit(0)
    main()
