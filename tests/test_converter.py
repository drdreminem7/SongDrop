from pathlib import Path

import pytest

from songdrop.exceptions import MissingDependency
from songdrop.models import AudioFormat
from songdrop.services.converter import AudioConverter


def test_missing_ffmpeg_fails_when_conversion_is_required(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "source.webm"
    source.write_bytes(b"audio")
    monkeypatch.setattr("songdrop.services.converter.shutil.which", lambda name: None)

    with pytest.raises(MissingDependency, match="FFmpeg was not found"):
        AudioConverter().convert(source, tmp_path / "output.mp3", AudioFormat.MP3)


def test_existing_m4a_is_copied_without_ffmpeg(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "source.m4a"
    source.write_bytes(b"audio")
    destination = tmp_path / "output.m4a"
    monkeypatch.setattr("songdrop.services.converter.shutil.which", lambda name: None)

    assert AudioConverter().convert(source, destination, AudioFormat.M4A) == destination
    assert destination.read_bytes() == b"audio"
