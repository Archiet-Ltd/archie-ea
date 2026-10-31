"""Journey, in a browser: no button or link offers a person a page that refuses them.

The demo company is seeded the way the product's own seed does it. Two people sign in through the login form:

* the demo Enterprise Architect (platform role Viewer, persona Enterprise Architect, not an administrator);
* a viewer in the same organisation (platform role Viewer, no persona).

For each page that used to offer a refused link, every enabled link is followed with the person's own session and
none may answer 403 or an error. A control the person may not use stays on the page, disabled, with the reason and
the next step beside it. The two people are also checked against what they should be offered: the architect can
import a model file, edit an element and create a plateau and reaches the solution and its seven sub-pages with
content; the viewer reads the solution but is shown the model-changing controls disabled.
"""
import os
import re
import uuid

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

ARCHITECT_EMAIL = "demo@lantern-quay.example.com"
VIEWER_EMAIL = "viewer.%s@lantern-quay.example.com" % uuid.uuid4().hex[:6]

PAGES = [
    "/architecture/dashboard",
    "/enterprise/architecture/elements",
    "/architecture/elements",
    "/enterprise/implementation/plateaus",
    "/dashboard/applications/vendors",
    "/my-applications/health",
    "/dashboard/compliance",
    "/analytics/flow",
    "/batch-import/",
    "/batch-import/new",
    "/capability-map/trees",
    "/solutions/",
    "/enterprise/solutions_architecture_dashboard",
    "/adm-kanban/v2",
    "/ea-workflows",
]
SOLUTION_SUBPAGES = [
    "", "/review-packet", "/conformance", "/options-advisor", "/migration-roadmap",
    "/risks/heatmap", "/sad", "/traceability",
]
SKIP_LINK = re.compile(r"^(#|javascript:|mailto:|tel:)|/account/logout|/static/|/logout|/delete|/download")


@pytest.fixture(scope="module")
def demo(live_server, app):
    """Seed the demo company, add a viewer, and give each person a journey linked to a solution."""
    from app import db
    from app.commands.seed_demo_company import seed_demo_company

    previous = os.environ.get("DEMO_USER_PASSWORD")
    os.environ["DEMO_USER_PASSWORD"] = PASSWORD
    try:
        with app.app_context():
            seed_demo_company()
            from app.models.architecture_journey import ArchitectureJourney
            from app.models.organization import Organization
            from app.models.solution_models import Solution
            from app.models.user import Role, User

            org = Organization.query.filter_by(slug="lantern-quay").one()
            architect = User.query.filter_by(email=ARCHITECT_EMAIL).one()
            architect.password = PASSWORD
            viewer = User(email=VIEWER_EMAIL, first_name="Vera", last_name="Viewer",
                          organization_id=org.id, confirmed=True,
                          role=Role.query.filter_by(name="Viewer").first())
            viewer.password = PASSWORD
            db.session.add(viewer)
            db.session.commit()
            solution = (Solution.query.filter_by(organization_id=org.id)
                        .filter(Solution.created_by_id != architect.id)
                        .order_by(Solution.id).first()
                        or Solution.query.filter_by(organization_id=org.id).order_by(Solution.id).first())
            # the solution belongs to someone else, so opening it is the matrix's decision, not ownership
            owner = User.query.filter(User.organization_id == org.id, User.id.notin_([architect.id, viewer.id])).first()
            solution.created_by_id = owner.id
            solution.solution_owner = solution.business_sponsor = solution.technical_lead = None
            db.session.commit()
            journeys = {}
            for who, user in (("architect", architect), ("viewer", viewer)):
                journey = ArchitectureJourney(
                    owner_id=user.id, organization_id=org.id, title="Linked journey %s" % who,
                    intent="operating_model", selected_layers=["business"], current_stage="frame",
                    status="active", solution_id=solution.id)
                db.session.add(journey)
                db.session.commit()
                journeys[who] = journey.id
            out = {"solution_id": solution.id, "journeys": journeys,
                   "architect_role": (architect.enterprise_role, bool(architect.is_platform_admin)),
                   "viewer_role": (viewer.enterprise_role, bool(viewer.is_platform_admin))}
            db.session.remove()
    finally:
        if previous is None:
            os.environ.pop("DEMO_USER_PASSWORD", None)
        else:
            os.environ["DEMO_USER_PASSWORD"] = previous
    return out


def _sign_in(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)
    assert "/account/login" not in page.url, "could not sign in as %s" % email


def _enabled_get_targets(page):
    """Every enabled link (and GET form) on the open page, as same-site paths."""
    hrefs = page.eval_on_selector_all(
        "a[href]:not([aria-disabled='true']), form[method='get' i][action]",
        "els => els.map(e => e.getAttribute('href') || e.getAttribute('action'))")
    targets = set()
    for href in hrefs:
        if not href or SKIP_LINK.search(href):
            continue
        if href.startswith("http"):
            if not href.startswith(page.url.split("/", 3)[0] + "//" + page.url.split("/", 3)[2]):
                continue
            href = "/" + href.split("/", 3)[3]
        if href.startswith("/") and "{{" not in href:
            targets.add(href.split("#")[0])
    return sorted(targets)


