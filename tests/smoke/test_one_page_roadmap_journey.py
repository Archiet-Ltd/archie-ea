"""An enterprise architect opens any roadmap address and lands on the one roadmap.

The goal: see what the organisation is changing, when, and open the work behind it,
without choosing between a dozen roadmap pages. As the Enterprise Architect of a
seeded company this journey

* opens every older roadmap address and asserts it lands on /genome/roadmap/ with the
  view or filter that address stood for, and that the board there holds the
  company's own plateaus and work packages;
* switches view and filters to a solution and to an application, and asserts the
  figures on the page agree with the records seeded;
* follows each page that used to link to an old address and asserts it arrives on a
  roadmap with content;
* opens a work package from a card and asserts its own page names it;
* checks a second organisation's roadmap never leaks in, and an empty organisation is
  told what is missing and offered the next step;
* checks the board stacks to one column at 390 wide with nothing running off screen.
"""
import os
import re
import uuid
from datetime import date, datetime, timedelta

import pytest

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK_DIR = os.environ.get("ROADMAP_WALK_DIR")

OLD_ADDRESSES = [
    ("/archimate-roadmap", None),
    ("/strategic/roadmap", None),
    ("/enterprise/strategic/roadmap", None),
    ("/governance/roadmap", None),
    ("/adm-kanban/roadmap", None),
    ("/adm-kanban/plateau-roadmap", None),
    ("/roadmaps", None),
    ("/roadmap-builder/", None),
    ("/strategic/technology-roadmap", "technology"),
    ("/enterprise/strategic/technology-roadmap", "technology"),
    ("/technology-roadmap", "technology"),
    ("/hybrid-roadmap", "capability"),
]


def _seed(org_id, label):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.implementation_migration import Plateau
    from app.services import work_package_service
    from app.models.solution_models import Solution

    app = create_app("testing")
    with app.app_context():
        component = ApplicationComponent(name="%s Customer Portal" % label, organization_id=org_id)
        solution = Solution(name="%s Billing Renewal" % label, organization_id=org_id)
        db.session.add_all([component, solution])
        db.session.flush()
        done = Plateau(name="%s Baseline" % label, organization_id=org_id, sequence_order=1, target_date=date(2026, 3, 1))
        now = Plateau(name="%s Target Architecture" % label, organization_id=org_id, sequence_order=2,
                      target_date=date.today() + timedelta(days=90))
        db.session.add_all([done, now])
        db.session.flush()
        soon = datetime.now() + timedelta(days=90)
        done_wp = work_package_service.create_work_package(
            organization_id=org_id, name="%s Retire mainframe" % label, plateau_id=done.id,
            status="completed", progress_percentage=100, end_date=datetime(2026, 3, 1))
        portal_wp = work_package_service.create_work_package(
            organization_id=org_id, name="%s Move portal to cloud" % label, plateau_id=now.id,
            status="in_progress", progress_percentage=40, application_component_id=component.id,
            togaf_phase="D", end_date=soon)
        billing_wp = work_package_service.create_work_package(
            organization_id=org_id, name="%s Replace billing engine" % label, plateau_id=now.id,
            status="planned", end_date=soon)
        billing_wp.context = "solution"
        billing_wp.context_id = solution.id
        db.session.commit()
        ids = {
            "app": component.id, "solution": solution.id, "portal_wp": portal_wp.id,
            "names": {"done": done_wp.name, "portal": portal_wp.name, "billing": billing_wp.name,
                      "target": now.name, "baseline": done.name, "app": component.name,
                      "solution": solution.name},
        }
        db.session.remove()
    return ids


def _persona_is_enterprise_architect(user_id):
    from app import create_app, db
    from app.models.user import User

    app = create_app("testing")
    with app.app_context():
        user = db.session.get(User, user_id)
        assert user.enterprise_role == "enterprise_architect", user.enterprise_role
        assert not user.is_admin(), "the journey must not run as an administrator"
        assert not (user.role and user.role.name == "Administrator")
        db.session.remove()


@pytest.fixture(scope="module")
def world():
    suffix = uuid.uuid4().hex[:6]
    a = create_fresh_org("enterprise_architect")
    b = create_fresh_org("enterprise_architect")
    empty = create_fresh_org("enterprise_architect")
    a["data"] = _seed(a["org_id"], "Alpha%s" % suffix)
    b["data"] = _seed(b["org_id"], "Bravo%s" % suffix)
    return {"a": a, "b": b, "empty": empty}


