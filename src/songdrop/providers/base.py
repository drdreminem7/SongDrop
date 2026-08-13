"""Provider contract for remote media sources."""

from abc import ABC, abstractmethod

from songdrop.models import CollectionMetadata, DownloadOptions, DownloadResult, TrackMetadata


class MediaProvider(ABC):
    """Normalize a provider's metadata and download behavior."""

    @abstractmethod
    def supports(self, url: str) -> bool:
        """Return whether this provider can handle the URL shape."""

    @abstractmethod
    def get_metadata(self, url: str) -> TrackMetadata:
        """Retrieve normalized metadata without downloading media."""

    @abstractmethod
    def download(self, url: str, options: DownloadOptions) -> DownloadResult:
        """Download media into a staging directory."""

    def source_id(self, url: str) -> str | None:
        """Return a stable provider identifier encoded in a URL, when available."""

        return None


class CollectionProvider(ABC):
    """Discover ordered track references without downloading their media."""

    @abstractmethod
    def supports_collection(self, url: str) -> bool:
        """Return whether this provider understands the collection URL."""

    @abstractmethod
    def get_collection(self, url: str, *, max_items: int) -> CollectionMetadata:
        """Return at most ``max_items`` lightweight entries in provider order."""
