"""Regression guards for HTTP 500s on /api/hybrid-mapping/statistics and /api/hybrid-mapping/export.

Both routes returned 500 for every persona in GitHub CI smoke probes.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


@pytest.fixture
def org(make_org):
    return make_org("hybrid500")


@pytest.fixture
def logged_in_client(app, db_session, org, login_as):
    """A test client authenticated as an administrator in a fresh org."""
    from app.models.user import Permission, Role, User

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        role = Role(name="Administrator", permissions=Permission.ADMINISTER)
        db_session.add(role)
        db_session.flush()

    user = User(
        email=f"hybrid500-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Hybrid",
        last_name="Mapping",
        organization_id=org.id,
        role=role,
        confirmed=True,
    )
    db_session.add(user)
    db_session.flush()

    client = app.test_client()
    login_as(client, user)
    return client


def test_statistics_returns_200(logged_in_client):
    """GET /api/hybrid-mapping/statistics must return 200 with JSON."""
    resp = logged_in_client.get("/api/hybrid-mapping/statistics")
    assert resp.status_code == 200, f"Got {resp.status_code}: {resp.get_data(as_text=True)}"
    data = resp.get_json()
    assert isinstance(data, dict)
    assert "total_capabilities" in data


def test_statistics_hostile_pagination_returns_200_or_400(logged_in_client):
    """?page=-1 must not 500."""
    resp = logged_in_client.get("/api/hybrid-mapping/statistics?page=-1")
    assert resp.status_code in (200, 400), f"Got {resp.status_code}: {resp.get_data(as_text=True)}"


def test_statistics_hostile_pagination_abc_returns_200_or_400(logged_in_client):
    """?page=abc must not 500."""
    resp = logged_in_client.get("/api/hybrid-mapping/statistics?page=abc")
    assert resp.status_code in (200, 400), f"Got {resp.status_code}: {resp.get_data(as_text=True)}"


def test_export_returns_200(logged_in_client):
    """GET /api/hybrid-mapping/export must return 200 with JSON."""
    resp = logged_in_client.get("/api/hybrid-mapping/export")
    assert resp.status_code == 200, f"Got {resp.status_code}: {resp.get_data(as_text=True)}"
    data = resp.get_json()
    assert isinstance(data, dict)
    assert "statistics" in data


def test_export_hostile_pagination_returns_200_or_400(logged_in_client):
    """?page=-1 must not 500."""
    resp = logged_in_client.get("/api/hybrid-mapping/export?page=-1")
    assert resp.status_code in (200, 400), f"Got {resp.status_code}: {resp.get_data(as_text=True)}"


# ── tenant isolation ──────────────────────────────────────────────────────


@pytest.fixture
def two_orgs_with_b_data(db_session, make_org, app):
    """Create org A (empty) and org B (populated with every mapping kind).

    Returns (org_a, org_b, b_cap, b_app, b_element, b_vendor, b_product) so
    the test can assert that org A sees none of org B's data.
    """
    from app.models.archimate_core import ArchiMateElement
    from app.models.application_portfolio import ApplicationComponent
    from app.models.capability_to_vendor_mapping import CapabilityVendorProductMapping
    from app.models.unified_application_capability_mapping import (
        UnifiedApplicationCapabilityMapping,
    )
    from app.models.unified_capability import UnifiedCapability
    from app.models.vendor.vendor_organization import VendorOrganization, VendorProduct

    org_a = make_org("iso-a")
    org_b = make_org("iso-b")
    uniq = uuid.uuid4().hex[:8]

    # --- org B: ArchiMate element (created first so b_cap can reference it) ---
    b_element = ArchiMateElement(
        name=f"B Element {uniq}",
        type="ApplicationComponent",
        layer="Application",
        organization_id=org_b.id,
    )
    db_session.add(b_element)
    db_session.flush()

    # --- org B: capability (with archimate_element_id set) ---
    b_cap = UnifiedCapability(
        name=f"B Capability {uniq}",
        code=f"B-CAP-{uniq}",
        level=1,
        organization_id=org_b.id,
        archimate_element_id=b_element.id,
    )
    db_session.add(b_cap)
    db_session.flush()

    # --- org B: application ---
    b_app = ApplicationComponent(
        name=f"B Application {uniq}",
        organization_id=org_b.id,
    )
    db_session.add(b_app)
    db_session.flush()

    # --- org B: vendor + product (global tables) ---
    b_vendor = VendorOrganization(name=f"B Vendor {uniq}")
    db_session.add(b_vendor)
    db_session.flush()

    b_product = VendorProduct(
        name=f"B Product {uniq}",
        vendor_organization_id=b_vendor.id,
    )
    db_session.add(b_product)
    db_session.flush()

    # --- org B: application-capability mapping ---
    b_app_map = UnifiedApplicationCapabilityMapping(
        unified_capability_id=b_cap.id,
        application_component_id=b_app.id,
        relationship_strength=5,
        coverage_percentage=80,
    )
    db_session.add(b_app_map)
    db_session.flush()

    # --- org B: product-capability mapping ---
    b_prod_map = CapabilityVendorProductMapping(
        unified_capability_id=b_cap.id,
        vendor_product_id=b_product.id,
        mapping_strength=5,
        coverage_percentage=80,
    )
    db_session.add(b_prod_map)
    db_session.flush()

    return org_a, org_b, b_cap, b_app, b_element, b_vendor, b_product


@pytest.fixture
def client_a(app, db_session, two_orgs_with_b_data, login_as):
    """A test client authenticated as org A (which has no data of its own)."""
    from app.models.user import Permission, Role, User

    org_a = two_orgs_with_b_data[0]

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        role = Role(name="Administrator", permissions=Permission.ADMINISTER)
        db_session.add(role)
        db_session.flush()

    user = User(
        email=f"iso-a-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Iso",
        last_name="A",
        organization_id=org_a.id,
        role=role,
        confirmed=True,
    )
    db_session.add(user)
    db_session.flush()

    client = app.test_client()
    login_as(client, user)
    return client


def test_statistics_excludes_org_b_counts(client_a, two_orgs_with_b_data):
    """Org A's statistics must not include org B's capabilities or mappings."""
    _org_a, _org_b, b_cap, _b_app, _b_element, _b_vendor, _b_product = two_orgs_with_b_data

    resp = client_a.get("/api/hybrid-mapping/statistics")
    assert resp.status_code == 200, f"Got {resp.status_code}: {resp.get_data(as_text=True)}"
    data = resp.get_json()

    # Org A has no capabilities of its own, so every count must be zero.
    assert data["total_capabilities"] == 0, (
        f"org A total_capabilities should be 0, got {data['total_capabilities']}"
    )
    assert data["application_centric"]["capabilities_with_apps"] == 0
    assert data["product_centric"]["capabilities_with_products"] == 0
    assert data["direct_archimate"]["capabilities_with_archimate"] == 0
    assert data["multi_path"]["capabilities_with_multi_path"] == 0
    assert data["quality_metrics"]["total_mappings"] == 0
    assert data["quality_metrics"]["high_quality_mappings"] == 0


def test_export_excludes_org_b_data(client_a, two_orgs_with_b_data):
    """Org A's export must not contain org B's capability names or mapping rows."""
    _org_a, _org_b, b_cap, b_app, b_element, b_vendor, b_product = two_orgs_with_b_data

    resp = client_a.get("/api/hybrid-mapping/export")
    assert resp.status_code == 200, f"Got {resp.status_code}: {resp.get_data(as_text=True)}"
    data = resp.get_json()

    # Statistics sub-document — same assertions as the statistics endpoint.
    stats = data["statistics"]
    assert stats["total_capabilities"] == 0

    # Application mappings must not mention org B's application or capability.
    app_names = {m["application_name"] for m in data["application_mappings"]}
    assert b_app.name not in app_names, (
        f"org A export leaked org B application name {b_app.name!r}"
    )
    cap_names_in_app = {m["capability_name"] for m in data["application_mappings"]}
    assert b_cap.name not in cap_names_in_app, (
        f"org A export leaked org B capability name {b_cap.name!r}"
    )

    # Product mappings must not mention org B's product or capability.
    prod_names = {m["product_name"] for m in data["product_mappings"]}
    assert b_product.name not in prod_names, (
        f"org A export leaked org B product name {b_product.name!r}"
    )
    cap_names_in_prod = {m["capability_name"] for m in data["product_mappings"]}
    assert b_cap.name not in cap_names_in_prod, (
        f"org A export leaked org B capability name {b_cap.name!r}"
    )

    # ArchiMate mappings must not mention org B's element or capability.
    arch_names = {m["archimate_element_name"] for m in data["archimate_mappings"]}
    assert b_element.name not in arch_names, (
        f"org A export leaked org B element name {b_element.name!r}"
    )
    cap_names_in_arch = {m["capability_name"] for m in data["archimate_mappings"]}
    assert b_cap.name not in cap_names_in_arch, (
        f"org A export leaked org B capability name {b_cap.name!r}"
    )

    # Unmapped capabilities must not mention org B's capability.
    unmapped_cap_names = {c["name"] for c in data["unmapped_capabilities"]}
    assert b_cap.name not in unmapped_cap_names, (
        f"org A unmapped capabilities leaked org B name {b_cap.name!r}"
    )

    # Unmapped vendor products must not mention org B's product.
    unmapped_prod_names = {p["name"] for p in data["unmapped_vendor_products"]}
    assert b_product.name not in unmapped_prod_names, (
        f"org A unmapped products leaked org B name {b_product.name!r}"
    )

    # Unmapped ArchiMate elements must not mention org B's element.
    unmapped_arch_names = {e["name"] for e in data["unmapped_archimate_elements"]}
    assert b_element.name not in unmapped_arch_names, (
        f"org A unmapped elements leaked org B name {b_element.name!r}"
    )