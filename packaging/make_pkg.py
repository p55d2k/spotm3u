"""Create a macOS installer package (``.pkg``) from the packaged ``SpotM3U.app``.

A browser-downloaded file always carries the ``com.apple.quarantine``
attribute. On recent macOS a quarantined, unsigned (ad-hoc signed) PyInstaller
app hangs in ``dyld`` on first launch instead of opening: the process stays
alive with no window and no server. macOS 26 also re-stamps quarantine onto any
file copied out of a quarantined disk image, so ZIP and disk-image routes
cannot avoid it without signing or notarization.

A package is different: the installer writes the payload files fresh during
installation, so the installed ``/Applications/SpotM3U.app`` carries no
quarantine attribute and launches normally. ``pkgbuild`` is the only tool used,
so local and GitHub Actions builds behave identically. The bundle is copied to
a staging directory first and every extended attribute is stripped there, so
the payload is clean even if the source bundle itself is quarantined.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

IDENTIFIER = "com.p55d2k.spotm3u"
INSTALL_LOCATION = "/Applications"
# Mirrors the ``SPOTM3U_APP_VERSION`` default in packaging/spotm3u.spec.
VERSION_DEFAULT = "0.0.0"


def pkg_command(staging_root: Path, dest_pkg: Path, identifier: str, version: str) -> list[str]:
    """The ``pkgbuild`` invocation that produces ``dest_pkg`` from ``staging_root``."""
    return [
        "pkgbuild",
        "--root",
        str(staging_root),
        "--install-location",
        INSTALL_LOCATION,
        "--identifier",
        identifier,
        "--version",
        version,
        str(dest_pkg),
    ]


def _run(command: list[str]) -> None:
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise SystemExit(f"{command[0]} failed (exit {result.returncode}): {detail}")


def build_pkg(
    source_app: str | Path,
    dest_pkg: str | Path,
    identifier: str = IDENTIFIER,
    version: str = VERSION_DEFAULT,
    staging_dir: str | Path | None = None,
) -> Path:
    """Create ``dest_pkg`` from ``source_app`` and return its path."""
    src = Path(source_app)
    dest = Path(dest_pkg)
    if not src.is_dir():
        raise SystemExit(f"source application does not exist: {src}")
    if src.suffix != ".app":
        raise SystemExit(f"source must be a .app bundle, got: {src}")
    dest.parent.mkdir(parents=True, exist_ok=True)

    temporary = staging_dir is None
    stage = Path(staging_dir) if staging_dir else Path(tempfile.mkdtemp(prefix="spotm3u-pkg-"))
    try:
        staged_app = stage / src.name
        # ditto preserves the bundle's symlinks; xattr -cr then guarantees a
        # quarantine-free payload regardless of the source bundle's own state.
        _run(["ditto", str(src), str(staged_app)])
        _run(["xattr", "-cr", str(staged_app)])
        result = subprocess.run(
            pkg_command(stage, dest, identifier, version),
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise SystemExit(f"pkgbuild failed (exit {result.returncode}): {detail}")
        return dest
    finally:
        if temporary:
            shutil.rmtree(stage, ignore_errors=True)


def _usage() -> None:
    raise SystemExit(f"usage: {sys.argv[0]} <SpotM3U.app> <out.pkg> [--version <version>]")


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    version = VERSION_DEFAULT
    if "--version" in args:
        position = args.index("--version")
        if position + 1 >= len(args):
            _usage()
        version = args.pop(position + 1)
        args.pop(position)
    if len(args) != 2:
        _usage()
    created = build_pkg(args[0], args[1], version=version)
    print(f"packaged {args[0]} -> {created}")


if __name__ == "__main__":
    main()
