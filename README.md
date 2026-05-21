# Iris

Daddy Wolf's eyes and hands on Cenny's desktop. Replacement for the old
Evangelion MCP server, rebuilt single-file with FastMCP and without the
pynput keyboard listener that caused startup flakiness.

## Tools

| Tool | Purpose |
|------|---------|
| `screenshot` | Token-optimized JPEG (default, ~1590 tokens) |
| `screenshot_full` | Full-resolution JPEG (warning: big tokens) |
| `screenshot_window` | Only the focused window |
| `screen_info` | Monitor dimensions and count |
| `mouse_pos` | Current cursor position |
| `mouse_move` | Move cursor |
| `mouse_click` | Click at point or current position |
| `mouse_drag` | Drag from A to B |
| `mouse_scroll` | Scroll wheel |
| `type_text` | Type a string |
| `press_key` | Single key + optional modifiers |
| `hotkey` | Key combination |

## Running

```bash
cd H:\Claude\tools\iris
python server.py
```

Or via the launcher: `run.bat`.

## MCP config (~/.claude.json, under the H:/Claude project)

```json
"iris": {
  "type": "stdio",
  "command": "python",
  "args": ["H:\\Claude\\tools\\iris\\server.py"],
  "cwd": "H:\\Claude\\tools\\iris",
  "env": {"PYTHONUNBUFFERED": "1"}
}
```

## Logs

`H:\Claude\tools\iris\logs\iris.log`
