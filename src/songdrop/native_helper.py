"""Restricted Chromium native-messaging helper for starting SongDrop locally."""

import fcntl
import json
import os
import shutil
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO, TypedDict

from songdrop.browser import (
    API_BASE_URL,
    EXTENSION_ORIGIN,
    NATIVE_HOST_NAME,
    brave_native_host_manifest_paths,
    extension_origin_variants,
)
from songdrop.exceptions import BrowserHelperFailed

_MAX_MESSAGE_BYTES = 1024 * 1024
_START_TIMEOUT_SECONDS = 12.0


class StartResult(TypedDict):
    """Result returned to the browser after ensuring the API is available."""

    ok: bool
    started: bool


def _application_support_directory() -> Path:
    return Path.home() / "Library" / "Application Support" / "SongDrop"


def _service_log_path() -> Path:
    return Path.home() / "Library" / "Logs" / "SongDrop" / "service.log"


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = stream.read(size - len(chunks))
        if not chunk:
            raise BrowserHelperFailed("The browser sent an incomplete native message.")
        chunks.extend(chunk)
    return bytes(chunks)


def read_native_message(stream: BinaryIO) -> dict[str, object] | None:
    """Read one Chromium native-messaging frame."""

    header = stream.read(4)
    if not header:
        return None
    if len(header) != 4:
        raise BrowserHelperFailed("The browser sent an invalid native message header.")
    (message_size,) = struct.unpack("=I", header)
    if message_size > _MAX_MESSAGE_BYTES:
        raise BrowserHelperFailed("The browser native message was unexpectedly large.")
    try:
        decoded = json.loads(_read_exact(stream, message_size).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BrowserHelperFailed("The browser sent invalid native message JSON.") from error
    if not isinstance(decoded, dict):
        raise BrowserHelperFailed("The browser native message must be a JSON object.")
    return decoded


def write_native_message(stream: BinaryIO, payload: dict[str, object]) -> None:
    """Write one Chromium native-messaging frame without polluting stdout."""

    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > _MAX_MESSAGE_BYTES:
        raise BrowserHelperFailed("The SongDrop native response was unexpectedly large.")
    stream.write(struct.pack("=I", len(encoded)))
    stream.write(encoded)
    stream.flush()


def service_is_ready() -> bool:
    """Return whether SongDrop's expected loopback API is healthy."""

    try:
        with urllib.request.urlopen(f"{API_BASE_URL}/v1/health", timeout=0.5) as response:
            if response.status != 200:
                return False
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, urllib.error.URLError):
        return False
    return isinstance(payload, dict) and payload.get("service") == "songdrop"


def _service_working_directory() -> Path:
    """Keep editable installs compatible with their project-local uncommitted .env."""

    project_root = Path(__file__).resolve().parents[2]
    if (project_root / "pyproject.toml").is_file():
        return project_root
    return Path.home()


def _service_environment() -> dict[str, str]:
    environment = os.environ.copy()
    existing_path = environment.get("PATH", "")
    candidates = [
        str(Path(sys.executable).resolve().parent),
        "/opt/homebrew/bin",
        "/usr/local/bin",
    ]
    if existing_path:
        candidates.append(existing_path)
    environment["PATH"] = os.pathsep.join(candidates)
    environment["PYTHONUNBUFFERED"] = "1"
    return environment


