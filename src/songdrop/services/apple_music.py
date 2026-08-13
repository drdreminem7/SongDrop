"""Isolated macOS Music.app import and verification integration."""

import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from songdrop.exceptions import (
    AppleMusicImportFailed,
    AppleMusicUnavailable,
    AppleMusicVerificationFailed,
    UnsupportedPlatform,
)
from songdrop.models import MusicImportResult

_FIELD_SEPARATOR = "\x1e"
_DEFAULT_MUSIC_APP_PATHS = (
    Path("/System/Applications/Music.app"),
    Path("/Applications/Music.app"),
)

_APPLE_MUSIC_IMPORT_SCRIPT = r"""
on run argv
    set fieldSeparator to character id 30
    if (count of argv) is not 1 then
        return "IMPORT_ERROR" & fieldSeparator & "SongDrop did not provide one source path."
    end if

    set sourcePath to item 1 of argv
    try
        set sourceFile to POSIX file sourcePath as alias
        tell application "Music"
            set addedResult to add {sourceFile}
            if class of addedResult is list then
                if (count of addedResult) is 0 then error "Music returned no imported track."
                set importedTrack to item 1 of addedResult
            else
                set importedTrack to addedResult
            end if
            if importedTrack is missing value then error "Music returned no imported track."
            set importedID to (get persistent ID of importedTrack)
        end tell
    on error errorMessage number errorNumber
        return "IMPORT_ERROR" & fieldSeparator & errorNumber & ": " & errorMessage
    end try

    try
        if importedID is missing value or importedID is "" then
            error "The imported track has no persistent ID."
        end if

        tell application "Music"
            set verifiedTracks to {}
            repeat 12 times
                set verifiedTracks to (every file track of library playlist 1 ¬
                    whose persistent ID is importedID)
                if (count of verifiedTracks) is greater than 0 then exit repeat
                delay 0.25
            end repeat
            if (count of verifiedTracks) is 0 then
                error "No file track with the returned persistent ID exists in the Music library."
            end if

            set verifiedTrack to item 1 of verifiedTracks
            if (get persistent ID of verifiedTrack) is not importedID then
                error "The Music library persistent ID did not match the imported track."
            end if
            set importedLocation to location of verifiedTrack
            if importedLocation is missing value then
                error "The imported Music track has no local file location."
            end if
            set importedPath to POSIX path of importedLocation
        end tell
    on error errorMessage number errorNumber
        return "VERIFY_ERROR" & fieldSeparator & errorNumber & ": " & errorMessage
    end try

    return "OK" & fieldSeparator & importedID & fieldSeparator & importedPath
end run
"""


class MusicLibraryImporter(Protocol):
    """Destination-independent contract consumed by the download pipeline."""

    def ensure_available(self) -> None:
        """Fail before downloading if this destination cannot be used."""

    def import_track(self, path: Path) -> MusicImportResult:
        """Import and verify a fully prepared local audio file."""


class AppleScriptRunner(Protocol):
    def __call__(
        self,
        args: Sequence[str],
        *,
        input: str,
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]: ...


class AppleMusicImporter:
    """Import into Music.app and prove it owns a separate local media file."""

    def __init__(
        self,
        *,
        runner: AppleScriptRunner = subprocess.run,
        platform_name: str | None = None,
        osascript_path: str | None = None,
        music_app_path: Path | None = None,
    ) -> None:
        self._runner = runner
        self._platform_name = platform_name or sys.platform
        self._osascript_path = osascript_path
        self._music_app_path = music_app_path

    def ensure_available(self) -> None:
        if self._platform_name != "darwin":
            raise UnsupportedPlatform(
                "SongDrop's current default library destination is Apple Music on macOS.\n"
                "This platform is not yet supported."
            )
        if self._resolve_osascript() is None:
            raise AppleMusicUnavailable(
                "AppleScript is unavailable, so SongDrop cannot import into Apple Music."
            )
        if self._resolve_music_app() is None:
            raise AppleMusicUnavailable("The Apple Music application was not found.")

    def import_track(self, path: Path) -> MusicImportResult:
        self.ensure_available()
        source = path.expanduser().resolve(strict=False)
        if not source.is_file():
            raise AppleMusicImportFailed(f"The prepared audio file does not exist: {source}")

        osascript = self._resolve_osascript()
        assert osascript is not None
        try:
            completed = self._runner(
                [osascript, "-", str(source)],
                input=_APPLE_MUSIC_IMPORT_SCRIPT,
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError as error:
            raise AppleMusicUnavailable(f"Could not start AppleScript: {error}") from error

        if completed.returncode != 0:
            detail = completed.stderr.strip() or "AppleScript exited without an error message."
            if "-1743" in detail or "not authorized" in detail.lower():
                raise AppleMusicUnavailable(
                    "macOS denied SongDrop permission to control Music. Allow your terminal or "
                    "SongDrop under System Settings > Privacy & Security > Automation."
                )
            raise AppleMusicImportFailed(f"Apple Music import failed: {detail}")

        fields = completed.stdout.rstrip("\r\n").split(_FIELD_SEPARATOR)
        if fields and fields[0] == "IMPORT_ERROR":
            detail = fields[1] if len(fields) > 1 else "Music rejected the file."
            if "-1743" in detail or "not authorized" in detail.lower():
                raise AppleMusicUnavailable(
                    "macOS denied SongDrop permission to control Music. Allow your terminal or "
                    "SongDrop under System Settings > Privacy & Security > Automation."
                )
            raise AppleMusicImportFailed(f"Apple Music could not import the track: {detail}")
        if fields and fields[0] == "VERIFY_ERROR":
            detail = fields[1] if len(fields) > 1 else "The library entry was not found."
            raise AppleMusicVerificationFailed(
                f"SongDrop could not verify the Apple Music library entry: {detail}"
            )
        if len(fields) != 3 or fields[0] != "OK" or not fields[1] or not fields[2]:
            raise AppleMusicVerificationFailed(
                "Apple Music returned an incomplete verification response."
            )

        library_path = Path(fields[2]).expanduser().resolve(strict=False)
        if not library_path.is_file():
            raise AppleMusicVerificationFailed(
                "The imported Apple Music entry does not point to an existing local media file."
            )
        if library_path == source or (source.exists() and os.path.samefile(source, library_path)):
            raise AppleMusicVerificationFailed(
                "Apple Music is referencing SongDrop's staging file instead of a managed copy. "
                "Enable 'Copy files to Music Media folder when adding to library' in "
                "Music > Settings > Files, then retry."
            )
        return MusicImportResult(persistent_id=fields[1], library_path=library_path)

    def _resolve_osascript(self) -> str | None:
        return self._osascript_path or shutil.which("osascript")

    def _resolve_music_app(self) -> Path | None:
        if self._music_app_path is not None:
            return self._music_app_path if self._music_app_path.is_dir() else None
        return next((path for path in _DEFAULT_MUSIC_APP_PATHS if path.is_dir()), None)
