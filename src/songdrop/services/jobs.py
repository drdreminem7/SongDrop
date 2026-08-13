"""Serialized, memory-only execution for localhost API requests."""

import logging
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from songdrop.api_models import (
    JobDestination,
    JobProgress,
    JobRequest,
    JobStatus,
    JobView,
    utc_now,
)
from songdrop.config import load_config
from songdrop.exceptions import SongDropError
from songdrop.models import BatchItemResult, BatchOptions
from songdrop.providers import default_collection_providers, default_providers
from songdrop.providers.youtube import is_explicit_playlist_url
from songdrop.services.batch import BatchDownloadService
from songdrop.services.downloader import build_download_service

logger = logging.getLogger(__name__)
_STOP = object()


class JobQueueFull(RuntimeError):
    """Raised when the local client submits more work than can be retained safely."""


class JobExecutor(Protocol):
    """Execute one job and report observable progress to the manager."""

    def __call__(
        self,
        request: JobRequest,
        update: Callable[[JobProgress], None],
    ) -> "ExecutionResult": ...


@dataclass(frozen=True)
class ExecutionResult:
    """Compact execution outcome retained only for status presentation."""

    imported: int = 0
    saved: int = 0
    skipped: int = 0
    failed: int = 0
    result_path: Path | None = None
    message: str | None = None
    preserved_path: Path | None = None


@dataclass
class _Job:
    request: JobRequest
    status: JobStatus = JobStatus.QUEUED
    progress: JobProgress = field(default_factory=lambda: JobProgress(phase="queued"))
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    result: ExecutionResult = field(default_factory=ExecutionResult)
    cancel_requested: bool = False


