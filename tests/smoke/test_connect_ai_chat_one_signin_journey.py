"""Connecting from an AI chat takes one sign-in and ends on the allow page, then back at the client.

A signed-out person opens the authorise address their AI chat sends them to, signs in once (with the second
code where two-factor applies), sees the client name and what it asks for with the original request intact,
presses Allow and is sent to the client's redirect address with a code and the same state. A next that points
anywhere but the authorise step of the connector falls back to the dashboard. The answers the connector then
gives carry absolute addresses.
"""
import base64
import hashlib
import json
import os
import re
import urllib.parse

import pyotp
import pytest
import requests
from playwright.sync_api import expect

from tests.smoke.conftest import PASSWORD, boot_live_server
from tests.smoke.fresh_org import create_fresh_org

PAGE_TIMEOUT = 30000
CALLBACK = "http://client.example/callback"
STATE = "state-4711"
SHOTS = os.environ.get("MCPCONNECT_SHOTS", "")
VIEWPORTS = [("1440x900-light", {"width": 1440, "height": 900}, "light"),
             ("390x844-dark", {"width": 390, "height": 844}, "dark")]

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


@pytest.fixture(scope="module")
def server(request, ai_protocol_stub, app):
    """A server with the real administrator two-factor gate (the shared smoke server skips it)."""
    return boot_live_server(request, ai_protocol_stub, app, extra_env={"FLASK_CONFIG": "testing"})


def _client_request(server):
    from app import create_app
    from app.modules.oauth_provider.models import OAuthClient

    with create_app("testing").app_context():
        client = OAuthClient.register(client_name="Acme Assistant", redirect_uris=CALLBACK)
        client_id = client.client_id
    verifier = "v" * 64
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    query = urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": CALLBACK, "response_type": "code",
        "code_challenge": challenge, "code_challenge_method": "S256", "scope": "mcp:read",
        "resource": server.rstrip("/") + "/mcp", "state": STATE,
    })
    return {"client_id": client_id, "verifier": verifier, "path": "/oauth/authorize?" + query}


def _admin(*, enrolled):
    from app import create_app, db
    from app.models.user import User
    from app.services import mfa_service

    org = create_fresh_org("enterprise_architect", org_admin=True)
    email = org["emails"]["enterprise_architect"]
    secret = None
    if enrolled:
        with create_app("testing").app_context():
            user = User.query.filter_by(email=email).one()
            secret = mfa_service.generate_secret()
            user.mfa_secret, user.mfa_enabled = secret, True
            db.session.commit()
            db.session.remove()
    return email, secret


def _new_page(browser, viewport, scheme):
    context = browser.new_context(ignore_https_errors=True, viewport=viewport, color_scheme=scheme)
    page = context.new_page()
    captured = {}

    def callback(route, request):
        captured["url"] = request.url
        route.fulfill(status=200, content_type="text/plain", body="ok")

    page.route(CALLBACK + "**", callback)
    return page, captured


def _sign_in(page, base, email, path):
    page.goto(base + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert "/account/login" in page.url, page.url
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()


def _on_allow_page(page, req):
    expect(page.get_by_role("button", name=re.compile("^Allow", re.I))).to_be_visible(timeout=PAGE_TIMEOUT)
    assert page.url.split("?")[0].endswith("/oauth/authorize"), page.url
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(page.url).query)
    assert query["client_id"] == [req["client_id"]] and query["state"] == [STATE], page.url
    assert query["redirect_uri"] == [CALLBACK] and query["code_challenge_method"] == ["S256"]
    body = " ".join(page.inner_text("body").split())
    assert "Acme Assistant" in body, body[:300]
    assert re.search(r"read", body, re.I), body[:300]


def _press_allow(page, captured):
    page.get_by_role("button", name=re.compile("^Allow", re.I)).click()
    page.wait_for_function("true")
    for _ in range(60):
        if "url" in captured:
            break
        page.wait_for_timeout(250)
    assert "url" in captured, "the client's redirect address was never reached"
    got = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(captured["url"]).query))
    assert got.get("code") and got.get("state") == STATE, captured["url"]
    return got["code"]


def _frame(page, label):
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        page.screenshot(path=os.path.join(SHOTS, "a-allow-%s.png" % label))


