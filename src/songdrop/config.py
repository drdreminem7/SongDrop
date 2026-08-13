"""Runtime configuration with a future-friendly resolution boundary."""

import os
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from songdrop.models import AudioFormat


class LibraryDestination(StrEnum):
    """Supported destinations for completed imports."""

    APPLE_MUSIC = "apple_music"
    FILESYSTEM = "filesystem"


def default_staging_dir() -> Path:
    """Return the default location for transient and preserved audio."""

    return Path.home() / "Downloads" / "SongDrop"


def default_acoustid_api_key() -> str | None:
    """Read the optional key from the process or a local, uncommitted .env file."""

    environment_value = os.environ.get("SONGDROP_ACOUSTID_API_KEY")
    if environment_value:
        return environment_value
    dotenv_path = Path.cwd() / ".env"
    try:
        lines = dotenv_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        candidate = line.strip()
        if candidate.startswith("export "):
            candidate = candidate.removeprefix("export ").lstrip()
        key, separator, value = candidate.partition("=")
        if separator and key.strip() == "SONGDROP_ACOUSTID_API_KEY":
            cleaned = value.strip()
            if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {'"', "'"}:
                cleaned = cleaned[1:-1]
            return cleaned or None
    return None


class SongDropConfig(BaseModel):
    """Resolved settings for one SongDrop invocation.

    A persistent configuration source can later be merged before constructing this model.
    """

    model_config = ConfigDict(frozen=True)

    staging_dir: Path = Field(default_factory=default_staging_dir)
    library_destination: LibraryDestination = LibraryDestination.APPLE_MUSIC
    delete_staging_after_verified_import: bool = True
    metadata_enrichment_enabled: bool = True
    lyrics_enabled: bool = True
    acoustid_api_key: str | None = Field(default_factory=default_acoustid_api_key, repr=False)
    audio_format: AudioFormat = AudioFormat.MP3
    max_batch_items: int = Field(default=200, ge=1, le=10_000)
    fail_fast: bool = False
    verbose: bool = False

    @property
    def download_only(self) -> bool:
        """Return whether finished files should remain in SongDrop's output folder."""

        return self.library_destination is LibraryDestination.FILESYSTEM

    @field_validator("staging_dir", mode="before")
    @classmethod
    def expand_staging_dir(cls, value: object) -> Path:
        if not isinstance(value, (str, Path)):
            raise TypeError("staging_dir must be a filesystem path")
        return Path(value).expanduser().resolve(strict=False)


def load_config(
    *,
    output: Path | None = None,
    audio_format: AudioFormat | str | None = None,
    verbose: bool = False,
    keep_file: bool = False,
    max_items: int | None = None,
    fail_fast: bool = False,
    download_only: bool = False,
) -> SongDropConfig:
    """Resolve defaults and per-command overrides into one immutable config."""

    values: dict[str, object] = {
        "verbose": verbose,
        "delete_staging_after_verified_import": not keep_file and not download_only,
        "fail_fast": fail_fast,
        "library_destination": (
            LibraryDestination.FILESYSTEM if download_only else LibraryDestination.APPLE_MUSIC
        ),
    }
    if output is not None:
        values["staging_dir"] = output
    if audio_format is not None:
        values["audio_format"] = audio_format
    if max_items is not None:
        values["max_batch_items"] = max_items
    return SongDropConfig.model_validate(values)
