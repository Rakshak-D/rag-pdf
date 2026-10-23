"""Immutable, content-addressed storage for serialized experiment artifacts."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import tempfile
from typing import Literal

from backend.config.paths import PathContainmentError, resolve_owned_path

from .ablation_plans import AblationPlan
from .ablation_runner import AblationRunResult
from .evaluation_runner import EvaluationRunResult
from .serialization import (
    SERIALIZATION_SCHEMA_VERSION,
    deserialize_ablation_result,
    deserialize_evaluation_result,
    serialize_ablation_result,
    serialize_evaluation_result,
)


ArtifactType = Literal["evaluation_result", "ablation_result"]
_ARTIFACT_TYPES = ("evaluation_result", "ablation_result")
_ID_RE = re.compile(r"^[0-9a-f]{64}$")
_PREFIX_RE = re.compile(r"^[0-9a-f]{2}$")
_MAX_DEFAULT = 64 * 1024 * 1024


class ArtifactStoreError(ValueError):
    """Base error for invalid store requests or unsafe filesystem state."""


class ArtifactNotFoundError(ArtifactStoreError):
    """Raised when a valid artifact ID has no published artifact."""


class ArtifactIntegrityError(ArtifactStoreError):
    """Raised when a published artifact or store layout is inconsistent."""


@dataclass(frozen=True)
class StoredArtifact:
    """Portable metadata for one immutable artifact."""

    artifact_type: ArtifactType
    artifact_id: str
    relative_path: str
    byte_length: int
    serialization_schema_version: str

    def __post_init__(self) -> None:
        if self.artifact_type not in _ARTIFACT_TYPES:
            raise ArtifactStoreError("artifact type is invalid")
        if not isinstance(self.artifact_id, str) or not _ID_RE.fullmatch(self.artifact_id):
            raise ArtifactStoreError("artifact ID is invalid")
        expected = f"{self.artifact_type}/{self.artifact_id[:2]}/{self.artifact_id}.json"
        if self.relative_path != expected or "\\" in self.relative_path or ".." in Path(self.relative_path).parts:
            raise ArtifactStoreError("artifact relative path is invalid")
        if isinstance(self.byte_length, bool) or not isinstance(self.byte_length, int) or self.byte_length <= 0:
            raise ArtifactStoreError("artifact byte length is invalid")
        if self.serialization_schema_version != SERIALIZATION_SCHEMA_VERSION:
            raise ArtifactStoreError("serialization schema version is invalid")


class ExperimentArtifactStore:
    """A bounded, immutable filesystem store for canonical experiment JSON."""

    def __init__(self, store_root: str | Path, *, max_artifact_size: int = _MAX_DEFAULT) -> None:
        if not isinstance(store_root, (str, Path)) or not str(store_root).strip():
            raise ArtifactStoreError("store root is required")
        if isinstance(max_artifact_size, bool) or not isinstance(max_artifact_size, int) or max_artifact_size <= 0:
            raise ArtifactStoreError("max_artifact_size must be a positive integer")
        raw_root = Path(store_root).expanduser()
        if raw_root.exists() and raw_root.is_symlink():
            raise ArtifactStoreError("store root symlinks are not supported")
        self._root = raw_root.resolve(strict=False)
        self._max_size = max_artifact_size

    @staticmethod
    def _validate_type(artifact_type: str) -> ArtifactType:
        if artifact_type not in _ARTIFACT_TYPES:
            raise ArtifactStoreError("artifact type is invalid")
        return artifact_type  # type: ignore[return-value]

    @staticmethod
    def _validate_id(artifact_id: str) -> str:
        if not isinstance(artifact_id, str) or not _ID_RE.fullmatch(artifact_id):
            raise ArtifactStoreError("artifact ID is invalid")
        return artifact_id

    def _check_component(self, path: Path) -> None:
        """Reject symlinked components below the owned root."""

        try:
            relative = path.relative_to(self._root)
        except ValueError as exc:
            raise ArtifactIntegrityError("artifact path escapes the store") from exc
        current = self._root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ArtifactIntegrityError("store symlinks are not supported")

    def _destination(self, artifact_type: str, artifact_id: str) -> Path:
        kind = self._validate_type(artifact_type)
        digest = self._validate_id(artifact_id)
        candidate = self._root / kind / digest[:2] / f"{digest}.json"
        try:
            resolved = resolve_owned_path(self._root, candidate)
        except PathContainmentError as exc:
            raise ArtifactIntegrityError("artifact path escapes the store") from exc
        if resolved != candidate.resolve(strict=False):
            raise ArtifactIntegrityError("artifact path is redirected")
        self._check_component(candidate)
        return candidate

    def _reference(self, artifact_type: ArtifactType, artifact_id: str, byte_length: int) -> StoredArtifact:
        return StoredArtifact(artifact_type, artifact_id, f"{artifact_type}/{artifact_id[:2]}/{artifact_id}.json", byte_length, SERIALIZATION_SCHEMA_VERSION)

    def _bounded_read(self, path: Path) -> bytes:
        self._check_component(path)
        if not path.exists() or not path.is_file():
            raise ArtifactIntegrityError("published artifact is not a regular file")
        flags = os.O_RDONLY
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        if nofollow:
            flags |= nofollow
        try:
            fd = os.open(str(path), flags)
            with os.fdopen(fd, "rb") as stream:
                data = stream.read(self._max_size + 1)
                size = os.fstat(stream.fileno()).st_size
        except OSError as exc:
            raise ArtifactIntegrityError("artifact cannot be read safely") from exc
        if size > self._max_size or len(data) > self._max_size:
            raise ArtifactIntegrityError("artifact exceeds the configured size limit")
        return data

    def _existing_or_raise(self, path: Path, expected: bytes, artifact_type: ArtifactType, artifact_id: str) -> StoredArtifact:
        actual = self._bounded_read(path)
        if actual != expected or hashlib.sha256(actual).hexdigest() != artifact_id:
            raise ArtifactIntegrityError("published artifact content does not match its ID")
        return self._reference(artifact_type, artifact_id, len(actual))

    def _publish(self, artifact_type: ArtifactType, data: bytes) -> StoredArtifact:
        if len(data) == 0 or len(data) > self._max_size:
            raise ArtifactStoreError("serialized artifact exceeds the configured size limit")
        artifact_id = hashlib.sha256(data).hexdigest()
        destination = self._destination(artifact_type, artifact_id)
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            self._check_component(destination.parent)
        except OSError as exc:
            raise ArtifactStoreError("artifact directory cannot be created") from exc
        temp_path: Path | None = None
        try:
            fd, name = tempfile.mkstemp(prefix=".artifact-", suffix=".tmp", dir=str(destination.parent))
            temp_path = Path(name)
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temp_path, destination)
            except FileExistsError:
                return self._existing_or_raise(destination, data, artifact_type, artifact_id)
            except OSError as exc:
                raise ArtifactStoreError("filesystem does not support safe no-overwrite publication") from exc
            return self._reference(artifact_type, artifact_id, len(data))
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def save_evaluation_result(self, result: EvaluationRunResult) -> StoredArtifact:
        if not isinstance(result, EvaluationRunResult):
            raise ArtifactStoreError("evaluation result is invalid")
        try:
            serialized = serialize_evaluation_result(result)
            verified = deserialize_evaluation_result(serialized)
            data = serialized.encode("utf-8")
            if serialize_evaluation_result(verified) != serialized:
                raise ArtifactIntegrityError("evaluation serialization is not stable")
        except ArtifactStoreError:
            raise
        except Exception as exc:
            raise ArtifactStoreError("evaluation artifact validation failed") from exc
        return self._publish("evaluation_result", data)

    def load_evaluation_result(self, artifact_id: str) -> EvaluationRunResult:
        return self._load_evaluation(artifact_id)

    def _load_evaluation(self, artifact_id: str) -> EvaluationRunResult:
        digest = self._validate_id(artifact_id)
        path = self._destination("evaluation_result", digest)
        if not path.exists():
            other = self._destination("ablation_result", digest)
            if other.exists():
                raise ArtifactIntegrityError("artifact type does not match loader")
            raise ArtifactNotFoundError("artifact was not found")
        data = self._bounded_read(path)
        if hashlib.sha256(data).hexdigest() != digest:
            raise ArtifactIntegrityError("published artifact content does not match its ID")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ArtifactIntegrityError("artifact is not valid UTF-8") from exc
        try:
            return deserialize_evaluation_result(text)
        except Exception as exc:
            raise ArtifactIntegrityError("evaluation artifact failed validation") from exc

    def save_ablation_result(self, result: AblationRunResult, *, ablation_plan: AblationPlan) -> StoredArtifact:
        if not isinstance(result, AblationRunResult) or not isinstance(ablation_plan, AblationPlan):
            raise ArtifactStoreError("ablation result or plan is invalid")
        try:
            ablation_plan.__post_init__()
            if result.document_manifest_hash != ablation_plan.document_manifest_hash or result.shared_index_fingerprint != ablation_plan.shared_index_fingerprint:
                raise ArtifactIntegrityError("ablation result does not match the plan")
            serialized = serialize_ablation_result(result)
            verified = deserialize_ablation_result(serialized, ablation_plan=ablation_plan)
            data = serialized.encode("utf-8")
            if serialize_ablation_result(verified) != serialized:
                raise ArtifactIntegrityError("ablation serialization is not stable")
        except ArtifactStoreError:
            raise
        except Exception as exc:
            raise ArtifactStoreError("ablation artifact validation failed") from exc
        return self._publish("ablation_result", data)

    def load_ablation_result(self, artifact_id: str, *, ablation_plan: AblationPlan) -> AblationRunResult:
        if not isinstance(ablation_plan, AblationPlan):
            raise ArtifactStoreError("ablation plan is invalid")
        ablation_plan.__post_init__()
        digest = self._validate_id(artifact_id)
        path = self._destination("ablation_result", digest)
        if not path.exists():
            other = self._destination("evaluation_result", digest)
            if other.exists():
                raise ArtifactIntegrityError("artifact type does not match loader")
            raise ArtifactNotFoundError("artifact was not found")
        data = self._bounded_read(path)
        if hashlib.sha256(data).hexdigest() != digest:
            raise ArtifactIntegrityError("published artifact content does not match its ID")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ArtifactIntegrityError("artifact is not valid UTF-8") from exc
        try:
            return deserialize_ablation_result(text, ablation_plan=ablation_plan)
        except Exception as exc:
            raise ArtifactIntegrityError("ablation artifact failed validation") from exc

    def list_artifacts(self, artifact_type: str | None = None) -> tuple[StoredArtifact, ...]:
        kinds = (self._validate_type(artifact_type),) if artifact_type is not None else _ARTIFACT_TYPES
        if not self._root.exists():
            return ()
        if self._root.is_symlink() or not self._root.is_dir():
            raise ArtifactIntegrityError("store root is not a regular directory")
        for entry in self._root.iterdir():
            if entry.is_symlink() or entry.name not in _ARTIFACT_TYPES:
                raise ArtifactIntegrityError("store root contains an unexpected entry")
        result: list[StoredArtifact] = []
        for kind in kinds:
            directory = self._root / kind
            if not directory.exists():
                continue
            self._check_component(directory)
            if not directory.is_dir():
                raise ArtifactIntegrityError("artifact type directory is invalid")
            for prefix in sorted(directory.iterdir(), key=lambda item: item.name):
                if prefix.is_symlink():
                    raise ArtifactIntegrityError("store symlinks are not supported")
                if not prefix.is_dir() or not _PREFIX_RE.fullmatch(prefix.name):
                    raise ArtifactIntegrityError("artifact directory layout is invalid")
                for path in sorted(prefix.iterdir(), key=lambda item: item.name):
                    if path.name.startswith(".artifact-") and path.name.endswith(".tmp"):
                        continue
                    if path.is_symlink() or not path.is_file() or not path.name.endswith(".json"):
                        raise ArtifactIntegrityError("unexpected artifact entry")
                    digest = path.name[:-5]
                    if not _ID_RE.fullmatch(digest) or digest[:2] != prefix.name:
                        raise ArtifactIntegrityError("artifact filename is invalid")
                    data = self._bounded_read(path)
                    if hashlib.sha256(data).hexdigest() != digest:
                        raise ArtifactIntegrityError("published artifact content does not match its ID")
                    result.append(self._reference(kind, digest, len(data)))
        return tuple(sorted(result, key=lambda item: (item.artifact_type, item.artifact_id)))


__all__ = [
    "ArtifactType",
    "StoredArtifact",
    "ExperimentArtifactStore",
    "ArtifactStoreError",
    "ArtifactNotFoundError",
    "ArtifactIntegrityError",
]
