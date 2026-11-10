"""A read-only auditor cannot change the portfolio through the API; an editor still can.

The browser hides "Add" from a Viewer, but the screen's own session could still create, rename and
delete an application through the JSON interface. The Viewer calls each from the signed-in page and
is refused in plain words; the list they read afterwards is unchanged, and so is the other
organisation's. An Editor makes the same three calls and each lands on the list they read.
"""

import os
import uuid
from pathlib import Path

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .fresh_org import api, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = os.environ.get("VIEWER_API_SHOTS")
VIEWPORTS = [
    pytest.param({"width": 1440, "height": 900}, "light", id="1440-light"),
    pytest.param({"width": 390, "height": 844}, "dark", id="390-dark"),
]
REFUSAL = "Your role is read-only, so it cannot make this change."


def _shot(page, name, width, mode):
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / ("%s-%d-%s.png" % (name, width, mode))), full_page=True)


@pytest.fixture(scope="module")
def world(live_server):
    """Two organisations, each with a linked application; a Viewer and an Editor in the first."""
    from app import create_app, db

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:8]
    out = {"suffix": suffix, "emails": {}}
    with app.app_context():
        from app.models.application_portfolio import ApplicationComponent
        from app.models.org_role import OrgRole
        from app.models.organization import Organization
        from app.models.user import Role, User
        from app.services.billing_plans import set_contract_plan

        Role.insert_roles()
        architect = Role.query.filter_by(name="Architect").one()
        orgs = []
        for label in ("audit", "other"):
            org = Organization(name="Read only %s %s" % (label, suffix), slug="ro-%s-%s" % (label, suffix))
            db.session.add(org)
            db.session.flush()
            set_contract_plan(org, "enterprise", None)
            db.session.commit()
            orgs.append(org)
        for key, org_role in (("viewer", "viewer"), ("editor", "architect")):
            email = "ro.%s.%s@example.com" % (key, suffix)
            user = User(email=email, first_name="Read", last_name=key, organization_id=orgs[0].id,
                        confirmed=True)
            user.role = architect
            user.password = PASSWORD
            db.session.add(user)
            db.session.commit()
            OrgRole.set_role(orgs[0].id, user.id, org_role)
            db.session.commit()
            out["emails"][key] = email
        mine = ApplicationComponent(name="Ledger %s" % suffix, organization_id=orgs[0].id)
        theirs = ApplicationComponent(name="Other ledger %s" % suffix, organization_id=orgs[1].id)
        db.session.add_all([mine, theirs])
        db.session.commit()
        out.update(mine=mine.id, mine_name=mine.name, theirs=theirs.id, theirs_name=theirs.name)
        db.session.remove()
    return out


def _applications_page(page, live_server):
    page.goto(live_server + "/applications/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    return page


def _seen(page, text):
    return page.get_by_text(text).locator("visible=true")


def _body(response):
    return response if isinstance(response, str) else (response.get("data", response) if isinstance(response, dict) else {})


@pytest.mark.parametrize("viewport,mode", VIEWPORTS)
def test_a_viewer_is_refused_create_rename_and_delete_and_the_list_is_unchanged(
    browser, live_server, world, viewport, mode
):
    context = browser.new_context(viewport=viewport, color_scheme=mode)
    context.add_init_script("try { localStorage.setItem('archie_onboarding_ts', '1'); } catch (e) {}")
    page = context.new_page()
    try:
        sign_in(page, live_server, world["emails"]["viewer"])
        _applications_page(page, live_server)
        expect(_seen(page, world["mine_name"]).first).to_be_visible(timeout=PAGE_TIMEOUT)

        status, body = api(page, "POST", "/api/v1/applications/", {"name": "Viewer made %s" % world["suffix"]})
        assert status == 403, (status, body)
        assert body["error"] == REFUSAL
        status, body = api(page, "PUT", "/api/v1/applications/%d" % world["mine"], {"name": "Viewer renamed"})
        assert status == 403 and body["error"] == REFUSAL, (status, body)
        status, body = api(page, "DELETE", "/api/v1/applications/%d" % world["mine"])
        assert status == 403 and body["error"] == REFUSAL, (status, body)
        status, body = api(page, "DELETE", "/api/v1/applications/%d" % world["theirs"])
        assert status in (403, 404), (status, body)

        _applications_page(page, live_server)
        expect(_seen(page, world["mine_name"]).first).to_be_visible(timeout=PAGE_TIMEOUT)
        assert page.get_by_text("Viewer made %s" % world["suffix"]).count() == 0
        assert page.get_by_text("Viewer renamed").count() == 0
        _shot(page, "viewer-list-after-refused-writes", viewport["width"], mode)
    finally:
        context.close()

    from app import create_app, db

    with create_app("testing").app_context():
        from app.models.application_portfolio import ApplicationComponent

        names = {a.id: a.name for a in ApplicationComponent.query.filter(
            ApplicationComponent.id.in_([world["mine"], world["theirs"]])).all()}
        assert names == {world["mine"]: world["mine_name"], world["theirs"]: world["theirs_name"]}
        db.session.remove()


def test_an_editor_creates_renames_and_deletes_an_application_and_the_list_follows(browser, live_server, world):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.add_init_script("try { localStorage.setItem('archie_onboarding_ts', '1'); } catch (e) {}")
    page = context.new_page()
    name = "Editor made %s" % world["suffix"]
    try:
        sign_in(page, live_server, world["emails"]["editor"])
        _applications_page(page, live_server)
        status, body = api(page, "POST", "/api/v1/applications/", {"name": name})
        assert status in (200, 201), (status, body)
        data = _body(body)
        app_id = data.get("id") or data.get("application", {}).get("id")
        assert app_id, body

        _applications_page(page, live_server)
        expect(_seen(page, name).first).to_be_visible(timeout=PAGE_TIMEOUT)

        status, body = api(page, "PUT", "/api/v1/applications/%s" % app_id, {"name": name + " renamed"})
        assert status == 200, (status, body)
        _applications_page(page, live_server)
        expect(_seen(page, name + " renamed").first).to_be_visible(timeout=PAGE_TIMEOUT)

        status, body = api(page, "DELETE", "/api/v1/applications/%s" % app_id)
        assert status == 200, (status, body)
        _applications_page(page, live_server)
        expect(_seen(page, world["mine_name"]).first).to_be_visible(timeout=PAGE_TIMEOUT)
        assert page.get_by_text(name + " renamed").count() == 0
    finally:
        context.close()
