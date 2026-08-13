"""YouTube and YouTube Music support through the yt-dlp Python API."""

import logging
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from pydantic import HttpUrl, ValidationError

from songdrop.exceptions import (
    CollectionFailed,
    CollectionLimitExceeded,
    DownloadFailed,
    MetadataFailed,
    MissingDependency,
)
from songdrop.models import (
    AudioFormat,
    CollectionMetadata,
    DownloadOptions,
    DownloadResult,
    TrackMetadata,
    TrackRequest,
)
from songdrop.providers.base import CollectionProvider, MediaProvider
from songdrop.utils.filenames import ensure_within

logger = logging.getLogger(__name__)

_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
}
_VIDEO_PATH_PREFIXES = ("/shorts/", "/live/", "/embed/")
_JS_RUNTIME_PRIORITY = ("deno", "node")
_TITLE_PRESENTATION_SUFFIX = re.compile(
    r"""
    \s*(?:
        [|｜]\s*|
        [\[(]\s*
    )
    (?:official\s+)?
    (?:music\s+)?
    (?:video|audio|lyric\s+video|lyrics|visuali[sz]er)
    (?:\s+(?:hd|hq|uhd|4k|1080p|720p))*
    (?:\s+\d{4})?
    \s*[\])]?\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)
_FEATURE_SEPARATOR = re.compile(r"\s+(?:feat\.?|ft\.?|featuring|with|x|&)\s+", re.IGNORECASE)
_FEATURE_CREDIT = re.compile(
    r"^(?P<primary>.+?)\s+(?:feat\.?|ft\.?|featuring)\s+(?P<guest>.+)$",
    re.IGNORECASE,
)
_NON_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")
_RETRYABLE_DOWNLOAD_FAILURE = re.compile(
    r"(?:HTTP Error (?:403|408|429|5\d\d)|timed?\s*out|temporar(?:y|ily))",
    re.IGNORECASE,
)


class _YTDLPLogger:
    """Route yt-dlp messages through standard logging."""

    def debug(self, message: str) -> None:
        if not message.startswith("[debug] "):
            logger.info(message)
        else:
            logger.debug(message)

    def info(self, message: str) -> None:
        logger.info(message)

    def warning(self, message: str) -> None:
        logger.warning(message)

    def error(self, message: str) -> None:
        logger.error(message)


def normalize_youtube_metadata(info: dict[str, Any], source_url: str) -> TrackMetadata:
    """Convert a yt-dlp extractor result into SongDrop's compact model."""

    title = info.get("title")
    if not isinstance(title, str) or not title.strip():
        raise MetadataFailed("YouTube did not return a track title.")

    webpage_url = info.get("webpage_url")
    normalized_url = webpage_url if isinstance(webpage_url, str) else source_url
    duration = info.get("duration")
    if not isinstance(duration, (int, float)) or duration < 0:
        duration = None

    def optional_text(key: str) -> str | None:
        value = info.get(key)
        return value.strip() if isinstance(value, str) and value.strip() else None

    track_number = _positive_int(info.get("track_number"))
    release_date = _parse_release_date(info.get("release_date"))
    release_year = _release_year(info.get("release_year"))
    if release_year is None and release_date is not None:
        release_year = release_date.year

    inferred_artist, inferred_title = infer_music_title_artist(info)
    featured_artists = _featured_artists_from_title(inferred_title)
    try:
        return TrackMetadata(
            title=optional_text("track") or inferred_title or title.strip(),
            artist=optional_text("artist") or inferred_artist,
            featured_artists=featured_artists,
            album=optional_text("album"),
            track_number=track_number,
            release_date=release_date,
            release_year=release_year,
            duration_seconds=float(duration) if duration is not None else None,
            source="youtube",
            source_url=HttpUrl(normalized_url),
            source_id=optional_text("id"),
            thumbnail_url=(HttpUrl(thumbnail) if (thumbnail := _thumbnail_url(info)) else None),
        )
    except ValidationError as error:
        raise MetadataFailed("YouTube returned invalid metadata.") from error


def infer_music_title_artist(info: dict[str, Any]) -> tuple[str | None, str | None]:
    """Infer artist/title only from a corroborated, conventional music-video title.

    This intentionally narrow fallback is used only when YouTube supplies neither structured
    track nor artist fields. It does not parse arbitrary video titles.
    """

    if _optional_text(info, "track"):
        return None, None
    categories = info.get("categories")
    if not (
        isinstance(categories, list)
        and any(isinstance(value, str) and value.casefold() == "music" for value in categories)
    ):
        return None, None
    raw_title = _optional_text(info, "title")
    if raw_title is None:
        return None, None
    cleaned_title = _TITLE_PRESENTATION_SUFFIX.sub("", raw_title).strip()
    if " - " not in cleaned_title:
        return None, None
    artist_candidate, track_candidate = (
        component.strip() for component in cleaned_title.split(" - ", 1)
    )
    if not artist_candidate or not track_candidate:
        return None, None
    structured_artist = _optional_text(info, "artist")
    corroborating_identity = (
        structured_artist or _optional_text(info, "channel") or _optional_text(info, "uploader")
    )
    if corroborating_identity is None or not _artist_matches_channel(
        artist_candidate, corroborating_identity
    ):
        return None, None
    if structured_artist:
        return None, track_candidate
    feature_credit = _FEATURE_CREDIT.match(artist_candidate)
    if feature_credit:
        primary_artist = feature_credit.group("primary").strip()
        guest_artist = feature_credit.group("guest").strip()
        return primary_artist, f"{track_candidate} (feat. {guest_artist})"
    return artist_candidate, track_candidate


def _artist_matches_channel(artist: str, channel: str) -> bool:
    primary_artist = _FEATURE_SEPARATOR.split(artist, maxsplit=1)[0]
    normalized_artist = _normalize_identity(primary_artist)
    normalized_channel = _normalize_identity(channel.removeprefix("@"))
    for suffix in ("officialmusic", "official", "music", "vevo"):
        normalized_channel = normalized_channel.removesuffix(suffix)
    return bool(normalized_artist and normalized_artist == normalized_channel)


def _normalize_identity(value: str) -> str:
    return _NON_ALPHANUMERIC.sub("", value.casefold())


def _featured_artists_from_title(title: str | None) -> tuple[str, ...]:
    if title is None:
        return ()
    match = re.search(r"\(feat\.\s+(.+?)\)\s*$", title, re.IGNORECASE)
    return (match.group(1).strip(),) if match else ()


def _optional_text(info: dict[str, Any], key: str) -> str | None:
    value = info.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _release_year(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or not 1000 <= value <= 9999:
        return None
    return value


def _parse_release_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    for date_format in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, date_format).date()
        except ValueError:
            continue
    return None


def _thumbnail_url(info: dict[str, Any]) -> str | None:
    thumbnails = info.get("thumbnails")
    if isinstance(thumbnails, list):
        for item in reversed(thumbnails):
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            extension = item.get("ext")
            if (
                isinstance(url, str)
                and url.strip()
                and isinstance(extension, str)
                and extension.lower() in {"jpg", "jpeg", "png"}
            ):
                return url.strip()
    thumbnail = info.get("thumbnail")
    return thumbnail.strip() if isinstance(thumbnail, str) and thumbnail.strip() else None


class YouTubeProvider(MediaProvider, CollectionProvider):
    """Provider implementation for individual YouTube videos."""

    def __init__(
        self,
        *,
        download_attempts: int = 3,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.download_attempts = max(1, download_attempts)
        self.sleeper = sleeper

    def supports(self, url: str) -> bool:
        try:
            parsed = urlparse(url)
        except ValueError:
            return False
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in _YOUTUBE_HOSTS:
            return False
        if parsed.hostname == "youtu.be":
            return bool(parsed.path.strip("/"))
        if parsed.path.rstrip("/") == "/watch":
            return bool(parse_qs(parsed.query).get("v", [""])[0])
        return any(
            parsed.path.startswith(prefix) and parsed.path != prefix
            for prefix in _VIDEO_PATH_PREFIXES
        )

    def source_id(self, url: str) -> str | None:
        """Extract a YouTube video ID without performing a network request."""

        try:
            parsed = urlparse(url)
        except ValueError:
            return None
        if parsed.hostname not in _YOUTUBE_HOSTS:
            return None
        if parsed.hostname == "youtu.be":
            return parsed.path.strip("/").split("/", 1)[0] or None
        if parsed.path.rstrip("/") == "/watch":
            return parse_qs(parsed.query).get("v", [""])[0] or None
        if any(parsed.path.startswith(prefix) for prefix in _VIDEO_PATH_PREFIXES):
            parts = parsed.path.strip("/").split("/")
            return parts[1] if len(parts) > 1 and parts[1] else None
        return None

    def supports_collection(self, url: str) -> bool:
        """Recognize YouTube playlist URLs, including watch URLs with a list ID."""

        try:
            parsed = urlparse(url)
        except ValueError:
            return False
        return bool(
            parsed.scheme in {"http", "https"}
            and parsed.hostname in _YOUTUBE_HOSTS - {"youtu.be"}
            and parse_qs(parsed.query).get("list", [""])[0]
        )

    def get_collection(self, url: str, *, max_items: int) -> CollectionMetadata:
        """Use yt-dlp's flat extraction to discover a playlist without media downloads."""

        if not self.supports_collection(url):
            raise CollectionFailed("The URL is not a supported YouTube playlist URL.")
        try:
            from yt_dlp import YoutubeDL
            from yt_dlp.utils import DownloadError
        except ImportError as error:  # pragma: no cover - installed project dependency
            raise MissingDependency(
                "yt-dlp was not found. Reinstall SongDrop to restore its dependencies."
            ) from error

        options = self._base_options()
        options.update(
            {
                "extract_flat": "in_playlist",
                "noplaylist": False,
                "playlistend": max_items + 1,
                "skip_download": True,
            }
        )
        try:
            with YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=False)
        except DownloadError as error:
            raise CollectionFailed(f"Could not inspect the playlist: {error}") from error
        except Exception as error:
            raise CollectionFailed("Could not inspect the YouTube playlist.") from error
        if not isinstance(info, dict):
            raise CollectionFailed("YouTube did not return playlist information.")
        raw_entries = info.get("entries")
        if not isinstance(raw_entries, list):
            raise CollectionFailed("YouTube did not return playlist entries.")
        entries = [entry for entry in raw_entries if isinstance(entry, dict)]
        if len(entries) > max_items:
            raise CollectionLimitExceeded(
                f"The playlist contains more than the {max_items}-track safety limit.\n"
                "Use --max-items to explicitly allow a larger batch."
            )

        requests = tuple(
            request for entry in entries if (request := self._collection_entry(entry)) is not None
        )
        if not requests:
            raise CollectionFailed("The playlist contains no available video entries.")
        playlist_id = _optional_text(info, "id") or _playlist_id(url)
        return CollectionMetadata(
            title=_optional_text(info, "title"),
            source="youtube",
            source_url=url,
            source_id=playlist_id,
            items=requests,
        )

    def get_metadata(self, url: str) -> TrackMetadata:
        if not self.supports(url):
            raise MetadataFailed("The URL is not a supported YouTube video URL.")
        try:
            from yt_dlp import YoutubeDL
            from yt_dlp.utils import DownloadError
        except ImportError as error:  # pragma: no cover - installed project dependency
            raise MissingDependency(
                "yt-dlp was not found. Reinstall SongDrop to restore its dependencies."
            ) from error

        try:
            with YoutubeDL(self._base_options()) as ydl:
                info = ydl.extract_info(url, download=False)
        except DownloadError as error:
            raise MetadataFailed(f"Could not retrieve metadata: {error}") from error
        except Exception as error:
            raise MetadataFailed("Could not retrieve metadata from YouTube.") from error
        if not isinstance(info, dict):
            raise MetadataFailed("YouTube did not return media metadata.")
        return normalize_youtube_metadata(info, url)

    def download(self, url: str, options: DownloadOptions) -> DownloadResult:
        try:
            from yt_dlp import YoutubeDL
            from yt_dlp.utils import DownloadError
        except ImportError as error:  # pragma: no cover - installed project dependency
            raise MissingDependency(
                "yt-dlp was not found. Reinstall SongDrop to restore its dependencies."
            ) from error

        staging_dir = options.staging_dir.resolve(strict=False)
        staging_dir.mkdir(parents=True, exist_ok=True)
        ydl_options = self._base_options()
        ydl_options.update(
            {
                "format": self._format_selector(options.audio_format),
                "outtmpl": str(staging_dir / "source.%(ext)s"),
            }
        )
        for attempt in range(1, self.download_attempts + 1):
            try:
                with YoutubeDL(ydl_options) as ydl:
                    info = ydl.extract_info(url, download=True)
                    if not isinstance(info, dict):
                        raise DownloadFailed("YouTube did not return download information.")
                    downloaded_path = self._downloaded_path(info, ydl, staging_dir)
                break
            except DownloadFailed:
                raise
            except DownloadError as error:
                if attempt >= self.download_attempts or not _is_retryable_download_error(error):
                    raise DownloadFailed(f"Download failed: {error}") from error
                delay = 0.75 * (2 ** (attempt - 1))
                logger.warning(
                    "Temporary YouTube media failure; refreshing the stream URL "
                    "and retrying (%d/%d).",
                    attempt + 1,
                    self.download_attempts,
                )
                self.sleeper(delay)
            except OSError as error:
                raise DownloadFailed(f"Could not write the download: {error}") from error
            except Exception as error:
                raise DownloadFailed("The YouTube download failed unexpectedly.") from error

        return DownloadResult(
            path=downloaded_path,
            metadata=normalize_youtube_metadata(info, url),
        )

    @staticmethod
    def _base_options() -> dict[str, Any]:
        return {
            "js_runtimes": _detect_js_runtimes(),
            "logger": _YTDLPLogger(),
            "noplaylist": True,
            "quiet": True,
            "no_warnings": False,
        }

    @staticmethod
    def _format_selector(audio_format: AudioFormat) -> str:
        if audio_format is AudioFormat.M4A:
            return "bestaudio[ext=m4a]/bestaudio/best"
        return "bestaudio/best"

    @staticmethod
    def _downloaded_path(info: dict[str, Any], ydl: Any, staging_dir: Path) -> Path:
        candidates: list[object] = [info.get("filepath"), info.get("_filename")]
        requested = info.get("requested_downloads")
        if isinstance(requested, list):
            candidates.extend(item.get("filepath") for item in requested if isinstance(item, dict))
        candidates.append(ydl.prepare_filename(info))
        for candidate in candidates:
            if not isinstance(candidate, str):
                continue
            path = ensure_within(staging_dir, Path(candidate))
            if path.is_file():
                return path
        files = [path for path in staging_dir.iterdir() if path.is_file()]
        if len(files) == 1:
            return files[0]
        raise DownloadFailed("yt-dlp completed but the downloaded audio file was not found.")

    def _collection_entry(self, entry: dict[str, Any]) -> TrackRequest | None:
        source_id = _optional_text(entry, "id")
        raw_url = _optional_text(entry, "webpage_url") or _optional_text(entry, "url")
        if raw_url and self.supports(raw_url):
            entry_url = raw_url
            source_id = source_id or self.source_id(raw_url)
        elif source_id:
            entry_url = f"https://www.youtube.com/watch?v={source_id}"
        else:
            return None
        return TrackRequest(
            url=entry_url,
            source="youtube",
            source_id=source_id,
            title=_optional_text(entry, "title"),
        )


def is_explicit_playlist_url(url: str) -> bool:
    """Return whether a URL explicitly addresses a playlist rather than one video."""

    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return bool(
        parsed.scheme in {"http", "https"}
        and parsed.hostname in _YOUTUBE_HOSTS - {"youtu.be"}
        and parsed.path.rstrip("/") == "/playlist"
        and _playlist_id(url)
    )


def _playlist_id(url: str) -> str | None:
    try:
        return parse_qs(urlparse(url).query).get("list", [""])[0] or None
    except ValueError:
        return None


def _is_retryable_download_error(error: Exception) -> bool:
    """Return whether a fresh yt-dlp extraction can reasonably recover the failure."""

    return bool(_RETRYABLE_DOWNLOAD_FAILURE.search(str(error)))


def _detect_js_runtimes() -> dict[str, dict[str, str]]:
    """Enable the first supported JavaScript runtime available on PATH."""

    for runtime_name in _JS_RUNTIME_PRIORITY:
        runtime_path = shutil.which(runtime_name)
        if runtime_path and _runtime_is_supported(runtime_name, runtime_path):
            logger.debug("Using %s JavaScript runtime at %s", runtime_name, runtime_path)
            return {runtime_name: {"path": runtime_path}}
    raise MissingDependency(
        "A supported JavaScript runtime was not found.\n"
        "Install Deno 2.3+ or Node.js 22+ and ensure it is available on PATH."
    )


def _runtime_is_supported(runtime_name: str, runtime_path: str) -> bool:
    minimum_major = {"deno": 2, "node": 22}[runtime_name]
    try:
        completed = subprocess.run(
            [runtime_path, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if completed.returncode != 0:
        return False
    version_output = completed.stdout.strip() or completed.stderr.strip()
    if runtime_name == "deno":
        version_output = version_output.removeprefix("deno ")
    else:
        version_output = version_output.removeprefix("v")
    try:
        major = int(version_output.split(".", 1)[0])
    except ValueError:
        return False
    return major >= minimum_major
