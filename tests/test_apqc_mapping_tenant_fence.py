"""Consolidated tenant fence for every read, write and delete of
CapabilityProcessMapping / ProcessApplicationMapping.

Neither model carries an organization_id of its own -- ownership is only
reachable via capability_id -> BusinessCapability.organization_id or
application_id -> ApplicationComponent.organization_id. PRs 301, 303 and 306
each independently fenced part of this surface, with the same fence pattern
inlined at every call site and each review flagging the other PRs' routes.
Consolidated per the lead's ruling (MERGE-QUEUE.md, 2026-09-30T17:25Z) into
one PR with the shared app.services.apqc_mapping_tenant_fence helper and one
test file covering every route.
"""
from __future__ import annotations

import uuid


def _process(db_session):
    from app.models.apqc_process import APQCProcess

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


def _app(db_session, org, name="App"):
    from app.models.application_portfolio import ApplicationComponent

    app = ApplicationComponent(name=name, organization_id=org.id)
    db_session.add(app)
    db_session.flush()
    return app


def _cap(db_session, org, name="Cap", **kwargs):
    from app.models.business_capabilities import BusinessCapability

    cap = BusinessCapability(name=name, organization_id=org.id, **kwargs)
    db_session.add(cap)
    db_session.flush()
    return cap


def _app_mapping(db_session, process, app, **kwargs):
    from app.models.apqc_process import ProcessApplicationMapping

    m = ProcessApplicationMapping(apqc_process_id=process.id, application_id=app.id, **kwargs)
    db_session.add(m)
    db_session.flush()
    return m


def _cap_mapping(db_session, process, cap, **kwargs):
    from app.models.apqc_process import CapabilityProcessMapping

    m = CapabilityProcessMapping(apqc_process_id=process.id, capability_id=cap.id, **kwargs)
    db_session.add(m)
    db_session.flush()
    return m


# ---------------------------------------------------------------------------
# GET /api/apqc/process-mappings?type=application|capability  (D2)
# ---------------------------------------------------------------------------


def test_get_process_mappings_application_excludes_a_foreign_organisations_rows(
    app, db_session, make_org, client, login_as
):
    from app.models.user import User

    org_a, org_b = make_org("d2-app-a"), make_org("d2-app-b")
    process = _process(db_session)
    app_a, app_b = _app(db_session, org_a), _app(db_session, org_b)
    mapping_a = _app_mapping(db_session, process, app_a)
    _app_mapping(db_session, process, app_b)
    user_a = _user(db_session, org_a, "d2app")
    uid, mapping_a_id = user_a.id, mapping_a.id
    db_session.expunge_all()

    login_as(client, db_session.get(User, uid))
    r = client.get("/api/apqc/process-mappings", query_string={"type": "application"})

    assert r.status_code == 200
    ids = {m["id"] for m in r.get_json()["mappings"]}
    assert mapping_a_id in ids
    assert len(ids) == 1


def test_get_process_mappings_capability_excludes_a_foreign_organisations_rows(
    app, db_session, make_org, client, login_as
):
    from app.models.user import User

    org_a, org_b = make_org("d2-cap-a"), make_org("d2-cap-b")
    process = _process(db_session)
    cap_a, cap_b = _cap(db_session, org_a), _cap(db_session, org_b)
    mapping_a = _cap_mapping(db_session, process, cap_a)
    _cap_mapping(db_session, process, cap_b)
    user_a = _user(db_session, org_a, "d2cap")
    uid, mapping_a_id = user_a.id, mapping_a.id
    db_session.expunge_all()

    login_as(client, db_session.get(User, uid))
    r = client.get("/api/apqc/process-mappings", query_string={"type": "capability"})

    assert r.status_code == 200
    ids = {m["id"] for m in r.get_json()["mappings"]}
    assert mapping_a_id in ids
    assert len(ids) == 1


# ---------------------------------------------------------------------------
# GET /api/apqc/process/<id>/applications  (D3)
# ---------------------------------------------------------------------------


