# Changelog

All notable changes to Iris will be documented in this file. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
this project adheres to semantic versioning.

## [Unreleased]

### Added
- Per-Monitor V2 DPI awareness is set at server startup so synthesized
  clicks land on the correct pixel across mixed-DPI multi-monitor setups.
  Mode visible via `iris_status().dpi_mode`.
- OCR-to-UIA widget upgrade: after OCR finds text, the resolver asks UIA
  `ControlFromPoint` and substitutes the enclosing clickable widget's
  bounds. Buttons with offset labels and list rows now click the widget
  center rather than the glyphs. Hits carry `upgraded_to_uia: true`.
- UIA Invoke fast path: when the resolved hit is a UIA control exposing
  `Invoke`/`Toggle`/`SelectionItem.Select`/`ExpandCollapse.Expand`, the
  click is delivered without mouse motion. Result includes
  `click_method: "uia_pattern"`. Disable per-call with `prefer_invoke=False`.
- `is_invoke_trusted` denylist for known-broken UIA Invoke classes
  (currently `TkTopLevel`, `TkChild`). Iris falls back to a geometric
  click while still using the upgraded widget bounds.
- SendInput-backed mouse primitives in `iris/input.py`. Atomic delivery
  so cursor move and button down/up cannot be interrupted by other
  synthesized input.
- Live window bounds: `spatial.current_bounds(hwnd)` and
  `FocusToken.current_bounds()`. OCR coord translation reads these at
  find time, not at token creation time.
- Pre-flight click clamp: `click(token, target)` refuses with
  `error: click_outside_window` when the resolved coords would land
  outside the token's current window. Off-monitor clicks refused with
  `error: click_off_screen`.
- Real-time accuracy bench at `iris.bench.runner`. Spawns an instrumented
  Tk harness, drives find+click across baseline + drag + resize +
  per-monitor + raise scenarios, measures actual landed-pixel error.
- System integration tools: clipboard get/set, list/find/kill processes
  (with force/confirm guards), Windows toast notifications, window state
  (minimize/maximize/restore/close), registry read/list/write/delete
  (writes require `confirm=True`).
- Config externalized: apps.yaml is searched via `$IRIS_APPS`, CWD, user
  config dir (`platformdirs`), then bundled example. Logs likewise
  searched via `$IRIS_LOG_DIR`, user log dir, or repo `logs/` when
  running from a source checkout.

### Changed
- `mouse_click`, `mouse_move`, `mouse_drag`, `mouse_scroll`, `mouse_pos`
  now go through `iris/input.py` (SendInput) instead of pyautogui's
  legacy `mouse_event` path. pyautogui is still used for keyboard
  primitives.

### Notes
- Public test posture: 172 unit + integration tests passing, 15 self
  tests green, bench at 100% correct-button rate on a 3-monitor mixed-DPI
  reference machine.
