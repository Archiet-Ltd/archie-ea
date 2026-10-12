"""Delegation tests: each delegated finder really calls the matcher and
preserves its legacy return shape.

Every test in this file fails on plain main (the matcher module does not
exist there) and passes on this branch after the delegation fix.
"""

from __future__ import annotations

import unittest.mock as mock
import uuid


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #

def _element(db_session, org_id, name, type_name="ApplicationComponent", layer="Application"):
    """Create an ArchiMateElement in the given organisation."""
    from app.models.archimate_core import ArchiMateElement
    elem = ArchiMateElement(
        name=name,
        type=type_name,
        layer=layer,
        organization_id=org_id,
    )
    db_session.add(elem)
    db_session.flush()
    return elem


def _vendor(db_session, label):
    """Create a VendorOrganization row with a unique name."""
    from app.models.vendor.vendor_organization import VendorOrganization
    name = f"{label}-{uuid.uuid4().hex[:8]}"
    v = VendorOrganization(name=name)
    db_session.add(v)
    db_session.flush()
    return v


def _user(db_session, org_id):
    """Create a minimal user belonging to the given organisation."""
    from app.models.user import User
    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"delegation-{suffix}@example.com",
        first_name="Delegation",
        last_name="Tester",
        organization_id=org_id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    db_session.add(user)
    db_session.flush()
    return user


# --------------------------------------------------------------------------- #
# 1. vendor_mdm.VendorMDMService.find_duplicates                              #
# --------------------------------------------------------------------------- #

def test_vendor_mdm_find_duplicates_delegates_to_matcher(
    db_session, make_org, tenant_ctx
):
    """vendor_mdm.find_duplicates calls MatcherService.match_by_name and
    returns the legacy pair shape [{"name1","name2","similarity","method"}]."""
    from app.modules.intelligence.services.matcher_service import MatcherService
    from app.modules.vendors.services.vendor_mdm import VendorMDMService

    org = make_org("delegate-mdm")

    with tenant_ctx(org.id):
        # Seed an element in the org that a vendor name can fuzzy-match.
        _element(db_session, org.id, "Acme Corp")
        # Seed a vendor whose name is a near-duplicate of the element name.
        _vendor(db_session, "Acme Corp Services")

        with mock.patch.object(MatcherService, "match_by_name", wraps=MatcherService.match_by_name) as spy:
            service = VendorMDMService()
            result = service.find_duplicates(name_type="vendor", threshold=0.5)

        # The matcher must have been called at least once.
        assert spy.call_count > 0, "matcher was never called"

        # Every call must have been scoped to the acting organisation.
        for call in spy.call_args_list:
            _, kwargs = call
            assert kwargs.get("org_id") == org.id, "matcher called with wrong org_id"

        # The result must be a list of dicts with the legacy keys.
        assert isinstance(result, list)
        for entry in result:
            assert isinstance(entry, dict)
            assert "name1" in entry
            assert "name2" in entry
            assert "similarity" in entry
            assert "method" in entry


# --------------------------------------------------------------------------- #
# 2. UnifiedVendorService.find_duplicates (chain → quality → matcher)         #
# --------------------------------------------------------------------------- #

def test_unified_vendors_find_duplicates_delegates_to_matcher(
    db_session, make_org, tenant_ctx
):
    """UnifiedVendorService.find_duplicates reaches the matcher through the
    quality-service chain and returns the legacy group shape List[List[Dict]]."""
    from app.modules.intelligence.services.matcher_service import MatcherService
    from app.modules.vendors.services.unified_vendors_services import (
        UnifiedVendorService,
    )

    org = make_org("delegate-uvs")

    with tenant_ctx(org.id):
        _element(db_session, org.id, "Acme Corp")
        _vendor(db_session, "Acme Corp Services")

        with mock.patch.object(MatcherService, "match_by_name", wraps=MatcherService.match_by_name) as spy:
            service = UnifiedVendorService()
            result = service.find_duplicates(entity_type="vendor", threshold=0.5)

        assert spy.call_count > 0, "matcher was never called through the chain"

        # Legacy shape: List[List[Dict]].
        assert isinstance(result, list)
        for group in result:
            assert isinstance(group, list)
            for entry in group:
                assert isinstance(entry, dict)


# --------------------------------------------------------------------------- #
# 3. VendorDataQualityService.find_duplicates (leaf)                           #
# --------------------------------------------------------------------------- #