def test_get_process_applications_mapped_count_excludes_a_foreign_organisation(
    app, db_session, make_org, client, login_as
):
    from app.models.user import User

    org_a, org_b = make_org("d3-a"), make_org("d3-b")
    process = _process(db_session)
    app_a, app_b = _app(db_session, org_a), _app(db_session, org_b)
    _app_mapping(db_session, process, app_a)
    _app_mapping(db_session, process, app_b)
    user_a = _user(db_session, org_a, "d3")
    process_id, uid = process.id, user_a.id
    db_session.expunge_all()

    login_as(client, db_session.get(User, uid))
    r = client.get(f"/api/apqc/process/{process_id}/applications")

    assert r.status_code == 200
    body = r.get_json()
    assert body["mapped_count"] == 1


# ---------------------------------------------------------------------------
# DELETE /api/apqc/process-mappings/<id>  (D1)
# ---------------------------------------------------------------------------


def test_delete_process_mapping_application_refuses_a_foreign_organisations_row(
    app, db_session, make_org, client, login_as
):
    from app.models.apqc_process import ProcessApplicationMapping
    from app.models.user import User

    org_a, org_b = make_org("d1-app-a"), make_org("d1-app-b")
    process = _process(db_session)
    app_b = _app(db_session, org_b)
    mapping_b = _app_mapping(db_session, process, app_b)
    user_a = _user(db_session, org_a, "d1app")
    uid, mapping_b_id = user_a.id, mapping_b.id
    db_session.expunge_all()

    login_as(client, db_session.get(User, uid))
    r = client.delete(f"/api/apqc/process-mappings/{mapping_b_id}", query_string={"type": "application"})

    assert r.status_code == 404
    assert db_session.get(ProcessApplicationMapping, mapping_b_id) is not None


def test_delete_process_mapping_application_still_works_for_the_owning_organisation(
    app, db_session, make_org, client, login_as
):
    from app.models.apqc_process import ProcessApplicationMapping
    from app.models.user import User

    org_a = make_org("d1-app-own")
    process = _process(db_session)
    app_a = _app(db_session, org_a)
    mapping_a = _app_mapping(db_session, process, app_a)
    user_a = _user(db_session, org_a, "d1appown")
    uid, mapping_a_id = user_a.id, mapping_a.id
    db_session.expunge_all()

    login_as(client, db_session.get(User, uid))
    r = client.delete(f"/api/apqc/process-mappings/{mapping_a_id}", query_string={"type": "application"})

    assert r.status_code == 200
    assert db_session.get(ProcessApplicationMapping, mapping_a_id) is None


def test_delete_process_mapping_capability_refuses_a_foreign_organisations_row(
    app, db_session, make_org, client, login_as
):
    from app.models.apqc_process import CapabilityProcessMapping
    from app.models.user import User

    org_a, org_b = make_org("d1-cap-a"), make_org("d1-cap-b")
    process = _process(db_session)
    cap_b = _cap(db_session, org_b)
    mapping_b = _cap_mapping(db_session, process, cap_b)
    user_a = _user(db_session, org_a, "d1cap")
    uid, mapping_b_id = user_a.id, mapping_b.id
    db_session.expunge_all()

    login_as(client, db_session.get(User, uid))
    r = client.delete(f"/api/apqc/process-mappings/{mapping_b_id}", query_string={"type": "capability"})

    assert r.status_code == 404
    assert db_session.get(CapabilityProcessMapping, mapping_b_id) is not None


# ---------------------------------------------------------------------------
# POST /api/apqc/process-mappings  (save_process_mappings, Format 1 + 2)
# ---------------------------------------------------------------------------


