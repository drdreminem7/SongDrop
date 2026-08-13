"""Artwork retrieval and audio tag writing."""

import logging
import os
from pathlib import Path
from typing import cast
from urllib.error import URLError
from urllib.request import Request, urlopen

from mutagen import MutagenError
from mutagen.id3 import APIC, TALB, TDRC, TIT2, TPE1, TRCK, TXXX, USLT
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Cover
from mutagen.mp4 import error as MP4Error

from songdrop.exceptions import MetadataFailed
from songdrop.models import AudioFormat, TrackMetadata
from songdrop.services.duplicate_detector import (
    _M4A_SOURCE_TAG,
    _SOURCE_TAG_DESCRIPTION,
    source_identity,
)

_MAX_ARTWORK_BYTES = 10 * 1024 * 1024
logger = logging.getLogger(__name__)


class MetadataService:
    """Fetch optional artwork and write normalized tags with mutagen."""

    def fetch_artwork(self, metadata: TrackMetadata) -> bytes | None:
        if metadata.artwork_url is None:
            return None
        request = Request(str(metadata.artwork_url), headers={"User-Agent": "SongDrop/0.3"})
        try:
            with urlopen(request, timeout=15) as response:  # noqa: S310 - provider URL
                data = cast(bytes, response.read(_MAX_ARTWORK_BYTES + 1))
        except (OSError, URLError) as error:
            logger.warning("Artwork could not be downloaded; continuing without it: %s", error)
            return None
        if len(data) > _MAX_ARTWORK_BYTES:
            logger.warning("Artwork exceeds the 10 MiB safety limit; continuing without it.")
            return None
        if not (data.startswith(b"\x89PNG") or data.startswith(b"\xff\xd8\xff")):
            logger.warning("Artwork is not a supported PNG or JPEG; continuing without it.")
            return None
        return data

    def write(
        self,
        path: Path,
        audio_format: AudioFormat,
        metadata: TrackMetadata,
        artwork: bytes | None = None,
    ) -> None:
        try:
            if audio_format is AudioFormat.MP3:
                self._write_mp3(path, metadata, artwork)
            else:
                self._write_m4a(path, metadata, artwork)
            self._flush(path)
        except (OSError, ValueError, MutagenError, MP4Error) as error:
            raise MetadataFailed(f"Could not write metadata to {path.name}: {error}") from error

    @staticmethod
    def _write_mp3(path: Path, metadata: TrackMetadata, artwork: bytes | None) -> None:
        audio = MP3(path)  # type: ignore[no-untyped-call]
        if audio.tags is None:
            audio.add_tags()  # type: ignore[no-untyped-call]
        assert audio.tags is not None
        audio.tags.setall("TIT2", [TIT2(encoding=3, text=[metadata.title])])
        if metadata.artist:
            audio.tags.setall("TPE1", [TPE1(encoding=3, text=[metadata.artist])])
        if metadata.album:
            audio.tags.setall("TALB", [TALB(encoding=3, text=[metadata.album])])
        if metadata.track_number is not None:
            audio.tags.setall("TRCK", [TRCK(encoding=3, text=[str(metadata.track_number)])])
        release = _release_text(metadata)
        if release:
            audio.tags.setall("TDRC", [TDRC(encoding=3, text=[release])])
        if metadata.lyrics:
            audio.tags.setall(
                "USLT",
                [USLT(encoding=3, lang="eng", desc="", text=metadata.lyrics)],
            )
        if source := source_identity(metadata):
            audio.tags.setall(
                "TXXX:SongDrop Source",
                [TXXX(encoding=3, desc=_SOURCE_TAG_DESCRIPTION, text=[source])],
            )
        if artwork:
            mime = _image_mime(artwork)
            audio.tags.setall(
                "APIC",
                [APIC(encoding=3, mime=mime, type=3, desc="Cover", data=artwork)],
            )
        audio.save(v2_version=3)

    @staticmethod
    def _write_m4a(path: Path, metadata: TrackMetadata, artwork: bytes | None) -> None:
        audio = MP4(path)  # type: ignore[no-untyped-call]
        if audio.tags is None:
            audio.add_tags()  # type: ignore[no-untyped-call]
        audio["\xa9nam"] = [metadata.title]
        if metadata.artist:
            audio["\xa9ART"] = [metadata.artist]
        if metadata.album:
            audio["\xa9alb"] = [metadata.album]
        if metadata.track_number is not None:
            audio["trkn"] = [(metadata.track_number, 0)]
        release = _release_text(metadata)
        if release:
            audio["\xa9day"] = [release]
        if metadata.lyrics:
            audio["\xa9lyr"] = [metadata.lyrics]
        if source := source_identity(metadata):
            audio[_M4A_SOURCE_TAG] = [source.encode("utf-8")]
        if artwork:
            image_format = (
                MP4Cover.FORMAT_PNG if artwork.startswith(b"\x89PNG") else MP4Cover.FORMAT_JPEG
            )
            audio["covr"] = [
                MP4Cover(artwork, imageformat=image_format)  # type: ignore[no-untyped-call]
            ]
        audio.save()  # type: ignore[no-untyped-call]

    @staticmethod
    def _flush(path: Path) -> None:
        """Ensure all tag bytes reach the filesystem before another app imports them."""

        with path.open("rb") as tagged_file:
            os.fsync(tagged_file.fileno())


def _image_mime(data: bytes) -> str:
    return "image/png" if data.startswith(b"\x89PNG") else "image/jpeg"


def _release_text(metadata: TrackMetadata) -> str | None:
    if metadata.release_date is not None:
        return metadata.release_date.isoformat()
    if metadata.release_year is not None:
        return str(metadata.release_year)
    return None
