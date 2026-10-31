"""Where data flows: the data_flow answer walks the access relationships and the
lineage rows of one organisation, each hop with its owner and its source, and
the data architecture page, the entity catalogue and the assistant's overview
agree on the entities."""

import re
import uuid

from app.modules.architecture.services.data_architecture_service import (
    data_flow_overview,
    record_entity_access,
)


def _estate(db_session, org_id):
    """Customer, written by a CRM, read by Billing, copied in a batch to a
    profile the warehouse writes and reporting reads."""
    from app.models.all_missing_models import DataLineage
    from app.models.application_portfolio import ApplicationComponent
    from app.models.process_data import DataDomain, DataEntity

    tag = uuid.uuid4().hex[:6]
    domain = DataDomain(name=f"Customer {tag}", organization_id=org_id, data_steward="Customer lead")
    db_session.add(domain)
    db_session.flush()
    customer = DataEntity(name=f"Customer {tag}", domain_id=domain.id, organization_id=org_id,
                          contains_pii=True)
    profile = DataEntity(name=f"Profile {tag}", domain_id=domain.id, organization_id=org_id,
                         contains_pii=True)
    orders = DataEntity(name=f"Order {tag}", domain_id=domain.id, organization_id=org_id)
    apps = {
        name: ApplicationComponent(name=f"{name} {tag}", organization_id=org_id)
        for name in ("CRM", "Billing", "Warehouse", "Reporting")
    }
    db_session.add_all([customer, profile, orders, *apps.values()])
    db_session.flush()
    record_entity_access(customer, apps["CRM"], ["C", "R", "U"])
    record_entity_access(customer, apps["Billing"], ["R"])
    record_entity_access(profile, apps["Warehouse"], ["C", "U"])
    record_entity_access(profile, apps["Reporting"], ["R"])
    copy = DataLineage(name="copy", organization_id=org_id, lineage_type="Batch",
                       archimate_element_id=customer.archimate_element_id,
                       target_archimate_element_id=profile.archimate_element_id)
    db_session.add(copy)
    db_session.flush()
    return {"tag": tag, "customer": customer, "profile": profile, "orders": orders, "apps": apps, "copy": copy}


def test_the_flow_runs_from_the_writer_to_the_readers_and_through_the_copy(db_session, make_org, tenant_ctx):
    from app.modules.intelligence.services.answer_envelope import KIND_DATA_FLOW, build_envelope
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("flow")
    with tenant_ctx(org.id):
        e = _estate(db_session, org.id)
        result = IntelligenceQueryService.data_flow(e["customer"].archimate_element_id)
        envelope = build_envelope(KIND_DATA_FLOW, {"element_id": e["customer"].archimate_element_id}, result)

    tag = e["tag"]
    steps = [
        (r["level"], r["hop"], result["elements"][str(r["element_id"])]["name"], r["relation"]["kind"])
        for r in result["rows"]
    ]
    assert steps == [
        (0, "writes", f"CRM {tag}", "explicit"),
        (1, "reads", f"Billing {tag}", "derived"),
        (1, "copied to", f"Profile {tag}", "explicit"),
        (2, "writes", f"Warehouse {tag}", "explicit"),
        (3, "reads", f"Reporting {tag}", "derived"),
    ]
    crm, billing, copy = result["rows"][0], result["rows"][1], result["rows"][2]
    assert crm["access"] == ["creates", "reads", "updates"]
    # A worked-out hop says what it was worked out from and rests on both accesses.
    assert billing["relation"]["provenance"]["text"] == (
        f"Worked out: CRM {tag} writes Customer {tag}, and Billing {tag} reads it")
    assert len(billing["relation"]["chain"]) == 2
    assert copy["relation"]["provenance"]["text"] == "Copy recorded in the data lineage (Batch)"
    assert copy["steward"] == "Customer lead"

    assert envelope["truth_class"] == "derived"
    cited = {(c["type"], c["id"]) for c in envelope["citations"]}
    assert ("data_lineage", e["copy"].id) in cited
    assert all(("relationship", rel_id) in cited for rel_id in billing["relation"]["chain"])


