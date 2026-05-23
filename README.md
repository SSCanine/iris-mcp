# Iris

[![CI](https://github.com/SSCanine/iris-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/SSCanine/iris-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%20|%203.11%20|%203.12-blue.svg)](https://www.python.org/)
[![Windows](https://img.shields.io/badge/platform-Windows%2010%20|%2011-0078D4.svg)]()
[![MCP](https://img.shields.io/badge/MCP-server-7C3AED.svg)](https://modelcontextprotocol.io/)

**High-precision Windows desktop control for AI agents.**
A Model Context Protocol (MCP) server that lets LLMs see, find, and click
real UI on Windows, with sub-pixel accuracy on mixed-DPI multi-monitor
setups, and without requiring the target app to expose an accessibility
tree.

Iris uses three rendering backends behind one API:

1. **Spatial** (Win32) for window enumeration, focus, occlusion, bounds.
2. **Semantic** (UIA) for accessibility-aware element queries and pattern
   invocation.
3. **Vision** (`mss` + Tesseract OCR + perceptual-hash cache) for apps that
   draw their own widgets and skip the accessibility tree.

A resolver routes between them: UIA first, then OCR, then a "vision handoff"
that returns a screenshot crop so the LLM can decide directly. The result is
that Iris works on apps that pure-UIA tools cannot reach: Tkinter, custom Qt
widgets, OBS dock widgets, Resolume panels, game launchers, anything visible.

## Why Iris

A live, measurable accuracy benchmark on a three-monitor mixed-DPI setup
(100% / 125% / 150% scales) at the time of writing:

```
total attempts        : 52
find rate             : 100.0%
correct-button rate   : 100.0%
miss distance  mean   : 0.19 px
miss distance  p50    : 0.0 px
miss distance  p95    : 1.0 px
miss distance  max    : 1.0 px
```

Run it yourself with `python -m iris.bench.runner` (see below). The bench
spawns an instrumented Tk harness, drives Iris's find+click against varied
targets, and reads back exact pixel-level hit locations. No vendor claims
without proof.

## How Iris differs from other Windows MCP servers

| Capability | Iris | Pure-UIA servers |
|---|---|---|
| Works on Tk / custom Qt / canvas apps | yes (OCR fallback) | no |
| Per-Monitor V2 DPI awareness | yes | usually not |
| UIA `Invoke` fast path (no mouse motion) | yes | no |
| OCR→UIA widget upgrade (text bbox -> widget bbox) | yes | n/a |
| Pre-flight off-screen / off-window clamp | yes | no |
| `SendInput` for synthesized clicks | yes | usually `mouse_event` |
| Closed-loop click verification (UIA drift / text appears) | yes | no |
| Live accuracy bench | yes | rarely |
| YAML recipe engine | yes | no |
| Tk-widget `Invoke` silent-failure denylist | yes | no |

## Quick start

Requires Windows 10/11, Python 3.10+, and Tesseract OCR on PATH.

```powershell
# Clone the repo
git clone https://github.com/SSCanine/iris-mcp.git
cd iris-mcp

# Install dependencies
python -m pip install -r requirements.txt

# Optional: install Tesseract for OCR fallback
winget install UB-Mannheim.TesseractOCR

# Run the self-test (no MCP client needed)
python -c "from iris.self_test import run_self_test; import json; print(json.dumps(run_self_test(), indent=2))"

# Run the live accuracy bench (opens a Tk window, drives clicks against it)
python -m iris.bench.runner
```

Add Iris to your Claude Desktop / Claude Code MCP config:

```json
{
  "mcpServers": {
    "iris": {
      "type": "stdio",
      "command": "python",
      "args": ["<full-path-to-iris>/server.py"],
      "cwd": "<full-path-to-iris>",
      "env": {"PYTHONUNBUFFERED": "1"}
    }
  }
}
```

## Try the examples

Standalone scripts that work with just the Python API (no MCP client
needed). Read, run, copy:

```powershell
python examples/01_hello_iris.py             # status + monitor topology
python examples/02_clipboard_pipeline.py     # read/transform/write clipboard
python examples/03_open_url_in_chrome.py     # recipe-driven Chrome control
python examples/04_find_and_click_real_app.py # spawn Notepad, find + UIA invoke
```

See `examples/README.md` for the index.

## See it actually work

Reference machine: Windows 11 + Python 3.12, three monitors at 100% / 125% /
150% DPI. Tagged before v0.1.0:

```
total attempts        : 52
find rate             : 100.0%
correct-button rate   : 100.0%
miss distance  mean   : 0.19 px
miss distance  p50    : 0.0 px
miss distance  p95    : 1.0 px
miss distance  max    : 1.0 px
backend distribution  : {'ocr': 52}
OCR -> UIA upgrades   : 52
```

Per-scenario:
```
baseline_static  correct=100.0%  miss_p50=0.0  miss_p95=0.0
window_dragged   correct=100.0%  miss_p50=0.0  miss_p95=0.0
window_resized   correct=100.0%  miss_p50=0.0  miss_p95=0.0
per_monitor      correct=100.0%  miss_p50=0.0  miss_p95=1.0
raise_then_click correct=100.0%  miss_p50=1.0  miss_p95=1.0
```

Full transcript: `assets/bench-output.txt`. Doctor diagnostic on the same
machine: `assets/doctor-output.txt`. Run `python -m iris.bench.runner` on
your own hardware to compare.

## Tools the MCP server exposes

### Diagnostics
- `iris_status` backend availability, OCR + UIA readiness, DPI mode, cache stats
- `self_test` runs the 15-check battery against a built-in Tk harness

### Window discovery
- `list_windows` enumerate top-level windows, filterable by process / title
- `find_window` match a single window from a spec
- `screen_info` monitor count, resolutions, scaling
- `focus` bring a window forward and get a `FocusToken`

### Vision
- `see` window-cropped screenshot (recommended default)
- `see_full` whole-desktop screenshot
- `screenshot` / `screenshot_window` / `screenshot_full` variants

### Find and act
- `find(token, target)` resolver-routed locate (UIA -> OCR -> handoff)
- `inspect(token)` walk the UIA tree
- `discover(token)` full ground-truth dump (UIA + OCR + screenshot)
- `discover_panels` Qt/Win32/Electron panel discovery
- `suggest_alternatives` lower-threshold fuzzy matches when exact text not found
- `wait_for` poll for text/state changes with backoff

### Input
- `click(token, target, ...)` find + click with optional UIA-invoke fast path
- `mouse_move`, `mouse_click`, `mouse_drag`, `mouse_scroll`, `mouse_pos`
- `type_text`, `press_key`, `hotkey`, `release`

### App lifecycle
- `launch(app)` start an app from the `apps.yaml` registry
- `list_apps` what the registry knows about

### Recipes (named workflows)
- `list_recipes` enumerate YAML-defined workflows
- `run_recipe(name, args)` execute a chained recipe by name

## Architecture

```
                          MCP client (Claude, etc.)
                                    |
                                    v
                            +---------------+
                            |   server.py    |
                            +-------+-------+
                                    |
                                    v
+--------------+    +---------------+----------------+    +-----------------+
|              |    |                                |    |                 |
|  spatial.py  |--->|          resolver.py           |<---|   semantic.py   |
|  Win32 / mss |    |    UIA -> OCR -> handoff       |    |   uiautomation  |
|              |    |                                |    |                 |
+--------------+    +---------------+----------------+    +-----------------+
                                    |
                                    v
                            +---------------+
                            |   vision.py    |
                            | mss + Tesseract |
                            +---------------+
```

Each `find()` call goes:

1. Try UIA. If the window exposes accessibility and a control matches, return.
2. Try OCR. PrintWindow-capture the window (works through occlusion),
   Tesseract-OCR the pixels, fuzzy-match the target.
3. After OCR finds text, call UIA `ControlFromPoint` to upgrade the text
   bbox to the enclosing widget bbox. Click the widget center, not the
   glyphs.
4. If nothing matches, return a `vision_handoff` with a screenshot crop so
   the LLM can decide.

`click()` calls `find()`, then either:

- Invokes a UIA pattern (`InvokePattern`, `TogglePattern`, etc.) directly
  with no mouse motion. Bypasses coord math, DPI, occlusion, animation
  timing. Most reliable.
- Falls back to a `SendInput` move + click at the resolved coords, with a
  pre-flight check that the target is inside the current window AND on a
  monitor.

A denylist (`TkTopLevel`, `TkChild`) catches classes whose UIA `Invoke` is a
silent no-op so Iris uses geometric clicks for those.

## Recipes

Recipes are YAML files in `recipes/` that chain primitives. Example:

```yaml
name: obs.start_recording
description: Focus OBS, click Start Recording, wait for Stop to appear.
steps:
  - id: tok
    action: focus
    args: { match: { process: "obs64.exe" } }
  - action: click
    args: { token: "${tok.token}", target: "Start Recording" }
  - action: wait_for
    args: { token: "${tok.token}", target: "Stop Recording", timeout_ms: 5000 }
```

Run with `mcp__iris__run_recipe(name="obs.start_recording")`. Add new
recipes by dropping `whatever.yaml` into the recipes directory.

### Built-in recipe gallery

| Recipe | Inputs | What it does |
|---|---|---|
| `obs.start_recording` | none | Focus OBS, click Start Recording, wait for Stop to appear |
| `obs.stop_recording` | none | Focus OBS, click Stop Recording, wait for Start to come back |
| `chrome.open_url` | `url` | Focus Chrome, Ctrl+L, type URL, Enter |
| `vscode.command_palette` | `command` | Focus VS Code, Ctrl+Shift+P, type command, Enter |
| `alt_tab_to` | `title_contains` | Bring a matching window to foreground |

## Live accuracy bench

```powershell
python -m iris.bench.runner                              # all scenarios
python -m iris.bench.runner --scenarios baseline_static  # one scenario
python -m iris.bench.runner --no-invoke                  # force geometric clicks
python -m iris.bench.runner --keep-harness               # leave window open
```

The bench spawns an instrumented Tk harness with a grid of buttons of
varied sizes and label styles, then drives Iris find+click against each
under five scenarios: static, dragged window, resized window, parked on
each monitor, raise-from-occlusion. Every button reports exact pixel-level
hit location so the bench measures actual miss distance, not just
"did we click somewhere".

See `iris/bench/README.md` for details.

## Status

Early. Working and tested on Windows 11 with Python 3.10-3.12. Pull
requests welcome, see CONTRIBUTING.md.

## License

MIT. See LICENSE.
