"""Native Playwright freshness preflight tests."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from crawlee._types import BasicCrawlingContext
from crawlee._utils.http_freshness import FreshnessResult
from crawlee.crawlers import PlaywrightCrawler
from crawlee.errors import ContextPipelineInterruptedError


@pytest.mark.asyncio
async def test_unchanged_page_interrupts_before_browser_allocation() -> None:
    checker = MagicMock()
    checker.check = AsyncMock(return_value=FreshnessResult(changed=False, status_code=304))
    checker.mark_processed = AsyncMock()
    crawler = PlaywrightCrawler(http_freshness_checker=checker)
    context = MagicMock(spec=BasicCrawlingContext)
    context.request.url = 'https://example.com/'

    middleware = crawler._check_http_freshness(context)
    with pytest.raises(ContextPipelineInterruptedError, match='Unchanged'):
        await middleware.__anext__()
    checker.mark_processed.assert_not_awaited()


@pytest.mark.asyncio
async def test_changed_page_commits_only_after_success() -> None:
    result = FreshnessResult(changed=True, status_code=200, etag='"v2"')
    checker = MagicMock()
    checker.check = AsyncMock(return_value=result)
    checker.mark_processed = AsyncMock()
    crawler = PlaywrightCrawler(http_freshness_checker=checker)
    context = MagicMock(spec=BasicCrawlingContext)
    context.request.url = 'https://example.com/'

    middleware = crawler._check_http_freshness(context)
    assert await middleware.__anext__() is context
    with pytest.raises(StopAsyncIteration):
        await middleware.asend(None)
    checker.mark_processed.assert_awaited_once_with(context.request.url, result)

    checker.mark_processed.reset_mock()
    middleware = crawler._check_http_freshness(context)
    assert await middleware.__anext__() is context
    with pytest.raises(StopAsyncIteration):
        await middleware.asend(RuntimeError('handler failed'))
    checker.mark_processed.assert_not_awaited()
