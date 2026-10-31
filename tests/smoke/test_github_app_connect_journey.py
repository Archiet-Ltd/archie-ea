"""Connect GitHub in one click, through the Entelim GitHub App.

The administrator opens Connect GitHub, sees the one-click button as the primary
action, and is sent to ``https://github.com/apps/<slug>/installations/new`` with a
state this server signed. GitHub is never actually reached: this journey proves
the return leg only -- what happens at ``/admin/connectors/github/installed`` once
GitHub sends the person back -- because the install screen itself lives on
github.com and installing a real GitHub App is outside what a sandboxed browser
journey can do. Every call this server would make to GitHub's API is stubbed at
the client, inside the server subprocess, exactly as ``tests/test_github_connector.
py`` stubs it for the unit suite; no test here touches the network.

A forged state and a state issued for a different organisation are both refused
with a plain message, never a stack trace -- the isolation the brief requires.
"""

from __future__ import annotations

import json
import os
import re
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PAGE_TIMEOUT, boot_live_server
from tests.smoke.fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SKIP_WELCOME = "localStorage.setItem('archie_onboarding_ts', Date.now().toString());"
INSTALLATION_ID = "777"
MOCK_INSTALLATION_TOKEN = "ghs_journey_mock_installation_token"
SHOTS = os.environ.get("JOB0_JOURNEY_SHOTS")

# Runs inside the server subprocess, before the application: every request to
# api.github.com is answered from here, never from the real network. The three
# GitHub App endpoints are canned directly; the organisation's repositories reuse
# the same recorded fixture the unit suite reads (tests/fixtures/github_acme_org.json).
_SITECUSTOMIZE = '''
def _install():
    import json
    import os

    import requests

    fixture_path = os.path.join(os.path.dirname(__file__), "github_acme_org_copy.json")
    with open(fixture_path) as handle:
        bundle = dict(json.load(handle)["responses"])
    bundle["users/acme"] = {"type": "Organization", "login": "acme"}
    bundle["app/installations/777"] = {"account": {"login": "acme"}, "suspended_at": None}
    bundle["app/installations/777/access_tokens"] = {"token": "ghs_journey_mock_installation_token"}
    bundle["installation/repositories"] = {"total_count": 12}
    # Who completed the install: the code names the GitHub person, and that person's
    # token sees installation 777 or only somebody else's.
    tokens = {"code-sees": "ghu_sees_777", "code-blind": "ghu_sees_999"}
    visible = {"ghu_sees_777": [777], "ghu_sees_999": [999]}

    original = requests.Session.request

    class _Resp:
        def __init__(self, status, body):
            self.status_code = status
            self._body = body
            self.headers = {"X-RateLimit-Remaining": "4990"}
            self.reason = "OK" if status < 300 else "Error"
            self.text = json.dumps(body)
            self.is_redirect = False

        def json(self):
            return self._body

    def dispatch(self, method=None, url=None, params=None, headers=None, **kw):
        if url == "https://github.com/login/oauth/access_token":
            token = tokens.get((kw.get("json") or {}).get("code"))
            return _Resp(200, {"access_token": token} if token else {"error": "bad_verification_code"})
        if not (isinstance(url, str) and url.startswith("https://api.github.com/")):
            return original(self, method, url, params=params, headers=headers, **kw)
        if url == "https://api.github.com/user/installations":
            bearer = ((headers or {}).get("Authorization") or "").replace("Bearer ", "")
            ids = visible.get(bearer, [])
            return _Resp(200, {"total_count": len(ids), "installations": [{"id": i} for i in ids]})
        path = url[len("https://api.github.com/"):].strip("/")
        if path not in bundle:
            return _Resp(404, {"message": "Not Found"})
        body = bundle[path]
        params = dict(params or {})
        if path.endswith("/repos") or path.endswith("/teams"):
            page = int(params.get("page", 1))
            per_page = int(params.get("per_page", 30))
            body = body[(page - 1) * per_page : page * per_page]
        return _Resp(200, body)

    requests.Session.request = dispatch


_install()
'''


@pytest.fixture(scope="module")
def private_key_path(tmp_path_factory):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    path = tmp_path_factory.mktemp("github_app_key") / "github-app-private-key.pem"
    path.write_text(pem.decode("utf-8"))
    return str(path)


