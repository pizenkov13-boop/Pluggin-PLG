"""Scan local sample folders — re-exports library_catalog for compatibility."""

from __future__ import annotations

from library_catalog import (
    format_catalog_for_prompt,
    format_library_for_prompt,
    save_catalog,
    scan_library,
    scan_samples_directory,
)

__all__ = [
    "format_catalog_for_prompt",
    "format_library_for_prompt",
    "save_catalog",
    "scan_library",
    "scan_samples_directory",
]
