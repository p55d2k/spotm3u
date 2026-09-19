"""Tests for abandonment-based cleanup of upload job directories."""

import os
import time
from pathlib import Path

import pytest

from spotm3u.uploads import cleanup_jobs


def job_directory(upload_root: Path, name: str, *, age: float = 0.0) -> Path:
    directory = upload_root / f"job-{name}"
    directory.mkdir()
    (directory / "extracted").mkdir()
    (directory / "extracted" / "playlist.csv").write_text("title", encoding="utf-8")
    (directory / "state.json").write_text("{}", encoding="utf-8")
    old = time.time() - age
    os.utime(directory, (old, old))
    return directory


def test_cleanup_removes_old_inactive_jobs(tmp_path: Path) -> None:
    old = job_directory(tmp_path, "aaa", age=3600)
    job_directory(tmp_path, "bbb", age=7200)

    removed = cleanup_jobs(tmp_path, max_age_seconds=1800, now=time.time())

    assert removed == 2
    assert not old.exists()
    assert os.listdir(tmp_path) == []


def test_cleanup_keeps_jobs_younger_than_threshold(tmp_path: Path) -> None:
    keep = job_directory(tmp_path, "aaa", age=600)

    removed = cleanup_jobs(tmp_path, max_age_seconds=1800, now=time.time())

    assert removed == 0
    assert keep.exists()


def test_cleanup_never_removes_active_jobs(tmp_path: Path) -> None:
    running = job_directory(tmp_path, "aaa", age=7200)
    stale = job_directory(tmp_path, "bbb", age=7200)

    removed = cleanup_jobs(
        tmp_path,
        max_age_seconds=1800,
        active_job_ids={"aaa"},
        now=time.time(),
    )

    assert removed == 1
    assert running.exists()
    assert not stale.exists()


def test_cleanup_treats_non_job_directories_and_missing_root(tmp_path: Path) -> None:
    foreign = tmp_path / "keep-me"
    foreign.mkdir()
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")

    assert cleanup_jobs(tmp_path / "missing", max_age_seconds=1800) == 0
    assert cleanup_jobs(tmp_path, max_age_seconds=1800, now=time.time()) == 0
    assert foreign.exists()
    assert (tmp_path / "notes.txt").is_file()


def test_cleanup_rejects_non_positive_age(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="positive"):
        cleanup_jobs(tmp_path, max_age_seconds=0)
