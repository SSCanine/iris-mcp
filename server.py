"""Iris v2 MCP server.

Built alongside server.py (v1). Cutover happens by:
  1. mv server.py archive/server_v1_2026-04-27.py
  2. mv server_v2.py server.py
  3. restart Claude Code

Or test before cutover by changing the MCP config to point at server_v2.py.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any, Optional

import pyautogui

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp import Image as MCPImage

# Ensure iris/ package is importable when launched from anywhere
_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))

from iris.geometry import Rect
from iris.tokens import (
    FocusToken, default_registry, revalidate as token_revalidate, inspect as token_inspect,
)
from iris import spatial as spatial_mod
from iris import semantic as semantic_mod
from iris import vision as vision_mod
from iris import resolver as resolver_mod
from iris import verify as verify_mod
from iris import fingerprint as fingerprint_mod
from iris import launcher as launcher_mod
from iris import panels as panels_mod
from iris import recipes as recipes_mod
from iris.self_test import run_self_test as run_self_test_impl


pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.0


# ---------------------------------------------------------------------------
# Logging - file only. STDOUT/STDERR MUST stay clean for MCP stdio transport.
# ---------------------------------------------------------------------------
LOG_DIR = _HERE / "logs"
LOG_DIR.mkdir(exist_ok=True)

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
        hwnd=info.hwnd, pid=info.pid, exe_name=info.exe_name,
        title=info.title, monitor_index=monitor, bounds=info.bounds,
        fingerprint=fp,
    )
    registry.store(tk)
    return tk


# ===========================================================================
#  V1 BACKWARDS COMPAT TOOLS (keep these stable!)
# ===========================================================================
@mcp.tool()
def screenshot(monitor: int = 0, region: dict[str, int] | None = None,
               quality: int = 60) -> MCPImage:
    """Capture the screen, token-optimized (~1590 tokens). Default tool for seeing the desktop.

    Args:
        monitor: 0 = all monitors, 1 = primary, 2 = secondary.
        region: Optional {x, y, width, height} to capture a specific area.
        quality: JPEG quality 1-100 (default 60).
    """
    bounds = None
    if region:
        bounds = Rect(int(region.get("x", 0)), int(region.get("y", 0)),
                      int(region.get("width", 800)), int(region.get("height", 600)))
    img = vision_mod.capture(bounds=bounds, monitor=monitor)
    data, w, h = vision_mod.encode_jpeg(img, quality=quality, optimize_tokens=True)
    log.info("screenshot %dx%d", w, h)
    return MCPImage(data=data, format="jpeg")


@mcp.tool()
def screenshot_full(monitor: int = 0, region: dict[str, int] | None = None,
                    quality: int = 85) -> MCPImage:
    """Full-resolution screenshot. WARNING: 6K-44K+ tokens. Use only for tiny text.

    Args:
        monitor: 0 = all monitors, 1 = primary, 2 = secondary.
        region: Optional {x, y, width, height}.
        quality: JPEG quality 1-100 (default 85).
    """
    bounds = None
    if region:
        bounds = Rect(int(region.get("x", 0)), int(region.get("y", 0)),
                      int(region.get("width", 800)), int(region.get("height", 600)))
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
    """Get current cursor position."""
    x, y = pyautogui.position()
    return {"x": int(x), "y": int(y)}


@mcp.tool()
def mouse_move(x: int, y: int, duration: float = 0.0) -> dict[str, Any]:
    """Move cursor to (x, y)."""
    pyautogui.moveTo(int(x), int(y), duration=max(0.0, float(duration)))
    return {"ok": True, "x": int(x), "y": int(y)}


@mcp.tool()
def mouse_click(x: int | None = None, y: int | None = None,
                button: str = "left", clicks: int = 1) -> dict[str, Any]:
    """Click the mouse at (x, y) or current position."""
    if button not in ("left", "right", "middle"):
        raise ValueError(f"button must be left/right/middle, got {button!r}")
    kwargs: dict[str, Any] = {"button": button, "clicks": int(clicks)}
    if x is not None and y is not None:
        kwargs["x"] = int(x)
        kwargs["y"] = int(y)
    pyautogui.click(**kwargs)
    final = pyautogui.position()
    return {"ok": True, "x": int(final.x), "y": int(final.y), "button": button, "clicks": int(clicks)}


@mcp.tool()
def mouse_drag(start_x: int, start_y: int, end_x: int, end_y: int,
               button: str = "left", duration: float = 0.3) -> dict[str, Any]:
    """Drag the mouse from start to end."""
    pyautogui.moveTo(int(start_x), int(start_y), duration=0.0)
    pyautogui.dragTo(int(end_x), int(end_y), duration=max(0.0, float(duration)), button=button)
    return {"ok": True, "from": [int(start_x), int(start_y)], "to": [int(end_x), int(end_y)]}


@mcp.tool()
def mouse_scroll(amount: int, x: int | None = None, y: int | None = None) -> dict[str, Any]:
    """Scroll the mouse wheel. Positive = up, negative = down."""
    if x is not None and y is not None:
        pyautogui.moveTo(int(x), int(y), duration=0.0)
    pyautogui.scroll(int(amount))
    return {"ok": True, "amount": int(amount)}


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
def list_windows(filter: dict | None = None,
                 visible_only: bool = True, titled_only: bool = True) -> dict[str, Any]:
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
        "uia_supported": semantic_mod.supports_uia(tk.hwnd, tk.pid) if semantic_mod.HAS_UIA else False,
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
def click(token: str | None = None, target: str | None = None,
          x: int | None = None, y: int | None = None,
          button: str = "left", clicks: int = 1,
          verify: bool = False, verify_text: str | None = None,
          verify_timeout_ms: int = 2000) -> dict[str, Any]:
    """Click. Three modes:
    - click(x, y) - direct coords (legacy)
    - click(token, target='Save') - find target in focused window then click center
    - click(token) - just click center of focused window
    """
    if button not in ("left", "right", "middle"):
        raise ValueError("button must be left/right/middle")

    target_x, target_y = None, None
    backend = "direct"

    if x is not None and y is not None:
        target_x, target_y = int(x), int(y)
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
        else:
            cx, cy = tk.bounds_at_creation.center
            target_x, target_y = cx, cy
    else:
        raise ValueError("must provide x+y, or token (with optional target)")

    pyautogui.click(x=target_x, y=target_y, button=button, clicks=int(clicks))

    out: dict[str, Any] = {
        "ok": True, "x": target_x, "y": target_y, "backend": backend,
    }
    if verify and token is not None:
        tk = _resolve_token(token)
        v = verify_text or target
        if v:
            wait = verify_mod.wait_for_text(tk, v, timeout_ms=verify_timeout_ms)
            out["verified"] = wait
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
    }


@mcp.tool()
def self_test() -> dict[str, Any]:
    """Spawn the test harness, run a battery of checks, return a structured report."""
    return run_self_test_impl()


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
