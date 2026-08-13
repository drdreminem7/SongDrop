from pathlib import Path
from typing import Any

from songdrop.models import TrackMetadata
from songdrop.services.enrichment import (
    AcoustIDIdentifier,
    OnlineMetadataResolver,
    RemoteLookupError,
    clean_canonical_title,
)


class FakeTransport:
    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.requests: list[tuple[str, dict[str, str | int], str]] = []

    def request_json(
        self,
        url: str,
        params: dict[str, str | int],
        *,
        method: str = "GET",
    ) -> object:
        self.requests.append((url, params, method))
        response = self.responses.get(url)
        if isinstance(response, Exception):
            raise response
        if response is None:
            raise RemoteLookupError("not found", status=404)
        return response


def source_metadata() -> TrackMetadata:
    return TrackMetadata(
        title="In Your Eyes (feat. Yandel)",
        artist="INNA",
        featured_artists=("Yandel",),
        duration_seconds=205,
        source="youtube",
        source_url="https://music.youtube.com/watch?v=Od-6uzcLGqw",
        source_id="Od-6uzcLGqw",
        thumbnail_url="https://i.ytimg.com/video-thumbnail.jpg",
    )


def musicbrainz_response() -> dict[str, Any]:
    return {
        "recordings": [
            {
                "id": "recording-id",
                "score": 100,
                "title": "In Your Eyes",
                "length": 204500,
                "artist-credit": [
                    {"name": "INNA", "joinphrase": " feat. "},
                    {"name": "Yandel", "joinphrase": ""},
                ],
                "releases": [
                    {
                        "id": "release-id",
                        "title": "In Your Eyes",
                        "status": "Official",
                        "date": "2013-12-03",
                        "release-group": {"primary-type": "Single"},
                        "media": [
                            {
                                "track": [
                                    {
                                        "number": "1",
                                        "recording": {"id": "recording-id"},
                                    }
                                ]
                            }
                        ],
                    }
                ],
            }
        ]
    }


def test_resolves_canonical_metadata_release_artwork_and_lyrics(tmp_path: Path) -> None:
    transport = FakeTransport(
        {
            "https://musicbrainz.org/ws/2/recording": musicbrainz_response(),
            "https://coverartarchive.org/release/release-id": {
                "images": [
                    {
                        "front": True,
                        "thumbnails": {"large": "https://archive.org/real-single-cover.jpg"},
                    }
                ]
            },
            "https://lrclib.net/api/get": {
                "trackName": "In Your Eyes",
                "artistName": "INNA",
                "plainLyrics": "First line\nSecond line",
            },
        }
    )
    audio = tmp_path / "track.m4a"
    audio.write_bytes(b"audio")

    result = OnlineMetadataResolver(transport=transport).resolve(source_metadata(), audio)

    assert result.title == "In Your Eyes (feat. Yandel)"
    assert result.artist == "INNA"
    assert result.featured_artists == ("Yandel",)
    assert result.album == "In Your Eyes"
    assert result.track_number == 1
    assert result.release_date is not None
    assert result.release_date.isoformat() == "2013-12-03"
    assert result.release_year == 2013
    assert str(result.artwork_url) == "https://archive.org/real-single-cover.jpg"
    assert str(result.thumbnail_url) == "https://i.ytimg.com/video-thumbnail.jpg"
    assert result.lyrics == "First line\nSecond line"
    assert result.musicbrainz_recording_id == "recording-id"
    assert result.musicbrainz_release_id == "release-id"


def test_low_confidence_match_does_not_overwrite_source_metadata(tmp_path: Path) -> None:
    response = musicbrainz_response()
    response["recordings"][0]["score"] = 70
    transport = FakeTransport(
        {
            "https://musicbrainz.org/ws/2/recording": response,
            "https://lrclib.net/api/get": RemoteLookupError("not found", status=404),
        }
    )

    result = OnlineMetadataResolver(transport=transport).resolve(
        source_metadata(), tmp_path / "audio.m4a"
    )

    assert result.title == "In Your Eyes (feat. Yandel)"
    assert result.artist == "INNA"
    assert result.album is None
    assert result.artwork_url is None
    assert result.musicbrainz_recording_id is None


def test_mismatched_lyrics_are_rejected(tmp_path: Path) -> None:
    transport = FakeTransport(
        {
            "https://musicbrainz.org/ws/2/recording": RemoteLookupError("unavailable", status=503),
            "https://lrclib.net/api/get": {
                "trackName": "A Different Song",
                "artistName": "Someone Else",
                "plainLyrics": "Wrong lyrics",
            },
        }
    )

    result = OnlineMetadataResolver(transport=transport).resolve(
        source_metadata(), tmp_path / "audio.m4a"
    )
    assert result.lyrics is None
    assert result.artwork_url is None


