import pytest

from songdrop.exceptions import UnsupportedURL
from songdrop.providers import select_provider
from songdrop.providers.youtube import YouTubeProvider, _detect_js_runtimes


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=abc123",
        "https://youtube.com/watch?v=abc123&list=something",
        "https://m.youtube.com/watch?v=abc123",
        "https://youtu.be/abc123",
        "https://www.youtube.com/shorts/abc123",
        "https://www.youtube.com/live/abc123",
    ],
)
def test_recognizes_youtube_video_urls(url: str) -> None:
    assert YouTubeProvider().supports(url)


def test_recognizes_youtube_music_url() -> None:
    assert YouTubeProvider().supports("https://music.youtube.com/watch?v=abc123")


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/watch?v=abc123",
        "https://www.youtube.com/",
        "https://www.youtube.com/channel/abc123",
        "javascript:alert(1)",
        "not a url",
    ],
)
def test_rejects_unsupported_urls(url: str) -> None:
    assert not YouTubeProvider().supports(url)


def test_provider_selection_raises_readable_error() -> None:
    with pytest.raises(UnsupportedURL, match="YouTube and YouTube Music"):
        select_provider("https://example.com/audio", (YouTubeProvider(),))


def test_detects_node_runtime_when_deno_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "songdrop.providers.youtube.shutil.which",
        lambda name: "/usr/local/bin/node" if name == "node" else None,
    )
    monkeypatch.setattr(
        "songdrop.providers.youtube._runtime_is_supported",
        lambda name, path: True,
    )
    assert _detect_js_runtimes() == {
        "node": {"path": "/usr/local/bin/node"},
    }


def test_missing_js_runtime_is_a_clear_dependency_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from songdrop.exceptions import MissingDependency

    monkeypatch.setattr("songdrop.providers.youtube.shutil.which", lambda name: None)
    with pytest.raises(MissingDependency, match=r"Deno 2.3\+ or Node.js 22\+"):
        _detect_js_runtimes()
