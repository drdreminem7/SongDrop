from typing import Any

import pytest

from songdrop.exceptions import CollectionLimitExceeded
from songdrop.providers.youtube import YouTubeProvider, is_explicit_playlist_url


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/playlist?list=PL123",
        "https://music.youtube.com/playlist?list=PL123",
        "https://www.youtube.com/watch?v=abc123&list=PL123",
    ],
)
def test_recognizes_youtube_playlist_urls(url: str) -> None:
    assert YouTubeProvider().supports_collection(url)


def test_only_playlist_path_is_automatic_collection_workflow() -> None:
    assert is_explicit_playlist_url("https://www.youtube.com/playlist?list=PL123")
    assert not is_explicit_playlist_url("https://www.youtube.com/watch?v=abc123&list=PL123")


def test_flat_playlist_discovery_preserves_order_and_unicode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_options: list[dict[str, Any]] = []

    class FakeYDL:
        def __init__(self, options: dict[str, Any]) -> None:
            captured_options.append(options)

        def __enter__(self) -> "FakeYDL":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def extract_info(self, url: str, *, download: bool) -> dict[str, Any]:
            assert download is False
            return {
                "id": "PL123",
                "title": "Летни песни",
                "entries": [
                    {"id": "abc12345678", "title": "Песен едно"},
                    {
                        "id": "xyz12345678",
                        "title": "Song Two",
                        "url": "https://music.youtube.com/watch?v=xyz12345678",
                    },
                ],
            }

    monkeypatch.setattr("yt_dlp.YoutubeDL", FakeYDL)
    monkeypatch.setattr(YouTubeProvider, "_base_options", staticmethod(lambda: {}))

    result = YouTubeProvider().get_collection(
        "https://music.youtube.com/playlist?list=PL123",
        max_items=10,
    )

    assert result.title == "Летни песни"
    assert [item.source_id for item in result.items] == ["abc12345678", "xyz12345678"]
    assert result.items[0].url == "https://www.youtube.com/watch?v=abc12345678"
    assert result.items[1].title == "Song Two"
    assert captured_options[0]["extract_flat"] == "in_playlist"
    assert captured_options[0]["playlistend"] == 11
    assert captured_options[0]["skip_download"] is True


def test_playlist_limit_uses_one_extra_entry_to_detect_overflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeYDL:
        def __init__(self, options: dict[str, Any]) -> None:
            self.options = options

        def __enter__(self) -> "FakeYDL":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def extract_info(self, url: str, *, download: bool) -> dict[str, Any]:
            return {"entries": [{"id": "one00000001"}, {"id": "two00000002"}]}

    monkeypatch.setattr("yt_dlp.YoutubeDL", FakeYDL)
    monkeypatch.setattr(YouTubeProvider, "_base_options", staticmethod(lambda: {}))

    with pytest.raises(CollectionLimitExceeded, match="--max-items"):
        YouTubeProvider().get_collection(
            "https://www.youtube.com/playlist?list=PL123",
            max_items=1,
        )
