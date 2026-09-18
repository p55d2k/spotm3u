"""Secure storage for uploaded Exportify archives."""

from __future__ import annotations

import secrets
import shutil
import tempfile
import time
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .config import (
    DEFAULT_MAX_ARCHIVE_ENTRIES,
    DEFAULT_MAX_DECOMPRESSED_SIZE,
)


class UploadError(ValueError):
    """An upload could not be accepted safely."""


@dataclass(frozen=True)
class UploadJob:
    """Server-side locations belonging to one upload."""

    job_id: str
    directory: Path
    archive: Path
    extracted: Path
    state: Path
    output: Path


def store_upload(
    uploaded_file,
    *,
    upload_root: Path,
    max_upload_size: int,
    max_decompressed_size: int = DEFAULT_MAX_DECOMPRESSED_SIZE,
    max_archive_entries: int = DEFAULT_MAX_ARCHIVE_ENTRIES,
) -> UploadJob:
    """Validate, store, and safely extract one uploaded ZIP archive.

    ``max_upload_size`` bounds the compressed archive bytes, while
    ``max_decompressed_size`` bounds the total expanded bytes and
    ``max_archive_entries`` bounds the number of extracted entries. These
    caps protect against zip bombs and path-exhaustion archives.
    """
    filename = uploaded_file.filename or ""
    if not filename.lower().endswith(".zip"):
        raise UploadError("Please upload the ZIP file downloaded from Exportify.")

    upload_root = Path(upload_root)
    upload_root.mkdir(parents=True, exist_ok=True)
    job_id = secrets.token_urlsafe(16)
    job_directory = upload_root / f"job-{job_id}"
    archive_path = job_directory / "export.zip"
    extracted_directory = job_directory / "extracted"
    state_path = job_directory / "state.json"
    output_directory = job_directory / "output"

    try:
        job_directory.mkdir()
        _write_limited(uploaded_file, archive_path, max_upload_size)
        _extract_zip(
            archive_path,
            extracted_directory,
            max_decompressed_size=max_decompressed_size,
            max_archive_entries=max_archive_entries,
        )
        _remove_archive(archive_path)
        output_directory.mkdir()
        state_path.write_text("{}", encoding="utf-8")
    except (zipfile.BadZipFile, zipfile.LargeZipFile) as error:
        shutil.rmtree(job_directory, ignore_errors=True)
        raise UploadError("The uploaded file is not a valid ZIP archive.") from error
    except (OSError, UploadError):
        shutil.rmtree(job_directory, ignore_errors=True)
        raise

    return UploadJob(
        job_id=job_id,
        directory=job_directory,
        archive=archive_path,
        extracted=extracted_directory,
        state=state_path,
        output=output_directory,
    )


def _remove_archive(archive_path: Path) -> None:
    """Best-effort removal of the stored archive after successful extraction."""
    try:
        archive_path.unlink(missing_ok=True)
    except OSError:
        pass


def _write_limited(uploaded_file, destination: Path, max_upload_size: int) -> None:
    if max_upload_size <= 0:
        raise ValueError("max_upload_size must be positive")

    total = 0
    with destination.open("wb") as output:
        while chunk := uploaded_file.stream.read(1024 * 1024):
            total += len(chunk)
            if total > max_upload_size:
                raise UploadError("That file is too large to upload.")
            output.write(chunk)


def _extract_zip(
    archive_path: Path,
    destination: Path,
    *,
    max_decompressed_size: int,
    max_archive_entries: int,
) -> None:
    if max_archive_entries <= 0 or max_decompressed_size <= 0:
        raise UploadError("The uploaded ZIP could not be extracted safely.")
    destination.mkdir()
    destination_root = destination.resolve()

    with zipfile.ZipFile(archive_path) as archive:
        entries = archive.infolist()
        if len(entries) > max_archive_entries:
            raise UploadError("The uploaded ZIP contains too many files.")
        if archive.testzip() is not None:
            raise UploadError("The uploaded ZIP is damaged and cannot be read.")

        extracted_total = 0
        for entry in entries:
            relative_path = _safe_archive_path(entry.filename)
            target = (destination / relative_path).resolve()
            if destination_root not in target.parents and target != destination_root:
                raise UploadError("The uploaded ZIP contains an unsafe path.")
            if not _is_regular_entry(entry):
                raise UploadError("The uploaded ZIP contains an unsupported entry.")

            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue

            target.parent.mkdir(parents=True, exist_ok=True)
            extracted_total += _extract_file(
                archive,
                entry,
                target,
                max_decompressed_size,
                extracted_total,
            )


def _extract_file(
    archive: zipfile.ZipFile,
    entry: zipfile.ZipInfo,
    target: Path,
    max_decompressed_size: int,
    extracted_so_far: int,
) -> int:
    if entry.file_size > max_decompressed_size:
        raise UploadError("The uploaded ZIP expands to too much data.")
    written = 0
    with archive.open(entry) as source, target.open("wb") as output:
        while chunk := source.read(64 * 1024):
            written += len(chunk)
            if extracted_so_far + written > max_decompressed_size:
                raise UploadError("The uploaded ZIP expands to too much data.")
            output.write(chunk)
    return written


def _safe_archive_path(name: str) -> Path:
    if not name or "\\" in name:
        raise UploadError("The uploaded ZIP contains an unsafe path.")
    if any(char < " " for char in name):
        raise UploadError("The uploaded ZIP contains an unsafe path.")
    if len(name) > 4096:
        raise UploadError("The uploaded ZIP contains an unsafe path.")

    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or (path.parts and path.parts[0].endswith(":"))
    ):
        raise UploadError("The uploaded ZIP contains an unsafe path.")
    return Path(*path.parts)


def _is_regular_entry(entry: zipfile.ZipInfo) -> bool:
    """True for plain files and directories; false for links and special files."""
    if _is_symlink(entry):
        return False
    if entry.is_dir():
        return True
    file_type = (entry.external_attr >> 16) & 0o170000
    return file_type in (0, 0o100000)


def _is_symlink(entry: zipfile.ZipInfo) -> bool:
    file_type = (entry.external_attr >> 16) & 0o170000
    return file_type == 0o120000


def cleanup_jobs(
    upload_root: str | Path,
    *,
    max_age_seconds: float,
    active_job_ids: Iterable[str] = (),
    now: float | None = None,
) -> int:
    """Remove old, abandoned upload job directories.

    A job directory is removed only when ``max_age_seconds`` have passed since
    it was created and its ``job_id`` is not in ``active_job_ids``, so cleanup
    never deletes data while a job is still processing. Returns the number of
    directories removed.
    """
    if max_age_seconds <= 0:
        raise ValueError("max_age_seconds must be positive")
    root = Path(upload_root)
    if not root.is_dir():
        return 0

    timestamp = time.time() if now is None else now
    active = set(active_job_ids or ())
    removed = 0
    for directory in root.iterdir():
        if not directory.is_dir() or not directory.name.startswith("job-"):
            continue
        job_id = directory.name[len("job-"):]
        if job_id in active:
            continue
        try:
            age = timestamp - directory.stat().st_mtime
        except OSError:
            continue
        if age < max_age_seconds:
            continue
        shutil.rmtree(directory, ignore_errors=True)
        removed += 1
    return removed


def default_upload_root() -> Path:
    """Return the default location for temporary upload jobs."""
    return Path(tempfile.gettempdir()) / "spotm3u"
