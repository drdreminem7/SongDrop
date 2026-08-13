from pathlib import Path

import pytest

from songdrop.exceptions import FileWriteFailed
from songdrop.models import AudioFormat, TrackMetadata
from songdrop.services.library import LibraryService


def test_builds_flat_transient_staging_destination(
    library_root: Path, track: TrackMetadata
) -> None:
    destination = LibraryService(library_root).destination(track, AudioFormat.M4A)
    assert destination == library_root / "An Artist - A Track.m4a"


def test_unknown_artist_has_explicit_staging_filename(library_root: Path) -> None:
    metadata = TrackMetadata(
        title="A / Track",
        source="youtube",
        source_url="https://youtu.be/abc123",
    )
    destination = LibraryService(library_root).destination(metadata, AudioFormat.MP3)
    assert destination == library_root / "Unknown Artist - A _ Track.mp3"


def test_download_only_destination_uses_title_without_artist(
    library_root: Path, track: TrackMetadata
) -> None:
    destination = LibraryService(library_root).destination(
        track,
        AudioFormat.MP3,
        title_only=True,
    )

    assert destination == library_root / "A Track.mp3"


def test_only_owned_staging_files_can_be_deleted(library_root: Path) -> None:
    external = library_root / "user-file.m4a"
    external.write_bytes(b"user data")
    library = LibraryService(library_root)

    with pytest.raises(FileWriteFailed, match="does not own"):
        library.delete_owned(external)
    assert external.read_bytes() == b"user data"


def test_promote_and_delete_owned_file(library_root: Path, track: TrackMetadata) -> None:
    library = LibraryService(library_root)
    session = library.create_session()
    source = session / "processed.m4a"
    source.write_bytes(b"audio")
    promoted = library.promote(
        session,
        source,
        library.destination(track, AudioFormat.M4A),
    )

    assert library.owns_file(promoted)
    library.delete_owned(promoted)
    library.cleanup_session(session)
    assert list(library_root.iterdir()) == []


def test_promote_rejects_source_outside_owned_session(
    tmp_path: Path, library_root: Path, track: TrackMetadata
) -> None:
    external = tmp_path / "outside.m4a"
    external.write_bytes(b"audio")
    library = LibraryService(library_root)
    session = library.create_session()
    with pytest.raises(FileWriteFailed, match="outside library"):
        library.promote(session, external, library.destination(track, AudioFormat.M4A))