def test_musicbrainz_query_uses_clean_title_and_primary_artist(tmp_path: Path) -> None:
    transport = FakeTransport(
        {
            "https://musicbrainz.org/ws/2/recording": {"recordings": []},
            "https://lrclib.net/api/get": RemoteLookupError("not found", status=404),
        }
    )
    OnlineMetadataResolver(transport=transport).resolve(source_metadata(), tmp_path / "audio.m4a")

    _, params, _ = transport.requests[0]
    assert params["query"] == 'recording:"In Your Eyes" AND artist:"INNA"'


def test_prefers_exact_single_and_never_uses_compilation_just_for_artwork(
    tmp_path: Path,
) -> None:
    response = musicbrainz_response()
    matched = response["recordings"][0]
    matched["releases"] = [
        {
            "id": "compilation-id",
            "title": "Greatest Hits Collection",
            "status": "Official",
            "date": "2018-01-01",
            "release-group": {
                "primary-type": "Album",
                "secondary-types": ["Compilation"],
            },
        },
        {
            "id": "single-id",
            "title": "In Your Eyes",
            "status": "Official",
            "date": "2013-12-03",
            "release-group": {"primary-type": "Single", "secondary-types": []},
        },
    ]
    transport = FakeTransport(
        {
            "https://musicbrainz.org/ws/2/recording": response,
            "https://coverartarchive.org/release/single-id": RemoteLookupError(
                "not found", status=404
            ),
            "https://lrclib.net/api/get": RemoteLookupError("not found", status=404),
        }
    )

    result = OnlineMetadataResolver(transport=transport).resolve(
        source_metadata(), tmp_path / "audio.m4a"
    )

    assert result.album == "In Your Eyes"
    assert result.musicbrainz_release_id == "single-id"
    assert result.artwork_url is None
    assert not any("compilation-id" in request[0] for request in transport.requests)


def test_partial_release_date_sets_year_without_inventing_a_day(tmp_path: Path) -> None:
    response = musicbrainz_response()
    response["recordings"][0]["releases"][0]["date"] = "2013"
    transport = FakeTransport(
        {
            "https://musicbrainz.org/ws/2/recording": response,
            "https://coverartarchive.org/release/release-id": RemoteLookupError(
                "not found", status=404
            ),
            "https://lrclib.net/api/get": RemoteLookupError("not found", status=404),
        }
    )

    result = OnlineMetadataResolver(transport=transport).resolve(
        source_metadata(), tmp_path / "audio.m4a"
    )
    assert result.release_date is None
    assert result.release_year == 2013


def test_acoustid_uses_fpcalc_and_returns_high_confidence_recording_id(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    class Completed:
        returncode = 0
        stdout = '{"duration": 195.2, "fingerprint": "fingerprint-data"}'
        stderr = ""

    monkeypatch.setattr("songdrop.services.enrichment.shutil.which", lambda name: "/bin/fpcalc")
    monkeypatch.setattr(
        "songdrop.services.enrichment.subprocess.run",
        lambda *args, **kwargs: Completed(),
    )
    transport = FakeTransport(
        {
            "https://api.acoustid.org/v2/lookup": {
                "results": [
                    {
                        "score": 0.98,
                        "recordings": [{"id": "fingerprinted-recording-id"}],
                    }
                ]
            }
        }
    )

    result = AcoustIDIdentifier("api-key", transport).identify(tmp_path / "track.m4a")

    assert result == "fingerprinted-recording-id"
    _, params, method = transport.requests[0]
    assert method == "POST"
    assert params["duration"] == 195
    assert params["fingerprint"] == "fingerprint-data"


def test_removes_redundant_canonical_edition_labels() -> None:
    assert clean_canonical_title("Stereo Love (original version)") == "Stereo Love"
    assert clean_canonical_title("Toca Toca (radio edit)") == "Toca Toca"
    assert clean_canonical_title("Déjà vu (Play & Win Radio Edit)") == "Déjà vu"


def test_preserves_meaningful_canonical_version_labels() -> None:
    for title in (
        "Song (Live)",
        "Song (Acoustic)",
        "Song (Extended Remix)",
        "Song (Instrumental)",
        "Song (Remastered 2012)",
    ):
        assert clean_canonical_title(title) == title


def test_cleaned_edition_title_retains_feature_credit(tmp_path: Path) -> None:
    response = musicbrainz_response()["recordings"][0]
    response["title"] = "In Your Eyes (Play & Win Radio Edit)"

    class Fingerprinter:
        def identify(self, audio_path: Path) -> str | None:
            return "recording-id"

    transport = FakeTransport(
        {
            "https://musicbrainz.org/ws/2/recording/recording-id": response,
            "https://coverartarchive.org/release/release-id": RemoteLookupError(
                "not found", status=404
            ),
            "https://lrclib.net/api/get": RemoteLookupError("not found", status=404),
        }
    )

    result = OnlineMetadataResolver(
        transport=transport,
        fingerprinter=Fingerprinter(),
    ).resolve(source_metadata(), tmp_path / "audio.mp3")

    assert result.title == "In Your Eyes (feat. Yandel)"
