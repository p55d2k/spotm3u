#!/usr/bin/env python3
"""
csv_to_m3u.py
-------------
Automation for converting Spotify playlist CSVs into raw .m3u playlist files.

For each CSV in the `csv/` folder:
  1. Reads Track Name + Artist Name(s) from the CSV.
  2. Tries to match each track against MP3s already present under
     `Music/Music/Media.localized/Music/me/` (recursively, fuzzy match on the
     filename, which typically looks like "Song - Artist.mp3").
  3. For tracks that could NOT be matched locally, downloads them from YouTube
     (via yt-dlp, same settings as the spotify-playlist-to-mp3 tool) into the
     existing `me/flight!/` folder, named "Song - Artist.mp3".
  4. Writes an absolute-path .m3u playlist to `playlists/<csv_name>.m3u`.

Nothing existing is ever modified: matched songs just reference the on-disk
file, and only brand-new downloads are added to `flight!/`.

Existing files/folders are preserved. Use --no-download (default) to only
resolve which songs are missing (dry-run report) without downloading anything.

Usage:
    python3 csv_to_m3u.py [--csv CSV_NAMES ...] [--download] [--threads N]

Examples:
    python3 csv_to_m3u.py --csv adele k            # only those csvs, report-only
    python3 csv_to_m3u.py --download               # all csvs, with downloads
    python3 csv_to_m3u.py --csv mp --download      # one csv, with downloads
"""

import argparse
import csv
import os
import re
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import yt_dlp
except ImportError:  # pragma: no cover
    yt_dlp = None

HOME = os.path.expanduser("~")
MUSIC_ROOT = os.path.join(
    HOME, "Music", "Music", "Media.localized", "Music", "me"
)
CSV_DIR = os.path.join(HOME, "Music", "csv")
PLAYLIST_DIR = os.path.join(HOME, "Music", "playlists")
FLIGHT_DIR = os.path.join(MUSIC_ROOT, "flight!")
# ---------------------------------------------------------------------------
# Normalisation helpers (used for fuzzy filename matching)
# ---------------------------------------------------------------------------

