"""The OAuth consent screen, in a real browser, as two real archetypes.

Signs in on the real login page (not a seeded session), lets the browser
actually render the consent screen the way an assistant's "connect your
workspace" flow would, clicks the real Allow/Deny buttons, and follows the
real redirect to a local listener that stands in for the client's callback —
the same thing Playwright + an authorization-code client both need to see
happen for the integration to be real rather than merely unit-tested.

NOT RUN HERE: this environment has no way to drive a browser against the
live_server fixture. Written to the same conventions as the rest of
tests/smoke/ (ARCHETYPES, PASSWORD, live_server, seeded) so it runs wherever
the existing suite already does.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import secrets
import threading
import urllib.parse

import pytest
import requests

from .conftest import ARCHETYPES, PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


class _CallbackCapture(http.server.BaseHTTPRequestHandler):
    """A throwaway HTTP server standing in for the OAuth client's redirect_uri."""

    captured: dict | None = None

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's naming
        parsed = urllib.parse.urlparse(self.path)
        # Only the redirect target counts: a browser also asks for /favicon.ico,
        # and that request must not overwrite the captured redirect.
        if parsed.path == "/callback":
            _CallbackCapture.captured = dict(urllib.parse.parse_qsl(parsed.query))
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *_args):  # silence default stderr logging
        pass


@pytest.fixture
def local_callback_server():
    """A real local HTTP listener the browser can actually redirect to."""
    _CallbackCapture.captured = None
    server = http.server.HTTPServer(("127.0.0.1", 0), _CallbackCapture)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/callback"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _register_test_client(redirect_uri: str, live_server: str) -> str:
    """Register through the real POST /oauth/register endpoint (RFC 7591),
    not by calling the model directly — this is the same path a real
    assistant backend uses to onboard itself before ever reaching consent."""
    resp = requests.post(
        live_server + "/oauth/register",
        json={"redirect_uris": [redirect_uri], "client_name": "Smoke Test Assistant"},
        timeout=10,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["client_id"]


def _login(page, base, email):
    """Sign in the way a user does, and wait for the URL to change."""
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    try:
        page.click("#submit", no_wait_after=True)
    except TypeError:
        page.locator("#submit").click()
    try:
        page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)
    except Exception:
        pass
    page.wait_for_timeout(800)
    assert "/account/login" not in page.url, "could not sign in as %s" % email


def _authorize_url(live_server, client_id, redirect_uri, challenge, scope):
    resource = live_server + "/mcp"
    return live_server + "/oauth/authorize?" + urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "scope": scope,
        "resource": resource,
    })


def test_allow_reaches_local_listener_and_exchanges_for_a_token(
    page, live_server, seeded, local_callback_server
):
    """solution_architect: sign in, see consent, click Allow, redeem the code."""
    client_id = _register_test_client(local_callback_server, live_server)
    verifier, challenge = _pkce_pair()

    _login(page, live_server, seeded["emails"]["solution_architect"])
    page.goto(
        _authorize_url(live_server, client_id, local_callback_server, challenge, "mcp:read mcp:propose"),
        wait_until="domcontentloaded", timeout=PAGE_TIMEOUT,
    )

    assert page.locator("text=Authorize access").count() > 0
    assert page.locator("text=Propose changes").count() > 0, (
        "solution_architect has general write permission and asked for "
        "mcp:propose — the consent screen must offer it"
    )

    page.click('button[name="decision"][value="allow"]')
    page.wait_for_timeout(1500)

    assert _CallbackCapture.captured is not None, "local listener never received the redirect"
    assert "code" in _CallbackCapture.captured, "redirect carried no authorization code"

    resp = requests.post(
        live_server + "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": _CallbackCapture.captured["code"],
            "redirect_uri": local_callback_server,
            "client_id": client_id,
            "code_verifier": verifier,
            "resource": live_server + "/mcp",
        },
        timeout=10,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "access_token" in body
    assert body["token_type"] == "Bearer"


def test_read_only_archetype_is_not_offered_propose_changes(
    page, live_server, seeded, local_callback_server
):
    """procurement: a read-only integration is never shown the write scope.

    The assistant backend for a read-only integration requests only
    mcp:read — it never asks for mcp:propose in the first place, so the
    consent screen has nothing to offer regardless of the signed-in user's
    own permissions. This is the scope allow-list working as the client's
    own request shapes it, not a claim that this particular archetype lacks
    write permission on their account.
    """
    client_id = _register_test_client(local_callback_server, live_server)
    _verifier, challenge = _pkce_pair()

    _login(page, live_server, seeded["emails"]["procurement"])
    page.goto(
        _authorize_url(live_server, client_id, local_callback_server, challenge, "mcp:read"),
        wait_until="domcontentloaded", timeout=PAGE_TIMEOUT,
    )

    assert page.locator("text=Authorize access").count() > 0
    assert page.locator("text=Propose changes").count() == 0
    assert page.locator('button[name="decision"][value="deny"]').count() == 1


def test_deny_redirects_with_access_denied(page, live_server, seeded, local_callback_server):
    client_id = _register_test_client(local_callback_server, live_server)
    _verifier, challenge = _pkce_pair()

    _login(page, live_server, seeded["emails"]["business_architect"])
    page.goto(
        _authorize_url(live_server, client_id, local_callback_server, challenge, "mcp:read"),
        wait_until="domcontentloaded", timeout=PAGE_TIMEOUT,
    )
    page.click('button[name="decision"][value="deny"]')
    page.wait_for_timeout(1500)

    assert _CallbackCapture.captured is not None
    assert _CallbackCapture.captured.get("error") == "access_denied"
    assert "code" not in _CallbackCapture.captured
