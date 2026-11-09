"""A Product's page lists every element joined to it, by ArchiMate layer, and nothing from another organisation.

The members shown are compared to the relationship rows themselves, not to the page's own idea of them.
"""

from __future__ import annotations

import json
import uuid

import pytest

from app.models import ArchiMateElement, ArchiMateRelationship
from app.models.archimate_core import SavedDiagram, SavedDiagramElement
from app.models.archimate_missing_elements import Product
from app.models.user import User

pytestmark = pytest.mark.usefixtures("db_session")


def _user(db_session, org):
    user = User(
        email=f"prod-{uuid.uuid4().hex[:10]}@example.com", first_name="Pat", last_name="Product",
        organization_id=org.id, confirmed=True, enterprise_role="enterprise_architect",
    )
    db_session.add(user)
    db_session.flush()
    return user


def _el(db_session, org, name, type_, layer, **extra):
    row = ArchiMateElement(organization_id=org.id, name=name, type=type_, layer=layer, **extra)
    db_session.add(row)
    db_session.flush()
    return row


def _rel(db_session, org, source, target, kind):
    db_session.add(ArchiMateRelationship(organization_id=org.id, source_id=source.id, target_id=target.id, type=kind))
    db_session.flush()


def _estate(db_session, org, suffix):
    """A product with members in four layers, one served step outward, and one element too far to list."""
    product = _el(db_session, org, f"Portal {suffix}", "Product", "business")
    db_session.add(Product(organization_id=org.id, name=product.name, archimate_element_id=product.id))
    service = _el(db_session, org, f"Orders {suffix}", "BusinessService", "Business")
    contract = _el(db_session, org, f"Terms {suffix}", "Contract", "Business")
    capability = _el(db_session, org, f"Ordering {suffix}", "Capability", "Strategy")
    app = _el(db_session, org, f"Order Desk {suffix}", "ApplicationComponent", "Application")
    node = _el(db_session, org, f"Server {suffix}", "Node", "Technology")
    _rel(db_session, org, product, service, "Aggregation")
    _rel(db_session, org, product, contract, "Composition")
    _rel(db_session, org, product, capability, "Association")
    _rel(db_session, org, app, service, "Serving")
    _rel(db_session, org, node, app, "Serving")  # two steps past the service: not on the page
    db_session.flush()
    return {"product": product, "service": service, "contract": contract, "capability": capability, "app": app,
            "node": node}


def _get(client, login_as, user, path):
    login_as(client, user)
    return client.get(path)


def _product_url(product):
    return f"/architecture/business/Product/{product.id}?ae=1"


def test_members_are_the_elements_the_relationships_join(app, db_session, make_org, tenant_ctx):
    from app.modules.architecture.routes.archimate_crud import product_page

    org = make_org("members")
    one = _estate(db_session, org, "a")
    with tenant_ctx(org.id):
        walked = product_page.members(one["product"].id, org.id)
    ids = {r["element"].id for r in walked["rows"]}
    assert ids == {one["service"].id, one["contract"].id, one["capability"].id, one["app"].id}
    by_id = {r["element"].id: r for r in walked["rows"]}
    assert by_id[one["app"].id]["via"] == one["service"].id
    assert by_id[one["service"].id]["via"] is None
    relationships = ArchiMateRelationship.query.filter_by(organization_id=org.id).all()
    joined_somewhere = {r.source_id for r in relationships} | {r.target_id for r in relationships}
    assert ids <= joined_somewhere


def test_page_groups_members_by_layer_with_relationship_in_words(app, db_session, make_org, client, login_as):
    org = make_org("page")
    one = _estate(db_session, org, "b")
    user = _user(db_session, org)
    response = _get(client, login_as, user, _product_url(one["product"]))
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    for heading in ("Strategy", "Business", "Application", "Technology and physical", "Motivation",
                    "Implementation and migration"):
        assert f">{heading}<" in html
    assert "Orders b" in html and "Is grouped in this product" in html
    assert "Is part of this product" in html  # the contract, composed into the product
    assert "Serves Orders b" in html  # the application, one step out, names the service it serves
    assert "Server b" not in html  # two steps out is not listed
    assert "No requirement, goal or driver is joined to this product yet." in html
    assert "has 4 connected elements across 3 layers" in html


def test_another_organisations_elements_never_appear(app, db_session, make_org, client, login_as):
    mine, theirs = make_org("mine"), make_org("theirs")
    one = _estate(db_session, mine, "c")
    other = _estate(db_session, theirs, "d")
    # A relationship row that crosses the boundary: the foreign element must still not be listed.
    _rel(db_session, mine, one["product"], other["service"], "Association")
    user = _user(db_session, mine)
    html = _get(client, login_as, user, _product_url(one["product"])).get_data(as_text=True)
    assert "Orders c" in html
    assert "Orders d" not in html and "Order Desk d" not in html and "Portal d" not in html
    foreign = _user(db_session, theirs)
    assert _get(client, login_as, foreign, _product_url(one["product"])).status_code == 404


