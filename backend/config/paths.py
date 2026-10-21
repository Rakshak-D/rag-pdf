"""Path containment helpers for workbench-owned storage."""

from __future__ import annotations

from pathlib import Path


class PathContainmentError(ValueError):
    """Raised when a candidate path is outside an owned storage root."""


def resolve_owned_path(root: str | Path, candidate: str | Path) -> Path:
    """Resolve ``candidate`` and require it to remain under ``root``.

    ``Path.resolve`` is intentionally performed before containment checking so
    existing symlinks in the path cannot redirect a workbench-owned location.
    No directory is created by this function.
    """

    root_path = Path(root).expanduser().resolve(strict=False)
    candidate_path = Path(candidate).expanduser()
    resolved = (candidate_path if candidate_path.is_absolute() else root_path / candidate_path).resolve(
        strict=False
    )
    try:
        resolved.relative_to(root_path)
    except ValueError as exc:
        raise PathContainmentError(
            f"storage path {candidate!s} resolves outside owned root {root_path}"
        ) from exc
    return resolved
