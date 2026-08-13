"""Structured music metadata, release artwork, lyrics, and optional fingerprint matching."""

import json
import logging
import random
import re
import shutil
import subprocess
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from pathlib import Path
from typing import Any, Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from songdrop.models import TrackMetadata

logger = logging.getLogger(__name__)

_MUSICBRAINZ_RECORDING_URL = "https://musicbrainz.org/ws/2/recording"
_COVER_ART_RELEASE_URL = "https://coverartarchive.org/release"
_LRCLIB_GET_URL = "https://lrclib.net/api/get"
_ACOUSTID_LOOKUP_URL = "https://api.acoustid.org/v2/lookup"
_USER_AGENT = "SongDrop/0.3 (local music metadata client)"
_FEATURE_SUFFIX = re.compile(r"\s*\(feat\.\s+.+?\)\s*$", re.IGNORECASE)
_NORMALIZE_TEXT = re.compile(r"[^a-z0-9]+")
_REDUNDANT_EDITION_SUFFIX = re.compile(
    r"\s*[\[(](?:original\s+version|(?:[^\[\]()]+?\s+)?radio\s+edit)[\])]\s*$",
    re.IGNORECASE,
)
_MINIMUM_REQUEST_INTERVAL = {
    "musicbrainz.org": 1.0,
    "api.acoustid.org": 1 / 3,
    "coverartarchive.org": 0.5,
    "lrclib.net": 0.5,
}
_RETRYABLE_HTTP_STATUS = {429, 500, 502, 503, 504}


@dataclass(frozen=True)
class _CachedError:
    message: str
    status: int | None


