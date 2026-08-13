import inspect
import subprocess
from pathlib import Path
from typing import Any

import pytest

from songdrop.config import SongDropConfig
from songdrop.exceptions import AppleMusicVerificationFailed, UnsupportedPlatform
from songdrop.providers import base, youtube
from songdrop.services.apple_music import AppleMusicImporter
from songdrop.services.downloader import build_download_service


class CapturingRunner:
    def __init__(self, stdout: str, *, returncode: int = 0, stderr: str = "") -> None:
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr
        self.args: list[str] | None = None
        self.options: dict[str, Any] = {}

    def __call__(self, args: list[str], **options: Any) -> subprocess.CompletedProcess[str]:
        self.args = args
        self.options = options
        return subprocess.CompletedProcess(
            args,
            self.returncode,
            stdout=self.stdout,
            stderr=self.stderr,
        )


def configured_importer(
    tmp_path: Path,
    runner: CapturingRunner,
) -> AppleMusicImporter:
    music_app = tmp_path / "Music.app"
    music_app.mkdir()
    return AppleMusicImporter(
        runner=runner,  # type: ignore[arg-type]
        platform_name="darwin",
        osascript_path="/usr/bin/osascript",
        music_app_path=music_app,
    )


def test_unicode_and_space_path_is_passed_as_a_separate_osascript_argument(
    tmp_path: Path,
) -> None:
    source = tmp_path / "SongDrop ü" / "Björk – Jóga.m4a"
    source.parent.mkdir()
    source.write_bytes(b"tagged")
    managed = tmp_path / "Music Media" / "Björk – Jóga.m4a"
    managed.parent.mkdir()
    managed.write_bytes(b"managed")
    runner = CapturingRunner(f"OK\x1eABC123\x1e{managed}\n")

    result = configured_importer(tmp_path, runner).import_track(source)

    assert runner.args == ["/usr/bin/osascript", "-", str(source.resolve())]
    assert 'tell application "Music"' in runner.options["input"]
    assert "shell" not in runner.options
    assert result.persistent_id == "ABC123"
    assert result.library_path == managed


def test_imported_track_that_still_references_staging_fails_verification(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.m4a"
    source.write_bytes(b"tagged")
    runner = CapturingRunner(f"OK\x1eABC123\x1e{source}\n")

    with pytest.raises(AppleMusicVerificationFailed, match="managed copy"):
        configured_importer(tmp_path, runner).import_track(source)
    assert source.exists()


def test_applescript_verification_error_is_not_treated_as_success(tmp_path: Path) -> None:
    source = tmp_path / "source.m4a"
    source.write_bytes(b"tagged")
    runner = CapturingRunner("VERIFY_ERROR\x1eNo matching persistent ID\n")

    with pytest.raises(AppleMusicVerificationFailed, match="persistent ID"):
        configured_importer(tmp_path, runner).import_track(source)


def test_unsupported_platform_is_rejected_without_invoking_runner(tmp_path: Path) -> None:
    runner = CapturingRunner("")
    importer = AppleMusicImporter(runner=runner, platform_name="linux")  # type: ignore[arg-type]

    with pytest.raises(UnsupportedPlatform, match="not yet supported"):
        importer.ensure_available()
    assert runner.args is None


def test_apple_music_integration_is_absent_from_provider_modules() -> None:
    provider_source = inspect.getsource(base) + inspect.getsource(youtube)
    assert "apple_music" not in provider_source
    assert "osascript" not in provider_source


def test_application_composition_uses_apple_music_as_default_destination(
    tmp_path: Path,
) -> None:
    service = build_download_service(SongDropConfig(staging_dir=tmp_path))
    assert isinstance(service.importer, AppleMusicImporter)
