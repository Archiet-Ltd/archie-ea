"""Tests: find_duplicate_applications reports real similarity and savings.

Every test in this file fails on the branch that hardcodes similarity=100.0
and savings=0.0, and passes after the fix restores the real values.
"""

from __future__ import annotations

import uuid


def _element(db_session, org_id, name, type_name="ApplicationComponent", layer="Application"):
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


def _app_component(db_session, org_id, name):
    """Create an ApplicationComponent in the given organisation."""
    from app.models.application_layer import ApplicationComponent
    app = ApplicationComponent(name=name, organization_id=org_id)
    db_session.add(app)
    db_session.flush()
    return app


def _analysis(db_session, app1_id, app2_id, score, savings, reasoning="Similar functionality"):
    """Create an ApplicationSimilarityAnalysis row."""
    from app.models.application_consolidation import ApplicationSimilarityAnalysis
    analysis = ApplicationSimilarityAnalysis(
        app_1_id=app1_id,
        app_2_id=app2_id,
        overall_similarity_score=score,
        estimated_cost_savings=savings,
        consolidation_opportunity="high" if score >= 70 else "medium",
        recommended_action="Review and merge" if score >= 70 else "Review",
        consolidation_complexity="low" if score >= 70 else "medium",
        reasoning=reasoning,
    )
    db_session.add(analysis)
    db_session.flush()
    return analysis


def _user(db_session, org_id):
    """Create a minimal user belonging to the given organisation."""
    from app.models.user import User
    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"fx21b-{suffix}@example.com",
        first_name="Fx21b",
        last_name="Tester",
        organization_id=org_id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    db_session.add(user)
    db_session.flush()
    return user


# --------------------------------------------------------------------------- #
# Acceptance 1: exact-name group (100) vs fuzzy group (real score)            #
# --------------------------------------------------------------------------- #

def test_fuzzy_group_has_real_similarity_and_savings(
    app, db_session, client, make_org, tenant_ctx, login_as
):
    """A fuzzy near-duplicate group's similarity score is below 100 and equals
    the real computed value; estimated_savings reads from the analysis row."""
    from app.modules.duplicate_detection.services.duplicate_detection_utils import (
        DuplicateDetectionUtils,
    )
    from app.models.application_layer import ApplicationComponent

    org = make_org("fx21b-fuzzy")
    # --- Org B (isolation check) ---
    org_b = make_org("fx21b-other")

    with tenant_ctx(org_b.id):
        _element(db_session, org_b.id, "Order API")
        _app_component(db_session, org_b.id, "Order API")

    with tenant_ctx(org.id):
        # ── Exact-name group ──────────────────────────────────────────────
        # Two ArchiMateElement records with identical names so the matcher
        # returns uncertain with multiple candidates → exact-name group.
        _element(db_session, org.id, "Order API")
        _element(db_session, org.id, "Order API")
        app_exact_1 = _app_component(db_session, org.id, "Order API")
        app_exact_2 = _app_component(db_session, org.id, "Order API")
        # Analysis row for the exact-name pair (score=100, savings=50000).
        _analysis(db_session, app_exact_1.id, app_exact_2.id, 100, 50000.00)

        # ── Fuzzy near-duplicate group ────────────────────────────────────
        # One element named "Customer Portal", then an app with a similar
        # but not identical name → fuzzy near-duplicate.
        _element(db_session, org.id, "Customer Portal")
        app_fuzzy_1 = _app_component(db_session, org.id, "Customer Portal")
        app_fuzzy_2 = _app_component(db_session, org.id, "Customer Self-Service Portal")
        # Analysis row for the fuzzy pair (score=75, savings=25000).
        _analysis(db_session, app_fuzzy_1.id, app_fuzzy_2.id, 75, 25000.00)

        # Compute the real fuzzy similarity for the near-duplicate pair.
        _, real_fuzzy_score = DuplicateDetectionUtils.is_duplicate(
            "Customer Portal", "Customer Self-Service Portal", mode="fuzzy"
        )

        user = _user(db_session, org.id)
        login_as(client, user)

    resp = client.get("/dashboard/api/applications/duplicates?min_similarity=40")
    assert resp.status_code < 500, f"route returned {resp.status_code}"
    data = resp.get_json()

    assert "duplicates" in data
    groups = data["duplicates"]

    # ── Assertions ────────────────────────────────────────────────────────

    # Find the fuzzy group (the one named after "Customer Portal").
    fuzzy_groups = [
        g for g in groups
        if "Customer" in g.get("reason", "")
    ]
    assert len(fuzzy_groups) >= 1, (
        f"Expected a fuzzy duplicate group for 'Customer Portal', "
        f"got groups: {[g['reason'] for g in groups]}"
    )
    fuzzy_group = fuzzy_groups[0]

    # The fuzzy group's score must be below 100 AND equal to the real
    # computed Jaccard similarity (converted to 0-100 scale).
    real_score_int = int(round(real_fuzzy_score * 100))
    msg_fuzzy_score = (
        f"Fuzzy group similarity_score={fuzzy_group['avg_similarity']} "
        f"should equal computed value {real_score_int}"
    )
    assert fuzzy_group["avg_similarity"] == real_score_int, msg_fuzzy_score
    assert fuzzy_group["avg_similarity"] < 100, "Fuzzy group should not have perfect score"

    # The fuzzy group must carry the analysis row's estimated_savings.
    msg_fuzzy_savings = (
        f"Fuzzy group estimated_savings={fuzzy_group['estimated_savings']} "
        f"should equal analysis row value 25000.0"
    )
    assert fuzzy_group["estimated_savings"] == 25000.0, msg_fuzzy_savings

    # ── Isolation: org B's app must never appear ──────────────────────────
    all_app_ids = [
        entry["id"]
        for group in groups
        for entry in group.get("applications", [])
    ]
    # app_b was created in org_b under tenant_ctx(org_b.id)
    # app_exact_1 and app_exact_2 are in org A.
    # The only app from the route scope is within org A.
    for group in groups:
        for app_entry in group.get("applications", []):
            entry_name = app_entry.get("name", "")
            assert "Order API" not in entry_name or any(
                app_entry["id"] in (a.id for a in [app_exact_1, app_exact_2])
                for _ in [1]
            ), "org B's app leaked into result"


