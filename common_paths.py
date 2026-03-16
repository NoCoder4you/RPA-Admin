"""Central path helpers for common project folders and files.

This module keeps path construction in one place so cogs/core modules
do not each re-implement repository-relative path logic.
"""

from __future__ import annotations

from pathlib import Path


# Resolve once at import time: this file lives in the repository root.
PROJECT_ROOT = Path(__file__).resolve().parent


def json_dir() -> Path:
    """Return the repository's shared JSON configuration directory."""

    return PROJECT_ROOT / "JSON"


def cogs_dir() -> Path:
    """Return the repository's uppercase COGS extension directory."""

    return PROJECT_ROOT / "COGS"


def json_file(filename: str) -> Path:
    """Build an absolute path to a file inside the JSON directory.

    Args:
        filename: Basename or relative filename under ``JSON/``.
    """

    return json_dir() / filename


def cogs_file(filename: str) -> Path:
    """Build an absolute path to a file inside the COGS directory.

    Args:
        filename: Basename or relative filename under ``COGS/``.
    """

    return cogs_dir() / filename

