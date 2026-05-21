"""Action verification: polling helpers."""
from __future__ import annotations
import time
from typing import Callable

from iris.tokens import FocusToken
from iris import resolver as resolver_mod


def _backoff_iter(start_ms: int, max_ms: int, factor: float):
    cur = start_ms
    while True:
        yield cur
        cur = min(int(cur * factor), max_ms)


def wait_for_text(token: FocusToken, text: str, *,
                  timeout_ms: int = 3000, start_interval_ms: int = 100,
                  max_interval_ms: int = 500, factor: float = 1.3) -> dict:
    """Poll resolver.find for text. Returns {found, elapsed_ms, polls}."""
    deadline = time.perf_counter() + (timeout_ms / 1000.0)
    polls = 0
    backoff = _backoff_iter(start_interval_ms, max_interval_ms, factor)
    t0 = time.perf_counter()
    while time.perf_counter() < deadline:
        polls += 1
        result = resolver_mod.find(token, text, capture_for_handoff=False)
        if result.found:
            return {
                "found": True,
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
                "polls": polls,
                "backend": result.backend,
            }
        sleep_ms = next(backoff)
        time.sleep(sleep_ms / 1000.0)
    return {
        "found": False,
        "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
        "polls": polls,
    }


def wait_for_no_text(token: FocusToken, text: str, *,
                     timeout_ms: int = 3000, start_interval_ms: int = 100,
                     max_interval_ms: int = 500, factor: float = 1.3) -> dict:
    """Poll until text is NO LONGER found (e.g. 'Start Recording' disappears after click)."""
    deadline = time.perf_counter() + (timeout_ms / 1000.0)
    polls = 0
    backoff = _backoff_iter(start_interval_ms, max_interval_ms, factor)
    t0 = time.perf_counter()
    while time.perf_counter() < deadline:
        polls += 1
        result = resolver_mod.find(token, text, capture_for_handoff=False)
        if not result.found:
            return {
                "vanished": True,
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
                "polls": polls,
            }
        sleep_ms = next(backoff)
        time.sleep(sleep_ms / 1000.0)
    return {
        "vanished": False,
        "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
        "polls": polls,
    }


def wait_for(predicate: Callable[[], bool], *,
             timeout_ms: int = 3000, interval_ms: int = 100) -> dict:
    """Generic predicate poll. Returns {ok, elapsed_ms, polls}."""
    deadline = time.perf_counter() + (timeout_ms / 1000.0)
    polls = 0
    t0 = time.perf_counter()
    while time.perf_counter() < deadline:
        polls += 1
        try:
            if predicate():
                return {
                    "ok": True,
                    "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
                    "polls": polls,
                }
        except Exception:
            pass
        time.sleep(interval_ms / 1000.0)
    return {
        "ok": False,
        "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
        "polls": polls,
    }
