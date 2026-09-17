"""Secure storage for uploaded Exportify archives."""

from __future__ import annotations

import secrets
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


class UploadError(ValueError):
    """An upload could not be accepted safely."""


@dataclass(frozen=True)
class UploadJob:
    """Server-side locations belonging to one upload."""

    job_id: str
    directory: Path
    archive: Path
    extracted: Path


def store_upload(
    uploaded_file,
    *,
    upload_root: Path,
    max_upload_size: int,
) -> UploadJob:
    """Validate, store, and safely extract one uploaded ZIP archive."""
    filename = uploaded_file.filename or ""
    if not filename.lower().endswith(".zip"):
        raise UploadError("Please upload the ZIP file downloaded from Exportify.")

    upload_root = Path(upload_root)
    upload_root.mkdir(parents=True, exist_ok=True)
    job_id = secrets.token_urlsafe(16)
    job_directory = upload_root / f"job-{job_id}"
    archive_path = job_directory / "export.zip"
    extracted_directory = job_directory / "extracted"

    try:
        job_directory.mkdir()
        _write_limited(uploaded_file, archive_path, max_upload_size)
        _extract_zip(archive_path, extracted_directory)
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
    )


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


def _extract_zip(archive_path: Path, destination: Path) -> None:
    destination.mkdir()
    destination_root = destination.resolve()

    with zipfile.ZipFile(archive_path) as archive:
        if archive.testzip() is not None:
            raise UploadError("The uploaded ZIP is damaged and cannot be read.")

        for entry in archive.infolist():
            relative_path = _safe_archive_path(entry.filename)
            target = (destination / relative_path).resolve()
            if destination_root not in target.parents and target != destination_root:
                raise UploadError("The uploaded ZIP contains an unsafe path.")
            if _is_symlink(entry):
                raise UploadError("The uploaded ZIP contains an unsupported link.")

            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue

            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(entry) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)


def _safe_archive_path(name: str) -> Path:
    if not name or "\\" in name:
        raise UploadError("The uploaded ZIP contains an unsafe path.")

    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or (path.parts and path.parts[0].endswith(":"))
    ):
        raise UploadError("The uploaded ZIP contains an unsafe path.")
    return Path(*path.parts)


def _is_symlink(entry: zipfile.ZipInfo) -> bool:
    file_type = (entry.external_attr >> 16) & 0o170000
    return file_type == 0o120000


def default_upload_root() -> Path:
    """Return the default location for temporary upload jobs."""
    return Path(tempfile.gettempdir()) / "spotm3u"
