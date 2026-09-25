"""End-to-end online pipeline integration.

Chains the real Exportify parser, local resolver, online search, ranking,
source validation, downloader, audio validation, and M3U writer together with
only the network boundary (yt_dlp) and audio inspection mocked. Verifies that
the full pipeline can be exercised without live Spotify or live web results.
"""

from __future__ import annotations

import sys
import types
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from spotm3u.audio import LocalAudioResolver
from spotm3u.exportify import parse_exportify_zip
from spotm3u.m3u import write_m3u
from spotm3u.online import DownloadCache, OnlineSourceSearcher, download_track
from spotm3u.resolution import TrackResolver

ZIP_HEADER = "Track URI,Track Name,Album Name,Artist Name(s),Duration (ms)\n"


def export_zip(*files: tuple[str, str]) -> BytesIO:
    archive = BytesIO()
    with ZipFile(archive, "w") as output:
        for name, content in files:
            output.writestr(name, content)
    archive.seek(0)
    return archive


def entries(text: str) -> list[str]:
    return [line for line in text.splitlines() if line and not line.startswith("#")]


def equal_track(
    url: str, title: str, uploader: str, duration: int, artist: str | None = None
) -> dict[str, object]:
    values: dict[str, object] = {
        "title": title,
        "webpage_url": url,
        "uploader": uploader,
        "duration": duration,
    }
    if artist is not None:
        values["artist"] = artist
    return values


def install_yt_dlp(
    monkeypatch,
    *,
    results_by_query: dict[str, list[dict[str, object]]],
    failing_urls: set[str] = (),
    download_log: list[str] | None = None,
) -> None:
    """Fake yt_dlp serving both search and download from the same module."""

    class YoutubeDL:
        def __init__(self, options: dict):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, spec, download=False):
            query = spec.split(":", 1)[1]
            return {"entries": results_by_query.get(query, [])}

        def download(self, urls):
            if any(url in failing_urls for url in urls):
                raise RuntimeError("network error")
            if download_log is not None:
                download_log.extend(urls)
            output = Path(self.options["outtmpl"].replace("%(ext)s", "mp3"))
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"valid mp3")
            return 0

    monkeypatch.setitem(
        sys.modules,
        "yt_dlp",
        types.SimpleNamespace(
            YoutubeDL=YoutubeDL,
            utils=types.SimpleNamespace(DownloadError=RuntimeError),
        ),
    )


def install_valid_audio_validation(monkeypatch) -> None:
    def fake(track, path):
        return types.SimpleNamespace(status="valid", reasons=())

    for module in (
        "spotm3u.online.downloader",
        "spotm3u.online.cache",
        "spotm3u.resolution",
    ):
        monkeypatch.setattr(f"{module}.validate_downloaded_audio", fake)


def wonderwall_results(url: str = "https://example.com/wonderwall") -> list[dict[str, object]]:
    return [equal_track(url, "Wonderwall", "Oasis", 258)]


def test_full_pipeline_resolves_local_and_downloaded_tracks(tmp_path, monkeypatch) -> None:
    library = tmp_path / "library"
    library.mkdir()
    (library / "Coldplay - Yellow.mp3").write_bytes(b"audio")

    install_yt_dlp(monkeypatch, results_by_query={"oasis wonderwall": wonderwall_results()})
    install_valid_audio_validation(monkeypatch)

    archive = export_zip(
        (
            "Morning.csv",
            ZIP_HEADER + "spotify:track:cs,Yellow,,Coldplay,269000\n"
            "spotify:track:ww,Wonderwall,,Oasis,258000\n",
        )
    )
    playlist = parse_exportify_zip(archive)[0]
    output_dir = tmp_path / "downloads"

    resolver = TrackResolver(
        LocalAudioResolver(library),
        output_dir,
        searcher=OnlineSourceSearcher(max_results=8),
        downloader=download_track,
    )
    report = resolver.resolve_report(playlist.tracks)

    assert report.counts["local"] == 1
    assert report.counts["downloaded"] == 1
    assert report.counts["missing"] == 0

    assert report.results[0].successful
    assert report.results[1].status == "downloaded"
    assert report.results[1].source_url == "https://example.com/wonderwall"
    downloaded = output_dir / "Wonderwall - Oasis.mp3"
    assert downloaded.is_file()

    m3u_path = output_dir / "playlist.m3u"
    write_m3u(m3u_path, report)
    listed = entries(m3u_path.read_text(encoding="utf-8"))
    assert listed == [str(library / "Coldplay - Yellow.mp3"), str(downloaded)]


def test_full_pipeline_reports_missing_rejected_and_failed(tmp_path, monkeypatch) -> None:
    install_yt_dlp(
        monkeypatch,
        results_by_query={
            "ed sheeran perfect": [
                equal_track(
                    "https://example.com/wrong-artist",
                    "Perfect",
                    "Wrong Artist",
                    250,
                    artist="Wrong Artist",
                )
            ],
            "band broken song": [
                equal_track("https://example.com/broken", "Broken Song", "Band", 200)
            ],
        },
        failing_urls={"https://example.com/broken"},
    )
    install_valid_audio_validation(monkeypatch)

    archive = export_zip(
        (
            "Whatever.csv",
            ZIP_HEADER + "spotify:track:m,Missing Song,,Nobody,150000\n"
            "spotify:track:r,Perfect,,Ed Sheeran,250000\n"
            "spotify:track:f,Broken Song,,Band,200000\n",
        )
    )
    playlist = parse_exportify_zip(archive)[0]
    output_dir = tmp_path / "downloads"

    report = TrackResolver(
        LocalAudioResolver(tmp_path / "empty"),
        output_dir,
        searcher=OnlineSourceSearcher(max_results=8),
        downloader=download_track,
    ).resolve_report(playlist.tracks)

    assert report.counts["missing"] == 1
    assert report.counts["rejected"] == 1
    assert report.counts["failed"] == 1
    assert report.counts["downloaded"] == 0

    assert report.results[0].status == "missing"
    assert report.results[1].status == "rejected"
    assert report.results[2].status == "failed"

    m3u_path = output_dir / "playlist.m3u"
    write_m3u(m3u_path, report)
    assert entries(m3u_path.read_text(encoding="utf-8")) == []


def test_full_pipeline_shares_one_download_across_duplicate_entries(tmp_path, monkeypatch) -> None:
    download_log: list[str] = []
    install_yt_dlp(
        monkeypatch,
        results_by_query={"oasis wonderwall": wonderwall_results()},
        download_log=download_log,
    )
    install_valid_audio_validation(monkeypatch)

    archive = export_zip(
        (
            "Loop.csv",
            ZIP_HEADER + "spotify:track:ww,Wonderwall,,Oasis,258000\n"
            "spotify:track:ww,Wonderwall,,Oasis,258000\n",
        )
    )
    playlist = parse_exportify_zip(archive)[0]
    output_dir = tmp_path / "downloads"
    cache = DownloadCache(output_dir)

    report = TrackResolver(
        LocalAudioResolver(tmp_path / "empty"),
        output_dir,
        searcher=OnlineSourceSearcher(max_results=8),
        downloader=download_track,
        cache=cache,
    ).resolve_report(playlist.tracks)

    assert [result.status for result in report.results] == ["downloaded", "downloaded"]
    assert report.results[0].local_path == report.results[1].local_path
    assert len(download_log) == 1

    m3u_path = output_dir / "playlist.m3u"
    write_m3u(m3u_path, report)
    listed = entries(m3u_path.read_text(encoding="utf-8"))
    assert len(listed) == 2
    assert listed[0] == listed[1]
    assert Path(listed[0]).is_file()
