from pathlib import Path
from typing import Any

import pytest
from yt_dlp.utils import DownloadError

from songdrop.exceptions import DownloadFailed
from songdrop.models import AudioFormat, DownloadOptions
from songdrop.providers.youtube import YouTubeProvider


def test_transient_403_refreshes_extraction_and_retries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    attempts = 0
    sleeps: list[float] = []

    class FakeYDL:
        def __init__(self, options: dict[str, Any]) -> None:
            self.options = options

        def __enter__(self) -> "FakeYDL":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def extract_info(self, url: str, *, download: bool) -> dict[str, Any]:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise DownloadError("unable to download video data: HTTP Error 403: Forbidden")
            output = tmp_path / "source.webm"
            output.write_bytes(b"audio")
            return {
                "id": "abc12345678",
                "title": "Artist - Song (Official Video)",
                "artist": "Artist",
                "track": "Song",
                "webpage_url": url,
                "filepath": str(output),
            }

        def prepare_filename(self, info: dict[str, Any]) -> str:
            return str(tmp_path / "source.webm")

    monkeypatch.setattr("yt_dlp.YoutubeDL", FakeYDL)
    monkeypatch.setattr(YouTubeProvider, "_base_options", staticmethod(lambda: {}))
    provider = YouTubeProvider(sleeper=sleeps.append)

    result = provider.download(
        "https://www.youtube.com/watch?v=abc12345678",
        DownloadOptions(staging_dir=tmp_path, audio_format=AudioFormat.MP3),
    )

    assert attempts == 2
    assert sleeps == [0.75]
    assert result.path == tmp_path / "source.webm"


def test_non_transient_download_error_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    attempts = 0

    class FakeYDL:
        def __init__(self, options: dict[str, Any]) -> None:
            pass

        def __enter__(self) -> "FakeYDL":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def extract_info(self, url: str, *, download: bool) -> dict[str, Any]:
            nonlocal attempts
            attempts += 1
            raise DownloadError("This video is private")

    monkeypatch.setattr("yt_dlp.YoutubeDL", FakeYDL)
    monkeypatch.setattr(YouTubeProvider, "_base_options", staticmethod(lambda: {}))
    provider = YouTubeProvider(sleeper=lambda seconds: None)

    with pytest.raises(DownloadFailed, match="private"):
        provider.download(
            "https://www.youtube.com/watch?v=abc12345678",
            DownloadOptions(staging_dir=tmp_path),
        )

    assert attempts == 1
