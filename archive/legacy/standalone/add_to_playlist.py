#!/usr/bin/env python3
"""
create_m3u.py
-------------
Scans a chosen folder for MP3 files and writes them to a raw .m3u playlist
file. A raw M3U playlist is a plain text file with one file path per line
(no #EXTM3U / #EXTINF metadata).

The script is fully portable (works on macOS, Linux, or Windows) and only
uses the Python standard library.

Usage:
    python3 create_m3u.py

Notes:
    - Paths in the .m3u can be absolute or relative to the scanned folder.
    - The .m3u output replaces the old "add to Apple Music playlist" behaviour:
      nothing is sent to Music.app, no playlist is created, files are untouched.
"""

import os
import sys
import glob


BAR = "=" * 60


def collect_mp3s(folder: str, recursive: bool) -> list:
    """Return a sorted list of absolute MP3 paths under `folder`."""
    pattern = (
        os.path.join(folder, "**", "*.mp3")
        if recursive
        else os.path.join(folder, "*.mp3")
    )
    return sorted(glob.glob(pattern, recursive=recursive))


def write_m3u(file_path: str, mp3s: list, relative: bool, base: str) -> int:
    """Write `mp3s` to `file_path` as a raw M3U playlist.

    If `relative` is True, each entry is written relative to `base`;
    otherwise absolute paths are used. Returns the number of lines written.
    """
    lines = []
    for path in mp3s:
        entry = os.path.relpath(path, base) if relative else path
        lines.append(entry + "\n")
    with open(file_path, "w", encoding="utf-8") as f:
        f.writelines(lines)
    return len(lines)


def main() -> None:
    print(BAR)
    print(" Create a Raw .m3u Playlist from MP3s")
    print(BAR)

    # 1) Folder to scan.
    default_folder = os.getcwd()
    folder = input(f"Folder with MP3s [default: {default_folder}]: ").strip()
    if not folder:
        folder = default_folder
    folder = os.path.abspath(os.path.expanduser(folder))
    if not os.path.isdir(folder):
        print(f"Error: folder does not exist or is not a directory: {folder}")
        sys.exit(1)

    # 2) Recursive scan?
    rec = input("Scan sub-folders recursively? (y/N): ").strip().lower()
    recursive = rec in ("y", "yes")

    # 3) Output .m3u file.
    default_out = os.path.join(folder, "playlist.m3u")
    out_file = input(f"Output .m3u file [default: {default_out}]: ").strip()
    if not out_file:
        out_file = default_out
    out_file = os.path.abspath(os.path.expanduser(out_file))
    if not out_file.lower().endswith(".m3u"):
        print("Error: output file must have a .m3u extension.")
        sys.exit(1)
    if os.path.exists(out_file):
        overwrite = input(
            f'"{out_file}" already exists. Overwrite? (Y/n): '
        ).strip().lower()
        if overwrite in ("n", "no"):
            print("Aborted by user.")
            sys.exit(0)

    # 4) Relative or absolute paths in the file?
    rel = input("Store paths relative to the scanned folder? (y/N): ").strip().lower()
    relative = rel in ("y", "yes")

    # 5) Gather MP3 files.
    mp3s = collect_mp3s(folder, recursive)
    if not mp3s:
        print(f"No .mp3 files found in: {folder}")
        sys.exit(1)

    print(f"\nFound {len(mp3s)} MP3 file(s).")
    print(BAR)
    print(f"Folder      : {folder}")
    print(f"Output file : {out_file}")
    if recursive:
        print("Recursive   : yes")
    print("Paths       : " + ("relative to folder" if relative else "absolute"))
    proceed = input("\nProceed to write .m3u file? (Y/n): ").strip().lower()
    if proceed in ("n", "no"):
        print("Aborted by user.")
        sys.exit(0)

    # 6) Write the file.
    written = write_m3u(out_file, mp3s, relative, folder)
    print("\n" + BAR)
    print(f'Done. Wrote {written} file path(s) to "{out_file}".')
    print(BAR)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(130)
