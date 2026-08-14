"""Shared constants for SongDrop's local browser integration."""

from pathlib import Path

EXTENSION_ID = "golnlibblfmclfpbmibdgkpmejmhhofg"
EXTENSION_ORIGIN = f"chrome-extension://{EXTENSION_ID}"
NATIVE_HOST_NAME = "com.songdrop.service_launcher"
API_HOST = "127.0.0.1"
API_PORT = 8765
API_BASE_URL = f"http://{API_HOST}:{API_PORT}"


def extension_origin_variants(origin: str = EXTENSION_ORIGIN) -> tuple[str, str]:
    """Return Chromium's two equivalent serializations of one extension origin."""

    normalized = origin.rstrip("/")
    return normalized, f"{normalized}/"


def brave_native_host_manifest_path(home: Path | None = None) -> Path:
    """Return Brave's per-user native-messaging manifest location on macOS."""

    return brave_native_host_manifest_paths(home)[0]


def brave_native_host_manifest_paths(home: Path | None = None) -> tuple[Path, ...]:
    """Return native-host locations used across current Brave macOS builds."""

    root = home or Path.home()
    filename = f"{NATIVE_HOST_NAME}.json"
    brave_path = (
        root
        / "Library"
        / "Application Support"
        / "BraveSoftware"
        / "Brave-Browser"
        / "NativeMessagingHosts"
        / filename
    )
    chromium_compatibility_path = (
        root
        / "Library"
        / "Application Support"
        / "Google"
        / "Chrome"
        / "NativeMessagingHosts"
        / filename
    )
    return brave_path, chromium_compatibility_path
