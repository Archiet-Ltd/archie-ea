"""A demonstration architect opens Products, opens one, and sees everything that makes it up.

The goal: "what is this product made of, layer by layer, and who and what is linked to it". The journey signs in
as the demonstration architect on the seeded organisation, opens Products from the sidebar, opens a product, and
compares the page to the relationship rows. It follows three element links and two linked-material links to pages
with content, adds an element to an empty layer and sees it listed after a reload, and checks that on a phone
each layer keeps its own grouping. A second organisation with no products shows an explained empty list.

Run at 1440 wide in the light scheme and at 390 wide in the dark scheme.
"""

import json
import os
import re
import uuid
from pathlib import Path

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

DEMO_EMAIL = "demo@lantern-quay.example.com"
WALK = os.environ.get("PRODUCT_PAGE_WALK_DIR")
VIEWS = [("light", 1440, 900), ("dark", 390, 844)]
PRODUCT = "Calibrated Sensor Service"
OTHER_PRODUCT = "Recertified Assembly Programme"
REPOSITORY = "lantern-quay/calibration-ledger"
CONNECTED_APPLICATION = "Calibration Ledger"
LAYERS = ["strategy", "business", "application", "technology", "motivation", "implementation"]
EMPTY_AT_THE_START = ["technology"]


def _remove_added_elements(app, org_id):
    """Take away the elements an earlier run of the add journey made (by name and organisation), so Technology
    is empty again at the start and the shared test database is left as the seed made it."""
    from app import db
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship

    with app.app_context():
        ids = [e.id for e in ArchiMateElement.query.filter(
            ArchiMateElement.organization_id == org_id, ArchiMateElement.name.like("Edge cache %")).all()]
        if ids:
            ArchiMateRelationship.query.filter(
                ArchiMateRelationship.organization_id == org_id,
                db.or_(ArchiMateRelationship.source_id.in_(ids), ArchiMateRelationship.target_id.in_(ids))
            ).delete(synchronize_session=False)
            ArchiMateElement.query.filter(
                ArchiMateElement.organization_id == org_id, ArchiMateElement.id.in_(ids)
            ).delete(synchronize_session=False)
            db.session.commit()
        db.session.remove()


def _connect_repository(app, org_id, restore=None):
    """Record a connector repository on the application element (the way a connector sync does), returning what
    it held before; passing that back puts it as it was."""
    from app import db
    from app.models.archimate_core import ArchiMateElement

    with app.app_context():
        element = ArchiMateElement.query.filter_by(
            organization_id=org_id, name=CONNECTED_APPLICATION, type="ApplicationComponent").one()
        held = (element.source_kind, element.source_ref, element.source_label)
        if restore is None:
            element.source_kind = "connector"
            element.source_ref = json.dumps({"system": "github", "repository": REPOSITORY})
            element.source_label = "GitHub " + REPOSITORY
        else:
            element.source_kind, element.source_ref, element.source_label = restore
        db.session.commit()
        db.session.remove()
        return held


@pytest.fixture(scope="module")
def demo(live_server, app):
    """The demonstration organisation, seeded the way the product's own seed does it."""
    from app import db

    previous = os.environ.get("DEMO_USER_PASSWORD")
    os.environ["DEMO_USER_PASSWORD"] = PASSWORD
    try:
        with app.app_context():
            from app.commands.seed_demo_company import seed_demo_company
            from app.models.archimate_core import ArchiMateElement
            from app.models.organization import Organization
            from app.models.user import User

            seed_demo_company()
            org_id = Organization.query.filter_by(slug="lantern-quay").one().id
            architect = User.query.filter_by(email=DEMO_EMAIL, organization_id=org_id).one()
            architect.password = PASSWORD
            db.session.commit()
            product = ArchiMateElement.query.filter_by(
                organization_id=org_id, name=PRODUCT, type="Product").one()
            other = ArchiMateElement.query.filter_by(
                organization_id=org_id, name=OTHER_PRODUCT, type="Product").one()
            out = {"org_id": org_id, "product_id": product.id, "other_product_id": other.id}
            db.session.remove()
    finally:
        if previous is None:
            os.environ.pop("DEMO_USER_PASSWORD", None)
        else:
            os.environ["DEMO_USER_PASSWORD"] = previous
    _remove_added_elements(app, out["org_id"])
    before = _connect_repository(app, out["org_id"])
    yield out
    _connect_repository(app, out["org_id"], before)
    _remove_added_elements(app, out["org_id"])