def ensure_service(
    *,
    health_check: Callable[[], bool] = service_is_ready,
    timeout_seconds: float = _START_TIMEOUT_SECONDS,
) -> StartResult:
    """Start the API only when absent, then wait for a verified health response."""

    if health_check():
        return StartResult(ok=True, started=False)

    state_directory = _application_support_directory()
    log_path = _service_log_path()
    state_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    log_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = state_directory / "service-start.lock"

    with lock_path.open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        if health_check():
            return StartResult(ok=True, started=False)

        try:
            with log_path.open("ab") as log_file:
                process = subprocess.Popen(
                    [sys.executable, "-m", "songdrop.cli", "serve"],
                    cwd=_service_working_directory(),
                    env=_service_environment(),
                    stdin=subprocess.DEVNULL,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    close_fds=True,
                )
        except OSError as error:
            raise BrowserHelperFailed(
                f"SongDrop could not start its local service: {error}"
            ) from error

        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if health_check():
                return StartResult(ok=True, started=True)
            if process.poll() is not None:
                raise BrowserHelperFailed(
                    "SongDrop's local service exited before it became ready. "
                    f"See {log_path}"
                )
            time.sleep(0.1)

    raise BrowserHelperFailed(
        "SongDrop's local service did not become ready in time. " f"See {_service_log_path()}"
    )


def native_helper_executable() -> Path:
    """Locate the console entry point installed beside the active Python executable."""

    # Virtual-environment Python executables are commonly symlinks to the framework Python.
    # Keep the lexical venv directory so we find its sibling console entry point.
    beside_python = Path(sys.executable).absolute().parent / "songdrop-native-helper"
    if beside_python.is_file():
        return beside_python
    located = shutil.which("songdrop-native-helper")
    if located:
        return Path(located).resolve()
    raise BrowserHelperFailed(
        "The SongDrop browser helper executable is not installed. "
        "Run 'python -m pip install -e .' and try again."
    )


def install_brave_native_host(
    *,
    executable: Path | None = None,
    destinations: tuple[Path, ...] | None = None,
) -> tuple[Path, ...]:
    """Atomically register SongDrop's exact extension origin with Brave lookup paths."""

    helper = (executable or native_helper_executable()).expanduser().resolve(strict=True)
    if not helper.is_file() or not os.access(helper, os.X_OK):
        raise BrowserHelperFailed(f"SongDrop's browser helper is not executable: {helper}")
    manifest = {
        "name": NATIVE_HOST_NAME,
        "description": "Start SongDrop's local browser service",
        "path": str(helper),
        "type": "stdio",
        "allowed_origins": [f"{EXTENSION_ORIGIN}/"],
    }
    installed_paths: list[Path] = []
    for requested_path in destinations or brave_native_host_manifest_paths():
        manifest_path = requested_path.expanduser().resolve(strict=False)
        manifest_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary_path = manifest_path.with_name(f".{manifest_path.name}.tmp")
        try:
            descriptor = os.open(
                temporary_path,
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                0o600,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as manifest_file:
                json.dump(manifest, manifest_file, ensure_ascii=False, indent=2)
                manifest_file.write("\n")
            os.replace(temporary_path, manifest_path)
            manifest_path.chmod(0o600)
        except OSError as error:
            raise BrowserHelperFailed(f"Could not register SongDrop with Brave: {error}") from error
        installed_paths.append(manifest_path)
    return tuple(installed_paths)


def caller_is_authorized(origin: str) -> bool:
    """Accept Chromium's two equivalent serializations of the fixed extension origin."""

    return origin in extension_origin_variants()


def main() -> None:
    """Serve one tightly scoped request from the authorized extension."""

    response: dict[str, object]
    try:
        caller_origin = sys.argv[1] if len(sys.argv) > 1 else ""
        if not caller_is_authorized(caller_origin):
            raise BrowserHelperFailed("The native request did not come from SongDrop's extension.")
        request = read_native_message(sys.stdin.buffer)
        if request is None:
            return
        if request.get("action") != "ensure_service":
            raise BrowserHelperFailed("Unsupported SongDrop native-helper action.")
        response = dict(ensure_service())
    except BrowserHelperFailed as error:
        response = {"ok": False, "error": str(error)}
    except Exception:
        response = {"ok": False, "error": "SongDrop's browser helper failed unexpectedly."}
    write_native_message(sys.stdout.buffer, response)


if __name__ == "__main__":  # pragma: no cover
    main()
