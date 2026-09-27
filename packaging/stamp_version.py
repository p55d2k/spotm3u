"""Stamp the release version and the target platform into the built sources.

Two build-time constants have to be known when the bundle is produced, and both
are written into the sources rather than discovered at runtime.

The version: the in-app update check (``GET /api/update``) compares the running
``spotm3u.__version__`` against the latest published GitHub release tag, and a
PyInstaller bundle embeds whatever that constant held at build time. The release
workflow therefore rewrites the constant from the pushed tag before building, so
an installed app reports the version it was actually built from instead of the
version committed on ``main`` -- otherwise every packaged build would claim to
be an old release and the update notice would fire forever.

The target platform: the frontend draws either Windows/Linux window controls or
a transparent macOS strip, and it needs to know which *before* the pywebview
bridge exists, because the bridge injects itself on its own thread and a first
render can beat it. The application is built per platform and cross-compilation
is not supported, so the answer is known at build time: the stamped
``BUILD_TARGET_PLATFORM`` constant is authoritative and the bridge only
corroborates it. Guessing instead would draw the wrong chrome, and on macOS a
lost value reads as "not macOS" and puts the Windows/Linux controls on screen.

Both are single source assignments rewritten the same way, with the same
failure behavior: an unrecognized or missing value fails the build instead of
producing a bundle that guesses.

Usage (from the repository root):

    uv run -q python packaging/stamp_version.py 1.2.3
    uv run -q python packaging/stamp_version.py 1.2.3 mac
    uv run -q python packaging/stamp_version.py --platform mac

The workflow calls this automatically; developers only need it to build a local
bundle that reports a real version.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TARGET = ROOT / "src" / "spotm3u" / "__init__.py"
DEFAULT_PLATFORM_TARGET = ROOT / "frontend" / "src" / "lib" / "target.ts"

# Mirrors the tags ``spotm3u.update.parse_version`` understands (``v1.2.3``,
# ``1.2.3-dev``). Stamping anything this rejects would make the update check
# compare nothing and silently never offer an update, so fail loudly instead.
VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+(?:[.-][0-9A-Za-z][0-9A-Za-z.-]*)?$")
_ASSIGNMENT = re.compile(r'^__version__ = "[^"]*"$', re.MULTILINE)

# The platform names the frontend uses (``WindowPlatform`` in
# ``frontend/src/lib/bridge.ts``). A value outside this set is a mistake, not a
# new platform to support quietly.
SUPPORTED_PLATFORMS = ("mac", "windows", "linux")
# ``sys.platform`` and the release workflow's matrix both spell these
# differently from the frontend, so they are accepted on the way in and resolved
# to the supported name rather than stamped through as-is.
_PLATFORM_ALIASES = {
    "darwin": "mac",
    "macos": "mac",
    "win32": "windows",
}
_PLATFORM_ASSIGNMENT = re.compile(r'^export const BUILD_TARGET_PLATFORM = "[^"]*";$', re.MULTILINE)


def resolve_platform(value: str) -> str:
    """Return ``value`` as one of :data:`SUPPORTED_PLATFORMS`.

    Accepts the names the build host and the release matrix use (``darwin``,
    ``win32``, ``macos``) and rejects everything else, so a build for an
    unrecognized platform fails instead of shipping a bundle whose frame was
    decided by a guess.
    """
    platform = value.strip().lower()
    platform = _PLATFORM_ALIASES.get(platform, platform)
    if platform not in SUPPORTED_PLATFORMS:
        raise SystemExit(
            f"unsupported target platform: {value!r}; "
            f"expected one of {', '.join(SUPPORTED_PLATFORMS)}"
        )
    return platform


def check_version(version: str) -> str:
    """Return ``version`` unchanged, or fail the build if it is not semantic.

    Split out from :func:`stamp` so the caller can reject a value before it
    touches a file, and fail for the reason it actually is.
    """
    if not VERSION_PATTERN.match(version):
        raise SystemExit(f"not a semantic version: {version!r}")
    return version


def stamp(text: str, version: str) -> str:
    """Return ``text`` with its single ``__version__`` assignment set to ``version``.

    Raises ``SystemExit`` when the version is not a semantic version or the text
    does not hold exactly one plain ``__version__`` line, so a release fails
    instead of publishing an app that reports the wrong version.
    """
    check_version(version)
    stamped, replacements = _ASSIGNMENT.subn(f'__version__ = "{version}"', text)
    if replacements != 1:
        raise SystemExit(
            f"expected exactly one __version__ assignment, found {replacements}; "
            "update packaging/stamp_version.py if the constant moved"
        )
    return stamped


def stamp_platform(text: str, platform: str) -> str:
    """Return ``text`` with its single ``BUILD_TARGET_PLATFORM`` set to ``platform``.

    Raises ``SystemExit`` for an unsupported platform or a text that does not
    hold exactly one such assignment, mirroring :func:`stamp`: a bundle must
    never be produced with a frame platform nobody chose.
    """
    resolved = resolve_platform(platform)
    stamped, replacements = _PLATFORM_ASSIGNMENT.subn(
        f'export const BUILD_TARGET_PLATFORM = "{resolved}";', text
    )
    if replacements != 1:
        raise SystemExit(
            f"expected exactly one BUILD_TARGET_PLATFORM assignment, found {replacements}; "
            "update packaging/stamp_version.py if the constant moved"
        )
    return stamped


def _write(target: Path, transformed: str) -> None:
    # newline="\n" keeps the file's existing LF endings on Windows instead of
    # rewriting every line to CRLF.
    target.write_text(transformed, encoding="utf-8", newline="\n")
    print(f"stamped {target}")


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    version: str | None = None
    platform: str | None = None
    while args:
        arg = args.pop(0)
        if arg == "--platform":
            if not args:
                raise SystemExit(_usage())
            platform = args.pop(0)
        elif not arg.startswith("-") and version is None:
            version = arg
        else:
            raise SystemExit(_usage())
    if version is None and platform is None:
        raise SystemExit(_usage())
    # Both values are rejected before any file is opened, so a bad build fails
    # for the reason it is bad instead of on a file it never needed.
    if version is not None:
        check_version(version)
    if platform is not None:
        platform = resolve_platform(platform)
    if version is not None:
        target = DEFAULT_TARGET
        _write(target, stamp(target.read_text(encoding="utf-8"), version))
    if platform is not None:
        target = DEFAULT_PLATFORM_TARGET
        _write(target, stamp_platform(target.read_text(encoding="utf-8"), platform))


def _usage() -> str:
    return (
        f"usage: {Path(sys.argv[0]).name} <version> [--platform <target>]\n"
        f"       {Path(sys.argv[0]).name} --platform <target>"
    )


if __name__ == "__main__":
    main()
