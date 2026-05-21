# Iris v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Iris v2 (vision + OCR + UIA hybrid with backend-agnostic primitives) per the approved design at `docs/specs/2026-04-27-iris-v2-design.md`.

**Architecture:** Three internal layers (Spatial, Vision, Semantic) coordinated by a Resolver. Backend-agnostic primitives so Claude calls one tool and the resolver picks UIA, OCR, or vision-handoff transparently. Stateful focus tokens with revalidation, drift detection, popup follow.

**Tech Stack:** Python 3.11+, FastMCP, mss, Pillow, pyautogui, pywin32, uiautomation, pytesseract (with bundled Tesseract binary), pyyaml, pytest.

**Working directory:** `H:\Claude\tools\iris\` (build alongside live v1 server.py, do NOT touch v1 until final cutover)

**Testing philosophy:** TDD where practical. Some Win32/UIA code requires a real Windows fixture (Tkinter test harness) rather than pure mocks. CI runs unit tests only; integration tests run locally.

---

## Task 1: Package skeleton and dependencies

**Files:**
- Create: `H:\Claude\tools\iris\iris\__init__.py`
- Create: `H:\Claude\tools\iris\iris\_version.py`
- Modify: `H:\Claude\tools\iris\requirements.txt`
- Create: `H:\Claude\tools\iris\tests\__init__.py`
- Create: `H:\Claude\tools\iris\tests\unit\__init__.py`
- Create: `H:\Claude\tools\iris\tests\integration\__init__.py`
- Create: `H:\Claude\tools\iris\tests\fixtures\__init__.py`

- [ ] **Step 1: Create directory structure**

```bash
mkdir -p H:/Claude/tools/iris/iris
mkdir -p H:/Claude/tools/iris/tests/unit
mkdir -p H:/Claude/tools/iris/tests/integration
mkdir -p H:/Claude/tools/iris/tests/fixtures
mkdir -p H:/Claude/tools/iris/vendor
mkdir -p H:/Claude/tools/iris/archive
```

- [ ] **Step 2: Create `iris/__init__.py`**

```python
"""Iris v2: vision + OCR + UIA hybrid for desktop awareness and control."""
from iris._version import __version__

__all__ = ["__version__"]
```

- [ ] **Step 3: Create `iris/_version.py`**

```python
__version__ = "2.0.0-dev"
```

- [ ] **Step 4: Create test package init files (empty)**

All `__init__.py` files in `tests/`, `tests/unit/`, `tests/integration/`, `tests/fixtures/` are empty.

- [ ] **Step 5: Update `requirements.txt`**

```
mss>=9.0.0
Pillow>=10.0.0
pyautogui>=0.9.54
pywin32>=306; sys_platform == 'win32'
mcp>=1.0.0
uiautomation>=2.0.18; sys_platform == 'win32'
pytesseract>=0.3.10
pyyaml>=6.0
pytest>=7.4.0
pytest-asyncio>=0.21.0
```

- [ ] **Step 6: Install dependencies**

Run: `C:\Users\Cenny\anaconda3\python.exe -m pip install -r H:/Claude/tools/iris/requirements.txt`
Expected: All packages install successfully

---

## Task 2: Bundled Tesseract setup

**Files:**
- Create: `H:\Claude\tools\iris\vendor\README.md`
- Create: `H:\Claude\tools\iris\iris\tesseract_bootstrap.py`

- [ ] **Step 1: Document Tesseract bundling strategy**

Write `vendor/README.md` describing how Tesseract is acquired (winget install OR portable download), where it's expected to live (`vendor/tesseract/tesseract.exe`), and the fallback chain.

- [ ] **Step 2: Write `iris/tesseract_bootstrap.py`**

```python
"""Locate Tesseract binary: bundled vendor/ first, then PATH, then well-known install dirs."""
from __future__ import annotations
import os
import shutil
from pathlib import Path

import pytesseract

VENDOR_DIR = Path(__file__).parent.parent / "vendor" / "tesseract"
WELL_KNOWN = [
    Path("C:/Program Files/Tesseract-OCR/tesseract.exe"),
    Path("C:/Program Files (x86)/Tesseract-OCR/tesseract.exe"),
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Tesseract-OCR" / "tesseract.exe",
]


