"""A read-only person is turned away from every write, and keeps the few things that are theirs.

Journey 1: a Viewer opens the applications list, tries the three writes that sat outside the API
(delete several, quick update, add a requirement) and a form post, and is told in plain words why;
the list they read afterwards, and the other organisation's, are unchanged. The refusal page leads
back to a dashboard with content. An Editor's quick update still lands on the list.

Journey 2: the owner of a vendor analysis, whose organisation role is viewer, removes a stakeholder through
the analysis screen's own session, and the invited stakeholder, also a viewer, submits their own scores.
"""

import os
import uuid
from pathlib import Path

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .fresh_org import api, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = os.environ.get("SMOKE_WALK_DIR") or os.environ.get("VIEWER_API_SHOTS")
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
    """Two organisations with a linked application each; a Viewer and an Editor in the first, and a
    vendor analysis owned by one Viewer with a second Viewer invited to it."""
    from app import create_app, db

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:8]
    out = {"suffix": suffix, "emails": {}}
    with app.app_context():
        from app.models.application_portfolio import ApplicationComponent
        from app.models.business_capability import BusinessCapability
        from app.models.org_role import OrgRole
        from app.models.organization import Organization
        from app.models.user import Role, User
        from app.models.vendor_analysis import OptionsAnalysis
        from app.services.billing_plans import set_contract_plan

        Role.insert_roles()
        architect = Role.query.filter_by(name="Architect").one()
        orgs = []
        for label in ("audit", "other"):
            org = Organization(name="Anywhere %s %s" % (label, suffix), slug="aw-%s-%s" % (label, suffix))
            db.session.add(org)
            db.session.flush()
            set_contract_plan(org, "enterprise", None)
            db.session.commit()
            orgs.append(org)
        users = {}
        for key, org_role in (("viewer", "viewer"), ("editor", "architect"), ("owner", "viewer"), ("invited", "viewer")):
            email = "aw.%s.%s@example.com" % (key, suffix)
            user = User(email=email, first_name="Anywhere", last_name=key, organization_id=orgs[0].id, confirmed=True)
            user.role = architect
            user.password = PASSWORD
            db.session.add(user)
            db.session.commit()
            OrgRole.set_role(orgs[0].id, user.id, org_role)
            db.session.commit()
            out["emails"][key] = email
            users[key] = user
        mine = ApplicationComponent(name="Ledger %s" % suffix, organization_id=orgs[0].id)
        theirs = ApplicationComponent(name="Other ledger %s" % suffix, organization_id=orgs[1].id)
        db.session.add_all([mine, theirs])
        capability = BusinessCapability(name="Capability %s" % suffix, organization_id=orgs[0].id)
        db.session.add(capability)
        db.session.flush()

        def extra(model):
            return {"organization_id": orgs[0].id} if hasattr(model, "organization_id") else {}

        analysis = OptionsAnalysis(
            name="Analysis %s" % suffix, capability_id=capability.id, created_by_id=users["owner"].id,
            **extra(OptionsAnalysis)
        )
        db.session.add(analysis)
        db.session.flush()
        db.session.commit()
        out.update(
            mine=mine.id, mine_name=mine.name, theirs=theirs.id, theirs_name=theirs.name,
            analysis=analysis.id,
        )
        db.session.remove()
    return out


def _invite(world, user_key, role):
    """Invite one of the world's people to the analysis; returns the new row's id."""
    from app import create_app, db

    with create_app("testing").app_context():
        from app.models.user import User
        from app.models.vendor_analysis import OptionsAnalysis, StakeholderInput

        analysis = db.session.get(OptionsAnalysis, world["analysis"])
        person = User.query.filter_by(email=world["emails"][user_key]).one()
        extra = {"organization_id": analysis.organization_id} if hasattr(StakeholderInput, "organization_id") else {}
        row = StakeholderInput(
            analysis_id=analysis.id, stakeholder_id=person.id, stakeholder_role=role, **extra
        )
        db.session.add(row)
        db.session.commit()
        row_id = row.id
        db.session.remove()
    return row_id


def _context(browser, viewport, mode):
    context = browser.new_context(viewport=viewport, color_scheme=mode)
    context.add_init_script("try { localStorage.setItem('archie_onboarding_ts', '1'); } catch (e) {}")
    return context


def _seen(page, text):
    return page.get_by_text(text).locator("visible=true")


