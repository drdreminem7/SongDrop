"""FFmpeg-specific audio processing."""

import logging
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from songdrop.exceptions import ConversionFailed, MissingDependency
from songdrop.models import AudioFormat

logger = logging.getLogger(__name__)


class CommandRunner(Protocol):
    def __call__(
        self,
        args: Sequence[str],
        *,
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]: ...


class AudioConverter:
    """Create a requested output format while avoiding needless transcoding."""

    def __init__(
        self,
        *,
        ffmpeg_path: str | None = None,
        runner: CommandRunner = subprocess.run,
    ) -> None:
        self._configured_ffmpeg = ffmpeg_path
        self._runner = runner

    def convert(self, source: Path, destination: Path, audio_format: AudioFormat) -> Path:
        if not source.is_file():
            raise ConversionFailed(f"Downloaded audio file does not exist: {source}")
        destination.parent.mkdir(parents=True, exist_ok=True)

        if audio_format is AudioFormat.M4A and source.suffix.lower() in {".m4a", ".mp4"}:
            try:
                shutil.copy2(source, destination)
            except OSError as error:
                raise ConversionFailed(f"Could not stage M4A audio: {error}") from error
            return destination

        ffmpeg = self._find_ffmpeg()
        if audio_format is AudioFormat.MP3:
            command = [
                ffmpeg,
                "-nostdin",
                "-y",
                "-i",
                str(source),
                "-map",
                "0:a:0",
                "-vn",
                "-c:a",
                "libmp3lame",
                "-q:a",
                "2",
                str(destination),
            ]
            self._run(command, "MP3 conversion")
            return destination

        remux = [
            ffmpeg,
            "-nostdin",
            "-y",
            "-i",
            str(source),
            "-map",
            "0:a:0",
            "-vn",
            "-c:a",
            "copy",
            str(destination),
        ]
        try:
            self._run(remux, "M4A remux")
        except ConversionFailed:
            logger.info("The source codec cannot be remuxed to M4A; encoding AAC instead.")
            transcode = [
                ffmpeg,
                "-nostdin",
                "-y",
                "-i",
                str(source),
                "-map",
                "0:a:0",
                "-vn",
                "-c:a",
                "aac",
                "-b:a",
                "256k",
                str(destination),
            ]
            self._run(transcode, "M4A conversion")
        return destination

    def _find_ffmpeg(self) -> str:
        ffmpeg = self._configured_ffmpeg or shutil.which("ffmpeg")
        if not ffmpeg:
            raise MissingDependency(
                "FFmpeg was not found.\nInstall FFmpeg and ensure it is available on PATH."
            )
        return ffmpeg

    def _run(self, command: list[str], operation: str) -> None:
        try:
            result = self._runner(command, check=False, capture_output=True, text=True)
        except OSError as error:
            raise ConversionFailed(f"Could not start FFmpeg: {error}") from error
        if result.returncode != 0:
            detail = result.stderr.strip().splitlines()
            suffix = f" {detail[-1]}" if detail else ""
            raise ConversionFailed(f"{operation} failed.{suffix}")
