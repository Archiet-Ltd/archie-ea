"""The transformation backlog tells one story: what is ranked, how far a package has got, and what backs it.

The goal, as the demonstration organisation's Administrator: "I rank the work still to do, see how far each
work package really is, and find the evidence and the definition of done behind both". Runs against the
demonstration organisation's own seed (eight backlog requirements, twelve work packages), plus one
requirement with no definition of done so the empty state and its action are walked too.
"""
import os
import pathlib
import re
import tempfile
import uuid
from datetime import datetime

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = pathlib.Path(os.environ.get("BACKLOG_WALK_DIR") or tempfile.gettempdir()) / "backlog-walk"
DEMO_USER = "demo@lantern-quay.example.com"
PACKAGE = "Deploy mobile field app"
DELIVERED = ("Publish every instrument change as an event", "Export certificates as signed documents")
RANKED = (
    "Record the as-found reading for every instrument", "Show a customer the status of an open request",
    "Suggest the nearest qualified engineer", "Suppliers confirm delivery dates in the portal",
    "Reject certificates with a missing reference standard", "Retry a failed event delivery three times",
)
BARE = "Alert on a missed calibration deadline %s" % uuid.uuid4().hex[:6]


@pytest.fixture(scope="module")
def administrator(live_server):
    """The demonstration organisation, an Administrator in it, and one requirement with no definition of done."""
    from app import create_app, db
    from app.commands.seed_demo_company import seed_demo_company
    from app.models.solution_architect_models import RequirementType, SolutionRequirement
    from app.models.user import Role, User

    os.environ["DEMO_USER_PASSWORD"] = PASSWORD
    app = create_app("testing")
    email = "backlog.admin.%s@example.com" % uuid.uuid4().hex[:6]
    try:
        with app.app_context():
            seed_demo_company()
            org_id = User.query.filter_by(email=DEMO_USER).one().organization_id
            Role.insert_roles()
            user = User(email=email, first_name="Demo", last_name="Administrator", organization_id=org_id,
                        enterprise_role="platform_admin", confirmed=True,
                        role=Role.query.filter_by(name="Administrator").one())
            user.is_org_admin = True
            user.password = PASSWORD
            user.onboarding_completed_at = datetime.utcnow()
            db.session.add(user)
            db.session.add(SolutionRequirement(
                name=BARE, description="Raise an alert before a calibration falls overdue.", status="Draft",
                moscow_priority="SHOULD", requirement_type=RequirementType.FUNCTIONAL, organization_id=org_id,
                owner="Ivo Reed"))
            db.session.commit()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]
    return email


def _open(browser, live_server, email, path, width, scheme="light"):
    context = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    _login(page, live_server, email)
    page.goto(live_server + path, timeout=PAGE_TIMEOUT)
    page.wait_for_load_state("networkidle")
    return context, page


def _shot(page, name, width, scheme):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / ("%s-%s-%d.png" % (name, scheme, width))), full_page=True)


