import pytest

from songdrop.exceptions import MetadataFailed
from songdrop.providers.youtube import infer_music_title_artist, normalize_youtube_metadata


def test_normalizes_only_reliable_youtube_metadata() -> None:
    metadata = normalize_youtube_metadata(
        {
            "id": "abc123",
            "title": "  Real title ",
            "artist": "  Real artist ",
            "album": "Real album",
            "track_number": 4,
            "release_date": "20250130",
            "release_year": 2025,
            "uploader": "Channel name",
            "duration": 42,
            "webpage_url": "https://www.youtube.com/watch?v=abc123",
            "thumbnail": "https://i.ytimg.com/cover.jpg",
        },
        "https://youtu.be/abc123",
    )

    assert metadata.title == "Real title"
    assert metadata.artist == "Real artist"
    assert metadata.album == "Real album"
    assert metadata.duration_seconds == 42.0
    assert metadata.source_id == "abc123"
    assert metadata.track_number == 4
    assert metadata.release_date is not None
    assert metadata.release_date.isoformat() == "2025-01-30"
    assert metadata.release_year == 2025


def test_does_not_treat_uploader_as_artist() -> None:
    metadata = normalize_youtube_metadata(
        {"id": "abc123", "title": "Title", "uploader": "A channel"},
        "https://youtu.be/abc123",
    )
    assert metadata.artist is None
    assert metadata.album is None


def test_missing_title_is_a_metadata_failure() -> None:
    with pytest.raises(MetadataFailed, match="track title"):
        normalize_youtube_metadata({"id": "abc123"}, "https://youtu.be/abc123")


def test_infers_artist_and_clean_track_for_corroborated_music_video() -> None:
    info = {
        "title": "INNA feat. Yandel - In Your Eyes | Official Music Video",
        "channel": "INNA",
        "uploader": "INNA",
        "categories": ["Music"],
    }

    assert infer_music_title_artist(info) == ("INNA", "In Your Eyes (feat. Yandel)")
    metadata = normalize_youtube_metadata(info, "https://youtu.be/Od-6uzcLGqw")
    assert metadata.artist == "INNA"
    assert metadata.title == "In Your Eyes (feat. Yandel)"


@pytest.mark.parametrize(
    ("video_title", "expected_title"),
    [
        ("Artist - Song (Official Video)", "Song"),
        ("Artist - Song [Official Audio]", "Song"),
        ("Artist - Song | Lyric Video", "Song"),
        ("Artist - Song | Visualizer", "Song"),
        ("Artist - Song (Official Video HD)", "Song"),
        ("Artist - Song [Official Music Video 4K]", "Song"),
    ],
)
def test_removes_only_allowlisted_music_presentation_suffixes(
    video_title: str,
    expected_title: str,
) -> None:
    assert infer_music_title_artist(
        {"title": video_title, "channel": "Artist Official", "categories": ["Music"]}
    ) == ("Artist", expected_title)


def test_does_not_infer_from_non_music_video() -> None:
    assert infer_music_title_artist(
        {
            "title": "Creator - A surprising announcement | Official Video",
            "channel": "Creator",
            "categories": ["Entertainment"],
        }
    ) == (None, None)


def test_does_not_infer_when_channel_does_not_corroborate_artist() -> None:
    assert infer_music_title_artist(
        {
            "title": "Famous Artist - Song | Official Music Video",
            "channel": "Random Reuploads",
            "categories": ["Music"],
        }
    ) == (None, None)


def test_structured_track_and_artist_take_precedence_over_heuristic() -> None:
    metadata = normalize_youtube_metadata(
        {
            "title": "Channel - Video title | Official Video",
            "track": "Canonical Track",
            "artist": "Canonical Artist",
            "channel": "Channel",
            "categories": ["Music"],
        },
        "https://youtu.be/abcdefghijk",
    )
    assert metadata.title == "Canonical Track"
    assert metadata.artist == "Canonical Artist"


def test_structured_artist_is_kept_while_video_title_is_cleaned() -> None:
    metadata = normalize_youtube_metadata(
        {
            "title": "Canonical Artist - Clean Me | Official Music Video",
            "artist": "Canonical Artist",
            "categories": ["Music"],
        },
        "https://youtu.be/abcdefghijk",
    )
    assert metadata.title == "Clean Me"
    assert metadata.artist == "Canonical Artist"
