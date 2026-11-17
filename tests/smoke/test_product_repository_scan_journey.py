"""A product manager brings a product's repositories into its page, in a real browser.

The goal: "this product's code is in GitHub; show me what it is made of, and where each piece was read from".
A solution architect of a new organisation (with a product already linked to a business service and an
application) opens the product, sees GitHub is not connected, connects it from there and comes back to the
product, chooses two repositories, waits for the read, reviews and approves what was found, and returns to the
product to see the applications and the technology in their layers, each citing the file it was read from. A
second organisation sees none of it and is offered Connect GitHub of its own.

GitHub is never called: the recorded organisation of the connector tests is served to the server under test.
The token is a plain placeholder. Run at 1440 wide in the light scheme and at 390 wide in the dark scheme.
"""

import os
import re

import pytest
from playwright.sync_api import expect

from scripts.capture.github_fixture import SITECUSTOMIZE
from tests.smoke.conftest import PAGE_TIMEOUT, boot_live_server
from tests.smoke.fresh_org import create_fresh_org, sign_in
from tests.smoke.test_connect_github_to_cited_answer_journey import remove_organisations

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

TOKEN = "test-token-not-real"
SKIP_WELCOME = "localStorage.setItem('archie_onboarding_ts', Date.now().toString());"
SHOTS = os.environ.get("PRODUCT_SCAN_JOURNEY_SHOTS")
CHOSEN = ["acme/payments-service", "acme/customer-portal"]
EXPECTED = {  # element name -> (layer on the page, repository, file that shows it)
    "Payments Service": ("application", "acme/payments-service"),
    "Customer Portal": ("application", "acme/customer-portal"),
    "Redis": ("technology", "acme/customer-portal"),
}


def _seed_product(app, org_id, name):
    """A product already joined to a business service and an application, as an architect would have it."""
    from app import db
    from app.models import ArchiMateElement, ArchiMateRelationship

    with app.app_context():
        def add(label, kind, layer):
            row = ArchiMateElement(organization_id=org_id, name=label, type=kind, layer=layer)
            db.session.add(row)
            db.session.flush()
            return row

        product = add(name, "Product", "business")
        service = add(name + " ordering", "BusinessService", "Business")
        desk = add(name + " order desk", "ApplicationComponent", "Application")
        db.session.add(ArchiMateRelationship(organization_id=org_id, source_id=product.id, target_id=service.id, type="Aggregation"))
        db.session.add(ArchiMateRelationship(organization_id=org_id, source_id=desk.id, target_id=service.id, type="Serving"))
        db.session.commit()
        out = product.id
        db.session.remove()
        return out


@pytest.fixture(scope="module")
def github_server(request, app, tmp_path_factory):
    folder = tmp_path_factory.mktemp("github_product_peer")
    (folder / "sitecustomize.py").write_text(SITECUSTOMIZE)
    path = os.pathsep.join(p for p in (str(folder), os.environ.get("PYTHONPATH", "")) if p)
    return boot_live_server(request, None, app, extra_env={"PYTHONPATH": path, "ALLOW_EXTERNAL_API_CALLS": "true"})


@pytest.fixture(scope="module")
def world(github_server, app):
    one = create_fresh_org("solution_architect", org_admin=True)
    two = create_fresh_org("solution_architect", org_admin=True)
    out = {
        "base": str(github_server), "one": one, "two": two,
        "product_one": _seed_product(app, one["org_id"], "Shop Checkout"),
        "product_two": _seed_product(app, two["org_id"], "Field Service"),
    }
    yield out
    remove_organisations(app, [one["org_id"], two["org_id"]])


def product_url(world, key="one"):
    return "%s/architecture/business/Product/%d?ae=1" % (world["base"], world["product_" + key])


def new_page(browser, world, who, width=1440, height=900, scheme="light"):
    context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme)
    context.add_init_script(SKIP_WELCOME)
    context.set_default_timeout(PAGE_TIMEOUT)
    context.set_default_navigation_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    sign_in(page, world["base"], world[who]["emails"]["solution_architect"])
    return context, page