def test_prioritisation_ranks_every_requirement_not_yet_implemented(browser, live_server, administrator):
    context, page = _open(browser, live_server, administrator, "/backlog/prioritisation", 1440)
    try:
        expect(page.locator(".kanban-card").first).to_be_visible(timeout=PAGE_TIMEOUT)
        body = page.inner_text("body")
        assert "No requirements to prioritise" not in body
        for name in RANKED:
            expect(page.locator(".kanban-card").filter(has_text=name)).to_have_count(1)
        for name in DELIVERED:
            assert page.locator(".kanban-card").filter(has_text=name).count() == 0
        expect(page.get_by_text("Requirements already implemented are left out.")).to_be_visible()
        _shot(page, "prioritisation", 1440, "light")

        # The backlog holds the same requirements: everything on the board is there and not Implemented.
        page.goto(live_server + "/enterprise/requirements-backlog", timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle")
        for name in RANKED + DELIVERED:
            expect(page.get_by_text(name).first).to_be_visible(timeout=PAGE_TIMEOUT)
    finally:
        context.close()


def test_a_work_package_reads_one_progress_in_its_figure_and_its_evidence(browser, live_server, administrator):
    context, page = _open(browser, live_server, administrator, "/enterprise/implementation/work-packages", 1440)
    try:
        row = page.get_by_test_id("work-package-row").filter(has_text=PACKAGE)
        listed = row.get_by_test_id("work-package-progress").inner_text().strip()
        assert re.fullmatch(r"\d+%", listed) and listed != "0%", listed
        row.get_by_test_id("work-package-link").click()
        page.wait_for_load_state("networkidle")
        expect(page.get_by_role("heading", name=PACKAGE).first).to_be_visible(timeout=PAGE_TIMEOUT)
        shown = page.get_by_test_id("wp-progress").inner_text().strip().split("\n")[0].strip()
        evidence = page.get_by_test_id("result-footer-evidence").inner_text()
        assert shown == listed, (shown, listed)
        assert "Progress: %s" % shown in evidence, (shown, evidence)
        assert "Progress: 0%" not in evidence
        _shot(page, "work-package", 1440, "light")
    finally:
        context.close()


def test_work_package_list_and_backlog_carry_evidence_and_definitions_of_done(browser, live_server, administrator):
    context, page = _open(browser, live_server, administrator, "/enterprise/implementation/work-packages", 1440)
    try:
        footer = page.get_by_test_id("result-footer")
        expect(footer).to_be_visible(timeout=PAGE_TIMEOUT)
        for part in ("result-footer-evidence", "result-footer-source", "result-footer-confidence", "result-footer-date"):
            assert len(footer.get_by_test_id(part).inner_text().strip()) > 12, part
        # The footer counts the same packages the list holds: the ones it names plus "N more" make the total.
        total = int(page.get_by_test_id("wp-stat-total").inner_text().strip().split()[0])
        evidence = footer.get_by_test_id("result-footer-evidence")
        named = evidence.locator("a").count()
        more = re.search(r"(\d+) more work package", evidence.inner_text())
        assert named + (int(more.group(1)) if more else 0) == total, (named, more, total)
        evidence.locator("a").first.click()
        page.wait_for_load_state("networkidle")
        assert re.search(r"/implementation/work-packages/\d+$", page.url), page.url
        expect(page.get_by_test_id("wp-progress")).to_be_visible(timeout=PAGE_TIMEOUT)
        page.go_back()
        page.wait_for_load_state("networkidle")

        page.goto(live_server + "/enterprise/requirements-backlog", timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle")
        footer = page.get_by_test_id("result-footer")
        expect(footer).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(footer.get_by_test_id("result-footer-evidence")).to_contain_text("in the backlog")
        expect(footer.get_by_test_id("result-footer-confidence")).to_be_visible()
        assert "No checklist" not in page.inner_text("body")
        # Seeded requirements carry a written definition of done.
        seeded = page.locator("tr").filter(has_text="Record the as-found reading for every instrument")
        expect(seeded).to_contain_text("0/3 DoD")
        delivered = page.locator("tr").filter(has_text="Export certificates as signed documents")
        expect(delivered).to_contain_text("DoD")
        _shot(page, "backlog", 1440, "light")

        # One with none says so and offers the action; adding an item persists across a reload.
        bare = page.locator("tr").filter(has_text=BARE)
        bare.get_by_role("button", name="Add a definition of done for " + BARE).click()
        page.get_by_placeholder("Add DoD item...").fill("The alert reaches the planner")
        page.get_by_role("button", name="Add", exact=True).click()
        expect(page.get_by_text("The alert reaches the planner")).to_be_visible(timeout=PAGE_TIMEOUT)
        page.reload()
        page.wait_for_load_state("networkidle")
        expect(page.locator("tr").filter(has_text=BARE)).to_contain_text("0/1 DoD", timeout=PAGE_TIMEOUT)
    finally:
        context.close()


@pytest.mark.parametrize("path", ["/backlog/prioritisation", "/enterprise/requirements-backlog",
                                  "/enterprise/implementation/work-packages"])
def test_pages_hold_at_390_in_dark(browser, live_server, administrator, path):
    context, page = _open(browser, live_server, administrator, path, 390, "dark")
    try:
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        assert len(page.inner_text("body").strip()) > 200
        if path == "/backlog/prioritisation":
            # A person sees a ranked requirement without scrolling the board sideways.
            seen = page.locator(".kanban-card").filter(visible=True)
            expect(seen.first).to_be_visible(timeout=PAGE_TIMEOUT)
            in_view = page.evaluate("""() => [...document.querySelectorAll('.kanban-card')].filter(el => {
                const r = el.getBoundingClientRect();
                return r.left >= 0 && r.right <= window.innerWidth && r.height > 0;
            }).length""")
            assert in_view > 0, "no ranked requirement is inside the 390-wide screen"
        _shot(page, path.strip("/").replace("/", "-"), 390, "dark")
    finally:
        context.close()


def test_a_requirement_whose_status_a_person_changed_keeps_its_checklist_and_completion_in_step(administrator):
    """Re-running the seed over a requirement a person marked Implemented, with no checklist, completes it."""
    from app import create_app
    from app.commands.seed_demo_company import seed_demo_company
    from app.models.solution_architect_models import SolutionRequirement

    name = "Reject certificates with a missing reference standard"
    app = create_app("testing")
    os.environ["DEMO_USER_PASSWORD"] = PASSWORD
    try:
        with app.app_context():
            from app import db
            row = SolutionRequirement.query.filter_by(name=name).first()
            original = (row.status, row.dod_checklist, row.dod_complete)
            row.status, row.dod_checklist, row.dod_complete = "Implemented", None, False
            db.session.commit()
            try:
                seed_demo_company()
                row = SolutionRequirement.query.filter_by(name=name).first()
                ticked = [item for item in (row.dod_checklist or []) if item.get("done") or item.get("checked")]
                assert row.dod_checklist and row.dod_complete is True, (row.status, row.dod_complete, row.dod_checklist)
                assert len(ticked) == len(row.dod_checklist)
            finally:
                row = SolutionRequirement.query.filter_by(name=name).first()
                row.status, row.dod_checklist, row.dod_complete = original
                db.session.commit()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]
