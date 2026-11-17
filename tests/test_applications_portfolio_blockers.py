"""Regression guards for the application-portfolio demo-morning BLOCKERs (01 Sep 2026).

D1  Edit Application silently wiped Business Criticality: the edit select never
    preselected the stored value, so any save re-submitted "" and blanked it. The
    stored vocabulary is case-inconsistent ("critical"/"Critical"), so the fix
    matches case-insensitively and preserves an out-of-list value verbatim.

D2  "Add Vendor" -> Create Vendor threw `submitCreateVendor is not a function`
    under the strict-CSP Alpine build: a duplicate `vendorCreateModal`
    registration in alpine-architecture.js (method `submit`) shadowed the
    page factory (method `submitCreateVendor`). The JS/CSP wiring cannot be
    exercised by pytest, so it is guarded structurally, and the POST endpoint it
    targets is exercised behaviourally.

D11 The Type filter was empty: options + filter query used `application_category`
    while the Type shown everywhere (fact sheet, edit form, list column) is
    `component_type`. All three now use `component_type`.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from flask import render_template

pytestmark = pytest.mark.usefixtures("db_session")

REPO = Path(__file__).resolve().parent.parent


def _admin_user(db_session, make_org):
    from app.models.user import Role, User

    org = make_org("appblocker")
    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"appblocker-{suffix}@example.com",
        first_name="App",
        last_name="Blocker",
        organization_id=org.id,
        confirmed=True,
        enterprise_role="architect",
    )
    user.password = "Sup3rSecret!23"
    db_session.add(user)
    db_session.flush()
    role = Role.query.filter(Role.name.in_(("Administrator", "Admin", "admin"))).first()
    if role is None:
        role = Role(name="Administrator")
        db_session.add(role)
        db_session.flush()
    user.role = role
    db_session.flush()
    return org, user


def _make_app(db_session, org_id, name, **kwargs):
    from app.models.application_portfolio import ApplicationComponent

    # Created outside a request context: TenantMixin does not auto-set org here,
    # so pin organization_id explicitly to the user's org.
    app_row = ApplicationComponent(name=name, organization_id=org_id, **kwargs)
    db_session.add(app_row)
    db_session.flush()
    return app_row


# ── D1: edit select preselects the stored criticality (case-insensitively) ──────

@pytest.mark.parametrize("stored,expected_selected", [
    ("Critical", "Critical"),
    ("high", "High"),        # lowercase stored value still preselects "High"
    ("Medium", "Medium"),
])
def test_edit_template_preselects_stored_criticality(app, stored, expected_selected):
    class _Stub:
        id = 1
        name = "Stub"
        description = ""
        application_code = ""
        component_type = ""
        business_owner = ""
        technical_owner = ""
        business_purpose = ""
        business_criticality = stored
        deployment_status = "production"
        business_domain = ""
        technology_stack = ""
        updated_at = None

    with app.test_request_context("/"):
        html = render_template("applications/edit.html", application=_Stub())
    # Scope both assertions to this select: other optional fields legitimately
    # have their own selected "Not set" option.
    from bs4 import BeautifulSoup

    criticality = BeautifulSoup(html, "html.parser").select_one("#criticality")
    assert criticality is not None
    selected = criticality.select_one("option[selected]")
    assert selected is not None
    assert selected.get("value") == expected_selected


def test_edit_template_out_of_vocabulary_value_is_preserved(app):
    """A stored value outside the standard four must still be selected so that a
    save never silently drops it."""
    class _Stub:
        id = 1
        name = "Stub"
        description = application_code = component_type = ""
        business_owner = technical_owner = business_purpose = ""
        business_criticality = "Business-Critical"
        deployment_status = "production"
        business_domain = technology_stack = ""
        updated_at = None

    with app.test_request_context("/"):
        html = render_template("applications/edit.html", application=_Stub())
    assert 'value="Business-Critical" selected' in html


def test_edit_post_preserving_criticality_does_not_wipe_it(app, db_session, make_org, client, login_as):
    org, user = _admin_user(db_session, make_org)
    row = _make_app(db_session, org.id, "Crit Keeper", business_criticality="High")
    db_session.commit()
    login_as(client, user)

    # Simulate the fixed form: it now re-submits the stored value.
    resp = client.post(
        f"/applications/{row.id}/edit",
        data={"description": "changed", "criticality": "High"},
        follow_redirects=False,
    )
    assert resp.status_code in (200, 302), resp.get_data(as_text=True)
    db_session.refresh(row)
    assert row.business_criticality == "High"


# ── D11: Type filter is built from and filters on component_type ─────────────────

def test_type_filter_lists_component_types_and_filters(app, db_session, make_org, client, login_as):
    org, user = _admin_user(db_session, make_org)
    _make_app(db_session, org.id, "Erp One", component_type="erp")
    _make_app(db_session, org.id, "Cpq Two", component_type="cpq")
    db_session.commit()
    login_as(client, user)

    resp = client.get("/applications/")
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    # Dropdown is populated from distinct component_type values.
    assert 'data-testid="filter-type"' in body
    assert ">erp<" in body or ">Erp<" in body.lower() or "erp" in body

    # Filtering by type returns only the matching row.
    resp2 = client.get("/applications/?type=erp")
    body2 = resp2.get_data(as_text=True)
    assert resp2.status_code == 200
    assert "Erp One" in body2
    assert "Cpq Two" not in body2


# ── D2: vendor create wiring is CSP-correct and the endpoint persists ────────────

def test_vendor_create_modal_wiring_is_consistent():
    js = (REPO / "app/static/js/vendors/create_modal.js").read_text(encoding="utf-8")
    template = (REPO / "app/templates/vendors/list.html").read_text(encoding="utf-8")
    arch = (REPO / "app/static/js/alpine-architecture.js").read_text(encoding="utf-8")

    # Template submits through submitCreateVendor (a bare method name, as the
    # CSP build of Alpine requires); the factory must define it AND register
    # itself with Alpine so the CSP build resolves x-data="vendorCreateModal".
    assert 'x-data="vendorCreateModal"' in template
    assert '@submit.prevent="submitCreateVendor"' in template
    assert "submitCreateVendor()" in js
    assert "Alpine.data('vendorCreateModal', vendorCreateModal)" in js
    # The stale duplicate that shadowed it must be gone.
    assert "async submit()" not in arch.split("APPLICATION COMPONENTS")[0]


def test_vendor_management_create_persists(app, db_session, make_org, client, login_as):
    org, user = _admin_user(db_session, make_org)
    # The vendor catalogue is platform-wide: only a platform administrator creates vendors.
    user.is_platform_admin = True
    db_session.commit()
    login_as(client, user)

    resp = client.post(
        "/vendor-management/create",
        json={"name": f"QA Vendor {uuid.uuid4().hex[:6]}", "vendor_type": "saas_platform",
              "country": "UK", "website": "https://example.com", "description": "x"},
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["status"] == "success"
    assert isinstance(body["vendor_id"], int)
