"""A data architect draws the logical data model of a domain.

Goal: open the Customer domain's entity relationship diagram, see the entities and
how they relate, add an entity with attributes, relate it one to many to another,
and find that the finder, the status line and the diagram agree, the entity is the
same record on the data objects list and in the architecture model, and another
organisation sees none of it.
"""

from __future__ import annotations

import os
import re
import pathlib
import uuid

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = pathlib.Path(os.environ.get("DATAERD_SHOTS", "/home/ubuntu/verify/seats/dataerd01/walk"))


def _uid():
    return uuid.uuid4().hex[:8]


def _seed_org(suffix):
    """An organisation with the demonstration data model: three domains whose
    entities carry attributes and are related to one another."""
    from app import create_app, db
    from app.commands.seed_demo_company import _seed_data_model
    from app.models.organization import Organization
    from app.models.user import Role, User

    app = create_app("testing")
    out = {"emails": {}}
    with app.app_context():
        Role.insert_roles()
        org = Organization(name=f"ERD Org {suffix}", slug=f"erd-{suffix}")
        db.session.add(org)
        db.session.commit()
        out["org_id"] = org.id
        email = f"erd.architect.{suffix}@example.com"
        user = User(email=email, first_name="Dana", last_name="Data", organization_id=org.id,
                    enterprise_role="data_architect", confirmed=True)
        user.role = Role.query.filter_by(name="Architect").one()
        user.password = PASSWORD
        db.session.add(user)
        _seed_data_model(org.id)
        db.session.commit()
        out["email"] = email
        db.session.remove()
    return out


def _assert_persona(email):
    from app import create_app, db
    from app.models.user import User

    app = create_app("testing")
    with app.app_context():
        user = User.query.filter_by(email=email).one()
        assert user.enterprise_role == "data_architect", user.enterprise_role
        assert not user.is_platform_admin and not user.is_org_admin
        assert user.role.name not in ("Administrator", "Platform Admin")
        db.session.remove()


