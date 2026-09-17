"""Command-line interface for spotm3u."""

import asyncio
from typing import Annotated

import typer
from rich.console import Console

from . import __version__
from .spotify.authentication import AuthenticationTimeoutError, SpotifyAuthenticator
from .spotify.browser import BrowserManager

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
    try:
        asyncio.run(_authenticate())
    except AuthenticationTimeoutError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc


async def _authenticate() -> None:
    browser = BrowserManager()
    await browser.start()
    try:
        page = await browser.new_page()
        console.print("\nOpening Spotify...")
        console.print("Please log in using the Spotify window.")
        await SpotifyAuthenticator().authenticate(page)
        console.print("[green]✓ Spotify login detected.[/green]")
    finally:
        await browser.close()
