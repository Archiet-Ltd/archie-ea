"""Browser-level proof that /intelligence/history/changes answers a normal
page (not a raw JSON 400) when called without parameters, showing the shared
not_available block with its next step instead.
"""

from __future__ import annotations

import pytest

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def test_history_changes_without_parameters_renders_a_page_not_json(
    browser, live_server, seeded
):
    page = browser.new_page()
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        response = page.goto(
            live_server + "/intelligence/history/changes",
            wait_until="networkidle",
            timeout=PAGE_TIMEOUT,
        )
        print("\n[debug] url:", page.url)
        print("[debug] status:", response.status if response else None)
        content = page.content()
        print("[debug] has Next step in content:", "Next step:" in content)
        print("[debug] has MISSING_PARAMETER:", "MISSING_PARAMETER" in content)
        assert response is not None
        assert response.status == 200, response.status
        content_type = response.headers.get("content-type") or ""
        assert "application/json" not in content_type
        assert "Next step:" in content
        assert "MISSING_PARAMETER" not in content
    finally:
        page.close()
