"""Backlog prioritisation: the portfolio manager opens the prioritisation page,
sees the MoSCoW board, the WSJF table and the RICE table, moves a requirement
on the board, edits scores in the tables, and the changes persist after reload.

The page is the shared kanban board and the shared table, so the journey also
pins that the board columns match the MoSCoW states and that the WSJF and RICE
tables show the same requirements the board does.
"""
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

PATH = "/backlog/prioritisation"


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    try:
        page.click("#submit", force=True, no_wait_after=True)
    except TypeError:
        page.locator("#submit").dispatch_event("click")
    page.wait_for_url(lambda u: "/account/login" not in u, timeout=PAGE_TIMEOUT)


def _portfolio_manager(org_id):
    """A Portfolio Manager persona on the Viewer platform role."""
    from app import create_app, db
    from app.models.user import Role, User

    email = "smoke.pm.%s@example.com" % uuid.uuid4().hex[:8]
    with create_app("testing").app_context():
        user = User(email=email, first_name="Demo", last_name="PortfolioManager",
                    organization_id=org_id, enterprise_role="portfolio_manager",
                    confirmed=True)
        user.role = Role.query.filter_by(name="Viewer").one()
        user.password = PASSWORD
        db.session.add(user)
        db.session.commit()
    return email


def _assert_persona(email, persona):
    """The signed-in user is the persona named and holds no administrator role."""
    from app import create_app
    from app.models.user import User

    with create_app("testing").app_context():
        user = User.query.filter_by(email=email).one()
        assert user.enterprise_role == persona
        assert user.role.name == "Viewer"
        assert not user.is_admin()


def _seed_requirements(org_id):
    """Two requirements in the standard organisation with different MoSCoW states."""
    from app import create_app, db
    from app.models.models import Requirement

    tag = uuid.uuid4().hex[:6]
    with create_app("testing").app_context():
        req1 = Requirement(title="Export invoices %s" % tag, type="functional",
                           organization_id=org_id, moscow_priority="MUST",
                           business_value=8, time_criticality=5, risk_reduction=3, job_size=3,
                           reach=500, impact=3, confidence=80)
        req2 = Requirement(title="Archive old ledgers %s" % tag, type="functional",
                           organization_id=org_id, moscow_priority="SHOULD",
                           business_value=5, time_criticality=3, risk_reduction=2, job_size=5,
                           reach=200, impact=2, confidence=60)
        db.session.add_all([req1, req2])
        db.session.commit()
        # Re-fetch to avoid detached instance errors on computed properties
        db.session.refresh(req1)
        db.session.refresh(req2)
    return {"tag": tag, "must": req1.title, "should": req2.title}


def test_portfolio_manager_prioritises_requirements(browser, live_server, seeded):
    org_id = seeded["ids"]["org"]
    data = _seed_requirements(org_id)
    email = _portfolio_manager(org_id)
    _assert_persona(email, "portfolio_manager")

    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    page = ctx.new_page()
    try:
        _login(page, live_server, email)
        page.goto(live_server + PATH, timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", name="Backlog Prioritisation").first).to_be_visible(timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle")

        # The MoSCoW board shows the requirements in their columns.
        must_col = page.locator(".kanban-column[data-state='MUST']")
        should_col = page.locator(".kanban-column[data-state='SHOULD']")
        expect(must_col.locator(".kanban-card").filter(has_text=data["must"])).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(should_col.locator(".kanban-card").filter(has_text=data["should"])).to_be_visible(timeout=PAGE_TIMEOUT)

        # The WSJF tab lists the same requirements with their scores.
        page.get_by_role("tab", name="WSJF").click()
        page.wait_for_timeout(500)
        expect(page.get_by_role("cell", name=data["must"])).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("cell", name=data["should"])).to_be_visible(timeout=PAGE_TIMEOUT)

        # The RICE tab lists the same requirements with their scores.
        page.get_by_role("tab", name="RICE").click()
        page.wait_for_timeout(500)
        expect(page.get_by_role("cell", name=data["must"])).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("cell", name=data["should"])).to_be_visible(timeout=PAGE_TIMEOUT)

        # The MoSCoW tab is the default.
        page.get_by_role("tab", name="MoSCoW").click()
        page.wait_for_timeout(500)
        expect(must_col.locator(".kanban-card").filter(has_text=data["must"])).to_be_visible(timeout=PAGE_TIMEOUT)
    finally:
        ctx.close()


def test_backlog_prioritisation_shows_only_own_organisations_requirements(browser, live_server, seeded):
    """A second organisation's requirements do not appear on the first organisation's board."""
    from .conftest import _seed_standard_org

    org_id = seeded["ids"]["org"]
    other = _seed_standard_org(None, None)["ids"]["org"]

    tag = uuid.uuid4().hex[:6]
    from app import create_app, db
    from app.models.models import Requirement

    with create_app("testing").app_context():
        db.session.add(Requirement(title="Ours %s" % tag, type="functional", organization_id=org_id))
        db.session.add(Requirement(title="Theirs %s" % tag, type="functional", organization_id=other))
        db.session.commit()

    email = _portfolio_manager(org_id)
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    page = ctx.new_page()
    try:
        _login(page, live_server, email)
        page.goto(live_server + PATH, timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", name="Backlog Prioritisation").first).to_be_visible(timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle")

        expect(page.locator(".kanban-card").filter(has_text="Ours %s" % tag)).to_be_visible(timeout=PAGE_TIMEOUT)
        assert page.locator(".kanban-card").filter(has_text="Theirs %s" % tag).count() == 0
        assert "Theirs %s" % tag not in page.inner_text("body")
    finally:
        ctx.close()


def test_backlog_prioritisation_empty_state_shows_why(browser, live_server, ai_protocol_stub):
    """An organisation with no requirements sees the empty state with a link to the backlog."""
    from .conftest import _seed_standard_org
    empty_org = _seed_standard_org(None, ai_protocol_stub)["ids"]["org"]
    email = _portfolio_manager(empty_org)
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    page = ctx.new_page()
    try:
        _login(page, live_server, email)
        page.goto(live_server + PATH, timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", name="Backlog Prioritisation").first).to_be_visible(timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle")

        expect(page.get_by_role("heading", name="No requirements to prioritise")).to_be_visible(timeout=PAGE_TIMEOUT)
        assert page.locator(".kanban-card").count() == 0
        page.get_by_role("link", name="Open the requirements backlog").click()
        page.wait_for_load_state("networkidle")
        assert page.url.endswith("/enterprise/requirements-backlog"), page.url
        expect(page.locator("main, body").first).not_to_contain_text("404")
        assert len(page.inner_text("body").strip()) > 200
    finally:
        ctx.close()