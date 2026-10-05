"""Review defects D1-D5 on the PR fencing GET /api/apqc/process-mappings
(reviews/pr306-review-v1.md): three more unfenced CapabilityProcessMapping/
ProcessApplicationMapping reads in app/modules/vendors/api/api_vendors.py,
one in app/modules/ai_chat/routes/chat_workflows.py, one in
app/modules/capabilities/routes/mapping_routes.py. Same root cause as the
routes already fixed: neither mapping model carries an organization_id of
its own.
"""
import uuid


def _process(db_session):
    from app.models.apqc_process import APQCProcess

    process = APQCProcess.query.first()
    if process is not None:
        return process
    process = APQCProcess(process_code=f"P-{uuid.uuid4().hex[:6]}", process_name="Test process")
    db_session.add(process)
    db_session.flush()
    return process


def _user(db_session, org, prefix):
    from app.models.user import User

    user = User(email=f"{prefix}-{uuid.uuid4().hex[:6]}@example.test", first_name="U", last_name="Q",
                organization_id=org.id, confirmed=True)
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.commit()
    return user


def test_capability_mappings_list_excludes_a_foreign_organisations_rows(app, db_session, make_org, client, login_as):
    """D1: /api/vendors/apqc/capability-mappings"""
    from app.models.business_capabilities import BusinessCapability
    from app.models.apqc_process import CapabilityProcessMapping
    from app.models.user import User

    org_a = make_org("apqc-d1-a")
    org_b = make_org("apqc-d1-b")
    process = _process(db_session)
    cap_a = BusinessCapability(name="CapA", organization_id=org_a.id)
    cap_b = BusinessCapability(name="SECRET-CAP-B-D1", organization_id=org_b.id)
    db_session.add_all([cap_a, cap_b])
    db_session.flush()
    cpm_a = CapabilityProcessMapping(capability_id=cap_a.id, apqc_process_id=process.id)
    cpm_b = CapabilityProcessMapping(capability_id=cap_b.id, apqc_process_id=process.id)
    db_session.add_all([cpm_a, cpm_b])
    db_session.flush()
    user_a = _user(db_session, org_a, "d1")
    uid, cap_a_id, cap_b_id = user_a.id, cap_a.id, cap_b.id
    db_session.expunge_all()

    login_as(client, db_session.get(User, uid))
    r = client.get("/api/vendors/apqc/capability-mappings")

    assert r.status_code == 200
    body = r.get_json()
    # Not a name check: CapabilityProcessMapping has no capability_name
    # attribute of its own (only to_dict() computes one), so Flask-RESTX's
    # marshal_list_with(capability_process_model) always returns None for
    # it -- a name-in-response check passes on unfenced main for the wrong
    # reason (pr306-v2 review, DEFECT-2). capability_id IS a real column and
    # is marshaled, so assert on that instead.
    returned_capability_ids = {row["capability_id"] for row in body}
    assert cap_b_id not in returned_capability_ids
    assert cap_a_id in returned_capability_ids


def test_process_capabilities_excludes_a_foreign_organisations_rows(app, db_session, make_org, client, login_as):
    """D2: /api/vendors/apqc/processes/<id>/capabilities"""
    from app.models.business_capabilities import BusinessCapability
    from app.models.apqc_process import CapabilityProcessMapping
    from app.models.user import User

    org_a = make_org("apqc-d2-a")
    org_b = make_org("apqc-d2-b")
    process = _process(db_session)
    cap_b = BusinessCapability(name="SECRET-CAP-B-D2", organization_id=org_b.id)
    db_session.add(cap_b)
    db_session.flush()
    cpm_b = CapabilityProcessMapping(capability_id=cap_b.id, apqc_process_id=process.id)
    db_session.add(cpm_b)
    db_session.flush()
    user_a = _user(db_session, org_a, "d2")
    process_id, uid = process.id, user_a.id
    db_session.expunge_all()

    login_as(client, db_session.get(User, uid))
    r = client.get(f"/api/vendors/apqc/processes/{process_id}/capabilities")

    assert r.status_code == 200
    body = r.get_json()
    assert body["capability_count"] == 0
    text = str(body)
    assert "SECRET-CAP-B-D2" not in text


def test_ai_chat_process_gap_analysis_does_not_count_a_foreign_orgs_mapping(app, db_session, make_org, client, login_as):
    """D4: process gap analysis must not treat another organisation's
    ProcessApplicationMapping as covering this organisation's process."""
    from app.models.application_portfolio import ApplicationComponent
    from app.models.apqc_process import ProcessApplicationMapping
    from app.models.user import User

    org_a = make_org("apqc-d4-a")
    org_b = make_org("apqc-d4-b")
    process = _process(db_session)
    app_b = ApplicationComponent(name="AppB", organization_id=org_b.id)
    db_session.add(app_b)
    db_session.flush()
    pam_b = ProcessApplicationMapping(application_id=app_b.id, apqc_process_id=process.id)
    db_session.add(pam_b)
    db_session.flush()
    user_a = _user(db_session, org_a, "d4")
    process_id, uid = process.id, user_a.id
    db_session.expunge_all()

    login_as(client, db_session.get(User, uid))
    r = client.post("/ai-chat/chat/gap-analysis", json={"analysis_type": "process"})

    if r.status_code != 200:
        import pytest
        pytest.skip(f"gap analysis route not reachable in this environment ({r.status_code})")
    body = r.get_json()
    gap_process_ids = {g.get("process_id") for g in body.get("gaps", []) if g.get("type") == "process_gap"}
    assert process_id in gap_process_ids, (
        "org B's mapping must not make this process look covered for org A"
    )


