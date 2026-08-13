"""Authenticated localhost API used by trusted SongDrop browser extensions."""

import logging
import os
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware

from songdrop import __version__
from songdrop.api_models import (
    HealthResult,
    JobAccepted,
    JobRequest,
    JobView,
    SessionResult,
)
from songdrop.services.jobs import JobManager, JobQueueFull, validate_job_url

_EXTENSION_ID = "golnlibblfmclfpbmibdgkpmejmhhofg"
_EXTENSION_ORIGIN = f"chrome-extension://{_EXTENSION_ID}"


def default_token_path() -> Path:
    """Return the user-private operational credential location on macOS."""

    return Path.home() / "Library" / "Application Support" / "SongDrop" / "api-token"


class TokenStore:
    """Load or create one user-private bearer token with restrictive permissions."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = (path or default_token_path()).expanduser().resolve(strict=False)

    def load_or_create(self) -> str:
        try:
            existing = self.path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            existing = ""
        except OSError as error:
            raise RuntimeError(f"Could not read local API token: {error}") from error
        if existing:
            return existing
        token = secrets.token_urlsafe(32)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as token_file:
                token_file.write(token + "\n")
        except FileExistsError:
            return self.path.read_text(encoding="utf-8").strip()
        except OSError as error:
            raise RuntimeError(f"Could not create local API token: {error}") from error
        return token


def create_app(
    *,
    manager: JobManager | None = None,
    token: str | None = None,
    allowed_origin: str = _EXTENSION_ORIGIN,
) -> FastAPI:
    """Build the API with injectable operational state for tests."""

    job_manager = manager or JobManager()
    session_token = token or TokenStore().load_or_create()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        del app
        job_manager.start()
        try:
            yield
        finally:
            job_manager.stop()

    api = FastAPI(
        title="SongDrop Local API",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    api.add_middleware(
        CORSMiddleware,
        allow_origins=[allowed_origin],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
        max_age=600,
    )

    def require_extension_origin(request: Request) -> None:
        origin = request.headers.get("origin")
        if origin != allowed_origin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This request did not come from the SongDrop extension",
            )

    def require_token(
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> None:
        require_extension_origin(request)
        scheme, _, credential = (authorization or "").partition(" ")
        if scheme.casefold() != "bearer" or not secrets.compare_digest(
            credential,
            session_token,
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="SongDrop extension authorization expired",
            )

    @api.get("/v1/health", response_model=HealthResult)
    def health() -> HealthResult:
        return HealthResult(version=__version__)

    @api.post("/v1/session", response_model=SessionResult)
    def session(request: Request) -> SessionResult:
        """Authorize the known extension origin without a manual pairing step."""

        require_extension_origin(request)
        return SessionResult(token=session_token)

    @api.post(
        "/v1/jobs",
        response_model=JobAccepted,
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(require_token)],
    )
    def submit_job(payload: JobRequest) -> JobAccepted:
        cleaned = payload.url.strip()
        if not validate_job_url(cleaned):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Unsupported YouTube or YouTube Music URL",
            )
        try:
            snapshot = job_manager.submit(payload.model_copy(update={"url": cleaned}))
        except JobQueueFull as error:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="SongDrop's local queue is full; wait for a job to finish",
            ) from error
        return JobAccepted(job_id=snapshot.id, status=snapshot.status)

    @api.get(
        "/v1/jobs/{job_id}",
        response_model=JobView,
        dependencies=[Depends(require_token)],
    )
    def get_job(job_id: str) -> JobView:
        try:
            return job_manager.get(job_id)
        except KeyError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Job not found"
            ) from error

    @api.post(
        "/v1/jobs/{job_id}/cancel",
        response_model=JobView,
        dependencies=[Depends(require_token)],
    )
    def cancel_job(job_id: str) -> JobView:
        try:
            snapshot = job_manager.cancel(job_id)
        except KeyError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Job not found"
            ) from error
        if snapshot.status.value == "running":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A running media operation cannot be interrupted safely",
            )
        return snapshot

    return api


def serve(*, port: int = 8765, verbose: bool = False) -> None:
    """Run the extension API on the loopback interface only."""

    import uvicorn

    logging.getLogger("songdrop.services.jobs").setLevel(logging.INFO)
    token = TokenStore().load_or_create()
    app = create_app(token=token)
    print("SongDrop is ready for the browser extension.", flush=True)
    print(f"Listening on http://127.0.0.1:{port}", flush=True)
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=port,
        log_level="debug" if verbose else "warning",
        access_log=verbose,
    )