@pytest.fixture(scope="module")
def github_app_server(request, app, tmp_path_factory, private_key_path):
    """A second server configured with the GitHub App settings, whose GitHub calls
    are all stubbed (the App endpoints directly, the organisation's repositories
    from the recorded fixture)."""
    folder = tmp_path_factory.mktemp("github_app_peer")
    (folder / "sitecustomize.py").write_text(_SITECUSTOMIZE)
    src = os.path.join(os.path.dirname(__file__), "..", "fixtures", "github_acme_org.json")
    with open(src) as handle:
        (folder / "github_acme_org_copy.json").write_text(handle.read())
    path = os.pathsep.join(p for p in (str(folder), os.environ.get("PYTHONPATH", "")) if p)
    return boot_live_server(
        request,
        None,
        app,
        extra_env={
            "PYTHONPATH": path,
            "ALLOW_EXTERNAL_API_CALLS": "true",
            "GITHUB_APP_ID": "5534377",
            "GITHUB_APP_SLUG": "entelim",
            "GITHUB_APP_PRIVATE_KEY_PATH": private_key_path,
            "GITHUB_APP_CLIENT_ID": "Iv1.journeyclientid",
            "GITHUB_APP_CLIENT_SECRET": "journey-client-secret-not-real",
        },
    )


@pytest.fixture(scope="module")
def world(github_app_server, app):
    one = create_fresh_org("solution_architect", org_admin=True)
    two = create_fresh_org("solution_architect", org_admin=True)
    yield {"base": str(github_app_server), "one": one, "two": two}
    remove_organisations(app, [one["org_id"], two["org_id"]])


def remove_organisations(app, org_ids):
    """Remove the journey's organisations through the deletion service, the same path the
    organisation delete journey uses; no row is removed by hand and no trigger is disabled."""
    from app import db
    from app.models.organization import Organization
    from app.services.org_deletion_service import OrganizationDeletionService

    with app.app_context():
        for org_id in org_ids:
            db.session.remove()
            if db.session.get(Organization, org_id) is not None:
                OrganizationDeletionService.delete_organization(org_id)
        db.session.remove()


def _page(browser, color_scheme="light", width=1440, height=900):
    context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=color_scheme)
    context.add_init_script(SKIP_WELCOME)
    context.set_default_timeout(PAGE_TIMEOUT)
    context.set_default_navigation_timeout(PAGE_TIMEOUT)
    return context, context.new_page()


def shoot(page, name, width, color_scheme):
    if not SHOTS:
        return
    os.makedirs(SHOTS, exist_ok=True)
    page.screenshot(path=os.path.join(SHOTS, "%s-%d-%s.png" % (name, width, color_scheme)), full_page=True)


def _install_url_and_state(page, base):
    page.goto(base + "/admin/connectors/github", wait_until="domcontentloaded")
    link = page.get_by_test_id("github-app-connect-link")
    expect(link).to_be_visible()
    expect(link).to_have_text(re.compile("Connect GitHub"))
    href = link.get_attribute("href")
    assert href.startswith("https://github.com/apps/entelim/installations/new?state="), href
    state = parse_qs(urlparse(href).query)["state"][0]
    return state


# --------------------------------------------------------------------------------------------- #
# The journey, in order                                                                          #
# --------------------------------------------------------------------------------------------- #


def test_1_connect_github_is_the_primary_one_click_button(world, browser):
    context, page = _page(browser)
    try:
        sign_in(page, world["base"], world["one"]["emails"]["solution_architect"])
        state = _install_url_and_state(page, world["base"])
        world["state_one"] = state
        # The token form is still there, now framed as the second option.
        expect(page.get_by_text("Or paste a read-only token:", exact=False)).to_be_visible()
        expect(page.get_by_label("Read-only token")).to_be_visible()
        shoot(page, "1-connect-github-app", 1440, "light")
    finally:
        context.close()
    context, page = _page(browser, color_scheme="dark", width=390, height=844)
    try:
        sign_in(page, world["base"], world["one"]["emails"]["solution_architect"])
        page.goto(world["base"] + "/admin/connectors/github", wait_until="domcontentloaded")
        expect(page.get_by_test_id("github-app-connect-link")).to_be_visible()
        shoot(page, "1-connect-github-app", 390, "dark")
    finally:
        context.close()