class RemoteLookupError(Exception):
    """An enrichment endpoint could not provide a usable response."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class JsonTransport(Protocol):
    """Small injectable HTTP boundary used by all metadata clients."""

    def request_json(
        self,
        url: str,
        params: Mapping[str, str | int],
        *,
        method: str = "GET",
    ) -> object: ...


class UrlLibJsonTransport:
    """Bounded HTTP transport with per-run caching, throttling, and safe retries."""

    def __init__(
        self,
        *,
        timeout: float = 15,
        max_bytes: int = 2 * 1024 * 1024,
        cache_size: int = 256,
        max_attempts: int = 3,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.cache_size = cache_size
        self.max_attempts = max_attempts
        self.clock = clock
        self.sleeper = sleeper
        self._cache: OrderedDict[tuple[str, str, bytes], object] = OrderedDict()
        self._last_request: dict[str, float] = {}

    def request_json(
        self,
        url: str,
        params: Mapping[str, str | int],
        *,
        method: str = "GET",
    ) -> object:
        encoded = urlencode(params).encode("utf-8")
        cache_key = (method, url, sha256(encoded).digest())
        cached = self._cache_get(cache_key)
        if cached is not None:
            if isinstance(cached, _CachedError):
                raise RemoteLookupError(cached.message, status=cached.status)
            return deepcopy(cached)

        request_url = f"{url}?{encoded.decode('ascii')}" if method == "GET" else url
        request = Request(
            request_url,
            data=encoded if method == "POST" else None,
            method=method,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": _USER_AGENT,
            },
        )
        payload = self._request_with_retries(request, url, cache_key)
        if len(payload) > self.max_bytes:
            raise RemoteLookupError(f"{url} returned an unexpectedly large response.")
        try:
            parsed = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RemoteLookupError(f"{url} returned invalid JSON.") from error
        self._cache_put(cache_key, parsed)
        return deepcopy(parsed)

    def _request_with_retries(
        self,
        request: Request,
        url: str,
        cache_key: tuple[str, str, bytes],
    ) -> bytes:
        for attempt in range(self.max_attempts):
            self._throttle(url)
            try:
                with urlopen(  # noqa: S310 - clients only pass fixed metadata APIs
                    request,
                    timeout=self.timeout,
                ) as response:
                    return cast(bytes, response.read(self.max_bytes + 1))
            except HTTPError as error:
                message = f"{url} returned HTTP {error.code}."
                if error.code not in _RETRYABLE_HTTP_STATUS or attempt + 1 >= self.max_attempts:
                    if error.code == 404:
                        self._cache_put(
                            cache_key,
                            _CachedError(message, error.code),
                        )
                    raise RemoteLookupError(message, status=error.code) from error
                retry_after = error.headers.get("Retry-After") if error.headers else None
                self._retry_delay(attempt, retry_after)
            except (OSError, URLError) as error:
                if attempt + 1 >= self.max_attempts:
                    raise RemoteLookupError(f"Could not reach {url}: {error}") from error
                self._retry_delay(attempt, None)
        raise RemoteLookupError(f"Could not reach {url}.")  # pragma: no cover

    def _throttle(self, url: str) -> None:
        host = urlparse(url).hostname or ""
        interval = _MINIMUM_REQUEST_INTERVAL.get(host, 0.0)
        previous = self._last_request.get(host)
        now = self.clock()
        if previous is not None and interval:
            remaining = interval - (now - previous)
            if remaining > 0:
                self.sleeper(remaining)
        self._last_request[host] = self.clock()

    def _retry_delay(self, attempt: int, retry_after: str | None) -> None:
        try:
            server_delay = float(retry_after) if retry_after else 0.0
        except ValueError:
            server_delay = 0.0
        exponential = 0.5 * (2**attempt) + random.uniform(0, 0.25)  # noqa: S311
        self.sleeper(max(server_delay, exponential))

    def _cache_get(self, key: tuple[str, str, bytes]) -> object | None:
        if key not in self._cache:
            return None
        value = self._cache.pop(key)
        self._cache[key] = value
        return value

    def _cache_put(self, key: tuple[str, str, bytes], value: object) -> None:
        if self.cache_size <= 0:
            return
        self._cache[key] = deepcopy(value)
        self._cache.move_to_end(key)
        while len(self._cache) > self.cache_size:
            self._cache.popitem(last=False)


class MetadataResolver(Protocol):
    """Enrich normalized provider data before final tags are written."""

    def resolve(self, metadata: TrackMetadata, audio_path: Path) -> TrackMetadata: ...


class FingerprintIdentifier(Protocol):
    """Return a MusicBrainz recording ID when an audio fingerprint matches."""

    def identify(self, audio_path: Path) -> str | None: ...


class AcoustIDIdentifier:
    """Generate a Chromaprint fingerprint and query AcoustID without shell execution."""

    def __init__(
        self,
        api_key: str,
        transport: JsonTransport,
        *,
        fpcalc_path: str | None = None,
    ) -> None:
        self.api_key = api_key
        self.transport = transport
        self.fpcalc_path = fpcalc_path

    def identify(self, audio_path: Path) -> str | None:
        fpcalc = self.fpcalc_path or shutil.which("fpcalc")
        if not fpcalc:
            logger.info("AcoustID configured, but fpcalc is unavailable; using text matching.")
            return None
        try:
            completed = subprocess.run(
                [fpcalc, "-json", str(audio_path)],
                check=False,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            logger.warning("Could not fingerprint audio: %s", error)
            return None
        if completed.returncode != 0:
            logger.warning("Chromaprint could not fingerprint the downloaded audio.")
            return None
        try:
            fingerprint = json.loads(completed.stdout)
            duration = int(round(float(fingerprint["duration"])))
            fingerprint_value = str(fingerprint["fingerprint"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            logger.warning("fpcalc returned an invalid fingerprint response.")
            return None
        try:
            response = self.transport.request_json(
                _ACOUSTID_LOOKUP_URL,
                {
                    "client": self.api_key,
                    "duration": duration,
                    "fingerprint": fingerprint_value,
                    "format": "json",
                    "meta": "recordingids",
                },
                method="POST",
            )
        except RemoteLookupError as error:
            logger.warning("AcoustID lookup failed: %s", error)
            return None
        if not isinstance(response, dict):
            return None
        results = response.get("results")
        if not isinstance(results, list):
            return None
        ranked = sorted(
            (result for result in results if isinstance(result, dict)),
            key=lambda item: _number(item.get("score")),
            reverse=True,
        )
        for result in ranked:
            if _number(result.get("score")) < 0.8:
                continue
            recordings = result.get("recordings")
            if not isinstance(recordings, list):
                continue
            for recording in recordings:
                if isinstance(recording, dict) and isinstance(recording.get("id"), str):
                    return cast(str, recording["id"])
        return None


class OnlineMetadataResolver:
    """Resolve canonical metadata without guessing or requiring an LLM."""

    def __init__(
        self,
        *,
        transport: JsonTransport | None = None,
        fingerprinter: FingerprintIdentifier | None = None,
        lyrics_enabled: bool = True,
    ) -> None:
        self.transport = transport or UrlLibJsonTransport()
        self.fingerprinter = fingerprinter
        self.lyrics_enabled = lyrics_enabled

    def resolve(self, metadata: TrackMetadata, audio_path: Path) -> TrackMetadata:
        """Return verified improvements and omit uncertain release-derived fields."""

        enriched = _validated_update(metadata, artwork_url=None, lyrics=None)
        recording = self._find_recording(enriched, audio_path)
        if recording is not None:
            enriched = self._apply_recording(enriched, recording)
        if self.lyrics_enabled and enriched.artist:
            lyrics = self._find_lyrics(enriched)
            if lyrics:
                enriched = _validated_update(enriched, lyrics=lyrics)
        return enriched

    def _find_recording(
        self,
        metadata: TrackMetadata,
        audio_path: Path,
    ) -> dict[str, Any] | None:
        recording_id = self.fingerprinter.identify(audio_path) if self.fingerprinter else None
        try:
            if recording_id:
                response = self.transport.request_json(
                    f"{_MUSICBRAINZ_RECORDING_URL}/{recording_id}",
                    {
                        "fmt": "json",
                        "inc": "artist-credits+releases+release-groups+media",
                    },
                )
                return response if isinstance(response, dict) else None
            if not metadata.artist:
                return None
            query_title = _base_title(metadata.title).replace('"', "")
            query_artist = metadata.artist.replace('"', "")
            response = self.transport.request_json(
                _MUSICBRAINZ_RECORDING_URL,
                {
                    "fmt": "json",
                    "limit": 8,
                    "query": f'recording:"{query_title}" AND artist:"{query_artist}"',
                },
            )
        except RemoteLookupError as error:
            logger.warning("MusicBrainz lookup failed; keeping source metadata: %s", error)
            return None
        if not isinstance(response, dict) or not isinstance(response.get("recordings"), list):
            return None
        candidates = cast(list[object], response["recordings"])
        selected = _choose_recording(metadata, candidates)
        if selected is None:
            return None
        selected = dict(selected)
        selected["_songdrop_related_releases"] = _related_releases(metadata, candidates)
        return selected

    def _apply_recording(
        self,
        metadata: TrackMetadata,
        recording: dict[str, Any],
    ) -> TrackMetadata:
        canonical_title = recording.get("title")
        title = (
            clean_canonical_title(canonical_title)
            if isinstance(canonical_title, str)
            else metadata.title
        )
        artist, featured = _parse_artist_credit(recording.get("artist-credit"))
        final_featured = featured or metadata.featured_artists
        if final_featured:
            title = f"{_base_title(title)} (feat. {', '.join(final_featured)})"

        release_values: list[object] = []
        if isinstance(recording.get("releases"), list):
            release_values.extend(recording["releases"])
        if isinstance(recording.get("_songdrop_related_releases"), list):
            release_values.extend(recording["_songdrop_related_releases"])
        releases = _rank_releases(release_values, title)
        selected_release = releases[0] if releases else None
        artwork_url = (
            self._find_cover_art(cast(str, selected_release["id"])) if selected_release else None
        )

        release_date = _parse_date(selected_release.get("date")) if selected_release else None
        release_year = _parse_year(selected_release.get("date")) if selected_release else None
        album = _text(selected_release.get("title")) if selected_release else metadata.album
        track_number = (
            _recording_track_number(selected_release, cast(str | None, recording.get("id")))
            if selected_release
            else metadata.track_number
        )
        return _validated_update(
            metadata,
            title=title,
            artist=artist or metadata.artist,
            featured_artists=final_featured,
            album=album,
            track_number=track_number,
            release_date=release_date or metadata.release_date,
            release_year=release_year or metadata.release_year,
            artwork_url=artwork_url,
            musicbrainz_recording_id=_text(recording.get("id")),
            musicbrainz_release_id=(
                _text(selected_release.get("id")) if selected_release else None
            ),
        )

    def _find_cover_art(self, release_id: str) -> str | None:
        try:
            response = self.transport.request_json(
                f"{_COVER_ART_RELEASE_URL}/{release_id}",
                {},
            )
        except RemoteLookupError as error:
            if error.status != 404:
                logger.warning("Cover Art Archive lookup failed: %s", error)
            return None
        if not isinstance(response, dict) or not isinstance(response.get("images"), list):
            return None
        images = cast(list[object], response["images"])
        front = next(
            (image for image in images if isinstance(image, dict) and image.get("front") is True),
            None,
        )
        if not isinstance(front, dict):
            return None
        thumbnails = front.get("thumbnails")
        if isinstance(thumbnails, dict):
            for key in ("large", "500", "1200", "250"):
                value = thumbnails.get(key)
                if isinstance(value, str) and value.startswith("https://"):
                    return value
        image_url = front.get("image")
        if isinstance(image_url, str) and image_url.startswith("https://"):
            return image_url
        return None

    def _find_lyrics(self, metadata: TrackMetadata) -> str | None:
        params: dict[str, str | int] = {
            "track_name": _base_title(metadata.title),
            "artist_name": metadata.artist or "",
        }
        if metadata.album:
            params["album_name"] = metadata.album
        if metadata.duration_seconds is not None:
            params["duration"] = round(metadata.duration_seconds)
        try:
            response = self.transport.request_json(_LRCLIB_GET_URL, params)
        except RemoteLookupError as error:
            if error.status != 404:
                logger.warning("Lyrics lookup failed: %s", error)
            return None
        if not isinstance(response, dict):
            return None
        if not _lyrics_match(metadata, response):
            logger.warning("Lyrics response did not match the resolved track and was ignored.")
            return None
        lyrics = response.get("plainLyrics")
        return lyrics.strip() if isinstance(lyrics, str) and lyrics.strip() else None


def build_metadata_resolver(
    *,
    acoustid_api_key: str | None,
    lyrics_enabled: bool,
) -> OnlineMetadataResolver:
    """Compose online metadata clients with optional fingerprint identification."""

    transport = UrlLibJsonTransport()
    fingerprinter = AcoustIDIdentifier(acoustid_api_key, transport) if acoustid_api_key else None
    return OnlineMetadataResolver(
        transport=transport,
        fingerprinter=fingerprinter,
        lyrics_enabled=lyrics_enabled,
    )


def _choose_recording(
    metadata: TrackMetadata,
    candidates: list[object],
) -> dict[str, Any] | None:
    expected_title = _normalize(_base_title(metadata.title))
    expected_artist = _normalize(metadata.artist or "")
    expected_featured = {_normalize(artist) for artist in metadata.featured_artists}
    accepted: list[tuple[float, dict[str, Any]]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        title = _text(candidate.get("title"))
        artist, featured = _parse_artist_credit(candidate.get("artist-credit"))
        if not title or _normalize(title) != expected_title:
            continue
        if expected_artist and _normalize(artist or "") != expected_artist:
            continue
        candidate_featured = {_normalize(value) for value in featured}
        if expected_featured and candidate_featured != expected_featured:
            continue
        source_score = _number(candidate.get("score"))
        if source_score < (80 if expected_featured else 90):
            continue
        duration_score = 0.0
        length_ms = _number(candidate.get("length"))
        if metadata.duration_seconds is not None and length_ms:
            difference = abs(metadata.duration_seconds - length_ms / 1000)
            if difference > max(10, metadata.duration_seconds * 0.08):
                continue
            duration_score = max(0, 10 - difference)
        accepted.append((source_score + duration_score, candidate))
    return max(accepted, key=lambda item: item[0])[1] if accepted else None


def _related_releases(metadata: TrackMetadata, candidates: list[object]) -> list[object]:
    """Collect same-song releases without treating another recording as the audio match."""

    expected_title = _normalize(_base_title(metadata.title))
    expected_artist = _normalize(metadata.artist or "")
    releases: list[object] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        artist, _ = _parse_artist_credit(candidate.get("artist-credit"))
        if _normalize(_text(candidate.get("title")) or "") != expected_title:
            continue
        if expected_artist and _normalize(artist or "") != expected_artist:
            continue
        candidate_releases = candidate.get("releases")
        if isinstance(candidate_releases, list):
            releases.extend(candidate_releases)
    return releases


def _rank_releases(value: object, track_title: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    ranked: list[tuple[int, str, dict[str, Any]]] = []
    for item in value:
        if not isinstance(item, dict) or not _text(item.get("id")):
            continue
        group = item.get("release-group")
        secondary_types = group.get("secondary-types") if isinstance(group, dict) else None
        if isinstance(secondary_types, list) and any(
            isinstance(kind, str) and kind.casefold() == "compilation" for kind in secondary_types
        ):
            continue
        score = 0
        if _text(item.get("status")) == "Official":
            score += 30
        if isinstance(group, dict) and _text(group.get("primary-type")) == "Single":
            score += 40
        if _normalize(_text(item.get("title")) or "") == _normalize(_base_title(track_title)):
            score += 20
        release_date = _text(item.get("date")) or "9999-99-99"
        ranked.append((score, release_date, item))
    ranked.sort(key=lambda entry: (-entry[0], entry[1]))
    return [item for _, _, item in ranked]


def _parse_artist_credit(value: object) -> tuple[str | None, tuple[str, ...]]:
    if not isinstance(value, list):
        return None, ()
    parts = [item for item in value if isinstance(item, dict) and _text(item.get("name"))]
    if not parts:
        return None, ()
    feature_at = next(
        (
            index
            for index, item in enumerate(parts)
            if "feat" in (_text(item.get("joinphrase")) or "").casefold()
        ),
        None,
    )
    if feature_at is not None:
        primary = "".join(
            f"{_text(item.get('name'))}{_text(item.get('joinphrase')) or ''}"
            for item in parts[: feature_at + 1]
        )
        primary = re.sub(r"\s*feat\.?\s*$", "", primary, flags=re.IGNORECASE).strip()
        guests = tuple(cast(str, _text(item.get("name"))) for item in parts[feature_at + 1 :])
        return primary, guests
    rendered = "".join(
        f"{_text(item.get('name'))}{_text(item.get('joinphrase')) or ''}" for item in parts
    ).strip()
    return rendered or None, ()


def _recording_track_number(release: Mapping[str, Any], recording_id: str | None) -> int | None:
    media = release.get("media")
    if not isinstance(media, list):
        return None
    for medium in media:
        if not isinstance(medium, dict) or not isinstance(medium.get("track"), list):
            continue
        for track in medium["track"]:
            if not isinstance(track, dict):
                continue
            nested_recording = track.get("recording")
            if recording_id and (
                not isinstance(nested_recording, dict) or nested_recording.get("id") != recording_id
            ):
                continue
            number = track.get("number")
            if isinstance(number, str) and number.isdigit() and int(number) > 0:
                return int(number)
    return None


def _lyrics_match(metadata: TrackMetadata, response: Mapping[str, Any]) -> bool:
    response_title = _text(response.get("trackName"))
    response_artist = _text(response.get("artistName"))
    if not response_title or _normalize(_base_title(response_title)) != _normalize(
        _base_title(metadata.title)
    ):
        return False
    if response_artist and metadata.artist:
        return _normalize(response_artist).startswith(_normalize(metadata.artist))
    return True


def _validated_update(metadata: TrackMetadata, **updates: object) -> TrackMetadata:
    values = metadata.model_dump()
    values.update(updates)
    return TrackMetadata.model_validate(values)


def _base_title(value: str) -> str:
    return _FEATURE_SUFFIX.sub("", value).strip()


def clean_canonical_title(value: str) -> str:
    """Remove redundant edition labels while retaining meaningful recording variants."""

    cleaned = _REDUNDANT_EDITION_SUFFIX.sub("", value.strip()).strip()
    return cleaned or value.strip()


def _normalize(value: str) -> str:
    return _NORMALIZE_TEXT.sub("", value.casefold())


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _number(value: object) -> float:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0
    return 0


def _parse_date(value: object) -> date | None:
    text = _text(value)
    if not text or len(text) != 10:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _parse_year(value: object) -> int | None:
    text = _text(value)
    if not text or len(text) < 4 or not text[:4].isdigit():
        return None
    year = int(text[:4])
    return year if 1000 <= year <= 9999 else None
