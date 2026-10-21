"""Deterministic PDF manifests and immutable source snapshots."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import uuid
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.config.paths import (
    PathContainmentError,
    resolve_disjoint_paths,
    resolve_owned_path,
)


MANIFEST_SCHEMA_VERSION = "1"
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_DOCUMENT_ID_RE = re.compile(r"^doc_[0-9a-f]{64}$")
_COPY_BUFFER_SIZE = 1024 * 1024


class ManifestValidationError(ValueError):
    """Raised for malformed manifests or unsafe source/snapshot paths."""


class ManifestMismatchError(ManifestValidationError):
    """Raised when a manifest does not match the current PDF corpus."""


class ManifestSourceChangedError(ManifestValidationError):
    """Raised when a PDF changes during hashing or snapshot copying."""


class DocumentManifestEntry(BaseModel):
    """Portable identity and byte facts for one PDF."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    relative_path: str
    size_bytes: int
    sha256: str
    document_id: str

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        if not value or "\\" in value:
            raise ValueError("relative_path must be a non-empty POSIX path")
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or PureWindowsPath(value).is_absolute()
            or PureWindowsPath(value).drive
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise ValueError("relative_path must be normalized and must not escape its source root")
        if path.as_posix() != value or path.suffix.lower() != ".pdf":
            raise ValueError("relative_path must be a normalized POSIX PDF path")
        return value

    @field_validator("size_bytes")
    @classmethod
    def validate_size(cls, value: int) -> int:
        if value < 0:
            raise ValueError("size_bytes must be non-negative")
        return value

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("sha256 must be exactly 64 hexadecimal characters")
        return value.lower()

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        if not _DOCUMENT_ID_RE.fullmatch(value):
            raise ValueError("document_id must be a doc_ followed by 64 lowercase hex characters")
        return value


