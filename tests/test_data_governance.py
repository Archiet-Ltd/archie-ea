"""System of record, undeclared copies, master data domains and the standards
check, each proven with two real organisations."""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _uid():
    return uuid.uuid4().hex[:8]


def _app(db_session, org_id, name):
    from app.models.application_portfolio import ApplicationComponent

    row = ApplicationComponent(name=name, organization_id=org_id)
    db_session.add(row)
    db_session.flush()
    return row


def _domain(db_session, org_id, domain_type="master"):
    from app.models.process_data import DataDomain

    row = DataDomain(name="Domain " + _uid(), domain_type=domain_type, organization_id=org_id)
    db_session.add(row)
    db_session.flush()
    return row


def _entity(db_session, org_id, name, domain=None, **kw):
    from app.models.process_data import DataEntity

    domain = domain or _domain(db_session, org_id)
    row = DataEntity(name=name, domain_id=domain.id, organization_id=org_id, **kw)
    db_session.add(row)
    db_session.flush()
    return row


def _holds(db_session, org_id, application, object_name):
    from app.models.application_layer import DataObject

    row = DataObject(
        name=object_name,
        description=object_name,
        application_component_id=application.id,
        organization_id=org_id,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _user(db_session, org_id, role):
    from app.models.user import User

    user = User(
        email=f"dg.{role}.{_uid()}@example.com", first_name="D", last_name="G",
        organization_id=org_id, enterprise_role=role, confirmed=True,
    )
    user.password = "Passw0rd!x"
    db_session.add(user)
    db_session.flush()
    return user


# ------------------------------------------------------------- declaration


def test_declare_persists_column_label_and_serving_link(db_session, make_org, tenant_ctx):
    from app.models.models import ArchiMateRelationship
    from app.modules.architecture.services import data_sor_service as sor

    org = make_org("a")
    with tenant_ctx(org.id):
        application = _app(db_session, org.id, "Billing Core")
        entity = _entity(db_session, org.id, "Customer")
        sor.declare_system_of_record(org.id, entity.id, application.id)
        db_session.refresh(entity)
        assert entity.system_of_record_application_id == application.id
        assert entity.system_of_record is None
        link = ArchiMateRelationship.query.filter_by(
            type="serving",
            source_id=application.archimate_element_id,
            target_id=entity.archimate_element_id,
        ).all()
        assert len(link) == 1


def test_redeclare_moves_the_serving_link(db_session, make_org, tenant_ctx):
    from app.models.models import ArchiMateRelationship
    from app.modules.architecture.services import data_sor_service as sor

    org = make_org("a")
    with tenant_ctx(org.id):
        first, second = _app(db_session, org.id, "First"), _app(db_session, org.id, "Second")
        entity = _entity(db_session, org.id, "Invoice")
        sor.declare_system_of_record(org.id, entity.id, first.id)
        sor.declare_system_of_record(org.id, entity.id, second.id)
        rows = ArchiMateRelationship.query.filter_by(
            type="serving", target_id=entity.archimate_element_id
        ).all()
        assert [r.source_id for r in rows] == [second.archimate_element_id]


def test_cannot_declare_another_organisations_application_or_entity(db_session, make_org, tenant_ctx):
    from app.modules.architecture.services import data_sor_service as sor

    org_a, org_b = make_org("a"), make_org("b")
    with tenant_ctx(org_a.id):
        entity_a = _entity(db_session, org_a.id, "Customer")
        app_a = _app(db_session, org_a.id, "A app")
    with tenant_ctx(org_b.id):
        app_b = _app(db_session, org_b.id, "B app")
    with tenant_ctx(org_a.id):
        with pytest.raises(sor.DataSorError):
            sor.declare_system_of_record(org_a.id, entity_a.id, app_b.id)
        db_session.expunge_all()
    with tenant_ctx(org_b.id):
        with pytest.raises(sor.DataSorError):
            sor.declare_system_of_record(org_b.id, entity_a.id, app_b.id)
    with tenant_ctx(org_a.id):
        db_session.expunge_all()
        assert sor.get_entity(org_a.id, entity_a.id).system_of_record_application_id is None
        assert app_a.id  # org A's own application is unaffected


# ------------------------------------------------------ copies and holders


def test_holders_flag_copies_once_declared(db_session, make_org, tenant_ctx):
    from app.modules.architecture.services import data_sor_service as sor

    org = make_org("a")
    with tenant_ctx(org.id):
        crm, erp = _app(db_session, org.id, "CRM"), _app(db_session, org.id, "ERP")
        entity = _entity(db_session, org.id, "Customer", technical_name="cust_master")
        _holds(db_session, org.id, crm, "Customer")
        _holds(db_session, org.id, erp, "customer")
        roles = {h["application_name"]: h["role"] for h in sor.entity_holders(org.id, entity)}
        assert roles == {"CRM": "undeclared", "ERP": "undeclared"}
        sor.declare_system_of_record(org.id, entity.id, crm.id)
        db_session.refresh(entity)
        roles = {h["application_name"]: h["role"] for h in sor.entity_holders(org.id, entity)}
        assert roles == {"CRM": "system_of_record", "ERP": "copy"}


def test_undeclared_copies_are_per_organisation_and_ranked(db_session, make_org, tenant_ctx):
    from app.models.business_capabilities import BusinessCapability
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.modules.architecture.services import data_sor_service as sor

    org_a, org_b = make_org("a"), make_org("b")
    with tenant_ctx(org_a.id):
        a1, a2 = _app(db_session, org_a.id, "A1"), _app(db_session, org_a.id, "A2")
        quiet = _entity(db_session, org_a.id, "Quiet Entity")
        busy = _entity(db_session, org_a.id, "Busy Entity")
        single = _entity(db_session, org_a.id, "Single Holder")
        for entity in (quiet, busy):
            _holds(db_session, org_a.id, a1, entity.name)
            _holds(db_session, org_a.id, a2, entity.name)
        _holds(db_session, org_a.id, a1, single.name)
        capability = BusinessCapability(name="Busy Capability " + _uid(), organization_id=org_a.id)
        db_session.add(capability)
        db_session.flush()
        busy.owning_capability_id = capability.id
        db_session.flush()

        db_session.add_all(
            [
                ApplicationCapabilityMapping(
                    organization_id=org_a.id,
                    application_component_id=a1.id,
                    business_capability_id=capability.id,
                ),
                ApplicationCapabilityMapping(
                    organization_id=org_a.id,
                    application_component_id=a2.id,
                    business_capability_id=capability.id,
                ),
            ]
        )
    with tenant_ctx(org_b.id):
        b1, b2 = _app(db_session, org_b.id, "B1"), _app(db_session, org_b.id, "B2")
        other = _entity(db_session, org_b.id, "Org B Entity")
        _holds(db_session, org_b.id, b1, other.name)
        _holds(db_session, org_b.id, b2, other.name)

    with tenant_ctx(org_a.id):
        rows = sor.undeclared_copies(org_a.id)
        assert [r["entity"].name for r in rows] == ["Busy Entity", "Quiet Entity"]
        assert [r["consumer_count"] for r in rows] == [2, 2]
    with tenant_ctx(org_b.id):
        assert [r["entity"].name for r in sor.undeclared_copies(org_b.id)] == ["Org B Entity"]


def test_similarity_matching_flags_near_match_copies_per_organisation(db_session, make_org, tenant_ctx):
    from app.modules.architecture.services import data_sor_service as sor

    org_a, org_b = make_org("sim-a"), make_org("sim-b")
    with tenant_ctx(org_a.id):
        crm = _app(db_session, org_a.id, "CRM")
        erp = _app(db_session, org_a.id, "ERP")
        support = _app(db_session, org_a.id, "Support")
        _entity(
            db_session,
            org_a.id,
            "Customer",
            description="Customer master record",
        )
        _holds(db_session, org_a.id, crm, "Customer Master")
        _holds(db_session, org_a.id, erp, "Customer Master")
        _holds(db_session, org_a.id, support, "Customer Support Ticket")
    with tenant_ctx(org_b.id):
        other_a = _app(db_session, org_b.id, "Other A")
        other_b = _app(db_session, org_b.id, "Other B")
        _entity(db_session, org_b.id, "Supplier", description="Supplier reference")
        _holds(db_session, org_b.id, other_a, "Supplier Master")
        _holds(db_session, org_b.id, other_b, "Supplier Master")

    with tenant_ctx(org_a.id):
        rows = sor.undeclared_copies(org_a.id)
        assert [row["entity"].name for row in rows] == ["Customer"]
        assert rows[0]["applications"] == ["CRM", "ERP"]
def test_master_domains_show_consumer_applications_not_process_names(db_session, make_org, tenant_ctx):
    from app.models.business_capabilities import BusinessCapability
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.modules.architecture.services import data_sor_service as sor

    org_a, org_b = make_org("dom-a"), make_org("dom-b")
    with tenant_ctx(org_a.id):
        crm = _app(db_session, org_a.id, "CRM")
        erp = _app(db_session, org_a.id, "ERP")
        domain = _domain(db_session, org_a.id)
        capability = BusinessCapability(name="Customer Capability " + _uid(), organization_id=org_a.id)
        db_session.add(capability)
        db_session.flush()
        entity = _entity(
            db_session,
            org_a.id,
            "Customer",
            domain=domain,
            owning_capability_id=capability.id,
        )
        _holds(db_session, org_a.id, crm, "Customer")
        _holds(db_session, org_a.id, erp, "Customer Master")
        sor.declare_system_of_record(org_a.id, entity.id, crm.id)
        db_session.add(
            ApplicationCapabilityMapping(
                organization_id=org_a.id,
                application_component_id=erp.id,
                business_capability_id=capability.id,
            )
        )
    with tenant_ctx(org_b.id):
        foreign_domain = _domain(db_session, org_b.id)
        foreign_entity = _entity(db_session, org_b.id, "Customer", domain=foreign_domain)
        foreign_app = _app(db_session, org_b.id, "Foreign ERP")
        _holds(db_session, org_b.id, foreign_app, "Customer")
        sor.declare_system_of_record(org_b.id, foreign_entity.id, foreign_app.id)

    with tenant_ctx(org_a.id):
        rows = sor.master_domains(org_a.id)
        assert [row["domain"].id for row in rows] == [domain.id]
        assert rows[0]["entities"][0]["consumers"] == ["ERP"]


def test_backfill_links_legacy_system_of_record_text_without_second_authority(
    app, client, db_session, make_org, login_as, tenant_ctx
):
    from app.models.process_data import DataEntity

    org_a, org_b = make_org("route-a"), make_org("route-b")
    architect = _user(db_session, org_a.id, "data_architect")
    foreign_architect = _user(db_session, org_b.id, "data_architect")
    local_domain = _domain(db_session, org_a.id)
    foreign_domain = _domain(db_session, org_b.id)
    local_app = _app(db_session, org_a.id, "ERP Canonical")
    foreign_app = _app(db_session, org_b.id, "ERP Canonical")

    with tenant_ctx(org_a.id):
        legacy = _entity(
            db_session,
            org_a.id,
            "Invoice",
            domain=local_domain,
            system_of_record="ERP Canonical",
        )
    with tenant_ctx(org_b.id):
        _entity(
            db_session,
            org_b.id,
            "Foreign Invoice",
            domain=foreign_domain,
            system_of_record="ERP Canonical",
        )

    login_as(client, architect)
    catalog = client.get("/data-governance/entities")
    assert catalog.status_code == 200
    db_session.expire_all()
    local_entity = db_session.get(DataEntity, legacy.id)
    assert local_entity.system_of_record_application_id == local_app.id
    assert local_entity.system_of_record == "ERP Canonical"
    assert b"ERP Canonical" in catalog.data

    page = client.get(f"/architecture/data-entities/{legacy.id}/edit")
    assert page.status_code == 200
    assert b"Legacy label" not in page.data

    resp = client.post(
        f"/architecture/data-entities/{legacy.id}/edit",
        data={
            "name": "Invoice",
            "domain_id": local_domain.id,
            "application_id": local_app.id,
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    db_session.expire_all()
    local_entity = db_session.get(DataEntity, legacy.id)
    assert local_entity.system_of_record_application_id == local_app.id
    assert local_entity.system_of_record == "ERP Canonical"

    login_as(client, foreign_architect)
    foreign_catalog = client.get("/data-governance/entities")
    assert foreign_catalog.status_code == 200
    db_session.expire_all()
    reloaded = db_session.get(DataEntity, legacy.id)
    assert reloaded.system_of_record_application_id == local_app.id
    assert reloaded.system_of_record_application_id != foreign_app.id


def test_create_data_entity_rejects_foreign_domain_and_application(
    app, client, db_session, make_org, login_as
):
    from app.models.process_data import DataEntity

    org_a, org_b = make_org("create-a"), make_org("create-b")
    architect = _user(db_session, org_a.id, "data_architect")
    local_domain = _domain(db_session, org_a.id)
    foreign_domain = _domain(db_session, org_b.id)
    local_app = _app(db_session, org_a.id, "Local Register")
    foreign_app = _app(db_session, org_b.id, "Foreign Register")

    login_as(client, architect)

    foreign_domain_response = client.post(
        "/architecture/data-entities/create",
        data={
            "name": "Cross Tenant Create",
            "domain_id": foreign_domain.id,
            "application_id": local_app.id,
        },
        follow_redirects=True,
    )
    assert foreign_domain_response.status_code == 200
    assert b"Pick a data domain from your organisation." in foreign_domain_response.data
    assert (
        DataEntity.query.filter(
            DataEntity.organization_id == org_a.id,
            DataEntity.name == "Cross Tenant Create",
        ).count()
        == 0
    )

    foreign_application_response = client.post(
        "/architecture/data-entities/create",
        data={
            "name": "Cross Tenant Application",
            "domain_id": local_domain.id,
            "application_id": foreign_app.id,
        },
        follow_redirects=True,
    )
    assert foreign_application_response.status_code == 200
    assert b"Pick an application from your portfolio." in foreign_application_response.data
    assert (
        DataEntity.query.filter(
            DataEntity.organization_id == org_a.id,
            DataEntity.name == "Cross Tenant Application",
        ).count()
        == 0
    )


def test_edit_data_entity_rejects_foreign_ids_and_foreign_entity_route(
    app, client, db_session, make_org, login_as
):
    org_a, org_b = make_org("edit-a"), make_org("edit-b")
    architect = _user(db_session, org_a.id, "data_architect")
    local_domain = _domain(db_session, org_a.id)
    replacement_domain = _domain(db_session, org_a.id)
    foreign_domain = _domain(db_session, org_b.id)
    local_app = _app(db_session, org_a.id, "Local Authoritative App")
    foreign_app = _app(db_session, org_b.id, "Foreign Authoritative App")
    entity = _entity(db_session, org_a.id, "Customer Ledger", domain=local_domain)
    foreign_entity = _entity(db_session, org_b.id, "Foreign Ledger", domain=foreign_domain)

    login_as(client, architect)

    assert client.get(f"/architecture/data-entities/{foreign_entity.id}/edit").status_code == 404

    foreign_domain_response = client.post(
        f"/architecture/data-entities/{entity.id}/edit",
        data={
            "name": entity.name,
            "domain_id": foreign_domain.id,
            "application_id": local_app.id,
        },
        follow_redirects=True,
    )
    assert foreign_domain_response.status_code == 200
    assert b"Pick a data domain from your organisation." in foreign_domain_response.data
    db_session.expire_all()
    entity = db_session.get(type(entity), entity.id)
    assert entity.domain_id == local_domain.id
    assert entity.system_of_record_application_id is None

    foreign_application_response = client.post(
        f"/architecture/data-entities/{entity.id}/edit",
        data={
            "name": entity.name,
            "domain_id": replacement_domain.id,
            "application_id": foreign_app.id,
        },
        follow_redirects=True,
    )
    assert foreign_application_response.status_code == 200
    assert b"Pick an application from your portfolio." in foreign_application_response.data
    db_session.expire_all()
    entity = db_session.get(type(entity), entity.id)
    assert entity.domain_id == local_domain.id
    assert entity.system_of_record_application_id is None


# --------------------------------------------------------- master domains


def test_master_domains_list_only_the_callers_master_domains(db_session, make_org, tenant_ctx):
    from app.modules.architecture.services import data_sor_service as sor

    org_a, org_b = make_org("a"), make_org("b")
    with tenant_ctx(org_a.id):
        golden = _app(db_session, org_a.id, "Golden")
        mine = _domain(db_session, org_a.id)
        _domain(db_session, org_a.id, domain_type="transactional")
        _entity(db_session, org_a.id, "Product", domain=mine)
        sor.declare_golden_source(org_a.id, mine.id, golden.id)
    with tenant_ctx(org_b.id):
        theirs = _domain(db_session, org_b.id)
    with tenant_ctx(org_a.id):
        rows = sor.master_domains(org_a.id)
        assert [r["domain"].id for r in rows] == [mine.id]
        assert rows[0]["golden_source_name"] == "Golden"
        assert [e["entity"].name for e in rows[0]["entities"]] == ["Product"]
        with pytest.raises(sor.DataSorError):
            sor.declare_golden_source(org_a.id, theirs.id, golden.id)


def test_golden_source_rejects_another_organisations_application(db_session, make_org, tenant_ctx):
    from app.modules.architecture.services import data_sor_service as sor

    org_a, org_b = make_org("a"), make_org("b")
    with tenant_ctx(org_b.id):
        foreign = _app(db_session, org_b.id, "Foreign")
    with tenant_ctx(org_a.id):
        domain = _domain(db_session, org_a.id)
        with pytest.raises(sor.DataSorError):
            sor.declare_golden_source(org_a.id, domain.id, foreign.id)


# ------------------------------------------------------- standards check


def test_standards_check_cites_the_standard_and_finds_reuse(db_session, make_org, tenant_ctx):
    from app.models.all_missing_models import ConceptualDataModel, LogicalDataModel
    from app.modules.architecture.services.data_model_validation_service import (
        DataModelValidationService,
    )

    org = make_org("a")
    with tenant_ctx(org.id):
        _entity(db_session, org.id, "Customer", entity_type="master",
                data_classification="internal", technical_name="customer")
        bad = _entity(db_session, org.id, "tbl_customer_order")
        conceptual = ConceptualDataModel(name="Concept " + _uid(), organization_id=org.id)
        conceptual.data_entities.append(bad)
        db_session.add(conceptual)
        db_session.flush()
        model = LogicalDataModel(
            name="orders_model", conceptual_model_id=conceptual.id, organization_id=org.id
        )
        db_session.add(model)
        db_session.flush()
        db_session.refresh(model)
        result = DataModelValidationService().check_logical_model_standards(model, org.id)
        found = {(b["standard"], b["subject"]) for b in result["breaches"]}
        assert ("NAMING-1", "Model name") in found
        assert ("NAMING-1", "Entity tbl_customer_order") in found
        assert ("TYPING-1", "Entity tbl_customer_order") in found
        assert ("TYPING-2", "Entity tbl_customer_order") in found
        assert ("KEY-1", "Entity tbl_customer_order") in found
        assert all(b["standard_title"] for b in result["breaches"])
        assert result["entities_checked"] == 1

        twin = _entity(db_session, org.id, "Customer Order", entity_type="transactional",
                       data_classification="internal", technical_name="customer_order")
        db_session.refresh(twin)
        conceptual.data_entities.append(
            _entity(db_session, org.id, "customer  ", entity_type="master",
                    data_classification="internal", technical_name="cust")
        )
        db_session.flush()
        db_session.refresh(model)
        result = DataModelValidationService().check_logical_model_standards(model, org.id)
        assert ("REUSE-1", "Entity customer  ") in {
            (b["standard"], b["subject"]) for b in result["breaches"]
        }


# ------------------------------------------------------------------ routes

ROUTES = (
    "/data-governance/entities",
    "/data-governance/undeclared-copies",
    "/data-governance/domains",
    "/data-governance/models",
)


def test_routes_reach_data_architect_and_refuse_procurement(
    app, client, db_session, make_org, login_as
):
    org = make_org("a")
    architect = _user(db_session, org.id, "data_architect")
    buyer = _user(db_session, org.id, "procurement")
    for path in ROUTES:
        login_as(client, architect)
        assert client.get(path).status_code == 200, path
        login_as(client, buyer)
        assert client.get(path).status_code == 403, path


def test_declare_route_writes_and_refuses_unauthorised_and_foreign(
    app, client, db_session, make_org, login_as
):
    org_a, org_b = make_org("a"), make_org("b")
    architect = _user(db_session, org_a.id, "data_architect")
    buyer = _user(db_session, org_a.id, "procurement")
    application = _app(db_session, org_a.id, "Ledger")
    foreign = _app(db_session, org_b.id, "Foreign ledger")
    entity = _entity(db_session, org_a.id, "Payment")
    url = f"/data-governance/entities/{entity.id}/system-of-record"

    login_as(client, buyer)
    assert client.post(url, data={"application_id": application.id}).status_code == 403
    login_as(client, architect)
    client.post(url, data={"application_id": foreign.id})
    db_session.expire_all()
    assert entity.system_of_record_application_id is None
    client.post(url, data={"application_id": application.id})
    db_session.expire_all()
    assert entity.system_of_record_application_id == application.id
    page = client.get(f"/data-governance/entities/{entity.id}")
    assert page.status_code == 200 and b"Ledger" in page.data


def test_system_of_record_pages_share_the_application_picker_helper(
    app, client, db_session, make_org, login_as
):
    from flask import render_template

    org = make_org("picker")
    architect = _user(db_session, org.id, "data_architect")
    entity = _entity(db_session, org.id, "Customer")

    login_as(client, architect)
    detail_page = client.get(f"/data-governance/entities/{entity.id}")
    assert detail_page.status_code == 200
    assert b"applicationPickerSearch" in detail_page.data
    assert b"dataGovAppPicker" not in detail_page.data

    with app.test_request_context():
        partial = render_template(
            "solutions/partials/_linked_applications_picker.html",
            solution={"id": 1},
            linked_apps=[],
        )
    partial_bytes = partial.encode("utf-8")
    assert b"applicationPickerSearch" in partial_bytes
    assert b"/applications/api/list" in partial_bytes
    assert b"/api/enterprise/applications" not in partial_bytes


def test_entities_page_shows_legacy_system_of_record_label_with_reason(
    app, client, db_session, make_org, login_as
):
    org = make_org("legacy")
    architect = _user(db_session, org.id, "data_architect")
    _entity(
        db_session,
        org.id,
        "Invoice",
        system_of_record="Legacy ERP label with no matching app",
    )

    login_as(client, architect)
    page = client.get("/data-governance/entities")
    assert page.status_code == 200
    assert b"Legacy label: Legacy ERP label with no matching app" in page.data
    assert b"no matching application found yet" in page.data
    assert b"Not declared" not in page.data


def test_entity_detail_is_not_visible_to_another_organisation(
    app, client, db_session, make_org, login_as
):
    org_a, org_b = make_org("a"), make_org("b")
    entity = _entity(db_session, org_a.id, "Secret Entity")
    other = _user(db_session, org_b.id, "data_architect")
    login_as(client, other)
    assert client.get(f"/data-governance/entities/{entity.id}").status_code == 404