def test_an_entity_nothing_writes_lists_its_readers_as_recorded(db_session, make_org, tenant_ctx):
    from app.models.application_portfolio import ApplicationComponent
    from app.models.process_data import DataDomain, DataEntity
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("flow-readers")
    with tenant_ctx(org.id):
        domain = DataDomain(name="Ref", organization_id=org.id)
        db_session.add(domain)
        db_session.flush()
        country = DataEntity(name="Country", domain_id=domain.id, organization_id=org.id)
        app = ApplicationComponent(name="Shop", organization_id=org.id)
        db_session.add_all([country, app])
        db_session.flush()
        record_entity_access(country, app, ["R"])
        rows = IntelligenceQueryService.data_flow(country.archimate_element_id)["rows"]

    assert [(r["level"], r["hop"], r["relation"]["kind"]) for r in rows] == [(0, "reads", "explicit")]


def test_another_organisation_sees_no_flow_and_no_entities(db_session, make_org, tenant_ctx):
    from app.modules.intelligence.services.query_service import (
        ELEMENT_NOT_FOUND_REASON,
        IntelligenceQueryService,
    )

    org_a, org_b = make_org("flow-a"), make_org("flow-b")
    with tenant_ctx(org_a.id):
        e = _estate(db_session, org_a.id)
    with tenant_ctx(org_b.id):
        result = IntelligenceQueryService.data_flow(e["customer"].archimate_element_id)
        overview = data_flow_overview(org_b.id)

    assert result["rows"] == [] and result["reasons"] == [ELEMENT_NOT_FOUND_REASON]
    assert overview == {"entity_count": 0, "entities": [], "personal_data_applications": []}


def test_the_overview_names_who_uses_each_entity_and_who_handles_personal_data(db_session, make_org, tenant_ctx):
    org = make_org("flow-overview")
    with tenant_ctx(org.id):
        e = _estate(db_session, org.id)
        overview = data_flow_overview(org.id)

    tag = e["tag"]
    by_name = {row["name"]: row for row in overview["entities"]}
    assert overview["entity_count"] == 3
    customer = by_name[f"Customer {tag}"]
    assert customer["personal_data"] is True and customer["steward"] == "Customer lead"
    assert customer["copies"] == 1
    assert [(a["name"], a["access"]) for a in customer["applications"]] == [
        (f"Billing {tag}", ["reads"]),
        (f"CRM {tag}", ["creates", "reads", "updates"]),
    ]
    assert by_name[f"Order {tag}"]["applications"] == []
    assert [(a["name"], a["entities"]) for a in overview["personal_data_applications"]] == [
        (f"Billing {tag}", [f"Customer {tag}"]),
        (f"CRM {tag}", [f"Customer {tag}"]),
        (f"Reporting {tag}", [f"Profile {tag}"]),
        (f"Warehouse {tag}", [f"Profile {tag}"]),
    ]


def _architect(db_session, org_id):
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(email="flow-%s@example.com" % uuid.uuid4().hex[:10], first_name="D", last_name="A",
                organization_id=org_id, confirmed=True, enterprise_role="data_architect")
    user.password = "x" * 16
    db_session.add(user)
    db_session.flush()
    return user


def test_the_page_the_catalogue_and_the_api_agree_on_the_entities(app, db_session, make_org, tenant_ctx, client, login_as):
    org = make_org("flow-agree")
    with tenant_ctx(org.id):
        e = _estate(db_session, org.id)
    user = _architect(db_session, org.id)

    login_as(client, user)
    page = client.get("/architecture/data-architecture").get_data(as_text=True)
    login_as(client, user)
    catalogue = client.get("/architecture/data-entities").get_data(as_text=True)
    login_as(client, user)
    api = client.get("/architecture/api/data-flows").get_json()

    on_page = re.search(r'data-testid="data-entity-count">(\d+) ', page)
    assert on_page and int(on_page.group(1)) == api["entity_count"] == 3
    assert catalogue.count('data-testid="data-entity-row-') == 3
    assert all(f'data-testid="data-entity-row-{row["id"]}"' in catalogue for row in api["entities"])
    # The page opens on the flow of the personal data something writes.
    assert f"Where Customer {e['tag']} flows" in page
    assert page.count('data-testid="personal-data-application"') == 4