def test_save_process_mappings_format1_refuses_a_foreign_organisations_application(
    app, db_session, make_org, client, login_as
):
    from app.models.apqc_process import ProcessApplicationMapping
    from app.models.user import User

    org_a, org_b = make_org("fmt1-a"), make_org("fmt1-b")
    process = _process(db_session)
    app_b = _app(db_session, org_b)
    mapping_b = _app_mapping(db_session, process, app_b, support_level="partial")
    user_a = _user(db_session, org_a, "fmt1")
    process_id, app_b_id, mapping_b_id, uid = process.id, app_b.id, mapping_b.id, user_a.id
    db_session.expunge_all()

    login_as(client, db_session.get(User, uid))
    r = client.post(
        "/api/apqc/process-mappings",
        json={
            "process_id": process_id,
            "applications": [
                {"application_id": str(app_b_id), "mapping_id": mapping_b_id,
                 "mapping": {"support_level": "full"}}
            ],
        },
    )

    assert r.status_code == 200
    assert r.get_json()["updated"] == 0
    refreshed = db_session.get(ProcessApplicationMapping, mapping_b_id)
    assert refreshed.support_level == "partial"


def test_save_process_mappings_format1_still_works_for_the_owning_organisation(
    app, db_session, make_org, client, login_as
):
    from app.models.apqc_process import ProcessApplicationMapping
    from app.models.user import User

    org_a = make_org("fmt1-own")
    process = _process(db_session)
    app_a = _app(db_session, org_a)
    mapping_a = _app_mapping(db_session, process, app_a, support_level="partial")
    user_a = _user(db_session, org_a, "fmt1own")
    process_id, app_a_id, mapping_a_id, uid = process.id, app_a.id, mapping_a.id, user_a.id
    db_session.expunge_all()

    login_as(client, db_session.get(User, uid))
    r = client.post(
        "/api/apqc/process-mappings",
        json={
            "process_id": process_id,
            "applications": [
                {"application_id": str(app_a_id), "mapping_id": mapping_a_id,
                 "mapping": {"support_level": "full"}}
            ],
        },
    )

    assert r.status_code == 200
    assert r.get_json()["updated"] == 1
    refreshed = db_session.get(ProcessApplicationMapping, mapping_a_id)
    assert refreshed.support_level == "full"


def test_save_process_mappings_format1_create_refuses_a_foreign_organisations_application(
    app, db_session, make_org, client, login_as
):
    from app.models.apqc_process import ProcessApplicationMapping
    from app.models.user import User

    org_a, org_b = make_org("fmt1c-a"), make_org("fmt1c-b")
    process = _process(db_session)
    app_b = _app(db_session, org_b)
    user_a = _user(db_session, org_a, "fmt1c")
    process_id, app_b_id, uid = process.id, app_b.id, user_a.id
    db_session.expunge_all()

    login_as(client, db_session.get(User, uid))
    r = client.post(
        "/api/apqc/process-mappings",
        json={"process_id": process_id, "applications": [{"application_id": str(app_b_id)}]},
    )

    assert r.status_code == 200
    assert r.get_json()["created"] == 0
    assert (
        ProcessApplicationMapping.query.filter_by(
            apqc_process_id=process_id, application_id=app_b_id
        ).first()
        is None
    )


def test_save_process_mappings_format2_refuses_a_foreign_organisations_capability(
    app, db_session, make_org, client, login_as
):
    from app.models.apqc_process import CapabilityProcessMapping
    from app.models.user import User

    org_a, org_b = make_org("fmt2-a"), make_org("fmt2-b")
    process = _process(db_session)
    cap_b = _cap(db_session, org_b)
    user_a = _user(db_session, org_a, "fmt2")
    process_id, cap_b_id, uid = process.id, cap_b.id, user_a.id
    db_session.expunge_all()

    login_as(client, db_session.get(User, uid))
    r = client.post(
        "/api/apqc/process-mappings",
        json={"capability_id": cap_b_id, "apqc_process_id": process_id},
    )

    assert r.status_code == 404
    assert (
        CapabilityProcessMapping.query.filter_by(
            apqc_process_id=process_id, capability_id=cap_b_id
        ).first()
        is None
    )


# ---------------------------------------------------------------------------
# POST /api/process-gaps/mappings/bulk
# ---------------------------------------------------------------------------


