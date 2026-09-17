"""Tests for secure Exportify ZIP uploads."""

from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest
from werkzeug.datastructures import FileStorage

from spotm3u.uploads import UploadError, store_upload


def file_storage(content: bytes, filename: str = "export.zip") -> FileStorage:
    return FileStorage(stream=BytesIO(content), filename=filename)


def zip_bytes(filename: str = "playlist.csv", content: bytes = b"title") -> bytes:
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr(filename, content)
    return output.getvalue()


def test_store_upload_creates_isolated_job_and_extracts_zip(tmp_path: Path) -> None:
    job = store_upload(
        file_storage(zip_bytes()),
        upload_root=tmp_path,
        max_upload_size=1024,
    )

    assert job.directory.parent == tmp_path
    assert job.archive.name == "export.zip"
    assert job.state.read_text() == "{}"
    assert job.output.is_dir()
    assert job.extracted.joinpath("playlist.csv").read_bytes() == b"title"


@pytest.mark.parametrize("filename", ["export.txt", "", "export.zip.exe"])
def test_store_upload_rejects_non_zip_filename(tmp_path: Path, filename: str) -> None:
    with pytest.raises(UploadError, match="ZIP"):
        store_upload(
            file_storage(b"not a zip", filename),
            upload_root=tmp_path,
            max_upload_size=1024,
        )


@pytest.mark.parametrize("entry", ["../outside.txt", "/outside.txt", "C:/outside.txt"])
def test_store_upload_rejects_unsafe_zip_paths(tmp_path: Path, entry: str) -> None:
    with pytest.raises(UploadError, match="unsafe path"):
        store_upload(
            file_storage(zip_bytes(entry)),
            upload_root=tmp_path,
            max_upload_size=1024,
        )

    assert list(tmp_path.iterdir()) == []


def test_store_upload_rejects_invalid_zip_and_oversized_upload(tmp_path: Path) -> None:
    with pytest.raises(UploadError, match="valid ZIP"):
        store_upload(
            file_storage(b"not a zip"),
            upload_root=tmp_path,
            max_upload_size=1024,
        )

    with pytest.raises(UploadError, match="too large"):
        store_upload(
            file_storage(zip_bytes(content=b"x" * 20)),
            upload_root=tmp_path,
            max_upload_size=10,
        )
