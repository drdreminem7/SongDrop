from pathlib import Path

from songdrop.models import AudioFormat, TrackMetadata
from songdrop.services.duplicate_detector import DuplicateDetector, ExistingTrack


def metadata(
    *,
    title: str = "Amazing",
    artist: str = "INNA",
    source_id: str = "gKHe12T6GMY",
    duration: float = 206.6,
) -> TrackMetadata:
    return TrackMetadata(
        title=title,
        artist=artist,
        duration_seconds=duration,
        source="youtube",
        source_url=f"https://www.youtube.com/watch?v={source_id}",
        source_id=source_id,
    )


def test_source_id_is_the_strongest_duplicate_signal(tmp_path: Path) -> None:
    existing = tmp_path / "legacy name.mp3"
    existing.write_bytes(b"audio")

    def inspect(path: Path, audio_format: AudioFormat) -> ExistingTrack:
        return ExistingTrack(
            title="Different title",
            artist="Different artist",
            duration_seconds=1,
            source_identity="youtube:gKHe12T6GMY",
        )

    assert (
        DuplicateDetector(tmp_path, inspector=inspect).find(metadata(), AudioFormat.MP3) == existing
    )


def test_legacy_file_matches_exact_tags_and_duration_without_source_tag(
    tmp_path: Path,
) -> None:
    existing = tmp_path / "INNA - Amazing.mp3"
    existing.write_bytes(b"audio")

    def inspect(path: Path, audio_format: AudioFormat) -> ExistingTrack:
        return ExistingTrack(
            title="Amazing",
            artist="INNA",
            duration_seconds=206.59,
            source_identity=None,
        )

    assert (
        DuplicateDetector(tmp_path, inspector=inspect).find(metadata(), AudioFormat.MP3) == existing
    )


def test_same_title_by_different_artist_is_not_a_duplicate(tmp_path: Path) -> None:
    existing = tmp_path / "Amazing.mp3"
    existing.write_bytes(b"audio")

    def inspect(path: Path, audio_format: AudioFormat) -> ExistingTrack:
        return ExistingTrack(
            title="Amazing",
            artist="Another Artist",
            duration_seconds=206.6,
            source_identity=None,
        )

    assert DuplicateDetector(tmp_path, inspector=inspect).find(metadata(), AudioFormat.MP3) is None


def test_same_artist_and_title_with_different_duration_is_not_a_duplicate(
    tmp_path: Path,
) -> None:
    existing = tmp_path / "Amazing.mp3"
    existing.write_bytes(b"audio")

    def inspect(path: Path, audio_format: AudioFormat) -> ExistingTrack:
        return ExistingTrack(
            title="Amazing",
            artist="INNA",
            duration_seconds=250,
            source_identity=None,
        )

    assert DuplicateDetector(tmp_path, inspector=inspect).find(metadata(), AudioFormat.MP3) is None


def test_prefers_title_only_path_over_legacy_and_numbered_duplicates(tmp_path: Path) -> None:
    paths = (
        tmp_path / "INNA - Amazing.mp3",
        tmp_path / "INNA - Amazing (2).mp3",
        tmp_path / "Amazing.mp3",
    )
    for path in paths:
        path.write_bytes(b"audio")

    def inspect(path: Path, audio_format: AudioFormat) -> ExistingTrack:
        return ExistingTrack(
            title="Amazing",
            artist="INNA",
            duration_seconds=206.6,
            source_identity=None,
        )

    assert DuplicateDetector(tmp_path, inspector=inspect).find(metadata(), AudioFormat.MP3) == (
        tmp_path / "Amazing.mp3"
    )
