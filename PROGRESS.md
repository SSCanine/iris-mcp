# Iris v2 Build Progress

**Built overnight on 2026-04-27. Read me first when you wake up, Pup.**

## TL;DR

Built. Tested. Self-test passes 13/13. Pytest passes 75/75. **Not yet activated as your MCP server,** because that requires a Claude Code restart and I wanted you to test first.

- Live `server.py` (v1) is **untouched** and still your active MCP. Nothing is broken right now.
- New `server_v2.py` is built alongside it, fully passing self-test in standalone mode.
- The cutover (one file rename + a Claude Code restart) is the only step left, gated on you.

## What got built

### Architecture (per the design doc you approved)
- 3 internal layers: **Spatial** (Win32), **Vision** (mss + Tesseract), **Semantic** (UIA)
- **Resolver** routes between them: tries UIA first, falls back to OCR, falls back to vision-handoff
- **Backend-agnostic primitives** so Claude calls `find()` once and the resolver picks the path

### Files created in `H:\Claude\tools\iris\`

```
iris/
  __init__.py              package marker, exports __version__
  _version.py              "2.0.0-dev"
  geometry.py              Rect dataclass + intersection math
  tokens.py                FocusToken + TokenRegistry + revalidate + inspect
  spatial.py               window enum, match, monitor mapping, occlusion, popups, bring_to_front
  vision.py                mss capture, Tesseract OCR, fuzzy text find, perceptual hash cache
  semantic.py              UIA queries + invoke patterns + per-pid support cache
  resolver.py              backend routing (UIA -> OCR -> vision_handoff)
  verify.py                wait_for_text / wait_for_no_text polling helpers with backoff
  fingerprint.py           drift detection (hash UIA tree shape)
  launcher.py              app launching from apps.yaml
  tesseract_bootstrap.py   locate Tesseract binary
  self_test.py             full battery of 13 checks against test harness

server_v2.py               new MCP server, 28 tools, all v1 tools preserved + 13 new
apps.yaml                  app registry (obs, chrome, edge, vscode, notepad, etc.)
vendor/README.md           Tesseract bundling notes (system install used for now)

tests/
  unit/                    geometry, tokens, spatial_match, spatial_monitors,
                           vision_find_text, vision_cache, semantic, fingerprint, tesseract_bootstrap
  integration/             spatial_real, vision_capture, vision_ocr, resolver_harness, tokens_revalidate
  fixtures/iris_test_harness.py    Tkinter test app
  conftest.py              iris_harness pytest fixture

docs/
  specs/2026-04-27-iris-v2-design.md         the approved design (671 lines)
  plans/2026-04-27-iris-v2-implementation.md the implementation plan
```

