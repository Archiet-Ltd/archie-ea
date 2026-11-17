"""The logical data model of a domain: entities, their attributes, and the
relationships between them. One entity store, two organisations kept apart."""

import uuid

import pytest

from app.models.archimate_core import ArchiMateElement
from app.models.process_data import (
    DataDomain,
    DataEntity,
    DataEntityAttribute,
    DataEntityRelationship,
)
from app.models.user import Role, User
from app.modules.architecture.services import data_erd_service as erd

pytestmark = pytest.mark.usefixtures("db_session")


def _domain(db_session, org, name="Customer"):
    domain = DataDomain(name=f"{name} {uuid.uuid4().hex[:8]}", organization_id=org.id)
    db_session.add(domain)
    db_session.flush()
    return domain


def _user(db_session, org, role="data_architect"):
    Role.insert_roles()
    user = User(
        email=f"erd.{uuid.uuid4().hex[:8]}@example.com",
        first_name="Dana",
        last_name="Architect",
        organization_id=org.id,
        enterprise_role=role,
        confirmed=True,
    )
    user.role = Role.query.filter_by(name="Architect").one()
    user.password = "Correct-Horse-9!"
    db_session.add(user)
    db_session.flush()
    return user


def test_new_entity_keeps_its_attributes_and_is_the_archimate_data_object(db_session, make_org, tenant_ctx):
    org = make_org("erd")
    with tenant_ctx(org.id):
        domain = _domain(db_session, org)
        entity = erd.create_entity(
            domain,
            "Invoice",
            [
                {"name": "invoice_id", "data_type": "uuid", "is_primary_key": True},
                {"name": "customer_id", "data_type": "uuid", "is_foreign_key": True},
                {"name": "note", "data_type": "text", "is_nullable": True},
            ],
        )
        assert [a.name for a in entity.attributes] == ["invoice_id", "customer_id", "note"]
        assert entity.attributes[0].is_primary_key and not entity.attributes[0].is_nullable
        assert entity.attributes[1].is_foreign_key
        element = db_session.get(ArchiMateElement, entity.archimate_element_id)
        assert element.type == "DataObject" and element.name == "Invoice"
        # Renaming the entity renames the same ArchiMate element: one record.
        erd.update_entity(entity, "Sales invoice", [{"name": "invoice_id", "id": entity.attributes[0].id}])
        db_session.refresh(element)
        assert element.name == "Sales invoice"
        assert [a.name for a in entity.attributes] == ["invoice_id"]


def test_relationship_one_to_many_sets_both_ends(db_session, make_org, tenant_ctx):
    org = make_org("erd")
    with tenant_ctx(org.id):
        domain = _domain(db_session, org)
        customer = erd.create_entity(domain, "Customer", [{"name": "id"}])
        invoice = erd.create_entity(domain, "Invoice", [{"name": "id"}])
        rel = erd.relate(customer, invoice, preset="one-to-many", label="receives")
        assert (rel.source_cardinality, rel.target_cardinality) == ("exactly_one", "zero_or_many")
        payload = erd.domain_diagram(domain)
        assert [e["name"] for e in payload["entities"]] == ["Customer", "Invoice"]
        assert len(payload["relationships"]) == 1
        with pytest.raises(erd.ErdError):
            erd.relate(customer, invoice, preset="sideways")


def test_duplicate_names_are_refused(db_session, make_org, tenant_ctx):
    org = make_org("erd")
    with tenant_ctx(org.id):
        domain = _domain(db_session, org)
        erd.create_entity(domain, "Order", [])
        with pytest.raises(erd.ErdError):
            erd.create_entity(domain, "order", [])
        with pytest.raises(erd.ErdError):
            erd.create_entity(domain, "Line", [{"name": "a"}, {"name": "A"}])


def test_two_organisations_cannot_see_or_change_each_others_models(db_session, make_org, client, login_as):
    org_a, org_b = make_org("erd-a"), make_org("erd-b")
    domain_a = _domain(db_session, org_a)
    a1 = DataEntity(name="Alpha", domain_id=domain_a.id, organization_id=org_a.id)
    a2 = DataEntity(name="Beta", domain_id=domain_a.id, organization_id=org_a.id)
    db_session.add_all([a1, a2])
    db_session.flush()
    rel = DataEntityRelationship(
        source_entity_id=a1.id, target_entity_id=a2.id, organization_id=org_a.id
    )
    db_session.add(rel)
    user_a, user_b = _user(db_session, org_a), _user(db_session, org_b)
    domain_b = _domain(db_session, org_b)
    own_b = DataEntity(name="Gamma", domain_id=domain_b.id, organization_id=org_b.id)
    db_session.add(own_b)
    db_session.flush()
    ids = (domain_a.id, a1.id, a2.id, rel.id, own_b.id, domain_b.id)
    domain_a_id, a1_id, a2_id, rel_id, b_entity_id, domain_b_id = ids
    db_session.commit()

    login_as(client, user_a)
    body = client.get(f"/architecture/api/data-erd/{domain_a_id}").get_json()
    assert [e["name"] for e in body["entities"]] == ["Alpha", "Beta"]
    assert len(body["relationships"]) == 1

    login_as(client, user_b)
    assert client.get(f"/architecture/api/data-erd/{domain_a_id}").status_code == 404
    assert client.post(
        f"/architecture/api/data-erd/{domain_a_id}/entities", json={"name": "Intruder"}
    ).status_code == 404
    assert client.put(f"/architecture/api/data-erd/entities/{a1_id}", json={"name": "Hacked"}).status_code == 404
    assert client.delete(f"/architecture/api/data-erd/entities/{a1_id}").status_code == 404
    assert client.put(f"/architecture/api/data-erd/relationships/{rel_id}", json={"preset": "one-to-one"}).status_code == 404
    assert client.delete(f"/architecture/api/data-erd/relationships/{rel_id}").status_code == 404
    # Relating one's own entity to a foreign one is refused too.
    assert client.post(
        "/architecture/api/data-erd/relationships",
        json={"source_id": b_entity_id, "target_id": a1_id, "preset": "one-to-many"},
    ).status_code == 404

    login_as(client, user_a)
    after = client.get(f"/architecture/api/data-erd/{domain_a_id}").get_json()
    assert [e["name"] for e in after["entities"]] == ["Alpha", "Beta"]
    assert len(after["relationships"]) == 1
    assert DataEntityAttribute.query.filter_by(organization_id=org_b.id).count() == 0
