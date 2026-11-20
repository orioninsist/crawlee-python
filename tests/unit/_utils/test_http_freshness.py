"""Tests for persisted conditional HTTP validators."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from crawlee._utils.http_freshness import HttpFreshnessChecker


@pytest.mark.asyncio
async def test_conditional_get_persists_only_after_successful_processing() -> None:
    records: dict[str, dict[str, str | None]] = {}
    store = MagicMock()
    store.get_value = AsyncMock(side_effect=lambda key: records.get(key))

    async def save(key: str, value: dict[str, str | None]) -> None:
        records[key] = value

    store.set_value = AsyncMock(side_effect=save)
    client = MagicMock()
    response = MagicMock(status_code=200, headers={'ETag': '"v1"', 'Last-Modified': 'Wed, 21 Oct 2015 07:28:00 GMT'})
    client.send_request = AsyncMock(return_value=response)
    checker = HttpFreshnessChecker(client, store)
    url = 'https://example.com/page'

    first = await checker.check(url)
    assert first.changed
    assert records == {}
    await checker.mark_processed(url, first)

    client.send_request.return_value = MagicMock(status_code=304, headers={})
    second = await checker.check(url)
    assert not second.changed
    assert second.status_code == 304
    assert client.send_request.await_args.kwargs['headers'] == {'If-None-Match': '"v1"'}
    assert len(records) == 1


@pytest.mark.asyncio
async def test_last_modified_fallback_and_errors() -> None:
    store = MagicMock()
    store.get_value = AsyncMock(return_value={'last_modified': 'Wed, 21 Oct 2015 07:28:00 GMT'})
    store.set_value = AsyncMock()
    client = MagicMock()
    client.send_request = AsyncMock(return_value=MagicMock(status_code=304, headers={}))
    checker = HttpFreshnessChecker(client, store)

    result = await checker.check('https://example.com/')
    assert not result.changed
    assert client.send_request.await_args.kwargs['headers'] == {
        'If-Modified-Since': 'Wed, 21 Oct 2015 07:28:00 GMT'
    }

    client.send_request.return_value = MagicMock(status_code=503, headers={})
    with pytest.raises(ValueError, match='503'):
        await checker.check('https://example.com/')
    store.set_value.assert_not_awaited()


@pytest.mark.asyncio
async def test_unexpected_304_is_not_treated_as_unchanged() -> None:
    store = MagicMock()
    store.get_value = AsyncMock(return_value=None)
    client = MagicMock()
    client.send_request = AsyncMock(return_value=MagicMock(status_code=304, headers={}))
    checker = HttpFreshnessChecker(client, store)
    with pytest.raises(ValueError, match='Unexpected 304'):
        await checker.check('https://example.com/')
