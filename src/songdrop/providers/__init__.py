"""Media provider implementations and selection helpers."""

from collections.abc import Iterable

from songdrop.exceptions import CollectionFailed, UnsupportedURL
from songdrop.providers.base import CollectionProvider, MediaProvider
from songdrop.providers.youtube import YouTubeProvider


def default_providers() -> tuple[MediaProvider, ...]:
    """Build the currently available single-track provider set."""

    return (YouTubeProvider(),)


def select_provider(url: str, providers: Iterable[MediaProvider]) -> MediaProvider:
    """Return the first provider that explicitly supports the URL."""

    for provider in providers:
        if provider.supports(url):
            return provider
    raise UnsupportedURL(
        "The URL is unsupported; SongDrop accepts YouTube and YouTube Music tracks."
    )


def default_collection_providers() -> tuple[CollectionProvider, ...]:
    """Build collection providers without adding persistent library state."""

    return (YouTubeProvider(),)


def select_collection_provider(
    url: str,
    providers: Iterable[CollectionProvider],
) -> CollectionProvider:
    """Return the first provider that explicitly supports a collection URL."""

    for provider in providers:
        if provider.supports_collection(url):
            return provider
    raise CollectionFailed("The URL is not a supported YouTube or YouTube Music playlist.")


__all__ = [
    "CollectionProvider",
    "MediaProvider",
    "YouTubeProvider",
    "default_collection_providers",
    "default_providers",
    "select_collection_provider",
    "select_provider",
]