def _page(browser, base, org, width=1440, dark=False):
    context = browser.new_context(
        ignore_https_errors=True, viewport={"width": width, "height": 1000},
        color_scheme="dark" if dark else "light",
    )
    page = context.new_page()
    sign_in(page, base, org["emails"]["enterprise_architect"])
    return page


def _shot(page, name):
    if WALK_DIR:
        os.makedirs(WALK_DIR, exist_ok=True)
        page.screenshot(path=os.path.join(WALK_DIR, name + ".png"), full_page=True)


def _board_text(page):
    return page.locator("[data-testid='roadmap-board']").inner_text()


def test_signed_in_as_the_enterprise_architect(world):
    _persona_is_enterprise_architect(world["a"]["user_ids"]["enterprise_architect"])


@pytest.mark.parametrize("old,lens", OLD_ADDRESSES)
def test_every_older_address_lands_on_the_one_roadmap(browser, live_server, world, old, lens):
    page = _page(browser, live_server, world["a"])
    try:
        page.goto(live_server + old, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert page.url.split("?")[0].rstrip("/").endswith("/genome/roadmap"), "%s landed on %s" % (old, page.url)
        if lens:
            assert "lens=%s" % lens in page.url, page.url
        names = world["a"]["data"]["names"]
        if lens == "capability":
            assert "Nothing in this view matches" in page.content()
            return
        board = _board_text(page)
        # The strategic view holds every plateau; the others hold the ones linked to them.
        if lens is None:
            assert names["target"] in board and names["baseline"] in board
        assert names["portal"] in board or lens == "capability"
        assert "Bravo" not in page.content()
    finally:
        page.context.close()


def test_views_and_filters_agree_with_the_records(browser, live_server, world):
    a = world["a"]
    names = a["data"]["names"]
    page = _page(browser, live_server, a)
    try:
        page.goto(live_server + "/genome/roadmap/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _shot(page, "strategic-1440-light")
        assert "2 plateaus and 3 work packages shown" in page.locator("[data-testid='roadmap-totals']").inner_text()
        column = {c.get_attribute("data-column"): c for c in page.locator("[data-testid='roadmap-column']").all()}
        assert names["baseline"] in column["completed"].inner_text()
        assert names["target"] in column["in-progress"].inner_text()
        assert column["completed"].locator("[data-testid='roadmap-column-count']").inner_text() == "1"

        # Application view: only work that changes an application.
        page.get_by_role("link", name="Application", exact=True).click()
        page.wait_for_url(re.compile(r"lens=application"))
        board = _board_text(page)
        assert names["portal"] in board and names["billing"] not in board and names["done"] not in board
        assert "1 plateau and 1 work package shown" in page.locator("[data-testid='roadmap-totals']").inner_text()

        # Technology view: the technology phase package.
        page.get_by_role("link", name="Technology", exact=True).click()
        page.wait_for_url(re.compile(r"lens=technology"))
        assert names["portal"] in _board_text(page)

        # Capability view: nothing is linked to a capability, and the page says why and what next.
        page.get_by_role("link", name="Capability", exact=True).click()
        page.wait_for_url(re.compile(r"lens=capability"))
        assert "Nothing in this view matches" in page.content()
        assert "3 work packages" in page.content()
        page.get_by_role("link", name="Open the Strategic view").click()
        page.wait_for_load_state("domcontentloaded")
        assert names["target"] in _board_text(page)

        # Product view: Now, Next, Later; the finished plateau has no place.
        page.get_by_role("link", name="Product", exact=True).click()
        page.wait_for_url(re.compile(r"lens=product"))
        headings = [h.inner_text() for h in page.locator("[data-testid='roadmap-column'] h2").all()]
        assert headings == ["Now", "Next", "Later"]
        assert names["baseline"] not in _board_text(page)

        # Solution filter: the same board narrowed, with the breadcrumb naming the solution.
        page.goto("%s/genome/roadmap/?solution=%d" % (live_server, a["data"]["solution"]), wait_until="domcontentloaded")
        board = _board_text(page)
        assert names["billing"] in board and names["portal"] not in board
        assert names["solution"] in page.locator("nav[aria-label='Breadcrumb']").first.inner_text()
        _shot(page, "solution-filter-1440-light")
        page.locator("[data-testid='roadmap-clear-filter']").click()
        page.wait_for_load_state("domcontentloaded")
        assert names["portal"] in _board_text(page) and "solution=" not in page.url

        # Application filter.
        page.goto("%s/genome/roadmap/?application=%d" % (live_server, a["data"]["app"]), wait_until="domcontentloaded")
        board = _board_text(page)
        assert names["portal"] in board and names["billing"] not in board
        assert names["app"] in page.locator("nav[aria-label='Breadcrumb']").first.inner_text()
    finally:
        page.context.close()


def test_a_work_package_card_opens_its_own_page(browser, live_server, world):
    a = world["a"]
    page = _page(browser, live_server, a)
    try:
        page.goto(live_server + "/genome/roadmap/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_role("link", name=a["data"]["names"]["portal"]).click()
        page.wait_for_url(re.compile(r"/implementation/work-packages/%d" % a["data"]["portal_wp"]))
        assert a["data"]["names"]["portal"] in page.locator("body").inner_text()
        assert page.locator("body").inner_text().strip(), "work package page is empty"
    finally:
        page.context.close()


def test_pages_that_linked_to_an_old_roadmap_now_reach_the_one_roadmap(browser, live_server, world):
    a = world["a"]
    names = a["data"]["names"]
    page = _page(browser, live_server, a)
    try:
        for start, link_name in (
            ("/adm-kanban/gap-analysis", re.compile(r"Roadmap")),
            ("/enterprise/strategic", re.compile(r"Technology Roadmap", re.I)),
        ):
            page.goto(live_server + start, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            link = page.locator("a[href*='/genome/roadmap']").filter(has_text=link_name).first
            assert link.count() == 1, "%s has no link to the roadmap" % start
            link.click()
            page.wait_for_url(re.compile(r"/genome/roadmap"))
            assert names["portal"] in _board_text(page), start
        # The ADM board's toolbar offers one Roadmap button.
        page.goto(live_server + "/adm-kanban/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        toolbar = page.locator("[data-testid='adm-kanban-toolbar'] a[href*='roadmap']")
        hrefs = [h.get_attribute("href") for h in toolbar.all()]
        assert hrefs and all(h.startswith("/genome/roadmap") for h in hrefs), hrefs
        toolbar.first.click()
        page.wait_for_url(re.compile(r"/genome/roadmap"))
        assert names["target"] in _board_text(page)
    finally:
        page.context.close()


def test_another_organisation_never_appears_and_an_empty_one_is_told_what_to_do(browser, live_server, world):
    a, b, empty = world["a"], world["b"], world["empty"]
    page = _page(browser, live_server, a)
    try:
        page.goto(live_server + "/genome/roadmap/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert b["data"]["names"]["target"] not in page.content()
        response = page.goto("%s/genome/roadmap/?solution=%d" % (live_server, b["data"]["solution"]), wait_until="domcontentloaded")
        assert response.status == 404
    finally:
        page.context.close()
    page = _page(browser, live_server, empty)
    try:
        page.goto(live_server + "/genome/roadmap/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        body = page.locator("body").inner_text()
        assert "No plateaus or work packages yet" in body
        page.get_by_role("link", name="Add a work package").click()
        page.wait_for_url(re.compile(r"/implementation/work-packages/create"))
        assert page.locator("form").count() >= 1
    finally:
        page.context.close()


@pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
def test_narrow_screen_stacks_and_nothing_runs_off(browser, live_server, world, dark):
    a = world["a"]
    page = _page(browser, live_server, a, width=390, dark=dark)
    try:
        page.goto(live_server + "/genome/roadmap/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_selector("[data-testid='roadmap-board']")
        xs = {round(c.bounding_box()["x"]) for c in page.locator("[data-testid='roadmap-column']").all()}
        assert len(xs) == 1, "columns did not stack at 390 wide: %s" % xs
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        _shot(page, "strategic-390-%s" % ("dark" if dark else "light"))
    finally:
        page.context.close()


def test_wide_dark_walk(browser, live_server, world):
    page = _page(browser, live_server, world["a"], dark=True)
    try:
        page.goto(live_server + "/genome/roadmap/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_selector("[data-testid='roadmap-board']")
        _shot(page, "strategic-1440-dark")
    finally:
        page.context.close()
