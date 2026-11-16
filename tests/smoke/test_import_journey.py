"""The import job as an enterprise architect does it: a file, a diagram, then the review of what was guessed.

Each organisation is its own; the second one holds a review item of its own that the first must never see.
Screenshots go to IMPORT_WALK_DIR when it is set.
"""
import os
import re
from pathlib import Path

import pytest

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK = os.environ.get("IMPORT_WALK_DIR")
DIAGRAM = Path(__file__).parent / "data" / "import_diagram_sample.json"
PAGES = ["/archimate/import", "/archimate/import/diagram", "/archimate/import-review"]
CSV = b"name,type,description\nZephyr Order Desk,ApplicationComponent,Takes orders\nAtlas Billing Ledger,ApplicationComponent,Bills them\n"


def _seed_review(app, org_id, tag):
    """Two elements the importer typed by guess and one relationship it read from a swim lane."""
    from app import db
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship

    with app.app_context():
        guessed = [ArchiMateElement(name="%s Pick Station" % tag, type="Node", layer="technology", organization_id=org_id,
                                    custom_properties={"lucid_type_source": "fallback"}),
                   ArchiMateElement(name="%s Pack Station" % tag, type="Node", layer="technology", organization_id=org_id,
                                    custom_properties={"lucid_type_source": "fallback"})]
        db.session.add_all(guessed)
        db.session.flush()
        db.session.add(ArchiMateRelationship(type="assignment", source_id=guessed[0].id, target_id=guessed[1].id,
                                             organization_id=org_id, derived_from="bpmn_lane"))
        db.session.commit()
        names = [e.name for e in guessed]
        db.session.remove()
    return names


@pytest.fixture(scope="module")
def orgs(live_server, app):
    mine = create_fresh_org("enterprise_architect", org_admin=True)
    other = create_fresh_org("enterprise_architect", org_admin=True)
    empty = create_fresh_org("enterprise_architect", org_admin=True)
    mine["review"] = _seed_review(app, mine["org_id"], "Mine")
    other["review"] = _seed_review(app, other["org_id"], "Theirs")
    yield {"mine": mine, "other": other, "empty": empty}
    from app import db
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship, SavedDiagramElement

    with app.app_context():
        for org in (mine, other, empty):
            ids = [e.id for e in ArchiMateElement.query.filter_by(organization_id=org["org_id"]).all()]
            if ids:
                SavedDiagramElement.query.filter(SavedDiagramElement.element_id.in_(ids)).delete(synchronize_session=False)
                ArchiMateRelationship.query.filter(ArchiMateRelationship.source_id.in_(ids)).delete(synchronize_session=False)
                ArchiMateElement.query.filter(ArchiMateElement.id.in_(ids)).delete(synchronize_session=False)
        db.session.commit()
        db.session.remove()


def _page(browser, base, who, width=1440, scheme="light"):
    context = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme)
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    sign_in(page, base, who["emails"]["enterprise_architect"])
    return context, page


def _ready(page):
    """Alpine has bound the page, so a chosen file is noticed."""
    page.wait_for_load_state("networkidle")
    page.wait_for_function("document.querySelectorAll('[x-data]').length > 0 && [...document.querySelectorAll('[x-data]')].every(e => e._x_dataStack)")


def _shot(page, name, scheme, width):
    if WALK:
        Path(WALK).mkdir(parents=True, exist_ok=True)
        page.wait_for_timeout(500)
        page.screenshot(path=os.path.join(WALK, "%s-%s-%d.png" % (name, scheme, width)), full_page=True)


