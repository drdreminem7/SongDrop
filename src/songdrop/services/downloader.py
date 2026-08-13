"""End-to-end import orchestration independent of the CLI and Music implementation."""

from pathlib import Path

from songdrop.config import LibraryDestination, SongDropConfig
from songdrop.exceptions import (
    AppleMusicVerificationFailed,
    FileWriteFailed,
    OperationCancelled,
    SongDropError,
)
from songdrop.models import DownloadOptions, ImportResult, TrackMetadata
from songdrop.providers import default_providers, select_provider
from songdrop.providers.base import MediaProvider
from songdrop.services.apple_music import AppleMusicImporter, MusicLibraryImporter
from songdrop.services.converter import AudioConverter
from songdrop.services.duplicate_detector import DuplicateDetector
from songdrop.services.enrichment import MetadataResolver, build_metadata_resolver
from songdrop.services.library import LibraryService
from songdrop.services.metadata import MetadataService


class DownloadService:
    """Prepare, tag, import, verify, and safely clean up one track."""

    def __init__(
        self,
        config: SongDropConfig,
        *,
        providers: tuple[MediaProvider, ...] | None = None,
        converter: AudioConverter | None = None,
        metadata_service: MetadataService | None = None,
        library: LibraryService | None = None,
        importer: MusicLibraryImporter | None = None,
        metadata_resolver: MetadataResolver | None = None,
        duplicate_detector: DuplicateDetector | None = None,
    ) -> None:
        self.config = config
        self.providers = providers or default_providers()
        self.converter = converter or AudioConverter()
        self.metadata_service = metadata_service or MetadataService()
        self.library = library or LibraryService(config.staging_dir)
        self.importer = importer or AppleMusicImporter()
        self.metadata_resolver = metadata_resolver
        self.duplicate_detector = duplicate_detector or DuplicateDetector(config.staging_dir)

    def ensure_available(self) -> None:
        """Validate the configured destination before collection discovery or download."""

        if self.config.library_destination is LibraryDestination.APPLE_MUSIC:
            self.importer.ensure_available()

    def import_url(self, url: str) -> ImportResult:
        """Run the ordered pipeline, preserving recoverable audio on every failure."""

        self.ensure_available()
        provider = select_provider(url, self.providers)
        metadata = provider.get_metadata(url)
        if self.config.download_only:
            duplicate = self.duplicate_detector.find(metadata, self.config.audio_format)
            if duplicate is not None:
                return self._duplicate_result(duplicate, metadata)

        session = self.library.create_session()
        downloaded_path: Path | None = None
        processed_path: Path | None = None
        staging_path: Path | None = None
        try:
            downloaded = provider.download(
                url,
                DownloadOptions(
                    staging_dir=session,
                    audio_format=self.config.audio_format,
                ),
            )
            downloaded_path = downloaded.path
            metadata = downloaded.metadata

            processed_path = session / f"processed.{self.config.audio_format.value}"
            self.converter.convert(downloaded_path, processed_path, self.config.audio_format)
            if self.metadata_resolver is not None:
                metadata = self.metadata_resolver.resolve(metadata, processed_path)
            artwork = self.metadata_service.fetch_artwork(metadata)
            self.metadata_service.write(
                processed_path,
                self.config.audio_format,
                metadata,
                artwork,
            )

            if self.config.download_only:
                duplicate = self.duplicate_detector.find(metadata, self.config.audio_format)
                if duplicate is not None:
                    self.library.cleanup_session(session)
                    return self._duplicate_result(duplicate, metadata)

            # Music only receives the completed, tagged, flushed file at this stable path.
            staging_path = self.library.promote(
                session,
                processed_path,
                self.library.destination(
                    metadata,
                    self.config.audio_format,
                    title_only=self.config.download_only,
                ),
            )
            if self.config.download_only:
                self.library.cleanup_session(session)
                return ImportResult(
                    path=staging_path,
                    metadata=metadata,
                    staging_path=staging_path,
                )
            music_import = self.importer.import_track(staging_path)
            managed_path = music_import.library_path.expanduser().resolve(strict=False)
            if managed_path.is_relative_to(session.resolve(strict=False)):
                raise AppleMusicVerificationFailed(
                    "Apple Music reported a media file inside SongDrop's active work directory."
                )
        except SongDropError as error:
            preserved = self._preserve_after_failure(
                session,
                staging_path or self._best_candidate(processed_path, downloaded_path),
                metadata,
            )
            error.with_preserved_path(preserved)
            raise
        except KeyboardInterrupt as error:
            preserved = self._preserve_after_failure(
                session,
                staging_path or self._best_candidate(processed_path, downloaded_path),
                metadata,
            )
            raise OperationCancelled(
                "Import cancelled by user.", preserved_path=preserved
            ) from error
        except Exception as error:
            preserved = self._preserve_after_failure(
                session,
                staging_path or self._best_candidate(processed_path, downloaded_path),
                metadata,
            )
            raise SongDropError(
                "An unexpected error interrupted the import.", preserved_path=preserved
            ) from error

        if self.config.delete_staging_after_verified_import:
            try:
                self.library.delete_owned(staging_path)
                self.library.cleanup_session(session)
            except FileWriteFailed as error:
                preserved = staging_path if staging_path.exists() else session
                error.with_preserved_path(preserved)
                raise
            retained_staging_path = None
        else:
            try:
                self.library.cleanup_session(session)
            except FileWriteFailed as error:
                error.with_preserved_path(staging_path)
                raise
            retained_staging_path = staging_path

        return ImportResult(
            path=music_import.library_path,
            metadata=metadata,
            staging_path=retained_staging_path,
            music_persistent_id=music_import.persistent_id,
        )

    def _preserve_after_failure(
        self,
        session: Path,
        candidate: Path | None,
        metadata: TrackMetadata,
    ) -> Path:
        if candidate is not None and self.library.owns_file(candidate):
            return candidate
        try:
            return self.library.preservation_path(session, candidate, metadata)
        except FileWriteFailed:
            return session

    @staticmethod
    def _best_candidate(processed: Path | None, downloaded: Path | None) -> Path | None:
        if processed is not None and processed.is_file():
            return processed
        if downloaded is not None and downloaded.is_file():
            return downloaded
        return None

    @staticmethod
    def _duplicate_result(path: Path, metadata: TrackMetadata) -> ImportResult:
        return ImportResult(
            path=path,
            metadata=metadata,
            staging_path=path,
            already_downloaded=True,
        )


def build_download_service(config: SongDropConfig) -> DownloadService:
    """Compose the current default Apple Music destination."""

    resolver = (
        build_metadata_resolver(
            acoustid_api_key=config.acoustid_api_key,
            lyrics_enabled=config.lyrics_enabled,
        )
        if config.metadata_enrichment_enabled
        else None
    )
    return DownloadService(
        config,
        importer=AppleMusicImporter(),
        metadata_resolver=resolver,
    )
