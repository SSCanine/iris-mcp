"""Win32 window operations: enumeration, geometry, monitors, occlusion, popups.

Pure spatial reasoning. No pixels, no UIA. Just where windows live.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from iris.geometry import Rect

try:
    import win32con
    import win32gui
    import win32process

    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False

try:
    import psutil

    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

try:
    import mss

    HAS_MSS = True
except ImportError:
    HAS_MSS = False


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    pid: int
    exe_name: str
    title: str
    bounds: Rect
    visible: bool
    minimized: bool

    def to_dict(self) -> dict:
        return {
            "hwnd": self.hwnd,
            "pid": self.pid,
            "exe": self.exe_name,
            "title": self.title,
            "bounds": self.bounds.to_dict(),
            "visible": self.visible,
            "minimized": self.minimized,
        }


# ---------------------------------------------------------------------------
# PID -> exe name (cached, ttl 60s)
# ---------------------------------------------------------------------------
_EXE_CACHE: dict[int, tuple[str, float]] = {}
_EXE_TTL = 60.0


def _exe_for_pid(pid: int) -> str:
    if pid <= 0:
        return ""
    cached = _EXE_CACHE.get(pid)
    now = time.time()
    if cached and (now - cached[1]) < _EXE_TTL:
        return cached[0]
    name = ""
    if HAS_PSUTIL:
        try:
            name = psutil.Process(pid).name()
        except Exception:
            name = ""
    _EXE_CACHE[pid] = (name, now)
    return name


def _make_window_info(hwnd: int) -> WindowInfo | None:
    if not HAS_WIN32 or not win32gui.IsWindow(hwnd):
        return None
    title = win32gui.GetWindowText(hwnd) or ""
    visible = bool(win32gui.IsWindowVisible(hwnd))
    try:
        l, t, r, b = win32gui.GetWindowRect(hwnd)
    except Exception:
        return None
    minimized = l == -32000 and t == -32000
    bounds = Rect.from_ltrb(l, t, r, b)
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
    except Exception:
        pid = 0
    exe = _exe_for_pid(pid) if pid else ""
    return WindowInfo(
        hwnd=hwnd,
        pid=pid,
        exe_name=exe,
        title=title,
        bounds=bounds,
        visible=visible,
        minimized=minimized,
    )


def enumerate_windows(visible_only: bool = True, titled_only: bool = True) -> list[WindowInfo]:
    """List all top-level windows."""
    if not HAS_WIN32:
        raise RuntimeError("Win32 unavailable")
    out: list[WindowInfo] = []

    def cb(hwnd: int, _):
        info = _make_window_info(hwnd)
        if info is None:
            return
        if visible_only and not info.visible:
            return
        if titled_only and not info.title:
            return
        out.append(info)

    win32gui.EnumWindows(cb, None)
    return out


# ---------------------------------------------------------------------------
# match_window
# ---------------------------------------------------------------------------
def match_window(spec: dict, candidates: list[WindowInfo] | None = None) -> list[WindowInfo]:
    """Match windows by spec dict.

    spec keys (any combination):
        hwnd: int                 exact hwnd
        pid: int                  exact pid
        process: str              exe name (case-insensitive)
        title: str                exact title
        title_contains: str       substring match (case-insensitive)
        title_regex: str          regex match
    """
    pool = candidates if candidates is not None else enumerate_windows()
    out: list[WindowInfo] = []
    for w in pool:
        if "hwnd" in spec and w.hwnd != spec["hwnd"]:
            continue
        if "pid" in spec and w.pid != spec["pid"]:
            continue
        if "process" in spec and w.exe_name.lower() != str(spec["process"]).lower():
            continue
        if "title" in spec and w.title != spec["title"]:
            continue
        if "title_contains" in spec and str(spec["title_contains"]).lower() not in w.title.lower():
            continue
        if "title_regex" in spec and not re.search(spec["title_regex"], w.title):
            continue
        out.append(w)
    return out


# ---------------------------------------------------------------------------
# Monitors
# ---------------------------------------------------------------------------
_MONITOR_CACHE: list[Rect] | None = None
_MONITOR_CACHE_AT: float = 0.0
_MONITOR_TTL = 5.0


def list_monitors(force_refresh: bool = False) -> list[Rect]:
    """List physical monitor bounds (mss monitors[1:], skipping the virtual all-monitors entry).

    Index 0 is the first monitor (mss treats monitors[1] as primary).
    """
    global _MONITOR_CACHE, _MONITOR_CACHE_AT
    now = time.time()
    if not force_refresh and _MONITOR_CACHE and (now - _MONITOR_CACHE_AT) < _MONITOR_TTL:
        return _MONITOR_CACHE
    if not HAS_MSS:
        raise RuntimeError("mss unavailable")
    with mss.mss() as sct:
        rects = []
        for m in sct.monitors[1:]:
            rects.append(Rect(m["left"], m["top"], m["width"], m["height"]))
    _MONITOR_CACHE = rects
    _MONITOR_CACHE_AT = now
    return rects


def get_monitor_for_window(hwnd_or_bounds, monitors: list[Rect] | None = None) -> int:
    """Largest-area-overlap algorithm. Returns 0-based monitor index, or -1 if no overlap."""
    if monitors is None:
        monitors = list_monitors()
    if isinstance(hwnd_or_bounds, Rect):
        bounds = hwnd_or_bounds
    else:
        info = _make_window_info(int(hwnd_or_bounds))
        if info is None:
            return -1
        bounds = info.bounds
    best_idx = -1
    best_area = 0
    for i, m in enumerate(monitors):
        a = m.intersection_area(bounds)
        if a > best_area:
            best_area = a
            best_idx = i
    return best_idx


# ---------------------------------------------------------------------------
# Occlusion check (Z-order walk)
# ---------------------------------------------------------------------------
def is_occluded(hwnd: int) -> bool:
    """True if any visible non-minimized window above this hwnd intersects its bounds."""
    if not HAS_WIN32:
        raise RuntimeError("Win32 unavailable")
    target = _make_window_info(hwnd)
    if target is None or target.minimized:
        return False
    # Walk Z-order from this hwnd upward (PREV = higher Z).
    cur = hwnd
    seen = 0
    while seen < 200:
        prev = win32gui.GetWindow(cur, win32con.GW_HWNDPREV)
        if not prev:
            return False
        seen += 1
        info = _make_window_info(prev)
        cur = prev
        if info is None:
            continue
        if not info.visible or info.minimized:
            continue
        if info.bounds.intersects(target.bounds):
            return True
    return False


# ---------------------------------------------------------------------------
# Popup detection
# ---------------------------------------------------------------------------
def find_popups_for(
    pid: int, since_timestamp: float | None = None, exclude_hwnd: int | None = None
) -> list[WindowInfo]:
    """Top-level windows belonging to pid. Filtering by 'since_timestamp' isn't
    supported by Win32 directly; callers track which hwnds existed before and
    diff. We return all current windows for the pid and let caller diff."""
    out = []
    for w in enumerate_windows(visible_only=True, titled_only=False):
        if w.pid != pid:
            continue
        if exclude_hwnd is not None and w.hwnd == exclude_hwnd:
            continue
        out.append(w)
    return out


# ---------------------------------------------------------------------------
# Bring to front (with the AttachThreadInput dance)
# ---------------------------------------------------------------------------
def bring_to_front(hwnd: int) -> bool:
    """Force a window to the foreground.

    Returns True if the window is the foreground window after the call.

    Strategy (in order):
      1. Restore the window if minimized.
      2. Try a direct SetForegroundWindow (works if we already have input focus).
      3. AttachThreadInput dance: attach our thread to the foreground thread
         AND to the target thread, then issue the raise commands. This bypasses
         Windows' anti-focus-stealing protection because Windows treats attached
         threads as having shared input rights.
      4. SetWindowPos(HWND_TOPMOST) then HWND_NOTOPMOST as a final nudge.
      5. Verify by checking GetForegroundWindow().
    """
    if not HAS_WIN32:
        raise RuntimeError("Win32 unavailable")
    if not win32gui.IsWindow(hwnd):
        return False

    try:
        import win32api
    except ImportError:
        win32api = None  # type: ignore

    try:
        # 1. Restore from minimized
        try:
            placement = win32gui.GetWindowPlacement(hwnd)
            if placement and placement[1] == win32con.SW_SHOWMINIMIZED:
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        except Exception:
            pass

        # 2. Quick path: maybe we already have input rights.
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass
        if win32gui.GetForegroundWindow() == hwnd:
            return True

        # 3. AttachThreadInput dance. Get OUR thread, the foreground thread,
        #    and the target thread, then attach OUR thread to both.
        cur_thread = win32api.GetCurrentThreadId() if win32api else 0
        fg_hwnd = win32gui.GetForegroundWindow()
        if fg_hwnd:
            fg_thread, _ = win32process.GetWindowThreadProcessId(fg_hwnd)
        else:
            fg_thread = 0
        target_thread, _ = win32process.GetWindowThreadProcessId(hwnd)

        attached_fg = False
        attached_target = False
        try:
            if cur_thread and fg_thread and fg_thread != cur_thread:
                try:
                    win32process.AttachThreadInput(cur_thread, fg_thread, True)
                    attached_fg = True
                except Exception:
                    pass
            if (
                cur_thread
                and target_thread
                and target_thread != cur_thread
                and target_thread != fg_thread
            ):
                try:
                    win32process.AttachThreadInput(cur_thread, target_thread, True)
                    attached_target = True
                except Exception:
                    pass

            # 4. The topmost-toggle trick. SetWindowPos with HWND_TOPMOST
            #    followed by HWND_NOTOPMOST reliably brings a window to the
            #    top of the Z-order without leaving it as always-on-top.
            try:
                win32gui.SetWindowPos(
                    hwnd,
                    win32con.HWND_TOPMOST,
                    0,
                    0,
                    0,
                    0,
                    win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW,
                )
                win32gui.SetWindowPos(
                    hwnd,
                    win32con.HWND_NOTOPMOST,
                    0,
                    0,
                    0,
                    0,
                    win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW,
                )
            except Exception:
                pass

            try:
                win32gui.BringWindowToTop(hwnd)
            except Exception:
                pass

            try:
                win32gui.SetForegroundWindow(hwnd)
            except Exception:
                pass
        finally:
            if attached_target:
                try:
                    win32process.AttachThreadInput(cur_thread, target_thread, False)
                except Exception:
                    pass
            if attached_fg:
                try:
                    win32process.AttachThreadInput(cur_thread, fg_thread, False)
                except Exception:
                    pass

        # 5. Verify
        try:
            return win32gui.GetForegroundWindow() == hwnd
        except Exception:
            return False
    except Exception:
        return False


def wait_for_window_visible(
    hwnd: int, timeout_ms: int = 500, poll_ms: int = 25
) -> WindowInfo | None:
    """Poll for a window to materialize after a state change (e.g. restore from minimize).

    Win32's SetForegroundWindow + ShowWindow are partly async: even after they
    return, the WM may not have committed the new bounds yet. Callers that need
    fresh bounds (e.g. focus() right after bring_to_front) use this to wait.

    Returns the latest WindowInfo, or None if hwnd became invalid. Returns early
    when the window is no longer minimized AND its bounds are on-screen
    (x > -30000). On timeout, returns the most recent snapshot we got.
    Caller can check info.minimized and info.bounds.x to decide.
    """
    if not HAS_WIN32 or not win32gui.IsWindow(hwnd):
        return None
    deadline = time.time() + (timeout_ms / 1000.0)
    last: WindowInfo | None = None
    while True:
        info = _make_window_info(hwnd)
        if info is None:
            return last
        last = info
        if not info.minimized and info.bounds.x > -30000:
            return info
        if time.time() >= deadline:
            return last
        time.sleep(poll_ms / 1000.0)


def get_foreground_window_info() -> WindowInfo | None:
    if not HAS_WIN32:
        return None
    h = win32gui.GetForegroundWindow()
    if not h:
        return None
    return _make_window_info(h)


def current_bounds(hwnd: int) -> Rect | None:
    """Cheap, allocation-light read of a window's CURRENT screen-absolute bounds.

    Skips the WindowInfo wrapper (no exe-name lookup, no title read) because
    hot-path callers (resolver OCR translation, click() clamp check) only need
    the rect. Returns None for dead hwnds.
    """
    if not HAS_WIN32 or not win32gui.IsWindow(hwnd):
        return None
    try:
        l, t, r, b = win32gui.GetWindowRect(hwnd)
    except Exception:
        return None
    if l == -32000 and t == -32000:
        return None
    return Rect.from_ltrb(l, t, r, b)