def test_vendor_data_quality_find_duplicates_delegates_to_matcher(
    db_session, make_org, tenant_ctx
):
    """VendorDataQualityService.find_duplicates calls the matcher and returns
    the legacy group shape List[List[Dict]]."""
    from app.modules.intelligence.services.matcher_service import MatcherService
    from app.modules.vendors.services.unified_vendors_services import (
        VendorDataQualityService,
    )

    org = make_org("delegate-quality")

    with tenant_ctx(org.id):
        _element(db_session, org.id, "Acme Corp")
        _vendor(db_session, "Acme Corp Services")

        with mock.patch.object(MatcherService, "match_by_name", wraps=MatcherService.match_by_name) as spy:
            service = VendorDataQualityService()
            result = service.find_duplicates(entity_type="vendor", threshold=0.5)

        assert spy.call_count > 0, "matcher was never called"
        assert isinstance(result, list)
        for group in result:
            assert isinstance(group, list)
            for entry in group:
                assert isinstance(entry, dict)


# --------------------------------------------------------------------------- #
# 4. Two-organisation vendor test                                              #
# --------------------------------------------------------------------------- #

def test_vendor_finder_never_returns_other_orgs_rows(
    db_session, make_org, tenant_ctx
):
    """The vendor finder, when run in org A, never returns or names records
    that belong to org B. The matcher's candidate set is org-scoped."""
    from app.modules.intelligence.services.matcher_service import MatcherService
    from app.modules.vendors.services.vendor_mdm import VendorMDMService

    org_a = make_org("delegate-vendor-a")
    org_b = make_org("delegate-vendor-b")

    with tenant_ctx(org_a.id):
        _element(db_session, org_a.id, "Alpha Corp")
    with tenant_ctx(org_b.id):
        _element(db_session, org_b.id, "Beta Ltd")

    # Global vendor catalogue — both names exist.
    _vendor(db_session, "Alpha Corp Services")
    _vendor(db_session, "Beta Ltd Services")

    # Run as org A.
    with tenant_ctx(org_a.id):
        with mock.patch.object(MatcherService, "match_by_name", wraps=MatcherService.match_by_name) as spy_a:
            result_a = VendorMDMService().find_duplicates(name_type="vendor", threshold=0.5)
        assert spy_a.call_count > 0, "matcher was never called as org A"

    # Assert org A's result never mentions "Beta" (org B's element name).
    for entry in result_a:
        assert "Beta" not in entry.get("name2", ""), "org B's element leaked into A's result"
        assert "Beta" not in entry.get("name1", ""), "org B's vendor leaked into A's result"

    # Run as org B.
    with tenant_ctx(org_b.id):
        with mock.patch.object(MatcherService, "match_by_name", wraps=MatcherService.match_by_name) as spy_b:
            result_b = VendorMDMService().find_duplicates(name_type="vendor", threshold=0.5)
        assert spy_b.call_count > 0, "matcher was never called as org B"

    for entry in result_b:
        assert "Alpha" not in entry.get("name2", ""), "org A's element leaked into B's result"
        assert "Alpha" not in entry.get("name1", ""), "org A's vendor leaked into B's result"


# --------------------------------------------------------------------------- #
# 5. Two-organisation application finder test                                  #
# --------------------------------------------------------------------------- #

def test_application_finder_never_returns_other_orgs_rows(
    app, db_session, client, make_org, tenant_ctx, login_as
):
    """The application duplicate finder, when run in org A, never returns or
    names applications that belong to org B."""
    from app.modules.intelligence.services.matcher_service import MatcherService
    from app.models.application_layer import ApplicationComponent

    org_a = make_org("delegate-app-a")
    org_b = make_org("delegate-app-b")

    with tenant_ctx(org_a.id):
        # Two apps with the same name in org A → duplicate group.
        app_a1 = ApplicationComponent(name="Order API", organization_id=org_a.id)
        app_a2 = ApplicationComponent(name="Order API", organization_id=org_a.id)
        db_session.add_all([app_a1, app_a2])
        db_session.flush()

    with tenant_ctx(org_b.id):
        # One app with the same name in org B (not a duplicate within B).
        app_b = ApplicationComponent(name="Order API", organization_id=org_b.id)
        db_session.add(app_b)
        db_session.flush()

    # Log in as org A's user and run the finder as org A.
    user_a = _user(db_session, org_a.id)
    login_as(client, user_a)

    with mock.patch.object(MatcherService, "match_by_name", wraps=MatcherService.match_by_name) as spy:
        resp = client.get("/dashboard/api/applications/duplicates?min_similarity=40")
        assert resp.status_code < 500, f"route returned {resp.status_code}"
        data = resp.get_json()

    assert spy.call_count > 0, "matcher was never called"
    # The response must contain the legacy envelope keys.
    assert "duplicates" in data
    assert "total_duplicate_groups" in data
    assert "total_duplicate_applications" in data
    assert "estimated_savings" in data
    assert "analyses_count" in data
    assert "message" in data

    # The group must contain only org A's app ids, never org B's.
    for group in data.get("duplicates", []):
        for app_entry in group.get("applications", []):
            assert app_entry["id"] in (app_a1.id, app_a2.id), \
                f"org B's app id {app_entry['id']} leaked into A's result"


