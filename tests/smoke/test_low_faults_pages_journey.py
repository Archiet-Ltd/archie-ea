"""An architect reads the roadmap, solutions, review board, change history and risk register.

The signed-in person is the architect of the demonstration company, to which the walk adds two plateaus with
one name, two solutions nobody leads yet, a risk and an application. A separate organisation holds a plateau of the same
name and a risk of its own, so a page can only pass by reading its own organisation's rows. Each step is
run at 1440 wide in the light theme and at 390 wide in the dark theme.
"""
import os
import tempfile
import uuid
from datetime import date
from pathlib import Path

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD
from .fresh_org import api, create_fresh_org, sign_in

DEMO_EMAIL = "demo@lantern-quay.example.com"

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

VIEWS = [("light", 1440, 900), ("dark", 390, 844)]
PLATEAU = "Modelled, Q4 2026 %s" % uuid.uuid4().hex[:6]
WALK = os.environ.get("LOW_FAULTS_WALK_DIR") or tempfile.mkdtemp(prefix="low-faults-walk-")


@pytest.fixture(scope="module")
def world(live_server, app):
    """The demonstration organisation, seeded as the product's own seed does, with the records the walk
    reads added to it; a second, separate organisation holds a plateau and a risk of its own."""
    other = create_fresh_org("enterprise_architect", org_admin=True)
    suffix = uuid.uuid4().hex[:6]
    out = {"email": DEMO_EMAIL, "suffix": suffix, "their_risk": "Their audit finding %s" % suffix}
    previous = os.environ.get("DEMO_USER_PASSWORD")
    os.environ["DEMO_USER_PASSWORD"] = PASSWORD
    try:
        with app.app_context():
            from app import db
            from app.commands.seed_demo_company import seed_demo_company
            from app.models.application_portfolio import ApplicationComponent
            from app.models.implementation_migration import Plateau
            from app.models.organization import Organization
            from app.models.risk import Risk
            from app.models.solution_models import Solution
            from app.models.user import User
            from app.services import work_package_service

            seed_demo_company()
            org = Organization.query.filter_by(slug="lantern-quay").one()
            architect = User.query.filter_by(email=DEMO_EMAIL, organization_id=org.id).one()
            architect.password = PASSWORD
            earlier = Plateau(name=PLATEAU, organization_id=org.id, sequence_order=91, target_date=date(2026, 10, 15))
            later = Plateau(name=PLATEAU, organization_id=org.id, sequence_order=92, target_date=date(2026, 12, 15))
            db.session.add_all([earlier, later])
            db.session.add(Plateau(name=PLATEAU, organization_id=other["org_id"], sequence_order=1,
                                   target_date=date(2026, 12, 15)))
            db.session.flush()
            out["work_package"] = "Move billing to the platform %s" % suffix
            work_package_service.create_work_package(
                organization_id=org.id, name=out["work_package"], plateau_id=later.id, status="planned",
                priority="medium", progress_percentage=0, start_date=None, end_date=None,
            )
            for n in (1, 2):
                db.session.add(Solution(name="Walk solution %d %s" % (n, suffix), organization_id=org.id,
                                        created_by_id=architect.id, status="planned", governance_status="draft"))
            out["risk"] = "Billing outage %s" % suffix
            out["application"] = "Ledger %s" % suffix
            risk = Risk(title=out["risk"], likelihood=3, impact=4, organization_id=org.id, owner="Ops")
            application = ApplicationComponent(name=out["application"], organization_id=org.id)
            db.session.add_all([risk, application, Risk(title=out["their_risk"], likelihood=2, impact=2,
                                                        organization_id=other["org_id"], owner="Ops")])
            db.session.commit()
            out["risk_id"], out["application_id"] = risk.id, application.id
            db.session.remove()
    finally:
        if previous is None:
            os.environ.pop("DEMO_USER_PASSWORD", None)
        else:
            os.environ["DEMO_USER_PASSWORD"] = previous
    return out


@pytest.fixture(params=VIEWS, ids=["light1440", "dark390"])
def view(request, browser, live_server, world):
    scheme, width, height = request.param
    context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme)
    context.set_default_timeout(PAGE_TIMEOUT)
    context.set_default_navigation_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    sign_in(page, live_server, world["email"])
    yield page, scheme, width
    context.close()


def _settle(page, scheme):
    page.wait_for_load_state("networkidle")
    if scheme == "dark":
        page.evaluate("() => document.documentElement.classList.add('dark')")