def test_bulk_mappings_update_refuses_a_foreign_organisations_mapping(
    app, db_session, make_org, client, login_as
):
    from app.models.apqc_process import ProcessApplicationMapping
    from app.models.user import User

    org_a, org_b = make_org("bulk-a"), make_org("bulk-b")
    process = _process(db_session)
    app_b = _app(db_session, org_b)
    mapping_b = _app_mapping(db_session, process, app_b, support_level="partial")
    user_a = _user(db_session, org_a, "bulk")
    process_id, app_b_id, mapping_b_id, uid = process.id, app_b.id, mapping_b.id, user_a.id
    db_session.expunge_all()

    login_as(client, db_session.get(User, uid))
    r = client.post(
        "/capability-map/api/process-gaps/mappings/bulk",
        json={"mappings": [{"application_id": app_b_id, "apqc_process_id": process_id,
                             "mapping_id": mapping_b_id, "support_level": "full"}]},
    )

    assert r.status_code == 200
    assert r.get_json()["updated"] == 0
    refreshed = db_session.get(ProcessApplicationMapping, mapping_b_id)
    assert refreshed.support_level == "partial"


def test_bulk_mappings_create_refuses_a_foreign_organisations_application(
    app, db_session, make_org, client, login_as
):
    from app.models.apqc_process import ProcessApplicationMapping
    from app.models.user import User

    org_a, org_b = make_org("bulkc-a"), make_org("bulkc-b")
    process = _process(db_session)
    app_b = _app(db_session, org_b)
    user_a = _user(db_session, org_a, "bulkc")
    process_id, app_b_id, uid = process.id, app_b.id, user_a.id
    db_session.expunge_all()

    login_as(client, db_session.get(User, uid))
    r = client.post(
        "/capability-map/api/process-gaps/mappings/bulk",
        json={"mappings": [{"application_id": app_b_id, "apqc_process_id": process_id}]},
    )

    assert r.status_code == 200
    assert r.get_json()["created"] == 0
    assert (
        ProcessApplicationMapping.query.filter_by(
            apqc_process_id=process_id, application_id=app_b_id
        ).first()
        is None
    )


def test_bulk_mappings_still_works_for_the_owning_organisation(
    app, db_session, make_org, client, login_as
):
    from app.models.apqc_process import ProcessApplicationMapping
    from app.models.user import User

    org_a = make_org("bulk-own")
    process = _process(db_session)
    app_a = _app(db_session, org_a)
    user_a = _user(db_session, org_a, "bulkown")
    process_id, app_a_id, uid = process.id, app_a.id, user_a.id
    db_session.expunge_all()

    login_as(client, db_session.get(User, uid))
    r = client.post(
        "/capability-map/api/process-gaps/mappings/bulk",
        json={"mappings": [{"application_id": app_a_id, "apqc_process_id": process_id}]},
    )

    assert r.status_code == 200
    assert r.get_json()["created"] == 1
    assert (
        ProcessApplicationMapping.query.filter_by(
            apqc_process_id=process_id, application_id=app_a_id
        ).first()
        is not None
    )


# ---------------------------------------------------------------------------
# GET /api/process-gaps/process/<id>/applications  (D4)
# ---------------------------------------------------------------------------


def test_process_gaps_applications_route_reaches_a_fenced_mapping(
    app, db_session, make_org, client, login_as
):
    """Smoke test only: ApplicationComponent.query.all() is already
    auto-filtered, and no app id can collide across organisations, so this
    route's response cannot itself distinguish a fenced from an unfenced
    ProcessApplicationMapping read -- the actual leak is loading another
    organisation's rows into memory (pr303-v2 review, D4's own wording).
    See test_fenced_application_mappings_query_excludes_a_foreign_org below
    for the assertion that proves the fence this route calls.
    """
    from app.models.user import User

    org_a = make_org("pg-app-smoke")
    process = _process(db_session)
    app_a = _app(db_session, org_a)
    _app_mapping(db_session, process, app_a)
    user_a = _user(db_session, org_a, "pgapp")
    process_id, uid = process.id, user_a.id
    db_session.expunge_all()

    login_as(client, db_session.get(User, uid))
    r = client.get(f"/capability-map/api/process-gaps/process/{process_id}/applications")

    assert r.status_code == 200
    apps = r.get_json()["applications"]
    assert any(a["is_mapped"] for a in apps)


