from __future__ import annotations

import os
import stat
from pathlib import Path


def is_path_redirect(path: Path) -> bool:
    """Return true for symlinks and Windows junction/reparse-point indirection."""
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if callable(is_junction) and bool(is_junction()):
        return True
    if os.name != "nt":
        return False
    try:
        attributes = int(getattr(os.lstat(path), "st_file_attributes", 0))
    except OSError:
        return False
    reparse_point = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return bool(attributes & reparse_point)


def path_redirect_component(path: Path) -> Path | None:
    """Return a redirect at the final component after stabilizing its parent."""
    absolute = path if path.is_absolute() else Path.cwd() / path
    # Existing ancestors may legitimately include platform-managed symlinks or
    # junctions (`/tmp` and `/var` on macOS are common examples). Resolve that
    # stable parent once, then inspect the application-managed leaf without
    # following it. Callers continue all later work from canonical parents.
    candidate = absolute.parent.resolve(strict=False) / absolute.name
    if is_path_redirect(candidate):
        return candidate
    return None
