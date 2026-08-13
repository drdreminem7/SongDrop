import stat
import time
from pathlib import Path

from fastapi.testclient import TestClient

from songdrop.api import TokenStore, create_app
from songdrop.api_models import JobRequest
from songdrop.services.jobs import ExecutionResult, JobManager

_ORIGIN = "chrome-extension://abcdefghijklmnopabcdefghijklmnop"


def _wait_for_status(client: TestClient, job_id: str, token: str) -> dict[str, object]:
    headers = {"Authorization": f"Bearer {token}", "Origin": _ORIGIN}
    for _ in range(100):
        response = client.get(f"/v1/jobs/{job_id}", headers=headers)
        payload = response.json()
        if payload["status"] in {"completed", "failed", "cancelled"}:
            return payload
        time.sleep(0.005)
    raise AssertionError("job did not complete")


def test_health_connect_submit_and_poll_job() -> None:
    captured: list[JobRequest] = []

    def executor(request: JobRequest, update: object) -> ExecutionResult:
        captured.append(request)
        return ExecutionResult(imported=1, result_path=Path("/Music/Track.mp3"))

    manager = JobManager(executor)
    app = create_app(manager=manager, token="secret-token", allowed_origin=_ORIGIN)

    with TestClient(app) as client:
        health = client.get("/v1/health")
        assert health.status_code == 200
        assert health.json()["service"] == "songdrop"
        assert "secret-token" not in health.text

        connected = client.post(
            "/v1/session",
            headers={"Origin": _ORIGIN},
        )
        assert connected.status_code == 200
        assert connected.json() == {"token": "secret-token"}

        submitted = client.post(
            "/v1/jobs",
            headers={"Authorization": "Bearer secret-token", "Origin": _ORIGIN},
            json={
                "url": "https://music.youtube.com/watch?v=abc12345678",
                "destination": "apple_music",
                "audio_format": "mp3",
            },
        )
        assert submitted.status_code == 202
        payload = _wait_for_status(client, submitted.json()["job_id"], "secret-token")

    assert payload["status"] == "completed"
    assert payload["imported"] == 1
    assert captured[0].destination.value == "apple_music"


def test_api_rejects_disallowed_unauthorized_and_unsupported_requests() -> None:
    manager = JobManager(lambda request, update: ExecutionResult())
    app = create_app(manager=manager, token="secret-token", allowed_origin=_ORIGIN)
    with TestClient(app) as client:
        missing_origin = client.post("/v1/session")
        assert missing_origin.status_code == 403

        denied_origin = client.post(
            "/v1/session",
            headers={"Origin": "https://malicious.example"},
        )
        assert denied_origin.status_code == 403

        wrong_extension = client.post(
            "/v1/session",
            headers={"Origin": "chrome-extension://bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"},
        )
        assert wrong_extension.status_code == 403

        missing_token = client.post(
            "/v1/jobs",
            headers={"Origin": _ORIGIN},
            json={"url": "https://www.youtube.com/watch?v=abc12345678"},
        )
        assert missing_token.status_code == 401

        unsupported = client.post(
            "/v1/jobs",
            headers={"Authorization": "Bearer secret-token", "Origin": _ORIGIN},
            json={"url": "https://example.com/not-media"},
        )
        assert unsupported.status_code == 422


def test_api_allows_extension_cors_preflight_only() -> None:
    manager = JobManager(lambda request, update: ExecutionResult())
    app = create_app(manager=manager, token="secret-token", allowed_origin=_ORIGIN)
    with TestClient(app) as client:
        allowed = client.options(
            "/v1/jobs",
            headers={
                "Origin": _ORIGIN,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        denied = client.options(
            "/v1/jobs",
            headers={
                "Origin": "https://malicious.example",
                "Access-Control-Request-Method": "POST",
            },
        )

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == _ORIGIN
    assert denied.status_code == 400


def test_token_store_creates_private_stable_credential(tmp_path: Path) -> None:
    path = tmp_path / "Application Support" / "SongDrop" / "api-token"
    store = TokenStore(path)

    first = store.load_or_create()
    second = store.load_or_create()

    assert first == second
    assert len(first) >= 32
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
