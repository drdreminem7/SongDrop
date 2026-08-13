from pathlib import Path

import pytest

from songdrop.config import LibraryDestination, SongDropConfig, load_config
from songdrop.models import AudioFormat


def test_default_configuration_targets_apple_music_with_transient_staging() -> None:
    config = SongDropConfig()
    assert config.staging_dir == (Path.home() / "Downloads" / "SongDrop").resolve()
    assert config.library_destination is LibraryDestination.APPLE_MUSIC
    assert config.delete_staging_after_verified_import is True
    assert config.metadata_enrichment_enabled is True
    assert config.lyrics_enabled is True
    assert config.audio_format is AudioFormat.MP3
    assert config.max_batch_items == 200
    assert config.fail_fast is False


def test_cli_configuration_overrides_are_resolved(tmp_path: Path) -> None:
    config = load_config(
        output=tmp_path / "custom",
        audio_format="mp3",
        verbose=True,
        max_items=350,
        fail_fast=True,
    )
    assert config.staging_dir == (tmp_path / "custom").resolve()
    assert config.audio_format is AudioFormat.MP3
    assert config.verbose is True
    assert config.max_batch_items == 350
    assert config.fail_fast is True


def test_download_only_targets_filesystem_and_retains_finished_files(tmp_path: Path) -> None:
    config = load_config(output=tmp_path, download_only=True)

    assert config.library_destination is LibraryDestination.FILESYSTEM
    assert config.download_only is True
    assert config.delete_staging_after_verified_import is False


def test_keep_file_override_disables_verified_import_cleanup(tmp_path: Path) -> None:
    config = load_config(output=tmp_path, keep_file=True)
    assert config.delete_staging_after_verified_import is False


def test_optional_acoustid_key_is_read_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SONGDROP_ACOUSTID_API_KEY", "test-key")
    assert SongDropConfig().acoustid_api_key == "test-key"


def test_optional_acoustid_key_is_read_from_local_dotenv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("SONGDROP_ACOUSTID_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        'SONGDROP_ACOUSTID_API_KEY="dotenv-key"\n',
        encoding="utf-8",
    )

    assert SongDropConfig().acoustid_api_key == "dotenv-key"
