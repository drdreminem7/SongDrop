from pathlib import Path

import pytest

from songdrop.exceptions import CollectionFailed, DownloadFailed, OperationCancelled
from songdrop.models import (
    BatchOptions,
    BatchStatus,
    CollectionMetadata,
    ImportResult,
    TrackMetadata,
    TrackRequest,
)
from songdrop.providers.base import CollectionProvider
from songdrop.providers.youtube import YouTubeProvider
from songdrop.services.batch import BatchDownloadService


def _import_result(url: str) -> ImportResult:
    metadata = TrackMetadata(
        title=url.rsplit("=", 1)[-1],
        source="youtube",
        source_url=url,
    )
    return ImportResult(path=Path("/Music") / f"{metadata.title}.m4a", metadata=metadata)


class FakeTrackImporter:
    def __init__(self, failures: set[str] | None = None) -> None:
        self.failures = failures or set()
        self.events: list[str] = []

    def ensure_available(self) -> None:
        self.events.append("preflight")

    def import_url(self, url: str) -> ImportResult:
        self.events.append(url)
        if url in self.failures:
            raise DownloadFailed("download failed", preserved_path=Path("/staging/kept.m4a"))
        return _import_result(url)


class FakeCollectionProvider(CollectionProvider):
    def __init__(self, collection: CollectionMetadata, events: list[str]) -> None:
        self.collection = collection
        self.events = events

    def supports_collection(self, url: str) -> bool:
        return True

    def get_collection(self, url: str, *, max_items: int) -> CollectionMetadata:
        self.events.append(f"discover:{max_items}")
        return self.collection


def test_batch_preserves_order_deduplicates_and_continues_after_failure() -> None:
    first = "https://www.youtube.com/watch?v=first000001"
    failed = "https://www.youtube.com/watch?v=failed00001"
    duplicate = "https://youtu.be/first000001"
    last = "https://www.youtube.com/watch?v=last0000001"
    importer = FakeTrackImporter({failed})
    service = BatchDownloadService(importer, media_providers=(YouTubeProvider(),))
    requests = tuple(
        service._request_for_url(url)  # noqa: SLF001 - verifies request identity boundary
        for url in (first, failed, duplicate, last)
    )

    result = service.import_requests(requests, BatchOptions())

    assert [item.status for item in result.items] == [
        BatchStatus.IMPORTED,
        BatchStatus.FAILED,
        BatchStatus.SKIPPED,
        BatchStatus.IMPORTED,
    ]
    assert importer.events == ["preflight", first, failed, last]
    assert result.imported_count == 2
    assert result.skipped_count == 1
    assert result.failed_count == 1
    assert result.items[1].preserved_path == Path("/staging/kept.m4a")


def test_fail_fast_stops_after_first_failure() -> None:
    failed = TrackRequest(url="https://example.test/bad", source="test")
    later = TrackRequest(url="https://example.test/later", source="test")
    importer = FakeTrackImporter({failed.url})
    service = BatchDownloadService(importer)

    result = service.import_requests(
        (failed, later),
        BatchOptions(fail_fast=True),
    )

    assert len(result.items) == 1
    assert result.failed_count == 1
    assert later.url not in importer.events


def test_batch_file_supports_comments_spaces_and_unicode_paths(tmp_path: Path) -> None:
    batch_path = tmp_path / "списък с песни.txt"
    batch_path.write_text(
        "# permitted tracks\n\nhttps://youtu.be/abc12345678\n"
        "  https://www.youtube.com/watch?v=xyz12345678  \n",
        encoding="utf-8",
    )
    importer = FakeTrackImporter()
    service = BatchDownloadService(importer, media_providers=(YouTubeProvider(),))

    result = service.import_file(batch_path, BatchOptions())

    assert result.title == "списък с песни.txt"
    assert result.imported_count == 2
    assert importer.events[1:] == [
        "https://youtu.be/abc12345678",
        "https://www.youtube.com/watch?v=xyz12345678",
    ]


def test_batch_file_limit_is_checked_before_processing(tmp_path: Path) -> None:
    batch_path = tmp_path / "urls.txt"
    batch_path.write_text("https://example.test/1\nhttps://example.test/2\n")
    service = BatchDownloadService(FakeTrackImporter())

    with pytest.raises(CollectionFailed, match="safety limit"):
        service.import_file(batch_path, BatchOptions(max_items=1))


def test_collection_discovery_preflights_destination_and_uses_single_track_pipeline() -> None:
    requests = (
        TrackRequest(
            url="https://www.youtube.com/watch?v=abc12345678",
            source="youtube",
            source_id="abc12345678",
        ),
    )
    collection = CollectionMetadata(
        title="A Playlist",
        source="youtube",
        source_url="https://www.youtube.com/playlist?list=PL123",
        items=requests,
    )
    importer = FakeTrackImporter()
    discovery_events: list[str] = []
    provider = FakeCollectionProvider(collection, discovery_events)
    service = BatchDownloadService(importer, collection_providers=(provider,))

    result = service.import_collection(collection.source_url, BatchOptions(max_items=25))

    assert importer.events == ["preflight", requests[0].url]
    assert discovery_events == ["discover:25"]
    assert result.title == "A Playlist"
    assert result.imported_count == 1


def test_failed_items_create_only_a_lightweight_retry_file(tmp_path: Path) -> None:
    failed_url = "https://example.test/bad"
    successful_url = "https://example.test/good"
    importer = FakeTrackImporter({failed_url})
    service = BatchDownloadService(importer, retry_root=tmp_path / "SongDrop")

    result = service.import_requests(
        (
            TrackRequest(url=failed_url, source="test"),
            TrackRequest(url=successful_url, source="test"),
        ),
        BatchOptions(),
    )

    assert result.retry_file is not None
    assert result.retry_file.read_text(encoding="utf-8") == f"{failed_url}\n"


def test_user_cancellation_stops_batch_and_preserves_reported_path(tmp_path: Path) -> None:
    preserved = tmp_path / "partial.m4a"

    class CancelledImporter(FakeTrackImporter):
        def import_url(self, url: str) -> ImportResult:
            raise OperationCancelled("cancelled", preserved_path=preserved)

    service = BatchDownloadService(CancelledImporter(), retry_root=tmp_path)
    result = service.import_requests(
        (
            TrackRequest(url="https://example.test/one", source="test"),
            TrackRequest(url="https://example.test/two", source="test"),
        ),
        BatchOptions(),
    )

    assert len(result.items) == 1
    assert result.items[0].status is BatchStatus.FAILED
    assert result.items[0].preserved_path == preserved
    assert result.retry_file is not None
