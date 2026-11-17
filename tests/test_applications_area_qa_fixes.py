"""Regression tests for the Applications-area QA findings (Sep 2026 Fortune-500 walkthrough).

Covers the behavioural fixes for:

* the bulk "Lifecycle" action actually updates ``lifecycle_status``.
* bulk Export respects the selected ``ids`` instead of exporting the whole portfolio.
* the Data Enrichment table round-trips cost/owner/criticality (save then reload).
* the vendor register lists only vendors the organisation names, with no hard-coded type list.

Written against the shared fixtures in ``tests/conftest.py`` (``db_session`` rolls the whole
test back), per the project convention. The frozen pagination getters in
``static/js/components/data_table.js`` and the count-label wording in
``list_simple.html`` are front-end-only changes with no Python surface to assert here.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _make_user(db_session, org_id):
    from app.models.user import User

    user = User(
        email=f"apps-qa-{uuid.uuid4().hex[:10]}@example.test",
        organization_id=org_id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    db_session.add(user)
    db_session.flush()
    return user


def _make_app(db_session, org_id, name, **kwargs):
    from app.models.application_portfolio import ApplicationComponent

    row = ApplicationComponent(name=name, organization_id=org_id, **kwargs)
    db_session.add(row)
    db_session.flush()
    return row


# ─────────────────────────────────────────────────────────────── lifecycle


def test_bulk_lifecycle_updates_selected_apps(db_session, make_org, client, login_as):
    org = make_org("lifecycle")
    user = _make_user(db_session, org.id)
    a = _make_app(db_session, org.id, "Lifecycle App A", lifecycle_status="2.2 tactical")
    b = _make_app(db_session, org.id, "Lifecycle App B", lifecycle_status="2.2 tactical")

    login_as(client, user)
    resp = client.post(
        "/applications/api/bulk-lifecycle",
        json={"ids": [a.id, b.id], "lifecycle_stage": "3. sunset"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    payload = resp.get_json()
    assert payload["success"] is True
    assert payload["updated_count"] == 2

    db_session.expire_all()
    assert a.lifecycle_status == "3. sunset"
    assert b.lifecycle_status == "3. sunset"


def test_bulk_lifecycle_rejects_invalid_stage(db_session, make_org, client, login_as):
    org = make_org("d5b")
    user = _make_user(db_session, org.id)
    a = _make_app(db_session, org.id, "Lifecycle App C", lifecycle_status="2.2 tactical")

    login_as(client, user)
    resp = client.post(
        "/applications/api/bulk-lifecycle",
        json={"ids": [a.id], "lifecycle_stage": "not-a-real-stage"},
    )
    assert resp.status_code == 400
    db_session.expire_all()
    assert a.lifecycle_status == "2.2 tactical"  # unchanged


# ───────────────────────────────────────────────────────────────── export


def test_export_csv_respects_selection(db_session, make_org, client, login_as):
    org = make_org("export")
    user = _make_user(db_session, org.id)
    keep = _make_app(db_session, org.id, "Export Keep Me")
    _make_app(db_session, org.id, "Export Exclude Me")

    login_as(client, user)
    resp = client.get(f"/applications/export/csv?ids={keep.id}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Export Keep Me" in body
    assert "Export Exclude Me" not in body


# ──────────────────────────────────────────────────────────────── enrich


def test_enrich_roundtrips_to_candidates(db_session, make_org, client, login_as):
    """Save cost/owner/criticality, then confirm the reload endpoint returns them."""
    org = make_org("enrich")
    user = _make_user(db_session, org.id)
    app_row = _make_app(db_session, org.id, "Enrich Target")

    login_as(client, user)
    save = client.post(
        f"/applications/rationalization/api/enrich/{app_row.id}",
        json={
            "total_cost_of_ownership": 123456,
            "application_owner": "Enterprise Architecture",
            "business_criticality": "High",
        },
    )
    assert save.status_code == 200, save.get_data(as_text=True)
    assert save.get_json()["success"] is True

    login_as(client, user)
    reload = client.get("/applications/rationalization/api/enrich-candidates?limit=200")
    assert reload.status_code == 200
    apps = {a["id"]: a for a in reload.get_json()["applications"]}
    assert app_row.id in apps, "saved app must appear in the reload payload"
    got = apps[app_row.id]
    assert got["total_cost_of_ownership"] == 123456
    assert got["application_owner"] == "Enterprise Architecture"
    assert got["business_criticality"] == "High"


# ──────────────────────────────────────────────────────────── vendor register


def test_vendor_register_lists_only_the_vendors_the_organisation_names(db_session, make_org, client, login_as):
    """The Vendors page is a register of the vendors named on an organisation's systems and
    contracts. It has a text filter and no type facet, so the property is on its rows: every row
    is a vendor this organisation names, a stored vendor type is not offered as a fixed option,
    and another organisation's vendors, or an unnamed catalogue entry, never appear."""
    import re

    from app.models.vendor.vendor_organization import VendorOrganization

    tag = uuid.uuid4().hex[:6]
    mine, other = make_org("vendor-mine"), make_org("vendor-other")
    user = _make_user(db_session, mine.id)
    _make_app(db_session, mine.id, f"Mine CRM {tag}", vendor_name=f"Mine Vendor {tag}")
    _make_app(db_session, other.id, f"Theirs CRM {tag}", vendor_name=f"Other Vendor {tag}")
    catalogue_only = VendorOrganization(
        name=f"Unnamed Catalogue {tag}", code=f"VEND-CAT-{tag.upper()}", seed_source_id=f"test:catalogue-{tag}",
        vendor_type=f"Systems Integrator {tag}", status="active")
    db_session.add(catalogue_only)
    db_session.flush()

    login_as(client, user)
    resp = client.get("/applications/vendors")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_data(as_text=True)
    listed = re.findall(r'data-testid="vendor-link"[^>]*>\s*([^<]+?)\s*<', body)
    assert listed == [f"Mine Vendor {tag}"]
    assert f"Other Vendor {tag}" not in body
    assert f"Unnamed Catalogue {tag}" not in body
    assert f"Systems Integrator {tag}" not in body
    # The old hard-coded vendor-type vocabulary is gone.
    assert 'value="Hybrid"' not in body
    assert 'value="Enterprise"' not in body
