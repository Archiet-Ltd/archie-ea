"""A platform admin opens a connector's sync history and tests the GitHub connection from it.

The goal: from the connectors page, open a connector, click "Test Connection" inside the history
dialog and read whether the token reaches GitHub; do the same from the card's "Test" button. The
dialog used to sit under its own backdrop, so any click inside it closed it without acting, and
"Test" answered with an internal error instead of a reason.

GitHub is never called: the server under test answers api.github.com from a stub, with one
account that exists and one that does not. A second organisation owns a connector that must not
appear.
"""

import os
import uuid

import pytest
from cryptography.fernet import Fernet
from playwright.sync_api import expect

from tests.smoke.conftest import PAGE_TIMEOUT, PASSWORD, boot_live_server

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = os.path.join("test-results", "connector-dialog")
# The server and the seeding process must encrypt and decrypt the stored token with one key.
MASTER_KEY = Fernet.generate_key().decode()

_SITECUSTOMIZE = '''
def _install():
    import json
    import requests

    original = requests.Session.request
    API = "https://api.github.com/"

    class _Resp:
        def __init__(self, status, body):
            self.status_code = status
            self._body = body
            self.headers = {"X-RateLimit-Remaining": "4990"}
            self.reason = {200: "OK", 401: "Unauthorized", 404: "Not Found"}.get(status, "Status")
            self.text = json.dumps(body)
            self.is_redirect = False

        def json(self):
            return self._body

    def dispatch(self, method=None, url=None, params=None, headers=None, **kw):
        if isinstance(url, str) and url.startswith(API):
            path = url[len(API):].strip("/")
            if path == "users/octocat":
                return _Resp(200, {"type": "User", "login": "octocat"})
            if path == "users/revoked":
                return _Resp(401, {"message": "Bad credentials"})
            return _Resp(404, {"message": "Not Found"})
        return original(self, method, url, params, headers, **kw)

    requests.Session.request = dispatch


_install()
'''


@pytest.fixture(scope="module")
def github_server(request, app, tmp_path_factory):
    folder = tmp_path_factory.mktemp("github_dialog")
    (folder / "sitecustomize.py").write_text(_SITECUSTOMIZE)
    path = os.pathsep.join(p for p in (str(folder), os.environ.get("PYTHONPATH", "")) if p)
    return boot_live_server(
        request, None, app, extra_env={
            "PYTHONPATH": path,
            "ALLOW_EXTERNAL_API_CALLS": "true",
            "ORG_ENCRYPTION_MASTER_KEY": MASTER_KEY,
        },
    )


def _seed(org_id):
    from app import create_app, db
    from app.models.connector_config import ConnectorConfig
    from app.models.organization import Organization
    from app.modules.codegen.services.credential_vault import OrgCredentialVault

    suffix = uuid.uuid4().hex[:8]
    app = create_app("testing")
    app.config["ORG_ENCRYPTION_MASTER_KEY"] = MASTER_KEY
    with app.app_context():
        other = Organization(name="Other Org %s" % suffix, slug="other-%s" % suffix)
        db.session.add(other)
        db.session.flush()
        OrgCredentialVault().store(org_id, "github", "api_token", "test-token-not-real")
        rows = {}
        for key, account in (("good", "octocat"), ("bad", "revoked"), ("missing", "ghost-team")):
            rows[key] = ConnectorConfig(
                organization_id=org_id,
                connector_type="github",
                name="GitHub %s %s" % (key, suffix),
                config={"organization": account, "account_type": "User"},
                status="active",
            )
        theirs = ConnectorConfig(
            organization_id=other.id,
            connector_type="github",
            name="Foreign GitHub %s" % suffix,
            config={"organization": "octocat", "account_type": "User"},
            status="active",
        )
        db.session.add_all(list(rows.values()) + [theirs])
        db.session.commit()
        return {
            "names": {k: v.name for k, v in rows.items()},
            "ids": [v.id for v in rows.values()] + [theirs.id],
            "foreign": theirs.name,
            "other": other.id,
        }


def _cleanup(data, org_id):
    from sqlalchemy import text

    from app import create_app, db

    app = create_app("testing")
    with app.app_context():
        db.session.execute(
            text("DELETE FROM connector_configs WHERE id = ANY(:ids)"), {"ids": data["ids"]}
        )
        db.session.execute(
            text("DELETE FROM org_connector_credentials WHERE organization_id = :o AND connector_type = 'github' AND credential_type = 'api_token'"),
            {"o": org_id},
        )
        db.session.execute(text("DELETE FROM organizations WHERE id = :o"), {"o": data["other"]})
        db.session.commit()


def _open(browser, base, seeded, size, scheme):
    context = browser.new_context(ignore_https_errors=True, viewport=size, color_scheme=scheme)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", seeded["emails"]["platform_admin"])
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)
    page.goto(base + "/integrations/connectors", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    return context, page, errors


def _card(page, name):
    card = page.locator("h3", has_text=name).locator(
        "xpath=ancestor::div[contains(@class,'cursor-pointer')][1]"
    )
    card.wait_for(state="visible", timeout=PAGE_TIMEOUT)
    return card


@pytest.mark.parametrize(
    "size,scheme",
    [({"width": 1440, "height": 900}, "light"), ({"width": 390, "height": 844}, "dark")],
)
def test_admin_tests_github_from_the_history_dialog_and_the_card(
    browser, github_server, seeded, size, scheme
):
    org_id = seeded["ids"]["org"]
    data = _seed(org_id)
    base = str(github_server)
    context, page, errors = _open(browser, base, seeded, size, scheme)
    os.makedirs(SHOTS, exist_ok=True)
    try:
        card = _card(page, data["names"]["good"])
        assert data["foreign"] not in page.content()

        # The dialog: a real click inside it acts, and the result is read in plain words.
        card.click()
        dialog = page.get_by_role("dialog")
        expect(dialog).to_be_visible()
        expect(dialog.get_by_role("heading", name=data["names"]["good"])).to_be_visible()
        button = dialog.get_by_role("button", name="Test Connection")
        top = button.evaluate(
            "el => { const r = el.getBoundingClientRect();"
            " const t = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);"
            " return el.contains(t); }"
        )
        assert top, "the Test Connection button is covered by something else"
        page.screenshot(path=os.path.join(SHOTS, "dialog-%d-%s.png" % (size["width"], scheme)))
        button.click()
        expect(page.get_by_text("Connected to GitHub as octocat")).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(dialog).to_be_visible()

        # A click on the backdrop closes it and focus goes back to the card that opened it.
        page.mouse.click(5, 5)
        expect(dialog).to_be_hidden()
        assert card.evaluate("el => el === document.activeElement")
        # Escape closes it too.
        card.click()
        expect(dialog).to_be_visible()
        page.keyboard.press("Escape")
        expect(dialog).to_be_hidden()
        assert card.evaluate("el => el === document.activeElement")

        # The card's own Test button: a token GitHub refuses, and an account it cannot find.
        _card(page, data["names"]["bad"]).get_by_role("button", name="Test connection").click()
        expect(page.get_by_text("GitHub rejected the token")).to_be_visible(timeout=PAGE_TIMEOUT)
        _card(page, data["names"]["missing"]).get_by_role("button", name="Test connection").click()
        expect(page.get_by_text("could not find")).to_be_visible(timeout=PAGE_TIMEOUT)
        assert "internal error" not in page.content().lower()
        page.screenshot(path=os.path.join(SHOTS, "card-%d-%s.png" % (size["width"], scheme)))
        assert not [e for e in errors if "favicon" not in e], errors
    finally:
        context.close()
        _cleanup(data, org_id)
