#!/usr/bin/env python3
"""Launcher: ./vb <command> <site>. The work is done in vb_tools."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from vb_tools.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
