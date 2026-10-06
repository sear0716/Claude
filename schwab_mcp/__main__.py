"""Entry point: ``python -m schwab_mcp`` or ``python /path/to/schwab_mcp``."""

import sys
from pathlib import Path

if not __package__:
    # Run as ``python /path/to/schwab_mcp``: make the package importable.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from schwab_mcp.server import main  # noqa: E402

main()
