"""Cross-tenant isolation for the OptionsAnalysis vendor-evaluation family.

Before TenantMixin, options_analysis and its six child tables (vendor_options,
analysis_recommendations, stakeholder_inputs, analysis_scenarios,
required_capabilities, analysis_audit_logs) had no tenant boundary at all.
The confirmed live leak: GET /vendors/ranking (vendor_catalog_routes.py) ran
``VendorOption.query.filter(VendorOption.total_score.isnot(None)).all()``
with no organization filter, returning every organization's vendor scoring
data to any logged-in user.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _make_user(db_session, org_id, email):
    from app.models.user import User

    user = User(email=email, first_name="U", last_name="Ser", organization_id=org_id)
    db_session.add(user)
    db_session.flush()
    return user


def _make_capability(db_session, org_id, name):
    from app.models.business_capabilities import BusinessCapability

    cap = BusinessCapability(name=name, organization_id=org_id)
    db_session.add(cap)
    db_session.flush()
    return cap


def _make_analysis(db_session, org_id, user_id, capability_id, name):
    from app.models.vendor_analysis import OptionsAnalysis

    analysis = OptionsAnalysis(
        name=name, capability_id=capability_id, created_by_id=user_id,
        organization_id=org_id,
    )
    db_session.add(analysis)
    db_session.flush()
    return analysis


def _make_vendor_option(db_session, org_id, analysis_id, name, score):
    from app.models.vendor_analysis import VendorOption

    option = VendorOption(
        analysis_id=analysis_id, vendor_name=name, total_score=score,
        organization_id=org_id,
    )
    db_session.add(option)
    db_session.flush()
    return option


def test_options_analysis_select_is_scoped_to_current_org(db_session, make_org, tenant_ctx):
    """org A must not see org B's vendor-analysis sessions."""
    from app.models.vendor_analysis import OptionsAnalysis

    org_a, org_b = make_org("a"), make_org("b")
    user_a = _make_user(db_session, org_a.id, "a@example.com")
    user_b = _make_user(db_session, org_b.id, "b@example.com")
    cap_a = _make_capability(db_session, org_a.id, "Org A capability")
    cap_b = _make_capability(db_session, org_b.id, "Org B capability")

    _make_analysis(db_session, org_a.id, user_a.id, cap_a.id, "Org A's vendor analysis")
    b_analysis = _make_analysis(db_session, org_b.id, user_b.id, cap_b.id, "Org B's vendor analysis")

    with tenant_ctx(org_a.id):
        visible_ids = {a.id for a in OptionsAnalysis.query.all()}

    assert b_analysis.id not in visible_ids, (
        "TENANT LEAK: org A can read org B's options_analysis rows."
    )


def test_vendor_option_select_is_scoped_to_current_org(db_session, make_org, tenant_ctx):
    """org A must not see org B's vendor scoring data."""
    from app.models.vendor_analysis import VendorOption

    org_a, org_b = make_org("a"), make_org("b")
    user_a = _make_user(db_session, org_a.id, "a2@example.com")
    user_b = _make_user(db_session, org_b.id, "b2@example.com")
    cap_a = _make_capability(db_session, org_a.id, "Org A capability 2")
    cap_b = _make_capability(db_session, org_b.id, "Org B capability 2")

    analysis_a = _make_analysis(db_session, org_a.id, user_a.id, cap_a.id, "Analysis A")
    analysis_b = _make_analysis(db_session, org_b.id, user_b.id, cap_b.id, "Analysis B")

    _make_vendor_option(db_session, org_a.id, analysis_a.id, "Vendor A", 80.0)
    b_option = _make_vendor_option(db_session, org_b.id, analysis_b.id, "Vendor B", 90.0)

    with tenant_ctx(org_a.id):
        visible_ids = {o.id for o in VendorOption.query.all()}

    assert b_option.id not in visible_ids, (
        "TENANT LEAK: org A can read org B's vendor_options rows."
    )


def test_required_capability_select_is_scoped_to_current_org(db_session, make_org, tenant_ctx):
    """org A must not see org B's required-capability rows for an analysis."""
    from app.models.vendor_analysis import RequiredCapability

    org_a, org_b = make_org("a"), make_org("b")
    user_a = _make_user(db_session, org_a.id, "a3@example.com")
    user_b = _make_user(db_session, org_b.id, "b3@example.com")
    cap_a = _make_capability(db_session, org_a.id, "Org A capability 3")
    cap_b = _make_capability(db_session, org_b.id, "Org B capability 3")

    analysis_a = _make_analysis(db_session, org_a.id, user_a.id, cap_a.id, "Analysis A3")
    analysis_b = _make_analysis(db_session, org_b.id, user_b.id, cap_b.id, "Analysis B3")

    req_a = RequiredCapability(
        analysis_id=analysis_a.id, capability_name="Org A required cap",
        importance="must_have", organization_id=org_a.id,
    )
    req_b = RequiredCapability(
        analysis_id=analysis_b.id, capability_name="Org B required cap",
        importance="must_have", organization_id=org_b.id,
    )
    db_session.add_all([req_a, req_b])
    db_session.flush()

    with tenant_ctx(org_a.id):
        visible_ids = {r.id for r in RequiredCapability.query.all()}

    assert req_b.id not in visible_ids, (
        "TENANT LEAK: org A can read org B's required_capabilities rows."
    )


def test_vendor_ranking_route_query_is_scoped_to_current_org(db_session, make_org, tenant_ctx):
    """The confirmed live leak: vendor_catalog_routes.py's /vendors/ranking
    used ``VendorOption.query.filter(VendorOption.total_score.isnot(None)).all()``
    with no explicit org predicate. With TenantMixin in place, do_orm_execute
    now injects the organization_id filter automatically inside a request
    context, so the same unmodified query is scoped -- no route change was
    needed, only the model fix."""
    from app.models.vendor_analysis import VendorOption

    org_a, org_b = make_org("a"), make_org("b")
    user_a = _make_user(db_session, org_a.id, "a4@example.com")
    user_b = _make_user(db_session, org_b.id, "b4@example.com")
    cap_a = _make_capability(db_session, org_a.id, "Org A capability 4")
    cap_b = _make_capability(db_session, org_b.id, "Org B capability 4")

    analysis_a = _make_analysis(db_session, org_a.id, user_a.id, cap_a.id, "Analysis A4")
    analysis_b = _make_analysis(db_session, org_b.id, user_b.id, cap_b.id, "Analysis B4")

    _make_vendor_option(db_session, org_a.id, analysis_a.id, "Vendor A4", 55.0)
    b_option = _make_vendor_option(db_session, org_b.id, analysis_b.id, "Vendor B4", 65.0)

    with tenant_ctx(org_a.id):
        options = VendorOption.query.filter(VendorOption.total_score.isnot(None)).all()
        visible_ids = {o.id for o in options}

    assert b_option.id not in visible_ids, (
        "TENANT LEAK: the /vendors/ranking query pattern still crosses org boundaries."
    )
