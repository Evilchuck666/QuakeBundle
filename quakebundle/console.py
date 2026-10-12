"""Console output: progress bars, sizes and step headers."""

from __future__ import annotations

import sys

try:
    from tqdm import tqdm
except ImportError:
    MESSAGE = 'This script needs tqdm: install it with "pip install tqdm"'
    MESSAGE += ', or run "uv run singleplayer.py"'
    sys.exit(MESSAGE)


def progress_bar(**kwargs) -> tqdm:
    """Return a tqdm bar that is hidden when stdout is not a terminal.

    The bar is removed when closed, so the caller prints the outcome instead.
    """
    return tqdm(file=sys.stdout, disable=None, leave=False, dynamic_ncols=True, **kwargs)


def byte_bar(**kwargs) -> tqdm:
    """Return a progress bar that counts bytes."""
    return progress_bar(unit="B", unit_scale=True, unit_divisor=1024, **kwargs)


def mb(size: int) -> str:
    """Return a size string in megabytes."""
    return f"{size / 1024 / 1024:,.1f} MB"


def step(text: str) -> None:
    """Print a section header for a step in the workflow."""
    print(f"\n==> {text}")