def test_file_import_lists_links_and_the_review_clears(browser, live_server, orgs):
    context, page = _page(browser, live_server, orgs["mine"])
    page.goto(live_server + "/archimate/import", wait_until="domcontentloaded")
    _ready(page)
    for name in ("File", "Diagram", "Review"):
        assert page.get_by_role("link", name=name, exact=True).count() >= 1, "the %s tab is missing" % name
    trail = page.get_by_label("Breadcrumb")
    assert [t.strip() for t in trail.inner_text().split("\n") if t.strip()] == ["Home", "Composer", "Import a file"]
    _shot(page, "import-file", "light", 1440)

    page.set_input_files("#import-file", {"name": "apps.csv", "mimeType": "text/csv", "buffer": CSV})
    page.get_by_test_id("import-submit").click()
    page.wait_for_url(re.compile(r"created=\d+"))
    result = page.get_by_test_id("import-result")
    assert "2 elements created" in result.inner_text()
    links = result.get_by_test_id("element-link")
    assert links.all_inner_texts() == ["Atlas Billing Ledger", "Zephyr Order Desk"]
    assert result.get_by_text("Application Component").count() == 2, "types must read as words, not code names"
    assert page.get_by_test_id("open-in-composer").count() == 1
    _shot(page, "import-file-result", "light", 1440)

    links.first.click()
    page.wait_for_load_state("domcontentloaded")
    assert page.locator("h1").first.inner_text().strip() == "Atlas Billing Ledger"

    # the middle crumb opens a page with content, not a file download
    page.goto(live_server + "/archimate/import", wait_until="domcontentloaded")
    with page.expect_navigation() as nav:
        page.get_by_label("Breadcrumb").get_by_role("link", name="Composer").click()
    assert nav.value.status == 200 and "text/html" in nav.value.headers.get("content-type", "")
    assert len(page.inner_text("body").strip()) > 100

    page.goto(live_server + "/archimate/import", wait_until="domcontentloaded")
    page.get_by_role("link", name="Review", exact=True).first.click()
    page.wait_for_url(re.compile(r"/archimate/import-review"))
    table = page.get_by_test_id("review-table")
    rows = page.get_by_test_id("review-row")
    assert rows.count() == 3
    for cell in table.locator("td").all_inner_texts():
        text = cell.strip()
        assert not re.match(r"^[a-z]+_[a-z_]+$", text), "a code name reached the page: %s" % text
    assert "Placed in a lane, so the role was inferred." in table.inner_text()
    assert "Application Component" not in table.inner_text() and "Node" in table.inner_text()
    body = page.inner_text("body")
    assert not any(n in body for n in orgs["other"]["review"]), "another organisation's review items are visible"
    _shot(page, "import-review", "light", 1440)

    target = orgs["mine"]["review"][0]
    page.get_by_label("Select %s" % target, exact=True).check()
    assert page.get_by_text("1 of 3 row(s) selected.").count() == 1
    page.locator("[data-review-confirm]").click()
    page.wait_for_load_state("networkidle")
    page.reload(wait_until="domcontentloaded")
    assert page.get_by_label("Select %s" % target, exact=True).count() == 0
    assert page.get_by_test_id("review-row").count() == 2
    context.close()


def test_review_with_nothing_waiting_says_why_and_what_next(browser, live_server, orgs):
    context, page = _page(browser, live_server, orgs["empty"])
    page.goto(live_server + "/archimate/import-review", wait_until="domcontentloaded")
    text = page.inner_text("main")
    assert "Nothing is waiting" in text or "nothing has been reviewed" in text
    link = page.get_by_role("link", name=re.compile("Import a diagram|already reviewed")).first
    link.click()
    page.wait_for_load_state("domcontentloaded")
    assert len(page.inner_text("main").strip()) > 80
    context.close()


def test_diagram_import_previews_a_file_and_offers_the_review(browser, live_server, orgs):
    context, page = _page(browser, live_server, orgs["empty"])
    page.goto(live_server + "/archimate/import/diagram", wait_until="domcontentloaded")
    _ready(page)
    assert [t.strip() for t in page.get_by_label("Breadcrumb").inner_text().split("\n") if t.strip()] == ["Home", "Composer", "Import a diagram"]
    body = page.inner_text("main")
    for jargon in ("deterministic", "LLM", "AI pass"):
        assert jargon not in body, "internal wording on the page: %s" % jargon
    page.set_input_files("#diagram-file", {"name": "orders.json", "mimeType": "application/json", "buffer": DIAGRAM.read_bytes()})
    page.locator("#fallback-type").select_option(label="Import as Application Component")
    page.get_by_role("button", name="Preview").click()
    page.get_by_test_id("figure-cards").wait_for()
    assert "Order Desk" in page.inner_text("main") and "Billing Ledger" in page.inner_text("main")
    _shot(page, "import-diagram-preview", "light", 1440)
    context.close()


@pytest.mark.parametrize("path", PAGES)
def test_phone_width_and_dark_card_border(browser, live_server, orgs, path):
    context, page = _page(browser, live_server, orgs["mine"], width=390)
    page.goto(live_server + path, wait_until="domcontentloaded")
    _ready(page)
    assert page.evaluate("document.documentElement.scrollWidth") == 390
    if path != PAGES[2]:
        field = "#import-file" if path == PAGES[0] else "#diagram-file"
        page.set_input_files(field, {"name": "q3-apps.csv" if path == PAGES[0] else "orders.json", "mimeType": "text/plain", "buffer": b"x"})
        assert page.get_by_text("q3-apps.csv" if path == PAGES[0] else "orders.json", exact=True).is_visible()
        assert page.evaluate("document.documentElement.scrollWidth") == 390
    _shot(page, path.strip("/").replace("/", "-"), "light", 390)
    context.close()

    context, page = _page(browser, live_server, orgs["mine"], scheme="dark")
    page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded")
    page.wait_for_load_state("networkidle")
    reference = page.evaluate("getComputedStyle(document.querySelector('[data-slot=card]')).borderTopColor")
    page.goto(live_server + path, wait_until="domcontentloaded")
    page.wait_for_load_state("networkidle")
    _shot(page, path.strip("/").replace("/", "-"), "dark", 1440)
    if path != PAGES[2]:
        mine = page.evaluate("getComputedStyle(document.querySelector('[data-slot=card]')).borderTopColor")
        assert mine == reference
    context.close()