def _sign_in(page, base, email):
    page.goto(f"{base}/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


@pytest.fixture(scope="module")
def world():
    return {"a": _seed_org(_uid()), "b": _seed_org(_uid())}


def _entities_on_canvas(page):
    return page.locator("#erd-canvas .joint-type-dataerd-entity")


def _links_on_canvas(page):
    return page.locator("#erd-canvas .joint-type-dataerd-relationship")


def _open_customer_diagram(page, base):
    page.goto(f"{base}/architecture/data-entities", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.get_by_role("link", name="Diagram").first.click()
    page.wait_for_url("**/data-entities/diagram**", timeout=PAGE_TIMEOUT)
    page.locator("#erd-domain").select_option(label="Customer")
    page.wait_for_url("**domain_id=**", timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("status-counts")).to_be_visible()


def test_architect_draws_and_relates_an_entity(page, live_server, world):
    org = world["a"]
    _assert_persona(org["email"])
    page.set_viewport_size({"width": 1440, "height": 900})
    _sign_in(page, str(live_server), org["email"])
    _open_customer_diagram(page, str(live_server))

    # The seeded domain opens filled: four entities, three relationships, and the
    # finder, the status line and the drawing all say so.
    expect(page.get_by_test_id("status-counts")).to_have_text("4 entities, 3 relationships")
    expect(_entities_on_canvas(page)).to_have_count(4)
    expect(_links_on_canvas(page)).to_have_count(3)
    finder = page.get_by_test_id("finder-list")
    for name in ("Customer", "Contact", "Delivery Address", "Consent"):
        expect(finder.get_by_text(name, exact=True)).to_be_visible()
    expect(page.get_by_test_id("finder-count")).to_have_text("4")
    # The attributes are drawn with their keys and types.
    expect(_entities_on_canvas(page).filter(has_text="customer_id")).not_to_have_count(0)
    expect(_entities_on_canvas(page).filter(has_text="PK")).not_to_have_count(0)
    # The legend names the ends that are drawn.
    legend = page.get_by_test_id("diagram-legend")
    expect(legend).to_contain_text("Exactly one")
    expect(legend).to_contain_text("Zero or many")
    page.screenshot(path=str(SHOTS / "journey-1440-light-filled.png"))

    # Add an entity with three attributes.
    page.get_by_test_id("erd-add-entity").click()
    page.get_by_test_id("erd-entity-name").fill("Loyalty Card")
    rows = page.get_by_test_id("erd-attribute-row")
    page.get_by_test_id("erd-add-attribute").click()
    page.get_by_test_id("erd-add-attribute").click()
    expect(rows).to_have_count(3)
    names = ["card_id", "customer_id", "points"]
    for index, attr in enumerate(names):
        rows.nth(index).get_by_test_id("erd-attribute-name").fill(attr)
    rows.nth(0).get_by_test_id("erd-attribute-type").select_option("uuid")
    rows.nth(0).get_by_test_id("erd-attribute-pk").check()
    rows.nth(1).get_by_test_id("erd-attribute-type").select_option("uuid")
    rows.nth(1).get_by_test_id("erd-attribute-fk").check()
    rows.nth(2).get_by_test_id("erd-attribute-type").select_option("integer")
    page.screenshot(path=str(SHOTS / "journey-1440-light-add-entity.png"))
    page.get_by_test_id("erd-save-entity").click()
    expect(page.get_by_test_id("status-counts")).to_have_text("5 entities, 3 relationships")
    expect(_entities_on_canvas(page)).to_have_count(5)
    expect(page.get_by_test_id("finder-list").get_by_text("Loyalty Card", exact=True)).to_be_visible()
    expect(_entities_on_canvas(page).filter(has_text="points")).to_have_count(1)

    # Relate Customer to Loyalty Card, one to many.
    page.get_by_test_id("tool-connect").click()
    _entities_on_canvas(page).filter(has_text="legal_name").click()
    _entities_on_canvas(page).filter(has_text="card_id").click()
    expect(page.get_by_test_id("erd-rel-summary")).to_have_text("Customer to Loyalty Card")
    expect(page.get_by_test_id("erd-rel-preset")).to_have_value("one-to-many")
    page.get_by_test_id("erd-rel-label").fill("holds")
    page.get_by_test_id("erd-save-relationship").click()
    expect(page.get_by_test_id("status-counts")).to_have_text("5 entities, 4 relationships")
    expect(_links_on_canvas(page)).to_have_count(4)
    page.screenshot(path=str(SHOTS / "journey-1440-light-related.png"))

    # It persisted: after a reload the drawing, the finder and the status line agree.
    page.reload(wait_until="domcontentloaded")
    expect(page.get_by_test_id("status-counts")).to_have_text("5 entities, 4 relationships")
    expect(_entities_on_canvas(page)).to_have_count(5)
    expect(_links_on_canvas(page)).to_have_count(4)
    expect(page.get_by_test_id("finder-count")).to_have_text("5")

    # The same record is on the data objects list, and in the architecture model.
    page.goto(f"{live_server}/architecture/data-entities?search=Loyalty", wait_until="domcontentloaded")
    link = page.get_by_role("link", name="Loyalty Card", exact=True)
    expect(link).to_be_visible()
    link.click()
    page.wait_for_url("**/data-entities/*", timeout=PAGE_TIMEOUT)
    expect(page.get_by_role("heading", name="Loyalty Card").first).to_be_visible()
    for attr in names:
        expect(page.get_by_text(attr, exact=True).first).to_be_visible()
    model_link = page.get_by_test_id("entity-archimate-link")
    expect(model_link).to_be_visible()
    model_link.click()
    page.wait_for_load_state("domcontentloaded")
    expect(page.get_by_text("Loyalty Card").first).to_be_visible()
    expect(page.get_by_text(re.compile(r"Data ?Object", re.I)).first).to_be_visible()


def test_another_organisation_sees_only_its_own_model(page, live_server, world):
    a, b = world["a"], world["b"]
    _sign_in(page, str(live_server), b["email"])
    page.set_viewport_size({"width": 1440, "height": 900})
    _open_customer_diagram(page, str(live_server))
    expect(page.get_by_test_id("status-counts")).to_have_text("4 entities, 3 relationships")
    expect(page.get_by_test_id("finder-list").get_by_text("Loyalty Card", exact=True)).to_have_count(0)
    # A domain of the first organisation is not reachable by id.
    from app import create_app, db
    from app.models.process_data import DataDomain

    app = create_app("testing")
    with app.app_context():
        foreign = DataDomain.query.filter_by(organization_id=a["org_id"], name="Finance").one().id
        db.session.remove()
    response = page.request.get(f"{live_server}/architecture/api/data-erd/{foreign}")
    assert response.status == 404
    page.goto(f"{live_server}/architecture/data-entities/diagram?domain_id={foreign}", wait_until="domcontentloaded")
    expect(page.get_by_test_id("erd-domain")).not_to_have_value(str(foreign))


def test_empty_domain_says_why_and_offers_the_next_step(page, live_server, world):
    from app import create_app, db
    from app.models.process_data import DataDomain

    org = world["a"]
    app = create_app("testing")
    with app.app_context():
        domain = DataDomain(name="Archive", organization_id=org["org_id"])
        db.session.add(domain)
        db.session.commit()
        domain_id = domain.id
        db.session.remove()
    page.set_viewport_size({"width": 1440, "height": 900})
    _sign_in(page, str(live_server), org["email"])
    page.goto(f"{live_server}/architecture/data-entities/diagram?domain_id={domain_id}", wait_until="domcontentloaded")
    empty = page.get_by_test_id("erd-empty")
    expect(empty).to_contain_text("This domain has no entities yet")
    empty.get_by_role("button", name="Add the first entity").click()
    expect(page.get_by_test_id("erd-entity-name")).to_be_visible()
    expect(page.get_by_test_id("status-counts")).to_have_text("0 entities, 0 relationships")


def test_a_selected_entity_opens_its_record_and_every_control_does_something(page, live_server, world):
    """From the diagram, one click on an entity shows what it is and opens its
    record; the record names the list it belongs to and says plainly which
    facts are not recorded yet; the help button opens real help."""
    org = world["a"]
    page.set_viewport_size({"width": 1440, "height": 900})
    _sign_in(page, str(live_server), org["email"])
    _open_customer_diagram(page, str(live_server))

    # Nothing is selected: the selection bar is out of the way.
    expect(page.get_by_test_id("erd-selection")).to_be_hidden()
    _entities_on_canvas(page).filter(has_text="legal_name").click()
    bar = page.get_by_test_id("erd-selection")
    expect(bar).to_be_visible()
    expect(page.get_by_test_id("erd-selection-name")).to_have_text("Customer")
    expect(bar).to_contain_text("4 attributes")
    expect(page.get_by_test_id("status-selection")).to_have_text("Selected: Customer")
    open_record = page.get_by_test_id("erd-open-record")
    expect(open_record).to_have_attribute("href", re.compile(r"/architecture/data-entities/\d+$"))

    # The toolbar's Properties button edits the selected entity, and the dialog links to the record too.
    page.get_by_test_id("action-properties").click()
    expect(page.get_by_test_id("erd-entity-name")).to_have_value("Customer")
    expect(page.get_by_test_id("erd-edit-open-record")).to_have_attribute("href", re.compile(r"/architecture/data-entities/\d+$"))
    page.get_by_role("button", name="Cancel").first.click()
    expect(page.get_by_test_id("erd-edit-form")).to_be_hidden()

    # The help button opens the shortcuts this page answers to.
    page.get_by_role("button", name="Keyboard Shortcuts").click()
    shortcuts = page.get_by_test_id("erd-shortcuts")
    expect(shortcuts).to_be_visible()
    expect(shortcuts).to_contain_text("Space + drag")
    expect(shortcuts).to_contain_text("Double-click")
    page.keyboard.press("Escape")
    expect(shortcuts).to_be_hidden()

    # The record: its list is "Data objects" everywhere, and a fact that is not recorded says so.
    expect(page.get_by_test_id("erd-selection")).to_be_visible()
    page.screenshot(path=str(SHOTS / "journey-1440-light-selected.png"))
    open_record.click()
    page.wait_for_url(re.compile(r"/architecture/data-entities/\d+$"), timeout=PAGE_TIMEOUT)
    expect(page.get_by_role("heading", name="Customer").first).to_be_visible()
    expect(page.get_by_role("link", name="Data objects")).to_be_visible()
    expect(page.get_by_text("Entity Catalog")).to_have_count(0)
    facts = page.get_by_test_id("entity-facts")
    expect(facts).to_contain_text("Not recorded")
    expect(facts).not_to_contain_text("—")
    record_it = facts.get_by_role("link", name=re.compile(r"^Record the")).first
    record_it.click()
    page.wait_for_url(re.compile(r"/data-entities/\d+/edit$"), timeout=PAGE_TIMEOUT)
    expect(page.get_by_role("heading").first).to_be_visible()


def test_on_a_phone_a_selected_entity_still_opens_its_record(page, live_server, world):
    org = world["b"]
    page.set_viewport_size({"width": 390, "height": 844})
    _sign_in(page, str(live_server), org["email"])
    _open_customer_diagram(page, str(live_server))
    _entities_on_canvas(page).filter(has_text="legal_name").click()
    expect(page.get_by_test_id("erd-selection-name")).to_have_text("Customer")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    page.screenshot(path=str(SHOTS / "journey-390-light-selected.png"))
    page.get_by_test_id("erd-open-record").click()
    page.wait_for_url(re.compile(r"/architecture/data-entities/\d+$"), timeout=PAGE_TIMEOUT)
    expect(page.get_by_role("heading", name="Customer").first).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")


def test_diagram_fits_a_phone(page, live_server, world):
    org = world["b"]
    page.set_viewport_size({"width": 390, "height": 844})
    _sign_in(page, str(live_server), org["email"])
    _open_customer_diagram(page, str(live_server))
    expect(_entities_on_canvas(page)).to_have_count(4)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    page.get_by_test_id("shell-rail-toggle").click()
    expect(page.get_by_test_id("finder-list")).to_be_visible()
    page.screenshot(path=str(SHOTS / "journey-390-light-finder.png"))
