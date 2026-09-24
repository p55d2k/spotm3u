"""Stamp the release version into the package's ``__version__`` constant.

The in-app update check (``GET /api/update``) compares the running
``spotm3u.__version__`` against the latest published GitHub release tag, and a
PyInstaller bundle embeds whatever that constant held at build time. The release
workflow therefore rewrites the constant from the pushed tag before building, so
an installed app reports the version it was actually built from instead of the
version committed on ``main`` -- otherwise every packaged build would claim to
be an old release and the update notice would fire forever.

Usage (from the repository root):

    uv run -q python packaging/stamp_version.py 1.2.3

The workflow calls this automatically; developers only need it to build a local
bundle that reports a real version.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TARGET = ROOT / "src" / "spotm3u" / "__init__.py"

# Mirrors the tags ``spotm3u.update.parse_version`` understands (``v1.2.3``,
# ``1.2.3-dev``). Stamping anything this rejects would make the update check
# compare nothing and silently never offer an update, so fail loudly instead.
VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+(?:[.-][0-9A-Za-z][0-9A-Za-z.-]*)?$")
_ASSIGNMENT = re.compile(r'^__version__ = "[^"]*"$', re.MULTILINE)


def stamp(text: str, version: str) -> str:
    """Return ``text`` with its single ``__version__`` assignment set to ``version``.

    Raises ``SystemExit`` when the version is not a semantic version or the text
    does not hold exactly one plain ``__version__`` line, so a release fails
    instead of publishing an app that reports the wrong version.
    """
    if not VERSION_PATTERN.match(version):
        raise SystemExit(f"not a semantic version: {version!r}")
    stamped, replacements = _ASSIGNMENT.subn(f'__version__ = "{version}"', text)
    if replacements != 1:
        raise SystemExit(
            f"expected exactly one __version__ assignment, found {replacements}; "
            "update packaging/stamp_version.py if the constant moved"
        )
    return stamped


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        raise SystemExit(f"usage: {Path(sys.argv[0]).name} <version>")
    version = args[0]
    target = DEFAULT_TARGET
    stamped = stamp(target.read_text(encoding="utf-8"), version)
    # newline="\n" keeps the file's existing LF endings on Windows instead of
    # rewriting every line to CRLF.
    target.write_text(stamped, encoding="utf-8", newline="\n")
    print(f"stamped {target} with version {version}")


if __name__ == "__main__":
    main()
