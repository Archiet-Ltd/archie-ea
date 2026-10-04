"""A data entity's CRUD matrix is read from, and recorded into, the ArchiMate
access relationships between application and data entity elements."""

import uuid

import pytest

from app.modules.architecture.services.data_architecture_service import (
    access_mode_for,
    entity_access_matrix,
    normalise_crud,
    record_entity_access,
)


@pytest.mark.parametrize("ops,crud,mode", [
    (["U", "C"], "CU", "write"),
    (["R"], "R", "read"),
    (["d", "r", "R"], "RD", "readwrite"),
    ([], "", None),
])
def test_crud_letters_and_access_mode(ops, crud, mode):
    assert normalise_crud(ops) == crud
    assert access_mode_for(crud) == mode


def _entity_and_apps(db_session, org_id):
    from app.models.application_portfolio import ApplicationComponent
    from app.models.process_data import DataDomain, DataEntity

    tag = uuid.uuid4().hex[:6]
    domain = DataDomain(name="Domain %s" % tag, organization_id=org_id)
    db_session.add(domain)
    db_session.flush()
    entity = DataEntity(name="Customer %s" % tag, domain_id=domain.id, organization_id=org_id)
    first = ApplicationComponent(name="CRM %s" % tag, organization_id=org_id)
    second = ApplicationComponent(name="Billing %s" % tag, organization_id=org_id)
    db_session.add_all([entity, first, second])
    db_session.flush()
    return entity, first, second


def test_recorded_access_reads_back_as_the_matrix(db_session, make_org, tenant_ctx):
    from app.models.archimate_core import ArchiMateRelationship

    org = make_org("crud-matrix")
    with tenant_ctx(org.id):
        entity, crm, billing = _entity_and_apps(db_session, org.id)
        assert entity_access_matrix(entity) == []

        rel = record_entity_access(entity, crm, ["C", "U"])
        record_entity_access(entity, billing, ["R"])
        # Recording again replaces, never duplicates.
        record_entity_access(entity, crm, ["C", "U"])
        rows = {row["name"]: row["cells"] for row in entity_access_matrix(entity)}

        stored = db_session.get(ArchiMateRelationship, rel.id)
        assert (stored.type, stored.access_mode, stored.crud_operations) == ("access", "write", "CU")
        assert stored.source_id == crm.archimate_element_id
        assert stored.target_id == entity.archimate_element_id

    assert rows == {
        crm.name: {"C": True, "R": False, "U": True, "D": False},
        billing.name: {"C": False, "R": True, "U": False, "D": False},
    }


def test_access_drawn_without_crud_detail_is_not_shown_as_no(db_session, make_org, tenant_ctx):
    from app.models.archimate_core import ArchiMateRelationship

    org = make_org("crud-matrix-drawn")
    with tenant_ctx(org.id):
        entity, crm, _billing = _entity_and_apps(db_session, org.id)
        db_session.add(ArchiMateRelationship(
            source_id=crm.archimate_element_id, target_id=entity.archimate_element_id,
            type="Access", access_mode="readwrite", organization_id=org.id,
        ))
        db_session.flush()
        rows = entity_access_matrix(entity)

    assert rows[0]["cells"] == {"R": True, "C": None, "U": None, "D": None}


def _user(db_session, org_id):
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(email="crud-%s@example.com" % uuid.uuid4().hex[:10], first_name="D",
                last_name="A", organization_id=org_id, confirmed=True,
                enterprise_role="data_architect")
    db_session.add(user)
    user.role = Role.query.filter_by(name="Architect").first()
    db_session.flush()
    return user


def test_every_organisation_can_create_an_entity_without_choosing_a_domain(
    app, db_session, make_org, client, login_as
):
    """The default domain's name is unique across organisations; a second
    organisation's first entity must not fail on it."""
    from app.models.process_data import DataDomain, DataEntity

    for label in ("crud-domain-a", "crud-domain-b"):
        org = make_org(label)
        login_as(client, _user(db_session, org.id))
        name = "Order %s" % uuid.uuid4().hex[:6]
        resp = client.post("/architecture/data-entities/create", data={"name": name})
        assert resp.status_code == 302, resp.get_data(as_text=True)[:300]
        entity = db_session.query(DataEntity).filter_by(name=name).one()
        domain = db_session.get(DataDomain, entity.domain_id)
        assert entity.organization_id == org.id and domain.organization_id == org.id


def test_no_operation_chosen_is_refused(db_session, make_org, tenant_ctx):
    org = make_org("crud-matrix-empty")
    with tenant_ctx(org.id):
        entity, crm, _billing = _entity_and_apps(db_session, org.id)
        with pytest.raises(ValueError):
            record_entity_access(entity, crm, [])
