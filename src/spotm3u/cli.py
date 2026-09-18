"""Command-line interface for spotm3u."""

from typing import Annotated

import typer
from rich.console import Console

from . import __version__
from .log import configure_logging

app = typer.Typer(
    name="spotm3u",
    help="Create local M3U playlists from Spotify playlist metadata.",
    invoke_without_command=True,
)
console = Console()


def version_callback(value: bool) -> None:
    if value:
        console.print(f"spotm3u {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool | None,
        typer.Option("--version", callback=version_callback, is_eager=True),
    ] = None,
) -> None:
    """Show the application entry point without starting browser automation."""
    configure_logging()
    console.print("[bold]Spotify → Local M3U[/bold]")
    console.print("Reusable playlist, local-audio, and M3U services are ready.")