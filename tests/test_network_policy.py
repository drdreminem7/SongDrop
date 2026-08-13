import json
from email.message import Message
from typing import Any
from urllib.error import HTTPError

import pytest

from songdrop.services.enrichment import UrlLibJsonTransport


class FakeResponse:
    def __init__(self, value: object) -> None:
        self.payload = json.dumps(value).encode()

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, limit: int) -> bytes:
        return self.payload[:limit]


def test_transport_caches_within_one_run_and_throttles_uncached_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    now = [0.0]
    sleeps: list[float] = []

    def fake_urlopen(request: Any, *, timeout: float) -> FakeResponse:
        calls.append(request.full_url)
        return FakeResponse({"ok": True})

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    monkeypatch.setattr("songdrop.services.enrichment.urlopen", fake_urlopen)
    transport = UrlLibJsonTransport(clock=lambda: now[0], sleeper=sleep)

    first = transport.request_json(
        "https://musicbrainz.org/ws/2/recording",
        {"query": "one"},
    )
    cached = transport.request_json(
        "https://musicbrainz.org/ws/2/recording",
        {"query": "one"},
    )
    transport.request_json(
        "https://musicbrainz.org/ws/2/recording",
        {"query": "two"},
    )

    assert first == cached == {"ok": True}
    assert len(calls) == 2
    assert sleeps == [1.0]


def test_transport_retries_transient_server_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    now = [0.0]
    sleeps: list[float] = []

    def fake_urlopen(request: Any, *, timeout: float) -> FakeResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise HTTPError(request.full_url, 503, "unavailable", Message(), None)
        return FakeResponse({"recordings": []})

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    monkeypatch.setattr("songdrop.services.enrichment.urlopen", fake_urlopen)
    monkeypatch.setattr("songdrop.services.enrichment.random.uniform", lambda start, end: 0)
    transport = UrlLibJsonTransport(clock=lambda: now[0], sleeper=sleep)

    result = transport.request_json(
        "https://musicbrainz.org/ws/2/recording",
        {"query": "track"},
    )

    assert result == {"recordings": []}
    assert calls == 2
    assert sum(sleeps) >= 1.0
