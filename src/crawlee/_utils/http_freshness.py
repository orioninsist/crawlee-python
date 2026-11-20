"""Persistent HTTP freshness checks for incremental crawling.

A 304 response is only useful after a previous successful processing run.
Call `mark_processed` after the downstream handler has committed its output.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from crawlee.http_clients._base import HttpClient
    from crawlee.storages import KeyValueStore


@dataclass(frozen=True)
class FreshnessResult:
    """Outcome of a conditional HTTP GET."""

    changed: bool
    status_code: int
    etag: str | None = None
    last_modified: str | None = None


class HttpFreshnessChecker:
    """Check resource freshness with persisted ETag and Last-Modified validators.

    This component does not persist validators until the caller explicitly
    confirms that processing succeeded. It never interprets errors as unchanged.
    """

    def __init__(self, http_client: HttpClient, store: KeyValueStore, *, key_prefix: str = 'HTTP-FRESHNESS-') -> None:
        self._http_client = http_client
        self._store = store
        self._key_prefix = key_prefix

    def _key(self, url: str) -> str:
        from hashlib import sha256

        return self._key_prefix + sha256(url.encode('utf-8')).hexdigest()

    async def check(self, url: str) -> FreshnessResult:
        """Use conditional GET; 304 means unchanged only with a previously stored validator."""
        record = await self._store.get_value(self._key(url)) or {}
        headers: dict[str, str] = {}
        if record.get('etag'):
            headers['If-None-Match'] = record['etag']
        elif record.get('last_modified'):
            headers['If-Modified-Since'] = record['last_modified']

        response = await self._http_client.send_request(url, method='GET', headers=headers)
        status = response.status_code
        if status == 304:
            if not headers:
                raise ValueError(f'Unexpected 304 without a stored validator: {url}')
            return FreshnessResult(changed=False, status_code=status)
        if not 200 <= status < 300:
            raise ValueError(f'HTTP freshness check failed for {url}: {status}')

        response_headers = {str(k).lower(): str(v) for k, v in response.headers.items()}
        return FreshnessResult(
            changed=True,
            status_code=status,
            etag=response_headers.get('etag'),
            last_modified=response_headers.get('last-modified'),
        )

    async def mark_processed(self, url: str, result: FreshnessResult) -> None:
        """Commit validators only after the caller successfully processes the page."""
        if not result.changed or not 200 <= result.status_code < 300:
            raise ValueError('Only successfully processed 2xx responses can update validators')
        await self._store.set_value(
            self._key(url),
            {'etag': result.etag, 'last_modified': result.last_modified},
        )
