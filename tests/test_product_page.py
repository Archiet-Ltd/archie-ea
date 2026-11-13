"""A Product's page lists every element joined to it, by ArchiMate layer, and nothing from another organisation.

The members shown are compared to the relationship rows themselves, not to the page's own idea of them.
"""

from __future__ import annotations

import json
import re
import uuid

import pytest

from app.models import ArchiMateElement, ArchiMateRelationship
from app.models.archimate_core import SavedDiagram, SavedDiagramElement
from app.models.archimate_missing_elements import Product
from app.models.user import User

pytestmark = pytest.mark.usefixtures("db_session")


def _user(db_session, org, role="enterprise_architect"):
    user = User(
        email=f"prod-{uuid.uuid4().hex[:10]}@example.com", first_name="Pat", last_name="Product",
        organization_id=org.id, confirmed=True, enterprise_role=role,
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
    # Cross-org relationships are now rejected by the model guard; the
    # product page still filters by organisation_id so foreign elements
    # never appear even if a relationship existed.
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
    user = _user(db_session, org, "cto")
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


def _viewer(db_session, org):
    from app.models.org_role import OrgRole
    from app.models.user import Role

    user = User(
        email=f"view-{uuid.uuid4().hex[:10]}@example.com", first_name="Vic", last_name="Viewer",
        organization_id=org.id, confirmed=True, enterprise_role="viewer", role=Role.query.filter_by(name="User").first(),
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    OrgRole.set_role(org.id, user.id, "viewer")
    db_session.flush()
    return user


def _named_user(db_session, org, first, last):
    user = User(
        email=f"{first.lower()}-{uuid.uuid4().hex[:8]}@example.com", first_name=first, last_name=last,
        organization_id=org.id, confirmed=True, enterprise_role="enterprise_architect",
    )
    db_session.add(user)
    db_session.flush()
    return user


def test_a_capability_owner_is_read_through_the_capability_record_whose_ids_differ_from_the_element(
        app, db_session, make_org, client, login_as):
    """The element id and the capability record id differ, and another capability sits at the element's id:
    the owner shown is the one on the record the element maps to."""
    from app.models.application_owner import ApplicationOwner
    from app.models.business_capabilities import BusinessCapability
    from app.models.unified_capability import UnifiedCapability

    org = make_org("cap-owner")
    one = _estate(db_session, org, "k")
    user = _user(db_session, org)
    real, decoy = _named_user(db_session, org, "Rhea", "Realowner"), _named_user(db_session, org, "Dana", "Decoyowner")
    # Explicit, distinct ids far from any sequence: the unified table's provenance key is unique across the database.
    element_id = 800_000_000 + uuid.uuid4().int % 90_000_000
    record_id = element_id + 100_000_000
    cap = _el(db_session, org, "Fulfilment k", "Capability", "Strategy", id=element_id)
    _rel(db_session, org, one["product"], cap, "Association")
    record = BusinessCapability(id=record_id, organization_id=org.id, name="Fulfilment k", level=1,
                                archimate_element_id=element_id)
    db_session.add(record)
    db_session.flush()
    # Saving the capability record projects it into the unified store; the second row sits at the element's id.
    projected = UnifiedCapability.query.filter_by(
        organization_id=org.id, source_table="business_capability", source_id=str(record_id)).one()
    collider = UnifiedCapability(organization_id=org.id, name="Someone else's capability", level=1,
                                 source_table="business_capability", source_id=str(element_id))
    db_session.add(collider)
    db_session.flush()
    for owner, target in ((real, projected), (decoy, collider)):
        db_session.add(ApplicationOwner(organization_id=org.id, user_id=owner.id, element_type="capability",
                                        element_id=target.id, ownership_type="primary"))
    db_session.flush()
    html = _get(client, login_as, user, _product_url(one["product"])).get_data(as_text=True)
    assert "Rhea Realowner" in html
    assert "Decoyowner" not in html


def test_owner_words_say_why_only_where_it_is_true(app, db_session, make_org, client, login_as):
    org = make_org("owner-words")
    one = _estate(db_session, org, "l")
    user = _user(db_session, org)
    html = _get(client, login_as, user, _product_url(one["product"])).get_data(as_text=True)
    rows = {m.group(1): m.group(0) for m in re.finditer(
        r'<tr[^>]*data-testid="product-element-row".*?data-testid="product-element-link"[^>]*>([^<]+)</a>.*?</tr>',
        html, re.S)}
    assert "No owner recorded" in rows["Ordering l"] and "Owners are not kept" not in rows["Ordering l"]
    assert "Owners are not kept for this type" in rows["Orders l"]


def test_an_application_element_with_no_portfolio_record_is_named_and_blocks_the_total(
        app, db_session, make_org, tenant_ctx):
    from app.models.application_portfolio import ApplicationComponent
    from app.modules.architecture.routes.archimate_crud import product_page

    org = make_org("no-record")
    one = _estate(db_session, org, "m")
    bare = _el(db_session, org, "Unlisted m", "ApplicationComponent", "Application")
    _rel(db_session, org, bare, one["service"], "Serving")
    db_session.add(ApplicationComponent(organization_id=org.id, name="Order Desk m",
                                        archimate_element_id=one["app"].id, total_cost_of_ownership=700))
    db_session.flush()
    with tenant_ctx(org.id):
        walked = product_page.members(one["product"].id, org.id)
        cost = product_page.cost_of(one["product"].id, walked["rows"], org.id)
    assert cost["missing"] == ["Unlisted m"]
    assert cost["total"] is None
    assert cost["counted"] == 2


def test_gaps_and_risks_read_in_plain_words_and_a_risk_opens_its_own_page(app, db_session, make_org, client, login_as):
    from app.models.implementation_migration import Gap
    from app.models.risk import Risk, RiskStatus

    org = make_org("problems")
    one = _estate(db_session, org, "n")
    user = _user(db_session, org)
    gap = Gap(organization_id=org.id, name="No batch certificate n", archimate_element_id=one["app"].id,
              resolution_status="in_progress")
    risk = Risk(organization_id=org.id, title="Ledger staleness n", likelihood=3, impact=3, status=RiskStatus.OPEN,
                archimate_element_id=one["app"].id)
    db_session.add_all([gap, risk])
    db_session.flush()
    html = _get(client, login_as, user, _product_url(one["product"])).get_data(as_text=True)
    assert "Gap, in progress" in html and "in_progress" not in html
    assert f'href="/risks/{risk.id}"' in html
    page = client.get(f"/risks/{risk.id}")
    assert page.status_code == 200 and "Ledger staleness n" in page.get_data(as_text=True)


def test_an_empty_product_explains_every_empty_layer_and_the_empty_summary(app, db_session, make_org, client, login_as):
    org = make_org("empty")
    product = _el(db_session, org, "Bare product", "Product", "business")
    user = _user(db_session, org, "cto")
    html = _get(client, login_as, user, _product_url(product)).get_data(as_text=True)
    assert "No element is joined to Bare product yet." in html
    for sentence in ("No capability, resource or course of action is joined to this product yet.",
                     "No business service, contract or process is joined to this product yet.",
                     "No application is joined to this product, directly or through a business service it is made of.",
                     "No requirement, goal or driver is joined to this product yet."):
        assert sentence in html
    assert "No repository is connected to this product or its elements." in html
    assert "No application or technology in this product has a yearly cost recorded, so no total is shown." in html


def test_a_repository_connected_to_a_member_is_listed_with_its_address_and_the_empty_sentence_is_absent(
        app, db_session, make_org, client, login_as):
    org = make_org("repo")
    one = _estate(db_session, org, "o")
    user = _user(db_session, org)
    one["app"].source_kind = "connector"
    one["app"].source_ref = json.dumps({"system": "github", "repository": "acme/desk-o"})
    one["app"].source_label = "GitHub acme/desk-o"
    db_session.flush()
    html = _get(client, login_as, user, _product_url(one["product"])).get_data(as_text=True)
    assert 'href="https://github.com/acme/desk-o"' in html
    assert "No repository is connected to this product or its elements." not in html


def test_a_viewer_sees_no_add_control_and_the_add_route_refuses_them(app, db_session, make_org, client, login_as):
    org = make_org("viewer")
    one = _estate(db_session, org, "p")
    viewer = _viewer(db_session, org)
    html = _get(client, login_as, viewer, _product_url(one["product"])).get_data(as_text=True)
    assert 'data-testid="product-add-technology"' not in html
    assert "Server p" not in html and "Orders p" in html  # the page itself is readable
    login_as(client, viewer)
    refused = client.post(f"/architecture/products/{one['product'].id}/elements",
                          data={"section": "technology", "element_type": "Node", "name": "Viewer attempt p"})
    assert refused.status_code == 403
    assert ArchiMateElement.query.filter_by(name="Viewer attempt p", organization_id=org.id).first() is None


def test_linked_material_gaps_risks_and_cost_of_another_organisation_never_appear(
        app, db_session, make_org, client, login_as):
    """Organisation B holds a diagram, a decision, a gap, a risk and a cost on the very elements that organisation
    A's product lists; the page shows A's own and none of B's."""
    from app.models.application_portfolio import ApplicationComponent
    from app.models.architecture_decision import ArchitectureDecision
    from app.models.implementation_migration import Gap
    from app.models.risk import Risk, RiskStatus

    mine, theirs = make_org("iso-a"), make_org("iso-b")
    one = _estate(db_session, mine, "q")
    user = _user(db_session, mine, "cto")
    own_diagram = SavedDiagram(organization_id=mine.id, name="Own overview q")
    foreign_diagram = SavedDiagram(organization_id=theirs.id, name="Foreign overview q")
    db_session.add_all([own_diagram, foreign_diagram])
    db_session.flush()
    for diagram in (own_diagram, foreign_diagram):
        db_session.add(SavedDiagramElement(diagram_id=diagram.id, element_id=one["service"].id))
    for org, title in ((mine, "Own decision q"), (theirs, "Foreign decision q")):
        db_session.add(ArchitectureDecision(
            organization_id=org.id, decision_id=f"AD-{uuid.uuid4().hex[:8]}", title=title, status="accepted",
            archimate_element_ids=[one["app"].id], enterprise_level=True))
    for org, label in ((mine, "Own"), (theirs, "Foreign")):
        db_session.add(Gap(organization_id=org.id, name=f"{label} gap q", archimate_element_id=one["app"].id,
                           resolution_status="identified"))
        db_session.add(Risk(organization_id=org.id, title=f"{label} risk q", likelihood=2, impact=2,
                            status=RiskStatus.OPEN, archimate_element_id=one["app"].id))
        db_session.add(ApplicationComponent(organization_id=org.id, name=f"{label} desk q",
                                            archimate_element_id=one["app"].id,
                                            total_cost_of_ownership=111 if org is mine else 999999))
    db_session.flush()
    html = _get(client, login_as, user, _product_url(one["product"])).get_data(as_text=True)
    for own in ("Own overview q", "Own decision q", "Own gap q", "Own risk q", "111"):
        assert own in html, own
    for foreign in ("Foreign overview q", "Foreign decision q", "Foreign gap q", "Foreign risk q", "999,999",
                    "Foreign desk q"):
        assert foreign not in html, foreign


def test_seeded_product_diagrams_place_no_two_boxes_on_top_of_each_other(app, db_session, make_org):
    """A diagram box is drawn up to 180 wide and 130 high; the seeded positions leave a gap beyond that, also on a
    diagram an earlier seed had placed too close."""
    from app.commands.seed_demo_company import _seed_products

    org = make_org("seed-diagrams")
    _seed_products(org.id)
    diagram = SavedDiagram.query.filter(
        SavedDiagram.organization_id == org.id, SavedDiagram.name.like("%: what it is made of")).first()
    assert diagram is not None
    squeezed = SavedDiagramElement.query.filter_by(diagram_id=diagram.id).all()
    for row in squeezed:  # an earlier seed's spacing
        row.position_y = 40 + 110 * (squeezed.index(row) // 4)
    db_session.flush()
    _seed_products(org.id)
    for one in SavedDiagram.query.filter(
            SavedDiagram.organization_id == org.id, SavedDiagram.name.like("%: what it is made of")).all():
        boxes = SavedDiagramElement.query.filter_by(diagram_id=one.id).all()
        assert len(boxes) >= 5
        for i, a in enumerate(boxes):
            for b in boxes[i + 1:]:
                apart_across = abs(a.position_x - b.position_x) >= 180
                apart_down = abs(a.position_y - b.position_y) >= 130
                assert apart_across or apart_down, (one.name, a.position_x, a.position_y, b.position_x, b.position_y)


def _document(db_session, org, tmp_path, label):
    """An application of the organisation with one real file on disk and its document record."""
    from app.models.application_portfolio import ApplicationComponent
    from app.models.miscellaneous import ArchitectureDocument

    application = ApplicationComponent(organization_id=org.id, name=f"Ledger {label}")
    db_session.add(application)
    db_session.flush()
    path = tmp_path / f"{label}.pdf"
    path.write_bytes(f"%PDF-1.4 content of {label}".encode())
    document = ArchitectureDocument(
        application_id=application.id, filename=f"{label}.pdf", file_path=str(path), mime_type="application/pdf",
        file_size=path.stat().st_size)
    db_session.add(document)
    db_session.flush()
    return application, document


def _download_url(application, document):
    from flask import current_app, url_for

    with current_app.test_request_context():
        return url_for("application_mgmt.api_download_architecture_document", id=application.id, doc_id=document.id)


def test_a_document_link_downloads_the_file_of_the_signed_in_organisation(
        app, db_session, make_org, client, login_as, tmp_path):
    mine = make_org("doc-a")
    application, document = _document(db_session, mine, tmp_path, "own-design")
    response = _get(client, login_as, _user(db_session, mine), _download_url(application, document))
    assert response.status_code == 200
    assert response.data == b"%PDF-1.4 content of own-design"
    assert "own-design.pdf" in response.headers["Content-Disposition"]


def test_a_document_of_another_organisation_answers_404_and_downloads_nothing(
        app, db_session, make_org, client, login_as, tmp_path):
    mine, theirs = make_org("doc-a"), make_org("doc-b")
    own_application, own_document = _document(db_session, mine, tmp_path, "own-design")
    foreign_application, foreign_document = _document(db_session, theirs, tmp_path, "foreign-design")
    user = _user(db_session, mine)
    login_as(client, user)
    for url in (
        _download_url(foreign_application, foreign_document),
        # the foreign document under the caller's own application, and the caller's under the foreign one
        _download_url(own_application, foreign_document),
        _download_url(foreign_application, own_document),
        _download_url(own_application, type("Missing", (), {"id": 2**40})),
    ):
        response = client.get(url)
        assert response.status_code == 404, url
        assert b"foreign-design" not in response.data
        assert "attachment" not in response.headers.get("Content-Disposition", "")


def test_the_product_page_document_link_points_at_a_download_that_works(
        app, db_session, make_org, client, login_as, tmp_path):
    mine = make_org("doc-a")
    one = _estate(db_session, mine, "d")
    from app.models.application_portfolio import ApplicationComponent
    from app.models.miscellaneous import ArchitectureDocument

    application = ApplicationComponent(organization_id=mine.id, name="Order Desk d", archimate_element_id=one["app"].id)
    db_session.add(application)
    db_session.flush()
    path = tmp_path / "desk.pdf"
    path.write_bytes(b"%PDF-1.4 desk")
    document = ArchitectureDocument(application_id=application.id, filename="desk.pdf", file_path=str(path),
                                    mime_type="application/pdf")
    db_session.add(document)
    db_session.flush()
    user = _user(db_session, mine)
    html = _get(client, login_as, user, _product_url(one["product"])).get_data(as_text=True)
    href = _download_url(application, document)
    assert href in html
    assert client.get(href).data == b"%PDF-1.4 desk"


def _costed_product(db_session, org, label):
    from app.models.application_portfolio import ApplicationComponent

    one = _estate(db_session, org, label)
    db_session.add(ApplicationComponent(organization_id=org.id, name=f"Order Desk {label}",
                                        archimate_element_id=one["app"].id, total_cost_of_ownership=12000))
    db_session.flush()
    return one


def test_an_architect_the_cost_rule_withholds_from_reads_no_product_cost_on_the_page_or_in_the_export(
        app, db_session, make_org, client, login_as):
    mine, theirs = make_org("cost-a"), make_org("cost-b")
    one = _costed_product(db_session, mine, "w")
    other = _costed_product(db_session, theirs, "x")
    architect = _user(db_session, mine)
    html = _get(client, login_as, architect, _product_url(one["product"])).get_data(as_text=True)
    assert html.count("Cost figures are withheld for your role.") == 1
    assert "12,000" not in html and "product-cost-line" not in html
    export = _get(client, login_as, architect, f"/architecture/products/{one['product'].id}/export").get_data(as_text=True)
    assert "12000" not in export and "12,000" not in export
    assert "Cost figures are withheld for your role." in export
    foreign = _get(client, login_as, _user(db_session, theirs), _product_url(one["product"]))
    assert foreign.status_code == 404
    assert other["product"].id != one["product"].id


def test_the_chief_technology_officer_reads_the_product_cost_the_architect_is_denied(
        app, db_session, make_org, client, login_as):
    org = make_org("cost-c")
    one = _costed_product(db_session, org, "y")
    cto = _user(db_session, org, "cto")
    html = _get(client, login_as, cto, _product_url(one["product"])).get_data(as_text=True)
    assert "12,000" in html and "Cost figures are withheld for your role." not in html
