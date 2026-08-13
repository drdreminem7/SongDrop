from pathlib import Path

import pytest

from songdrop.models import TrackMetadata


@pytest.fixture
def track() -> TrackMetadata:
    return TrackMetadata(
        title="A Track",
        artist="An Artist",
        album="An Album",
        track_number=3,
        release_date="2025-04-12",
        release_year=2025,
        duration_seconds=123.4,
        source="youtube",
        source_url="https://www.youtube.com/watch?v=abc123",
        source_id="abc123",
        thumbnail_url="https://i.ytimg.com/vi/abc123/hqdefault.jpg",
    )


@pytest.fixture
def library_root(tmp_path: Path) -> Path:
    root = tmp_path / "library"
    root.mkdir()
    return root