def test_linked_material_and_cost_are_read_from_their_stores(app, db_session, make_org, client, login_as):
    from app.models.application_portfolio import ApplicationComponent
    from app.models.architecture_decision import ArchitectureDecision

    org = make_org("linked")
    one = _estate(db_session, org, "e")
    user = _user(db_session, org)
    one["app"].source_kind = "connector"
    one["app"].source_ref = json.dumps({"system": "github", "repository": "acme/order-desk"})
    one["app"].source_label = "GitHub acme/order-desk"
    diagram = SavedDiagram(organization_id=org.id, name="Portal overview")
    db_session.add(diagram)
    db_session.flush()
    db_session.add(SavedDiagramElement(diagram_id=diagram.id, element_id=one["service"].id))
    db_session.add(ArchitectureDecision(
        organization_id=org.id, decision_id=f"AD-{uuid.uuid4().hex[:8]}", title="Take orders through one desk",
        status="accepted", archimate_element_ids=[one["app"].id], enterprise_level=True))
    db_session.add(ApplicationComponent(
        organization_id=org.id, name="Order Desk e", archimate_element_id=one["app"].id,
        total_cost_of_ownership=12000))
    db_session.flush()
    html = _get(client, login_as, user, _product_url(one["product"])).get_data(as_text=True)
    assert "Portal overview" in html
    assert "Take orders through one desk" in html
    assert "GitHub: acme/order-desk" in html and "https://github.com/acme/order-desk" in html
    assert "12,000" in html  # the one cost recorded, from the one cost accessor
    assert "No open gap or risk is recorded against this product" in html


def test_a_member_without_a_cost_blocks_the_total_rather_than_counting_as_zero(app, db_session, make_org, tenant_ctx):
    from app.models.application_portfolio import ApplicationComponent
    from app.modules.architecture.routes.archimate_crud import product_page

    org = make_org("cost")
    one = _estate(db_session, org, "f")
    extra = _el(db_session, org, "Billing f", "ApplicationComponent", "Application")
    _rel(db_session, org, extra, one["service"], "Serving")
    db_session.add(ApplicationComponent(organization_id=org.id, name="Order Desk f",
                                        archimate_element_id=one["app"].id, total_cost_of_ownership=500))
    db_session.add(ApplicationComponent(organization_id=org.id, name="Billing f", archimate_element_id=extra.id))
    db_session.flush()
    with tenant_ctx(org.id):
        walked = product_page.members(one["product"].id, org.id)
        cost = product_page.cost_of(one["product"].id, walked["rows"], org.id)
    assert cost["total"] is None
    assert cost["missing"] == ["Billing f"]
    assert [line["name"] for line in cost["lines"]] == ["Order Desk f"]


def test_adding_to_an_empty_layer_lists_the_new_element(app, db_session, make_org, client, login_as):
    org = make_org("add")
    one = _estate(db_session, org, "g")
    user = _user(db_session, org)
    login_as(client, user)
    response = client.post(
        f"/architecture/products/{one['product'].id}/elements",
        data={"section": "technology", "element_type": "Node", "name": "Edge cache g"}, follow_redirects=True)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Edge cache g" in html and "Is associated with this product" in html
    row = ArchiMateRelationship.query.filter_by(source_id=one["product"].id, organization_id=org.id).join(
        ArchiMateElement, ArchiMateElement.id == ArchiMateRelationship.target_id).filter(
        ArchiMateElement.name == "Edge cache g").first()
    assert row is not None and row.type == "association"


def test_adding_refuses_a_type_the_layer_does_not_offer_and_another_organisations_product(
        app, db_session, make_org, client, login_as):
    mine, theirs = make_org("add-a"), make_org("add-b")
    one = _estate(db_session, mine, "h")
    other = _estate(db_session, theirs, "i")
    user = _user(db_session, mine)
    login_as(client, user)
    client.post(f"/architecture/products/{one['product'].id}/elements",
                data={"section": "technology", "element_type": "Goal", "name": "Wrong layer h"})
    assert ArchiMateElement.query.filter_by(name="Wrong layer h", organization_id=mine.id).first() is None
    login_as(client, user)
    refused = client.post(f"/architecture/products/{other['product'].id}/elements",
                          data={"section": "technology", "element_type": "Node", "name": "Intruder h"})
    assert refused.status_code == 404
    assert ArchiMateElement.query.filter_by(name="Intruder h").first() is None


def test_export_holds_every_section(app, db_session, make_org, client, login_as):
    org = make_org("export")
    one = _estate(db_session, org, "j")
    user = _user(db_session, org)
    response = _get(client, login_as, user, f"/architecture/products/{one['product'].id}/export")
    assert response.status_code == 200
    body = json.loads(response.get_data(as_text=True))
    assert [layer["layer"] for layer in body["layers"]] == [
        "Strategy", "Business", "Application", "Technology and physical", "Motivation",
        "Implementation and migration"]
    business = next(layer for layer in body["layers"] if layer["layer"] == "Business")
    assert {e["name"] for e in business["elements"]} == {"Orders j", "Terms j"}
    assert body["product"]["name"] == "Portal j"


def test_every_preset_relationship_is_one_the_matrix_allows():
    from app.config.archimate_relationship_matrix import is_valid_relationship
    from app.modules.architecture.routes.archimate_crud import product_page

    for key, _label, _spellings, types, relationship in product_page.SECTIONS:
        for element_type in types:
            assert is_valid_relationship("Product", element_type, relationship), (key, element_type)