def _follow_all(page, base, path, refused):
    response = page.goto(base + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_timeout(600)
    assert response.status == 200, "%s answered %s" % (path, response.status)
    assert len(page.inner_text("body").strip()) > 80, "%s opened with no content" % path
    for target in _enabled_get_targets(page):
        landed = page.request.get(base + target, timeout=PAGE_TIMEOUT)
        if landed.status in (401, 403) or landed.status >= 500:
            refused.append("%s -> %s answered %s" % (path, target, landed.status))
        elif "html" in (landed.headers.get("content-type") or "") and len(landed.text().strip()) < 200:
            refused.append("%s -> %s landed on an empty page" % (path, target))


def _assert_disabled_controls_explained(page, path):
    for box in page.locator("[data-testid='disabled-action']").all():
        button = box.locator("button")
        assert button.is_disabled(), "%s: a refused control is still enabled" % path
        reason_id = button.get_attribute("aria-describedby")
        reason = box.locator("#" + reason_id).inner_text().strip()
        assert len(reason) > 25 and reason.endswith("."), "%s: disabled control has no reason (%r)" % (path, reason)


@pytest.mark.parametrize("who", ["architect", "viewer"])
def test_no_enabled_link_on_the_listed_pages_is_refused(page, live_server, demo, who):
    persona = demo["%s_role" % who]
    if who == "architect":
        assert persona == ("enterprise_architect", False), "signed in as %s, not the Enterprise Architect" % (persona,)
    else:
        assert persona == (None, False), "signed in as %s, not a plain viewer" % (persona,)
    _sign_in(page, live_server, ARCHITECT_EMAIL if who == "architect" else VIEWER_EMAIL)
    refused = []
    for path in PAGES + ["/solutions/%d%s" % (demo["solution_id"], sub) for sub in SOLUTION_SUBPAGES] + [
            "/architecture-journey/work/%d" % demo["journeys"][who]]:
        _follow_all(page, live_server, path, refused)
        _assert_disabled_controls_explained(page, path)
    assert not refused, "links offered but refused:\n" + "\n".join(refused)


def test_architect_is_offered_and_can_use_model_changing_controls(page, live_server, demo):
    _sign_in(page, live_server, ARCHITECT_EMAIL)
    page.goto(live_server + "/architecture/dashboard", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.locator("[data-testid='btn-import-oef']").click()
    page.wait_for_url(re.compile(r"/architecture/import/oef"), timeout=PAGE_TIMEOUT)
    assert "import" in page.inner_text("body").lower()
    page.goto(live_server + "/enterprise/implementation/plateaus", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.locator("[data-testid='btn-new-plateau']").click()
    page.wait_for_url(re.compile(r"/Plateau/new"), timeout=PAGE_TIMEOUT)
    assert page.locator("form").count() > 0, "the New Plateau page has no form"
    # the platform-administrator controls are shown disabled, with the reason
    page.goto(live_server + "/ea-workflows", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    reason = page.locator("#seed-defaults-reason").inner_text()
    assert "Only a platform administrator can add the default workflows" in reason
    assert page.get_by_role("button", name="Seed Defaults").is_disabled()
    page.goto(live_server + "/dashboard/applications/vendors", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    # The retired vendor list redirects to the one Vendors page, which opens with content for this person.
    assert page.url.split("?")[0].rstrip("/").endswith("/applications/vendors"), page.url
    assert page.get_by_role("heading", name="Vendors").first.is_visible()


def test_architect_opens_the_solution_and_its_seven_sub_pages_with_content(page, live_server, demo):
    _sign_in(page, live_server, ARCHITECT_EMAIL)
    for sub in SOLUTION_SUBPAGES:
        response = page.goto("%s/solutions/%d%s" % (live_server, demo["solution_id"], sub),
                             wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert response.status == 200, "%s answered %s" % (sub or "the solution", response.status)
        assert len(page.inner_text("body").strip()) > 200
    page.goto("%s/architecture-journey/work/%d" % (live_server, demo["journeys"]["architect"]),
              wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.get_by_role("link", name=re.compile("Open linked solution")).click()
    page.wait_for_load_state("domcontentloaded")
    assert "refuses" not in page.inner_text("body").lower()
    assert len(page.inner_text("body").strip()) > 200


def test_viewer_reads_the_solution_and_sees_model_changes_disabled(page, live_server, demo):
    _sign_in(page, live_server, VIEWER_EMAIL)
    response = page.goto("%s/solutions/%d" % (live_server, demo["solution_id"]),
                         wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert response.status == 200
    page.goto(live_server + "/architecture/dashboard", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert page.locator("[data-testid='btn-import-oef']").count() == 0
    reason = page.locator("#import-oef-reason").inner_text()
    assert "Only an architect can import model files" in reason and "Team settings" in reason
    page.goto("%s/architecture-journey/work/%d" % (live_server, demo["journeys"]["viewer"]),
              wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.get_by_role("link", name=re.compile("Open linked solution")).click()
    page.wait_for_url(re.compile(r"/solutions/%d" % demo["solution_id"]), timeout=PAGE_TIMEOUT)


@pytest.mark.parametrize("who,email", [("architect", ARCHITECT_EMAIL), ("viewer", VIEWER_EMAIL)])
def test_home_link_is_the_persons_own_home(page, live_server, demo, who, email):
    _sign_in(page, live_server, email)
    for path in ("/analytics/flow", "/batch-import/", "/batch-import/new", "/capability-map/trees"):
        page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert page.locator("a[href='/admin/dashboard']").count() == 0, "%s offers the administration home" % path
        home = page.locator("nav[aria-label='Breadcrumb' i] a").first
        with page.expect_navigation(wait_until="domcontentloaded") as landed:
            home.click()
        assert landed.value.status == 200, "%s: the home link answered %s" % (path, landed.value.status)
        assert "/admin/dashboard" not in page.url
        assert len(page.inner_text("body").strip()) > 200
