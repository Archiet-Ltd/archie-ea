"""Data-subject requests: scoping, search assignment, access and erasure.

Two organisations throughout. The Data Protection Officer in organisation A
scopes a request against A's model, assigns and completes searches, and runs
an erasure on one of A's users; organisation B's model, users, requests and
audit entries are never listed, counted or touched.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest


def _suffix():
    return uuid.uuid4().hex[:10]


def _make_user(db_session, org, *, role="solution_architect", email=None):
    from app.models.user import User

    user = User(
        confirmed=True,
        email=email or f"dsr-{_suffix()}@example.test",
        first_name="Data",
        last_name="Subject",
        organization_id=org.id,
        enterprise_role=role,
        password_hash="scrypt:dummy",
        external_id=f"ext-{_suffix()}",
        sso_provider="oidc",
    )
    db_session.add(user)
    db_session.flush()
    return user


def _element(db_session, org, type_, layer, name=None):
    from app.models.archimate_core import ArchiMateElement

    el = ArchiMateElement(name=name or f"{type_}-{_suffix()}", type=type_, layer=layer, organization_id=org.id)
    db_session.add(el)
    db_session.flush()
    return el


def _relate(db_session, org, type_, source_id, target_id):
    from app.models.archimate_core import ArchiMateRelationship

    rel = ArchiMateRelationship(type=type_, source_id=source_id, target_id=target_id, organization_id=org.id)
    db_session.add(rel)
    db_session.flush()
    return rel


def _seed_estate(db_session, org, label):
    """One personal-data category used by two applications (one directly,
    one through an interface), a third application that only exchanges data
    with the first, an application with no link at all, a non-personal
    category, and a supplier contract on the first application."""
    from app.models.application_portfolio import ApplicationComponent, VendorContract
    from app.models.process_data import DataDomain, DataEntity
    from app.models.vendor.vendor_organization import VendorOrganization

    s = _suffix()
    domain = DataDomain(name=f"Domain {label} {s}", organization_id=org.id)
    db_session.add(domain)
    db_session.flush()
    customer = DataEntity(name=f"Customer {label} {s}", domain_id=domain.id, contains_pii=True, organization_id=org.id)
    product = DataEntity(name=f"Product {label} {s}", domain_id=domain.id, contains_pii=False, organization_id=org.id)
    db_session.add_all([customer, product])
    db_session.flush()
    assert customer.archimate_element_id and product.archimate_element_id

    crm = ApplicationComponent(name=f"CRM {label} {s}", organization_id=org.id, vendor_name=f"Hosting {label} {s}")
    billing = ApplicationComponent(name=f"Billing {label} {s}", organization_id=org.id)
    reporting = ApplicationComponent(name=f"Reporting {label} {s}", organization_id=org.id)
    unlinked = ApplicationComponent(name=f"Intranet {label} {s}", organization_id=org.id)
    catalogue = ApplicationComponent(name=f"Catalogue {label} {s}", organization_id=org.id)
    db_session.add_all([crm, billing, reporting, unlinked, catalogue])
    db_session.flush()

    _relate(db_session, org, "access", crm.archimate_element_id, customer.archimate_element_id)
    api = _element(db_session, org, "ApplicationInterface", "application", name=f"Billing API {label} {s}")
    _relate(db_session, org, "composition", billing.archimate_element_id, api.id)
    _relate(db_session, org, "access", api.id, customer.archimate_element_id)
    _relate(db_session, org, "flow", reporting.archimate_element_id, crm.archimate_element_id)
    _relate(db_session, org, "access", catalogue.archimate_element_id, product.archimate_element_id)

    vendor = VendorOrganization(name=f"Processor {label} {s}")
    db_session.add(vendor)
    db_session.flush()
    contract = VendorContract(
        contract_name=f"CRM hosting {label}", organization_id=org.id, application_id=crm.id,
        vendor_id=vendor.id, start_date=dt.date(2026, 1, 1),
    )
    db_session.add(contract)
    db_session.flush()
    return {
        "customer": customer, "product": product, "crm": crm, "billing": billing,
        "reporting": reporting, "unlinked": unlinked, "catalogue": catalogue,
        "vendor": vendor, "api": api,
    }


def _names(scope, kind):
    return {i["name"] for i in scope["items"] if i["kind"] == kind}


# ---------------------------------------------------------------------------
# Scoping
# ---------------------------------------------------------------------------


def test_scope_lists_every_linked_system_and_processor_and_nothing_else(db_session, make_org, tenant_ctx):
    from app.services.gdpr_service import GDPRService

    org_a, org_b = make_org("dsr-a"), make_org("dsr-b")
    a = _seed_estate(db_session, org_a, "A")
    b = _seed_estate(db_session, org_b, "B")

    with tenant_ctx(org_a.id):
        scope = GDPRService.scope_data_subject_request(org_a.id, "access")

    systems = _names(scope, "system")
    assert systems == {a["crm"].name, a["billing"].name}
    # Recorded only as exchanging data with CRM, not as using the category.
    assert a["reporting"].name not in systems
    assert a["unlinked"].name not in systems
    # Uses a category that is not personal data.
    assert a["catalogue"].name not in systems
    assert _names(scope, "processor") == {a["vendor"].name, a["crm"].vendor_name}
    # Nothing of organisation B's.
    every_name = {i["name"] for i in scope["items"]}
    assert not every_name & {b["crm"].name, b["billing"].name, b["vendor"].name, b["crm"].vendor_name}
    assert [c["name"] for c in scope["categories"]] == [a["customer"].name]

    billing = next(i for i in scope["items"] if i["name"] == a["billing"].name)
    assert billing["via"][0]["path"] == [a["customer"].name, a["api"].name, a["billing"].name]
    processor = next(i for i in scope["items"] if i["name"] == a["vendor"].name)
    assert processor["via"] == [{"system": a["crm"].name, "link": "Contract: CRM hosting A"}]


def test_scope_for_b_lists_only_bs_systems(db_session, make_org, tenant_ctx):
    from app.services.gdpr_service import GDPRService

    org_a, org_b = make_org("dsr-a"), make_org("dsr-b")
    a = _seed_estate(db_session, org_a, "A")
    b = _seed_estate(db_session, org_b, "B")

    with tenant_ctx(org_b.id):
        scope = GDPRService.scope_data_subject_request(org_b.id, "erasure")
    assert _names(scope, "system") == {b["crm"].name, b["billing"].name}
    assert not {i["name"] for i in scope["items"]} & {a["crm"].name, a["billing"].name, a["vendor"].name}


def test_scope_refuses_to_run_for_another_organisation(db_session, make_org, tenant_ctx):
    from app.services.gdpr_service import DataSubjectRequestError, GDPRService

    org_a, org_b = make_org("dsr-a"), make_org("dsr-b")
    _seed_estate(db_session, org_a, "A")
    with tenant_ctx(org_b.id):
        with pytest.raises(DataSubjectRequestError):
            GDPRService.scope_data_subject_request(org_a.id, "access")


def test_category_with_no_recorded_link_is_listed_as_unlinked_and_ai_leg_not_recorded(
    db_session, make_org, tenant_ctx
):
    from app.models.process_data import DataDomain, DataEntity
    from app.services.gdpr_service import AI_SYSTEM_NOT_RECORDED, GDPRService

    org = make_org("dsr-unlinked")
    a = _seed_estate(db_session, org, "A")
    domain = DataDomain(name=f"HR {_suffix()}", organization_id=org.id)
    db_session.add(domain)
    db_session.flush()
    lonely = DataEntity(name=f"Health {_suffix()}", domain_id=domain.id, contains_pii=True, organization_id=org.id)
    db_session.add(lonely)
    db_session.flush()

    with tenant_ctx(org.id):
        scope = GDPRService.scope_data_subject_request(org.id, "access")
        traced = GDPRService.trace_categories(org.id, [lonely, a["customer"]])

    assert {"id": lonely.id, "name": lonely.name} in scope["unlinked_categories"]
    assert traced[0]["systems"] == []
    assert {s["ai_systems"] for s in traced[1]["systems"]} == {AI_SYSTEM_NOT_RECORDED}
    assert AI_SYSTEM_NOT_RECORDED == "not recorded"


# ---------------------------------------------------------------------------
# Erasure
# ---------------------------------------------------------------------------


def _seed_personal_data(db_session, org, user):
    from sqlalchemy import text

    from app.models.ai_chat_document import AIChatDocumentUpload

    thread_id = str(uuid.uuid4())
    now = dt.datetime.utcnow()
    db_session.execute(
        text(
            "INSERT INTO conversation_threads (id, user_id, title, model, created_at, updated_at, message_count) "
            "VALUES (:id, :user_id, 'My question', 'm', :now, :now, 2)"
        ),
        {"id": thread_id, "user_id": user.id, "now": now},
    )
    for role in ("user", "assistant"):
        db_session.execute(
            text(
                "INSERT INTO conversation_messages (id, thread_id, role, content, created_at) "
                "VALUES (:id, :thread_id, :role, 'personal words', :now)"
            ),
            {"id": str(uuid.uuid4()), "thread_id": thread_id, "role": role, "now": now},
        )
    upload = AIChatDocumentUpload(
        file_name=f"cv-{_suffix()}.pdf", original_filename="my-cv.pdf", uploaded_by_id=user.id,
        organization_id=org.id,
    )
    db_session.add(upload)
    db_session.flush()
    return thread_id


def _count(db_session, sql, **params):
    from sqlalchemy import text

    return db_session.execute(text(sql), params).scalar()


def test_erasure_removes_target_fields_and_records_audit_evidence_in_its_own_org(
    db_session, make_org, tenant_ctx
):
    from app.models.audit_log import AuditLog
    from app.services.gdpr_service import GDPRService

    org_a, org_b = make_org("dsr-erase-a"), make_org("dsr-erase-b")
    dpo = _make_user(db_session, org_a, role="security_architect")
    target = _make_user(db_session, org_a)
    bystander = _make_user(db_session, org_a)
    other_org_user = _make_user(db_session, org_b)
    for u in (target, bystander, other_org_user):
        _seed_personal_data(db_session, org_a if u.organization_id == org_a.id else org_b, u)

    with tenant_ctx(org_a.id):
        req = GDPRService.create_request(org_a.id, dpo, "erasure", subject_user_id=target.id)
        preview = GDPRService.review_erasure(org_a.id, req, dpo)
        assert preview["conversation_threads"] == 1 and preview["uploaded_documents"] == 1
        evidence = GDPRService.run_erasure(org_a.id, req, dpo)

    db_session.refresh(target)
    for field in ("email", "first_name", "last_name", "password_hash", "external_id", "sso_provider"):
        assert getattr(target, field) is None
    assert _count(db_session, "SELECT COUNT(*) FROM conversation_threads WHERE user_id = :u", u=target.id) == 0
    assert _count(
        db_session, "SELECT COUNT(*) FROM ai_chat_document_uploads WHERE uploaded_by_id = :u", u=target.id
    ) == 0
    assert evidence["fields_cleared"] == ["first_name", "last_name", "email", "password_hash", "external_id", "sso_provider"]
    assert evidence["conversation_threads_deleted"] == 1
    assert evidence["conversation_messages_deleted"] == 2
    assert evidence["uploaded_documents_deleted"] == 1
    assert evidence["removed_by_id"] == dpo.id

    entry = db_session.get(AuditLog, evidence["audit_entry_id"])
    assert entry.action == "gdpr_delete"
    assert entry.organization_id == org_a.id
    assert entry.user_id == dpo.id
    assert entry.new_value["subject_user_id"] == target.id
    # No personal value is copied into the evidence.
    assert "personal words" not in str(entry.new_value)

    db_session.refresh(req)
    assert req.status == "completed"
    assert req.erasure_evidence_json["review"]["reviewed_by_id"] == dpo.id
    assert req.erasure_evidence_json["result"]["audit_entry_id"] == entry.id

    # Another person in the same organisation, and a person in another
    # organisation, keep everything.
    for u in (bystander, other_org_user):
        db_session.refresh(u)
        assert u.email and u.first_name and u.password_hash
        assert _count(db_session, "SELECT COUNT(*) FROM conversation_threads WHERE user_id = :u", u=u.id) == 1
        assert _count(
            db_session, "SELECT COUNT(*) FROM ai_chat_document_uploads WHERE uploaded_by_id = :u", u=u.id
        ) == 1
    # Evidence rows are per organisation: nothing was written in B.
    assert AuditLog.query.filter(
        AuditLog.organization_id == org_b.id, AuditLog.table_name == "gdpr_requests"
    ).count() == 0


def test_erasure_cannot_run_before_review(db_session, make_org, tenant_ctx):
    from app.services.gdpr_service import DataSubjectRequestError, GDPRService

    org = make_org("dsr-review")
    dpo = _make_user(db_session, org, role="security_architect")
    target = _make_user(db_session, org)
    email = target.email
    with tenant_ctx(org.id):
        req = GDPRService.create_request(org.id, dpo, "erasure", subject_user_id=target.id)
        with pytest.raises(DataSubjectRequestError):
            GDPRService.run_erasure(org.id, req, dpo)
    db_session.refresh(target)
    assert target.email == email


def test_erasure_executor_refuses_a_user_of_another_organisation(db_session, make_org, tenant_ctx):
    from app.services.gdpr_service import DataSubjectRequestError, GDPRService

    org_a, org_b = make_org("dsr-x-a"), make_org("dsr-x-b")
    dpo = _make_user(db_session, org_a, role="security_architect")
    victim = _make_user(db_session, org_b)
    _seed_personal_data(db_session, org_b, victim)
    email = victim.email

    assert GDPRService.erase_platform_user_data(org_a.id, victim.id, dpo.id) is None
    with tenant_ctx(org_a.id):
        with pytest.raises(DataSubjectRequestError):
            GDPRService.create_request(org_a.id, dpo, "erasure", subject_user_id=victim.id)
    db_session.refresh(victim)
    assert victim.email == email
    assert _count(db_session, "SELECT COUNT(*) FROM conversation_threads WHERE user_id = :u", u=victim.id) == 1


# ---------------------------------------------------------------------------
# Routes, as the Data Protection Officer
# ---------------------------------------------------------------------------


def test_dpo_scopes_assigns_completes_and_org_b_cannot_see_the_request(
    db_session, make_org, client, login_as
):
    from app.models.gdpr_request import GDPRRequest

    org_a, org_b = make_org("dsr-route-a"), make_org("dsr-route-b")
    a = _seed_estate(db_session, org_a, "A")
    dpo_a = _make_user(db_session, org_a, role="security_architect")
    owner_a = _make_user(db_session, org_a)
    dpo_b = _make_user(db_session, org_b, role="security_architect")
    outsider_b = _make_user(db_session, org_b)

    login_as(client, dpo_a)
    resp = client.post(
        "/compliance/data-subject-requests",
        data={"request_type": "access", "subject_reference": "Customer ref 991"},
    )
    assert resp.status_code == 302
    req = GDPRRequest.query.filter(
        GDPRRequest.organization_id == org_a.id, GDPRRequest.subject_reference == "Customer ref 991"
    ).one()
    keys = [i["key"] for i in req.scope_json["items"]]
    assert "system:%s" % a["crm"].archimate_element_id in keys

    login_as(client, dpo_a)
    page = client.get(f"/compliance/data-subject-requests/{req.id}").get_data(as_text=True)
    assert a["crm"].name in page and a["billing"].name in page and a["unlinked"].name not in page

    crm_key = "system:%s" % a["crm"].archimate_element_id
    login_as(client, dpo_a)
    assert client.post(
        f"/compliance/data-subject-requests/{req.id}/assign", data={"key": crm_key, "assignee_id": owner_a.id}
    ).status_code == 302
    # An assignee from another organisation is refused.
    login_as(client, dpo_a)
    client.post(f"/compliance/data-subject-requests/{req.id}/assign", data={"key": crm_key, "assignee_id": outsider_b.id})
    login_as(client, dpo_a)
    client.post(f"/compliance/data-subject-requests/{req.id}/complete", data={"key": crm_key})
    db_session.refresh(req)
    crm = next(i for i in req.scope_json["items"] if i["key"] == crm_key)
    assert crm["assignee_id"] == owner_a.id
    assert crm["status"] == "done"

    login_as(client, dpo_a)
    page = client.get(f"/compliance/data-subject-requests/{req.id}").get_data(as_text=True)
    assert owner_a.email in page and "Search assigned" in page

    # Organisation B's officer: not in the list, not by id, cannot act on it.
    login_as(client, dpo_b)
    listing = client.get("/compliance/data-subject-requests").get_data(as_text=True)
    assert "Customer ref 991" not in listing and a["crm"].name not in listing
    login_as(client, dpo_b)
    assert client.get(f"/compliance/data-subject-requests/{req.id}").status_code == 404
    login_as(client, dpo_b)
    assert client.post(
        f"/compliance/data-subject-requests/{req.id}/complete",
        data={"key": "system:%s" % a["billing"].archimate_element_id},
    ).status_code == 404


def test_dpo_erasure_route_is_reviewed_then_run_and_org_b_cannot_run_it(
    db_session, make_org, client, login_as
):
    org_a, org_b = make_org("dsr-er-a"), make_org("dsr-er-b")
    dpo_a = _make_user(db_session, org_a, role="security_architect")
    target = _make_user(db_session, org_a)
    dpo_b = _make_user(db_session, org_b, role="security_architect")
    email = target.email

    login_as(client, dpo_a)
    client.post("/compliance/data-subject-requests", data={"request_type": "erasure", "subject_user_id": target.id})
    from app.models.gdpr_request import GDPRRequest

    req = GDPRRequest.query.filter(GDPRRequest.organization_id == org_a.id, GDPRRequest.user_id == target.id).one()

    # B cannot review or run A's erasure.
    login_as(client, dpo_b)
    assert client.post(f"/compliance/data-subject-requests/{req.id}/erasure/review").status_code == 404
    login_as(client, dpo_b)
    assert client.post(
        f"/compliance/data-subject-requests/{req.id}/erasure/run", data={"confirm": "erase"}
    ).status_code == 404
    db_session.refresh(target)
    assert target.email == email

    # A: running before review changes nothing.
    login_as(client, dpo_a)
    client.post(f"/compliance/data-subject-requests/{req.id}/erasure/run", data={"confirm": "erase"})
    db_session.refresh(target)
    assert target.email == email

    login_as(client, dpo_a)
    client.post(f"/compliance/data-subject-requests/{req.id}/erasure/review")
    login_as(client, dpo_a)
    client.post(f"/compliance/data-subject-requests/{req.id}/erasure/run", data={"confirm": "erase"})
    db_session.refresh(target)
    assert target.email is None

    login_as(client, dpo_a)
    page = client.get(f"/compliance/data-subject-requests/{req.id}").get_data(as_text=True)
    assert "Erasure run" in page and "Erasure reviewed" in page


def test_access_fulfilment_returns_the_subjects_data_and_records_evidence(
    db_session, make_org, client, login_as
):
    from app.models.audit_log import AuditLog
    from app.models.gdpr_request import GDPRRequest

    org = make_org("dsr-access")
    dpo = _make_user(db_session, org, role="security_architect")
    subject = _make_user(db_session, org)
    _seed_personal_data(db_session, org, subject)

    login_as(client, dpo)
    client.post("/compliance/data-subject-requests", data={"request_type": "access", "subject_user_id": subject.id})
    req = GDPRRequest.query.filter(GDPRRequest.organization_id == org.id, GDPRRequest.user_id == subject.id).one()
    login_as(client, dpo)
    resp = client.post(f"/compliance/data-subject-requests/{req.id}/access")
    assert resp.status_code == 200
    body = resp.get_json(force=True)
    assert body["profile"]["email"] == subject.email
    assert body["conversations"][0]["messages"][0]["content"] == "personal words"
    assert body["uploaded_documents"][0]["file_name"] == "my-cv.pdf"
    assert AuditLog.query.filter(
        AuditLog.organization_id == org.id, AuditLog.table_name == "gdpr_requests",
        AuditLog.record_id == req.id, AuditLog.action == "gdpr_access",
    ).count() == 1


def test_people_without_the_dpo_role_cannot_reach_the_pages(db_session, make_org, client, login_as):
    org = make_org("dsr-deny")
    architect = _make_user(db_session, org, role="solution_architect")
    login_as(client, architect)
    assert client.get("/compliance/data-subject-requests").status_code == 403
    login_as(client, architect)
    assert client.get("/compliance/personal-data-trace").status_code == 403


def test_trace_page_cites_each_hop_and_shows_ai_leg_not_recorded(db_session, make_org, client, login_as):
    org = make_org("dsr-trace")
    a = _seed_estate(db_session, org, "A")
    dpo = _make_user(db_session, org, role="security_architect")
    login_as(client, dpo)
    page = client.get(f"/compliance/personal-data-trace?category_id={a['customer'].id}").get_data(as_text=True)
    assert f"{a['customer'].name} → {a['api'].name} → {a['billing'].name}" in page
    assert a["vendor"].name in page
    assert "AI systems:</span> not recorded" in page
    assert a["reporting"].name not in page


# ---------------------------------------------------------------------------
# An erased person must not break the people pickers of everyone else
# ---------------------------------------------------------------------------


def test_people_list_omits_an_erased_person_and_stays_organisation_scoped(
    db_session, make_org, tenant_ctx, client, login_as
):
    from app.services.gdpr_service import GDPRService

    org_a, org_b = make_org("dsr-people-a"), make_org("dsr-people-b")
    dpo = _make_user(db_session, org_a, role="security_architect")
    kept = _make_user(db_session, org_a)
    erased = _make_user(db_session, org_a)
    b_user = _make_user(db_session, org_b)

    with tenant_ctx(org_a.id):
        req = GDPRService.create_request(org_a.id, dpo, "erasure", subject_user_id=erased.id)
        GDPRService.review_erasure(org_a.id, req, dpo)
        GDPRService.run_erasure(org_a.id, req, dpo)

    login_as(client, dpo)
    people = client.get("/api/users").get_json()["users"]
    ids = {p["id"] for p in people}
    assert kept.id in ids and dpo.id in ids
    assert erased.id not in ids
    assert b_user.id not in ids
    # Every person offered has a name and an address the pickers can search.
    assert all(p["email"] for p in people)


def test_people_pickers_tolerate_a_person_with_no_name_or_address():
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "app" / "templates"
    for rel in (
        "solutions/programme_wizard.html",
        "solutions/edit.html",
        "capability_management/governance_dashboard.html",
        "applications/list_simple.html",
        "applications/create.html",
        "applications/rationalization.html",
    ):
        text = (root / rel).read_text(encoding="utf-8")
        assert ".email.toLowerCase()" not in text, rel
        assert ".email || '').toLowerCase()" in text, rel


# ---------------------------------------------------------------------------
# The link is offered only to people who can open the page
# ---------------------------------------------------------------------------


def test_data_subject_requests_link_is_offered_only_to_people_who_can_open_it(
    db_session, make_org, client, login_as
):
    offered = {}
    for role in (
        "security_architect", "enterprise_architect", "solution_architect",
        "business_architect", "data_architect", "procurement",
    ):
        # A Community organisation admits three people, so one organisation each.
        user = _make_user(db_session, make_org("dsr-directory"), role=role)
        login_as(client, user)
        html = client.get("/modules").get_data(as_text=True)
        login_as(client, user)
        opens = client.get("/compliance/data-subject-requests").status_code == 200
        offered[role] = ("/compliance/data-subject-requests" in html, opens)

    # What is offered is exactly what opens.
    assert all(shown == opens for shown, opens in offered.values()), offered
    assert offered["security_architect"] == (True, True)
    assert offered["enterprise_architect"] == (False, False)
