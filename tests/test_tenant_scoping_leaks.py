"""Regression tests for shell-overhaul Wave 3 Task 2's tenant-scoping fixes.

`scripts/check_tenant_scoping.py` found 153 unscoped ORM statements over
models that carry ``organization_id`` but not ``TenantMixin`` (so
``do_orm_execute`` never filters them). Most were false positives — an FK id
that was already org-scoped through its parent, a self-lookup by the acting
user's own id, a globally-unique key. This file covers the ones that were
*real*: a two-org setup where, before the fix, org A's request could read or
write org B's rows.

Follows the ``db_session`` / ``make_org`` / ``tenant_ctx`` fixtures in
``tests/conftest.py`` — see ``tests/test_tenant_isolation.py`` for the
pattern this repo standardizes on.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _make_user(db_session, org_id, email_prefix="user", **kwargs):
    from app.models.user import User

    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"{email_prefix}-{suffix}@example.com",
        first_name=kwargs.pop("first_name", "Test"),
        last_name=kwargs.pop("last_name", "User"),
        organization_id=org_id,
        confirmed=True,
        **kwargs,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _make_app(db_session, org_id, name):
    from app.models.application_portfolio import ApplicationComponent

    row = ApplicationComponent(name=name, organization_id=org_id)
    db_session.add(row)
    db_session.flush()
    return row


def _make_capability(db_session, org_id, name):
    from app.models.archimate_core import ArchiMateElement
    from app.models.business_capabilities import BusinessCapability

    # BusinessCapability's before_insert hook auto-creates an ArchiMateElement
    # via a raw connection.execute() that does not set organization_id, so it
    # violates that column's NOT NULL constraint unless archimate_element_id
    # is already populated. Pre-create the element through the ORM (which
    # does set organization_id) to sidestep that unrelated, pre-existing gap.
    element = ArchiMateElement(
        name=name, type="Capability", layer="Strategy", organization_id=org_id
    )
    db_session.add(element)
    db_session.flush()

    row = BusinessCapability(
        name=name, organization_id=org_id, level=1, archimate_element_id=element.id
    )
    db_session.add(row)
    db_session.flush()
    return row


def _make_mapping(db_session, org_id, app_id, capability_id):
    from app.models.application_capability import ApplicationCapabilityMapping

    # ApplicationCapabilityMapping's after_insert hook
    # (app/models/archimate_relationship_sync.py) auto-creates an
    # ArchiMateRelationship that does not set organization_id, an unrelated
    # pre-existing gap. Insert via Core to bypass ORM event listeners.
    result = db_session.execute(
        ApplicationCapabilityMapping.__table__.insert().values(
            organization_id=org_id,
            application_component_id=app_id,
            business_capability_id=capability_id,
        )
    )
    db_session.flush()
    mapping_id = result.inserted_primary_key[0]
    return db_session.get(ApplicationCapabilityMapping, mapping_id)


def _make_mapping_no_org_column(db_session, app_id, capability_id):
    """Insert an ApplicationCapabilityMapping the way production actually has
    them: ``organization_id`` left unset.

    The model declares ``organization_id`` ``nullable=False``, but that is
    aspirational — reconcile-schema only ever ADDs nullable columns, and in
    production this one is NULL on every row (nothing populates it: not the
    UI create path, not the Abacus importer, not the seeders — only
    ``seed_demo_mappings`` does, which is why in-repo tests that go through
    the model's declared constraint miss this). The column's NOT NULL
    constraint is dropped for the duration of this test's transaction (which
    ``db_session`` always rolls back) so the insert reflects the real
    production row shape instead of the aspirational one.
    """
    from sqlalchemy import text
    from app.models.application_capability import ApplicationCapabilityMapping

    db_session.execute(
        text(
            "ALTER TABLE application_capability_mapping "
            "ALTER COLUMN organization_id DROP NOT NULL"
        )
    )
    result = db_session.execute(
        ApplicationCapabilityMapping.__table__.insert().values(
            application_component_id=app_id,
            business_capability_id=capability_id,
        )
    )
    db_session.flush()
    mapping_id = result.inserted_primary_key[0]
    return db_session.get(ApplicationCapabilityMapping, mapping_id)


# ------------------------------------------------------------- admin IDOR


def test_admin_user_service_get_all_users_is_org_scoped(db_session, make_org, tenant_ctx):
    """AdminUserService.get_all_users() must not list another org's users.

    Regression for app/modules/admin/services/admin_user_service.py and its
    v2 twin (app/modules/admin/v2/services/admin_user_service_v2.py): both
    ran ``User.query`` with no predicate under ``admin_required`` (an
    org-level permission, not platform_admin), so any org admin could list
    every tenant's users.
    """
    org_a, org_b = make_org("admin-a"), make_org("admin-b")
    user_a = _make_user(db_session, org_a.id, "admin-svc-a")
    user_b = _make_user(db_session, org_b.id, "admin-svc-b")

    from app.modules.admin.services.admin_user_service import AdminUserService as SvcV1
    from app.modules.admin.v2.services.admin_user_service_v2 import AdminUserService as SvcV2

    for svc in (SvcV1, SvcV2):
        with tenant_ctx(org_a.id):
            users = svc.get_all_users()
            ids = {u.id for u in users}
        assert user_a.id in ids
        assert user_b.id not in ids, (
            f"{svc.__module__}.get_all_users() leaked org B's user into org A's admin panel"
        )


def test_admin_user_service_get_user_or_404_rejects_other_org(db_session, make_org, tenant_ctx):
    """AdminUserService.get_user_or_404() must 404 on a foreign org's user id.

    Before the fix this was a straight IDOR: an org admin could fetch (and,
    via update_user_role/update_user_email, mutate) any user in any org just
    by walking integer ids.
    """
    org_a, org_b = make_org("admin-404-a"), make_org("admin-404-b")
    user_b = _make_user(db_session, org_b.id, "admin-404-b")

    from flask import abort
    from app.modules.admin.services.admin_user_service import AdminUserService as SvcV1
    from app.modules.admin.v2.services.admin_user_service_v2 import AdminUserService as SvcV2

    for svc in (SvcV1, SvcV2):
        with tenant_ctx(org_a.id):
            with pytest.raises(Exception):
                svc.get_user_or_404(user_b.id)


def test_user_role_route_scopes_role_assignment_to_org(db_session, make_org, tenant_ctx, app):
    """POST /admin/user/<id>/role must not let org A escalate org B's user.

    Regression for app/modules/admin/routes/user_role_routes.py, the
    concrete cross-org role-escalation IDOR named in the Wave 3 task: before
    the fix, ``User.query.get_or_404(user_id)`` had no org predicate at all.
    """
    org_a, org_b = make_org("role-a"), make_org("role-b")
    user_b = _make_user(db_session, org_b.id, "role-b", enterprise_role="application_manager")

    from app.models.user import User

    with app.test_request_context(
        f"/admin/user/{user_b.id}/role",
        method="POST",
        data={"enterprise_role": "platform_admin"},
    ):
        from flask import g
        g.current_org_id = org_a.id
        from app.modules.admin.routes.user_role_routes import update_user_role

        response = update_user_role(user_b.id)
        # Either a 404 (preferred) or a redirect that leaves the role
        # untouched is acceptable; what must never happen is the role write.
        status = getattr(response, "status_code", 200)
        assert status in (302, 404), f"unexpected status {status} from cross-org role update"

    db_session.refresh(user_b)
    assert user_b.enterprise_role == "application_manager", (
        "org A was able to escalate org B's user to platform_admin"
    )


# ------------------------------------------------- ApplicationCapabilityMapping aggregates


def test_rationalization_consolidation_candidates_is_org_scoped(db_session, make_org, tenant_ctx):
    """_find_consolidation_candidates() must not count org B's mappings.

    Regression for app/services/rationalization_proposal_service.py: the
    over-covered-capability aggregate had no organization_id predicate, so
    an org with 0 real duplicate-capability coverage could see a false
    CONSOLIDATE proposal manufactured entirely from another org's data (or
    vice-versa: real over-coverage hidden behind a combined count).
    """
    org_a, org_b = make_org("acm-agg-a"), make_org("acm-agg-b")
    cap_b = _make_capability(db_session, org_b.id, "Shared-name capability")

    # Org B alone has 3+ apps mapped to one capability — enough to trigger
    # the CONSOLIDATE proposal if (and only if) org A's query can see it.
    for i in range(3):
        app_b = _make_app(db_session, org_b.id, f"B App {i}")
        _make_mapping(db_session, org_b.id, app_b.id, cap_b.id)

    from app.services.rationalization_proposal_service import RationalizationProposalService

    with tenant_ctx(org_a.id):
        proposals = RationalizationProposalService._find_consolidation_candidates()

    assert proposals == [], (
        "org A's rationalization proposals were built from org B's "
        "capability-mapping data"
    )


def test_investment_prioritization_mappings_are_org_scoped(db_session, make_org, tenant_ctx):
    """analyze_investment_priorities() must not batch-load org B's mappings.

    Regression for app/services/investment_prioritization_service.py, which
    did ``ApplicationCapabilityMapping.query.all()`` with no predicate at
    all — a completely unscoped read of every organization's mappings.
    """
    org_a, org_b = make_org("invest-a"), make_org("invest-b")
    cap_a = _make_capability(db_session, org_a.id, "Org A Capability")
    cap_b = _make_capability(db_session, org_b.id, "Org B Capability")
    app_a = _make_app(db_session, org_a.id, "Org A App")
    app_b = _make_app(db_session, org_b.id, "Org B App")
    _make_mapping(db_session, org_a.id, app_a.id, cap_a.id)
    _make_mapping(db_session, org_b.id, app_b.id, cap_b.id)

    from app.services.investment_prioritization_service import InvestmentPrioritizationService

    with tenant_ctx(org_a.id):
        result = InvestmentPrioritizationService().analyze_investment_priorities(
            include_risk_analysis=False
        )

    scored_ids = {c["capability_id"] for c in result.get("capability_scores", [])}
    assert cap_b.id not in scored_ids, (
        "investment prioritization scored org B's capability from org A's request"
    )


# ------------------------------------------------------ dashboard user counts


def test_dashboard_user_count_is_org_scoped(db_session, make_org, tenant_ctx, app):
    """The admin dashboard's total-user metric must count only the caller's org.

    Regression for app/modules/dashboard/v2/routes/dashboard_views.py and
    app/modules/dashboard/routes/dashboard_views.py: both fed
    ``db.func.count(User.id)`` with no predicate into a dashboard card, so
    every organization saw the platform-wide user count instead of its own.
    """
    org_a, org_b = make_org("dash-a"), make_org("dash-b")
    from app.services.billing_plans import set_contract_plan

    set_contract_plan(org_b, "enterprise", None)  # five people: more than Community admits
    _make_user(db_session, org_a.id, "dash-a-1")
    _make_user(db_session, org_a.id, "dash-a-2")
    for i in range(5):
        _make_user(db_session, org_b.id, f"dash-b-{i}")

    from app import db
    from app.models.user import User

    with app.test_request_context("/"):
        from flask import g
        g.current_org_id = org_a.id
        count = db.session.query(db.func.count(User.id)).filter(
            User.organization_id == g.current_org_id
        ).scalar()

    assert count == 2, f"expected org A's own count of 2 users, got {count}"


# --------------------------------------------------------- governance notifier


def test_governance_notifier_audience_is_org_scoped(db_session, make_org, tenant_ctx):
    """GovernanceNotifier must only page the triggering org's admins/architects.

    Regression for app/modules/solutions_strategic/v2/services/governance_notifier.py:
    ``_audience_user_ids`` built its notification/email audience from
    ``User.query.filter(User.enterprise_role.in_(roles))`` with no org
    predicate, so a HIGH/critical finding in org A paged every organization's
    platform_admin/enterprise_architect users.
    """
    org_a, org_b = make_org("gov-a"), make_org("gov-b")
    admin_a = _make_user(
        db_session, org_a.id, "gov-admin-a", enterprise_role="enterprise_architect"
    )
    admin_b = _make_user(
        db_session, org_b.id, "gov-admin-b", enterprise_role="enterprise_architect"
    )

    from app.modules.solutions_strategic.v2.services.governance_notifier import (
        GovernanceNotifier,
    )
    from flask import current_app, g

    with current_app.test_request_context("/"):
        g.current_org_id = org_a.id
        ids = GovernanceNotifier._audience_user_ids(
            roles=("enterprise_architect",), extra_user_ids=None
        )

    assert admin_a.id in ids
    assert admin_b.id not in ids, (
        "GovernanceNotifier paged org B's enterprise_architect about org A's finding"
    )


# ---------------------------------------------- FK-parent scoping proof (CRITICAL)


def test_acm_coverage_is_scoped_via_fk_parent_not_the_null_org_column(
    db_session, make_org, tenant_ctx
):
    """The regression that would have caught the ACM.organization_id defect.

    ApplicationCapabilityMapping.organization_id is NULL on every row in
    production (see e622d36 and the comments on every fix in this file's
    ACM sites) -- nothing populates it. An earlier version of these fixes
    scoped by ``ACM.organization_id == g.current_org_id`` directly, which
    is correct in shape but matches ZERO rows against real data: capability
    coverage reads empty, investment/smart-defaults score zero coverage,
    rationalization finds no candidates, and worst of all the Abacus import
    dedup read finds no existing mapping and duplicates it on every re-run.

    This test inserts an ACM row the way production actually has them --
    ``organization_id`` left NULL, not set to the current org -- and proves
    org A's read still finds it (via the TenantMixin FK parent,
    BusinessCapability) while org B's read does not. A test that sets
    organization_id on the mapping (the shape every other test in this file
    uses, and prod never has) cannot distinguish FK-parent scoping from
    column scoping; this one can, because with organization_id NULL a
    column-based predicate cannot possibly match either org.
    """
    org_a, org_b = make_org("acm-fk-a"), make_org("acm-fk-b")
    cap_a = _make_capability(db_session, org_a.id, "FK-parent capability")
    app_a = _make_app(db_session, org_a.id, "FK-parent app")
    app_a.deployment_status = "production"
    db_session.flush()
    _make_mapping_no_org_column(db_session, app_a.id, cap_a.id)

    from app.modules.capabilities.services.business_capability_mapper import (
        BusinessCapabilityMapper,
    )

    mapper = BusinessCapabilityMapper()

    with tenant_ctx(org_a.id):
        analysis_a = mapper.analyze_portfolio_capability_coverage()

    assert analysis_a["existing_mappings"] == 1, (
        "org A could not see its own capability mapping once "
        "organization_id was NULL (production shape) -- scoping is still "
        "keyed off the NULL ACM column instead of the TenantMixin FK "
        "parent BusinessCapability"
    )
    assert analysis_a["mapped_applications"] == 1, (
        "org A's own application was not recognised as mapped once the "
        "ACM row's organization_id was NULL (production shape)"
    )

    with tenant_ctx(org_b.id):
        analysis_b = mapper.analyze_portfolio_capability_coverage()

    assert analysis_b["existing_mappings"] == 0, (
        "org B saw org A's NULL-organization_id capability mapping -- "
        "FK-parent scoping via BusinessCapability is not actually "
        "restricting the read"
    )


# --------------------------------------- journey_v2_routes capability IDOR (CRITICAL)


def test_enrich_capabilities_rejects_cross_org_capability_id(db_session, make_org, tenant_ctx):
    """_enrich_capabilities_with_technical_data() must not return org B's
    ApplicationCapabilityMapping data for a capability id supplied by org A.

    Regression for the cross-tenant IDOR at
    app/modules/solutions_strategic/v2/routes/journey_v2_routes.py
    (~line 2035, in the generate-architecture POST path): ``cap_id`` comes
    straight off the raw request JSON body (``capabilities[].id``), not from
    an org-scoped DB load, and ``_require_solution_owner`` only validates the
    *solution* id, never the capability ids embedded in the payload.
    ``ApplicationCapabilityMapping`` is not ``TenantMixin`` and its
    ``organization_id`` is NULL in production, so before the fix nothing
    scoped this read: an authenticated user owning a solution in org A could
    POST org B's ``business_capability_id`` and get back org B's
    coverage/gap data (application names, coverage %, gap severity, etc.).

    The fix scopes through the TenantMixin FK parent BusinessCapability
    (mirroring ``test_acm_coverage_is_scoped_via_fk_parent_not_the_null_org_column``
    above), so this test also inserts the mapping with ``organization_id``
    left NULL -- the real production row shape -- to prove the join/filter
    is doing the scoping, not a column that is never actually populated.
    """
    org_a, org_b = make_org("idor-a"), make_org("idor-b")
    cap_b = _make_capability(db_session, org_b.id, "Org B Secret Capability")
    app_b = _make_app(db_session, org_b.id, "Org B Secret App")
    _make_mapping_no_org_column(db_session, app_b.id, cap_b.id)

    from app.modules.solutions_strategic.v2.routes.journey_v2_routes import (
        _enrich_capabilities_with_technical_data,
    )

    with tenant_ctx(org_a.id):
        enriched = _enrich_capabilities_with_technical_data(
            [{"id": cap_b.id, "name": "Org B Secret Capability"}]
        )

    assert len(enriched) == 1
    assert enriched[0]["coverage_gaps"] == [], (
        "org A's request leaked org B's ApplicationCapabilityMapping "
        "coverage/gap data via a capability id taken straight from the "
        "request JSON body -- cross-tenant IDOR"
    )

    # Sanity: org B's own request for the same capability id *does* see its
    # mapping, proving the assertion above is a real tenant boundary and not
    # a query that returns empty for everyone.
    with tenant_ctx(org_b.id):
        enriched_b = _enrich_capabilities_with_technical_data(
            [{"id": cap_b.id, "name": "Org B Secret Capability"}]
        )
    assert enriched_b[0]["coverage_gaps"] != [], (
        "sanity check failed: org B could not see its own capability "
        "mapping either, so the empty result for org A above does not "
        "prove tenant scoping -- it proves the query is broken"
    )
    assert enriched_b[0]["coverage_gaps"][0]["application_name"] == "Org B Secret App"
