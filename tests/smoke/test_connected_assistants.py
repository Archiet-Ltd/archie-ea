"""Connected assistants, in a real browser: connect one, see it, disconnect it.

A person authorises an assistant on the consent screen, finds it listed under
account settings, clicks Disconnect, and the assistant's token stops working
(and stays stopped after a reload). Written to the conventions of
test_mcp_consent.py, whose helpers it reuses.
"""

from __future__ import annotations

import urllib.parse

import pytest
import requests

from .conftest import PAGE_TIMEOUT
from .test_mcp_consent import (
    _CallbackCapture,
    _authorize_url,
    _login,
    _pkce_pair,
    _register_test_client,
    local_callback_server,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _mcp_status(live_server, access_token):
    resp = requests.post(
        live_server + "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Authorization": "Bearer " + access_token},
        timeout=10,
    )
    return resp.status_code


def test_person_disconnects_an_assistant_and_it_stays_disconnected(
    page, live_server, seeded, local_callback_server  # noqa: F811
):
    client_id = _register_test_client(local_callback_server, live_server)
    verifier, challenge = _pkce_pair()

    _login(page, live_server, seeded["emails"]["solution_architect"])
    page.goto(
        _authorize_url(live_server, client_id, local_callback_server, challenge, "mcp:read"),
        wait_until="domcontentloaded", timeout=PAGE_TIMEOUT,
    )
    assert page.locator("text=your account settings").count() > 0
    page.click('button[name="decision"][value="allow"]')
    page.wait_for_url(lambda url: "code=" in url, timeout=PAGE_TIMEOUT)
    code = urllib.parse.parse_qs(urllib.parse.urlparse(page.url).query)["code"][0]

    token = requests.post(
        live_server + "/oauth/token",
        data={
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": local_callback_server, "client_id": client_id,
            "code_verifier": verifier, "resource": live_server + "/mcp",
        },
        timeout=10,
    ).json()
    assert _mcp_status(live_server, token["access_token"]) == 200

    page.goto(live_server + "/account/manage/connected-assistants", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert page.locator('[data-testid="connected-assistant-row"]').count() >= 1
    assert page.locator("text=Smoke Test Assistant").count() >= 1

    page.locator('[data-testid="connected-assistant-row"] button:has-text("Disconnect")').first.click()
    page.wait_for_load_state("domcontentloaded")

    assert _mcp_status(live_server, token["access_token"]) == 401
    page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert page.locator("text=Smoke Test Assistant").count() == 0
    assert page.locator('[data-testid="connected-assistants-empty"]').count() == 1