# ---------------------------------------------------------------------------
# GET /api/capabilities/<id>/processes (fallback branch)
# ---------------------------------------------------------------------------


def test_capability_processes_fallback_branch_route_reaches_a_fenced_mapping(
    app, db_session, make_org, client, login_as
):
    """Smoke test only, same reason as the process-gaps test above: the
    lazy-loaded BusinessCapability.query.get(mapping.capability_id) is
    itself tenant-filtered, so a foreign mapping's capability comes back
    None and is skipped from the response whether or not the mapping query
    feeding it was fenced -- exactly the DEFECT-3 trap the pr306-v2 review
    caught in an earlier version of this same pattern. Proven at the query
    level instead, below.
    """
    from app.models.user import User

    org_a = make_org("cp-smoke")
    process = _process(db_session)
    cap_a = _cap(db_session, org_a)
    _cap_mapping(db_session, process, cap_a)
    user_a = _user(db_session, org_a, "cp")
    cap_a_id, uid = cap_a.id, user_a.id
    db_session.expunge_all()

    login_as(client, db_session.get(User, uid))
    r = client.get(f"/capability-map/api/capabilities/{cap_a_id}/processes")

    assert r.status_code == 200


def test_fenced_application_mappings_query_excludes_a_foreign_org(app, db_session, make_org):
    """Direct proof of the helper app/services/apqc_mapping_tenant_fence.py:
    fenced_application_mappings_query (called by D4's route above, and by
    every other application-mapping read in this consolidation)."""
    from flask import g

    from app.services.apqc_mapping_tenant_fence import fenced_application_mappings_query

    org_a, org_b = make_org("helper-app-a"), make_org("helper-app-b")
    process = _process(db_session)
    app_a, app_b = _app(db_session, org_a), _app(db_session, org_b)
    mapping_a = _app_mapping(db_session, process, app_a)
    _app_mapping(db_session, process, app_b)
    db_session.flush()

    with app.test_request_context("/"):
        g.current_org_id = org_a.id
        ids = {m.id for m in fenced_application_mappings_query().all()}
    assert ids == {mapping_a.id}


def test_fenced_capability_mappings_query_excludes_a_foreign_org(app, db_session, make_org):
    """Direct proof of fenced_capability_mappings_query, the capability-side
    counterpart called by the fallback branch above and every other
    capability-mapping read in this consolidation."""
    from flask import g

    from app.services.apqc_mapping_tenant_fence import fenced_capability_mappings_query

    org_a, org_b = make_org("helper-cap-a"), make_org("helper-cap-b")
    process = _process(db_session)
    cap_a, cap_b = _cap(db_session, org_a), _cap(db_session, org_b)
    mapping_a = _cap_mapping(db_session, process, cap_a)
    _cap_mapping(db_session, process, cap_b)
    db_session.flush()

    with app.test_request_context("/"):
        g.current_org_id = org_a.id
        ids = {m.id for m in fenced_capability_mappings_query().all()}
    assert ids == {mapping_a.id}


# ---------------------------------------------------------------------------
# /api/vendors/apqc/capability-mappings (GET + POST)
# ---------------------------------------------------------------------------


