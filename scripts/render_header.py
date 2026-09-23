#!/usr/bin/env python3
"""Render the SpotM3U header fragment through Jinja so we can eyeball any icon swap."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import jinja2


def _build_jinja(package_dir: Path) -> jinja2.Environment:
    import jinja2

    loader = jinja2.FileSystemLoader(package_dir / "templates")
    env = jinja2.Environment(
        loader=loader,
        autoescape=True,
        block_start_string="{%",
        block_end_string="%}",
        variable_start_string="{{",
        variable_end_string="}}",
        comment_start_string="{#",
        comment_end_string="#}",
        keep_trailing_newline=True,
    )
    return env


def render_header() -> str:
    repo_root = Path(__file__).resolve().parents[1]
    package_dir = repo_root / "src" / "spotm3u"
    env = _build_jinja(package_dir)

    template = env.get_template("_header.html")
    request = type("Request", (), {"endpoint": "index"})()
    # ``workflow_stage`` is deliberately not passed: the template treats any
    # *defined* value as an explicit stage override, so passing ``None`` made it
    # compare ``None`` against the step numbers and fail. Leaving it undefined
    # is what the app does for the pages whose stage comes from the endpoint,
    # and the template derives ``current_stage`` itself.
    context = {
        "request": request,
        "url_for": _url_for_stub,
    }
    rendered = template.render(**context)
    return rendered


def _url_for_stub(endpoint: str, **kwargs) -> str:
    if endpoint == "index":
        return "/"
    if endpoint == "playlists":
        return f"/playlists/{kwargs.get('job_id', 'abc123')}"
    if endpoint in ("processing", "start_processing", "processing_status"):
        return f"/processing/{kwargs.get('job_id', 'abc123')}/{kwargs.get('playlist_id', 1)}"
    if endpoint in ("processing_result", "batch_processing_result"):
        return f"/result/{kwargs.get('job_id', 'abc123')}/{kwargs.get('playlist_id', 1)}"
    if endpoint in ("batch_processing", "start_batch_processing", "batch_processing_status"):
        return f"/batch/{kwargs.get('job_id', 'abc123')}"
    if endpoint == "select_playlist":
        return f"/playlists/{kwargs.get('job_id', 'abc123')}/1"
    if endpoint == "select_playlists_batch":
        return f"/playlists/{kwargs.get('job_id', 'abc123')}/batch"
    return f"/{endpoint}"


def main() -> None:
    html = render_header()
    print(html)


if __name__ == "__main__":
    main()
