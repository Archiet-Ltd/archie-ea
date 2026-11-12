"""A procurement lead opens the Solutions Architecture dashboard and finds the register's own contract figure.

Two organisations each hold solutions, patterns and contracts. The lead of the first signs in, opens the
Enterprise page, follows its Solutions Architecture card and reads the dashboard: its solutions, patterns and
contracts are the first organisation's alone, and its contract figure is the number of rows the Contracts
register lists when the lead follows the dashboard's own link. The second organisation's figures and names
never appear. The same holds on a narrow dark screen.
"""
import os
import re
import tempfile
import uuid
from datetime import date

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK_DIR = os.environ.get("SOLUTIONS_DASHBOARD_WALK_DIR") or tempfile.mkdtemp(prefix="solutions-dashboard-walk-")

OWN = {"solutions": 2, "patterns": 1, "contracts": 3}
RIVAL = {"solutions": 4, "patterns": 3, "contracts": 5}


@pytest.fixture(scope="module")
def estate(ai_protocol_stub, live_server):
    from app import create_app, db
    from app.models.application_portfolio import VendorContract
    from app.models.organization import Organization
    from app.models.solution_models import Solution, SolutionPattern
    from app.models.user import User

    tag = uuid.uuid4().hex[:6]
    app = create_app("testing")
    with app.app_context():
        own = Organization(name=f"Dash own {tag}", slug=f"dash-own-{tag}")
        rival = Organization(name=f"Dash rival {tag}", slug=f"dash-rival-{tag}")
        db.session.add_all([own, rival])
        db.session.flush()
        lead = User(email=f"dash-lead-{tag}@example.com", first_name="Dash", last_name="Lead",
                    organization_id=own.id, enterprise_role="procurement", confirmed=True)
        lead.password = PASSWORD
        db.session.add(lead)
        for org, label, counts in ((own, "Own", OWN), (rival, "Rival", RIVAL)):
            for n in range(counts["solutions"]):
                db.session.add(Solution(name=f"{label} solution {n} {tag}", organization_id=org.id))
            for n in range(counts["patterns"]):
                db.session.add(SolutionPattern(name=f"{label} pattern {n} {tag}", organization_id=org.id))
            for n in range(counts["contracts"]):
                db.session.add(VendorContract(contract_name=f"{label} contract {n} {tag}", organization_id=org.id,
                                              status="active", start_date=date.today()))
        db.session.commit()
        ids = {"orgs": [own.id, rival.id], "lead": lead.id, "email": lead.email}
        db.session.remove()
    yield {"tag": tag, **ids}
    # remove the records by key; the signed-in person stays (their audit rows are written as the session closes)
    with app.app_context():
        from app.models.application_portfolio import VendorContract as Contract
        from app.models.solution_models import Solution as Sol, SolutionPattern as Pattern

        for org_id in ids["orgs"]:
            Contract.query.filter(Contract.organization_id == org_id, Contract.contract_name.like(f"%{tag}")).delete(
                synchronize_session=False)
            Pattern.query.filter(Pattern.organization_id == org_id, Pattern.name.like(f"%{tag}")).delete(
                synchronize_session=False)
            Sol.query.filter(Sol.organization_id == org_id, Sol.name.like(f"%{tag}")).delete(synchronize_session=False)
        db.session.commit()
        db.session.remove()


def _shot(page, name):
    os.makedirs(WALK_DIR, exist_ok=True)
    page.screenshot(path=os.path.join(WALK_DIR, name + ".png"), full_page=True)


def _number(page, testid):
    locator = page.get_by_test_id(testid)
    expect(locator).to_be_visible(timeout=PAGE_TIMEOUT)
    text = locator.inner_text().strip()
    assert re.fullmatch(r"\d+", text), "%s shows %r, not a figure" % (testid, text)
    return int(text)


def _walk(page, base, email, tag, shot_suffix):
    _login(page, base, email)
    page.goto(base + "/enterprise/", timeout=PAGE_TIMEOUT)
    page.get_by_role("link", name=re.compile(r"Solutions Architecture")).first.click()
    page.wait_for_url(re.compile(r"/architecture/solutions-architecture"), timeout=PAGE_TIMEOUT)
    assert _number(page, "count-solutions-value") == OWN["solutions"]
    assert _number(page, "count-patterns-value") == OWN["patterns"]
    shown = _number(page, "count-contracts-value")
    assert shown == OWN["contracts"], shown
    body = page.locator("main").inner_text()
    assert "Rival" not in body, "another organisation's record is on the page"
    for n in range(OWN["solutions"]):
        assert f"Own solution {n} {tag}" in body
    _shot(page, "dashboard-" + shot_suffix)
    page.get_by_role("link", name="View the contracts register").click()
    page.wait_for_url(re.compile(r"/procurement/contracts"), timeout=PAGE_TIMEOUT)
    rows = page.get_by_test_id("contract-row")
    expect(rows.first).to_be_visible(timeout=PAGE_TIMEOUT)
    assert rows.count() == shown, "the dashboard says %d contracts, the register lists %d" % (shown, rows.count())
    assert "Rival" not in page.locator("main").inner_text()
    _shot(page, "register-" + shot_suffix)


def test_the_dashboard_contract_figure_is_the_registers_row_count_at_1440_light(browser, live_server, estate):
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        _walk(page, live_server, estate["email"], estate["tag"], "1440-light")
    finally:
        page.close()


def test_the_sidebar_dashboard_shows_the_same_figures_at_390_dark(browser, live_server, estate):
    page = browser.new_page(viewport={"width": 390, "height": 844}, color_scheme="dark")
    try:
        _login(page, live_server, estate["email"])
        page.goto(live_server + "/architecture/solutions-architecture", timeout=PAGE_TIMEOUT)
        assert _number(page, "count-solutions-value") == OWN["solutions"]
        assert _number(page, "count-patterns-value") == OWN["patterns"]
        shown = _number(page, "count-contracts-value")
        assert shown == OWN["contracts"], shown
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        _shot(page, "dashboard-390-dark")
        page.get_by_role("link", name="View the contracts register").click()
        page.wait_for_url(re.compile(r"/procurement/contracts"), timeout=PAGE_TIMEOUT)
        rows = page.get_by_test_id("contract-row")
        expect(rows.first).to_be_visible(timeout=PAGE_TIMEOUT)
        assert rows.count() == shown
        _shot(page, "register-390-dark")
    finally:
        page.close()