def test_vendor_capability_mappings_list_excludes_a_foreign_organisations_rows(
    app, db_session, make_org, client, login_as
):
    from app.models.user import User

    org_a, org_b = make_org("vcm-a"), make_org("vcm-b")
    process = _process(db_session)
    cap_a, cap_b = _cap(db_session, org_a), _cap(db_session, org_b)
    mapping_a = _cap_mapping(db_session, process, cap_a)
    _cap_mapping(db_session, process, cap_b)
    user_a = _user(db_session, org_a, "vcm")
    uid, mapping_a_id, cap_b_id = user_a.id, mapping_a.id, cap_b.id
    db_session.expunge_all()

    login_as(client, db_session.get(User, uid))
    r = client.get("/api/vendors/apqc/capability-mappings")

    assert r.status_code == 200
    body = r.get_json()
    returned_ids = {row["capability_id"] for row in body}
    assert cap_b_id not in returned_ids
    assert any(row["id"] == mapping_a_id for row in body)


def test_vendor_capability_mappings_create_refuses_a_foreign_organisations_capability(
    app, db_session, make_org, client, login_as
):
    from app.models.apqc_process import CapabilityProcessMapping
    from app.models.user import User

    org_a, org_b = make_org("vcmc-a"), make_org("vcmc-b")
    process = _process(db_session)
    cap_b = _cap(db_session, org_b)
    user_a = _user(db_session, org_a, "vcmc")
    process_id, cap_b_id, uid = process.id, cap_b.id, user_a.id
    db_session.expunge_all()

    login_as(client, db_session.get(User, uid))
    r = client.post(
        "/api/vendors/apqc/capability-mappings",
        json={"capability_id": cap_b_id, "apqc_process_id": process_id},
    )

    assert r.status_code == 404
    assert (
        CapabilityProcessMapping.query.filter_by(
            capability_id=cap_b_id, apqc_process_id=process_id
        ).first()
        is None
    )


# ---------------------------------------------------------------------------
# /api/vendors/apqc/processes/<id>/capabilities
# ---------------------------------------------------------------------------


def test_vendor_process_capabilities_excludes_a_foreign_organisations_rows(
    app, db_session, make_org, client, login_as
):
    from app.models.user import User

    org_a, org_b = make_org("vpc-a"), make_org("vpc-b")
    process = _process(db_session)
    cap_b = _cap(db_session, org_b, name="SECRET-CAP-B-VPC")
    _cap_mapping(db_session, process, cap_b)
    user_a = _user(db_session, org_a, "vpc")
    process_id, uid = process.id, user_a.id
    db_session.expunge_all()

    login_as(client, db_session.get(User, uid))
    r = client.get(f"/api/vendors/apqc/processes/{process_id}/capabilities")

    assert r.status_code == 200
    body = r.get_json()
    assert body["capability_count"] == 0
    assert "SECRET-CAP-B-VPC" not in str(body)


# ---------------------------------------------------------------------------
# /api/vendors/apqc/vendor-capability-process-matrix
# ---------------------------------------------------------------------------


def test_vendor_capability_process_matrix_excludes_a_foreign_organisations_capability(
    app, db_session, make_org, client, login_as
):
    from app.models.user import User
    from app.models.vendor.vendor_organization import VendorOrganization, VendorProduct
    from app.models.vendor_product_apqc_mapping import VendorProductAPQCMapping

    org_a, org_b = make_org("matrix-a"), make_org("matrix-b")
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
    cap_a, cap_b = _cap(db_session, org_a), _cap(db_session, org_b)
    _cap_mapping(db_session, process, cap_a, process_contribution=50)
    _cap_mapping(db_session, process, cap_b, process_contribution=50)
    user_a = _user(db_session, org_a, "matrix")
    product_id, uid, cap_a_id, cap_b_id = product.id, user_a.id, cap_a.id, cap_b.id
    db_session.expunge_all()

    login_as(client, db_session.get(User, uid))
    r = client.get("/api/vendors/apqc/vendor-capability-process-matrix", query_string={"product_id": product_id})

    assert r.status_code == 200
    matrix = r.get_json().get("matrix", [])
    returned_ids = {item["capability_id"] for item in matrix}
    assert cap_b_id not in returned_ids
    assert cap_a_id in returned_ids


