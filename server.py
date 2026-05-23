"""Iris MCP server entry shim for source checkouts.

When running from a cloned repo (`python server.py`) this file is on
sys.path's first entry, so we can import the iris package directly. After
pip install, callers should use either `iris-mcp` (console script) or
`python -m iris`; both go through iris/__main__.py and skip this file.

Kept as a one-liner so changes to server logic happen in one place
(iris/__main__.py).
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make sure the iris package next to this file is importable.
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from iris.__main__ import main

if __name__ == "__main__":
    main()
