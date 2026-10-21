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


def resolve_disjoint_paths(
    first: str | Path,
    second: str | Path,
    *,
    first_label: str = "first path",
    second_label: str = "second path",
) -> tuple[Path, Path]:
    """Resolve two paths and require that neither contains the other.

    This is deliberately side-effect-free and uses ``strict=False`` so it can
    validate a storage location before that location exists.  The check is
    symmetric: source and storage roots must be separate trees in either
    argument order.
    """

    first_path = Path(first).expanduser().resolve(strict=False)
    second_path = Path(second).expanduser().resolve(strict=False)
    overlaps = (
        first_path == second_path
        or first_path.is_relative_to(second_path)
        or second_path.is_relative_to(first_path)
    )
    if overlaps:
        raise PathContainmentError(
            f"{first_label} and {second_label} must be disjoint; "
            f"resolved paths overlap: {first_path} and {second_path}"
        )
    return first_path, second_path