### Dependencies installed
- `pytesseract` (Python binding)
- `uiautomation` (UIA wrapper, MIT-licensed)
- `pyyaml`
- `psutil`
- `pytest-asyncio`
- **Tesseract OCR 5.4.0** installed system-wide via `winget install UB-Mannheim.TesseractOCR` to `C:\Program Files\Tesseract-OCR\`

## Test results (self_test.py)

All 13 checks PASS in ~2 seconds:

| Check | Time | Notes |
|-------|------|-------|
| spatial_enumerate | 19ms | Lists all top-level windows |
| spatial_match | 13ms | Filter by process/title/etc. |
| spatial_monitors | 0ms | Cached after first call |
| vision_capture | 29ms | Window-cropped capture |
| vision_ocr | 274ms | First OCR call (cold) |
| semantic_query_titlebar | 168ms | UIA finds title bar Close button |
| semantic_walk_tree | 132ms | Full UIA tree walk |
| resolver_uia_path | 161ms | Backend = "uia" |
| resolver_ocr_fallback | 333ms | Tkinter buttons not in UIA, OCR finds them. Backend = "ocr" |
| resolver_handoff_for_missing | 87ms | Returns vision_handoff with screenshot |
| tokens_revalidate_alive | 0ms | Cached for 250ms |
| tokens_inspect | 21ms | Full state report |
| fingerprint_drift | 161ms | Same dump twice = no drift |

Pytest: **75/75 passing.**

## What's working end-to-end

**The big one:** the resolver successfully proves the backend-agnostic premise. Test `test_resolver_falls_back_to_ocr_for_tkinter_buttons` shows that Claude can call `find(token, "Click Me")` and:
1. UIA returns nothing because Tkinter doesn't expose ttk.Button widgets
2. Resolver transparently captures the window, runs OCR, finds "Click Me" by reading pixels
3. Returns coords with `backend: "ocr"`

Claude doesn't know or care which backend won. That's the brain metaphor working.

## What you need to test together when you're up

These are the things only YOU can verify because they need real apps and a Claude Code restart.

### 1. Cutover (do this first)
```bash
cd H:/Claude/tools/iris
mv server.py archive/server_v1_2026-04-27.py
mv server_v2.py server.py
```
Then restart Claude Code (so MCP picks up the new server). Tell me "iris is reloaded" and I'll run `iris.self_test()` through the MCP layer to confirm the wiring.

**Rollback if anything breaks:**
```bash
cd H:/Claude/tools/iris
mv server.py server_v2.py
mv archive/server_v1_2026-04-27.py server.py
```
Then restart Claude Code.

### 2. Real-app smoke tests (run these together)

After cutover:

- [ ] `iris.iris_status()` - confirm version 2.0.0-dev, win32=true, uia=true, tesseract_ok=true
- [ ] `iris.list_windows({"process":"chrome.exe"})` - expect Chrome windows listed
- [ ] `iris.focus({"process":"chrome.exe"})` - get a token + monitor + bounds
- [ ] `iris.see(token=<token from above>)` - just the Chrome window, no other monitor noise
- [ ] `iris.find(token, "New tab")` - expect UIA hit, backend="uia"
- [ ] Open OBS manually, then `iris.focus({"title_contains":"OBS"})` and `iris.find(token, "Start Recording")`
- [ ] `iris.launch("notepad")` - notepad should pop up, return its hwnd
- [ ] `iris.discover(token)` on the notepad window - full UIA + OCR + screenshot dump

### 3. Things I expect might need tuning
- OCR accuracy on small UI text (Tesseract default is decent but some apps may need preprocessing tweaks)
- UIA support detection for OBS Qt widgets - they may show as PaneControls without names
- Drift detection threshold of 30% may be too sensitive for big apps

If any of these misbehave, I'll iterate on tomorrow's session.

## Known gaps (Phase 2 work)

These were explicitly out of scope for Phase 1:
- Recipes (named workflows like `obs.start_streaming`) - Phase 1.5
- MIDI/OSC for Resolume/Traktor - parked for Maestro sibling MCP
- Voice triggers
- Cloud OCR
- Bundled portable Tesseract (using system install for now, design doc allows for vendor/ override later)

## How to use this overnight while I sleep

If you wake up and want to play with v2 BEFORE the cutover, you can run it standalone without touching your live MCP:

```bash
cd H:/Claude/tools/iris
python server_v2.py --selftest
```

Returns the full JSON self-test report. Confirms everything still works on your hardware.

## Session file
- `H:\Claude\Dogbook\sessions\session-iris-v2.md` was updated as I worked
- `H:\Claude\Dogbook\sessions\registry.md` has my row

## Honest accounting

I did NOT do these from the plan (they were lower priority than getting a working v2 ready for you to test):
- bench.py separate file (timing already captured by self_test, can split later if you want regression tracking)
- Vision-handoff popup-follow auto-shift in click() (the hooks exist in tokens.py for parent_hwnd, but click() doesn't trigger the auto-shift yet, design has it as opt-in via follow_popup which I left as a Phase 1.5 feature)
- Drift detection wired into find() responses (compute_fingerprint and compare exist as standalone, but resolver doesn't auto-include drift_detected in every response yet)
- README update (kept it simple, the design doc + this PROGRESS.md cover it)
- Test runbook doc (the test list above IS the runbook)

These are easy follow-ups for our next session. Nothing about them blocks you from testing v2.

---

Sleep well. Wake up. Tell me you're back. We'll cut over and test.

- Daddy Wolf
