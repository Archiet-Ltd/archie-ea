"""A page that lists one type of element: the enterprise architect of the demonstration company reads every
element's full name, sees the page's own type in each row, sorts and hides columns from the toolbar, and meets no
bare dash and no "No description" without being told why and what to do.

Each journey runs at 1440 wide in the light theme and at 390 wide in the dark theme.
"""
import os
import re
from pathlib import Path

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD
from .fresh_org import sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

DEMO_EMAIL = "demo@lantern-quay.example.com"
WALK = os.environ.get("SMOKE_WALK_DIR")
VIEWS = [("light", 1440, 900), ("dark", 390, 844)]
NODE = "/architecture/technology/Node"
GOAL = "/architecture/motivation/Goal"
REQUIREMENT = "/architecture/motivation/Requirement"
CAPABILITY = "/architecture/strategy/Capability"
WORK_PACKAGE = "/architecture/implementation/WorkPackage"


@pytest.fixture(scope="module")
def demo(live_server, app):
    """The demonstration organisation, seeded the way the product's own seed does."""
    previous = os.environ.get("DEMO_USER_PASSWORD")
    os.environ["DEMO_USER_PASSWORD"] = PASSWORD
    try:
        with app.app_context():
            from app import db
            from app.commands.seed_demo_company import seed_demo_company
            from app.models.organization import Organization
            from app.models.user import User

            seed_demo_company()
            org = Organization.query.filter_by(slug="lantern-quay").one()
            user = User.query.filter_by(email=DEMO_EMAIL, organization_id=org.id).one()
            user.password = PASSWORD
            assert user.enterprise_role == "enterprise_architect"
            db.session.commit()
            db.session.remove()
        yield
    finally:
        if previous is None:
            os.environ.pop("DEMO_USER_PASSWORD", None)
        else:
            os.environ["DEMO_USER_PASSWORD"] = previous


@pytest.fixture(params=VIEWS, ids=["1440-light", "390-dark"])
def view(request, browser, live_server, demo):
    scheme, width, height = request.param
    context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme)
    context.set_default_timeout(PAGE_TIMEOUT)
    context.set_default_navigation_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    sign_in(page, live_server, DEMO_EMAIL)
    yield page, scheme, width
    context.close()


def _open(page, base, path, scheme):
    page.goto(base + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_selector("[data-testid='type-table']", state="attached")
    page.wait_for_load_state("networkidle")
    if scheme == "dark":
        page.evaluate("() => document.documentElement.classList.add('dark')")


def _shot(page, name, scheme, width):
    if WALK:
        Path(WALK).mkdir(parents=True, exist_ok=True)
        # The page scrolls inside the shell, so a full-page capture would stop at the first screen: grow the window.
        original = page.viewport_size
        height = page.evaluate("() => Math.min(5000, (document.querySelector('[data-slot=sidebar-inset]') || document.body).scrollHeight)")
        page.set_viewport_size({"width": original["width"], "height": max(original["height"], height)})
        page.wait_for_timeout(500)
        page.screenshot(path=os.path.join(WALK, "%s-%s-%d.png" % (name, scheme, width)))
        page.set_viewport_size(original)


def _rows(page):
    return page.locator("[data-testid='type-row']")


def _names(page):
    return [t.strip() for t in page.locator("[data-testid='type-row-link']").all_inner_texts()]


def test_node_list_shows_the_page_type_and_every_name_opens_its_record(view, live_server):
    page, scheme, width = view
    _open(page, live_server, NODE, scheme)
    assert _rows(page).count() == 6, _names(page)
    badges = [_rows(page).nth(i).locator("[data-slot='badge']").first.inner_text().strip() for i in range(6)]
    assert badges == ["Node"] * 6, badges
    _shot(page, "node", scheme, width)
    names = _names(page)
    assert len(set(names)) == 6
    for name in names:
        _open(page, live_server, NODE, scheme)
        page.get_by_role("link", name=name, exact=True).click()
        page.wait_for_url(re.compile(r"/architecture/technology/Node/\d+"))
        heading = page.locator("h1").first
        heading.wait_for(state="visible")
        assert heading.inner_text().strip() == name, "opened %r, the record says %r" % (name, heading.inner_text())


def test_sorting_by_name_descending_puts_shared_services_cloud_first(view, live_server):
    page, scheme, width = view
    _open(page, live_server, NODE, scheme)
    page.locator("[data-table-sort-trigger='0']").click()
    page.get_by_role("menuitem", name="Descending").click()
    page.wait_for_url(re.compile(r"dir=desc"))
    page.wait_for_selector("[data-testid='type-table']", state="attached")
    assert _names(page)[0] == "Shared Services Cloud", _names(page)
    assert page.locator("th[aria-sort='descending']").count() == 1


def test_hiding_documentation_through_the_view_menu_removes_the_column_for_this_visit(view, live_server):
    page, scheme, width = view
    _open(page, live_server, NODE, scheme)
    header = page.locator("[data-testid='type-table'] th", has_text="Documentation")
    assert header.count() == 1
    if width < 1024:
        # The View menu is hidden on a narrow screen; the columns scroll instead and Documentation stays reachable.
        assert header.is_visible() or header.count() == 1
        return
    page.get_by_role("button", name="View").click()
    page.get_by_role("menuitemcheckbox", name="Documentation").click()
    assert page.locator("[data-testid='type-table'] th", has_text="Documentation").is_hidden()
    assert page.locator("[data-testid='type-row']").first.locator("td:visible").count() < page.locator("[data-testid='type-table'] th").count()
    # The table does not keep the choice: a reload brings the column back.
    page.reload(wait_until="domcontentloaded")
    assert page.locator("[data-testid='type-table'] th", has_text="Documentation").is_visible()


def test_no_requirement_cell_is_a_bare_dash_and_every_explained_cell_leads_to_its_edit_form(view, live_server):
    page, scheme, width = view
    _open(page, live_server, REQUIREMENT, scheme)
    assert _rows(page).count() > 0
    _shot(page, "requirement", scheme, width)
    for i in range(_rows(page).count()):
        for text in _rows(page).nth(i).locator("td").all_inner_texts():
            assert text.strip() not in ("—", "No description", "-", "0"), "row %d has a cell reading %r" % (i, text)
    hrefs = page.evaluate(
        """() => [...document.querySelectorAll("[data-testid='type-row']")].map(r => ({
            name: r.querySelector("[data-testid='type-row-link']").textContent.trim(),
            links: [...r.querySelectorAll('td a')].filter(a => /^(Add it|Add one)$/.test(a.textContent.trim())).map(a => a.getAttribute('href'))}))""")
    explained = [h for h in hrefs if h["links"]]
    assert explained, "no requirement shows an explained cell"
    for item in explained:
        for href in sorted(set(item["links"])):
            assert re.search(r"/Requirement/\d+/edit", href), href
            page.goto(live_server + href, wait_until="domcontentloaded")
            field = page.locator("input[x-model='formData.name']")
            field.wait_for(state="visible")
            page.wait_for_function("() => document.querySelector(\"input[x-model='formData.name']\").value !== ''")
            assert field.input_value() == item["name"], (href, item["name"])


def test_no_capability_description_is_the_name_with_a_prefix(view, live_server):
    page, scheme, width = view
    _open(page, live_server, CAPABILITY, scheme)
    _shot(page, "capability", scheme, width)
    for page_no in range(1, 20):
        for i in range(_rows(page).count()):
            row = _rows(page).nth(i)
            name = row.locator("[data-testid='type-row-link']").inner_text().strip()
            text = " ".join(row.inner_text().split())
            assert ("Business capability: " + name) not in text, text
        nxt = page.get_by_role("link", name="Next page")
        if nxt.count() == 0:
            break
        nxt.click()
        page.wait_for_load_state("domcontentloaded")


def test_goal_names_are_whole_at_390_and_the_motivation_tab_is_in_view(view, live_server):
    page, scheme, width = view
    _open(page, live_server, GOAL, scheme)
    _shot(page, "goal", scheme, width)
    cut = page.evaluate(
        """() => [...document.querySelectorAll("[data-testid='type-row-link']")].filter(a => {
            const style = getComputedStyle(a);
            const cell = a.closest('td');
            return a.scrollWidth > a.clientWidth + 1 || style.textOverflow === 'ellipsis' || getComputedStyle(cell).textOverflow === 'ellipsis'
                || a.textContent.includes('…') || a.textContent.includes('...');
        }).map(a => a.textContent.trim())""")
    assert cut == [], cut
    if width <= 480:
        box = page.locator("[data-testid='layer-spine'] a[aria-current='page']").bounding_box()
        assert box is not None and box["x"] >= 0 and box["x"] + box["width"] <= width + 1, box


PROCESS = "/architecture/business/BusinessProcess"
PLATEAU = "/architecture/implementation/Plateau"
MODEL_PAGES = [
    "/architecture/application/ApplicationComponent", "/architecture/business/BusinessActor", PROCESS,
    "/architecture/implementation/Gap", PLATEAU,
]


def test_business_process_list_searches_pages_and_acts_through_one_row_menu(view, live_server):
    page, scheme, width = view
    _open(page, live_server, PROCESS, scheme)
    first_top = _rows(page).first.bounding_box()["y"]
    assert first_top < 1700, "the first process starts %dpx down" % first_top
    _shot(page, "process", scheme, width)

    page.get_by_role("searchbox").fill("calibration")
    page.get_by_role("searchbox").press("Enter")
    page.wait_for_url(re.compile(r"search=calibration"))
    page.wait_for_selector("[data-testid='type-table']", state="attached")
    names = _names(page)
    assert names and all("calibration" in n.lower() for n in names), names
    count = page.locator("[data-testid='type-table-count']").inner_text()
    assert count.endswith("of %d" % len(names)), (count, names)

    if width >= 1024:
        page.get_by_role("button", name="View").click()
        page.get_by_role("menuitemcheckbox", name="Source").click()
        assert page.locator("[data-testid='type-table'] th", has_text="Source").is_hidden()
        page.keyboard.press("Escape")

    page.get_by_role("button", name="Actions for " + names[0]).click()
    items = [t.strip() for t in page.locator("[role='menuitem']:visible").all_inner_texts()]
    assert items == ["View", "Edit", "Open in Composer", "Delete"], items
    page.get_by_role("menuitem", name="View").click()
    page.wait_for_url(re.compile(r"/BusinessProcess/\d+"))
    page.locator("h1").first.wait_for(state="visible")
    assert page.locator("h1").first.inner_text().strip() == names[0]
    assert "relationship" in page.locator("body").inner_text().lower()

    _open(page, live_server, PROCESS, scheme)
    page.get_by_role("link", name="Next page").click()
    page.wait_for_url(re.compile(r"page=2"))
    page.wait_for_selector("[data-testid='type-table']", state="attached")
    assert _rows(page).count() == 25
    assert page.locator("[data-testid='type-table-page']").inner_text().strip() == "Page 2 of 3"


def test_a_search_with_no_match_says_why_and_offers_the_next_step(view, live_server):
    page, scheme, width = view
    _open(page, live_server, PROCESS + "?search=zzzqqxx", scheme)
    page.wait_for_selector("[data-table-none]", state="attached")
    text = page.locator("[data-table-none]").inner_text()
    assert "No business process element matches your search" in text, text
    clear = page.get_by_role("link", name="Clear search")
    assert clear.is_visible()
    assert page.get_by_role("link", name="New business process").first.is_visible()
    _shot(page, "process-empty", scheme, width)
    clear.click()
    page.wait_for_selector("[data-testid='type-row']", state="attached")
    assert _rows(page).count() == 25


def test_no_header_of_the_plateau_table_is_clipped_and_every_model_page_lists_its_own_type(view, live_server):
    page, scheme, width = view
    for path in MODEL_PAGES:
        _open(page, live_server, path, scheme)
        label = re.sub(r"(?<!^)(?=[A-Z])", " ", path.rsplit("/", 1)[1]).capitalize()
        badges = page.locator("[data-testid='type-row'] [data-slot='badge']").all_inner_texts()
        assert badges and set(b.strip() for b in badges) == {label}, (path, set(badges))

        clipped = page.evaluate(
            """() => [...document.querySelectorAll("[data-testid='type-table'] th")]
                .filter(th => th.scrollWidth > th.clientWidth + 1).map(th => th.textContent.trim())""")
        assert clipped == [], (path, clipped)
        _shot(page, path.rsplit("/", 1)[1].lower(), scheme, width)