class DocumentManifest(BaseModel):
    """Canonical PDF corpus manifest."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = MANIFEST_SCHEMA_VERSION
    entries: tuple[DocumentManifestEntry, ...] = Field(default_factory=tuple)
    content_hash: str

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: str) -> str:
        if value != MANIFEST_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported manifest schema version {value!r}; "
                f"supported version is {MANIFEST_SCHEMA_VERSION!r}"
            )
        return value

    @field_validator("content_hash")
    @classmethod
    def validate_content_hash(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("content_hash must be exactly 64 hexadecimal characters")
        return value.lower()

    @model_validator(mode="after")
    def validate_entries(self) -> "DocumentManifest":
        paths = [entry.relative_path for entry in self.entries]
        if len(paths) != len(set(paths)):
            raise ValueError("manifest entries contain duplicate relative paths")
        if paths != sorted(paths):
            raise ValueError("manifest entries must be sorted by relative_path")
        return self

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "entries": [entry.model_dump(mode="json") for entry in self.entries],
        }

    def verify_integrity(self) -> None:
        expected = _hash_canonical(self.canonical_payload())
        if expected != self.content_hash:
            raise ManifestValidationError(
                f"manifest content_hash mismatch: expected {expected}, got {self.content_hash}"
            )

    def to_serializable_json(self) -> str:
        payload = {**self.canonical_payload(), "content_hash": self.content_hash}
        return _canonical_json(payload)

    @classmethod
    def from_serializable_json(cls, value: str) -> "DocumentManifest":
        try:
            payload = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ManifestValidationError("serialized manifest is not valid JSON") from exc
        try:
            manifest = cls.model_validate(payload)
        except Exception as exc:
            raise ManifestValidationError("serialized manifest failed schema validation") from exc
        manifest.verify_integrity()
        return manifest


@dataclass(frozen=True)
class _FileSignature:
    size_bytes: int
    mtime_ns: int


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash_canonical(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _signature(path: Path) -> _FileSignature:
    stat = path.stat()
    return _FileSignature(size_bytes=stat.st_size, mtime_ns=stat.st_mtime_ns)


def _assert_regular_pdf(path: Path, source_root: Path) -> None:
    if path.is_symlink():
        raise ManifestValidationError(f"symlinked PDF input is not allowed: {path}")
    resolved = path.resolve(strict=True)
    try:
        resolved.relative_to(source_root)
    except ValueError as exc:
        raise ManifestValidationError(f"PDF input escapes source root: {path}") from exc
    if not path.is_file():
        raise ManifestValidationError(f"PDF input is not a regular file: {path}")


def _reject_symlinked_directories(root: Path) -> None:
    for current, directories, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        for directory in directories:
            candidate = current_path / directory
            if candidate.is_symlink():
                raise ManifestValidationError(f"symlinked directory is not allowed: {candidate}")
        for filename in files:
            candidate = current_path / filename
            if candidate.is_symlink():
                raise ManifestValidationError(f"symlinked input is not allowed: {candidate}")


def _source_root(input_dir: str | Path) -> Path:
    candidate = Path(input_dir).expanduser()
    if candidate.is_symlink():
        raise ManifestValidationError(f"symlinked source root is not allowed: {candidate}")
    root = candidate.resolve(strict=False)
    if not root.is_dir():
        raise NotADirectoryError(f"{root} is an invalid directory.")
    _reject_symlinked_directories(root)
    return root


def _pdf_files(root: Path) -> list[Path]:
    # This deliberately preserves the research ``rglob('*.pdf')`` semantics.
    return sorted(
        (path for path in root.rglob("*.pdf") if path.is_file()),
        key=lambda path: path.relative_to(root).as_posix(),
    )


def _hash_pdf(path: Path, source_root: Path) -> tuple[_FileSignature, str]:
    _assert_regular_pdf(path, source_root)
    before = _signature(path)
    digest = hashlib.sha256()
    bytes_read = 0
    with path.open("rb") as stream:
        while True:
            block = stream.read(_COPY_BUFFER_SIZE)
            if not block:
                break
            digest.update(block)
            bytes_read += len(block)
    after = _signature(path)
    if before != after or bytes_read != before.size_bytes:
        raise ManifestSourceChangedError(f"PDF changed while hashing: {path}")
    return before, digest.hexdigest()


def _document_id(relative_path: str, sha256: str) -> str:
    identity = f"{relative_path}\0{sha256}".encode("utf-8")
    return "doc_" + hashlib.sha256(identity).hexdigest()


def _validated_manifest(manifest: DocumentManifest) -> DocumentManifest:
    if not isinstance(manifest, DocumentManifest):
        raise TypeError("expected a DocumentManifest")
    validated = DocumentManifest.model_validate(manifest.model_dump(mode="python"))
    validated.verify_integrity()
    return validated


def build_document_manifest(input_dir: str | Path) -> DocumentManifest:
    """Hash the current PDF corpus without writing any files."""

    root = _source_root(input_dir)
    entries = []
    for path in _pdf_files(root):
        signature, digest = _hash_pdf(path, root)
        relative_path = path.relative_to(root).as_posix()
        entries.append(DocumentManifestEntry(
            relative_path=relative_path,
            size_bytes=signature.size_bytes,
            sha256=digest,
            document_id=_document_id(relative_path, digest),
        ))
    entries_tuple = tuple(entries)
    payload = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "entries": [entry.model_dump(mode="json") for entry in entries_tuple],
    }
    return DocumentManifest(
        schema_version=MANIFEST_SCHEMA_VERSION,
        entries=entries_tuple,
        content_hash=_hash_canonical(payload),
    )


def _manifest_difference(expected: DocumentManifest, current: DocumentManifest) -> list[str]:
    expected_map = {entry.relative_path: entry for entry in expected.entries}
    current_map = {entry.relative_path: entry for entry in current.entries}
    failures = []
    for path in sorted(current_map.keys() - expected_map.keys()):
        failures.append(f"added PDF: {path}")
    for path in sorted(expected_map.keys() - current_map.keys()):
        failures.append(f"missing PDF: {path}")
    for path in sorted(expected_map.keys() & current_map.keys()):
        expected_entry = expected_map[path]
        current_entry = current_map[path]
        if expected_entry.size_bytes != current_entry.size_bytes:
            failures.append(
                f"size changed for {path}: expected {expected_entry.size_bytes}, "
                f"got {current_entry.size_bytes}"
            )
        if expected_entry.sha256 != current_entry.sha256:
            failures.append(f"content hash changed for {path}")
        if expected_entry.document_id != current_entry.document_id:
            failures.append(f"document identity changed for {path}")
    if not failures and expected.content_hash != current.content_hash:
        failures.append(
            f"manifest content hash changed: expected {expected.content_hash}, got {current.content_hash}"
        )
    return failures


def verify_document_manifest(
    input_dir: str | Path,
    manifest: DocumentManifest,
) -> DocumentManifest:
    """Verify exact PDF paths and bytes; return the validated manifest on success."""

    expected = _validated_manifest(manifest)
    current = build_document_manifest(input_dir)
    failures = _manifest_difference(expected, current)
    if failures:
        raise ManifestMismatchError("document manifest mismatch: " + "; ".join(failures))
    return expected


def _all_snapshot_files(root: Path) -> set[str]:
    _reject_symlinked_directories(root)
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    }


def _verify_snapshot(root: Path, manifest: DocumentManifest) -> None:
    verify_document_manifest(root, manifest)
    expected_paths = {entry.relative_path for entry in manifest.entries}
    actual_paths = _all_snapshot_files(root)
    if actual_paths != expected_paths:
        raise ManifestMismatchError(
            f"snapshot file set mismatch: expected {sorted(expected_paths)}, "
            f"got {sorted(actual_paths)}"
        )


def _copy_verified_file(source: Path, destination: Path, entry: DocumentManifestEntry, source_root: Path) -> None:
    _assert_regular_pdf(source, source_root)
    before = _signature(source)
    if before.size_bytes != entry.size_bytes:
        raise ManifestSourceChangedError(f"PDF size changed before staging: {source}")
    digest = hashlib.sha256()
    bytes_written = 0
    with source.open("rb") as source_stream, destination.open("xb") as destination_stream:
        while True:
            block = source_stream.read(_COPY_BUFFER_SIZE)
            if not block:
                break
            digest.update(block)
            destination_stream.write(block)
            bytes_written += len(block)
    after = _signature(source)
    if before != after or bytes_written != entry.size_bytes or digest.hexdigest() != entry.sha256:
        raise ManifestSourceChangedError(f"PDF changed while staging: {source}")
    destination_signature = _signature(destination)
    if destination_signature.size_bytes != entry.size_bytes or _sha256_file(destination) != entry.sha256:
        raise ManifestValidationError(f"staged PDF verification failed: {entry.relative_path}")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(_COPY_BUFFER_SIZE), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_cleanup(path: Path, owned_root: Path) -> None:
    try:
        relative = path.resolve(strict=False).relative_to(owned_root)
    except ValueError as exc:
        raise PathContainmentError(f"staging cleanup path escaped owned root: {path}") from exc
    if relative == Path("."):
        raise PathContainmentError("refusing to clean the owned storage root")
    if path.exists():
        shutil.rmtree(path)


def stage_document_snapshot(
    input_dir: str | Path,
    manifest: DocumentManifest,
    storage_root: str | Path,
) -> Path:
    """Copy and verify a manifest-identified PDF corpus into owned storage."""

    expected = _validated_manifest(manifest)
    _, owned_root = resolve_disjoint_paths(
        input_dir,
        storage_root,
        first_label="PDF source root",
        second_label="snapshot storage root",
    )
    # Validate the caller's original path before using a canonical path for
    # source containment and staging.  Resolving first would hide a symlinked
    # source root from _source_root's explicit rejection.
    source_root = _source_root(input_dir)
    verify_document_manifest(source_root, expected)

    final_root = resolve_owned_path(owned_root, Path("snapshots") / expected.content_hash)
    if final_root.exists():
        if not final_root.is_dir():
            raise ManifestMismatchError(f"snapshot path is not a directory: {final_root}")
        _verify_snapshot(final_root, expected)
        return final_root

    owned_root.mkdir(parents=True, exist_ok=True)
    final_root.parent.mkdir(parents=True, exist_ok=True)
    staging_root = resolve_owned_path(
        owned_root,
        Path("snapshots") / f".{expected.content_hash}.staging-{uuid.uuid4().hex}",
    )
    staging_root.mkdir(parents=True, exist_ok=False)
    try:
        source_by_path = {
            entry.relative_path: source_root / Path(entry.relative_path)
            for entry in expected.entries
        }
        for entry in expected.entries:
            source = source_by_path[entry.relative_path]
            _assert_regular_pdf(source, source_root)
            destination = resolve_owned_path(staging_root, Path(entry.relative_path))
            destination.parent.mkdir(parents=True, exist_ok=True)
            _copy_verified_file(source, destination, entry, source_root)
        _verify_snapshot(staging_root, expected)
        try:
            staging_root.rename(final_root)
        except FileExistsError:
            if final_root.exists():
                _verify_snapshot(final_root, expected)
                _safe_cleanup(staging_root, owned_root)
                return final_root
            raise
        return final_root
    except Exception:
        _safe_cleanup(staging_root, owned_root)
        raise