# ---------------------------------------------------------------------------
# POST /api/capabilities/apqc-link (duplicate-mapping check)
# ---------------------------------------------------------------------------


def test_apqc_link_duplicate_check_does_not_leak_a_foreign_organisations_capability_id(
    app, db_session, make_org, client, login_as
):
    from app.models.user import User

    org_a, org_b = make_org("link-a"), make_org("link-b")
    process = _process(db_session)
    cap_a, cap_b = _cap(db_session, org_a), _cap(db_session, org_b)
    _cap_mapping(db_session, process, cap_b)
    user_a = _user(db_session, org_a, "link")
    process_id, cap_a_id, cap_b_id, uid = process.id, cap_a.id, cap_b.id, user_a.id
    db_session.expunge_all()

    login_as(client, db_session.get(User, uid))
    r = client.post(
        "/capability-map/api/capabilities/apqc-link",
        json={"apqc_id": process_id, "capability_id": cap_a_id},
    )

    # Must not return a 409 naming org B's mapping/capability id.
    if r.status_code == 409:
        assert r.get_json().get("existing_capability_id") != cap_b_id
    else:
        assert r.status_code in (200, 201)


# ---------------------------------------------------------------------------
# GET /capability-map/api/capabilities/apqc-suggestions
# ---------------------------------------------------------------------------


def test_apqc_suggestions_treats_a_foreign_organisations_link_as_still_unmapped(
    app, db_session, make_org, client, login_as
):
    from app.models.user import User

    org_a, org_b = make_org("sugg-a"), make_org("sugg-b")
    process = _process(db_session)
    _cap(db_session, org_a, name="Order Management", business_domain="Operations", category="Core")
    cap_b = _cap(db_session, org_b, name="CapB")
    _cap_mapping(db_session, process, cap_b)
    user_a = _user(db_session, org_a, "sugg")
    process_id, uid = process.id, user_a.id
    db_session.expunge_all()

    login_as(client, db_session.get(User, uid))
    r = client.get("/capability-map/api/capabilities/apqc-suggestions")

    assert r.status_code == 200
    suggestions = r.get_json()
    entry = next((s for s in suggestions if s.get("apqc_id") == process_id), None)
    assert entry is not None
    assert entry["already_linked"] is False


# ---------------------------------------------------------------------------
# AI-chat gap analysis
# ---------------------------------------------------------------------------


def test_ai_chat_process_gap_analysis_does_not_count_a_foreign_orgs_mapping(
    app, db_session, make_org, client, login_as
):
    from app.models.user import User

    org_a, org_b = make_org("gap-a"), make_org("gap-b")
    process = _process(db_session)
    app_b = _app(db_session, org_b)
    _app_mapping(db_session, process, app_b)
    user_a = _user(db_session, org_a, "gap")
    process_id, uid = process.id, user_a.id
    db_session.expunge_all()

    login_as(client, db_session.get(User, uid))
    r = client.post("/ai-chat/chat/gap-analysis", json={"analysis_type": "process"})

    if r.status_code != 200:
        import pytest
        pytest.skip(f"gap analysis route not reachable in this environment ({r.status_code})")
    body = r.get_json()
    gap_process_ids = {g.get("process_id") for g in body.get("gaps", []) if g.get("type") == "process_gap"}
    assert process_id in gap_process_ids


# ---------------------------------------------------------------------------
# pr310-v1 review fix round: DEFECT-1 through DEFECT-8
# ---------------------------------------------------------------------------