def _open(page, base, path, ready, scheme):
    page.goto(base + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_selector(ready, timeout=PAGE_TIMEOUT)
    _settle(page, scheme)


def _shot(page, name, scheme, width):
    Path(WALK).mkdir(parents=True, exist_ok=True)
    page.wait_for_timeout(600)
    page.screenshot(path=os.path.join(WALK, "%s-%s-%d.png" % (name, scheme, width)), full_page=True)


def _click_sidebar_link(page, group, href, width):
    """Click the sidebar entry as a person does: open the drawer on a phone and the group first."""
    if width < 768:
        drawer = page.get_by_role("dialog", name="Navigation")
        if not drawer.is_visible():
            page.get_by_test_id("sidebar-trigger").click()
        drawer.wait_for(state="visible")
        sidebar = drawer
    else:
        sidebar = page.get_by_test_id("sidebar")
    link = sidebar.locator('a[href="%s"]' % href).first
    if not link.is_visible():
        sidebar.locator('[data-nav-toggle="%s"]' % group).first.click()
    link.click()


def test_roadmap_names_two_alike_plateaus_apart_and_its_package_opens(view, live_server, world):
    page, scheme, width = view
    _open(page, live_server, "/genome/roadmap/", "[data-testid='roadmap-column']", scheme)
    names = [n.strip() for n in page.locator("[data-testid='roadmap-plateau-name']").all_inner_texts()]
    assert len(set(names)) == len(names), names
    names = [n for n in names if n.startswith(PLATEAU)]
    assert len(names) == 2 and len(set(names)) == 2, names
    assert any("15 Oct 2026" in n for n in names) and any("15 Dec 2026" in n for n in names), names
    assert all(n.startswith(PLATEAU) for n in names), names
    # a plateau is in one column
    columns = {}
    for column in page.locator("[data-testid='roadmap-column']").all():
        for n in column.locator("[data-testid='roadmap-plateau-name']").all_inner_texts():
            columns.setdefault(n.strip(), []).append(column.get_attribute("data-column"))
    assert all(len(v) == 1 for v in columns.values()), columns
    _shot(page, "roadmap", scheme, width)
    page.locator("[data-testid='roadmap-work-package']", has_text=world["work_package"]).click()
    page.wait_for_load_state("domcontentloaded")
    page.get_by_role("main").get_by_text(world["work_package"]).first.wait_for()
    assert world["their_risk"] not in page.content()


def test_solutions_page_says_no_lead_yet(view, live_server, world):
    page, scheme, width = view
    _open(page, live_server, "/solutions/?search=%s&bu=all" % world["suffix"], "main", scheme)
    body = page.get_by_role("main").inner_text().lower()
    assert "no solution has a lead yet" in body
    assert "led by unassigned" not in body
    assert "walk solution 1 %s" % world["suffix"] in body
    _shot(page, "solutions", scheme, width)


def test_sidebar_review_board_opens_the_boards_own_page(view, live_server, world):
    page, scheme, width = view
    _open(page, live_server, "/solutions/", "main", scheme)
    _click_sidebar_link(page, "compliance", "/arb/", width)
    page.wait_for_url("**/arb/")
    _settle(page, scheme)
    assert page.get_by_role("heading", level=1).first.inner_text().strip() == "Architecture Review Board"
    main = page.get_by_role("main")
    assert main.get_by_role("link", name="Full review ledger").count() == 1
    _shot(page, "review-board", scheme, width)
    main.get_by_role("link", name="Sessions").first.click()
    page.wait_for_url("**/arb/sessions")
    assert page.get_by_role("main").inner_text().strip()


def test_mapping_a_risk_appears_in_the_change_history(view, live_server, world):
    page, scheme, width = view
    _open(page, live_server, "/risks/", "main", scheme)
    # start from a risk that is not mapped, so each view records a mapping of its own
    _, links = api(page, "GET", "/api/risks/%d/links" % world["risk_id"])
    for link in links:
        api(page, "DELETE", "/api/risks/%d/links/%d" % (world["risk_id"], link["id"]))
    status, body = api(page, "POST", "/api/risks/%d/links" % world["risk_id"],
                       {"entity_type": "application", "entity_id": world["application_id"]})
    assert status in (200, 201), body
    _click_sidebar_link(page, "compliance", "/intelligence/history/changes", width)
    page.wait_for_url("**/intelligence/history/changes**")
    _settle(page, scheme)
    said = "%s mapped to %s" % (world["risk"], world["application"])
    shown = page.get_by_role("main").get_by_text(said).locator("visible=true")
    shown.first.wait_for()
    assert shown.count() >= 1
    assert page.get_by_role("main").get_by_text("Risk mapping").locator("visible=true").count() >= 1
    assert world["their_risk"] not in page.content()
    _shot(page, "change-history", scheme, width)


def test_risk_register_has_one_breadcrumb_trail(view, live_server, world):
    page, scheme, width = view
    _open(page, live_server, "/risks/", "main", scheme)
    trails = page.locator("nav[aria-label='Breadcrumb' i]")
    assert trails.count() == 1, trails.all_inner_texts()
    trail = " ".join(trails.first.inner_text().split())
    assert "Risk Register" not in trail and "Governance" not in trail, trail
    _shot(page, "risks", scheme, width)