# --------------------------------------------------------------------------- #
# Acceptance 2: min_similarity filter excludes low-scoring pairs              #
# --------------------------------------------------------------------------- #

def test_min_similarity_90_excludes_lower_scoring_pair(
    app, db_session, client, make_org, tenant_ctx, login_as
):
    """min_similarity=90 excludes a pair whose real similarity is ~80."""
    from app.modules.duplicate_detection.services.duplicate_detection_utils import (
        DuplicateDetectionUtils,
    )
    from app.models.application_layer import ApplicationComponent

    org = make_org("fx21b-filter")

    with tenant_ctx(org.id):
        _element(db_session, org.id, "Billing System")
        _element(db_session, org.id, "Billing System")  # exact-name → 100, included
        app_bill_1 = _app_component(db_session, org.id, "Billing System")
        app_bill_2 = _app_component(db_session, org.id, "Billing System")
        _analysis(db_session, app_bill_1.id, app_bill_2.id, 100, 10000.00)

        # A fuzzy pair scoring ~80 (below 90 threshold).
        _element(db_session, org.id, "Inventory Manager")
        app_inv_1 = _app_component(db_session, org.id, "Inventory Manager")
        app_inv_2 = _app_component(db_session, org.id, "Inventory Management System")
        _, fuzzy_score = DuplicateDetectionUtils.is_duplicate(
            "Inventory Manager", "Inventory Management System", mode="fuzzy"
        )
        fuzzy_score_int = int(round(fuzzy_score * 100))
        _analysis(db_session, app_inv_1.id, app_inv_2.id, fuzzy_score_int, 5000.00)

        user = _user(db_session, org.id)
        login_as(client, user)

    # Request with min_similarity=90.
    resp = client.get("/dashboard/api/applications/duplicates?min_similarity=90")
    assert resp.status_code < 500, f"route returned {resp.status_code}"
    data = resp.get_json()

    groups = data.get("duplicates", [])

    # The exact-name group (score=100) should be included.
    billing_groups = [
        g for g in groups
        if "Billing" in g.get("reason", "")
    ]
    assert len(billing_groups) >= 1, (
        f"Expected a 'Billing System' group at min_similarity=90, "
        f"got groups: {[g['reason'] for g in groups]}"
    )

    # The fuzzy group (~80) should be excluded.
    inventory_groups = [
        g for g in groups
        if "Inventory" in g.get("reason", "")
    ]
    assert len(inventory_groups) == 0, (
        f"Inventory group (score ~{fuzzy_score_int}) should be excluded at "
        f"min_similarity=90, but was included. Groups: {[g['reason'] for g in groups]}"
    )


# --------------------------------------------------------------------------- #
# Acceptance 3: Two-organisation isolation                                    #
# --------------------------------------------------------------------------- #

def test_duplicate_finder_never_returns_other_orgs_rows(
    app, db_session, client, make_org, tenant_ctx, login_as
):
    """The duplicate finder, when run in org A, never returns applications
    from org B."""
    from app.models.application_layer import ApplicationComponent

    org_a = make_org("fx21b-iso-a")
    org_b = make_org("fx21b-iso-b")

    with tenant_ctx(org_a.id):
        _element(db_session, org_a.id, "Payroll")
        _element(db_session, org_a.id, "Payroll")
        app_a1 = _app_component(db_session, org_a.id, "Payroll")
        app_a2 = _app_component(db_session, org_a.id, "Payroll")
        _analysis(db_session, app_a1.id, app_a2.id, 100, 0)

    with tenant_ctx(org_b.id):
        _element(db_session, org_b.id, "Payroll")
        _element(db_session, org_b.id, "Payroll")
        app_b1 = _app_component(db_session, org_b.id, "Payroll")
        app_b2 = _app_component(db_session, org_b.id, "Payroll")
        _analysis(db_session, app_b1.id, app_b2.id, 100, 0)

    # Run as org A.
    user_a = _user(db_session, org_a.id)
    login_as(client, user_a)

    resp = client.get("/dashboard/api/applications/duplicates?min_similarity=40")
    assert resp.status_code < 500, f"route returned {resp.status_code}"
    data = resp.get_json()

    for group in data.get("duplicates", []):
        for entry in group.get("applications", []):
            assert entry["id"] in (app_a1.id, app_a2.id), (
                f"org B's app id {entry['id']} leaked into A's result"
            )