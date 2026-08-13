import logging
import threading
import time
from datetime import timedelta

import pytest

from songdrop.api_models import JobRequest, JobStatus
from songdrop.services.jobs import ExecutionResult, JobManager, JobQueueFull


def _wait(manager: JobManager, job_id: str, status: JobStatus) -> None:
    for _ in range(200):
        if manager.get(job_id).status is status:
            return
        time.sleep(0.005)
    raise AssertionError(f"job did not reach {status}")


def test_jobs_are_executed_serially() -> None:
    release_first = threading.Event()
    first_started = threading.Event()
    order: list[str] = []
    active = 0
    maximum_active = 0
    lock = threading.Lock()

    def executor(request: JobRequest, update: object) -> ExecutionResult:
        nonlocal active, maximum_active
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
            order.append(request.url)
        if request.url.endswith("first"):
            first_started.set()
            release_first.wait(timeout=2)
        with lock:
            active -= 1
        return ExecutionResult(imported=1)

    manager = JobManager(executor)
    first = manager.submit(JobRequest(url="https://www.youtube.com/watch?v=first"))
    second = manager.submit(JobRequest(url="https://www.youtube.com/watch?v=second"))
    assert first_started.wait(timeout=1)
    assert manager.get(second.id).status is JobStatus.QUEUED
    release_first.set()
    _wait(manager, first.id, JobStatus.COMPLETED)
    _wait(manager, second.id, JobStatus.COMPLETED)
    manager.stop()

    assert maximum_active == 1
    assert order == [
        "https://www.youtube.com/watch?v=first",
        "https://www.youtube.com/watch?v=second",
    ]


def test_queued_job_can_be_cancelled_without_execution() -> None:
    release = threading.Event()
    started = threading.Event()
    executed: list[str] = []

    def executor(request: JobRequest, update: object) -> ExecutionResult:
        executed.append(request.url)
        if len(executed) == 1:
            started.set()
            release.wait(timeout=2)
        return ExecutionResult(imported=1)

    manager = JobManager(executor)
    first = manager.submit(JobRequest(url="https://www.youtube.com/watch?v=first"))
    second = manager.submit(JobRequest(url="https://www.youtube.com/watch?v=second"))
    assert started.wait(timeout=1)

    cancelled = manager.cancel(second.id)
    release.set()
    _wait(manager, first.id, JobStatus.COMPLETED)
    time.sleep(0.02)
    manager.stop()

    assert cancelled.status is JobStatus.CANCELLED
    assert executed == ["https://www.youtube.com/watch?v=first"]


def test_queue_rejects_excess_pending_work() -> None:
    release = threading.Event()

    def executor(request: JobRequest, update: object) -> ExecutionResult:
        release.wait(timeout=2)
        return ExecutionResult(imported=1)

    manager = JobManager(executor, max_pending=1, retention=timedelta(seconds=1))
    manager.submit(JobRequest(url="https://www.youtube.com/watch?v=first"))
    with pytest.raises(JobQueueFull):
        manager.submit(JobRequest(url="https://www.youtube.com/watch?v=second"))
    release.set()
    manager.stop()


def test_job_lifecycle_is_logged_for_local_service_feedback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="songdrop.services.jobs")
    manager = JobManager(lambda request, update: ExecutionResult(imported=1))

    job = manager.submit(JobRequest(url="https://www.youtube.com/watch?v=visible"))
    _wait(manager, job.id, JobStatus.COMPLETED)
    manager.stop()

    messages = [record.getMessage() for record in caplog.records]
    assert any(message.startswith("Queued apple music job") for message in messages)
    assert any(message.startswith("Started job") for message in messages)
    assert any(message.startswith("Completed job") for message in messages)
