"""SendInput-based mouse primitives.

The Win32 mouse_event API that pyautogui wraps is deprecated and can be filtered
or dropped by some apps (games, elevated windows, certain input hooks). SendInput
is the modern equivalent: atomic delivery, respects the active input desktop,
and survives focus-stealing protections.

Implementation notes:

* Coordinates are PHYSICAL screen pixels. The server sets
  PROCESS_PER_MONITOR_DPI_AWARE_V2 on startup so GetWindowRect, mss, and
  SendInput all agree on what "(x, y)" means. Without that flag this module
  would silently click in the wrong place on non-primary monitors.

* The absolute-coordinate calculation uses GetSystemMetrics(SM_XVIRTUALSCREEN,
  SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN) and maps to the
  0..65535 normalized space SendInput requires. The +1 in the denominator
  matches what Microsoft's example code does (avoids off-by-one at the right
  and bottom edges).

* This module deliberately does NOT depend on pyautogui or pygetwindow. We
  want a clean, minimal SendInput path so the click semantics are predictable.
"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from typing import Optional

try:
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    HAS_SENDINPUT = True
except (AttributeError, OSError):
    HAS_SENDINPUT = False


# ---------------------------------------------------------------------------
# Win32 structures
# ---------------------------------------------------------------------------
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

MOUSEEVENTF_MOVE        = 0x0001
MOUSEEVENTF_LEFTDOWN    = 0x0002
MOUSEEVENTF_LEFTUP      = 0x0004
MOUSEEVENTF_RIGHTDOWN   = 0x0008
MOUSEEVENTF_RIGHTUP     = 0x0010
MOUSEEVENTF_MIDDLEDOWN  = 0x0020
MOUSEEVENTF_MIDDLEUP    = 0x0040
MOUSEEVENTF_WHEEL       = 0x0800
MOUSEEVENTF_ABSOLUTE    = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000

SM_XVIRTUALSCREEN  = 76
SM_YVIRTUALSCREEN  = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", _MOUSEINPUT),
        ("ki", _KEYBDINPUT),
        ("hi", _HARDWAREINPUT),
    ]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [
        ("type", wintypes.DWORD),
        ("u", _INPUT_UNION),
    ]


# ---------------------------------------------------------------------------
# Coordinate normalization
# ---------------------------------------------------------------------------
def _virtual_desktop() -> tuple[int, int, int, int]:
    """Return (left, top, width, height) of the virtual desktop."""
    if not HAS_SENDINPUT:
        return (0, 0, 1, 1)
    gsm = _user32.GetSystemMetrics
    return (
        gsm(SM_XVIRTUALSCREEN),
        gsm(SM_YVIRTUALSCREEN),
        gsm(SM_CXVIRTUALSCREEN),
        gsm(SM_CYVIRTUALSCREEN),
    )


def _to_absolute(x: int, y: int) -> tuple[int, int]:
    """Map physical (x, y) into SendInput's 0..65535 virtual-desktop space.

    With MOUSEEVENTF_VIRTUALDESK set on the input flags, SendInput interprets
    dx/dy as (val * width / 65535) offsets from the virtual screen origin.
    """
    vl, vt, vw, vh = _virtual_desktop()
    # vw/vh are guaranteed > 0 on any active display config. Guard anyway.
    if vw <= 0 or vh <= 0:
        return 0, 0
    nx = int(((x - vl) * 65535) / max(vw - 1, 1))
    ny = int(((y - vt) * 65535) / max(vh - 1, 1))
    return nx, ny


# ---------------------------------------------------------------------------
# Send helpers
# ---------------------------------------------------------------------------
def _send(*events: _INPUT) -> int:
    """Submit one or more INPUT structs to SendInput. Returns events accepted."""
    if not HAS_SENDINPUT:
        return 0
    n = len(events)
    arr = (_INPUT * n)(*events)
    return _user32.SendInput(n, arr, ctypes.sizeof(_INPUT))


def _mouse_input(flags: int, x: int = 0, y: int = 0, data: int = 0) -> _INPUT:
    inp = _INPUT()
    inp.type = INPUT_MOUSE
    inp.mi = _MOUSEINPUT(
        dx=x, dy=y, mouseData=data, dwFlags=flags, time=0, dwExtraInfo=None,
    )
    return inp


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def position() -> tuple[int, int]:
    """Current cursor position in physical pixels."""
    if not HAS_SENDINPUT:
        return (0, 0)
    pt = wintypes.POINT()
    _user32.GetCursorPos(ctypes.byref(pt))
    return int(pt.x), int(pt.y)


def move(x: int, y: int) -> None:
    """Move the cursor to (x, y) in physical screen pixels. Instant, no easing."""
    if not HAS_SENDINPUT:
        return
    nx, ny = _to_absolute(int(x), int(y))
    _send(_mouse_input(
        MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
        x=nx, y=ny,
    ))


_BUTTON_FLAGS = {
    "left":   (MOUSEEVENTF_LEFTDOWN,   MOUSEEVENTF_LEFTUP),
    "right":  (MOUSEEVENTF_RIGHTDOWN,  MOUSEEVENTF_RIGHTUP),
    "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
}


def click(x: Optional[int] = None, y: Optional[int] = None,
          button: str = "left", clicks: int = 1,
          double_click_gap_ms: int = 60) -> dict:
    """Click at (x, y) or at current position.

    Atomic: cursor move and button down/up are submitted in the same SendInput
    batch when coords are given, so no other input can slip between the move
    and the press. That's the reliability win over SetCursorPos + mouse_event.
    """
    if button not in _BUTTON_FLAGS:
        raise ValueError(f"button must be left/right/middle, got {button!r}")
    if not HAS_SENDINPUT:
        return {"ok": False, "reason": "sendinput_unavailable"}
    down_flag, up_flag = _BUTTON_FLAGS[button]
    events: list[_INPUT] = []
    if x is not None and y is not None:
        nx, ny = _to_absolute(int(x), int(y))
        events.append(_mouse_input(
            MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
            x=nx, y=ny,
        ))
    # First click: emit move + down + up in one batch.
    first = list(events) + [_mouse_input(down_flag), _mouse_input(up_flag)]
    sent = _send(*first)
    # Subsequent clicks (double, triple) need a gap shorter than the system
    # double-click threshold (default 500ms) but long enough that the WM
    # registers them as separate. 60ms is the OBS / browser sweet spot.
    for _ in range(max(int(clicks) - 1, 0)):
        time.sleep(double_click_gap_ms / 1000.0)
        sent += _send(_mouse_input(down_flag), _mouse_input(up_flag))
    final_x, final_y = position()
    return {
        "ok": True, "x": final_x, "y": final_y,
        "events_sent": sent, "button": button, "clicks": int(clicks),
    }


def scroll(amount: int, x: Optional[int] = None, y: Optional[int] = None) -> dict:
    """Wheel scroll. Positive=up, negative=down. WHEEL_DELTA=120 per notch."""
    if not HAS_SENDINPUT:
        return {"ok": False, "reason": "sendinput_unavailable"}
    if x is not None and y is not None:
        move(int(x), int(y))
    delta = int(amount) * 120
    _send(_mouse_input(MOUSEEVENTF_WHEEL, data=delta))
    return {"ok": True, "amount": int(amount)}


def drag(start_x: int, start_y: int, end_x: int, end_y: int,
         button: str = "left", duration_ms: int = 250,
         steps: int = 20) -> dict:
    """Press-and-drag from start to end with stepped motion.

    Stepped motion (not a teleport) is critical because many apps interpret
    a single absolute jump as a click+release rather than a drag. The default
    20 steps over 250ms lands in the comfort zone for OBS, file explorer,
    and most browsers.
    """
    if button not in _BUTTON_FLAGS:
        raise ValueError(f"button must be left/right/middle, got {button!r}")
    if not HAS_SENDINPUT:
        return {"ok": False, "reason": "sendinput_unavailable"}
    down_flag, up_flag = _BUTTON_FLAGS[button]
    steps = max(int(steps), 1)
    duration_ms = max(int(duration_ms), 1)
    step_sleep = (duration_ms / 1000.0) / steps
    move(int(start_x), int(start_y))
    _send(_mouse_input(down_flag))
    for i in range(1, steps + 1):
        t = i / steps
        ix = int(start_x + (end_x - start_x) * t)
        iy = int(start_y + (end_y - start_y) * t)
        move(ix, iy)
        time.sleep(step_sleep)
    _send(_mouse_input(up_flag))
    return {
        "ok": True,
        "from": [int(start_x), int(start_y)],
        "to": [int(end_x), int(end_y)],
        "steps": steps, "duration_ms": duration_ms, "button": button,
    }