@pytest.fixture(scope="module")
def architect(browser, world):
    context, page = new_page(browser, world, "one")
    yield page
    context.close()


def shoot(page, name, selector=None):
    """Save a screenshot when PRODUCT_SCAN_JOURNEY_SHOTS is set. The page scrolls inside its own frame, so a
    section is captured by its selector."""
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        path = os.path.join(SHOTS, name)
        if selector:
            page.locator(selector).first.screenshot(path=path)
        else:
            page.screenshot(path=path, full_page=True)


def open_product(page, world, key="one"):
    page.goto(product_url(world, key), wait_until="domcontentloaded")
    page.wait_for_selector("[data-testid='product-layers']")


def row_named(page, name, layer=None):
    """The one row whose element is called exactly this (a part called 'Customer Portal Ui' is another row)."""
    scope = "[data-testid='product-layer'][data-layer='%s'] " % layer if layer else ""
    return page.locator(scope + "[data-testid='product-element-row']").filter(
        has=page.get_by_role("link", name=name, exact=True))


def layer_rows(page, layer):
    return page.locator("[data-testid='product-layer'][data-layer='%s'] [data-testid='product-element-row']" % layer)


def test_1_an_unconnected_product_offers_connect_github_and_connecting_returns_to_the_product(architect, world):
    open_product(architect, world)
    section = architect.get_by_test_id("product-bring-in")
    expect(section.get_by_role("heading", name="Bring in repositories")).to_be_visible()
    shoot(architect, "product-unconnected-1440.png", "#bring-in")
    section.get_by_test_id("product-connect-github").click()
    architect.wait_for_url("**/admin/connectors/github?product=*")
    expect(architect.get_by_test_id("github-for-product")).to_contain_text("Shop Checkout")
    form = architect.get_by_test_id("github-connect-form")
    form.get_by_label("GitHub organisation").fill("acme")
    form.get_by_label("Read-only token").fill(TOKEN)
    form.get_by_role("button", name="Connect and read my repositories").click()
    architect.wait_for_url(re.compile(r".*/architecture/business/Product/%d.*" % world["product_one"]))
    expect(architect.get_by_role("heading", name="Shop Checkout").first).to_be_visible()
    expect(architect.get_by_test_id("product-repositories-form")).to_be_visible()
    assert TOKEN not in architect.content(), "the token is never shown"


def test_2_the_person_chooses_two_repositories_and_the_read_proposes_what_they_show(architect, world):
    open_product(architect, world)
    architect.get_by_test_id("product-choose-repositories").click()
    architect.wait_for_selector("[data-testid='product-repositories-form']")
    options = architect.get_by_test_id("product-repository-option")
    expect(options).to_have_count(16)
    shoot(architect, "product-choosing-1440.png", "#bring-in")
    for name in CHOSEN:
        architect.get_by_label(name, exact=True).check()
    architect.get_by_test_id("product-repositories-submit").click()
    architect.wait_for_selector("[data-testid='product-scan-state']")
    waiting = architect.get_by_test_id("product-scan-waiting")
    for _ in range(60):  # the read runs in the background; the page shows its result when it has finished
        if waiting.count():
            break
        architect.wait_for_timeout(3000)
        architect.reload(wait_until="domcontentloaded")
    expect(waiting).to_be_visible()
    expect(architect.get_by_test_id("product-scan-state")).to_contain_text(", ".join(sorted(CHOSEN)))
    shoot(architect, "product-waiting-1440.png", "#bring-in")


