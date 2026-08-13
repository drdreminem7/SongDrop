"""Failure-safe, ownership-aware staging file management."""

import os
import shutil
import tempfile
from pathlib import Path

from songdrop.exceptions import FileWriteFailed
from songdrop.models import AudioFormat, TrackMetadata
from songdrop.utils.filenames import ensure_within, sanitize_component


class LibraryService:
    """Manage transient and preserved files below one SongDrop staging root.

    The historical class name is retained to avoid needless architectural churn. Music.app is
    now the actual library; this service only manages files SongDrop created itself.
    """

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve(strict=False)
        self._owned_sessions: set[Path] = set()
        self._owned_files: set[Path] = set()

    def ensure_exists(self) -> None:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise FileWriteFailed(f"Could not create staging directory: {error}") from error
        if not self.root.is_dir():
            raise FileWriteFailed(f"Staging path is not a directory: {self.root}")

    def destination(
        self,
        metadata: TrackMetadata,
        audio_format: AudioFormat,
        *,
        title_only: bool = False,
    ) -> Path:
        """Return the stable path used for import or safe failure preservation."""

        title = sanitize_component(metadata.title)
        if title_only:
            candidate = self.root / f"{title}.{audio_format.value}"
        else:
            artist = sanitize_component(metadata.artist or "Unknown Artist")
            candidate = self.root / f"{artist} - {title}.{audio_format.value}"
        return ensure_within(self.root, candidate)

    def create_session(self) -> Path:
        """Create and register a unique SongDrop-owned work directory."""

        self.ensure_exists()
        try:
            directory = Path(tempfile.mkdtemp(prefix=".songdrop-", dir=self.root)).resolve()
        except OSError as error:
            raise FileWriteFailed(f"Could not create a staging session: {error}") from error
        self._owned_sessions.add(directory)
        return directory

    def promote(self, session: Path, staged_file: Path, destination: Path) -> Path:
        """Move a completed session file to a stable, uniquely named staging path."""

        safe_session = self._require_owned_session(session)
        safe_source = ensure_within(safe_session, staged_file)
        if not safe_source.is_file():
            raise FileWriteFailed(f"Staged audio file does not exist: {safe_source}")
        safe_destination = self._available_destination(ensure_within(self.root, destination))
        try:
            safe_destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(safe_source, safe_destination)
        except OSError as error:
            raise FileWriteFailed(f"Could not preserve {safe_destination.name}: {error}") from error
        self._owned_files.add(safe_destination)
        return safe_destination

    def delete_owned(self, path: Path) -> None:
        """Delete exactly one file registered as created by this service instance."""

        safe_path = ensure_within(self.root, path)
        if safe_path not in self._owned_files:
            raise FileWriteFailed(f"Refusing to delete a file SongDrop does not own: {safe_path}")
        try:
            safe_path.unlink(missing_ok=True)
            self._remove_empty_parents(safe_path.parent)
        except OSError as error:
            raise FileWriteFailed(f"Could not remove staging file {safe_path}: {error}") from error
        self._owned_files.remove(safe_path)

    def owns_file(self, path: Path) -> bool:
        """Return whether a path was created and registered by this service instance."""

        return path.expanduser().resolve(strict=False) in self._owned_files

    def cleanup_session(self, session: Path) -> None:
        """Remove a work directory created and registered by this service instance."""

        safe_session = self._require_owned_session(session)
        try:
            shutil.rmtree(safe_session)
        except OSError as error:
            raise FileWriteFailed(
                f"Could not remove staging session {safe_session}: {error}"
            ) from error
        self._owned_sessions.remove(safe_session)

    def preservation_path(
        self,
        session: Path,
        candidate: Path | None,
        metadata: TrackMetadata,
    ) -> Path:
        """Expose the best available audio after a failed pipeline without deleting anything."""

        safe_session = self._require_owned_session(session)
        if candidate is None or not candidate.is_file():
            return safe_session
        suffix = candidate.suffix.lstrip(".") or "audio"
        artist = sanitize_component(metadata.artist or "Unknown Artist")
        title = sanitize_component(metadata.title)
        destination = self.root / f"{artist} - {title}.{suffix}"
        try:
            return self.promote(safe_session, candidate, destination)
        except FileWriteFailed:
            return ensure_within(safe_session, candidate)

    def _require_owned_session(self, session: Path) -> Path:
        safe_session = ensure_within(self.root, session)
        if safe_session not in self._owned_sessions:
            raise FileWriteFailed(
                f"Refusing to manage a staging session SongDrop does not own: {safe_session}"
            )
        return safe_session

    def _available_destination(self, destination: Path) -> Path:
        if not destination.exists():
            return destination
        for number in range(2, 10_000):
            candidate = destination.with_stem(f"{destination.stem} ({number})")
            if not candidate.exists():
                return candidate
        raise FileWriteFailed(
            f"Could not find an available staging filename for {destination.name}"
        )

    def _remove_empty_parents(self, directory: Path) -> None:
        current = directory
        while current != self.root:
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent
