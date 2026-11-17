"""A listed record is opened, edited and deleted by its own record id, never by its architecture element's id
(the two number spaces overlap); every type has one; what a person types on the Principle and Business Function
forms is kept and shown; one rating rule; a name too long is refused by its field and limit."""

from __future__ import annotations

import json
import re
import uuid

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.usefixtures("db_session")


def _editor(db_session, make_org, client, login_as, label):
    from tests.test_work_package_writer import _user

    org = make_org(label)
    login_as(client, _user(db_session, org, label.replace("-", ""), role="enterprise_architect"))
    return org


def _text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def _create(client, layer, element_type, **fields):
    created = client.post("/architecture/%s/%s/new" % (layer, element_type), json=fields)
    assert created.status_code == 200, created.get_json()
    return created.get_json()["id"]


def _two_nodes_with_overlapping_ids(db_session, client):
    """Node 'First' has a record id apart from its element id e; node 'Second' is forced onto record id e."""
    from app.models.technology_layer import Node

    first_id = _create(client, "technology", "Node", name="First node")
    second_id = _create(client, "technology", "Node", name="Second node")
    element_id = Node.query.filter_by(id=first_id).one().archimate_element_id
    table = Node.__tablename__
    apart = 800000000 + uuid.uuid4().int % 100000000
    db_session.execute(text("UPDATE %s SET id = :new WHERE id = :old" % table), {"new": apart, "old": first_id})
    taken = db_session.execute(text("SELECT count(*) FROM %s WHERE id = :id" % table), {"id": element_id}).scalar()
    if taken and element_id != second_id:
        raise AssertionError("record id %d is held by another node" % element_id)
    db_session.execute(text("UPDATE %s SET id = :new WHERE id = :old" % table), {"new": element_id, "old": second_id})
    db_session.expire_all()
    return apart, element_id


def _listed(client, layer, element_type):
    return client.get("/architecture/api/layer/%s/elements?element_type=%s&per_page=100" % (layer, element_type)).get_json()["elements"]


def test_delete_of_a_listed_node_removes_only_that_node_when_ids_overlap(db_session, make_org, client, login_as):
    from app.models.technology_layer import Node

    _editor(db_session, make_org, client, login_as, "delete-overlap")
    first_id, second_id = _two_nodes_with_overlapping_ids(db_session, client)
    row = {e["name"]: e for e in _listed(client, "technology", "Node")}["First node"]
    assert row["record_id"] == first_id and row["id"] == second_id  # the element id is the other node's record id

    deleted = client.post("/architecture/technology/Node/%d/delete" % row["record_id"], json={})
    assert deleted.status_code == 200 and deleted.get_json()["success"] is True
    db_session.expire_all()
    assert Node.query.filter_by(id=first_id).first() is None
    assert Node.query.filter_by(id=second_id).one().name == "Second node"


def test_bulk_delete_of_a_listed_node_removes_only_that_node_when_ids_overlap(db_session, make_org, client, login_as):
    from app.models.technology_layer import Node

    _editor(db_session, make_org, client, login_as, "bulk-overlap")
    first_id, second_id = _two_nodes_with_overlapping_ids(db_session, client)
    assert client.post("/architecture/technology/Node/bulk-delete", json={"ids": [first_id]}).get_json()["success"] is True
    db_session.expire_all()
    assert Node.query.filter_by(id=first_id).first() is None
    assert Node.query.filter_by(id=second_id).one().name == "Second node"


def test_every_listed_record_carries_its_record_id_including_types_with_no_form_configuration(
        db_session, make_org, client, login_as):
    from app.models.application_layer import ApplicationFunction
    from app.modules.architecture.routes.archimate_crud.routes import get_element_config

    _editor(db_session, make_org, client, login_as, "record-id-every-type")
    assert get_element_config("ApplicationFunction") is None
    record_id = _create(client, "application", "ApplicationFunction", name="Order entry")
    entry = [e for e in _listed(client, "application", "ApplicationFunction") if e["name"] == "Order entry"]
    assert len(entry) == 1 and entry[0]["record_id"] == record_id
    assert client.post("/architecture/application/ApplicationFunction/%d/edit" % entry[0]["record_id"],
                       json={"name": "Order capture"}).status_code == 200
    db_session.expire_all()
    assert ApplicationFunction.query.filter_by(id=record_id).one().name == "Order capture"
    page = _text(client.get("/architecture/application/ApplicationFunction/%d" % record_id).get_data(as_text=True))
    assert "Order capture" in page and "Order entry" not in page


def test_an_element_held_with_no_record_is_deleted_as_an_element_even_when_a_record_has_its_id(
        db_session, make_org, client, login_as):
    from app.models.archimate_core import ArchiMateElement
    from app.models.technology_layer import Node

    org = _editor(db_session, make_org, client, login_as, "element-only-delete")
    bystander = _create(client, "technology", "Node", name="Bystander")
    element = ArchiMateElement(organization_id=org.id, name="Element only", type="Node", layer="Technology")
    db_session.add(element)
    db_session.flush()
    assert Node.query.filter_by(id=element.id).first() is None or element.id == bystander
    db_session.execute(text("UPDATE %s SET id = :new WHERE id = :old" % Node.__tablename__), {"new": element.id, "old": bystander})
    db_session.expire_all()
    listed = {e["name"]: e for e in _listed(client, "technology", "Node")}
    assert "Element only" in listed and "record_id" not in listed["Element only"]
    assert client.post("/architecture/technology/Node/%d/delete?ae=1" % element.id, json={}).status_code == 200
    db_session.expire_all()
    assert ArchiMateElement.query.filter_by(id=element.id).first() is None
    assert Node.query.filter_by(id=element.id).one().name == "Bystander"


def test_a_principle_keeps_its_description_and_the_record_page_shows_it(db_session, make_org, client, login_as):
    from app.models.models import Principle

    _editor(db_session, make_org, client, login_as, "principle-description")
    record_id = _create(client, "motivation", "Principle", name="Reuse first", statement="Reuse before buying",
                        description="Applies to every new build")
    assert Principle.query.filter_by(id=record_id).one().description == "Applies to every new build"
    page = _text(client.get("/architecture/motivation/Principle/%d" % record_id).get_data(as_text=True))
    assert "Applies to every new build" in page and "No description yet" not in page
    assert client.post("/architecture/motivation/Principle/%d/edit" % record_id,
                       json={"name": "Reuse first", "statement": "Reuse before buying", "description": "Changed"}).status_code == 200
    db_session.expire_all()
    assert Principle.query.filter_by(id=record_id).one().description == "Changed"


def test_a_business_function_page_names_its_capability_with_a_link_and_is_not_discovered_by_the_ai(
        db_session, make_org, client, login_as):
    from app.models.business_capabilities import BusinessCapability, BusinessFunction
    from app.models.unified_capability import UnifiedCapability

    org = _editor(db_session, make_org, client, login_as, "function-capability-fact")
    name = "Fulfil orders %s" % uuid.uuid4().hex[:6]
    business = BusinessCapability(organization_id=org.id, name=name, level=1)
    db_session.add(business)
    db_session.flush()
    # Saving a business capability projects it into the one capability store, recording where it came from.
    unified = UnifiedCapability.query.filter_by(source_table="business_capability", source_id=str(business.id),
                                                organization_id=org.id).one()
    record_id = _create(client, "business", "BusinessFunction", name="Pick and pack", capability_id=str(unified.id))
    assert BusinessFunction.query.filter_by(id=record_id).one().discovered_by_ai is False
    page = client.get("/architecture/business/BusinessFunction/%d" % record_id).get_data(as_text=True)
    assert 'href="/capabilities/capabilities/%d/fact-sheet"' % business.id in page or (
        "/fact-sheet" in page and name in page)
    link = re.search(r'<a href="([^"]+fact-sheet)"[^>]*>\s*%s\s*</a>' % re.escape(name), page)
    assert link, "the capability is not a link to its fact sheet"
    landed = client.get(link.group(1))
    assert landed.status_code == 200 and name in _text(landed.get_data(as_text=True))
    assert "Discovered by" not in _text(page) or re.search(r"Discovered by ai\s+No", _text(page))


def test_a_name_too_long_is_refused_by_its_field_and_limit(db_session, make_org, client, login_as):
    _editor(db_session, make_org, client, login_as, "name-too-long")
    refused = client.post("/architecture/technology/Node/new", json={"name": "n" * 400})
    assert refused.status_code == 400
    assert re.fullmatch(r"Name is at most \d+ characters", refused.get_json()["error"])
    record_id = _create(client, "technology", "Node", name="Short")
    edited = client.post("/architecture/technology/Node/%d/edit" % record_id, json={"name": "n" * 400})
    assert edited.status_code == 400 and edited.get_json()["error"].startswith("Name is at most")


def test_one_rating_rule_serves_the_edit_page_and_the_detector_update(db_session, make_org, client, login_as):
    from app.models.implementation_migration import Gap
    from app.modules.architecture.services.gap_archimate_service import GapArchiMateService

    org = _editor(db_session, make_org, client, login_as, "one-rating-rule")
    gap = Gap(organization_id=org.id, name="Rated gap", gap_type="coverage", priority="low", severity="low")
    db_session.add(gap)
    db_session.flush()
    service = GapArchiMateService()
    service._update_gap_from_data(gap, {"name": "Rated gap", "priority": "high"})
    assert (gap.priority, gap.severity) == ("high", "high")
    # The same priority again leaves the severity a person set on its own alone.
    gap.severity = "critical"
    service._update_gap_from_data(gap, {"name": "Rated gap", "priority": "high"})
    assert (gap.priority, gap.severity) == ("high", "critical")
    # The Edit page: a new priority moves both; this must not depend on a second mapping in the route.
    import inspect
    from app.modules.architecture.routes.archimate_crud import routes
    assert "PRIORITY_SEVERITY_MAP[" not in inspect.getsource(routes._save_gap_edit)
    assert "PRIORITY_SEVERITY_MAP" not in inspect.getsource(GapArchiMateService._update_gap_from_data)


def test_a_value_stream_belongs_to_the_strategy_layer_in_the_field_configuration_and_the_layer_list():
    from app.modules.architecture.routes.archimate_crud.routes import LAYER_CONFIG
    from app.modules.architecture.routes.archimate_crud.services.field_configs import get_element_config

    layers = [layer for layer, config in LAYER_CONFIG.items() if "ValueStream" in config["elements"]]
    assert layers == ["strategy"] and get_element_config("ValueStream").layer == "strategy"


def test_an_edit_page_opened_on_a_record_saves_by_the_record_id_and_one_on_an_element_does_not(
        db_session, make_org, client, login_as):
    from app.models.archimate_core import ArchiMateElement

    org = _editor(db_session, make_org, client, login_as, "edit-payload-record-id")
    record_id = _create(client, "technology", "Node", name="A record")
    html = client.get("/architecture/technology/Node/%d/edit" % record_id).get_data(as_text=True)
    opened = json.loads(re.search(r"__APP_CONFIG__\.editingElement\s*=\s*(\{.*?\});", html, re.S).group(1))
    assert opened["record_id"] == record_id
    element = ArchiMateElement(organization_id=org.id, name="Only an element", type="Node", layer="Technology")
    db_session.add(element)
    db_session.flush()
    html = client.get("/architecture/technology/Node/%d/edit?ae=1" % element.id).get_data(as_text=True)
    opened = json.loads(re.search(r"__APP_CONFIG__\.editingElement\s*=\s*(\{.*?\});", html, re.S).group(1))
    assert "record_id" not in opened and opened["name"] == "Only an element"


def _element_with_no_record_beside_a_record_of_its_number(db_session, org, client):
    """An element held with no record, and a Node whose record id is that element's id."""
    from app.models.archimate_core import ArchiMateElement
    from app.models.technology_layer import Node

    element = ArchiMateElement(organization_id=org.id, name="Lonely element", type="Node", layer="Technology")
    db_session.add(element)
    db_session.flush()
    holder_id = _create(client, "technology", "Node", name="Holder node")
    table = Node.__tablename__
    taken = db_session.execute(text("SELECT count(*) FROM %s WHERE id = :id" % table), {"id": element.id}).scalar()
    if taken:
        raise AssertionError("record id %d is already held" % element.id)
    db_session.execute(text("UPDATE %s SET id = :new WHERE id = :old" % table), {"new": element.id, "old": holder_id})
    db_session.expire_all()
    return element.id


def test_the_page_of_an_element_with_no_record_edits_and_deletes_that_element_not_the_record_of_its_number(
        db_session, make_org, client, login_as):
    from app.models.archimate_core import ArchiMateElement
    from app.models.technology_layer import Node

    org = _editor(db_session, make_org, client, login_as, "page-no-record")
    number = _element_with_no_record_beside_a_record_of_its_number(db_session, org, client)

    page = client.get("/architecture/technology/Node/%d?ae=1" % number).get_data(as_text=True)
    assert "Lonely element" in _text(page)
    # Edit and Delete on this page say the id is an element's.
    assert "/architecture/technology/Node/%d/edit?ae=1" % number in page
    assert "/architecture/technology/Node/%d/delete?ae=1" % number in page

    # The form opens on the element, and saving lands back on the element's page, not the record's.
    opened = client.get("/architecture/technology/Node/%d/edit?ae=1" % number).get_data(as_text=True)
    assert "/architecture/technology/Node/%d?ae=1" % number in opened
    saved = client.post("/architecture/technology/Node/%d/edit?ae=1" % number, data={"name": "Lonely renamed"})
    assert saved.status_code == 302 and saved.headers["Location"].endswith("/Node/%d?ae=1" % number)
    db_session.expire_all()
    assert Node.query.filter_by(id=number).one().name == "Holder node"
    assert db_session.get(ArchiMateElement, number).name == "Lonely renamed"

    deleted = client.post("/architecture/technology/Node/%d/delete?ae=1" % number, json={})
    assert deleted.status_code == 200 and deleted.get_json()["success"] is True
    db_session.expire_all()
    assert db_session.get(ArchiMateElement, number) is None
    assert Node.query.filter_by(id=number).one().name == "Holder node"


def test_the_page_of_a_record_keeps_its_own_addresses(db_session, make_org, client, login_as):
    _editor(db_session, make_org, client, login_as, "page-record")
    record_id = _create(client, "technology", "Node", name="A record")
    page = client.get("/architecture/technology/Node/%d" % record_id).get_data(as_text=True)
    assert "/architecture/technology/Node/%d/edit" % record_id in page and "?ae=1" not in page


def test_both_gap_creates_that_read_the_map_directly_use_the_rating_rule():
    import inspect
    from app.modules.capabilities.routes import roadmap_routes
    from app.modules.solutions_strategic.v2.routes import roadmap_api

    for module in (roadmap_routes, roadmap_api):
        source = inspect.getsource(module)
        assert "PRIORITY_SEVERITY_MAP" not in source and "apply_rating_rule" in source