def normalize(text: str) -> str:
    """Lowercase, strip accents, and collapse punctuation for comparison."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", str(text))
    # remove combining diacritics
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    # replace any run of non-alphanumeric chars with a single space
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff\u3040-\u30ff]+", " ", text)
    return text.strip()


def stem_of(path: str) -> str:
    """Return the normalized filename WITHOUT the .mp3 extension."""
    return normalize(os.path.splitext(os.path.basename(path))[0])


def collect_existing_mp3s(root: str):
    """Return dict: normalized-stem -> absolute path for every mp3 under root."""
    existing = {}
    for dirpath, _dirs, files in os.walk(root):
        for fname in files:
            if fname.lower().endswith(".mp3"):
                full = os.path.join(dirpath, fname)
                existing[stem_of(full)] = full
    return existing


# ---------------------------------------------------------------------------
# CSV parsing
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def _artist_variants(artists: str):
    """Produce reasonable 'artist string' candidates for filename matching."""
    if not artists:
        return []
    # Replace semicolon separators with comma-space (the flight! convention)
    joined = artists.replace(";", ", ")
    return [
        joined,
        joined.replace(", ", ""),   # no spaces
        artists.replace(";", ""),   # no separators at all
    ]


def find_match(track, artists, existing):
    """
    Try to locate an existing mp3 for (track, artists).

    Checks, in order:
      1. exact "Track - Artist" stem in the map
      2. exact track-name stem match
      3. fallback: any file stem containing track name (+ artist bonus)

    Returns the absolute path or None.
    """
    track_norm = normalize(track)
    if not track_norm:
        return None

    artist_variants = _artist_variants(artists)
    artist_norm = normalize(artists) if artists else ""

    # 1. exact "<track> <artist>" combos (the flight! filename style)
    for v in map(normalize, artist_variants):
        if v and f"{track_norm} {v}" in existing:
            return existing[f"{track_norm} {v}"]

    # 2. exact track-name stem match
    if track_norm in existing:
        return existing[track_norm]

    # 3. fallback substring / contains across all existing stems
    best = None
    best_score = 0
    for stem, path in existing.items():
        if track_norm in stem:
            score = 1
            if artist_norm and artist_norm in stem:
                score = 3
            score += min(len(stem), 200) / 200.0  # prefer more specific stems
            if score > best_score:
                best_score = score
                best = path
    return best
def read_tracks(csv_path: str):
    """Yield dicts of {track, artists, source} for each non-empty row."""
    with open(csv_path, mode="r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            track = (row.get("Track Name") or "").strip()
            if not track:
                continue
            artists = (row.get("Artist Name(s)") or "").strip()
            yield {"track": track, "artists": artists}
# ---------------------------------------------------------------------------
# Downloading (mirrors the spotify-playlist-to-mp3 tool)
# ---------------------------------------------------------------------------

def sanitize_filename_component(value):
    if value is None:
        return "Unknown"
    cleaned = str(value)
    cleaned = cleaned.replace("/", " - ").replace("\\", " - ")
    cleaned = cleaned.replace(":", " - ")
    cleaned = cleaned.replace("*", " ").replace("?", "")
    cleaned = cleaned.replace('"', "").replace("<", " ").replace(">", " ")
    cleaned = cleaned.replace("|", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ._")
    return cleaned or "Unknown"


def build_output_basename(track_name, artist_names):
    track = sanitize_filename_component(track_name)
    artists_clean = artist_names.replace(";", ", ") if artist_names else ""
    artists = sanitize_filename_component(artists_clean)
    if artists and artists != track:
        return f"{track} - {artists}"
    return track


def download_song(track_name, artists, out_dir):
    if yt_dlp is None:
        return False, None, "yt_dlp not installed"
    base = build_output_basename(track_name, artists)
    query = f"{track_name} {artists}".strip()
    final_mp3 = os.path.join(out_dir, f"{base}.mp3")
    # skip if already there (idempotent retries)
    if os.path.exists(final_mp3):
        return True, final_mp3, "already present"
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": os.path.join(out_dir, f"{base}.%(ext)s"),
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ],
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 30,
        "retries": 5,
        "fragment_retries": 5,
        "skip_unavailable_fragments": True,
        "ignoreerrors": False,
        "default_search": "ytsearch",
        "extract_flat": False,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([f"ytsearch1:{query}"])
        if os.path.exists(final_mp3):
            return True, final_mp3, "downloaded"
        return False, None, f"no {os.path.basename(final_mp3)} produced"
    except Exception as e:  # pragma: no cover
        return False, None, str(e)
# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="CSV spotify playlists -> .m3u")
    ap.add_argument("--csv", nargs="*", default=None,
                    help="CSV name(s) to process (default: all in csv/).")
    ap.add_argument("--download", action="store_true",
                    help="Download missing songs into me/flight!/.")
    ap.add_argument("--threads", type=int, default=4)
    args = ap.parse_args()

    if not os.path.isdir(CSV_DIR):
        print(f"CSV dir not found: {CSV_DIR}")
        sys.exit(1)

    os.makedirs(PLAYLIST_DIR, exist_ok=True)

    if args.csv:
        csv_files = []
        for name in args.csv:
            p = name if name.endswith(".csv") else f"{name}.csv"
            full = os.path.join(CSV_DIR, p)
            if not os.path.isfile(full):
                print(f"!! Unknown csv: {full}")
                sys.exit(1)
            csv_files.append((os.path.splitext(p)[0], full))
    else:
        csv_files = sorted(
            (os.path.splitext(f)[0], os.path.join(CSV_DIR, f))
            for f in os.listdir(CSV_DIR)
            if f.lower().endswith(".csv")
        )

    print("Gathering existing MP3s under", MUSIC_ROOT, "...")
    existing = collect_existing_mp3s(MUSIC_ROOT)
    print(f"  -> {len(existing)} existing mp3 files indexed.\n")

    total_matched = 0
    total_missing = 0
    total_downloaded = 0
    total_failed = 0

    for csv_name, csv_path in csv_files:
        tracks = list(read_tracks(csv_path))
        print("=" * 70)
        print(f"  {csv_name}.csv  ({len(tracks)} tracks)")
        print("=" * 70)

        playlines = []   # absolute paths, in CSV order
        missing = []     # tracks needing download

        for t in tracks:
            path = find_match(t["track"], t["artists"], existing)
            if path:
                playlines.append(path)
                total_matched += 1
            else:
                missing.append(t)
                total_missing += 1

        if missing:
            print(f"\n  Missing locally ({len(missing)}):")
            for t in missing:
                print(f"    - {t['track']}  ({t['artists']})")

        if missing and args.download:
            os.makedirs(FLIGHT_DIR, exist_ok=True)
            print(f"\n  Downloading {len(missing)} track(s) into:\n    {FLIGHT_DIR}")
            with ThreadPoolExecutor(max_workers=max(1, args.threads)) as ex:
                futs = {
                    ex.submit(download_song, t["track"], t["artists"], FLIGHT_DIR): t
                    for t in missing
                }
                for fut in as_completed(futs):
                    t = futs[fut]
                    ok, path, msg = fut.result()
                    if ok and path:
                        playlines.append(path)
                        total_downloaded += 1
                        print(f"    [ok]  {path}")
                    else:
                        total_failed += 1
                        print(f"    [!!]  {t['track']} - {msg}")

        out_m3u = os.path.join(PLAYLIST_DIR, f"{csv_name}.m3u")
        with open(out_m3u, "w", encoding="utf-8") as f:
            for p in playlines:
                f.write(p + "\n")
        print(f"\n  Wrote playlist -> {out_m3u}  ({len(playlines)} entries)\n")

    print("#" * 70)
    print(f"  SUMMARY")
    print(f"    Matched locally   : {total_matched}")
    print(f"    Missing locally   : {total_missing}")
    print(f"    Downloaded        : {total_downloaded}")
    if args.download:
        print(f"    Download failed   : {total_failed}")
    else:
        print("    (no downloads run; re-run with --download to fetch missing)")
    print("#" * 70)


if __name__ == "__main__":
    main()