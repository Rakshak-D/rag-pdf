"""A small persistent Chroma adapter for verified RAG index identities.

The adapter intentionally accepts already-computed vectors.  It does not
initialize an embedding function, model client, or any other RAG component.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import math
from numbers import Real
import os
from pathlib import Path
import uuid
from typing import Any

import chromadb
from pydantic import BaseModel, ConfigDict, field_validator

from backend.config.fingerprint import INDEX_SCHEMA_VERSION
from backend.config.paths import resolve_disjoint_paths, resolve_owned_path
from backend.experiments.run_plan import RunPlan


INDEX_STORE_SCHEMA_VERSION = "1"
TEXT_COLLECTION_NAME = "text_vectors"
IMAGE_COLLECTION_NAME = "image_vectors"
_SHA256_LENGTH = 64
_IDENTITY_KEYS = (
    "store_schema_version",
    "index_fingerprint",
    "document_manifest_hash",
    "index_schema_version",
    "modality",
    "collection_name",
)


class ChromaStoreError(RuntimeError):
    """Base exception for vector-store adapter failures."""


class ChromaStoreInputError(ChromaStoreError, ValueError):
    """Raised before Chroma when vector-operation inputs are invalid."""


class ChromaStoreStateError(ChromaStoreError):
    """Raised when persistent store state is incomplete or incompatible."""


class _IndexStoreManifest(BaseModel):
    """Portable identity metadata stored beside one Chroma database."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    store_schema_version: str = INDEX_STORE_SCHEMA_VERSION
    index_fingerprint: str
    document_manifest_hash: str
    index_schema_version: str = INDEX_SCHEMA_VERSION
    text_collection_name: str = TEXT_COLLECTION_NAME
    image_collection_name: str = IMAGE_COLLECTION_NAME

    @field_validator("index_fingerprint", "document_manifest_hash")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        if len(value) != _SHA256_LENGTH or any(c not in "0123456789abcdefABCDEF" for c in value):
            raise ValueError("identity hashes must be exactly 64 hexadecimal characters")
        return value.lower()

    @field_validator(
        "store_schema_version",
        "index_schema_version",
        "text_collection_name",
        "image_collection_name",
    )
    @classmethod
    def validate_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("store identity fields must be non-empty")
        return value

    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )


def _identity_metadata(
    manifest: _IndexStoreManifest,
    modality: str,
    collection_name: str,
) -> dict[str, str]:
    return {
        "store_schema_version": manifest.store_schema_version,
        "index_fingerprint": manifest.index_fingerprint,
        "document_manifest_hash": manifest.document_manifest_hash,
        "index_schema_version": manifest.index_schema_version,
        "modality": modality,
        "collection_name": collection_name,
    }


def _load_manifest(path: Path) -> _IndexStoreManifest:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return _IndexStoreManifest.model_validate(payload)
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ChromaStoreStateError(f"invalid index manifest: {path}") from exc


def _write_manifest_atomic(path: Path, manifest: _IndexStoreManifest) -> None:
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(manifest.canonical_json())
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        if temporary.exists():
            temporary.unlink()
        raise ChromaStoreStateError(f"could not atomically write index manifest: {path}") from exc


def _validate_collection_metadata(
    collection: Any,
    manifest: _IndexStoreManifest,
    modality: str,
    collection_name: str,
) -> None:
    expected = _identity_metadata(manifest, modality, collection_name)
    metadata = collection.metadata or {}
    mismatches = [
        f"{key}={metadata.get(key)!r}, expected {value!r}"
        for key, value in expected.items()
        if metadata.get(key) != value
    ]
    if metadata.get("hnsw:space") != "cosine":
        mismatches.append(
            f"hnsw:space={metadata.get('hnsw:space')!r}, expected 'cosine'"
        )
    if mismatches:
        raise ChromaStoreStateError(
            f"collection {collection_name!r} metadata mismatch: " + "; ".join(mismatches)
        )