def test_2_a_forged_state_is_refused_with_a_plain_message(world, browser):
    context, page = _page(browser)
    try:
        sign_in(page, world["base"], world["one"]["emails"]["solution_architect"])
        tampered = world["state_one"][:-2] + ("zz" if world["state_one"][-2:] != "zz" else "aa")
        page.goto(
            world["base"] + "/admin/connectors/github/installed?installation_id=%s&setup_action=install&state=%s"
            % (INSTALLATION_ID, tampered),
            wait_until="domcontentloaded",
        )
        expect(page.locator("#main-content").get_by_text("not valid", exact=False)).to_be_visible()
        expect(page.get_by_test_id("github-progress")).to_have_count(0)
    finally:
        context.close()


def test_3_returning_through_installed_starts_the_first_read(world, browser):
    context, page = _page(browser)
    try:
        sign_in(page, world["base"], world["one"]["emails"]["solution_architect"])
        page.goto(
            world["base"] + "/admin/connectors/github/installed?installation_id=%s&setup_action=install&state=%s&code=code-sees"
            % (INSTALLATION_ID, world["state_one"]),
            wait_until="domcontentloaded",
        )
        expect(page.locator("#main-content").get_by_text("Connected through the GitHub App", exact=False)).to_be_visible()
        expect(page.get_by_test_id("github-progress")).to_be_visible()
        line = page.locator("#github-progress-line")
        expect(line).to_have_text(re.compile(r"Read \d+ repositories\. \d+ proposals are ready"), timeout=PAGE_TIMEOUT)
        shoot(page, "3-read-started", 1440, "light")
    finally:
        context.close()


def test_4_the_review_queue_shows_proposals(world, browser):
    context, page = _page(browser, color_scheme="dark", width=390, height=844)
    try:
        sign_in(page, world["base"], world["one"]["emails"]["solution_architect"])
        page.goto(world["base"] + "/admin/connectors/github/review", wait_until="domcontentloaded")
        expect(page.get_by_role("heading", name="Review what GitHub showed")).to_be_visible()
        rows = page.locator("table tbody tr")
        assert rows.count() > 0, "the review queue shows no proposals after the GitHub App read"
        shoot(page, "4-review-queue", 390, "dark")
    finally:
        context.close()


def test_5_another_organisation_cannot_attach_the_installation(world, browser):
    """Isolation: organisation B signs in and visits the exact URL GitHub sent
    organisation A back to. The state names A, so B is refused -- never attached."""
    context, page = _page(browser)
    try:
        sign_in(page, world["base"], world["two"]["emails"]["solution_architect"])
        page.goto(
            world["base"] + "/admin/connectors/github/installed?installation_id=%s&setup_action=install&state=%s"
            % (INSTALLATION_ID, world["state_one"]),
            wait_until="domcontentloaded",
        )
        expect(page.locator("#main-content").get_by_text("different organisation", exact=False)).to_be_visible()
        # Organisation B's own connect page shows no connection at all.
        page.goto(world["base"] + "/admin/connectors/github", wait_until="domcontentloaded")
        expect(page.get_by_test_id("github-progress")).to_have_count(0)
    finally:
        context.close()


def test_5b_organisation_b_cannot_swap_in_an_installation_its_github_user_cannot_see(world, browser):
    """The swap: organisation B starts its own install (a valid state for B) and puts
    installation 777 -- organisation A's -- in the address. B's GitHub user does not see
    777, so it is refused with a plain message and B's page shows no connection."""
    context, page = _page(browser)
    try:
        sign_in(page, world["base"], world["two"]["emails"]["solution_architect"])
        state_two = _install_url_and_state(page, world["base"])
        page.goto(
            world["base"] + "/admin/connectors/github/installed?installation_id=%s&setup_action=install&state=%s&code=code-blind"
            % (INSTALLATION_ID, state_two),
            wait_until="domcontentloaded",
        )
        expect(page.locator("#main-content").get_by_text("can't see that GitHub installation", exact=False)).to_be_visible()
        expect(page.get_by_test_id("github-progress")).to_have_count(0)
        page.goto(world["base"] + "/admin/connectors/github", wait_until="domcontentloaded")
        expect(page.get_by_test_id("github-progress")).to_have_count(0)
    finally:
        context.close()