# --------------------------------------------------------------------------- #
# 6. Route: unified_vendor_api /duplicates delegates through service           #
# --------------------------------------------------------------------------- #

def test_unified_vendor_api_route_delegates_through_service(
    app, db_session, client, make_org, login_as
):
    """GET /api/vendors/duplicates calls UnifiedVendorService which reaches
    the matcher, and returns the legacy JSON envelope."""
    from app.modules.intelligence.services.matcher_service import MatcherService

    org = make_org("delegate-route-uvs")

    with app.app_context():
        _element(db_session, org.id, "Acme Corp")
        _vendor(db_session, "Acme Corp Services")

        user = _user(db_session, org.id)
        login_as(client, user)

    with mock.patch.object(MatcherService, "match_by_name", wraps=MatcherService.match_by_name) as spy:
        resp = client.get("/api/vendors/duplicates?threshold=0.5")
        assert resp.status_code < 500, f"route returned {resp.status_code}"
        data = resp.get_json()

    assert spy.call_count > 0, "matcher was never called through the route"
    assert "success" in data
    assert "duplicates" in data
    assert "summary" in data
    assert "total_groups" in data["summary"]
    assert "total_pairs" in data["summary"]


# --------------------------------------------------------------------------- #
# 7. Route: vendor_mdm_api /duplicates delegates through service               #
# --------------------------------------------------------------------------- #

def test_vendor_mdm_api_route_delegates_through_service(
    app, db_session, client, make_org, login_as
):
    """GET /api/vendor-mdm/duplicates calls VendorMDMService which reaches
    the matcher, and returns the legacy JSON envelope."""
    from app.modules.intelligence.services.matcher_service import MatcherService

    org = make_org("delegate-route-mdm")

    with app.app_context():
        _element(db_session, org.id, "Acme Corp")
        _vendor(db_session, "Acme Corp Services")

        user = _user(db_session, org.id)
        login_as(client, user)

    with mock.patch.object(MatcherService, "match_by_name", wraps=MatcherService.match_by_name) as spy:
        resp = client.get("/api/vendor-mdm/duplicates?type=vendor&threshold=0.5")
        assert resp.status_code < 500, f"route returned {resp.status_code}"
        data = resp.get_json()

    assert spy.call_count > 0, "matcher was never called through the route"
    assert "success" in data
    assert "data" in data
    assert "summary" in data
    assert "total_groups" in data["summary"]
    assert "total_pairs" in data["summary"]


# --------------------------------------------------------------------------- #
# 8. Route: deprecated application duplicates route delegates to matcher       #
# --------------------------------------------------------------------------- #

def test_find_duplicate_applications_route_delegates_to_matcher(
    app, db_session, client, make_org, login_as
):
    """GET /dashboard/api/applications/duplicates reaches the matcher and
    returns the legacy JSON envelope with duplicate groups."""
    from app.modules.intelligence.services.matcher_service import MatcherService
    from app.models.application_layer import ApplicationComponent

    org = make_org("delegate-app-route")

    with app.app_context():
        app1 = ApplicationComponent(name="Order API", organization_id=org.id)
        app2 = ApplicationComponent(name="Order API", organization_id=org.id)
        db_session.add_all([app1, app2])
        db_session.flush()

        user = _user(db_session, org.id)
        login_as(client, user)

    with mock.patch.object(MatcherService, "match_by_name", wraps=MatcherService.match_by_name) as spy:
        resp = client.get("/dashboard/api/applications/duplicates?min_similarity=40")
        assert resp.status_code < 500, f"route returned {resp.status_code}"
        data = resp.get_json()

    assert spy.call_count > 0, "matcher was never called through the route"
    assert "duplicates" in data
    assert "total_duplicate_groups" in data
    assert "total_duplicate_applications" in data
    assert "estimated_savings" in data
    assert "analyses_count" in data
    assert "message" in data