def _validate_index_hierarchy(
    storage_root: Path,
    index_fingerprint: str,
) -> tuple[Path, Path, Path, Path]:
    """Validate fixed index path components before resolving or creating them."""

    indexes_path = storage_root / "indexes"
    index_root = indexes_path / index_fingerprint
    manifest_path = index_root / "index-manifest.json"
    chroma_path = index_root / "chroma"
    components = (
        ("indexes directory", indexes_path),
        ("index directory", index_root),
        ("index manifest", manifest_path),
        ("Chroma directory", chroma_path),
    )
    for label, path in components:
        if path.is_symlink():
            raise ChromaStoreStateError(
                f"symlinked {label} is not allowed in the reserved index hierarchy: {path}"
            )
        resolved = path.resolve(strict=False)
        try:
            resolved.relative_to(storage_root)
        except ValueError as exc:
            raise ChromaStoreStateError(
                f"{label} resolves outside storage root: {path}"
            ) from exc
    return indexes_path, index_root, manifest_path, chroma_path


def _as_vector(value: Any, *, label: str) -> list[float]:
    if isinstance(value, (str, bytes, bytearray)):
        raise ChromaStoreInputError(f"{label} must be a non-empty numeric vector")
    try:
        vector = list(value)
    except TypeError as exc:
        raise ChromaStoreInputError(f"{label} must be a non-empty numeric vector") from exc
    if not vector:
        raise ChromaStoreInputError(f"{label} must be non-empty")
    normalized: list[float] = []
    for item in vector:
        if isinstance(item, bool) or not isinstance(item, Real) or not math.isfinite(float(item)):
            raise ChromaStoreInputError(f"{label} must contain only finite numeric values")
        normalized.append(float(item))
    return normalized


def _validate_ids(ids: Sequence[str]) -> list[str]:
    if isinstance(ids, (str, bytes, bytearray)):
        raise ChromaStoreInputError("ids must be a sequence of non-empty strings")
    values = list(ids)
    if not values:
        raise ChromaStoreInputError("ids must not be empty")
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ChromaStoreInputError("ids must contain only non-empty strings")
    if len(values) != len(set(values)):
        raise ChromaStoreInputError("ids must be unique within a batch")
    return values


