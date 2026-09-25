"""The React frontend's build output: where it lives, how it is built, and how Flask serves it.

``frontend/`` is built by Vite (``uv run build``, or ``npm run build`` while
developing). The result is a static single-page application which Flask serves
at ``/`` - see ``docs/development.md`` and ``docs/packaging.md``. Flask only
ships the ``/api`` routes (``spotm3u.api``) and this single-page application.

The built directory is looked up in this order:

1. ``SPOTM3U_FRONTEND_DIST``, an explicit override (the tests and a one-off
   check of a build in another location use it);
2. inside the packaged application, where ``packaging/spotm3u.spec`` collects it
   as ``frontend/``;
3. ``frontend/dist`` in a source checkout, so a production build can be checked
   against the running Flask app without packaging anything.

Nothing here renders or transforms: the files Vite produced are handed to the
browser as they are.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from flask import Flask, Response, abort, send_from_directory

from .launcher import DEFAULT_HOST
from .preferences import read_preferences
from .runtime import bundle_roots

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIR = PROJECT_ROOT / "frontend"
DIST_DIRNAME = "dist"
INDEX = "index.html"

# Where the built application is served. ``frontend/vite.config.ts`` builds
# with the same base, which is what makes the hashed asset URLs resolve inside
# the client-side routes.
MOUNT = "/"

# Absolute path to a built directory, replacing the lookup above.
DIST_ENV = "SPOTM3U_FRONTEND_DIST"

# The ``<html ...>`` tag, so the stored theme can be rendered with the shell.
_HTML_TAG_RE = re.compile(r"<html\b([^>]*)>", re.IGNORECASE)
_THEME_ATTRIBUTE_RE = re.compile(r"\bdata-theme=", re.IGNORECASE)

_NOT_BUILT = (
    "The React frontend has not been built. Run 'uv run build' (or 'npm run build' in "
    "frontend/) and reload."
)


class FrontendError(RuntimeError):
    """The frontend cannot be built, found, or started."""


def _is_built(directory: Path) -> bool:
    """True when ``directory`` really holds a Vite build."""
    return (directory / INDEX).is_file()


def dist_directory() -> Path | None:
    """The directory holding the built frontend, or ``None`` when it has not been built."""
    override = os.environ.get(DIST_ENV)
    if override:
        candidate = Path(override).expanduser()
        return candidate if _is_built(candidate) else None
    candidates = [root / "frontend" for root in bundle_roots()]
    candidates.append(FRONTEND_DIR / DIST_DIRNAME)
    for candidate in candidates:
        if _is_built(candidate):
            return candidate
    return None


def dependencies_installed() -> bool:
    """True when ``npm install`` has already populated ``node_modules``."""
    return (FRONTEND_DIR / "node_modules").is_dir()


def require_dependencies() -> None:
    """Fail with an actionable message when the frontend dependencies are missing."""
    if not dependencies_installed():
        raise FrontendError(
            f"The frontend dependencies are missing. Run 'npm install' in {FRONTEND_DIR}."
        )


def npm_command() -> list[str]:
    """The command prefix that runs npm on this platform.

    ``npm`` is a shell script on POSIX and ``npm.cmd`` on Windows, which the
    command interpreter is needed to run.
    """
    npm = shutil.which("npm")
    if npm is None:
        raise FrontendError("npm was not found on PATH. Install Node.js (see docs/development.md).")
    return [npm] if os.name != "nt" else [os.environ.get("ComSpec", "cmd.exe"), "/c", npm]


def install_command() -> list[str]:
    """Install the frontend dependencies at the versions the lockfile pins.

    ``npm ci`` installs exactly ``package-lock.json`` and fails if the two have
    drifted apart, which is what keeps a packaged build reproducible. Without a
    lockfile there is nothing to be exact about, so a plain ``npm install`` is
    used instead.
    """
    npm = npm_command()
    lockfile = FRONTEND_DIR / "package-lock.json"
    return [*npm, "ci" if lockfile.is_file() else "install"]


def build_command() -> list[str]:
    """Produce the production frontend build (Vite, type-checked first)."""
    return [*npm_command(), "run", "build"]


def dev_command(port: int) -> list[str]:
    """Run Vite's development server on ``port``.

    The host and port are passed explicitly because the caller owns port
    selection: ``strictPort`` makes a collision a visible failure there rather
    than a silent move to an address nobody was told about.
    """
    return [
        *npm_command(),
        "run",
        "dev",
        "--",
        "--host",
        DEFAULT_HOST,
        "--port",
        str(port),
        "--strictPort",
    ]


def register_frontend(app: Flask) -> None:
    """Serve the built React application at ``MOUNT``.

    The routes only hand over files Vite produced and run no application logic.
    A path without a file suffix is a client-side route, so it answers with the
    shell (``index.html``) and the application renders it; a missing file with a
    suffix is a real 404. Until the frontend is built the routes explain how to
    build it instead of pretending the address does not exist.

    The catch-all route sits under the API namespace too, so an unknown
    ``/api/...`` path is handed back to the API's own 404 handler (a JSON error)
    instead of being swallowed by the shell.
    """

    @app.get(MOUNT)
    def frontend_index() -> Response:
        return _index_response()

    @app.get(f"{MOUNT}<path:asset>")
    def frontend_asset(asset: str) -> Response:
        if asset.startswith("api/"):
            abort(404)
        directory = dist_directory()
        if directory is None:
            return _not_built_response()
        resolved_directory = directory.resolve()
        target = (directory / asset).resolve()
        if target.is_file() and (
            target == resolved_directory or resolved_directory in target.parents
        ):
            return send_from_directory(directory, asset)
        if not Path(asset).suffix:
            return _index_response()
        return Response("Not found", status=404, mimetype="text/plain")


def _index_response() -> Response:
    """The application shell, carrying the stored theme when one is chosen.

    The desktop shell cannot keep WebView storage (pywebview's macOS backend
    drops it on exit), so a theme read only from the page would flash the
    system's theme on every launch before the API answered. Rendering the
    stored choice into ``<html data-theme>`` makes the very first paint the
    right one; the page then applies its own choice on top, unchanged. A build
    that is not themed is served exactly as Vite produced it.
    """
    directory = dist_directory()
    if directory is None:
        return _not_built_response()
    themed = _themed_document(directory)
    if themed is None:
        return send_from_directory(directory, INDEX)
    return Response(themed, mimetype="text/html")


def _themed_document(directory: Path) -> str | None:
    """The built index with the stored theme attribute added, or ``None``.

    ``None`` means "serve the file as it is": no theme is stored yet, the file
    cannot be read, or the build already sets ``data-theme`` itself.
    """
    theme = read_preferences().get("theme")
    if theme is None:
        return None
    try:
        document = (directory / INDEX).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    if _THEME_ATTRIBUTE_RE.search(document):
        return None
    themed, replaced = _HTML_TAG_RE.subn(
        lambda match: f'<html data-theme="{theme}"{match.group(1)}>',
        document,
        count=1,
    )
    return themed if replaced else None


def _not_built_response() -> Response:
    return Response(_NOT_BUILT, status=404, mimetype="text/plain")