@pytest.mark.parametrize("label,viewport,scheme", VIEWPORTS)
def test_one_sign_in_without_two_factor_ends_at_the_client(browser, server, label, viewport, scheme):
    req = _client_request(server)
    org = create_fresh_org("enterprise_architect")
    page, captured = _new_page(browser, viewport, scheme)
    _sign_in(page, server, org["emails"]["enterprise_architect"], req["path"])
    _on_allow_page(page, req)
    _frame(page, label)
    _press_allow(page, captured)


@pytest.mark.parametrize("label,viewport,scheme", VIEWPORTS)
def test_one_sign_in_with_the_second_code_ends_at_the_client(browser, server, label, viewport, scheme):
    req = _client_request(server)
    email, secret = _admin(enrolled=True)
    page, captured = _new_page(browser, viewport, scheme)
    _sign_in(page, server, email, req["path"])
    page.wait_for_url(re.compile("mfa-challenge"), timeout=PAGE_TIMEOUT)
    page.fill("input[name=code]", pyotp.TOTP(secret).now())
    page.locator("form button[type=submit]").first.click()
    _on_allow_page(page, req)
    code = _press_allow(page, captured)
    token = requests.post(server + "/oauth/token", timeout=20, data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": CALLBACK,
        "client_id": req["client_id"], "code_verifier": req["verifier"], "resource": server.rstrip("/") + "/mcp"})
    assert token.status_code == 200, token.text


@pytest.mark.parametrize("target", [
    "https://evil.example/steal", "//evil.example/steal", "/" + chr(92) + "evil.example",
    "/oauth/token", "/oauth/register", "/oauth/authorize/../token", "/oauth/%2e%2e/token",
    "/oauth/authorizer",
])
def test_a_next_outside_the_authorise_step_falls_back_to_the_dashboard(browser, server, target):
    org = create_fresh_org("enterprise_architect")
    page, _ = _new_page(browser, VIEWPORTS[0][1], "light")
    _sign_in(page, server, org["emails"]["enterprise_architect"], "/account/login?next=" + urllib.parse.quote(target, safe=""))
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)
    assert "evil.example" not in page.url and "/oauth/" not in page.url, page.url
    assert "/dashboard" in page.url, page.url


def test_answers_through_the_connector_carry_absolute_addresses(browser, server):
    """QA-040: an impact answer and a cited answer returned to the client open from outside the product."""
    from app import create_app, db
    from app.models import ArchiMateElement, ArchiMateRelationship

    req = _client_request(server)
    org = create_fresh_org("enterprise_architect")
    with create_app("testing").app_context():
        root = ArchiMateElement(name="Billing service", type="ApplicationComponent", layer="application",
                                organization_id=org["org_id"])
        leaf = ArchiMateElement(name="Invoice portal", type="ApplicationComponent", layer="application",
                                organization_id=org["org_id"])
        db.session.add_all([root, leaf])
        db.session.flush()
        db.session.add(ArchiMateRelationship(source_id=root.id, target_id=leaf.id, type="Serving",
                                             organization_id=org["org_id"]))
        db.session.commit()
        root_id = root.id
        db.session.remove()
    page, captured = _new_page(browser, VIEWPORTS[0][1], "light")
    _sign_in(page, server, org["emails"]["enterprise_architect"], req["path"])
    _on_allow_page(page, req)
    code = _press_allow(page, captured)
    token = requests.post(server + "/oauth/token", timeout=20, data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": CALLBACK,
        "client_id": req["client_id"], "code_verifier": req["verifier"], "resource": server.rstrip("/") + "/mcp"}).json()["access_token"]
    for tool, arguments in (("ask_impact", {"element_id": root_id}),
                            ("answer", {"question": "impact", "element_id": root_id})):
        resp = requests.post(server + "/mcp", timeout=30, headers={"Authorization": "Bearer " + token}, json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": arguments}})
        assert resp.status_code == 200, resp.text
        text = resp.json()["result"]["content"][0]["text"]
        data = json.loads(text)
        addresses = []

        def walk(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    if isinstance(value, str) and key.lower() in ("url", "link", "href") or (
                            isinstance(value, str) and key.lower().endswith(("_url", "_link", "_href"))):
                        addresses.append(value)
                    else:
                        walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(data)
        assert addresses, "%s returned no address to check: %s" % (tool, text[:300])
        assert all(a.startswith(server.rstrip("/") + "/") for a in addresses), (tool, addresses)