def _validate_metadata(value: Mapping[str, Any], *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ChromaStoreInputError(f"{label} must be a metadata mapping")
    if not value:
        raise ChromaStoreInputError(f"{label} must contain at least one attribute")
    result: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip():
            raise ChromaStoreInputError(f"{label} keys must be non-empty strings")
        if isinstance(item, bool):
            result[key] = item
        elif isinstance(item, (str, int)):
            result[key] = item
        elif isinstance(item, float) and math.isfinite(item):
            result[key] = item
        else:
            raise ChromaStoreInputError(
                f"{label}[{key!r}] must be a finite scalar string, integer, float, or boolean"
            )
    return result


def _validate_metadatas(metadatas: Sequence[Mapping[str, Any]] | None, count: int) -> list[dict[str, Any]] | None:
    if metadatas is None:
        return None
    if isinstance(metadatas, (str, bytes, bytearray)):
        raise ChromaStoreInputError("metadatas must be a sequence matching ids")
    values = list(metadatas)
    if len(values) != count:
        raise ChromaStoreInputError("metadatas length must match ids length")
    return [_validate_metadata(value, label=f"metadata[{index}]") for index, value in enumerate(values)]


class ChromaVectorStore:
    """Persistent text/image collections isolated by a verified RunPlan."""

    def __init__(
        self,
        *,
        storage_root: Path,
        index_root: Path,
        client: Any,
        text_collection: Any,
        image_collection: Any,
        manifest: _IndexStoreManifest,
    ) -> None:
        self.storage_root = storage_root
        self.index_root = index_root
        self.manifest = manifest
        self._client = client
        self._collections = {
            "text": text_collection,
            "image": image_collection,
        }

    @classmethod
    def _open(
        cls,
        storage_root: str | Path,
        run_plan: RunPlan,
        *,
        create: bool,
    ) -> "ChromaVectorStore":
        """Open a verified index, optionally creating a new empty store."""

        if not isinstance(run_plan, RunPlan):
            raise TypeError("ChromaVectorStore.open requires a RunPlan")
        run_plan.verify_integrity()
        config = run_plan.resolved_config()

        owned_root, _ = resolve_disjoint_paths(
            storage_root,
            config.pdf_dir,
            first_label="vector-store storage root",
            second_label="configured PDF source root",
        )
        _, raw_index_root, raw_manifest_path, raw_chroma_path = _validate_index_hierarchy(
            owned_root,
            run_plan.index_fingerprint,
        )
        index_root = resolve_owned_path(owned_root, raw_index_root.relative_to(owned_root))
        manifest_path = resolve_owned_path(owned_root, raw_manifest_path.relative_to(owned_root))
        chroma_path = resolve_owned_path(owned_root, raw_chroma_path.relative_to(owned_root))
        manifest = _IndexStoreManifest(
            index_fingerprint=run_plan.index_fingerprint,
            document_manifest_hash=run_plan.document_manifest_hash,
        )

        is_new = not index_root.exists()
        if is_new and not create:
            raise ChromaStoreStateError(
                f"index store does not exist: {index_root}"
            )
        if not is_new:
            if not index_root.is_dir():
                raise ChromaStoreStateError(f"index path is not a directory: {index_root}")
            if not manifest_path.is_file() or not chroma_path.is_dir():
                raise ChromaStoreStateError(
                    f"incomplete index store: expected {manifest_path} and {chroma_path}"
                )
            existing = _load_manifest(manifest_path)
            if existing != manifest:
                raise ChromaStoreStateError(
                    f"index manifest identity mismatch for {index_root}"
                )
            manifest = existing

        if is_new:
            try:
                index_root.mkdir(parents=True, exist_ok=False)
                chroma_path.mkdir(exist_ok=False)
            except OSError as exc:
                raise ChromaStoreStateError(f"could not create new index store: {index_root}") from exc

        try:
            client = chromadb.PersistentClient(path=str(chroma_path))
            if is_new:
                text_collection = client.create_collection(
                    name=TEXT_COLLECTION_NAME,
                    metadata={**_identity_metadata(manifest, "text", TEXT_COLLECTION_NAME), "hnsw:space": "cosine"},
                    embedding_function=None,
                )
                image_collection = client.create_collection(
                    name=IMAGE_COLLECTION_NAME,
                    metadata={**_identity_metadata(manifest, "image", IMAGE_COLLECTION_NAME), "hnsw:space": "cosine"},
                    embedding_function=None,
                )
                _write_manifest_atomic(manifest_path, manifest)
            else:
                names = {collection.name for collection in client.list_collections()}
                expected_names = {TEXT_COLLECTION_NAME, IMAGE_COLLECTION_NAME}
                if names != expected_names:
                    raise ChromaStoreStateError(
                        f"index collections mismatch: expected {sorted(expected_names)}, got {sorted(names)}"
                    )
                text_collection = client.get_collection(TEXT_COLLECTION_NAME, embedding_function=None)
                image_collection = client.get_collection(IMAGE_COLLECTION_NAME, embedding_function=None)
                _validate_collection_metadata(text_collection, manifest, "text", TEXT_COLLECTION_NAME)
                _validate_collection_metadata(image_collection, manifest, "image", IMAGE_COLLECTION_NAME)
        except ChromaStoreError:
            raise
        except Exception as exc:
            raise ChromaStoreStateError(f"could not open index store: {index_root}") from exc

        return cls(
            storage_root=owned_root,
            index_root=index_root,
            client=client,
            text_collection=text_collection,
            image_collection=image_collection,
            manifest=manifest,
        )

    @classmethod
    def open(cls, storage_root: str | Path, run_plan: RunPlan) -> "ChromaVectorStore":
        """Verify ``run_plan`` and open/create its isolated persistent store."""

        return cls._open(storage_root, run_plan, create=True)

    @classmethod
    def open_existing(
        cls,
        storage_root: str | Path,
        run_plan: RunPlan,
    ) -> "ChromaVectorStore":
        """Open only an existing verified store; never create or repair it."""

        return cls._open(storage_root, run_plan, create=False)

    def close(self) -> None:
        """Release adapter references; Chroma 1.5 has no client close method."""

        self._client = None
        self._collections = {}

    def _collection(self, modality: str) -> Any:
        if self._client is None:
            raise ChromaStoreError("vector store is closed")
        try:
            return self._collections[modality]
        except KeyError as exc:
            raise ChromaStoreInputError(f"unsupported modality: {modality}") from exc

    def _validate_embeddings(self, embeddings: Sequence[Any], count: int) -> list[list[float]]:
        if isinstance(embeddings, (str, bytes, bytearray)):
            raise ChromaStoreInputError("embeddings must be a sequence matching ids")
        values = list(embeddings)
        if len(values) != count:
            raise ChromaStoreInputError("embeddings length must match ids length")
        normalized = [_as_vector(value, label=f"embedding[{index}]") for index, value in enumerate(values)]
        dimensions = {len(value) for value in normalized}
        if len(dimensions) != 1:
            raise ChromaStoreInputError("all embeddings in a batch must have one dimension")
        return normalized

    def upsert_text(
        self,
        ids: Sequence[str],
        embeddings: Sequence[Any],
        documents: Sequence[str],
        metadatas: Sequence[Mapping[str, Any]] | None = None,
    ) -> None:
        values = _validate_ids(ids)
        vectors = self._validate_embeddings(embeddings, len(values))
        docs = list(documents) if not isinstance(documents, (str, bytes, bytearray)) else []
        if len(docs) != len(values) or any(not isinstance(doc, str) for doc in docs):
            raise ChromaStoreInputError("documents must be strings matching ids length")
        metadata = _validate_metadatas(metadatas, len(values))
        try:
            self._collection("text").upsert(
                ids=values, embeddings=vectors, documents=docs, metadatas=metadata
            )
        except Exception as exc:
            raise ChromaStoreInputError(f"Chroma rejected text records: {exc}") from exc

    def upsert_image(
        self,
        ids: Sequence[str],
        embeddings: Sequence[Any],
        metadatas: Sequence[Mapping[str, Any]] | None = None,
    ) -> None:
        values = _validate_ids(ids)
        vectors = self._validate_embeddings(embeddings, len(values))
        metadata = _validate_metadatas(metadatas, len(values))
        try:
            self._collection("image").upsert(ids=values, embeddings=vectors, metadatas=metadata)
        except Exception as exc:
            raise ChromaStoreInputError(f"Chroma rejected image records: {exc}") from exc

    def query_text(self, query_embedding: Any, n_results: int = 10) -> dict[str, Any]:
        return self._query("text", query_embedding, n_results)

    def query_image(self, query_embedding: Any, n_results: int = 10) -> dict[str, Any]:
        return self._query("image", query_embedding, n_results)

    def _query(self, modality: str, query_embedding: Any, n_results: int) -> dict[str, Any]:
        if isinstance(n_results, bool) or not isinstance(n_results, int) or n_results <= 0:
            raise ChromaStoreInputError("n_results must be a positive integer")
        vector = _as_vector(query_embedding, label="query_embedding")
        try:
            result = self._collection(modality).query(
                query_embeddings=[vector],
                n_results=n_results,
                include=["metadatas", "documents", "distances"],
            )
        except Exception as exc:
            raise ChromaStoreInputError(f"Chroma rejected query: {exc}") from exc
        return dict(result)

    def count(self, modality: str) -> int:
        return int(self._collection(modality).count())

    def ids(self, modality: str) -> list[str]:
        result = self._collection(modality).get(include=[])
        return list(result["ids"])