def locate_tesseract() -> Path | None:
    bundled = VENDOR_DIR / "tesseract.exe"
    if bundled.exists():
        return bundled
    on_path = shutil.which("tesseract")
    if on_path:
        return Path(on_path)
    for p in WELL_KNOWN:
        if p.exists():
            return p
    return None


def configure_tesseract() -> bool:
    binary = locate_tesseract()
    if binary is None:
        return False
    pytesseract.pytesseract.tesseract_cmd = str(binary)
    return True
```

- [ ] **Step 3: Write unit test**

`tests/unit/test_tesseract_bootstrap.py`:

```python
from iris.tesseract_bootstrap import locate_tesseract, configure_tesseract

def test_locate_tesseract_returns_path_or_none():
    result = locate_tesseract()
    assert result is None or result.exists()

def test_configure_tesseract_returns_bool():
    assert isinstance(configure_tesseract(), bool)
```

- [ ] **Step 4: Run test**

Run: `cd H:/Claude/tools/iris && python -m pytest tests/unit/test_tesseract_bootstrap.py -v`
Expected: PASS (will report whether Tesseract was found in environment)

- [ ] **Step 5: If Tesseract missing, install via winget**

```bash
winget install --id UB-Mannheim.TesseractOCR
```

(Alternative: download portable Tesseract zip and extract to `vendor/tesseract/`)

---

## Task 3: Geometry primitives and Rect dataclass

**Files:**
- Create: `H:\Claude\tools\iris\iris\geometry.py`
- Create: `H:\Claude\tools\iris\tests\unit\test_geometry.py`

- [ ] **Step 1: Write failing tests**

```python
from iris.geometry import Rect

def test_rect_from_ltrb():
    r = Rect.from_ltrb(10, 20, 30, 40)
    assert r.x == 10
    assert r.y == 20
    assert r.width == 20
    assert r.height == 20

def test_rect_intersects():
    a = Rect(0, 0, 100, 100)
    b = Rect(50, 50, 100, 100)
    assert a.intersects(b)

def test_rect_does_not_intersect():
    a = Rect(0, 0, 50, 50)
    b = Rect(100, 100, 50, 50)
    assert not a.intersects(b)

def test_rect_intersection_area():
    a = Rect(0, 0, 100, 100)
    b = Rect(50, 50, 100, 100)
    assert a.intersection_area(b) == 50 * 50

def test_rect_center():
    r = Rect(0, 0, 100, 200)
    assert r.center == (50, 100)

def test_rect_to_dict():
    r = Rect(10, 20, 30, 40)
    assert r.to_dict() == {"x": 10, "y": 20, "width": 30, "height": 40}
