import io
import json
import stat
import struct
from pathlib import Path
from typing import Any

import pytest

from songdrop.browser import (
    EXTENSION_ORIGIN,
    NATIVE_HOST_NAME,
    brave_native_host_manifest_paths,
)
from songdrop.exceptions import BrowserHelperFailed
from songdrop.native_helper import (
    caller_is_authorized,
    ensure_service,
    install_brave_native_host,
    native_helper_executable,
    read_native_message,
    write_native_message,
)


def test_native_message_round_trip_supports_spaces_and_unicode() -> None:
    stream = io.BytesIO()
    payload = {"action": "ensure_service", "path": "/Users/Test Music/Песен.mp3"}

    write_native_message(stream, payload)
    stream.seek(0)

    assert read_native_message(stream) == payload


def test_native_caller_accepts_only_the_fixed_extension_origin() -> None:
    assert caller_is_authorized(EXTENSION_ORIGIN)
    assert caller_is_authorized(f"{EXTENSION_ORIGIN}/")
    assert not caller_is_authorized("chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/")
    assert not caller_is_authorized(f"{EXTENSION_ORIGIN}/unexpected")


def test_brave_installer_covers_branded_and_chromium_lookup_paths(tmp_path: Path) -> None:
    paths = brave_native_host_manifest_paths(tmp_path)

    assert paths[0].parent == (
        tmp_path
        / "Library"
        / "Application Support"
        / "BraveSoftware"
        / "Brave-Browser"
        / "NativeMessagingHosts"
    )
    assert paths[1].parent == (
        tmp_path
        / "Library"
        / "Application Support"
        / "Google"
        / "Chrome"
        / "NativeMessagingHosts"
    )


def test_native_message_rejects_truncated_payload() -> None:
    stream = io.BytesIO(struct.pack("=I", 20) + b"{}")

    with pytest.raises(BrowserHelperFailed, match="incomplete"):
        read_native_message(stream)


def test_healthy_service_is_not_started() -> None:
    result = ensure_service(health_check=lambda: True)

    assert result == {"ok": True, "started": False}


def test_missing_service_is_started_detached_and_verified(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    checks = iter([False, False, True])
    popen_arguments: list[tuple[list[str], dict[str, Any]]] = []

    class FakeProcess:
        def poll(self) -> int | None:
            return None

    def fake_popen(command: list[str], **kwargs: Any) -> FakeProcess:
        popen_arguments.append((command, kwargs))
        return FakeProcess()

    monkeypatch.setattr(
        "songdrop.native_helper._application_support_directory",
        lambda: tmp_path / "Application Support" / "SongDrop",
    )
    monkeypatch.setattr(
        "songdrop.native_helper._service_log_path",
        lambda: tmp_path / "Logs" / "SongDrop" / "service.log",
    )
    monkeypatch.setattr("songdrop.native_helper._service_working_directory", lambda: tmp_path)
    monkeypatch.setattr("songdrop.native_helper._service_environment", lambda: {"PATH": "/bin"})
    monkeypatch.setattr("songdrop.native_helper.subprocess.Popen", fake_popen)

    result = ensure_service(health_check=lambda: next(checks), timeout_seconds=1)

    assert result == {"ok": True, "started": True}
    command, options = popen_arguments[0]
    assert command[1:] == ["-m", "songdrop.cli", "serve"]
    assert options["start_new_session"] is True
    assert options["stdin"] is not None
    assert options["cwd"] == tmp_path


def test_installer_registers_only_the_known_extension_origin(
    tmp_path: Path,
) -> None:
    helper = tmp_path / "Virtual Environment" / "bin" / "songdrop-native-helper"
    helper.parent.mkdir(parents=True)
    helper.write_text("#!/bin/sh\n", encoding="utf-8")
    helper.chmod(0o700)
    destination = tmp_path / "Brave Profile" / "NativeMessagingHosts" / "host.json"

    installed = install_brave_native_host(executable=helper, destinations=(destination,))
    manifest = json.loads(installed[0].read_text(encoding="utf-8"))

    assert manifest == {
        "name": NATIVE_HOST_NAME,
        "description": "Start SongDrop's local browser service",
        "path": str(helper.resolve()),
        "type": "stdio",
        "allowed_origins": [f"{EXTENSION_ORIGIN}/"],
    }
    assert stat.S_IMODE(installed[0].stat().st_mode) == 0o600


def test_helper_lookup_keeps_virtual_environment_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    virtual_environment = tmp_path / "Environment With Spaces" / "bin"
    virtual_environment.mkdir(parents=True)
    python_link = virtual_environment / "python"
    python_link.symlink_to(Path("/usr/bin/python3"))
    helper = virtual_environment / "songdrop-native-helper"
    helper.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr("songdrop.native_helper.sys.executable", str(python_link))

    assert native_helper_executable() == helper
