"""Normalized models shared across providers and services."""

from datetime import date
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class AudioFormat(StrEnum):
    """Audio containers currently supported by SongDrop."""

    M4A = "m4a"
    MP3 = "mp3"


class TrackMetadata(BaseModel):
    """Provider-independent metadata for one track."""

    model_config = ConfigDict(frozen=True)

    title: str = Field(min_length=1)
    artist: str | None = None
    featured_artists: tuple[str, ...] = ()
    album: str | None = None
    track_number: int | None = Field(default=None, ge=1)
    release_date: date | None = None
    release_year: int | None = Field(default=None, ge=1000, le=9999)
    duration_seconds: float | None = Field(default=None, ge=0)
    source: str
    source_url: HttpUrl
    source_id: str | None = None
    thumbnail_url: HttpUrl | None = None
    artwork_url: HttpUrl | None = None
    lyrics: str | None = None
    musicbrainz_recording_id: str | None = None
    musicbrainz_release_id: str | None = None


class DownloadOptions(BaseModel):
    """Options passed to a provider for its temporary download."""

    model_config = ConfigDict(frozen=True)

    staging_dir: Path
    audio_format: AudioFormat = AudioFormat.MP3


class DownloadResult(BaseModel):
    """A provider download before it is installed into the library."""

    model_config = ConfigDict(frozen=True)

    path: Path
    metadata: TrackMetadata


class ImportResult(BaseModel):
    """Final result returned after a completed import or download-only operation."""

    model_config = ConfigDict(frozen=True)

    path: Path
    metadata: TrackMetadata
    staging_path: Path | None = None
    music_persistent_id: str | None = None
    already_downloaded: bool = False


class MusicImportResult(BaseModel):
    """Verified identity of an imported local-library track."""

    model_config = ConfigDict(frozen=True)

    persistent_id: str = Field(min_length=1)
    library_path: Path


class TrackRequest(BaseModel):
    """One lightweight track reference discovered before any media is downloaded."""

    model_config = ConfigDict(frozen=True)

    url: str = Field(min_length=1)
    source: str
    source_id: str | None = None
    title: str | None = None


class CollectionMetadata(BaseModel):
    """A transient, ordered collection returned by a playlist-capable provider."""

    model_config = ConfigDict(frozen=True)

    title: str | None = None
    source: str
    source_url: str = Field(min_length=1)
    source_id: str | None = None
    items: tuple[TrackRequest, ...]


class BatchStatus(StrEnum):
    """Outcome of one item in a batch operation."""

    IMPORTED = "imported"
    SKIPPED = "skipped"
    FAILED = "failed"


class BatchItemResult(BaseModel):
    """One completed, skipped, or failed batch item."""

    model_config = ConfigDict(frozen=True)

    request: TrackRequest
    status: BatchStatus
    result: ImportResult | None = None
    message: str | None = None
    preserved_path: Path | None = None


class BatchResult(BaseModel):
    """Transient summary of a completed batch invocation."""

    model_config = ConfigDict(frozen=True)

    title: str | None = None
    items: tuple[BatchItemResult, ...]
    retry_file: Path | None = None

    @property
    def imported_count(self) -> int:
        return sum(item.status is BatchStatus.IMPORTED for item in self.items)

    @property
    def skipped_count(self) -> int:
        return sum(item.status is BatchStatus.SKIPPED for item in self.items)

    @property
    def failed_count(self) -> int:
        return sum(item.status is BatchStatus.FAILED for item in self.items)


class BatchOptions(BaseModel):
    """Controls collection expansion and current-operation failure behavior."""

    model_config = ConfigDict(frozen=True)

    max_items: int = Field(default=200, ge=1, le=10_000)
    fail_fast: bool = False