```

- [ ] **Step 2: Write geometry.py**

```python
"""Pure geometry primitives. No Win32, no IO."""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int

    @classmethod
    def from_ltrb(cls, left: int, top: int, right: int, bottom: int) -> "Rect":
        return cls(left, top, right - left, bottom - top)

    @property
    def left(self) -> int:
        return self.x

    @property
    def top(self) -> int:
        return self.y

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    @property
    def center(self) -> tuple[int, int]:
        return (self.x + self.width // 2, self.y + self.height // 2)

    @property
    def area(self) -> int:
        return self.width * self.height

    def intersects(self, other: "Rect") -> bool:
        return not (
            self.right <= other.left
            or other.right <= self.left
            or self.bottom <= other.top
            or other.bottom <= self.top
        )

    def intersection_area(self, other: "Rect") -> int:
        if not self.intersects(other):
            return 0
        ix = max(self.left, other.left)
        iy = max(self.top, other.top)
        ir = min(self.right, other.right)
        ib = min(self.bottom, other.bottom)
        return (ir - ix) * (ib - iy)

    def to_dict(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}

    def contains_point(self, x: int, y: int) -> bool:
        return self.left <= x < self.right and self.top <= y < self.bottom
```

- [ ] **Step 3: Run tests**

Run: `cd H:/Claude/tools/iris && python -m pytest tests/unit/test_geometry.py -v`
Expected: All 6 tests PASS

---

## Task 4: tokens.py FocusToken model and registry

**Files:** Create `iris/tokens.py`, `tests/unit/test_tokens.py`

FocusToken dataclass with id, hwnd, pid, exe_name, title, monitor_index, bounds, fingerprint, parent_hwnd, created_at, last_revalidated_at. TokenRegistry stores in process-local dict. Module-level default_registry().

**Verify:** `pytest tests/unit/test_tokens.py -v` covers create, store/get/remove, age_seconds.

---

## Task 5: spatial.py window enumeration

**Files:** Create `iris/spatial.py`, `tests/integration/test_spatial_real.py`

WindowInfo dataclass. enumerate_windows(visible_only, titled_only) using win32gui.EnumWindows. _exe_for_pid via psutil. _make_window_info reads title, rect, pid via win32process.GetWindowThreadProcessId.

**Verify:** Integration test against real desktop returns non-empty list with titled visible windows.

---

## Task 6: spatial.py match_window

**Files:** Append `iris/spatial.py`, create `tests/unit/test_spatial_match.py`

match_window(spec, candidates) supports keys: hwnd, pid, process (case-insensitive exe name), title (exact), title_contains (case-insensitive substring), title_regex.

**Verify:** Unit tests with synthetic WindowInfo list cover each filter and combined filters.

---

## Task 7: spatial.py monitor mapping

**Files:** Append `iris/spatial.py`, create `tests/unit/test_spatial_monitors.py`

list_monitors() via mss to get monitor rects. get_monitor_for_window(hwnd) uses largest-area-overlap algorithm: iterate monitors, compute Rect.intersection_area with window bounds, return index of max.

**Verify:** Unit test with mocked monitor list and synthetic window rects. Integration test confirms real desktop primary monitor returns 1.

---

## Task 8: spatial.py occlusion check

**Files:** Append `iris/spatial.py`, create `tests/integration/test_spatial_occlusion.py`

is_occluded(hwnd) walks Z-order via win32gui.GetWindow(GW_HWNDPREV) starting from hwnd, returns True if any window above intersects target's bounds AND is visible AND not minimized.

**Verify:** Integration test spawns Tkinter window, raises another on top, asserts occluded=True; lowers it, asserts occluded=False.

---

## Task 9: spatial.py popup detection and bring_to_front

**Files:** Append `iris/spatial.py`, create `tests/integration/test_spatial_popups.py`

find_popups_for(pid, since_timestamp) returns top-level windows belonging to pid created after timestamp (uses enumerate_windows + filter). bring_to_front(hwnd) does AttachThreadInput dance: get fg thread, attach our thread, SetForegroundWindow, detach.

**Verify:** Integration test against test harness app's "Spawn Dialog" button.

---

## Task 10: vision.py capture refactor

**Files:** Create `iris/vision.py`, move existing capture logic from current server.py

Persistent mss singleton (_get_sct). capture(bounds: Rect | None, monitor: int = 0) returns PIL Image. encode_jpeg(img, quality, optimize_tokens) returns bytes + final w/h. Constants: OPTIMAL_MAX_LONG_EDGE=1568, OPTIMAL_MAX_PIXELS=1_150_000, HARD_LIMIT=8000.

**Verify:** Unit test with mocked mss returns expected byte data; integration captures primary monitor.

---

## Task 11: vision.py OCR with Tesseract

**Files:** Append `iris/vision.py`, create `tests/integration/test_vision_ocr.py`

ocr_text(image: PIL.Image) returns list of {text, bbox: Rect, confidence}. Uses pytesseract.image_to_data with output_type=DATAFRAME. Preprocessing: convert to grayscale, threshold via PIL.ImageOps.autocontrast, optionally upscale 2x if image height < 400px. Pre-warm: call once at module import time on a 1x1 image.

**Verify:** Integration test creates PIL image with rendered text "HELLO WORLD" via ImageDraw, asserts OCR returns it.

---

## Task 12: vision.py find_text_in_image with fuzzy matching

**Files:** Append `iris/vision.py`, create `tests/unit/test_vision_find_text.py`

find_text_in_image(words, query, fuzzy=True, threshold=0.6) uses difflib.SequenceMatcher to score each word/phrase against query. Also tries 2-gram and 3-gram joined word combinations. Returns list of matches sorted by similarity descending.

**Verify:** Unit test with synthetic ocr_text output containing "Start Recording" matches query "Start Recording" exactly and "start record" with fuzzy.

---

## Task 13: vision.py OCR cache (perceptual hash)

**Files:** Append `iris/vision.py`, create `tests/unit/test_vision_cache.py`

phash(image) returns 64-bit perceptual hash via PIL: resize to 8x8 grayscale, threshold against mean. ocr_cache: lru_cache-like dict keyed on (token_id, phash). cached_ocr(token_id, image) returns cached result or computes + stores.

**Verify:** Unit test confirms identical images return cached result; modified image triggers recompute.

---

## Task 14: semantic.py UIA queries

**Files:** Create `iris/semantic.py`, create `tests/integration/test_semantic_uia.py`

uia_root() cached uiautomation.GetRootControl(). control_for_hwnd(hwnd) returns Control. query(hwnd, role=None, name=None, automation_id=None, max_depth=8) walks tree returning matching controls with screen-absolute bounds. walk_tree(hwnd, max_depth) returns full hierarchical dump for discover().

Per-pid UIA support cache: probe by querying root.GetChildren(); empty result for known UIA-friendly process flags as unsupported.

**Verify:** Integration test against Tkinter test harness queries known button names.

---

## Task 15: semantic.py invoke patterns

**Files:** Append `iris/semantic.py`, create `tests/integration/test_semantic_invoke.py`

invoke(control, action='click') auto-selects pattern: Button -> InvokePattern.Invoke(); CheckBox/RadioButton -> TogglePattern.Toggle(); Edit -> ValuePattern.SetValue(text); ComboBox -> ExpandCollapsePattern.Expand() then SelectionItemPattern.Select() on child.

**Verify:** Integration test against test harness clicks a button via UIA invoke and confirms state change.

---

## Task 16: resolver.py backend routing

**Files:** Create `iris/resolver.py`, create `tests/unit/test_resolver.py`

Resolver class with semantic, vision, spatial deps injected (testability). find(token, target, fuzzy=True): try semantic first (cached UIA support), fall back to OCR via vision.cached_ocr + find_text_in_image, fall back to vision_handoff with screenshot + nearest_matches via suggest_alternatives.

response shape: {found: bool, hits: [...], backend: 'uia'|'ocr'|'vision_handoff', screenshot?: bytes, nearest_matches?: [...], drift_detected?: bool}

**Verify:** Unit test with mocked semantic returning hits asserts backend='uia'; mocked semantic empty + mocked vision OCR returning hits asserts backend='ocr'; both empty asserts backend='vision_handoff' with screenshot.

---

## Task 17: verify.py polling helpers

**Files:** Create `iris/verify.py`, create `tests/unit/test_verify.py`

wait_for_text(token, text, timeout=3000ms, interval=100ms): poll resolver.find(token, text), return True if found before timeout. wait_for_control(token, role/name, timeout). wait_for_window(spec, timeout). wait_for_no_text(...).

Backoff: start at 100ms, multiply by 1.3 each poll, cap at 500ms. Returns dict with found, elapsed_ms, polls.

**Verify:** Unit test with mock resolver eventually returning hit asserts found=True and reasonable poll count.

---

## Task 18: drift detection and fingerprinting

**Files:** Create `iris/fingerprint.py`, create `tests/unit/test_fingerprint.py`

compute_fingerprint(uia_tree_dump) returns sha256 of sorted control roles + names (excluding text values). compare(old_fp, new_fp, semantic_dump) returns drift_summary dict with buttons_added, buttons_removed, bounds_changed.

**Verify:** Unit test with two synthetic UIA dumps confirms no drift on identical, drift_detected=True with right summary on changes.

---

## Task 19: tokens.py revalidation

**Files:** Append `iris/tokens.py`, create `tests/integration/test_tokens_revalidate.py`

revalidate(token, spatial_module) called per-call. Steps: 1) check win32gui.IsWindow(hwnd); if dead, attempt repair: enumerate windows, find one with matching pid+exe+title fuzzy match, update token.hwnd. 2) cache result for 250ms keyed on token.id. Returns bool valid.

inspect(token, spatial, semantic): full report dict with alive, bounds_now, monitor_now, occluded, popups (list of WindowInfo), drift_detected (if fingerprint set).

**Verify:** Integration test focuses Tkinter harness, kills it, asserts revalidate returns False; relaunches harness with same title, asserts repair succeeds.

---

## Task 20: tests/fixtures/iris_test_harness.py

**Files:** Create `tests/fixtures/iris_test_harness.py`

Tkinter app with: title "IRIS_TEST_HARNESS", buttons "Click Me" (toggles label), "Spawn Dialog" (creates Toplevel), "Move Window" (translates +100,+100), "Minimize Self", "Simulate Update" (rebuilds widgets with new names), text Entry "Type here", Label that shows last action.

CLI flags: --geometry WxH+X+Y for spawn position. Lifecycle: graceful shutdown on SIGTERM. Exit codes used by pytest fixtures.

**Verify:** Run standalone: `python tests/fixtures/iris_test_harness.py --geometry 600x400+100+100` confirms it opens.

---

## Task 21: pytest fixture for harness

**Files:** Create `tests/conftest.py`

@pytest.fixture(scope='function') iris_harness yields handle with .pid, .hwnd (resolved via spatial.match_window on title="IRIS_TEST_HARNESS"). Spawns subprocess, polls until enumerate_windows finds it (max 3s), yields, terminates on teardown.

**Verify:** Smoke test: `def test_harness_fixture(iris_harness): assert iris_harness.hwnd > 0` passes.

---

## Task 22: server.py rewrite, MCP tool surface

**Files:** Backup current `server.py` to `archive/server_v1_2026-04-27.py`; write new `server.py`

Entry point uses FastMCP("iris"). Tools (each 3-5 line wrapper to a layer module):

Discovery: screen_info(), list_windows(filter), find_window(match)
Focus: focus(match, raise_=False), release(token), inspect(token)
Sight: see(token=None, quality=60), see_full(token=None, quality=85), screenshot(monitor=0, region=None, quality=60)
Locate: find(token, target, fuzzy=True), find_text(token, text), find_control(token, role=None, name=None)
Act: click(token=None, target=None, x=None, y=None, button='left', clicks=1, verify=False), type_text(text, target=None, interval=0.0), press_key(key, modifiers=None), hotkey(keys), scroll(token=None, amount=1, x=None, y=None)
Lifecycle: launch(app), wait_for(token, target, timeout=3000), verify_action(token, expected)
Diagnostics: iris_status(), self_test(), discover(token), suggest_alternatives(token, target)

CRITICAL: keep all v1 tool signatures intact (screenshot, screenshot_full, screenshot_window, screen_info, mouse_*, type_text, press_key, hotkey).

**Verify:** Server boots without exceptions: `python server.py --selftest` (add --selftest CLI flag that runs self_test() and exits with 0/1).

---

## Task 23: server.py backwards-compat shims

**Files:** Append `server.py`

Keep existing screenshot, screenshot_full, screenshot_window, screen_info, mouse_pos, mouse_move, mouse_click, mouse_drag, mouse_scroll, type_text, press_key, hotkey tool definitions but route internals through new modules. Same parameter names, same return shapes.

**Verify:** Boot server, confirm v1 tool list unchanged via `mcp.list_tools()` style introspection (or smoke test invoking each tool through the MCP harness).

---

## Task 24: self_test() MCP tool

**Files:** Create `iris/self_test.py`, register tool in `server.py`

run_self_test() spawns harness via subprocess, runs battery of checks, kills harness, returns structured report. Checks: spatial.enum, spatial.match, spatial.monitor_for_window, vision.capture, vision.ocr, semantic.query, resolver.find via UIA, resolver.find via OCR, tokens.revalidate, tokens.repair_after_close, popup detection, drift detection.

Each check timed, recorded as {name, status, ms, reason?}.

**Verify:** `python -c "from iris.self_test import run_self_test; print(run_self_test())"` returns dict with passed/failed counts.

---

## Task 25: bench.py performance benchmarks

**Files:** Create `tests/bench.py`

Measures latency of: focus (UIA available), find via UIA, find via OCR, see (1920x1080), discover full dump, revalidate. 100 iterations each, reports p50/p95/p99 to logs/bench.jsonl.

CLI: `python tests/bench.py --quick` (10 iters) or `--full` (100 iters).

**Verify:** Run `python tests/bench.py --quick`, check logs/bench.jsonl appended with results, all p95s under documented targets (or recorded as regressions).

---

## Task 26: app launching

**Files:** Create `iris/launcher.py`, create `apps.yaml`, register `launch()` tool in `server.py`

apps.yaml schema: per app key, launch (path or 'shell:start xxx'), match (process, title_contains, etc.). launcher.launch(name) reads yaml, runs subprocess.Popen for paths or os.startfile for shell entries; polls spatial.match_window for up to 5s; returns {pid, hwnd, monitor} or {error}.

Default apps.yaml entries: obs, chrome, edge, explorer, vscode, notepad.

**Verify:** Integration test launches notepad (always present on Windows), asserts hwnd resolved within 3s, kills it.

---

## Task 27: drift detection wire-up

**Files:** Modify `iris/resolver.py`, `iris/tokens.py`

On focus(): call semantic.walk_tree, compute fingerprint, store on token. On every find/click: cheap fingerprint recompute (just top-level button name set), compare. If diff > threshold, include drift_detected and drift_summary in response.

**Verify:** Integration test focuses harness, clicks "Simulate Update", calls find(), asserts drift_detected=True with right buttons_added/removed.

---

## Task 28: discover() MCP tool

**Files:** Append `server.py`

discover(token) returns: {window: inspect(token), uia_tree: walk_tree(hwnd, max_depth=8), ocr_text: ocr_text(see(token)), screenshot: see(token, quality=70), fingerprint: compute_fingerprint(...)}.

Run UIA + OCR + screenshot in parallel via asyncio.gather (asyncio.to_thread for blocking calls).

**Verify:** Integration test focuses harness, calls discover(), asserts all four sub-fields populated, total time under 800ms.

---

## Task 29: suggest_alternatives() MCP tool

**Files:** Append `server.py`

suggest_alternatives(token, target): runs UIA tree dump and OCR, fuzzy-scores ALL controls/text against target at threshold 0.4. Returns top 10 ranked by similarity * 0.7 + visual_prominence * 0.3 (prominence = bbox area / window area, capped at 1.0).

**Verify:** Integration test against harness with target "Spawn" returns "Spawn Dialog" first.

---

## Task 30: README and test runbook

**Files:** Modify `README.md`; create `docs/test-runbook.md`

README: update tool table, mention v2 features, note bundled Tesseract, link design doc + plan. Test runbook: manual e2e checklist with checkboxes for OBS, Chrome, File Explorer scenarios.

**Verify:** `cat README.md` includes new tools list and Tesseract note.

---

## Task 31: Cutover (gated on user)

**Files:** None new; cutover script

ONLY runs after self_test passes locally and user confirms. Steps:
1. Move current `server.py` to `archive/server_v1_2026-04-27.py`
2. Activate new package server.py (already in place from Task 22)
3. Document in PROGRESS.md what changed and what to test
4. User restarts Claude Code MCP host
5. User invokes iris.self_test() through Claude
6. If failures: revert via single-file swap

**Verify:** User-driven manual confirmation. Rollback path: `mv archive/server_v1_2026-04-27.py server.py` and restart MCP.

---

## Implementation order summary

1-3: Skeleton, Tesseract, geometry primitives (Task 1, 2, 3)
4: Token model (Task 4)
5-9: Spatial layer (Task 5, 6, 7, 8, 9)
10-13: Vision + OCR + cache (Task 10, 11, 12, 13)
14-15: Semantic UIA (Task 14, 15)
16-17: Resolver + verify (Task 16, 17)
18-19: Fingerprint + token revalidation (Task 18, 19)
20-21: Test harness + fixture (Task 20, 21)
22-23: Server rewrite + compat shims (Task 22, 23)
24: self_test (Task 24)
25-29: Benchmarks, launcher, drift, discover, suggest_alternatives (Task 25-29)
30: Docs (Task 30)
31: Cutover, gated on user (Task 31)

Frequent commits expected (one per task minimum).
