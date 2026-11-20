"""Browser-backed incremental HTTP crawling regression test."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from crawlee._utils.http_freshness import HttpFreshnessChecker
from crawlee.configuration import Configuration
from crawlee.crawlers import PlaywrightCrawler, PlaywrightCrawlingContext, PlaywrightPreNavCrawlingContext
from crawlee.http_clients import HttpxHttpClient
from crawlee.storage_clients import FileSystemStorageClient, MemoryStorageClient
from crawlee.storages import KeyValueStore, RequestQueue
from tests.unit.crawlers._playwright.test_incremental_http_e2e import freshness_server  # noqa: F401

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.asyncio
async def test_real_browser_incremental_etag_round_trip(
    freshness_server: tuple[str, dict[str, object]],
    tmp_path: Path,  # noqa: F811
) -> None:
    """The handler runs for 200, skips 304 before page creation, then runs for a new ETag."""
    url, server_state = freshness_server
    configuration = Configuration(storage_dir=str(tmp_path), purge_on_start=False)
    storage_client = FileSystemStorageClient()
    handled_titles: list[str] = []
    page_allocations: list[int] = []

    async def crawl_once(run_number: int) -> None:
        store = await KeyValueStore.open(
            name='browser-freshness',
            configuration=configuration,
            storage_client=storage_client,
        )
        # Each run needs a fresh request queue; otherwise handled requests are deduplicated.
        queue = await RequestQueue.open(
            name=f'browser-freshness-run-{run_number}', storage_client=MemoryStorageClient()
        )
        crawler = PlaywrightCrawler(
            http_freshness_checker=HttpFreshnessChecker(HttpxHttpClient(), store),
            request_manager=queue,
            configuration=configuration,
            storage_client=storage_client,
            max_request_retries=0,
        )

        @crawler.pre_navigation_hook
        async def count_page(_context: PlaywrightPreNavCrawlingContext) -> None:
            page_allocations.append(run_number)

        @crawler.router.default_handler
        async def handle(context: PlaywrightCrawlingContext) -> None:
            handled_titles.append(await context.page.title())

        await crawler.run([url])

    await crawl_once(1)
    assert handled_titles == ['"v1"']
    assert page_allocations == [1]

    await crawl_once(2)
    assert handled_titles == ['"v1"']
    assert page_allocations == [1]
    requests = server_state['requests']
    assert isinstance(requests, list)
    assert requests[-1] == '"v1"'

    server_state['etag'] = '"v2"'
    await crawl_once(3)
    assert handled_titles == ['"v1"', '"v2"']
    assert page_allocations == [1, 3]
