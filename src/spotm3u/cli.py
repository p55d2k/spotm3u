"""Command-line interface for spotm3u."""

from typing import Annotated

import typer
from rich.console import Console

from . import __version__

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
    """Start the Spotify to local M3U workflow."""
    console.print("[bold]Spotify → Local M3U[/bold]")
    console.print("Task 1 is ready. Browser automation will be added in a later task.")
