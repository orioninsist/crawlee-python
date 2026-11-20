"""Real conditional HTTP and disk persistence tests for incremental crawling."""

from __future__ import annotations

import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from crawlee import Request
from crawlee._types import BasicCrawlingContext
from crawlee._utils.http_freshness import HttpFreshnessChecker
from crawlee.configuration import Configuration
from crawlee.crawlers import PlaywrightCrawler
from crawlee.errors import ContextPipelineInterruptedError
from crawlee.http_clients import HttpxHttpClient
from crawlee.sessions import Session
from crawlee.storage_clients import FileSystemStorageClient
from crawlee.storages import KeyValueStore

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path


@pytest.fixture
def freshness_server() -> Generator[tuple[str, dict[str, object]], None, None]:
    """Serve real 200/304 responses while recording conditional headers."""
    state: dict[str, object] = {'etag': '"v1"', 'requests': []}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            requests = state['requests']
            assert isinstance(requests, list)
            requests.append(self.headers.get('If-None-Match'))
            etag = str(state['etag'])
            if self.headers.get('If-None-Match') == etag:
                self.send_response(HTTPStatus.NOT_MODIFIED)
                self.send_header('ETag', etag)
                self.end_headers()
                return

            body = f'<html><title>{etag}</title></html>'.encode()
            self.send_response(HTTPStatus.OK)
            self.send_header('ETag', etag)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *args: object) -> None:
            """Silence fixture requests."""

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/page', state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def make_context(url: str) -> BasicCrawlingContext:
    """Create an actual context for the full composed Playwright pipeline."""
    return BasicCrawlingContext(
        request=Request.from_url(url),
        send_request=AsyncMock(),
        add_requests=AsyncMock(),
        session=Session(),
        proxy_info=None,
        push_data=AsyncMock(),
        use_state=AsyncMock(),
        get_key_value_store=AsyncMock(),
        log=logging.getLogger(__name__),
        register_deferred_cleanup=lambda _: None,
    )


@pytest.mark.asyncio
async def test_real_http_validators_survive_reopened_file_store(
    freshness_server: tuple[str, dict[str, object]], tmp_path: Path
) -> None:
    """200 -> committed ETag -> 304 -> changed ETag, including failed processing."""
    url, state = freshness_server
    configuration = Configuration(storage_dir=str(tmp_path), purge_on_start=False)

    async def new_checker() -> HttpFreshnessChecker:
        store = await KeyValueStore.open(
            name='incremental-freshness-e2e',
            configuration=configuration,
            storage_client=FileSystemStorageClient(),
        )
        return HttpFreshnessChecker(HttpxHttpClient(), store)

    first_checker = await new_checker()
    first = await first_checker.check(url)
    assert first.changed
    assert first.status_code == HTTPStatus.OK
    assert first.etag == '"v1"'
    assert state['requests'] == [None]

    # No successful processing yet: a new checker must still fetch unconditionally.
    failed_checker = await new_checker()
    failed_retry = await failed_checker.check(url)
    assert failed_retry.changed
    assert state['requests'] == [None, None]

    await first_checker.mark_processed(url, first)
    second_checker = await new_checker()
    unchanged = await second_checker.check(url)
    assert unchanged.status_code == HTTPStatus.NOT_MODIFIED
    assert not unchanged.changed
    assert state['requests'] == [None, None, '"v1"']

    state['etag'] = '"v2"'
    updated_checker = await new_checker()
    updated = await updated_checker.check(url)
    assert updated.changed
    assert updated.etag == '"v2"'
    assert state['requests'] == [None, None, '"v1"', '"v1"']

    # A failed processing run does not replace the previous successful validator.
    retry_checker = await new_checker()
    retry = await retry_checker.check(url)
    assert retry.changed
    assert retry.etag == '"v2"'
    assert state['requests'] == [None, None, '"v1"', '"v1"', '"v1"']

    await updated_checker.mark_processed(url, updated)
    final_checker = await new_checker()
    final_result = await final_checker.check(url)
    assert not final_result.changed
    assert state['requests'] == [None, None, '"v1"', '"v1"', '"v1"', '"v2"']


@pytest.mark.asyncio
async def test_full_pipeline_304_skips_browser_allocation_and_handler() -> None:
    """A composed Playwright pipeline must never reach BrowserPool on 304."""
    url = 'https://example.com/page'
    checker = MagicMock()
    checker.check = AsyncMock(return_value=MagicMock(changed=False))
    checker.mark_processed = AsyncMock()
    pool = MagicMock()
    pool.new_page = AsyncMock(side_effect=AssertionError('Browser allocation was attempted'))
    crawler = PlaywrightCrawler(browser_pool=pool, http_freshness_checker=checker)
    handler = AsyncMock()

    with pytest.raises(ContextPipelineInterruptedError, match='Unchanged'):
        await crawler._context_pipeline(make_context(url), handler)

    pool.new_page.assert_not_awaited()
    handler.assert_not_awaited()
    checker.check.assert_awaited_once_with(url)
    checker.mark_processed.assert_not_awaited()
