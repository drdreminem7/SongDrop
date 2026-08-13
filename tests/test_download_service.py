from pathlib import Path

import pytest

from songdrop.config import LibraryDestination, SongDropConfig
from songdrop.exceptions import (
    AppleMusicImportFailed,
    AppleMusicVerificationFailed,
    MetadataFailed,
    UnsupportedPlatform,
)
from songdrop.models import (
    AudioFormat,
    DownloadOptions,
    DownloadResult,
    MusicImportResult,
    TrackMetadata,
)
from songdrop.providers.base import MediaProvider
from songdrop.services.apple_music import AppleMusicImporter
from songdrop.services.downloader import DownloadService


class FakeProvider(MediaProvider):
    def __init__(self, metadata: TrackMetadata, events: list[str] | None = None) -> None:
        self.metadata = metadata
        self.events = events if events is not None else []
        self.download_calls = 0
        self.metadata_calls = 0

    def supports(self, url: str) -> bool:
        return url.startswith("https://example.test/")

    def get_metadata(self, url: str) -> TrackMetadata:
        self.metadata_calls += 1
        self.events.append("provider_metadata")
        return self.metadata

    def download(self, url: str, options: DownloadOptions) -> DownloadResult:
        self.download_calls += 1
        self.events.append("download")
        source = options.staging_dir / "source.m4a"
        source.write_bytes(b"downloaded")
        return DownloadResult(path=source, metadata=self.metadata)


class FakeConverter:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def convert(self, source: Path, destination: Path, audio_format: AudioFormat) -> Path:
        self.events.append("convert")
        destination.write_bytes(source.read_bytes() + b"-processed")
        return destination


class FakeMetadataService:
    def __init__(self, events: list[str], *, fail: bool = False) -> None:
        self.events = events
        self.fail = fail

    def fetch_artwork(self, metadata: TrackMetadata) -> bytes | None:
        self.events.append("artwork")
        return b"artwork"

    def write(
        self,
        path: Path,
        audio_format: AudioFormat,
        metadata: TrackMetadata,
        artwork: bytes | None = None,
    ) -> None:
        self.events.append("metadata")
        if self.fail:
            raise MetadataFailed("tagging failed")
        assert artwork == b"artwork"
        path.write_bytes(path.read_bytes() + b"-tagged")


class FakeResolver:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def resolve(self, metadata: TrackMetadata, audio_path: Path) -> TrackMetadata:
        self.events.append("enrich")
        assert audio_path.read_bytes().endswith(b"-processed")
        return metadata


class FakeImporter:
    def __init__(
        self,
        managed_path: Path,
        events: list[str],
        *,
        failure: Exception | None = None,
    ) -> None:
        self.managed_path = managed_path
        self.events = events
        self.failure = failure
        self.import_calls = 0

    def ensure_available(self) -> None:
        self.events.append("available")

    def import_track(self, path: Path) -> MusicImportResult:
        self.import_calls += 1
        self.events.append("import")
        assert path.read_bytes().endswith(b"-tagged")
        if self.failure:
            raise self.failure
        self.managed_path.parent.mkdir(parents=True, exist_ok=True)
        self.managed_path.write_bytes(path.read_bytes())
        return MusicImportResult(persistent_id="ABCDEF123", library_path=self.managed_path)


def build_service(
    staging_root: Path,
    track: TrackMetadata,
    importer: FakeImporter | AppleMusicImporter,
    events: list[str],
    *,
    keep_file: bool = False,
    metadata_failure: bool = False,
    enrich: bool = False,
    download_only: bool = False,
) -> tuple[DownloadService, FakeProvider]:
    provider = FakeProvider(track, events)
    service = DownloadService(
        SongDropConfig(
            staging_dir=staging_root,
            delete_staging_after_verified_import=not keep_file,
            library_destination=(
                LibraryDestination.FILESYSTEM if download_only else LibraryDestination.APPLE_MUSIC
            ),
        ),
        providers=(provider,),
        converter=FakeConverter(events),  # type: ignore[arg-type]
        metadata_service=FakeMetadataService(  # type: ignore[arg-type]
            events, fail=metadata_failure
        ),
        importer=importer,
        metadata_resolver=FakeResolver(events) if enrich else None,
    )
    return service, provider


def test_default_pipeline_imports_after_artwork_and_metadata_then_cleans_staging(
    tmp_path: Path, track: TrackMetadata
) -> None:
    events: list[str] = []
    staging_root = tmp_path / "SongDrop"
    managed = tmp_path / "Music Media" / "A Track.m4a"
    importer = FakeImporter(managed, events)
    service, provider = build_service(staging_root, track, importer, events)

    result = service.import_url("https://example.test/track")

    assert events == [
        "available",
        "provider_metadata",
        "download",
        "convert",
        "artwork",
        "metadata",
        "import",
    ]
    assert provider.download_calls == 1
    assert importer.import_calls == 1
    assert result.path == managed
    assert result.music_persistent_id == "ABCDEF123"
    assert result.staging_path is None
    assert list(staging_root.iterdir()) == []


