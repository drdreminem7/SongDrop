"""Application-level exceptions suitable for presentation by the CLI."""

from pathlib import Path


class SongDropError(Exception):
    """Base class for expected SongDrop failures."""

    def __init__(self, message: str, *, preserved_path: Path | None = None) -> None:
        super().__init__(message)
        self.preserved_path = preserved_path

    def with_preserved_path(self, path: Path) -> "SongDropError":
        """Attach a recovery location without obscuring the original failure."""

        if self.preserved_path is None:
            self.preserved_path = path
        return self


class UnsupportedURL(SongDropError):
    """Raised when no configured provider understands a URL."""


class MissingDependency(SongDropError):
    """Raised when a required external executable or package is unavailable."""


class DownloadFailed(SongDropError):
    """Raised when media cannot be downloaded."""


class CollectionFailed(SongDropError):
    """Raised when a playlist or batch input cannot be expanded safely."""


class CollectionLimitExceeded(CollectionFailed):
    """Raised when collection discovery exceeds the configured safety limit."""


class OperationCancelled(SongDropError):
    """Raised after user cancellation has preserved the best recoverable data."""


class MetadataFailed(SongDropError):
    """Raised when metadata cannot be retrieved or written."""


class ConversionFailed(SongDropError):
    """Raised when FFmpeg cannot create the requested audio format."""


class FileWriteFailed(SongDropError):
    """Raised when a safe library path cannot be created or written."""


class UnsupportedPlatform(SongDropError):
    """Raised when the configured library destination is unavailable on this OS."""


class AppleMusicUnavailable(SongDropError):
    """Raised when Music or its AppleScript bridge cannot be used."""


class AppleMusicImportFailed(SongDropError):
    """Raised when Music rejects or cannot complete an import."""


class AppleMusicVerificationFailed(SongDropError):
    """Raised when an imported Music library entry cannot be safely verified."""


class BrowserHelperFailed(SongDropError):
    """Raised when the local browser helper cannot be installed or started."""