@pytest.fixture(params=VIEWS, ids=["1440-light", "390-dark"])
def view(request, browser, live_server, demo):
    scheme, width, height = request.param
    context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme)
    context.set_default_timeout(PAGE_TIMEOUT)
    context.set_default_navigation_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    sign_in(page, live_server, DEMO_EMAIL)
    if scheme == "dark":
        page.add_init_script("document.documentElement.classList.add('dark')")
    yield page, scheme, width
    context.close()


def _shot(page, name, scheme, width, selector=None):
    """Save a screenshot when PRODUCT_PAGE_WALK_DIR is set: the whole page, or one section of it (the page scrolls
    inside its own frame, so a section is captured by its selector)."""
    if WALK:
        Path(WALK).mkdir(parents=True, exist_ok=True)
        page.wait_for_timeout(600)
        path = os.path.join(WALK, "%s-%s-%d.png" % (name, scheme, width))
        if selector:
            page.locator(selector).first.screenshot(path=path)
        else:
            page.screenshot(path=path, full_page=True)


def _expected_members(app, org_id, product_id):
    """Names of the elements the relationship rows join to the product, directly or one serving or realization
    step outward, read straight from the relationship table."""
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship

    with app.app_context():
        rels = ArchiMateRelationship.query.filter_by(organization_id=org_id).all()
        names = {e.id: e.name for e in ArchiMateElement.query.filter_by(organization_id=org_id).all()}
        direct = set()
        for r in rels:
            if product_id in (r.source_id, r.target_id):
                direct.add(r.target_id if r.source_id == product_id else r.source_id)
        reached = set(direct)
        for r in rels:
            if (r.type or "").lower() not in {"composition", "aggregation", "assignment", "realization", "serving"}:
                continue
            for near, far in ((r.source_id, r.target_id), (r.target_id, r.source_id)):
                if near in direct and far != product_id:
                    reached.add(far)
        reached.discard(product_id)
        return {names[i] for i in reached}


