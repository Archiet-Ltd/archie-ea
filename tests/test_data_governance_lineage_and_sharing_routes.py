"""Route tests for R1-B80's lineage, downstream-impact and sharing-agreement
screens (lineage_view, downstream_impact_view, sharing_agreements,
new_sharing_agreement)."""

from __future__ import annotations

import uuid

from app.models import ArchiMateElement
from app.models.all_missing_models import DataLineage
from app.models.data_sharing_agreement import DataSharingAgreement
from app.models.user import User


def _vendor_org(db_session, name="Route Vendor"):
    from app.models.vendor.vendor_organization import VendorOrganization

    vendor = VendorOrganization(name=f"{name} {uuid.uuid4().hex[:6]}")
    db_session.add(vendor)
    db_session.flush()
    return vendor


def _signed_in(client, db_session, login_as, org):
    """Create and log in an enterprise-architect user for ``org`` — the
    role granted access to the ``data_integration`` nav section that
    ``data_governance_routes._guard`` requires.

    Call this LAST, immediately before the first request: some models
    written here (e.g. ApplicationComponent) have before_insert/flush
    listeners that resolve ``current_user`` outside of a request context.
    Because the ``db_session`` fixture keeps one app context open for the
    whole test, resolving ``current_user`` with no active request caches
    an anonymous user onto that shared ``g`` -- and every later request in
    the test reuses that same ``g`` (see the ``login_as`` fixture's own
    docstring), so a login performed before such a flush is invisible to
    every request that follows. Logging in last avoids the trap instead
    of chasing it per-model.
    """
    user = User(
        first_name="Dg", last_name="Tester",
        email=f"dg-route-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=org.id, confirmed=True, enterprise_role="enterprise_architect",
    )
    db_session.add(user)
    db_session.flush()
    login_as(client, user)
    return user


def test_lineage_view_renders_hops_for_an_element_with_lineage(
    client, db_session, make_org, login_as
):
    org = make_org("dg-lineage-ok")
    a = ArchiMateElement(name="Source", type="ApplicationComponent", layer="application", organization_id=org.id)
    b = ArchiMateElement(name="Target", type="ApplicationComponent", layer="application", organization_id=org.id)
    db_session.add_all([a, b])
    db_session.flush()
    db_session.add(DataLineage(name="flow", archimate_element_id=a.id, target_archimate_element_id=b.id, organization_id=org.id))
    db_session.commit()
    _signed_in(client, db_session, login_as, org)

    resp = client.get(f"/data-governance/lineage/{a.id}")
    assert resp.status_code == 200
    assert b"Source" in resp.data


def test_lineage_view_renders_an_element_with_no_further_lineage_as_a_single_hop(
    client, db_session, make_org, login_as
):
    """multi_hop_lineage always includes the starting element itself as
    depth-0, so a valid element with no edges still renders (just one
    hop) rather than 404ing."""
    org = make_org("dg-lineage-none")
    a = ArchiMateElement(name="Lonely", type="ApplicationComponent", layer="application", organization_id=org.id)
    db_session.add(a)
    db_session.commit()
    _signed_in(client, db_session, login_as, org)

    resp = client.get(f"/data-governance/lineage/{a.id}")
    assert resp.status_code == 200
    assert b"Lonely" in resp.data


def test_lineage_view_404s_for_an_element_that_does_not_exist(client, db_session, make_org, login_as):
    org = make_org("dg-lineage-missing")
    _signed_in(client, db_session, login_as, org)

    resp = client.get("/data-governance/lineage/999999999")
    assert resp.status_code == 404


def test_downstream_impact_ranks_consumers_by_criticality(client, db_session, make_org, login_as):
    org = make_org("dg-impact-ranked")
    from app.models.application_portfolio import ApplicationComponent

    source = ArchiMateElement(name="Source Field", type="DataObject", layer="application", organization_id=org.id)
    low = ArchiMateElement(name="Low Consumer", type="ApplicationComponent", layer="application", organization_id=org.id)
    critical = ArchiMateElement(name="Critical Consumer", type="ApplicationComponent", layer="application", organization_id=org.id)
    db_session.add_all([source, low, critical])
    db_session.flush()
    db_session.add_all([
        DataLineage(name="to-low", archimate_element_id=source.id, target_archimate_element_id=low.id, organization_id=org.id),
        DataLineage(name="to-critical", archimate_element_id=source.id, target_archimate_element_id=critical.id, organization_id=org.id),
    ])
    db_session.add_all([
        ApplicationComponent(name="Low App", archimate_element_id=low.id, organization_id=org.id, business_criticality="Low"),
        ApplicationComponent(name="Critical App", archimate_element_id=critical.id, organization_id=org.id, business_criticality="Critical"),
    ])
    db_session.commit()
    _signed_in(client, db_session, login_as, org)

    resp = client.get(f"/data-governance/impact/{source.id}")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert body.index("Critical Consumer") < body.index("Low Consumer")


def test_downstream_impact_404s_for_an_element_that_does_not_exist(client, db_session, make_org, login_as):
    org = make_org("dg-impact-missing")
    _signed_in(client, db_session, login_as, org)

    resp = client.get("/data-governance/impact/999999999")
    assert resp.status_code == 404


def test_sharing_agreements_lists_only_this_organizations_agreements(
    client, db_session, make_org, login_as
):
    org = make_org("dg-sharing-list")
    other_org = make_org("dg-sharing-other")
    vendor = _vendor_org(db_session)
    mine = DataSharingAgreement(
        name="Mine", vendor_organization_id=vendor.id, organization_id=org.id, status="active"
    )
    theirs = DataSharingAgreement(
        name="Theirs", vendor_organization_id=vendor.id, organization_id=other_org.id, status="active"
    )
    db_session.add_all([mine, theirs])
    db_session.commit()
    _signed_in(client, db_session, login_as, org)

    resp = client.get("/data-governance/sharing-agreements")
    assert resp.status_code == 200
    assert b"Mine" in resp.data
    assert b"Theirs" not in resp.data


def test_new_sharing_agreement_get_renders_form(client, db_session, make_org, login_as):
    org = make_org("dg-sharing-new-get")
    _vendor_org(db_session)
    _signed_in(client, db_session, login_as, org)

    resp = client.get("/data-governance/sharing-agreements/new")
    assert resp.status_code == 200
    assert b"New agreement" in resp.data or b"Register agreement" in resp.data


def test_new_sharing_agreement_post_creates_and_redirects(client, db_session, make_org, login_as):
    org = make_org("dg-sharing-new-post")
    vendor = _vendor_org(db_session)
    _signed_in(client, db_session, login_as, org)

    resp = client.post(
        "/data-governance/sharing-agreements/new",
        data={"name": "Fresh Agreement", "vendor_organization_id": str(vendor.id)},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Fresh Agreement" in resp.data

    saved = DataSharingAgreement.query.filter_by(organization_id=org.id).one()
    assert saved.name == "Fresh Agreement"
    assert saved.vendor_organization_id == vendor.id


def test_new_sharing_agreement_post_without_name_flashes_and_redirects_back(
    client, db_session, make_org, login_as
):
    org = make_org("dg-sharing-new-invalid")
    vendor = _vendor_org(db_session)
    _signed_in(client, db_session, login_as, org)

    resp = client.post(
        "/data-governance/sharing-agreements/new",
        data={"name": "", "vendor_organization_id": str(vendor.id)},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert DataSharingAgreement.query.filter_by(organization_id=org.id).count() == 0