def test_5c_an_installation_already_connected_elsewhere_is_refused(world, browser):
    """Organisation A connected installation 777 in step 3. Organisation B, whose GitHub
    user can also see it, still cannot attach the same installation."""
    context, page = _page(browser)
    try:
        sign_in(page, world["base"], world["two"]["emails"]["solution_architect"])
        state_two = _install_url_and_state(page, world["base"])
        page.goto(
            world["base"] + "/admin/connectors/github/installed?installation_id=%s&setup_action=install&state=%s&code=code-sees"
            % (INSTALLATION_ID, state_two),
            wait_until="domcontentloaded",
        )
        expect(page.locator("#main-content").get_by_text("already connected to another organisation", exact=False)).to_be_visible()
        expect(page.get_by_test_id("github-progress")).to_have_count(0)
    finally:
        context.close()


@pytest.fixture(scope="module")
def unconfigured_server(request, app):
    """A second server with none of the three GitHub App settings set -- the
    ordinary, unmodified connect page."""
    return boot_live_server(request, None, app)


@pytest.fixture(scope="module")
def unconfigured_world(unconfigured_server, app):
    org = create_fresh_org("solution_architect", org_admin=True)
    yield {"base": str(unconfigured_server), "org": org}
    remove_organisations(app, [org["org_id"]])


def test_6_with_the_settings_unset_the_page_is_exactly_the_token_form(unconfigured_world, browser):
    """With the three settings unset, the connect page shows no one-click button
    at all: byte-for-byte today's token form, the acceptance criterion this
    journey module also carries."""
    context, page = _page(browser)
    try:
        sign_in(page, unconfigured_world["base"], unconfigured_world["org"]["emails"]["solution_architect"])
        page.goto(unconfigured_world["base"] + "/admin/connectors/github", wait_until="domcontentloaded")
        expect(page.get_by_test_id("github-app-connect-link")).to_have_count(0)
        expect(page.get_by_test_id("github-app-connect")).to_have_count(0)
        expect(page.get_by_text("Or paste a read-only token:", exact=False)).to_have_count(0)
        expect(page.get_by_role("heading", name="Connect GitHub", exact=True)).to_be_visible()
        expect(page.get_by_label("GitHub organisation")).to_be_visible()
        expect(page.get_by_label("Read-only token")).to_be_visible()
        expect(page.get_by_role("button", name="Connect and read my repositories")).to_be_visible()
        shoot(page, "6-token-form-only", 1440, "light")
    finally:
        context.close()
    context, page = _page(browser, color_scheme="dark", width=390, height=844)
    try:
        sign_in(page, unconfigured_world["base"], unconfigured_world["org"]["emails"]["solution_architect"])
        page.goto(unconfigured_world["base"] + "/admin/connectors/github", wait_until="domcontentloaded")
        expect(page.get_by_test_id("github-app-connect-link")).to_have_count(0)
        shoot(page, "6-token-form-only", 390, "dark")
    finally:
        context.close()


@pytest.fixture(scope="module")
def three_settings_server(request, app, private_key_path):
    """The three settings the one-click button needed before this round, without the
    client id and secret: the server cannot ask GitHub who is installing, so it offers
    no one-click path."""
    return boot_live_server(
        request,
        None,
        app,
        extra_env={
            "GITHUB_APP_ID": "5534377",
            "GITHUB_APP_SLUG": "entelim",
            "GITHUB_APP_PRIVATE_KEY_PATH": private_key_path,
            "GITHUB_APP_CLIENT_ID": "",
            "GITHUB_APP_CLIENT_SECRET": "",
        },
    )


def test_7_without_the_client_id_and_secret_the_page_is_exactly_the_token_form(three_settings_server, app, browser):
    org = create_fresh_org("solution_architect", org_admin=True)
    context, page = _page(browser)
    try:
        sign_in(page, str(three_settings_server), org["emails"]["solution_architect"])
        page.goto(str(three_settings_server) + "/admin/connectors/github", wait_until="domcontentloaded")
        expect(page.get_by_test_id("github-app-connect-link")).to_have_count(0)
        expect(page.get_by_text("Or paste a read-only token:", exact=False)).to_have_count(0)
        expect(page.get_by_label("Read-only token")).to_be_visible()
        expect(page.get_by_role("button", name="Connect and read my repositories")).to_be_visible()
    finally:
        context.close()
        remove_organisations(app, [org["org_id"]])
