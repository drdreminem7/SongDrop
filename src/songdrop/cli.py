"""Typer command-line interface for SongDrop."""

import logging
from functools import partial
from pathlib import Path
from typing import Annotated

import typer

from songdrop import __version__
from songdrop.config import load_config
from songdrop.exceptions import CollectionFailed, SongDropError
from songdrop.models import (
    AudioFormat,
    BatchItemResult,
    BatchOptions,
    BatchResult,
    BatchStatus,
    ImportResult,
)
from songdrop.providers.youtube import is_explicit_playlist_url
from songdrop.services.batch import build_batch_service
from songdrop.services.downloader import build_download_service

app = typer.Typer(
    add_completion=False,
    help="Prepare legally downloadable audio and import it into Apple Music.",
    no_args_is_help=True,
)
logger = logging.getLogger(__name__)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"SongDrop {__version__}")
        raise typer.Exit()


def configure_logging(verbose: bool) -> None:
    """Configure one concise stderr logging handler for the CLI."""

    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(levelname)s: %(message)s",
        force=True,
    )


@app.command()
def download(
    target: Annotated[
        str,
        typer.Argument(help="Track/playlist URL, or the word 'playlist', 'batch', or 'serve'."),
    ],
    value: Annotated[
        str | None,
        typer.Argument(help="URL after 'playlist', or text file after 'batch'."),
    ] = None,
    audio_format: Annotated[
        AudioFormat,
        typer.Option("--format", help="Output audio format."),
    ] = AudioFormat.MP3,
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Staging/preservation root (default: ~/Downloads/SongDrop).",
        ),
    ] = None,
    keep_file: Annotated[
        bool,
        typer.Option(
            "--keep-file",
            help="Keep SongDrop's tagged staging copy after a verified Music import.",
        ),
    ] = False,
    download_only: Annotated[
        bool,
        typer.Option(
            "--download-only",
            help="Save finished tagged files to the output folder without opening Apple Music.",
        ),
    ] = False,
    max_items: Annotated[
        int,
        typer.Option(
            "--max-items",
            min=1,
            max=10_000,
            help="Maximum playlist/batch items to expand (default: 200).",
        ),
    ] = 200,
    fail_fast: Annotated[
        bool,
        typer.Option("--fail-fast", help="Stop a batch after its first failed item."),
    ] = False,
    port: Annotated[
        int,
        typer.Option(
            "--port",
            min=1_024,
            max=65_535,
            help="Loopback port used by 'songdrop serve' (default: 8765).",
        ),
    ] = 8765,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Show diagnostic logging."),
    ] = False,
    version: Annotated[
        bool | None,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show the installed version and exit.",
        ),
    ] = None,
) -> None:
    """Import media into Apple Music, or run the local extension service."""

    del version
    configure_logging(verbose)
    result: ImportResult | None = None
    batch_result: BatchResult | None = None
    try:
        if target.strip().casefold() == "serve":
            if value is not None:
                raise CollectionFailed("Usage: songdrop serve [--port 8765]")
            from songdrop.api import serve

            serve(port=port, verbose=verbose)
            return
        config = load_config(
            output=output,
            audio_format=audio_format,
            verbose=verbose,
            keep_file=keep_file,
            max_items=max_items,
            fail_fast=fail_fast,
            download_only=download_only,
        )
        mode, source = _resolve_target(target, value)
        if mode == "track":
            typer.echo("Inspecting source…")
            result = build_download_service(config).import_url(source)
        else:
            options = BatchOptions(
                max_items=config.max_batch_items,
                fail_fast=config.fail_fast,
            )
            batch_service = build_batch_service(config)
            if mode == "playlist":
                typer.echo("Inspecting playlist…")
                batch_result = batch_service.import_collection(
                    source,
                    options,
                    progress=partial(_show_batch_progress, download_only=download_only),
                )
            else:
                typer.echo("Reading batch file…")
                batch_result = batch_service.import_file(
                    Path(source),
                    options,
                    progress=partial(_show_batch_progress, download_only=download_only),
                )
    except SongDropError as error:
        typer.secho(f"Error: {error}", fg=typer.colors.RED, err=True)
        if error.preserved_path is not None:
            typer.echo("\nSongDrop preserved the recoverable staging data at:", err=True)
            typer.echo(str(error.preserved_path), err=True)
        raise typer.Exit(code=1) from error
    except Exception as error:  # normal CLI use should never expose raw tracebacks
        if verbose:
            logger.exception("Unexpected failure")
        typer.secho(f"Error: Unexpected failure: {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from error

    if batch_result is not None:
        _show_batch_summary(batch_result, download_only=download_only)
        if batch_result.failed_count:
            raise typer.Exit(code=1)
        return

    assert result is not None
    if result.already_downloaded:
        typer.secho("Already downloaded", fg=typer.colors.YELLOW)
        typer.echo(f"Existing file: {result.path}")
        return
    if download_only:
        typer.secho("Downloaded and prepared", fg=typer.colors.GREEN)
        typer.echo(f"Saved to: {result.path}")
        return
    typer.secho("Imported into Apple Music", fg=typer.colors.GREEN)
    typer.echo(f"Apple Music file: {result.path}")
    if result.staging_path is not None:
        typer.echo(f"Staging file kept at: {result.staging_path}")


def _resolve_target(target: str, value: str | None) -> tuple[str, str]:
    cleaned = target.strip()
    mode = cleaned.casefold()
    if mode in {"playlist", "batch"}:
        if value is None or not value.strip():
            argument = "URL" if mode == "playlist" else "FILE"
            raise CollectionFailed(f"Usage: songdrop {mode} {argument}")
        return mode, value.strip()
    if value is not None:
        raise CollectionFailed(
            "Unexpected second argument. Use 'songdrop playlist URL' or 'songdrop batch FILE'."
        )
    if is_explicit_playlist_url(cleaned):
        return "playlist", cleaned
    return "track", cleaned


def _show_batch_progress(
    index: int,
    total: int,
    item: BatchItemResult,
    *,
    download_only: bool = False,
) -> None:
    label = (
        item.result.metadata.title
        if item.result is not None
        else item.request.title or item.request.url
    )
    prefix = f"[{index}/{total}]"
    if item.status is BatchStatus.IMPORTED:
        action = "Saved" if download_only else "Imported"
        typer.secho(f"{prefix} {action}: {label}", fg=typer.colors.GREEN)
    elif item.status is BatchStatus.SKIPPED:
        typer.secho(f"{prefix} Skipped: {label} ({item.message})", fg=typer.colors.YELLOW)
    else:
        typer.secho(f"{prefix} Failed: {label}: {item.message}", fg=typer.colors.RED, err=True)
        if item.preserved_path is not None:
            typer.echo(f"  Preserved at: {item.preserved_path}", err=True)


def _show_batch_summary(result: BatchResult, *, download_only: bool = False) -> None:
    success_label = "saved" if download_only else "imported"
    typer.echo(
        "Batch complete: "
        f"{result.imported_count} {success_label}, "
        f"{result.skipped_count} skipped, "
        f"{result.failed_count} failed."
    )
    if result.retry_file is not None:
        typer.echo(f"Retry failed items with: songdrop batch {result.retry_file}")


if __name__ == "__main__":  # pragma: no cover
    app()
