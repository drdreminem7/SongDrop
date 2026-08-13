"""Public request and response models for SongDrop's localhost API."""

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from songdrop.models import AudioFormat


class JobDestination(StrEnum):
    """User-facing destinations exposed to trusted local clients."""

    APPLE_MUSIC = "apple_music"
    FILESYSTEM = "filesystem"


class JobStatus(StrEnum):
    """Lifecycle of an in-memory local API job."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobRequest(BaseModel):
    """Strict input accepted from the authorized browser extension."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=2_048)
    destination: JobDestination = JobDestination.APPLE_MUSIC
    audio_format: AudioFormat = AudioFormat.MP3


class JobProgress(BaseModel):
    """Current observable progress without exposing implementation details."""

    model_config = ConfigDict(frozen=True)

    phase: str
    current: int | None = Field(default=None, ge=0)
    total: int | None = Field(default=None, ge=0)
    label: str | None = None


class JobView(BaseModel):
    """Safe snapshot returned to the extension while a job is retained."""

    model_config = ConfigDict(frozen=True)

    id: str
    status: JobStatus
    destination: JobDestination
    audio_format: AudioFormat
    progress: JobProgress
    created_at: datetime
    updated_at: datetime
    imported: int = Field(default=0, ge=0)
    saved: int = Field(default=0, ge=0)
    skipped: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    result_path: Path | None = None
    message: str | None = None
    preserved_path: Path | None = None


class JobAccepted(BaseModel):
    """Submission receipt used for subsequent status polling."""

    model_config = ConfigDict(frozen=True)

    job_id: str
    status: JobStatus = JobStatus.QUEUED


class SessionResult(BaseModel):
    """Automatically issued credential for the trusted local extension."""

    model_config = ConfigDict(frozen=True)

    token: str


class HealthResult(BaseModel):
    """Unauthenticated liveness response containing no sensitive state."""

    model_config = ConfigDict(frozen=True)

    service: str = "songdrop"
    version: str


def utc_now() -> datetime:
    """Return a timezone-aware timestamp for deterministic model construction."""

    return datetime.now(UTC)