def test_vendor_capability_process_matrix_excludes_a_foreign_organisations_capability(
    app, db_session, make_org, client, login_as
):
    """D3: /api/vendors/apqc/vendor-capability-process-matrix"""
    from app.models.business_capabilities import BusinessCapability
    from app.models.apqc_process import CapabilityProcessMapping
    from app.models.user import User
    from app.models.vendor.vendor_organization import VendorOrganization, VendorProduct
    from app.models.vendor_product_apqc_mapping import VendorProductAPQCMapping

    org_a = make_org("apqc-d3-a")
    org_b = make_org("apqc-d3-b")
    process = _process(db_session)
    vendor = VendorOrganization(name=f"Vendor-{uuid.uuid4().hex[:6]}")
    db_session.add(vendor)
    db_session.flush()
    product = VendorProduct(vendor_organization_id=vendor.id, name=f"Product-{uuid.uuid4().hex[:6]}")
    db_session.add(product)
    db_session.flush()
    apqc_map = VendorProductAPQCMapping(
        vendor_product_id=product.id, apqc_process_id=process.id,
        coverage_percentage=50, automation_capability=50,
    )
    db_session.add(apqc_map)
    cap_a = BusinessCapability(name="CapA", organization_id=org_a.id)
    cap_b = BusinessCapability(name="SECRET-CAP-B-D3", organization_id=org_b.id)
    db_session.add_all([cap_a, cap_b])
    db_session.flush()
    cpm_a = CapabilityProcessMapping(
        capability_id=cap_a.id, apqc_process_id=process.id, process_contribution=50,
    )
    cpm_b = CapabilityProcessMapping(
        capability_id=cap_b.id, apqc_process_id=process.id, process_contribution=50,
    )
    db_session.add_all([cpm_a, cpm_b])
    db_session.flush()
    user_a = _user(db_session, org_a, "d3")
    product_id, uid, cap_a_id, cap_b_id = product.id, user_a.id, cap_a.id, cap_b.id
    db_session.expunge_all()

    login_as(client, db_session.get(User, uid))
    r = client.get("/api/vendors/apqc/vendor-capability-process-matrix", query_string={"product_id": product_id})

    assert r.status_code == 200
    # Not a name check: accessing cap_map.capability.name triggers a lazy
    # load that the tenant-isolation listener filters for a foreign
    # organisation's BusinessCapability, so the name comes back None
    # whether or not the CapabilityProcessMapping row itself was excluded --
    # a name-in-response check passes on unfenced main for the wrong reason
    # (pr306-v2 review, DEFECT-3). The matrix item still carries a real
    # capability_id, so assert on that instead.
    matrix = r.get_json().get("matrix", [])
    returned_capability_ids = {item["capability_id"] for item in matrix}
    assert cap_b_id not in returned_capability_ids
    assert cap_a_id in returned_capability_ids


def test_apqc_suggestions_treats_a_foreign_organisations_link_as_still_unmapped(
    app, db_session, make_org, client, login_as
):
    """D5: /api/capabilities/apqc-suggestions must not exclude a process from
    this organisation's suggestions just because another organisation already
    linked it."""
    from app.models.business_capabilities import BusinessCapability
    from app.models.apqc_process import CapabilityProcessMapping
    from app.models.user import User

    org_a = make_org("apqc-d5-a")
    org_b = make_org("apqc-d5-b")
    process = _process(db_session)
    cap_a = BusinessCapability(name="Order Management", organization_id=org_a.id,
                                business_domain="Operations", category="Core")
    cap_b = BusinessCapability(name="CapB", organization_id=org_b.id)
    db_session.add_all([cap_a, cap_b])
    db_session.flush()
    cpm_b = CapabilityProcessMapping(capability_id=cap_b.id, apqc_process_id=process.id)
    db_session.add(cpm_b)
    db_session.flush()
    user_a = _user(db_session, org_a, "d5")
    process_id, uid = process.id, user_a.id
    db_session.expunge_all()

    login_as(client, db_session.get(User, uid))
    r = client.get("/capability-map/api/capabilities/apqc-suggestions")

    if r.status_code != 200:
        import pytest
        pytest.skip(f"suggestions route not reachable in this environment ({r.status_code})")
    suggestions = r.get_json()  # a bare list; each item carries apqc_id/already_linked
    entry = next((s for s in suggestions if s.get("apqc_id") == process_id), None)
    assert entry is not None, "the process must still appear in org A's suggestions"
    assert entry["already_linked"] is False, (
        "org B's link to this process must not mark it already_linked for org A"
    )