def test_3_the_review_lands_the_proposals_and_the_product_page_shows_them_by_layer_with_their_files(architect, world, app):
    architect.get_by_test_id("product-review-link").click()
    architect.wait_for_url("**/admin/connectors/github/review")
    architect.get_by_role("button", name=re.compile(r"^Approve all \(\d+\)$")).click()
    expect(architect.locator("#main-content").get_by_text("Nothing is waiting for review.", exact=False)).to_be_visible()
    open_product(architect, world)

    for name, (layer, repository) in EXPECTED.items():
        row = row_named(architect, name, layer)
        expect(row).to_have_count(1)
        source = row.get_by_test_id("product-element-source")
        expect(source).to_contain_text(repository + ":")
        file = source.inner_text().split(":", 1)[1].strip()
        assert file and file != "None", source.inner_text()
        href = source.get_by_role("link").get_attribute("href")
        assert re.fullmatch(r"https://github\.com/%s/blob/[0-9a-f]{40}/.+" % repository, href), href
        assert href.endswith("/" + file), (href, file)
    expect(architect.get_by_test_id("product-scan-waiting")).to_have_count(0)
    # What nobody chose and nothing joins to it is in the model but not on this product; what a chosen
    # application depends on is, one step out, with the relationship in words and its own file.
    expect(architect.get_by_test_id("product-layers")).not_to_contain_text("Inventory Service")
    expect(architect.get_by_test_id("product-layers")).not_to_contain_text("Mobile App")
    billing = row_named(architect, "Billing Api", "application")
    expect(billing).to_have_count(1)
    expect(billing).to_contain_text("Serves Customer Portal")
    expect(billing.get_by_test_id("product-element-source")).to_contain_text("acme/billing-api:")
    # The figures agree: the summary, the layer counts and the rows are one answer.
    rows = architect.get_by_test_id("product-element-row").count()
    counts = sum(int(c) for c in architect.locator("[data-testid='layer-count']").all_inner_texts())
    assert rows == counts, (rows, counts)
    assert re.search(r"has %d connected elements" % rows, architect.get_by_test_id("product-summary-text").inner_text())
    # Its own earlier members are still there.
    expect(layer_rows(architect, "business").filter(has_text="Shop Checkout ordering")).to_have_count(1)
    shoot(architect, "product-after-1440.png", "#layers")
    shoot(architect, "product-bring-in-after-1440.png", "#bring-in")

    from app import db
    from app.models import ArchiMateRelationship

    with app.app_context():
        joined = ArchiMateRelationship.query.filter_by(
            organization_id=world["one"]["org_id"], source_id=world["product_one"], source_kind="connector").count()
        db.session.remove()
    assert joined == len(EXPECTED), "one join to the product for each element the read landed, got %d" % joined


def test_4_every_element_link_lands_on_a_page_with_content(architect, world):
    open_product(architect, world)
    names = list(EXPECTED)
    for name in names:
        open_product(architect, world)
        row_named(architect, name).get_by_test_id("product-element-link").first.click()
        architect.wait_for_load_state("domcontentloaded")
        expect(architect.get_by_role("heading", name=name).first).to_be_visible()
        assert len(architect.locator("main").inner_text().strip()) > 80


def test_5_on_a_phone_in_the_dark_the_page_still_reads(browser, world):
    context, page = new_page(browser, world, "one", 390, 844, "dark")
    try:
        open_product(page, world)
        overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert overflow <= 0, "the product page scrolls sideways by %spx at 390 wide" % overflow
        for name in EXPECTED:
            expect(row_named(page, name)).to_have_count(1)
        page.get_by_test_id("product-choose-repositories").click()
        page.wait_for_selector("[data-testid='product-repositories-form']")
        overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert overflow <= 0, "the repository list scrolls sideways by %spx at 390 wide" % overflow
        shoot(page, "product-choosing-390-dark.png", "#bring-in")
        open_product(page, world)
        shoot(page, "product-after-390-dark.png", "#layers")
    finally:
        context.close()


def test_6_another_organisation_sees_none_of_it(browser, world):
    context, page = new_page(browser, world, "two")
    try:
        open_product(page, world, "two")
        expect(page.get_by_test_id("product-layers")).not_to_contain_text("Payments Service")
        expect(page.get_by_test_id("product-layers")).not_to_contain_text("Redis")
        expect(page.get_by_test_id("product-connect-github")).to_be_visible()
        expect(page.get_by_test_id("product-scan-waiting")).to_have_count(0)
        # A product of the first organisation is not found, and its repositories cannot be read for it.
        gone = page.goto(product_url(world, "one"), wait_until="domcontentloaded")
        assert gone.status == 404
    finally:
        context.close()