class JobManager:
    """Own one worker so FFmpeg, metadata services, and Music imports remain serialized."""

    def __init__(
        self,
        executor: JobExecutor | None = None,
        *,
        max_pending: int = 20,
        retention: timedelta = timedelta(hours=1),
    ) -> None:
        self._executor = executor or execute_job
        self._max_pending = max_pending
        self._retention = retention
        self._jobs: dict[str, _Job] = {}
        self._queue: queue.Queue[str | object] = queue.Queue()
        self._lock = threading.RLock()
        self._worker: threading.Thread | None = None

    def start(self) -> None:
        """Start the single worker once."""

        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return
            self._worker = threading.Thread(
                target=self._run,
                name="songdrop-job-worker",
                daemon=False,
            )
            self._worker.start()

    def stop(self, *, timeout: float | None = None) -> None:
        """Cancel queued jobs and stop after the active media operation finishes safely."""

        worker = self._worker
        if worker is None:
            return
        with self._lock:
            for job in self._jobs.values():
                if job.status is JobStatus.QUEUED:
                    job.cancel_requested = True
                    job.status = JobStatus.CANCELLED
                    job.progress = JobProgress(phase="cancelled")
                    job.updated_at = utc_now()
        self._queue.put(_STOP)
        worker.join(timeout=timeout)
        if not worker.is_alive():
            self._worker = None

    def submit(self, request: JobRequest) -> JobView:
        """Queue one validated job and return its initial snapshot."""

        self.start()
        job_id = uuid4().hex
        with self._lock:
            self._prune_finished_locked()
            pending = sum(
                job.status in {JobStatus.QUEUED, JobStatus.RUNNING} for job in self._jobs.values()
            )
            if pending >= self._max_pending:
                raise JobQueueFull("Too many SongDrop jobs are already queued")
            self._jobs[job_id] = _Job(request=request)
        self._queue.put(job_id)
        logger.info(
            "Queued %s job %s",
            request.destination.value.replace("_", " "),
            job_id[:8],
        )
        return self.get(job_id)

    def get(self, job_id: str) -> JobView:
        """Return a safe immutable snapshot or raise KeyError."""

        with self._lock:
            self._prune_finished_locked()
            job = self._jobs[job_id]
            return _snapshot(job_id, job)

    def cancel(self, job_id: str) -> JobView:
        """Cancel a queued job; running media operations finish safely."""

        with self._lock:
            job = self._jobs[job_id]
            if job.status is JobStatus.QUEUED:
                job.cancel_requested = True
                job.status = JobStatus.CANCELLED
                job.progress = JobProgress(phase="cancelled")
                job.updated_at = utc_now()
            return _snapshot(job_id, job)

    def _prune_finished_locked(self) -> None:
        """Forget old terminal jobs; SongDrop keeps no persistent activity history."""

        cutoff = utc_now() - self._retention
        expired = [
            job_id
            for job_id, job in self._jobs.items()
            if job.status in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}
            and job.updated_at < cutoff
        ]
        for job_id in expired:
            del self._jobs[job_id]

    def _run(self) -> None:
        while True:
            job_id = self._queue.get()
            try:
                if job_id is _STOP:
                    return
                assert isinstance(job_id, str)
                self._execute(job_id)
            finally:
                self._queue.task_done()

    def _execute(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            if job.cancel_requested:
                return
            job.status = JobStatus.RUNNING
            job.progress = JobProgress(phase="inspecting")
            job.updated_at = utc_now()
        logger.info("Started job %s", job_id[:8])

        def update(progress: JobProgress) -> None:
            with self._lock:
                current = self._jobs[job_id]
                current.progress = progress
                current.updated_at = utc_now()

        try:
            result = self._executor(job.request, update)
        except SongDropError as error:
            result = ExecutionResult(
                failed=1,
                message=str(error),
                preserved_path=error.preserved_path,
            )
            status = JobStatus.FAILED
        except Exception:
            logger.exception("Unexpected local API job failure")
            result = ExecutionResult(failed=1, message="An unexpected error interrupted the job.")
            status = JobStatus.FAILED
        else:
            status = JobStatus.FAILED if result.failed else JobStatus.COMPLETED

        with self._lock:
            job = self._jobs[job_id]
            job.result = result
            job.status = status
            job.progress = JobProgress(phase=status.value)
            job.updated_at = utc_now()
        logger.info(
            "%s job %s (imported=%d, saved=%d, skipped=%d, failed=%d)",
            status.value.capitalize(),
            job_id[:8],
            result.imported,
            result.saved,
            result.skipped,
            result.failed,
        )


def execute_job(
    request: JobRequest,
    update: Callable[[JobProgress], None],
) -> ExecutionResult:
    """Adapt one API request to SongDrop's existing single/collection services."""

    config = load_config(
        audio_format=request.audio_format,
        download_only=request.destination is JobDestination.FILESYSTEM,
    )
    track_service = build_download_service(config)
    if is_explicit_playlist_url(request.url):
        batch_service = BatchDownloadService(
            track_service,
            media_providers=track_service.providers,
            collection_providers=default_collection_providers(),
            retry_root=config.staging_dir,
        )

        def progress(current: int, total: int, item: BatchItemResult) -> None:
            label = item.result.metadata.title if item.result else item.request.title
            update(
                JobProgress(
                    phase="processing",
                    current=current,
                    total=total,
                    label=label,
                )
            )

        batch_result = batch_service.import_collection(
            request.url,
            BatchOptions(max_items=config.max_batch_items),
            progress=progress,
        )
        successes = batch_result.imported_count
        return ExecutionResult(
            imported=successes if request.destination is JobDestination.APPLE_MUSIC else 0,
            saved=successes if request.destination is JobDestination.FILESYSTEM else 0,
            skipped=batch_result.skipped_count,
            failed=batch_result.failed_count,
            message=("Some playlist items failed." if batch_result.failed_count else None),
            preserved_path=batch_result.retry_file,
        )

    update(JobProgress(phase="processing", current=0, total=1))
    track_result = track_service.import_url(request.url)
    update(JobProgress(phase="processing", current=1, total=1, label=track_result.metadata.title))
    if track_result.already_downloaded:
        return ExecutionResult(
            skipped=1,
            result_path=track_result.path,
            message="Already downloaded",
        )
    return ExecutionResult(
        imported=1 if request.destination is JobDestination.APPLE_MUSIC else 0,
        saved=1 if request.destination is JobDestination.FILESYSTEM else 0,
        result_path=track_result.path,
    )


def validate_job_url(url: str) -> bool:
    """Accept only URLs understood by current track or collection providers."""

    cleaned = url.strip()
    if is_explicit_playlist_url(cleaned):
        return any(
            provider.supports_collection(cleaned) for provider in default_collection_providers()
        )
    return any(provider.supports(cleaned) for provider in default_providers())


def _snapshot(job_id: str, job: _Job) -> JobView:
    return JobView(
        id=job_id,
        status=job.status,
        destination=job.request.destination,
        audio_format=job.request.audio_format,
        progress=job.progress,
        created_at=job.created_at,
        updated_at=job.updated_at,
        imported=job.result.imported,
        saved=job.result.saved,
        skipped=job.result.skipped,
        failed=job.result.failed,
        result_path=job.result.result_path,
        message=job.result.message,
        preserved_path=job.result.preserved_path,
    )
