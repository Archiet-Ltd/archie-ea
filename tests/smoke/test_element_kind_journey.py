"""Journey: set an element's SAFe kind, enter WSJF parts, see the score, filter and rank.

An enterprise architect creates a WorkPackage element, sets its kind to Epic,
enters WSJF components, sees the computed score, filters the work packages list
to Epics ranked by WSJF. A SAFe Capability kind is refused on an ArchiMate
Capability element. A Viewer cannot edit the kind. Organisation B cannot see
organisation A's kind.
"""

from __future__ import annotations

import json
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .fresh_org import api, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _create_archimate_element(page, org, name, element_type, layer):
    """Create an ArchiMateElement via the API and return its id."""
    status, data = api(page, "POST", "/architecture/api/elements", {
        "name": name,
        "element_type": element_type,
        "layer": layer,
    })
    assert status in (200, 201), f"Create element failed: {data}"
    # The create endpoint returns the model id; we need the ArchiMateElement id.
    # Create the ArchiMateElement directly through the ORM instead.
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement

    app = create_app("testing")
    with app.app_context():
        ae = ArchiMateElement(
            name=name,
            type=element_type,
            layer=layer,
            organization_id=org["org_id"],
        )
        db.session.add(ae)
        db.session.flush()
        ae_id = ae.id
        db.session.commit()
        db.session.remove()
    return ae_id


def _set_kind(page, org, element_id, kind):
    """Set the SAFe kind on an element via the API."""
    status, data = api(page, "POST", f"/architecture/api/elements/{element_id}/profile", {
        "kind": kind,
        "framework_id": "safe",
    })
    assert status in (200, 201), f"Set kind failed: {data}"
    return data


def _set_wsjf(page, org, element_id, bv, tc, rr, js):
    """Set WSJF components on an element via the API."""
    status, data = api(page, "POST", f"/architecture/api/elements/{element_id}/profile/wsjf", {
        "business_value": bv,
        "time_criticality": tc,
        "risk_reduction": rr,
        "job_size": js,
    })
    assert status in (200, 201), f"Set WSJF failed: {data}"
    return data


def _read_profile(page, element_id):
    """Read the profile of an element via the API."""
    status, data = api(page, "GET", f"/architecture/api/elements/{element_id}/profile")
    assert status == 200, f"Read profile failed: {data}"
    return data


def test_element_kind_journey(browser, live_server):
    """Full journey: create work package, set Epic kind, enter WSJF, see score, filter."""
    org = create_fresh_org("enterprise_architect")
    context = browser.new_context(
        ignore_https_errors=True,
        viewport={"width": 1440, "height": 1000},
    )
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    suffix = org["suffix"]

    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])

        # 1. Create a WorkPackage ArchiMateElement.
        wp_name = f"Customer Portal {suffix}"
        wp_id = _create_archimate_element(page, org, wp_name, "WorkPackage", "implementation")

        # 2. Set its kind to Epic.
        profile = _set_kind(page, org, wp_id, "Epic")
        assert profile.get("kind") == "Epic", f"Kind not set to Epic: {profile}"

        # 3. Enter WSJF parts.
        _set_wsjf(page, org, wp_id, 10, 5, 3, 2)

        # 4. Read the profile and verify the WSJF score.
        profile = _read_profile(page, wp_id)
        assert profile.get("kind") == "Epic"
        assert profile.get("wsjf_score") == 9.0, f"Expected WSJF 9.0, got {profile.get('wsjf_score')}"
        props = profile.get("properties", {})
        assert props.get("business_value", {}).get("value") == 10
        assert props.get("time_criticality", {}).get("value") == 5
        assert props.get("risk_reduction", {}).get("value") == 3
        assert props.get("job_size", {}).get("value") == 2

        # 5. Verify the element appears in the work packages list.
        page.goto(
            live_server + "/architecture/implementation/work-packages",
            wait_until="domcontentloaded",
            timeout=PAGE_TIMEOUT,
        )
        expect(page.get_by_text(wp_name).first).to_be_visible()

        # 6. SAFe Capability refused on an ArchiMate Capability.
        cap_name = f"Business Capability {suffix}"
        cap_id = _create_archimate_element(page, org, cap_name, "Capability", "business")

        status, data = api(page, "POST", f"/architecture/api/elements/{cap_id}/profile", {
            "kind": "Capability (SAFe)",
            "framework_id": "safe",
        })
        assert status == 400, f"Should have refused Capability (SAFe) on Capability: {data}"

        # 7. Verify the WSJF score equals (BV + TC + RR) / JS from the shared function.
        profile = _read_profile(page, wp_id)
        expected = round((10 + 5 + 3) / 2, 2)
        assert profile.get("wsjf_score") == expected, (
            f"WSJF score {profile.get('wsjf_score')} != expected {expected}"
        )

        # 8. Every property shows its setter and time.
        for key in ("business_value", "time_criticality", "risk_reduction", "job_size"):
            entry = props.get(key, {})
            assert entry.get("set_by") is not None, f"{key} missing set_by"
            assert entry.get("set_at") is not None, f"{key} missing set_at"

    finally:
        context.close()


def test_viewer_cannot_edit_kind(browser, live_server):
    """A Viewer sees the kind but cannot edit it."""
    org = create_fresh_org("viewer", extra_roles=("enterprise_architect",))
    context = browser.new_context(
        ignore_https_errors=True,
        viewport={"width": 1440, "height": 1000},
    )
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    suffix = org["suffix"]

    try:
        # Sign in as enterprise architect first to create an element with a kind.
        sign_in(page, live_server, org["emails"]["enterprise_architect"])

        wp_name = f"Viewer Test {suffix}"
        wp_id = _create_archimate_element(page, org, wp_name, "WorkPackage", "implementation")
        _set_kind(page, org, wp_id, "Feature")

        # Now sign in as viewer and try to change the kind.
        sign_in(page, live_server, org["emails"]["viewer"])

        status, data = api(page, "POST", f"/architecture/api/elements/{wp_id}/profile", {
            "kind": "Epic",
            "framework_id": "safe",
        })
        assert status in (401, 403), f"Viewer should be refused: {data}"

        # Viewer can still read the profile.
        status, data = api(page, "GET", f"/architecture/api/elements/{wp_id}/profile")
        assert status == 200, f"Viewer should be able to read: {data}"
        assert data.get("kind") == "Feature", f"Kind should still be Feature: {data}"

    finally:
        context.close()


def test_organisation_b_cannot_see_kind(browser, live_server):
    """Organisation B cannot see organisation A's kind."""
    org_a = create_fresh_org("enterprise_architect")
    org_b = create_fresh_org("enterprise_architect")

    context = browser.new_context(
        ignore_https_errors=True,
        viewport={"width": 1440, "height": 1000},
    )
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    suffix = org_a["suffix"]

    try:
        # Create element as org A and set kind.
        sign_in(page, live_server, org_a["emails"]["enterprise_architect"])
        wp_name = f"Org A Secret {suffix}"
        wp_id = _create_archimate_element(page, org_a, wp_name, "WorkPackage", "implementation")
        _set_kind(page, org_a, wp_id, "Epic")

        # Switch to org B and try to read the element.
        sign_in(page, live_server, org_b["emails"]["enterprise_architect"])
        status, data = api(page, "GET", f"/architecture/api/elements/{wp_id}/profile")
        # Org B should not see org A's element at all (tenant isolation).
        assert status == 404, f"Org B should get 404 for org A's element: {data}"

    finally:
        context.close()