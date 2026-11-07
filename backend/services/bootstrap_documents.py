"""Bounded PDF imports and manifest-backed bootstrap source inventory."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
from typing import AsyncIterable

from starlette.concurrency import run_in_threadpool

from backend.engine.manifest import (
    DocumentManifestEntry,
    ManifestSourceChangedError,
    ManifestValidationError,
    build_document_manifest,
    document_id_for,
)
from backend.services.bootstrap_jobs import BootstrapJobError, BootstrapJobManager


MAX_DOCUMENT_PAGES = 200
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100
MAX_OFFSET = 1_000_000
MAX_VIEWER_BYTES = 16 * 1024 * 1024
_FILENAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _().-]{0,114}\.pdf\Z", re.ASCII)
_DOCUMENT_ID_RE = re.compile(r"doc_[0-9a-f]{64}\Z", re.ASCII)
_MANIFEST_HASH_RE = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_WINDOWS_RESERVED_STEMS = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


class BootstrapDocumentError(ValueError):
    def __init__(self, code: str, status_code: int = 400):
        self.code = code
        self.status_code = status_code
        super().__init__(code)


@dataclass(frozen=True)
class DocumentInventory:
    documents: tuple[DocumentManifestEntry, ...]
    manifest_hash: str
    total: int
    limit: int
    offset: int
    complete: bool


@dataclass(frozen=True)
class DocumentImportResult:
    outcome: str
    document: DocumentManifestEntry
    manifest_hash: str | None
    error_code: str | None


@dataclass(frozen=True)
class DocumentContent:
    entry: DocumentManifestEntry
    manifest_hash: str
    page_count: int
    content: bytes


def validate_filename(value: object) -> str:
    """Accept a short ASCII basename with a lowercase .pdf suffix only."""
    if not isinstance(value, str) or len(value) > 120 or not _FILENAME_RE.fullmatch(value):
        raise BootstrapDocumentError("filename_invalid")
    stem = value[:-4].split(".", 1)[0].rstrip(" .").upper()
    if stem in _WINDOWS_RESERVED_STEMS or value.endswith((" ", ".")):
        raise BootstrapDocumentError("filename_invalid")
    return value


def _validate_source_root(value: str | Path) -> Path:
    root = Path(value)
    if not root.is_absolute():
        raise BootstrapDocumentError("document_source_unavailable", 503)
    current = Path(root.anchor)
    try:
        for part in root.parts[1:]:
            current = current / part
            if current.is_symlink() or _is_reparse_point(current):
                raise BootstrapDocumentError("document_source_unavailable", 503)
        resolved = root.resolve(strict=True)
        if not resolved.is_dir() or os.path.normcase(str(resolved)) != os.path.normcase(str(root)):
            raise BootstrapDocumentError("document_source_unavailable", 503)
    except BootstrapDocumentError:
        raise
    except OSError as exc:
        raise BootstrapDocumentError("document_source_unavailable", 503) from exc
    return resolved


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _relative_parts(value: str) -> tuple[str, ...]:
    relative = PurePosixPath(value)
    if (not value or str(relative) != value or relative.is_absolute() or "\\" in value or
            any(part in {"", ".", ".."} for part in relative.parts)):
        raise BootstrapDocumentError("document_unavailable", 404)
    return relative.parts


def _open_posix_manifest_file(source: Path, parts: tuple[str, ...]) -> int:
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    nofollow_flag = getattr(os, "O_NOFOLLOW", 0)
    if not directory_flag or not nofollow_flag or os.open not in getattr(os, "supports_dir_fd", set()):
        raise BootstrapDocumentError("document_source_unavailable", 503)
    current = os.open(source, os.O_RDONLY | directory_flag | nofollow_flag)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | directory_flag | nofollow_flag, dir_fd=current)
            os.close(current)
            current = child
        return os.open(parts[-1], os.O_RDONLY | nofollow_flag | getattr(os, "O_NONBLOCK", 0) |
                       getattr(os, "O_BINARY", 0), dir_fd=current)
    finally:
        os.close(current)


def _windows_final_path(handle: int) -> str:
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_final = kernel32.GetFinalPathNameByHandleW
    get_final.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32]
    get_final.restype = ctypes.c_uint32
    required = get_final(ctypes.c_void_p(handle), None, 0, 0)
    if not 1 <= required <= 32768:
        raise BootstrapDocumentError("document_unavailable", 404)
    buffer = ctypes.create_unicode_buffer(required + 1)
    written = get_final(ctypes.c_void_p(handle), buffer, len(buffer), 0)
    if not 1 <= written < len(buffer):
        raise BootstrapDocumentError("document_unavailable", 404)
    value = buffer.value
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return os.path.normcase(os.path.abspath(value)).rstrip("\\/")


def _open_windows_manifest_file(source: Path, parts: tuple[str, ...]) -> int:
    import ctypes
    import msvcrt

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                            ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    create_file.restype = ctypes.c_void_p
    handle = create_file(str(source.joinpath(*parts)), 0x80000000, 0x00000001, None,
                         3, 0x00200000 | 0x08000000, None)  # GENERIC_READ; share-read; OPEN_EXISTING; OPEN_REPARSE_POINT | SEQUENTIAL_SCAN
    invalid = ctypes.c_void_p(-1).value
    if handle in (None, invalid):
        error = ctypes.get_last_error()
        if error in {2, 3}:
            raise BootstrapDocumentError("document_unavailable", 404)
        raise BootstrapDocumentError("document_source_unavailable", 503)
    try:
        class FileTime(ctypes.Structure):
            _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]

        class FileInformation(ctypes.Structure):
            _fields_ = [("attributes", ctypes.c_uint32), ("creation", FileTime), ("access", FileTime),
                        ("write", FileTime), ("size_high", ctypes.c_uint32), ("size_low", ctypes.c_uint32),
                        ("links", ctypes.c_uint32), ("volume", ctypes.c_uint32),
                        ("index_high", ctypes.c_uint32), ("index_low", ctypes.c_uint32)]

        get_info = kernel32.GetFileInformationByHandle
        get_info.argtypes = [ctypes.c_void_p, ctypes.POINTER(FileInformation)]
        get_info.restype = ctypes.c_int
        get_type = kernel32.GetFileType
        get_type.argtypes = [ctypes.c_void_p]
        get_type.restype = ctypes.c_uint32
        info = FileInformation()
        if not get_info(handle, ctypes.byref(info)) or get_type(handle) != 1:  # FILE_TYPE_DISK
            raise BootstrapDocumentError("document_source_unavailable", 503)
        if info.attributes & (0x10 | 0x400):  # directory or reparse point
            raise BootstrapDocumentError("document_unavailable", 404)
        expected = os.path.normcase(os.path.abspath(str(source.joinpath(*parts)))).rstrip("\\/")
        if _windows_final_path(handle) != expected:
            raise BootstrapDocumentError("document_unavailable", 404)
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        handle = None
        return descriptor
    finally:
        if handle not in (None, invalid):
            kernel32.CloseHandle(ctypes.c_void_p(handle))


def _read_open_file(source: Path, entry: DocumentManifestEntry, maximum_bytes: int) -> bytes:
    parts = _relative_parts(entry.relative_path)
    for index, _part in enumerate(parts[:-1], start=1):
        candidate = source.joinpath(*parts[:index])
        if candidate.is_symlink() or _is_reparse_point(candidate):
            raise BootstrapDocumentError("document_unavailable", 404)
    if os.name == "nt":
        descriptor = _open_windows_manifest_file(source, parts)
    elif os.name == "posix":
        try:
            descriptor = _open_posix_manifest_file(source, parts)
        except FileNotFoundError as exc:
            raise BootstrapDocumentError("document_unavailable", 404) from exc
        except OSError as exc:
            if getattr(exc, "errno", None) in {getattr(os, "ELOOP", 40), 40}:
                raise BootstrapDocumentError("document_unavailable", 404) from exc
            raise BootstrapDocumentError("document_source_unavailable", 503) from exc
    else:
        raise BootstrapDocumentError("document_source_unavailable", 503)

    try:
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise BootstrapDocumentError("document_unavailable", 404)
            if before.st_size < 1 or before.st_size > maximum_bytes:
                raise BootstrapDocumentError("document_too_large", 413) if before.st_size > maximum_bytes else BootstrapDocumentError("document_invalid", 400)
            content = stream.read(maximum_bytes + 1)
            after = os.fstat(stream.fileno())
            if len(content) > maximum_bytes:
                raise BootstrapDocumentError("document_too_large", 413)
            stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
            if any(getattr(before, field, None) != getattr(after, field, None) for field in stable_fields):
                raise BootstrapDocumentError("document_changed", 409)
            if len(content) != entry.size_bytes or hashlib.sha256(content).hexdigest() != entry.sha256:
                raise BootstrapDocumentError("document_changed", 409)
            if not content.startswith(b"%PDF-"):
                raise BootstrapDocumentError("document_invalid", 400)
            return content
    except BootstrapDocumentError:
        raise
    except OSError as exc:
        raise BootstrapDocumentError("document_unavailable", 404) from exc


def read_document_content(
    source_root: str | Path,
    document_id: str,
    expected_manifest_hash: str,
    *,
    maximum_bytes: int = MAX_VIEWER_BYTES,
) -> DocumentContent:
    """Read one unchanged PDF selected by its current manifest identity."""

    if not isinstance(document_id, str) or not _DOCUMENT_ID_RE.fullmatch(document_id):
        raise BootstrapDocumentError("document_id_invalid", 400)
    if not isinstance(expected_manifest_hash, str) or not _MANIFEST_HASH_RE.fullmatch(expected_manifest_hash):
        raise BootstrapDocumentError("document_manifest_invalid", 400)
    if isinstance(maximum_bytes, bool) or not isinstance(maximum_bytes, int) or not 1 <= maximum_bytes <= MAX_VIEWER_BYTES:
        raise BootstrapDocumentError("document_view_limit_invalid", 503)
    source = _validate_source_root(source_root)
    manifest = _check_manifest(source)
    if manifest.content_hash != expected_manifest_hash:
        raise BootstrapDocumentError("document_manifest_changed", 409)
    entry = next((item for item in manifest.entries if item.document_id == document_id), None)
    if entry is None:
        raise BootstrapDocumentError("document_not_found", 404)
    if entry.size_bytes > maximum_bytes:
        raise BootstrapDocumentError("document_too_large", 413)
    content = _read_open_file(source, entry, maximum_bytes)
    try:
        import pymupdf

        document = pymupdf.open(stream=content, filetype="pdf")
        try:
            if not document.is_pdf or document.is_encrypted or document.needs_pass:
                raise BootstrapDocumentError("document_invalid", 400)
            pages = document.page_count
            if not 1 <= pages <= MAX_DOCUMENT_PAGES:
                raise BootstrapDocumentError("document_invalid", 400)
        finally:
            document.close()
    except BootstrapDocumentError:
        raise
    except Exception as exc:
        raise BootstrapDocumentError("document_invalid", 400) from exc
    return DocumentContent(entry=entry, manifest_hash=manifest.content_hash, page_count=pages, content=content)


def _check_manifest(source: Path):
    try:
        return build_document_manifest(source)
    except (ManifestSourceChangedError, ManifestValidationError, OSError, ValueError) as exc:
        raise BootstrapDocumentError("document_manifest_unavailable", 503) from exc


def list_documents(source_root: str | Path, *, limit: int, offset: int) -> DocumentInventory:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_PAGE_SIZE:
        raise BootstrapDocumentError("pagination_invalid")
    if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= MAX_OFFSET:
        raise BootstrapDocumentError("pagination_invalid")
    source = _validate_source_root(source_root)
    manifest = _check_manifest(source)
    total = len(manifest.entries)
    return DocumentInventory(
        documents=manifest.entries[offset:offset + limit],
        manifest_hash=manifest.content_hash,
        total=total,
        limit=limit,
        offset=offset,
        complete=True,
    )


def _check_collision(manifest, filename: str) -> None:
    requested_name = filename.casefold()
    requested_stem = filename[:-4].casefold()
    for entry in manifest.entries:
        existing_name = PurePosixPath(entry.relative_path).name
        existing_stem = existing_name[:-4].casefold() if existing_name.lower().endswith(".pdf") else existing_name.casefold()
        if existing_name.casefold() == requested_name or existing_stem == requested_stem:
            raise BootstrapDocumentError("document_name_conflict", 409)


def _validate_pdf(path: Path, *, expected_size: int, maximum_bytes: int) -> None:
    document = None
    try:
        import pymupdf

        # The file is first streamed to disk. Validation then reads at most the
        # configured 16 MiB ceiling into memory so a failed parser open cannot
        # leave a Windows file handle locking the temporary upload.
        with path.open("rb") as handle:
            payload = handle.read(maximum_bytes + 1)
        if len(payload) > maximum_bytes or len(payload) != expected_size:
            raise BootstrapDocumentError("upload_source_changed", 409)
        document = pymupdf.open(stream=payload, filetype="pdf")
        if not document.is_pdf or document.is_encrypted or document.needs_pass:
            raise BootstrapDocumentError("pdf_encrypted_or_unsupported", 400)
        page_count = document.page_count
        if page_count < 1:
            raise BootstrapDocumentError("pdf_empty", 400)
        if page_count > MAX_DOCUMENT_PAGES:
            raise BootstrapDocumentError("pdf_page_limit_exceeded", 413)
        # Load page trees without extracting text or rendering content.
        for index in range(page_count):
            page = document.load_page(index)
            rect = page.rect
            if not (rect.is_valid and rect.width > 0 and rect.height > 0):
                raise BootstrapDocumentError("pdf_structure_invalid", 400)
    except BootstrapDocumentError:
        raise
    except Exception as exc:
        raise BootstrapDocumentError("pdf_invalid", 400) from exc
    finally:
        if document is not None:
            try:
                document.close()
            except Exception as exc:
                raise BootstrapDocumentError("pdf_invalid", 400) from exc


def _parse_content_length(value: str | None, maximum: int) -> None:
    if value is None:
        return
    if not re.fullmatch(r"(?:0|[1-9][0-9]{0,9})", value):
        raise BootstrapDocumentError("content_length_invalid", 400)
    if int(value) > maximum:
        raise BootstrapDocumentError("upload_too_large", 413)


async def import_pdf(
    source_root: str | Path,
    manager: BootstrapJobManager,
    chunks: AsyncIterable[bytes],
    *,
    filename: str,
    content_type: str,
    content_encoding: str | None,
    content_length: str | None,
    maximum_bytes: int,
) -> DocumentImportResult:
    safe_name = validate_filename(filename)
    if content_type.strip().lower() != "application/pdf":
        raise BootstrapDocumentError("unsupported_content_type", 415)
    if content_encoding not in (None, "", "identity"):
        raise BootstrapDocumentError("content_encoding_unsupported", 415)
    _parse_content_length(content_length, maximum_bytes)
    source = _validate_source_root(source_root)

    try:
        mutation = manager.source_mutation()
        with mutation:
            current = await run_in_threadpool(_check_manifest, source)
            _check_collision(current, safe_name)
            fd, temporary_name = tempfile.mkstemp(prefix=".rag-upload-", suffix=".part", dir=source)
            temporary = Path(temporary_name)
            published = False
            byte_count = 0
            digest = hashlib.sha256()
            signature = bytearray()
            try:
                try:
                    with os.fdopen(fd, "wb") as handle:
                        async for chunk in chunks:
                            if not isinstance(chunk, bytes):
                                raise BootstrapDocumentError("upload_stream_invalid", 400)
                            byte_count += len(chunk)
                            if byte_count > maximum_bytes:
                                raise BootstrapDocumentError("upload_too_large", 413)
                            if len(signature) < 5:
                                signature.extend(chunk[:5 - len(signature)])
                            digest.update(chunk)
                            handle.write(chunk)
                        handle.flush()
                        os.fsync(handle.fileno())
                except BootstrapDocumentError:
                    raise
                except Exception as exc:
                    raise BootstrapDocumentError("upload_write_failed", 503) from exc

                if byte_count == 0:
                    raise BootstrapDocumentError("upload_empty", 400)
                if not signature.startswith(b"%PDF-"):
                    raise BootstrapDocumentError("pdf_signature_invalid", 400)
                await run_in_threadpool(
                    _validate_pdf,
                    temporary,
                    expected_size=byte_count,
                    maximum_bytes=maximum_bytes,
                )

                # Re-scan immediately before publication while the manager's
                # source-mutation gate prevents cooperating build admissions.
                current = await run_in_threadpool(_check_manifest, source)
                _check_collision(current, safe_name)
                destination = source / safe_name
                try:
                    os.link(temporary, destination)
                    published = True
                except FileExistsError as exc:
                    raise BootstrapDocumentError("document_name_conflict", 409) from exc
                except OSError as exc:
                    try:
                        published = os.path.samefile(temporary, destination)
                    except OSError:
                        published = False
                    if published:
                        digest_value = digest.hexdigest()
                        record = DocumentManifestEntry(
                            relative_path=safe_name,
                            size_bytes=byte_count,
                            sha256=digest_value,
                            document_id=document_id_for(safe_name, digest_value),
                        )
                        return DocumentImportResult(
                            "committed_inventory_unavailable", record, None, "publication_confirmation_failed"
                        )
                    raise BootstrapDocumentError("document_publication_failed", 503) from exc

                try:
                    temporary.unlink()
                except OSError:
                    record = DocumentManifestEntry(
                        relative_path=safe_name,
                        size_bytes=byte_count,
                        sha256=digest.hexdigest(),
                        document_id=document_id_for(safe_name, digest.hexdigest()),
                    )
                    return DocumentImportResult("committed_inventory_unavailable", record, None, "temporary_cleanup_failed")

                record = DocumentManifestEntry(
                    relative_path=safe_name,
                    size_bytes=byte_count,
                    sha256=digest.hexdigest(),
                    document_id=document_id_for(safe_name, digest.hexdigest()),
                )
                try:
                    confirmed = await run_in_threadpool(_check_manifest, source)
                    observed = next((entry for entry in confirmed.entries if entry.relative_path == safe_name), None)
                    if observed != record:
                        return DocumentImportResult("committed_inventory_unavailable", record, None, "manifest_confirmation_failed")
                except Exception:
                    return DocumentImportResult("committed_inventory_unavailable", record, None, "manifest_confirmation_failed")
                return DocumentImportResult("imported", record, confirmed.content_hash, None)
            finally:
                if not published or temporary.exists():
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError as exc:
                        if not published:
                            raise BootstrapDocumentError("upload_cleanup_failed", 503) from exc
    except BootstrapJobError as exc:
        status = 409 if exc.code in {"source_mutation_active", "source_build_active"} else 503
        code = "document_source_busy" if exc.code in {"source_mutation_active", "source_build_active"} else "bootstrap_service_unavailable"
        raise BootstrapDocumentError(code, status) from exc


__all__ = [
    "MAX_DOCUMENT_PAGES", "DEFAULT_PAGE_SIZE", "MAX_PAGE_SIZE", "MAX_OFFSET",
    "BootstrapDocumentError", "DocumentInventory", "DocumentImportResult",
    "validate_filename", "list_documents", "import_pdf",
]
