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
    assert not job.archive.exists()
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


def _multi_entry_zip(count: int) -> bytes:
    """A ZIP holding ``count`` tiny CSV entries."""
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        for index in range(count):
            archive.writestr(f"playlist-{index}.csv", b"title")
    return output.getvalue()


@pytest.mark.parametrize(
    ("build_archive", "limits", "message"),
    [
        pytest.param(
            lambda: zip_bytes("playlist.csv", b"x" * 4096),
            {"max_decompressed_size": 1024},
            "too much data",
            id="decompressed-size",
        ),
        pytest.param(
            lambda: _multi_entry_zip(4),
            {"max_archive_entries": 3},
            "too many files",
            id="archive-entries",
        ),
    ],
)
def test_store_upload_rejects_archives_past_the_limits(
    tmp_path: Path, build_archive, limits: dict, message: str
) -> None:
    with pytest.raises(UploadError, match=message):
        store_upload(
            file_storage(build_archive()),
            upload_root=tmp_path,
            max_upload_size=1024 * 1024,
            **limits,
        )

    assert list(tmp_path.iterdir()) == []


def test_store_upload_rejects_special_file_entries(tmp_path: Path) -> None:
    from zipfile import ZipInfo

    info = ZipInfo("fifo.dat")
    info.create_system = 3
    info.external_attr = 0o010000 << 16
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr(info, b"x")
    blob = output.getvalue()

    with pytest.raises(UploadError, match="unsupported entry"):
        store_upload(
            file_storage(blob),
            upload_root=tmp_path,
            max_upload_size=1024,
        )

    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "entry",
    [
        pytest.param("bad\x00name.csv", id="nul-byte"),
        pytest.param("a\nb.csv", id="newline"),
        pytest.param("CON.csv", id="windows-device-name"),
        pytest.param("folder/NUL", id="windows-device-name-nested"),
        pytest.param("name.csv ", id="trailing-space"),
        pytest.param("bad:name.csv", id="windows-colon"),
    ],
)
def test_safe_archive_path_rejects_unsafe_entries(entry: str) -> None:
    from spotm3u.uploads import _safe_archive_path

    with pytest.raises(UploadError, match="unsafe path"):
        _safe_archive_path(entry)


def test_store_upload_rejects_newline_in_zip_path(tmp_path: Path) -> None:
    with pytest.raises(UploadError, match="unsafe path"):
        store_upload(
            file_storage(zip_bytes("a\nb.csv")),
            upload_root=tmp_path,
            max_upload_size=1024,
        )

    assert list(tmp_path.iterdir()) == []
