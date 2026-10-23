"""Concurrency guard for the public /connectors hub page.

The page renderer shared one markdown.Markdown instance across every
request (the module-level ``_md`` in app/services/public_pages.py).
Markdown instances are not thread-safe, so 8 simultaneous requests to
/connectors corrupted the parser state and returned 500 for roughly half
of them. A fresh Markdown instance per conversion fixes it.

This test issues 8 concurrent requests to /connectors, repeated 5 times,
and asserts every response is 200. It fails on the base commit and passes
after the fix.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest


def _fetch_connectors(app) -> int:
    """One GET to /connectors through its own test client (one thread)."""
    with app.test_client() as client:
        return client.get("/connectors").status_code


@pytest.mark.parametrize("run", range(5))
def test_connectors_hub_stays_200_under_concurrent_requests(app, run):
    """8 simultaneous requests to /connectors all return 200, repeated 5 times."""
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(_fetch_connectors, app) for _ in range(8)]
        statuses = [future.result() for future in as_completed(futures)]
    assert all(status == 200 for status in statuses), statuses
