"""Database-free duplicate checks for download-only output files."""

import logging
import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from mutagen import MutagenError
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4

from songdrop.models import AudioFormat, TrackMetadata

logger = logging.getLogger(__name__)
_SOURCE_TAG_DESCRIPTION = "SongDrop Source"
_M4A_SOURCE_TAG = "----:com.songdrop:source"
_DURATION_TOLERANCE_SECONDS = 2.0
_COLLISION_SUFFIX = re.compile(r" \(\d+\)$")


@dataclass(frozen=True)
class ExistingTrack:
    """Small identity projection read from one finished audio file."""

    title: str | None
    artist: str | None
    duration_seconds: float | None
    source_identity: str | None


AudioInspector = Callable[[Path, AudioFormat], ExistingTrack | None]


class DuplicateDetector:
    """Find an existing output using source ID, then conservative tag matching."""

    def __init__(
        self,
        root: Path,
        *,
        inspector: AudioInspector | None = None,
    ) -> None:
        self.root = root.expanduser().resolve(strict=False)
        self.inspector = inspector or inspect_audio

    def find(self, metadata: TrackMetadata, audio_format: AudioFormat) -> Path | None:
        """Return a matching finished file directly below the configured output root."""

        if not self.root.is_dir():
            return None
        inspected: list[tuple[Path, ExistingTrack]] = []
        source_matches: list[Path] = []
        expected_source = source_identity(metadata)
        for path in sorted(self.root.glob(f"*.{audio_format.value}")):
            if not path.is_file():
                continue
            existing = self.inspector(path, audio_format)
            if existing is None:
                continue
            if expected_source and existing.source_identity == expected_source:
                source_matches.append(path)
            inspected.append((path, existing))
        if source_matches:
            return min(source_matches, key=lambda path: _path_priority(path, metadata.title))

        if not metadata.artist or metadata.duration_seconds is None:
            return None
        expected_title = _normalize(metadata.title)
        expected_artist = _normalize(metadata.artist)
        tag_matches: list[Path] = []
        for path, existing in inspected:
            if not existing.title or not existing.artist or existing.duration_seconds is None:
                continue
            if _normalize(existing.title) != expected_title:
                continue
            if _normalize(existing.artist) != expected_artist:
                continue
            if (
                abs(existing.duration_seconds - metadata.duration_seconds)
                <= _DURATION_TOLERANCE_SECONDS
            ):
                tag_matches.append(path)
        return (
            min(tag_matches, key=lambda path: _path_priority(path, metadata.title))
            if tag_matches
            else None
        )


def source_identity(metadata: TrackMetadata) -> str | None:
    """Render the provider identity embedded into SongDrop-created files."""

    if not metadata.source_id:
        return None
    return f"{metadata.source.casefold()}:{metadata.source_id}"


def inspect_audio(path: Path, audio_format: AudioFormat) -> ExistingTrack | None:
    """Read only the identity fields needed for duplicate detection."""

    try:
        if audio_format is AudioFormat.MP3:
            return _inspect_mp3(path)
        return _inspect_m4a(path)
    except (OSError, ValueError, MutagenError) as error:
        logger.debug("Ignoring unreadable duplicate candidate %s: %s", path, error)
        return None


def _inspect_mp3(path: Path) -> ExistingTrack:
    audio = MP3(path)  # type: ignore[no-untyped-call]
    tags = audio.tags
    source = None
    if tags is not None:
        source = next(
            (
                str(frame.text[0])
                for frame in tags.getall("TXXX")
                if frame.desc == _SOURCE_TAG_DESCRIPTION and frame.text
            ),
            None,
        )
    if audio.info is None:
        raise ValueError("MP3 has no audio information")
    return ExistingTrack(
        title=_frame_text(tags.get("TIT2") if tags is not None else None),
        artist=_frame_text(tags.get("TPE1") if tags is not None else None),
        duration_seconds=float(audio.info.length),
        source_identity=source,
    )


def _inspect_m4a(path: Path) -> ExistingTrack:
    audio = MP4(path)  # type: ignore[no-untyped-call]
    tags = cast(Mapping[str, object], audio.tags or {})
    source_value = tags.get(_M4A_SOURCE_TAG)
    source = None
    if isinstance(source_value, list) and source_value:
        value = source_value[0]
        if isinstance(value, bytes):
            source = value.decode("utf-8", errors="replace")
    if audio.info is None:
        raise ValueError("M4A has no audio information")
    return ExistingTrack(
        title=_first_text(tags.get("\xa9nam")),
        artist=_first_text(tags.get("\xa9ART")),
        duration_seconds=float(audio.info.length),
        source_identity=source,
    )


def _frame_text(frame: object) -> str | None:
    if frame is None:
        return None
    text = getattr(frame, "text", None)
    return _first_text(text)


def _first_text(value: object) -> str | None:
    if not isinstance(value, list) or not value:
        return None
    first = value[0]
    return first.strip() if isinstance(first, str) and first.strip() else None


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _path_priority(path: Path, title: str) -> tuple[int, str]:
    if _normalize(path.stem) == _normalize(title):
        return 0, path.name.casefold()
    if _COLLISION_SUFFIX.search(path.stem):
        return 2, path.name.casefold()
    return 1, path.name.casefold()


__all__ = [
    "DuplicateDetector",
    "ExistingTrack",
    "_M4A_SOURCE_TAG",
    "_SOURCE_TAG_DESCRIPTION",
    "source_identity",
]
