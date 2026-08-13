from pathlib import Path
from typing import Any
from urllib.error import URLError

import pytest

from songdrop.models import AudioFormat, TrackMetadata
from songdrop.services.metadata import MetadataService


class FakeMP4(dict[str, Any]):
    tags: dict[str, Any] | None

    def __init__(self) -> None:
        super().__init__()
        self.tags = self
        self.saved = False

    def add_tags(self) -> None:
        self.tags = self

    def save(self) -> None:
        self.saved = True


def test_m4a_writes_track_number_and_full_release_date(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    track: TrackMetadata,
) -> None:
    fake_audio = FakeMP4()
    monkeypatch.setattr("songdrop.services.metadata.MP4", lambda path: fake_audio)
    monkeypatch.setattr(MetadataService, "_flush", lambda self, path: None)

    MetadataService().write(tmp_path / "track.m4a", AudioFormat.M4A, track)

    assert fake_audio["trkn"] == [(3, 0)]
    assert fake_audio["\xa9day"] == ["2025-04-12"]
    assert fake_audio["----:com.songdrop:source"] == [b"youtube:abc123"]
    assert fake_audio.saved


def test_m4a_embeds_plain_lyrics(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    track: TrackMetadata,
) -> None:
    fake_audio = FakeMP4()
    monkeypatch.setattr("songdrop.services.metadata.MP4", lambda path: fake_audio)
    monkeypatch.setattr(MetadataService, "_flush", lambda self, path: None)
    track_with_lyrics = TrackMetadata.model_validate(
        {**track.model_dump(), "lyrics": "Line one\nLine two"}
    )

    MetadataService().write(
        tmp_path / "track.m4a",
        AudioFormat.M4A,
        track_with_lyrics,
    )

    assert fake_audio["\xa9lyr"] == ["Line one\nLine two"]


def test_youtube_thumbnail_is_not_used_as_release_artwork(track: TrackMetadata) -> None:
    assert track.thumbnail_url is not None
    assert track.artwork_url is None
    assert MetadataService().fetch_artwork(track) is None


def test_unavailable_canonical_artwork_fails_open(
    monkeypatch: pytest.MonkeyPatch,
    track: TrackMetadata,
) -> None:
    with_artwork = TrackMetadata.model_validate(
        {**track.model_dump(), "artwork_url": "https://coverartarchive.org/cover.jpg"}
    )
    monkeypatch.setattr(
        "songdrop.services.metadata.urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(URLError("offline")),
    )

    assert MetadataService().fetch_artwork(with_artwork) is None


def test_invalid_canonical_artwork_fails_open(
    monkeypatch: pytest.MonkeyPatch,
    track: TrackMetadata,
) -> None:
    class InvalidImageResponse:
        def __enter__(self) -> "InvalidImageResponse":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self, limit: int) -> bytes:
            return b"not-an-image"

    with_artwork = TrackMetadata.model_validate(
        {**track.model_dump(), "artwork_url": "https://coverartarchive.org/cover.jpg"}
    )
    monkeypatch.setattr(
        "songdrop.services.metadata.urlopen",
        lambda *args, **kwargs: InvalidImageResponse(),
    )

    assert MetadataService().fetch_artwork(with_artwork) is None