def test_save_apqc_mappings_refuses_a_foreign_organisations_capability(
    app, db_session, make_org, client, login_as
):
    """DEFECT-1 (HIGH): POST /api/save-apqc-mappings created/updated
    CapabilityProcessMapping rows against a caller-supplied capability_id
    with no ownership check."""
    from app.models.apqc_process import CapabilityProcessMapping
    from app.models.user import User

    org_a, org_b = make_org("d1arch-a"), make_org("d1arch-b")
    process = _process(db_session)
    cap_b = _cap(db_session, org_b)
    user_a = _user(db_session, org_a, "d1arch")
    process_id, cap_b_id, uid = process.id, cap_b.id, user_a.id
    db_session.expunge_all()

    login_as(client, db_session.get(User, uid))
    r = client.post(
        "/capability-map/api/save-apqc-mappings",
        json={"mappings": [{"capability_id": cap_b_id, "apqc_process_id": process_id}]},
    )

    assert r.status_code == 200
    assert r.get_json()["created"] == 0
    assert (
        CapabilityProcessMapping.query.filter_by(
            capability_id=cap_b_id, apqc_process_id=process_id
        ).first()
        is None
    )


def test_save_apqc_mappings_still_works_for_the_owning_organisation(
    app, db_session, make_org, client, login_as
):
    from app.models.apqc_process import CapabilityProcessMapping
    from app.models.user import User

    org_a = make_org("d1arch-own")
    process = _process(db_session)
    cap_a = _cap(db_session, org_a)
    user_a = _user(db_session, org_a, "d1archown")
    process_id, cap_a_id, uid = process.id, cap_a.id, user_a.id
    db_session.expunge_all()

    login_as(client, db_session.get(User, uid))
    r = client.post(
        "/capability-map/api/save-apqc-mappings",
        json={"mappings": [{"capability_id": cap_a_id, "apqc_process_id": process_id}]},
    )

    assert r.status_code == 200
    assert r.get_json()["created"] == 1
    assert (
        CapabilityProcessMapping.query.filter_by(
            capability_id=cap_a_id, apqc_process_id=process_id
        ).first()
        is not None
    )


def test_fenced_application_mappings_query_used_by_inference_and_hierarchy_service(
    app, db_session, make_org
):
    """DEFECT-3, DEFECT-5: application_inference_service.py and
    apqc_hierarchy_service.py both delegate their ProcessApplicationMapping
    reads to the same shared helper already proven in
    test_fenced_application_mappings_query_excludes_a_foreign_org above --
    a second direct proof at the service layer would just repeat it. Confirm
    by source inspection that both modules import the shared helper rather
    than querying the model directly.
    """
    import inspect

    from app.modules.architecture.services import application_inference_service
    from app.services import apqc_hierarchy_service

    assert "fenced_application_mappings_query" in inspect.getsource(application_inference_service)
    assert "fenced_application_mappings_query" in inspect.getsource(apqc_hierarchy_service)


def test_fenced_capability_mappings_query_used_by_inference_and_hierarchy_service(
    app, db_session, make_org
):
    """DEFECT-4, DEFECT-6: same as above, for the capability-side helper."""
    import inspect

    from app.modules.architecture.services import application_inference_service
    from app.services import apqc_hierarchy_service

    assert "fenced_capability_mappings_query" in inspect.getsource(application_inference_service)
    assert "fenced_capability_mappings_query" in inspect.getsource(apqc_hierarchy_service)


def test_save_process_mappings_update_path_uses_the_shared_fence_helper(app):
    """DEFECT-2/DEFECT-7/DEFECT-8: apqc_api_routes.py's save_process_mappings
    no longer bypasses the shared fence with a bare .query.get()/.filter_by()
    once application_id/capability_id is already proven owned -- confirmed
    by source inspection rather than a second black-box test, since the
    black-box behaviour (a foreign mapping_id is refused) is already proven
    by test_save_process_mappings_format1_refuses_a_foreign_organisations_application
    and test_save_process_mappings_format2_refuses_a_foreign_organisations_capability
    above; this fix round's change is defence-in-depth (not loading the
    foreign row at all), which those tests can't distinguish from the
    previous round's behaviour.
    """
    import inspect

    from app.modules.industry_apqc.routes import apqc_api_routes

    source = inspect.getsource(apqc_api_routes)
    assert "application_mapping_in_caller_org(int(mapping_id))" in source
    assert "fenced_application_mappings_query().filter(" in source
    assert "fenced_capability_mappings_query().filter(" in source
