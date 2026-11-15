"""An architect adds to a product's Application section: the element that already exists is found and joined.

The goal: "put the application Ifiok under the product Ifiok without making a second Ifiok". The journey signs in
to an organisation that holds both, opens the product, opens Add on the Application section, types a few letters,
and picks the existing application with the keyboard. The section lists it and the organisation's element count
is unchanged. A misspelt name offers "Did you mean Ifiok?" above the create option, and creating makes exactly one
element. Joining twice says so and adds no second relationship. Another organisation's element of the same name
never shows. Run at 1440 wide in the light scheme and at 390 wide in the dark scheme.
"""

import os
from pathlib import Path

import pytest

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK = os.environ.get("PRODUCT_JOIN_WALK_DIR")
VIEWS = [("light", 1440, 900), ("dark", 390, 844)]


def _seed(app, org_id, other_org_id):
    from app import db
    from app.models.archimate_core import ArchiMateElement
    from app.models.archimate_missing_elements import Product

    with app.app_context():
        product = ArchiMateElement(organization_id=org_id, name="Ifiok", type="Product", layer="business")
        application = ArchiMateElement(organization_id=org_id, name="Ifiok", type="ApplicationComponent",
                                       layer="Application")
        stranger = ArchiMateElement(organization_id=other_org_id, name="Ifiok Elsewhere",
                                    type="ApplicationComponent", layer="Application")
        db.session.add_all([product, application, stranger])
        db.session.flush()
        db.session.add(Product(organization_id=org_id, name="Ifiok", archimate_element_id=product.id))
        db.session.commit()
        out = {"product_id": product.id, "application_id": application.id, "stranger_id": stranger.id}
        db.session.remove()
    return out


def _counts(app, org_id, product_id):
    from app import db
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship

    with app.app_context():
        elements = ArchiMateElement.query.filter_by(organization_id=org_id).count()
        joins = ArchiMateRelationship.query.filter_by(organization_id=org_id, source_id=product_id).count()
        db.session.remove()
    return elements, joins


@pytest.fixture(params=VIEWS, ids=["1440-light", "390-dark"])
def world(request, browser, live_server, app):
    scheme, width, height = request.param
    mine = create_fresh_org("enterprise_architect")
    theirs = create_fresh_org("enterprise_architect")
    ids = _seed(app, mine["org_id"], theirs["org_id"])
    context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme)
    context.set_default_timeout(PAGE_TIMEOUT)
    context.set_default_navigation_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    sign_in(page, live_server, mine["emails"]["enterprise_architect"])
    if scheme == "dark":
        page.add_init_script("document.documentElement.classList.add('dark')")
    yield {"page": page, "scheme": scheme, "width": width, "org_id": mine["org_id"], "base": live_server, **ids}
    context.close()


def _shot(world, name):
    if WALK:
        Path(WALK).mkdir(parents=True, exist_ok=True)
        world["page"].wait_for_timeout(500)
        world["page"].screenshot(path=os.path.join(WALK, "%s-%s-%d.png" % (name, world["scheme"], world["width"])))


def _open_add(world):
    page = world["page"]
    page.goto("%s/architecture/business/Product/%d?ae=1" % (world["base"], world["product_id"]),
              wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_selector("[data-testid='product-layers']", timeout=PAGE_TIMEOUT)
    page.wait_for_load_state("networkidle")
    page.locator("[data-testid='product-add-application']").focus()
    page.keyboard.press("Enter")
    form = page.locator("[data-testid='product-add-form-application']")
    form.wait_for(state="visible")
    return form


def _application_section(page):
    return page.locator("[data-testid='product-layer'][data-layer='application']")


def test_a_few_letters_find_the_existing_element_and_joining_creates_nothing(world, app):
    page = world["page"]
    form = _open_add(world)
    before = _counts(app, world["org_id"], world["product_id"])
    box = form.get_by_role("combobox")
    box.focus()
    page.keyboard.type("Ifi")
    options = form.get_by_role("option")
    options.first.wait_for(state="visible")
    texts = [t for t in options.all_inner_texts()]
    assert any(t.startswith("Ifiok (") for t in texts)
    assert not any("Elsewhere" in t for t in texts)
    _shot(world, "search")
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")
    page.wait_for_selector("[data-testid='product-layers']", timeout=PAGE_TIMEOUT)
    section = _application_section(page)
    assert "Ifiok" in section.inner_text()
    assert section.locator("[data-testid='layer-empty']").count() == 0
    assert "joined to" in page.inner_text("body")
    assert _counts(app, world["org_id"], world["product_id"]) == (before[0], before[1] + 1)
    _shot(world, "joined")

    # joining it a second time says so and adds nothing
    form = _open_add(world)
    form.get_by_role("combobox").focus()
    page.keyboard.type("Ifi")
    form.get_by_role("option").first.wait_for(state="visible")
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")
    page.wait_for_selector("[data-testid='product-layers']", timeout=PAGE_TIMEOUT)
    assert "already joined" in page.inner_text("body")
    assert _counts(app, world["org_id"], world["product_id"]) == (before[0], before[1] + 1)
    _shot(world, "already-joined")


def test_a_misspelt_name_offers_did_you_mean_above_create_and_creates_one_element(world, app):
    page = world["page"]
    form = _open_add(world)
    before = _counts(app, world["org_id"], world["product_id"])
    form.get_by_role("combobox").focus()
    page.keyboard.type("Ifiokk")
    near = form.locator("[data-testid='product-add-near-option']")
    near.first.wait_for(state="visible")
    assert near.first.inner_text().strip() == "Did you mean Ifiok?"
    create = form.locator("[data-testid='product-add-create-application-submit']")
    # the near match sits above the create option
    assert near.first.bounding_box()["y"] < create.bounding_box()["y"]
    _shot(world, "near-match")
    form.locator("select[name=element_type]").select_option("ApplicationComponent")
    assert create.inner_text().strip() == "Create a new application named 'Ifiokk'"
    create.click()
    page.wait_for_selector("[data-testid='product-layers']", timeout=PAGE_TIMEOUT)
    assert "Ifiokk" in _application_section(page).inner_text()
    assert _counts(app, world["org_id"], world["product_id"]) == (before[0] + 1, before[1] + 1)


def test_no_create_option_while_an_exact_match_exists(world):
    page = world["page"]
    form = _open_add(world)
    form.get_by_role("combobox").focus()
    page.keyboard.type("ifiok")
    form.get_by_role("option").first.wait_for(state="visible")
    assert form.locator("[data-testid='product-add-create-application']").is_hidden()
