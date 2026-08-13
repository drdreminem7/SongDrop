from pathlib import Path

import pytest
from typer.testing import CliRunner

from songdrop.cli import app
from songdrop.config import SongDropConfig
from songdrop.exceptions import UnsupportedPlatform
from songdrop.models import (
    BatchItemResult,
    BatchResult,
    BatchStatus,
    ImportResult,
    TrackMetadata,
    TrackRequest,
)

runner = CliRunner()


def test_version_option_does_not_require_url() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "SongDrop 0.4.3"


def test_serve_target_starts_loopback_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[int, bool]] = []
    monkeypatch.setattr(
        "songdrop.api.serve",
        lambda *, port, verbose: calls.append((port, verbose)),
    )

    result = runner.invoke(app, ["serve", "--port", "9876", "--verbose"])

    assert result.exit_code == 0
    assert calls == [(9876, True)]


def test_unsupported_platform_is_readable_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnsupportedService:
        def import_url(self, url: str) -> None:
            raise UnsupportedPlatform(
                "SongDrop's current default library destination is Apple Music on macOS.\n"
                "This platform is not yet supported."
            )

    monkeypatch.setattr(
        "songdrop.cli.build_download_service",
        lambda config: UnsupportedService(),
    )
    result = runner.invoke(app, ["https://www.youtube.com/watch?v=abc123"])
    assert result.exit_code == 1
    assert "default library destination is Apple Music on macOS" in result.output
    assert "Traceback" not in result.output


def test_keep_file_cli_override_reaches_configuration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: list[SongDropConfig] = []
    metadata = TrackMetadata(
        title="Track",
        source="youtube",
        source_url="https://www.youtube.com/watch?v=abcdefghijk",
    )

    class SuccessfulService:
        def import_url(self, url: str) -> ImportResult:
            return ImportResult(
                path=tmp_path / "Music Media" / "Track.m4a",
                staging_path=tmp_path / "SongDrop" / "Track.m4a",
                metadata=metadata,
            )

    def fake_builder(config: SongDropConfig) -> SuccessfulService:
        captured.append(config)
        return SuccessfulService()

    monkeypatch.setattr("songdrop.cli.build_download_service", fake_builder)
    result = runner.invoke(
        app,
        ["https://www.youtube.com/watch?v=abcdefghijk", "--keep-file"],
    )

    assert result.exit_code == 0
    assert captured[0].delete_staging_after_verified_import is False
    assert "Staging file kept at:" in result.output


def test_explicit_playlist_command_uses_batch_service_by_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    playlist_url = "https://music.youtube.com/playlist?list=PL123"
    captured: list[tuple[str, int, bool]] = []
    metadata = TrackMetadata(
        title="Track",
        source="youtube",
        source_url="https://www.youtube.com/watch?v=abc12345678",
    )
    imported = ImportResult(path=tmp_path / "Music" / "Track.m4a", metadata=metadata)
    item = BatchItemResult(
        request=TrackRequest(
            url=str(metadata.source_url),
            source="youtube",
            source_id="abc12345678",
            title="Track",
        ),
        status=BatchStatus.IMPORTED,
        result=imported,
    )

    class SuccessfulBatchService:
        def import_collection(self, url: str, options: object, *, progress: object) -> BatchResult:
            captured.append((url, options.max_items, options.fail_fast))  # type: ignore[attr-defined]
            progress(1, 1, item)  # type: ignore[operator]
            return BatchResult(title="Playlist", items=(item,))

    monkeypatch.setattr(
        "songdrop.cli.build_batch_service",
        lambda config: SuccessfulBatchService(),
    )
    result = runner.invoke(
        app,
        ["playlist", playlist_url, "--max-items", "25", "--fail-fast"],
    )

    assert result.exit_code == 0
    assert captured == [(playlist_url, 25, True)]
    assert "[1/1] Imported: Track" in result.output
    assert "1 imported, 0 skipped, 0 failed" in result.output


def test_direct_playlist_url_uses_collection_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class EmptyBatchService:
        def import_collection(self, url: str, options: object, *, progress: object) -> BatchResult:
            calls.append(url)
            return BatchResult(items=())

    monkeypatch.setattr("songdrop.cli.build_batch_service", lambda config: EmptyBatchService())
    playlist_url = "https://www.youtube.com/playlist?list=PL123"

    result = runner.invoke(app, [playlist_url])

    assert result.exit_code == 0
    assert calls == [playlist_url]
    assert "Batch complete" in result.output


def test_watch_url_with_list_parameter_remains_single_track(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    metadata = TrackMetadata(
        title="Track",
        source="youtube",
        source_url="https://www.youtube.com/watch?v=abc12345678",
    )

    class SuccessfulService:
        def import_url(self, url: str) -> ImportResult:
            calls.append(url)
            return ImportResult(path=tmp_path / "Music" / "Track.m4a", metadata=metadata)

    monkeypatch.setattr("songdrop.cli.build_download_service", lambda config: SuccessfulService())
    url = "https://www.youtube.com/watch?v=abc12345678&list=PL123"

    result = runner.invoke(app, [url])

    assert result.exit_code == 0
    assert calls == [url]


def test_download_only_cli_skips_import_presentation_and_keeps_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: list[SongDropConfig] = []
    output_path = tmp_path / "SongDrop" / "Artist - Track.mp3"
    metadata = TrackMetadata(
        title="Track",
        artist="Artist",
        source="youtube",
        source_url="https://www.youtube.com/watch?v=abc12345678",
    )

    class DownloadOnlyService:
        def import_url(self, url: str) -> ImportResult:
            return ImportResult(
                path=output_path,
                staging_path=output_path,
                metadata=metadata,
            )

    def fake_builder(config: SongDropConfig) -> DownloadOnlyService:
        captured.append(config)
        return DownloadOnlyService()

    monkeypatch.setattr("songdrop.cli.build_download_service", fake_builder)
    result = runner.invoke(
        app,
        [
            "https://www.youtube.com/watch?v=abc12345678",
            "--format",
            "mp3",
            "--download-only",
        ],
    )

    assert result.exit_code == 0
    assert captured[0].download_only is True
    assert "Downloaded and prepared" in result.output
    assert f"Saved to: {output_path}" in result.output
    assert "Imported into Apple Music" not in result.output
