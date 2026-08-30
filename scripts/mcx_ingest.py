"""Compatibility launcher for the Shoonya MCX historical-ingestion CLI."""

import sys
import os

# Ensure repository root is in python path when executed from scripts/
repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from data_pipeline.shoonya_mcx.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