def _open_product(page, base):
    """Open Products from the sidebar (the menu is closed on a phone, so its link is read, not clicked)."""
    page.goto(base + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    link = page.locator("aside a", has_text="Products").first
    assert link.count() == 1, "the sidebar has no Products entry"
    href = link.get_attribute("href")
    assert "/Product" in href, href
    if page.viewport_size["width"] >= 1024:
        link.click()
        page.wait_for_url("**/Product*", timeout=PAGE_TIMEOUT)
    else:
        page.goto(base + href, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_load_state("networkidle")


def test_products_list_shows_every_product_and_each_opens_its_page(view, live_server, demo):
    page, scheme, width = view
    _open_product(page, live_server)
    body = page.locator("main").inner_text()
    assert PRODUCT in body and "Recertified Assembly Programme" in body
    _shot(page, "products-list", scheme, width)
    page.get_by_role("link", name=PRODUCT).first.click()
    page.wait_for_selector("[data-testid='product-layers']", timeout=PAGE_TIMEOUT)
    assert page.locator("h1").first.inner_text().strip()
    assert PRODUCT in page.locator("main").inner_text()


def test_product_page_shows_every_layer_and_matches_the_relationship_data(view, live_server, demo, app):
    page, scheme, width = view
    page.goto("%s/architecture/business/Product/%d?ae=1" % (live_server, demo["product_id"]),
              wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_selector("[data-testid='product-layers']", timeout=PAGE_TIMEOUT)
    page.wait_for_load_state("networkidle")
    _shot(page, "product-summary", scheme, width, "[data-testid='product-summary']")
    _shot(page, "product-layers", scheme, width, "#layers")
    _shot(page, "product-linked", scheme, width, "[data-testid='product-linked']")
    _shot(page, "product-cost", scheme, width, "[data-testid='product-cost']")

    summary = page.locator("[data-testid='product-summary-text']").inner_text()
    assert PRODUCT in summary and "connected elements" in summary, summary
    footer = page.locator("[data-testid='product-summary'] [data-testid='result-footer']")
    assert footer.count() == 1
    for part in ("evidence", "source", "confidence", "date"):
        assert footer.locator("[data-testid='result-footer-%s']" % part).inner_text().strip()

    sections = page.locator("[data-testid='product-layer']")
    assert [sections.nth(i).get_attribute("data-layer") for i in range(sections.count())] == LAYERS
    shown = set()
    relationships = {}
    for key in LAYERS:
        section = page.locator("[data-testid='product-layer'][data-layer='%s']" % key)
        rows = section.locator("[data-testid='product-element-row']")
        count = int(section.locator("[data-testid='layer-count']").inner_text())
        assert rows.count() == count
        if key in EMPTY_AT_THE_START:
            assert section.locator("[data-testid='layer-empty']").inner_text().strip()
            assert section.locator("[data-testid='product-add-%s']" % key).count() == 1
        for i in range(rows.count()):
            row = rows.nth(i)
            text = row.inner_text()
            name = row.locator("[data-testid='product-element-link']").inner_text().strip()
            shown.add(name)
            relationship = row.locator("td").nth(2).inner_text().strip()
            relationships[name] = relationship
            owner = row.locator("td").nth(3).inner_text().strip()
            assert owner and owner not in ("\u2014", "None", "0"), "no owner or reason on the row: %r" % text
    expected = _expected_members(app, demo["org_id"], demo["product_id"])
    assert shown == expected, (sorted(shown ^ expected))
    assert len(expected) >= 8
    for name, relationship in relationships.items():
        # A direct member names the product; one reached through another element names that element.
        assert relationship.endswith("this product") or any(relationship.endswith(" " + n) for n in expected), (
            name, relationship)
    layers_with_members = [k for k in LAYERS if page.locator(
        "[data-testid='product-layer'][data-layer='%s'] [data-testid='product-element-row']" % k).count()]
    assert len(layers_with_members) >= 4

    if width < 500:
        # On a phone each element row sits inside its own layer's section, under that section's heading.
        for key in layers_with_members:
            section = page.locator("[data-testid='product-layer'][data-layer='%s']" % key)
            heading_box = section.locator("h2").first.bounding_box()
            for i in range(section.locator("[data-testid='product-element-row']").count()):
                row_box = section.locator("[data-testid='product-element-row']").nth(i).bounding_box()
                assert row_box["y"] > heading_box["y"]
            assert section.bounding_box()["width"] <= width
        _assert_name_column_stays_while_the_rest_scrolls(page, layers_with_members)


def _assert_name_column_stays_while_the_rest_scrolls(page, layers):
    """On a phone each layer table scrolls sideways; the name column (heading and cells) stays at the left edge
    while it does, and the last column scrolls with the rest instead of floating over it."""
    scrolled = 0
    for key in layers:
        box = page.locator("[data-testid='product-layer'][data-layer='%s'] [data-slot='table-container']" % key).first
        if not box.evaluate("el => el.scrollWidth > el.clientWidth + 8"):
            continue
        scrolled += 1
        box.evaluate("el => { el.scrollLeft = el.scrollWidth; }")
        page.wait_for_timeout(200)
        edge = box.bounding_box()["x"]
        head = box.locator("thead th").first
        cell = box.locator("tbody td:first-child").first
        head_x, cell_x = head.bounding_box()["x"], cell.bounding_box()["x"]
        assert abs(head_x - edge) <= 3, (key, "the name heading moved with the scroll", head_x, edge)
        assert abs(cell_x - edge) <= 3, (key, "the name cell moved with the scroll", cell_x, edge)
        assert head.evaluate("el => getComputedStyle(el).position") == "sticky"
        assert box.locator("thead th").last.evaluate("el => getComputedStyle(el).position") == "static"
        assert box.locator("tbody td").nth(3).evaluate("el => getComputedStyle(el).position") == "static"
    assert scrolled >= 1, "no layer table scrolled sideways on a phone, so the name column was not exercised"


def test_links_land_on_pages_with_content(view, live_server, demo):
    page, scheme, width = view
    product_url = "%s/architecture/business/Product/%d?ae=1" % (live_server, demo["product_id"])
    page.goto(product_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_selector("[data-testid='product-layers']", timeout=PAGE_TIMEOUT)

    wanted = [("business", "Remote diagnostics that cut site visits"),
              ("application", "Calibration Ledger"),
              ("strategy", "Sensor Calibration")]
    for layer, name in wanted:
        page.goto(product_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        link = page.locator("[data-testid='product-layer'][data-layer='%s'] a[data-testid='product-element-link']" % layer,
                            has_text=name).first
        link.click()
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_selector("[data-testid='connections']", timeout=PAGE_TIMEOUT)
        assert name in page.locator("h1").first.inner_text()
        # The element's own page lists what it is connected to: the link was not to an empty page or to itself.
        connections = page.locator("[data-testid='connections']").inner_text()
        assert PRODUCT in connections or "Calibrated sensors with a certificate for every batch" in connections, (
            name, connections)

    page.goto(product_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    diagram = page.locator("[data-testid='linked-diagram-link']").first
    assert PRODUCT in diagram.inner_text()
    diagram.click()
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_load_state("networkidle")
    assert page.locator("body").inner_text().strip()
    assert "/archimate/composer" in page.url
    assert page.locator("text=Calibrated Sensor Service").count() >= 1
    # A long name stays inside its box on one line, clear of the type label, and its full text is the hover text.
    page.wait_for_selector("[joint-selector='nameLabel']", timeout=PAGE_TIMEOUT)
    page.wait_for_timeout(1500)
    long_name = "Calibrated sensors with a certificate for every batch"
    boxes = page.evaluate("""(wanted) => {
        const out = [];
        for (const g of document.querySelectorAll('.joint-element')) {
            const name = g.querySelector('[joint-selector="nameLabel"]');
            const type = g.querySelector('[joint-selector="typeLabel"]');
            if (!name || !type) continue;
            const shown = name.textContent.replace(/\\u00a0/g, ' ').replace(/\\u2026/g, '').trim();
            if (shown.length < 12 || !wanted.startsWith(shown.slice(0, 12))) continue;
            const n = name.getBoundingClientRect(), t = type.getBoundingClientRect();
            const box = g.querySelector('[joint-selector="body"]').getBoundingClientRect();
            out.push({nameBottom: n.bottom, typeTop: t.top, typeText: type.textContent.trim(), inside: n.right <= box.right + 1,
                      lines: name.querySelectorAll('tspan').length});
        }
        return out;
    }""", long_name)
    assert boxes, "the long-named element is not in the diagram"
    for box in boxes:
        assert box["nameBottom"] <= box["typeTop"] + 1, box
        assert box["inside"] and box["lines"] <= 1 and box["typeText"], box
    # A link can lie across the box, so the pointer's arrival is sent to the box itself.
    page.locator(".joint-element", has=page.locator("[joint-selector='nameLabel']", has_text="Calibrated sensors with")
                 ).first.locator("[joint-selector='body']").dispatch_event("mouseover")
    titles = page.evaluate("() => [...document.querySelectorAll('.joint-element > title')].map(t => t.textContent)")
    assert long_name in titles, titles
    _shot(page, "composer-long-name", scheme, width)

    page.goto(product_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    decision = page.locator("[data-testid='linked-decision-link']").first
    title = decision.inner_text().strip()
    decision.click()
    page.wait_for_load_state("domcontentloaded")
    assert title in page.locator("main").inner_text()
    _shot(page, "linked-decision", scheme, width)

    # The open risk sits on an element of the other product.
    page.goto("%s/architecture/business/Product/%d?ae=1" % (live_server, demo["other_product_id"]),
              wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    risk = page.locator("[data-testid='product-problem'] a", has_text="Inventory data staleness").first
    assert re.search(r"/risks/\d+$", risk.get_attribute("href")), risk.get_attribute("href")
    risk.click()
    page.wait_for_load_state("domcontentloaded")
    assert "Inventory data staleness" in page.locator("main").inner_text()
    page.goto(product_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    problems = page.locator("[data-testid='product-problems']").inner_text()
    assert "Gap, in progress" in problems, problems
    assert "in_progress" not in problems and "_" not in problems, problems

    # The repository the demonstration connects is listed with the address it opens (an outside site, so the
    # address is read, not followed).
    repositories = page.locator("[data-testid='linked-repositories']")
    assert "No repository is connected" not in repositories.inner_text()
    repository = repositories.locator("[data-testid='linked-repository-link']").first
    assert REPOSITORY in repository.inner_text()
    assert repository.get_attribute("href") == "https://github.com/" + REPOSITORY
    assert page.locator("[data-testid='product-cost-total']").inner_text().strip()


def test_adding_an_element_to_an_empty_layer_lists_it_after_a_reload(view, live_server, demo):
    page, scheme, width = view
    name = "Edge cache %s" % uuid.uuid4().hex[:6]
    page.goto("%s/architecture/business/Product/%d?ae=1" % (live_server, demo["product_id"]),
              wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_selector("[data-testid='product-layers']", timeout=PAGE_TIMEOUT)
    page.wait_for_load_state("networkidle")
    page.locator("[data-testid='product-add-technology']").click()
    dialog = page.locator("[data-testid='product-add-form-technology']")
    dialog.wait_for(state="visible")
    dialog.get_by_label("Type").select_option("Node")
    dialog.get_by_label("Name").fill(name)
    _shot(page, "add-technology-dialog", scheme, width)
    dialog.locator("[data-testid='product-add-submit-technology']").click()
    page.wait_for_selector("[data-testid='product-layers']", timeout=PAGE_TIMEOUT)
    for _ in range(2):  # once straight after the save, once after a reload
        section = page.locator("[data-testid='product-layer'][data-layer='technology']")
        assert name in section.inner_text()
        assert "Is associated with this product" in section.inner_text()
        assert section.locator("[data-testid='layer-empty']").count() == 0
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("[data-testid='product-layers']", timeout=PAGE_TIMEOUT)
    _shot(page, "added-technology", scheme, width, "[data-testid='product-layer'][data-layer='technology']")


def test_export_is_one_file_with_every_section(view, live_server, demo):
    page, scheme, width = view
    page.goto("%s/architecture/business/Product/%d?ae=1" % (live_server, demo["product_id"]),
              wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    href = page.locator("[data-testid='product-export']").get_attribute("href")
    response = page.request.get(live_server + href)
    assert response.status == 200
    body = response.json()
    assert [layer["layer"] for layer in body["layers"]][:3] == ["Strategy", "Business", "Application"]
    assert body["product"]["name"] == PRODUCT and body["decisions"] and body["diagrams"]


@pytest.mark.parametrize("width,height,scheme", [(1440, 900, "light"), (390, 844, "dark")], ids=["1440-light", "390-dark"])
def test_an_organisation_with_no_products_gets_an_explained_empty_list(browser, live_server, width, height, scheme):
    fresh = create_fresh_org("enterprise_architect", org_admin=True)
    context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme)
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    sign_in(page, live_server, fresh["emails"]["enterprise_architect"])
    page.goto(live_server + "/architecture/business/Product", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_selector("main", timeout=PAGE_TIMEOUT)
    page.wait_for_load_state("networkidle")
    text = page.locator("main").inner_text()
    assert "Calibrated Sensor Service" not in text
    # Nothing was filtered by the person, so the list says there are no Products yet and how to add one.
    assert "No Products yet" in text, text
    assert "Add the first Product" in text, text
    assert "No elements match these filters" not in text, text
    assert page.locator("[data-testid='architecture-true-empty-state']").is_visible()
    assert not page.locator("[data-testid='architecture-filtered-empty-state']").is_visible()
    assert page.locator("[data-testid='architecture-true-empty-state'] button", has_text="Create element").is_visible()
    _shot(page, "products-list-empty", scheme, width)
    # The filtered sentence appears only once a person sets a filter that matches nothing.
    page.locator("#architecture-search, [data-testid='filter-search']").first.fill("zzz no such thing")
    page.wait_for_selector("[data-testid='architecture-filtered-empty-state']", state="visible", timeout=PAGE_TIMEOUT)
    assert "No elements match these filters" in page.locator("main").inner_text()
    assert not page.locator("[data-testid='architecture-true-empty-state']").is_visible()
    context.close()


def test_a_document_link_downloads_the_file_and_another_organisation_cannot_open_it(view, live_server, demo, browser):
    page, scheme, width = view
    page.goto("%s/architecture/business/Product/%d?ae=1" % (live_server, demo["product_id"]),
              wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_selector("[data-testid='linked-documents']", timeout=PAGE_TIMEOUT)
    link = page.locator("[data-testid='linked-document-link']").first
    assert "calibration-ledger-design.pdf" in link.inner_text()
    assert "for Calibration Ledger" in page.locator("[data-testid='linked-documents']").inner_text()
    href = link.get_attribute("href")
    _shot(page, "document-link", scheme, width, "[data-testid='linked-documents']")
    with page.expect_download(timeout=PAGE_TIMEOUT) as taken:
        link.click()
    download = taken.value
    # Every download carries the organisation name in front of the file name.
    assert download.suggested_filename.endswith("calibration-ledger-design.pdf")
    with open(download.path(), "rb") as handle:
        assert handle.read().startswith(b"%PDF-")
    assert "database error" not in page.locator("body").inner_text().lower()

    # Signed in as a person of another organisation, the same address answers 404 and gives no file.
    fresh = create_fresh_org("enterprise_architect", org_admin=True)
    context = browser.new_context(viewport={"width": width, "height": 900})
    context.set_default_timeout(PAGE_TIMEOUT)
    other = context.new_page()
    sign_in(other, live_server, fresh["emails"]["enterprise_architect"])
    response = other.request.get(live_server + href)
    assert response.status == 404, response.status
    assert b"%PDF" not in response.body()
    assert "attachment" not in response.headers.get("content-disposition", "")
    context.close()