@pytest.mark.parametrize("viewport,mode", VIEWPORTS)
def test_a_viewer_is_told_why_for_each_write_outside_the_api_and_the_list_is_unchanged(
    browser, live_server, world, viewport, mode
):
    context = _context(browser, viewport, mode)
    page = context.new_page()
    try:
        sign_in(page, live_server, world["emails"]["viewer"])
        page.goto(live_server + "/applications/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(_seen(page, world["mine_name"]).first).to_be_visible(timeout=PAGE_TIMEOUT)

        for method, path, body in (
            ("POST", "/dashboard/applications/bulk-delete", {"ids": [world["mine"], world["theirs"]]}),
            ("POST", "/dashboard/applications/%d/quick-update" % world["mine"], {"name": "Viewer renamed"}),
            ("POST", "/ai-chat/data/create-requirement", {"name": "Viewer requirement %s" % world["suffix"]}),
        ):
            status, answer = api(page, method, path, body)
            assert status == 403, (path, status, answer)
            assert answer["error"] == REFUSAL, (path, answer)

        # The same refusal as a page, for a form a person posts from the browser.
        page.evaluate(
            """() => {
                const form = document.createElement('form');
                form.method = 'POST';
                form.action = '/dashboard/applications/bulk-delete';
                const meta = document.querySelector('meta[name="csrf-token"]');
                if (meta) {
                    const token = document.createElement('input');
                    token.type = 'hidden'; token.name = 'csrf_token'; token.value = meta.content;
                    form.appendChild(token);
                }
                document.body.appendChild(form);
                form.submit();
            }"""
        )
        expect(page.get_by_text(REFUSAL).first).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("You can view this, but not change it").first).to_be_visible(timeout=PAGE_TIMEOUT)
        _shot(page, "viewer-refused-change-page", viewport["width"], mode)

        # The way out of the refusal page lands on a page with content.
        page.get_by_role("link", name="Go to the dashboard").first.click()
        page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)
        assert "/dashboard" in page.url, page.url
        assert len(page.locator("main").first.inner_text().strip()) > 40

        page.goto(live_server + "/applications/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(_seen(page, world["mine_name"]).first).to_be_visible(timeout=PAGE_TIMEOUT)
        assert page.get_by_text("Viewer renamed").count() == 0
        _shot(page, "viewer-list-after-refused-writes", viewport["width"], mode)
    finally:
        context.close()

    from app import create_app, db

    with create_app("testing").app_context():
        from app.models.application_portfolio import ApplicationComponent

        names = {
            a.id: a.name
            for a in ApplicationComponent.query.filter(
                ApplicationComponent.id.in_([world["mine"], world["theirs"]])
            ).all()
        }
        assert names == {world["mine"]: world["mine_name"], world["theirs"]: world["theirs_name"]}
        db.session.remove()


def test_an_editor_quick_update_still_lands_on_the_list(browser, live_server, world):
    context = _context(browser, {"width": 1440, "height": 900}, "light")
    page = context.new_page()
    renamed = "Editor renamed %s" % world["suffix"]
    try:
        sign_in(page, live_server, world["emails"]["editor"])
        page.goto(live_server + "/applications/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        status, answer = api(page, "POST", "/dashboard/applications/%d/quick-update" % world["mine"], {"name": renamed})
        assert status == 200, (status, answer)
        page.goto(live_server + "/applications/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(_seen(page, renamed).first).to_be_visible(timeout=PAGE_TIMEOUT)
    finally:
        context.close()


@pytest.mark.parametrize("viewport,mode", VIEWPORTS)
def test_the_owner_removes_a_stakeholder_and_the_invited_person_submits_their_scores(
    browser, live_server, world, viewport, mode
):
    scoring_id = _invite(world, "invited", "technical")
    _invite(world, "editor", "business")
    context = _context(browser, viewport, mode)
    page = context.new_page()
    try:
        sign_in(page, live_server, world["emails"]["invited"])
        page.goto(live_server + "/dashboard/vendor-analysis", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        status, answer = api(
            page,
            "PATCH",
            "/dashboard/api/vendor-analysis/%d/stakeholders/%d/scores" % (world["analysis"], scoring_id),
            {"vendor_scores": {"1": {"cost": 7}}, "comments": "Fits our budget"},
        )
        assert status == 200, (status, answer)
        _shot(page, "invited-viewer-scores-saved", viewport["width"], mode)
    finally:
        context.close()

    from app import create_app, db

    with create_app("testing").app_context():
        from app.models.vendor_analysis import StakeholderInput

        saved = db.session.get(StakeholderInput, scoring_id)
        assert saved.comments == "Fits our budget"
        assert "7" in (saved.vendor_scores or "")
        db.session.remove()

    removable_id = _invite(world, "viewer", "finance")
    context = _context(browser, viewport, mode)
    page = context.new_page()
    try:
        sign_in(page, live_server, world["emails"]["owner"])
        page.goto(
            live_server + "/dashboard/vendor-analysis/%d" % world["analysis"],
            wait_until="domcontentloaded",
            timeout=PAGE_TIMEOUT,
        )
        # The analysis screen opens for its owner. Its stakeholder panel is drawn only on a step the
        # wizard does not offer, so the removal is made through the screen's own session, as its
        # script would.
        expect(page.locator("main").first).to_be_visible(timeout=PAGE_TIMEOUT)
        status, answer = api(page, "GET", "/dashboard/api/vendor-analysis/%d/stakeholders" % world["analysis"])
        assert status == 200, (status, answer)
        before = len(answer["stakeholders"])
        assert before >= 3, answer
        status, answer = api(
            page, "DELETE", "/dashboard/api/vendor-analysis/%d/stakeholders/%d" % (world["analysis"], removable_id)
        )
        assert status == 200 and answer["success"] is True, (status, answer)
        status, answer = api(page, "GET", "/dashboard/api/vendor-analysis/%d/stakeholders" % world["analysis"])
        assert len(answer["stakeholders"]) == before - 1
        assert removable_id not in [row["id"] for row in answer["stakeholders"]]
        _shot(page, "owner-viewer-analysis-after-removal", viewport["width"], mode)
    finally:
        context.close()

    from app import create_app, db

    with create_app("testing").app_context():
        from app.models.vendor_analysis import StakeholderInput

        assert StakeholderInput.query.filter_by(analysis_id=world["analysis"]).count() == before - 1
        db.session.remove()
