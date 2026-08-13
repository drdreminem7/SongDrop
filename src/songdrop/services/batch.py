"""Transient playlist and batch orchestration above the single-track pipeline."""

import logging
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from songdrop.config import SongDropConfig
from songdrop.exceptions import CollectionFailed, OperationCancelled, SongDropError
from songdrop.models import (
    BatchItemResult,
    BatchOptions,
    BatchResult,
    BatchStatus,
    ImportResult,
    TrackRequest,
)
from songdrop.providers import (
    default_collection_providers,
    default_providers,
    select_collection_provider,
)
from songdrop.providers.base import CollectionProvider, MediaProvider
from songdrop.services.downloader import DownloadService, build_download_service

BatchProgress = Callable[[int, int, BatchItemResult], None]
logger = logging.getLogger(__name__)


class TrackImporter(Protocol):
    """The existing one-track pipeline as consumed by batch orchestration."""

    def ensure_available(self) -> None: ...

    def import_url(self, url: str) -> ImportResult: ...


class BatchDownloadService:
    """Expand inputs, deduplicate one run, and isolate per-track failures."""

    def __init__(
        self,
        track_importer: TrackImporter,
        *,
        media_providers: tuple[MediaProvider, ...] | None = None,
        collection_providers: tuple[CollectionProvider, ...] | None = None,
        retry_root: Path | None = None,
    ) -> None:
        self.track_importer = track_importer
        self.media_providers = media_providers or default_providers()
        self.collection_providers = collection_providers or default_collection_providers()
        self.retry_root = retry_root.expanduser().resolve(strict=False) if retry_root else None

    def import_collection(
        self,
        url: str,
        options: BatchOptions,
        *,
        progress: BatchProgress | None = None,
    ) -> BatchResult:
        """Discover a collection and process its entries in provider order."""

        self.track_importer.ensure_available()
        provider = select_collection_provider(url, self.collection_providers)
        collection = provider.get_collection(url, max_items=options.max_items)
        return self.import_requests(
            collection.items,
            options,
            title=collection.title,
            progress=progress,
            preflight=False,
        )

    def import_file(
        self,
        path: Path,
        options: BatchOptions,
        *,
        progress: BatchProgress | None = None,
    ) -> BatchResult:
        """Read one URL per line and process the file without persisting history."""

        requests = self.requests_from_file(path, max_items=options.max_items)
        return self.import_requests(
            requests,
            options,
            title=path.name,
            progress=progress,
        )

    def requests_from_file(self, path: Path, *, max_items: int) -> tuple[TrackRequest, ...]:
        """Parse nonblank, non-comment URL lines from a UTF-8 text file."""

        source_path = path.expanduser().resolve(strict=False)
        try:
            lines = source_path.read_text(encoding="utf-8").splitlines()
        except OSError as error:
            raise CollectionFailed(f"Could not read batch file {source_path}: {error}") from error
        urls = [
            line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")
        ]
        if not urls:
            raise CollectionFailed("The batch file contains no URLs.")
        if len(urls) > max_items:
            raise CollectionFailed(
                f"The batch file contains {len(urls)} URLs, above the {max_items}-track "
                "safety limit.\nUse --max-items to explicitly allow a larger batch."
            )
        return tuple(self._request_for_url(url) for url in urls)

    def import_requests(
        self,
        requests: Iterable[TrackRequest],
        options: BatchOptions,
        *,
        title: str | None = None,
        progress: BatchProgress | None = None,
        preflight: bool = True,
    ) -> BatchResult:
        """Import sequentially so Music automation and shared rate limits remain ordered."""

        if preflight:
            self.track_importer.ensure_available()
        pending = tuple(requests)
        results: list[BatchItemResult] = []
        seen: set[tuple[str, str]] = set()
        for index, request in enumerate(pending, start=1):
            duplicate_key = _duplicate_key(request)
            if duplicate_key in seen:
                item = BatchItemResult(
                    request=request,
                    status=BatchStatus.SKIPPED,
                    message="Duplicate in this batch",
                )
            else:
                seen.add(duplicate_key)
                try:
                    item = self._import_one(request)
                except (OperationCancelled, KeyboardInterrupt) as error:
                    item = BatchItemResult(
                        request=request,
                        status=BatchStatus.FAILED,
                        message="Batch cancelled by user.",
                        preserved_path=(
                            error.preserved_path if isinstance(error, OperationCancelled) else None
                        ),
                    )
                    results.append(item)
                    if progress is not None:
                        progress(index, len(pending), item)
                    break
            results.append(item)
            if progress is not None:
                progress(index, len(pending), item)
            if options.fail_fast and item.status is BatchStatus.FAILED:
                break
        return BatchResult(
            title=title,
            items=tuple(results),
            retry_file=self._write_retry_file(results),
        )

    def _request_for_url(self, url: str) -> TrackRequest:
        source = "url"
        source_id = None
        for provider in self.media_providers:
            if provider.supports(url):
                source = provider.__class__.__name__.removesuffix("Provider").casefold()
                source_id = provider.source_id(url)
                break
        return TrackRequest(url=url, source=source, source_id=source_id)

    def _import_one(self, request: TrackRequest) -> BatchItemResult:
        try:
            result = self.track_importer.import_url(request.url)
        except OperationCancelled:
            raise
        except SongDropError as error:
            return BatchItemResult(
                request=request,
                status=BatchStatus.FAILED,
                message=str(error),
                preserved_path=error.preserved_path,
            )
        except Exception:
            return BatchItemResult(
                request=request,
                status=BatchStatus.FAILED,
                message="An unexpected error interrupted this item.",
            )
        if result.already_downloaded:
            return BatchItemResult(
                request=request,
                status=BatchStatus.SKIPPED,
                result=result,
                message="Already downloaded",
            )
        return BatchItemResult(
            request=request,
            status=BatchStatus.IMPORTED,
            result=result,
        )

    def _write_retry_file(self, results: list[BatchItemResult]) -> Path | None:
        """Persist only failed URLs so a partially successful operation can be retried."""

        failed_urls = [item.request.url for item in results if item.status is BatchStatus.FAILED]
        if not failed_urls or self.retry_root is None:
            return None
        retry_path = self.retry_root / f"retry-{uuid4().hex[:12]}.txt"
        try:
            self.retry_root.mkdir(parents=True, exist_ok=True)
            retry_path.write_text("\n".join(failed_urls) + "\n", encoding="utf-8")
        except OSError as error:
            logger.warning("Could not write batch retry file: %s", error)
            return None
        return retry_path


def _duplicate_key(request: TrackRequest) -> tuple[str, str]:
    if request.source_id:
        return request.source.casefold(), request.source_id
    return "url", request.url.strip().casefold()


def build_batch_service(config: SongDropConfig) -> BatchDownloadService:
    """Compose one shared single-track service for the entire transient batch."""

    track_service: DownloadService = build_download_service(config)
    return BatchDownloadService(
        track_service,
        media_providers=track_service.providers,
        retry_root=config.staging_dir,
    )