@pytest.mark.parametrize(
    "failure",
    [
        AppleMusicImportFailed("Music rejected the file"),
        AppleMusicVerificationFailed("Could not verify the entry"),
    ],
)
def test_import_or_verification_failure_preserves_tagged_file(
    tmp_path: Path,
    track: TrackMetadata,
    failure: Exception,
) -> None:
    events: list[str] = []
    staging_root = tmp_path / "SongDrop"
    importer = FakeImporter(tmp_path / "managed.m4a", events, failure=failure)
    service, _ = build_service(staging_root, track, importer, events)

    with pytest.raises(type(failure)) as captured:
        service.import_url("https://example.test/track")

    error = captured.value
    assert isinstance(error, (AppleMusicImportFailed, AppleMusicVerificationFailed))
    assert error.preserved_path is not None
    assert error.preserved_path.read_bytes().endswith(b"-tagged")
    assert error.preserved_path.parent == staging_root


def test_metadata_failure_prevents_import_and_preserves_processed_audio(
    tmp_path: Path, track: TrackMetadata
) -> None:
    events: list[str] = []
    staging_root = tmp_path / "SongDrop"
    importer = FakeImporter(tmp_path / "managed.m4a", events)
    service, _ = build_service(
        staging_root,
        track,
        importer,
        events,
        metadata_failure=True,
    )

    with pytest.raises(MetadataFailed) as captured:
        service.import_url("https://example.test/track")

    assert "import" not in events
    assert captured.value.preserved_path is not None
    assert captured.value.preserved_path.is_file()


def test_unsupported_platform_fails_before_provider_or_download(
    tmp_path: Path, track: TrackMetadata
) -> None:
    events: list[str] = []
    importer = AppleMusicImporter(platform_name="linux")
    service, provider = build_service(tmp_path / "SongDrop", track, importer, events)

    with pytest.raises(UnsupportedPlatform, match="not yet supported"):
        service.import_url("https://example.test/track")

    assert provider.metadata_calls == 0
    assert provider.download_calls == 0


def test_keep_file_retains_only_finished_tagged_staging_copy(
    tmp_path: Path, track: TrackMetadata
) -> None:
    events: list[str] = []
    staging_root = tmp_path / "SongDrop"
    importer = FakeImporter(tmp_path / "Music Media" / "track.m4a", events)
    service, _ = build_service(staging_root, track, importer, events, keep_file=True)

    result = service.import_url("https://example.test/track")

    assert result.staging_path is not None
    assert result.staging_path.is_file()
    assert result.staging_path.read_bytes().endswith(b"-tagged")
    assert list(staging_root.iterdir()) == [result.staging_path]


def test_enrichment_completes_before_artwork_and_tagging(
    tmp_path: Path, track: TrackMetadata
) -> None:
    events: list[str] = []
    importer = FakeImporter(tmp_path / "Music Media" / "track.m4a", events)
    service, _ = build_service(
        tmp_path / "SongDrop",
        track,
        importer,
        events,
        enrich=True,
    )

    service.import_url("https://example.test/track")

    assert events.index("convert") < events.index("enrich")
    assert events.index("enrich") < events.index("artwork")
    assert events.index("artwork") < events.index("metadata")


def test_download_only_keeps_tagged_file_and_never_contacts_apple_music(
    tmp_path: Path, track: TrackMetadata
) -> None:
    events: list[str] = []
    staging_root = tmp_path / "Downloads" / "SongDrop"
    importer = FakeImporter(tmp_path / "Music Media" / "track.m4a", events)
    service, _ = build_service(
        staging_root,
        track,
        importer,
        events,
        enrich=True,
        download_only=True,
    )

    result = service.import_url("https://example.test/track")

    assert events == [
        "provider_metadata",
        "download",
        "convert",
        "enrich",
        "artwork",
        "metadata",
    ]
    assert importer.import_calls == 0
    assert result.path.parent == staging_root
    assert result.path.name == "A Track.mp3"
    assert result.path.read_bytes().endswith(b"-tagged")
    assert result.staging_path == result.path
    assert result.music_persistent_id is None
    assert list(staging_root.iterdir()) == [result.path]


def test_download_only_existing_source_is_skipped_before_download(
    tmp_path: Path, track: TrackMetadata
) -> None:
    events: list[str] = []
    existing = tmp_path / "SongDrop" / "A Track.mp3"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"existing")

    class FakeDuplicateDetector:
        def find(self, metadata: TrackMetadata, audio_format: AudioFormat) -> Path | None:
            return existing

    importer = FakeImporter(tmp_path / "Music Media" / "track.mp3", events)
    provider = FakeProvider(track, events)
    service = DownloadService(
        SongDropConfig(
            staging_dir=existing.parent,
            library_destination=LibraryDestination.FILESYSTEM,
        ),
        providers=(provider,),
        converter=FakeConverter(events),  # type: ignore[arg-type]
        metadata_service=FakeMetadataService(events),  # type: ignore[arg-type]
        importer=importer,
        duplicate_detector=FakeDuplicateDetector(),  # type: ignore[arg-type]
    )

    result = service.import_url("https://example.test/track")

    assert result.already_downloaded is True
    assert result.path == existing
    assert provider.download_calls == 0
    assert importer.import_calls == 0
    assert events == ["provider_metadata"]
