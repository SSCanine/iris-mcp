"""Console-script entry points for the `iris-mcp` package.

`iris-mcp` -> serves the MCP server (stdio transport)
`iris-mcp-bench` -> runs the live accuracy bench (see iris.bench.runner)
`iris-mcp-doctor` -> environment diagnostics (see iris.doctor)
"""

from __future__ import annotations

import sys
from pathlib import Path


def serve() -> int:
    """Entry point for `iris-mcp`. Locates the bundled server.py and runs it.

    We deliberately don't import server.py at module load (it sets DPI
    awareness as a side effect, and ctypes calls would run on every
    `iris-mcp --help`). Instead we import inside this function so the
    side effects happen exactly when the server is asked to run.
    """
    # When installed, server.py lives at the package root (sibling of the
    # iris/ subpackage). Find it relative to this file.
    here = Path(__file__).resolve().parent
    server_path = here.parent / "server.py"
    if not server_path.exists():
        # Source checkout: server.py is at <repo>/server.py
        sys.stderr.write(
            f"iris-mcp: could not find server.py at {server_path}\n"
            f"This is a packaging bug. Please file an issue.\n"
        )
        return 2

    # Make sure server.py's directory is importable so its top-level
    # `from iris import ...` lines resolve.
    sys.path.insert(0, str(server_path.parent))

    import runpy

    runpy.run_path(str(server_path), run_name="__main__")
    return 0
