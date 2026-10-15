"""The /api/v1/mappings routes serve and change only the caller's organisation's mappings.

The three mapping tables behind these routes carry no organisation column. Every by-id
handler loaded the row with ``Model.query.get(mapping_id)`` so any signed-in user could read,
update or delete another organisation's mapping by walking integer ids, and the list and
summary routes counted every organisation's rows. The owning organisation is the one that owns
the application (application mappings) or the business capability (vendor strategy mappings).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text


def _user(db_session, org):
    from app.models.user import User

    user = User(email=f"mp-{uuid.uuid4().hex[:8]}@example.test", first_name="M", last_name="P",
                organization_id=org.id, confirmed=True)
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    return user


def _world(db_session, make_org):
    from app.models.application_portfolio import ApplicationComponent
    from app.models.capability_to_vendor_mapping import (
        ApplicationVendorProductMapping,
        UnifiedCapabilityApplicationMapping,
        UnifiedCapabilityVendorOrganizationMapping,
    )
    from app.models.unified_capability import UnifiedCapability
    from app.models.vendor.vendor_organization import VendorOrganization, VendorProduct

    org_a, org_b = make_org("map-a"), make_org("map-b")
    vendor = VendorOrganization(name=f"V {uuid.uuid4().hex[:6]}")
    db_session.add(vendor)
    db_session.flush()
    product = VendorProduct(name=f"P {uuid.uuid4().hex[:6]}", vendor_organization_id=vendor.id)
    db_session.add(product)
    rows = {}
    for tag, org in (("a", org_a), ("b", org_b)):
        comp = ApplicationComponent(name=f"App {tag} {uuid.uuid4().hex[:6]}", organization_id=org.id)
        cap = UnifiedCapability(name=f"Cap {tag} {uuid.uuid4().hex[:6]}", organization_id=org.id)
        db_session.add_all([comp, cap])
        db_session.flush()
        m_app = UnifiedCapabilityApplicationMapping(
            unified_capability_id=cap.id, application_component_id=comp.id, coverage_percentage=10.0)
        m_vendor = UnifiedCapabilityVendorOrganizationMapping(
            unified_capability_id=cap.id, vendor_organization_id=vendor.id, annual_spend=100)
        m_prod = ApplicationVendorProductMapping(
            application_component_id=comp.id, vendor_product_id=product.id, deployment_model="cloud")
        db_session.add_all([m_app, m_vendor, m_prod])
        db_session.flush()
        rows[tag] = dict(comp=comp.id, cap=cap.id, app=m_app.id, vendor=m_vendor.id, prod=m_prod.id)
    user_a, user_b = _user(db_session, org_a), _user(db_session, org_b)
    db_session.commit()
    ids = dict(rows=rows, user_a=user_a.id, user_b=user_b.id, product=product.id, vendor=vendor.id)
    db_session.expunge_all()
    return ids


def _login(client, login_as, db_session, user_id):
    from app.models.user import User

    login_as(client, db_session.get(User, user_id))


# kind -> (url segment, column proving a change, payload, original)
CASES = {
    "app": ("unified-to-application", "unified_capability_application_mappings",
            "coverage_percentage", {"coverage_percentage": 99.0}, 10.0),
    "vendor": ("unified-to-vendor-org", "unified_capability_vendor_organization_mappings",
               "annual_spend", {"annual_spend": 999999}, 100),
    "prod": ("application-to-vendor", "application_vendor_product_mappings",
             "deployment_model", {"deployment_model": "onprem"}, "cloud"),
}


def _table(db_session, kind):
    from app.models import capability_to_vendor_mapping as m

    cls = {"app": m.UnifiedCapabilityApplicationMapping,
           "vendor": m.UnifiedCapabilityVendorOrganizationMapping,
           "prod": m.ApplicationVendorProductMapping}[kind]
    return cls.__tablename__


@pytest.mark.parametrize("kind", sorted(CASES))
def test_another_organisations_mapping_is_not_updated(app, db_session, make_org, client, login_as, kind):
    w = _world(db_session, make_org)
    seg, _t, column, payload, original = CASES[kind]
    table = _table(db_session, kind)
    foreign = w["rows"]["b"][kind]
    _login(client, login_as, db_session, w["user_a"])
    resp = client.put(f"/api/v1/mappings/{seg}/{foreign}", json=payload)
    assert resp.status_code == 404, resp.get_json()
    stored = db_session.execute(text(f"select {column} from {table} where id = :i"), {"i": foreign}).scalar()
    assert float(stored) == float(original) if kind != "prod" else stored == original


@pytest.mark.parametrize("kind", sorted(CASES))
def test_another_organisations_mapping_is_not_read_or_deleted(app, db_session, make_org, client, login_as, kind):
    w = _world(db_session, make_org)
    seg, _t, *_ = CASES[kind]
    table = _table(db_session, kind)
    foreign = w["rows"]["b"][kind]
    _login(client, login_as, db_session, w["user_a"])
    assert client.get(f"/api/v1/mappings/{seg}/{foreign}").status_code == 404
    assert client.delete(f"/api/v1/mappings/{seg}/{foreign}").status_code == 404
    assert db_session.execute(text(f"select count(*) from {table} where id = :i"), {"i": foreign}).scalar() == 1


def test_the_summary_counts_only_the_callers_mappings(app, db_session, make_org, client, login_as):
    from app.models.capability_to_vendor_mapping import (
        ApplicationVendorProductMapping,
        UnifiedCapabilityApplicationMapping,
        UnifiedCapabilityVendorOrganizationMapping,
    )

    w = _world(db_session, make_org)
    _login(client, login_as, db_session, w["user_a"])
    summary = client.get("/api/v1/mappings/summary").get_json()["data"]["summary"]
    for key, model in (("unified_to_application", UnifiedCapabilityApplicationMapping),
                       ("unified_to_vendor_org", UnifiedCapabilityVendorOrganizationMapping),
                       ("application_to_vendor", ApplicationVendorProductMapping)):
        total = db_session.execute(text(f"select count(*) from {model.__tablename__}")).scalar()
        assert total >= 2
        assert summary[key] == 1, (key, summary)


def test_the_owner_can_still_update_and_delete(app, db_session, make_org, client, login_as):
    w = _world(db_session, make_org)
    _login(client, login_as, db_session, w["user_a"])
    for kind, (seg, _t, column, payload, original) in CASES.items():
        own = w["rows"]["a"][kind]
        assert client.put(f"/api/v1/mappings/{seg}/{own}", json=payload).status_code == 200
    assert client.delete(f"/api/v1/mappings/unified-to-application/{w['rows']['a']['app']}").status_code == 200


def test_a_mapping_cannot_be_created_against_another_organisations_application(
    app, db_session, make_org, client, login_as
):
    w = _world(db_session, make_org)
    _login(client, login_as, db_session, w["user_a"])
    resp = client.post("/api/v1/mappings/application-to-vendor", json={
        "application_id": w["rows"]["b"]["comp"], "vendor_product_id": w["product"] + 0})
    # 409 would tell the caller the foreign application already has this mapping.
    assert resp.status_code == 404, resp.get_json()
    resp = client.post("/api/v1/mappings/unified-to-application", json={
        "unified_capability_id": w["rows"]["a"]["cap"], "application_id": w["rows"]["b"]["comp"]})
    assert resp.status_code == 404, resp.get_json()


def _reference_capability(db_session):
    from app.models.unified_capability import UnifiedCapability

    cap = UnifiedCapability(name=f"Ref {uuid.uuid4().hex[:8]}", scope="reference", organization_id=None)
    db_session.add(cap)
    db_session.commit()
    cap_id = cap.id
    db_session.expunge_all()
    return cap_id


def test_a_shared_reference_capability_can_be_mapped_to_the_callers_own_application(
    app, db_session, make_org, client, login_as
):
    from app.models.capability_to_vendor_mapping import UnifiedCapabilityApplicationMapping

    w = _world(db_session, make_org)
    ref = _reference_capability(db_session)
    _login(client, login_as, db_session, w["user_a"])
    resp = client.post("/api/v1/mappings/unified-to-application", json={
        "unified_capability_id": ref, "application_id": w["rows"]["a"]["comp"]})
    assert resp.status_code == 201, resp.get_json()
    created = resp.get_json()["data"]["id"]
    row = db_session.get(UnifiedCapabilityApplicationMapping, created)
    assert row.application_component_id == w["rows"]["a"]["comp"]
    assert row.unified_capability_id == ref
    # a foreign application is still refused, and nothing is written for it
    before = db_session.execute(text(
        "select count(*) from unified_capability_application_mappings")).scalar()
    resp = client.post("/api/v1/mappings/unified-to-application", json={
        "unified_capability_id": ref, "application_id": w["rows"]["b"]["comp"]})
    assert resp.status_code == 404, resp.get_json()
    assert db_session.execute(text(
        "select count(*) from unified_capability_application_mappings")).scalar() == before


def test_a_shared_reference_capability_is_refused_for_vendor_organisation_mappings(
    app, db_session, make_org, client, login_as
):
    w = _world(db_session, make_org)
    ref = _reference_capability(db_session)
    _login(client, login_as, db_session, w["user_a"])
    resp = client.post("/api/v1/mappings/unified-to-vendor-org", json={
        "unified_capability_id": ref, "vendor_organization_id": w["vendor"]})
    assert resp.status_code == 404, resp.get_json()


def test_another_organisations_capability_is_refused_for_an_application_mapping(
    app, db_session, make_org, client, login_as
):
    w = _world(db_session, make_org)
    _login(client, login_as, db_session, w["user_a"])
    resp = client.post("/api/v1/mappings/unified-to-application", json={
        "unified_capability_id": w["rows"]["b"]["cap"], "application_id": w["rows"]["a"]["comp"]})
    assert resp.status_code == 404, resp.get_json()


def test_ids_that_are_integer_like_strings_work_and_booleans_are_refused(
    app, db_session, make_org, client, login_as
):
    w = _world(db_session, make_org)
    ref = _reference_capability(db_session)
    _login(client, login_as, db_session, w["user_a"])
    own_app = w["rows"]["a"]["comp"]
    # True == 1 in Python: it must never resolve to the row with id 1
    for bad in (True, False, 1.5, "", " ", "abc", "1; drop", -1, 0, 10**12, None, [1]):
        resp = client.post("/api/v1/mappings/unified-to-application", json={
            "unified_capability_id": w["rows"]["a"]["cap"], "application_id": bad})
        assert resp.status_code == 404, (bad, resp.status_code, resp.get_json())
        resp = client.post("/api/v1/mappings/application-to-vendor", json={
            "application_id": bad, "vendor_product_id": w["product"]})
        assert resp.status_code == 404, (bad, resp.status_code, resp.get_json())
    resp = client.post("/api/v1/mappings/unified-to-application", json={
        "unified_capability_id": str(ref), "application_id": f" {own_app} "})
    assert resp.status_code == 201, resp.get_json()
    # the existing own mapping is a duplicate (not a not-found) for the owner
    resp = client.post("/api/v1/mappings/application-to-vendor", json={
        "application_id": str(own_app), "vendor_product_id": w["product"]})
    assert resp.status_code != 404, resp.get_json()


def test_a_mapping_is_refused_without_an_active_organisation(app, db_session, make_org, client, login_as):
    w = _world(db_session, make_org)
    _login(client, login_as, db_session, w["user_a"])
    from app.api.v1 import mappings

    with app.test_request_context():
        from unittest import mock

        with mock.patch("app.utils.tenant.current_organization_id", return_value=None):
            assert mappings._owns_application(w["rows"]["a"]["comp"]) is False
            assert mappings._owns_capability(w["rows"]["a"]["cap"]) is False
            assert mappings._sees_capability(w["rows"]["a"]["cap"]) is False
