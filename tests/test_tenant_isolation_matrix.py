"""Prove tenant isolation empirically, and pin the models that opt out.

Multi-tenant leakage is the defect that ends an enterprise deal, and this
codebase enforces isolation through a SQLAlchemy event listener rather than
through query code - so nothing in a route tells you whether it is working. It
either applies or it silently does not.

This file asserts three things:

  1. TenantMixin actually filters. Two organisations, rows in each, and a query
     from inside one request context must never return the other's row. That is
     a positive control on the mechanism, not on any particular route.
  2. The set of models carrying organization_id WITHOUT the mixin is a fixed,
     justified list. Adding a new one is a decision someone has to make
     explicitly, not something that happens by omission.
  3. Bulk UPDATE and DELETE are documented as bypassing the filter entirely, and
     that remains true - so the documentation stays honest and anyone writing a
     bulk write knows they must scope it themselves.
  4. Every identifier-bearing route in the booted url_map refuses another
     organisation's identifiers - the route sweep at the end of this file,
     with its coverage figure ratcheted in verification_baseline.json.

The gap this closes was real: ApplicationDocument carries organization_id but no
mixin, and the delete handler forgot to scope, so any authenticated user could
destroy any tenant's document by walking integer ids.
"""

import uuid

import pytest

pytestmark = pytest.mark.journey


# Models that carry organization_id but deliberately do NOT get TenantMixin.
# Each needs a reason, because the default answer for tenant data is the mixin.
INTENTIONALLY_GLOBAL = {
    # Authentication and platform administration must resolve across tenants.
    "User": "login resolves by email before an org context exists",
    "AuditLog": "platform-wide audit trail; scoping it would hide cross-tenant events",
    "PendingInvitation": (
        "an invitation belongs to the inviting organisation but is read by an "
        "invitee from another one, and redeemed from an e-mailed link before any "
        "organisation is known; the Team page, resend and withdraw put "
        "organization_id in their own predicates"
    ),
    "SSOConfig": "read during authentication, before a tenant is known",
    "Subscription": "billing is administered platform-side",
    "UsageEvent": "metering is aggregated platform-side",
    "OrgRole": "role definitions are resolved during authorisation setup",
    "BusinessCapabilityEmbedding": (
        "tenant-scoped at the query layer, not by column constraint: "
        "pgvector_embedding_service.py's own _TENANT_EMBEDDING_TABLES "
        "classification uses strict organization_id equality in "
        "scoped_embedding_query; the column stays nullable because this "
        "PR's own tests/test_embedding_org_scoping.py requires it, and "
        "TenantMixin's automatic org auto-set would break "
        "create_capability_embedding's explicit cross-tenant-rewrite refusal"
    ),
    "SolutionEmbedding": (
        "tenant-scoped at the query layer, not by column constraint: "
        "pgvector_embedding_service.py's own _TENANT_EMBEDDING_TABLES "
        "classification uses strict organization_id equality in "
        "scoped_embedding_query; the column stays nullable because this "
        "PR's own tests/test_embedding_org_scoping.py requires it"
    ),
    "ApplicationComponentEmbedding": (
        "tenant-scoped at the query layer, not by column constraint: "
        "pgvector_embedding_service.py's own _TENANT_EMBEDDING_TABLES "
        "classification uses strict organization_id equality in "
        "scoped_embedding_query; the column stays nullable because this "
        "PR's own tests/test_embedding_org_scoping.py requires it"
    ),
    "ChatMessageEmbedding": (
        "tenant-scoped at the query layer, not by column constraint: "
        "always written with an explicit organization_id "
        "(multi_domain_chat_service.py's require_current_org_id), and "
        "matches pgvector_embedding_service.py's own "
        "_TENANT_EMBEDDING_TABLES classification; the column stays "
        "nullable because this PR's own "
        "tests/test_embedding_org_scoping.py requires it"
    ),
    "VendorProductEmbedding": (
        "vendor products are a shared reference catalogue every organisation "
        "searches against; pgvector_embedding_service.py's own comment marks "
        "this embedding table as shared reference data with no org"
    ),
    "VendorOrganizationEmbedding": (
        "vendor organisations are a shared reference catalogue every "
        "organisation searches against; pgvector_embedding_service.py's own "
        "mapping marks this embedding table as shared reference data with no org"
    ),
    "ProcessEmbedding": (
        "APQC/industry processes are a shared reference catalogue every "
        "organisation searches against; pgvector_embedding_service.py's own "
        "_SHARED_EMBEDDING_TABLES set classifies this as nullable-or-org, "
        "same as vendor product/organisation"
    ),
    "UserSession": (
        "a session row is looked up by sid on every authenticated request, before "
        "a tenant context exists, and revocation on logout or password change must "
        "reach the row regardless of the tenant filter; every query is keyed by "
        "sid or user_id and organization_id is kept for attribution only"
    ),
    # Child rows reached only through a TenantMixin parent, which scopes them.
    "ApplicationCapabilityMapping": "reached via ApplicationComponent, which is scoped",
    "ApplicationVersioning": "reached via ApplicationComponent, which is scoped",
    "DeploymentPipeline": "reached via ApplicationComponent, which is scoped",
    "ApplicationPerformanceMetrics": "reached via ApplicationComponent, which is scoped",
    "ApplicationOwner": "queried by user_id, which already implies one tenant",
    "ApplicationDocument": "reached via ApplicationComponent; handlers verify ownership",
    "ContractApplication": "join row between two scoped parents",
    # VendorProductCapability was listed here on the reasoning that the vendor
    # catalogue is shared. The catalogue is — VendorOrganization stays global —
    # but an assessment of how a product covers a business_capability is not:
    # that capability is tenant-owned, so the row describes one customer's
    # model. It now carries TenantMixin. See tests/test_vendor_tenancy_policy.py.
    "VendorProductPricing": "vendor catalogue is shared reference data",
    "SolutionScoringConfig": "scoring defaults are platform-level",
    "OrgConnectorConfig": "queried by explicit organization_id in every caller",
    "DevOpsConnectorConfig": "queried by explicit organization_id in every caller",
    "LucidchartConnectorConfig": "queried by explicit organization_id in every caller",
    # Wave-4 Phase B (ARB/EA tenant partitioning): these 3 are shared catalogs/
    # templates, not per-tenant governance data — see docs/superpowers/plans/
    # 2026-08-13-tenancy-wave-4.md Task 3. Their organization_id column exists
    # (Phase A, for schema symmetry with the sibling per-tenant models) but is
    # unused; TenantMixin would hide them from every org.
    "ARBGovernanceStandard": "shared governance standards catalogue, not org-owned",
    "ARBWorkflowStage": "shared workflow stage catalogue, not org-owned",
    "EAWorkflowDefinition": "shared workflow template catalogue, not org-owned",
    "ArtefactShareLink": (
        "owner actions explicitly scope by organization_id; the unauthenticated "
        "public token flow derives scope from the link"
    ),
    "ErrorEvent": (
        "operational telemetry about the platform, not tenant data — a platform "
        "admin needs to see every organisation's errors to tell 'one customer hit "
        "a bug' from 'the deploy just broke everything'; organization_id/user_id "
        "are plain nullable columns kept for attribution, not filtering (see the "
        "model's own docstring, app/models/error_event.py)"
    ),
    "AcmPropertyTemplate": (
        "NULL organization_id is a shared platform template every organisation "
        "reads; a value is that organisation's own definition, read by that "
        "organisation only (PropertyService.template_query, the model's own "
        "comment) — TenantMixin would hide the shared NULL rows from everyone"
    ),
    "LLMInteraction": (
        "queried by user_id (implies one tenant), by pipeline_stage_id -> "
        "architecture_id (same), or by an explicit organization_id filter in "
        "every caller that reports or lists interactions across a tenant "
        "(llm_cost_tracker.py's _get_organization_spending, "
        "LLMService's decision-log query, TRNT-072)"
    ),
    "ImportSessionLog": (
        "organization_id is nullable (rows written before the column existed "
        "stay valid, and the model's own comment says a row with no "
        "organisation is never offered as a restore point); every real query "
        "already adds an explicit organization_id filter "
        "(import_restore_service.py's restore lookup, "
        "import_snapshot_service.py's snapshot listing/creation, "
        "import_sophisticated_routes.py's idempotency check) — adding "
        "TenantMixin on top would need to decide how it treats those "
        "existing nullable-org rows, which is its own deliberate change, "
        "not something to fold into documenting the current state"
    ),
}


def _model_survey():
    """(models with TenantMixin, models with organization_id but without it)."""
    import io
    import os
    import re

    tenant, unprotected = set(), {}
    for dirpath, _dirnames, filenames in os.walk("app/models"):
        if "__pycache__" in dirpath:
            continue
        for name in filenames:
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            src = io.open(path, encoding="utf-8", errors="ignore").read()
            for m in re.finditer(r"^class (\w+)\(([^)]*)\):(.*?)(?=^class |\Z)", src, re.S | re.M):
                cls, bases, body = m.group(1), m.group(2), m.group(3)
                if "db.Model" not in bases:
                    continue
                if "TenantMixin" in bases:
                    tenant.add(cls)
                elif re.search(r"^\s{4}organization_id\s*=\s*(?:db\.)?Column", body, re.M):
                    unprotected[cls] = path.replace(os.sep, "/")
    return tenant, unprotected


def test_every_unscoped_model_is_a_deliberate_decision():
    """A new model must not silently join the unprotected set.

    Omitting TenantMixin is invisible in review - the model looks complete and
    the column is there. This turns the omission into a failing test that has to
    be answered with a reason.
    """
    _tenant, unprotected = _model_survey()
    undocumented = sorted(set(unprotected) - set(INTENTIONALLY_GLOBAL))
    assert not undocumented, (
        "%d model(s) carry organization_id without TenantMixin and without a "
        "reason:\n  %s\n\nEither add TenantMixin, or add an entry to "
        "INTENTIONALLY_GLOBAL explaining why this data is not tenant-scoped."
        % (len(undocumented), "\n  ".join("%s (%s)" % (m, unprotected[m]) for m in undocumented))
    )


def test_the_justification_list_has_not_gone_stale():
    """An entry for a model that now has the mixin is misleading documentation."""
    tenant, unprotected = _model_survey()
    stale = sorted(set(INTENTIONALLY_GLOBAL) & tenant)
    assert not stale, (
        "these models now have TenantMixin but are still listed as intentionally "
        "global: %s - remove the entries" % stale
    )
    missing = sorted(set(INTENTIONALLY_GLOBAL) - set(unprotected) - tenant)
    assert not missing, (
        "these justifications name models that no longer exist or no longer carry "
        "organization_id: %s" % missing
    )


@pytest.fixture(scope="module")
def app():
    import os

    os.environ.setdefault("SECRET_KEY", "x" * 32)
    from app import create_app, db

    application = create_app("testing")
    with application.app_context():
        db.create_all()
    return application


def test_tenant_mixin_actually_filters_reads(app):
    """The positive control: prove the mechanism, not a route that uses it.

    Isolation here is an ORM event listener. If it stopped applying, every route
    would keep returning 200 and quietly serve other tenants' rows.
    """
    from app import db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.organization import Organization

    marker = uuid.uuid4().hex[:8]
    with app.app_context():
        org_a = Organization(name="Iso A %s" % marker, slug="iso-a-%s" % marker)
        org_b = Organization(name="Iso B %s" % marker, slug="iso-b-%s" % marker)
        db.session.add_all([org_a, org_b])
        db.session.commit()
        a_id, b_id = org_a.id, org_b.id

        db.session.add_all([
            ApplicationComponent(name="A-only %s" % marker, organization_id=a_id),
            ApplicationComponent(name="B-only %s" % marker, organization_id=b_id),
        ])
        db.session.commit()

    # Inside a request bound to org A, org B's row must be invisible.
    with app.test_request_context():
        from flask import g

        g.current_org_id = a_id
        visible = {c.name for c in ApplicationComponent.query.filter(
            ApplicationComponent.name.like("%" + marker)).all()}

    assert ("A-only %s" % marker) in visible, (
        "tenant A cannot see its own row - the filter is over-applying")
    assert ("B-only %s" % marker) not in visible, (
        "TENANT LEAK: a request scoped to org %d returned org %d's row. "
        "The isolation listener is not applying." % (a_id, b_id))


def test_bulk_delete_is_tenant_filtered(app):
    """ADR-0003 gap 1 is closed: a bulk DELETE cannot cross tenants.

    This test previously pinned the opposite — do_orm_execute returned early for
    non-SELECT, so a bulk DELETE carried no tenant predicate — and its own
    docstring said that if it ever started passing, CLAUDE.md and the callers
    relying on that guidance needed revisiting. That is exactly what happened:
    the listener now applies with_loader_criteria to ORM-enabled UPDATE and
    DELETE as well, CLAUDE.md's bulk-write paragraph has been rewritten, and the
    two strict xfails in tests/test_tenant_isolation.py are plain passes.

    Bulk writes should still carry organization_id explicitly as
    defence-in-depth: raw SQL and anything outside a request context remain
    unfiltered (gap 2, by design).
    """
    from app import db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.organization import Organization

    marker = uuid.uuid4().hex[:8]
    with app.app_context():
        org_a = Organization(name="Bulk A %s" % marker, slug="bulk-a-%s" % marker)
        org_b = Organization(name="Bulk B %s" % marker, slug="bulk-b-%s" % marker)
        db.session.add_all([org_a, org_b])
        db.session.commit()
        a_id, b_id = org_a.id, org_b.id
        db.session.add(ApplicationComponent(name="bulk-target %s" % marker,
                                            organization_id=b_id))
        db.session.commit()

    with app.test_request_context():
        from flask import g

        g.current_org_id = a_id
        # Bulk delete issued from tenant A, targeting a row owned by B, with no
        # organization_id of its own in the predicate — the mechanism must supply it.
        deleted = ApplicationComponent.query.filter(
            ApplicationComponent.name == "bulk-target %s" % marker
        ).delete(synchronize_session=False)
        db.session.commit()

    assert deleted == 0, (
        "TENANT LEAK: a bulk DELETE issued in org %d's context matched org %d's "
        "row. do_orm_execute is not applying the tenant filter to DELETE." % (a_id, b_id))

    # And the row is genuinely still there, read back as its owner.
    with app.test_request_context():
        from flask import g

        g.current_org_id = b_id
        survivor = ApplicationComponent.query.filter(
            ApplicationComponent.name == "bulk-target %s" % marker
        ).one_or_none()
        assert survivor is not None, "org B's row was destroyed by org A's bulk delete"

    # This test commits real rows; clean them up rather than leaving residue in
    # the shared test database (the 540 stale organisations in it came from
    # exactly this pattern).
    with app.app_context():
        ApplicationComponent.query.filter(
            ApplicationComponent.name == "bulk-target %s" % marker
        ).delete(synchronize_session=False)
        Organization.query.filter(Organization.id.in_([a_id, b_id])).delete(
            synchronize_session=False)
        db.session.commit()


# =============================================================================
# The route sweep: every identifier-bearing route refuses another
# organisation's identifiers.
# =============================================================================
#
# The tests above prove the MECHANISM. This section proves the ROUTES: it walks
# every rule in the booted url_map that carries a record identifier, seeds that
# record in organisation A, and asks for it as organisation B -- then asks again
# as A, because B's refusal only means something when the owner is served. The
# engine is tests/_isolation_sweep.py; the policy it runs under is all here.
#
# Coverage is the share of in-scope routes (one rule, one method) PROVEN to
# refuse: the owner is served, and B gets 403/404/410, is redirected away, is
# served without A's record, or -- for a write -- changes nothing where the
# owner's identical write does. It is printed on every run and ratcheted as
# ``tenant_isolation_route_coverage_pct`` in verification_baseline.json, the one
# ratchet in that file where higher is better, so it cannot fall.
#
# A route that is not proven is one of: a LEAK (fails this test unless it is in
# KNOWN_LEAKS below), "unfenced" (its record type carries no organisation at
# all -- the list a security architect asks for), or a route the sweep could not
# yet drive to a verdict ("unproven", "unresolved", "unseedable", "not_refused",
# "b_error", "error"). Only named exclusions and named shared catalogues leave
# the denominator.

from tests import _isolation_sweep as sweep  # noqa: E402

import json as _json  # noqa: E402
import pathlib as _pathlib  # noqa: E402

COVERAGE_KEY = "tenant_isolation_route_coverage_pct"
_BASELINE_PATH = _pathlib.Path(__file__).resolve().parent.parent / "verification_baseline.json"

# An integer path parameter names a record, except these, which count or index
# within a record whose own id is another parameter of the same rule.
NON_IDENTIFIER_INTS = {
    "condition_idx", "condition_index", "version", "v1", "v2", "step", "step_num",
}

# A string parameter names a record when it is spelled like one. ``*_key`` and
# ``*_code`` parameters (prompt_key, viewpoint_key, industry_code, ...) name
# entries in platform catalogues, and layer/format/filename/path name no record
# at all, so neither makes a rule identifier-bearing.
STRING_IDENTIFIER = r"^(id|uuid|slug|token)$|_(id|uuid|ref|slug|token)$"

# Exclusions by path parameter: the identifier is not an organisation's record.
EXCLUDED_PARAMS = {
    "token": (
        "an unguessable share, invitation or reset token: the token is the grant, "
        "resolved before any organisation is known, so there is no other "
        "organisation's token to refuse"
    ),
    "org_id": (
        "platform administration of organisations themselves, gated on the "
        "cross-tenant is_platform_admin flag and pinned by "
        "tests/test_platform_admin_isolation.py"
    ),
    "organization_id": "as org_id",
}

# Exclusions by endpoint prefix: the whole blueprint serves no tenant record.
EXCLUDED_ENDPOINT_PREFIXES = {
    "static": "static files",
}

# Exclusions by exact endpoint.
EXCLUDED_ENDPOINTS: dict[str, str] = {
    "solution_design.mark_solution_notification_read": (
        "SolutionNotification has no organisation of its own (solution_id is "
        "nullable, so there is no required parent to derive one from either); "
        "the route scopes by the specific recipient's own user_id "
        "(filter_by(id=notification_id, user_id=current_user.id)), which "
        "another organisation's user can never match -- a narrower guarantee "
        "than organisation-scoping, not a gap in it"
    ),
}

# Record types that belong to no organisation by design. A route whose
# identifiers name only these is out of scope, with this reason. Any other
# record type without an organisation is reported as "unfenced" and counts
# against coverage -- being left off this list is the safe default.
SHARED_MODELS = {
    "VendorOrganization": "the vendor catalogue is shared reference data (see INTENTIONALLY_GLOBAL)",
    "VendorProduct": "the vendor product catalogue is shared reference data",
    "VendorProductPricing": "INTENTIONALLY_GLOBAL: vendor catalogue is shared reference data",
    "TechnicalCapabilityVendorMapping": "maps two shared catalogues (reference capabilities to catalogue vendors)",
    "APQCProcess": "the APQC process classification framework, a published reference model",
    "TechnicalCapability": "the ACM technical reference model, organised by its seven domains",
    "ElementTemplate": "reusable element templates from published frameworks (PCF, ITIL, COBIT)",
    "FrameworkConfigurationTemplate": "pre-defined configuration templates shipped with the platform",
    "FrameworkExtension": "the catalogue of extensions available to every organisation",
    "RequirementTemplate": "the requirement template library shipped with the platform",
    "SolutionTemplate": "reusable solution architecture templates shipped with the platform",
    "CodegenTemplateSet": "the code template marketplace, shared by every organisation",
    "ARBGovernanceStandard": "INTENTIONALLY_GLOBAL: shared governance standards catalogue",
    "ARBWorkflowStage": "INTENTIONALLY_GLOBAL: shared workflow stage catalogue",
    "EAWorkflowDefinition": "INTENTIONALLY_GLOBAL: shared workflow template catalogue",
    "SolutionScoringConfig": "INTENTIONALLY_GLOBAL: scoring defaults are platform-level",
    "ScoringConfiguration": "rationalisation scoring weights are platform-wide configuration",
    "FeatureFlag": "platform feature flags, administered platform-side",
    "SidebarMenuItem": "platform navigation, writable only by a platform administrator",
    "Role": "role definitions shared by every organisation",
}

# Leaks the sweep finds in code another open change owns. Each stays a strict
# expected failure naming its owner (test_known_leak_is_still_open below), so it
# turns red the moment the owner's fix lands and the entry must come out.
_PR258 = "PR 258 (scoring/consolidation): consolidation entries are fenced through their application"
_PR274 = "PR 274 and PR 218 edit solution_design_routes.py; the fix waits for them to land"
KNOWN_LEAKS = {
    # PR 421 (one work package store) scoped the deliverable update and delete
    # through the organisation's own work packages; test_known_leak_is_still_open
    # went XPASS(strict) on both, so their entries come out.
    "DELETE /api/v1/mappings/application-to-vendor/<int:mapping_id>": "PR 269 (vendor/contract)",
    "DELETE /api/v1/mappings/unified-to-application/<int:mapping_id>": "capability store brief",
    "DELETE /api/v1/mappings/unified-to-vendor-org/<int:mapping_id>": "capability store brief",
    "DELETE /capability-map/api/archimate-mappings/<int:mapping_id>": "capability store brief",
    "DELETE /consolidation-list/api/entry/<int:entry_id>": _PR258,
    "PUT /consolidation-list/api/entry/<int:entry_id>": _PR258,
    "GET /consolidation-list/api/entry/<int:entry_id>/detail": _PR258,
    # PR 220 and PR 218 landed (verified: test_known_leak_is_still_open was
    # XPASS(strict) on every one of these 14 routes) -- all proven, not
    # leaking, so their entries come out rather than mask a real regression.
    "DELETE /solutions/<int:solution_id>/archimate-elements/<int:mapping_id>": _PR274,
    "DELETE /solutions/<int:solution_id>/capabilities/<int:mapping_id>": _PR274,
    "POST /solutions/<int:solution_id>/copilot-insights/<int:insight_id>/dismiss": _PR274,
    "GET /solutions/api/<int:solution_id>/reasoning/<int:reasoning_id>": _PR274,
    "GET /solutions/api/registry/specs/<int:spec_id>": _PR274,
}

# Parameters whose record the codebase reading no longer finds, because the lookup moved
# into work_package_service (R1-B04 PR 2): the work package and deliverable routes.
PARAM_MODELS = {
    "wp_id": "unified_work_packages",
    "work_package_id": "unified_work_packages",
    "deliverable_id": "deliverables",
}

POLICY = sweep.Policy(
    param_models=PARAM_MODELS,
    non_identifier_ints=NON_IDENTIFIER_INTS,
    string_identifier=STRING_IDENTIFIER,
    excluded_params=EXCLUDED_PARAMS,
    excluded_endpoint_prefixes=EXCLUDED_ENDPOINT_PREFIXES,
    excluded_endpoints=EXCLUDED_ENDPOINTS,
    shared_models=SHARED_MODELS,
)


def _coverage_baseline():
    data = _json.loads(_BASELINE_PATH.read_text(encoding="utf-8"))
    return data.get("ratchets", {}).get(COVERAGE_KEY)


def _report(cases, excluded):
    """The coverage figure and every route that is not proven, grouped."""
    import collections

    proven, in_scope, percent = sweep.coverage(cases)
    by_status = collections.Counter(c.status for c in cases)
    lines = [
        "",
        "Tenant isolation sweep: %d identifier-bearing routes (%d excluded by name, "
        "%d naming only shared catalogues)" % (
            len(cases) + len(excluded), len(excluded), by_status.get(sweep.SHARED, 0)),
        "  coverage: %s%% -- %d of %d in-scope routes proven to refuse another organisation"
        % (percent, proven, in_scope),
    ]
    for status in (sweep.LEAK,) + sweep.NOT_PROVEN:
        if by_status.get(status):
            lines.append("  %-12s %d" % (status, by_status[status]))
    unfenced = sorted({
        name
        for c in cases if c.status == "unfenced"
        for name in c.detail.split(" carries")[0].replace("record type ", "").split(", ")})
    if unfenced:
        lines.append("  record types with no organisation (%d): %s" % (len(unfenced), ", ".join(unfenced)))
    return "\n".join(lines), percent


@pytest.fixture
def sweep_app(app):
    """This module's app (its fixture runs create_all) with CSRF off, as a test
    client drives it."""
    previous = app.config.get("WTF_CSRF_ENABLED")
    app.config["WTF_CSRF_ENABLED"] = False
    yield app
    app.config["WTF_CSRF_ENABLED"] = previous


def test_every_identifier_bearing_route_refuses_another_organisations_identifiers(
    sweep_app, login_as
):
    """Drive every identifier-bearing route as organisation B against organisation
    A's records; no route may hand B A's record or change it for B.

    Also holds the coverage figure to its ratchet, so the proven share of routes
    cannot fall -- a new identifier-bearing route has to be proven, or it lowers
    the figure and fails here.
    """
    cases, excluded = sweep.run_sweep(sweep_app, login_as, POLICY)
    report, percent = _report(cases, excluded)
    print(report)

    leaks = [c for c in cases if c.status == sweep.LEAK and c.key not in KNOWN_LEAKS]
    assert not leaks, (
        "TENANT LEAK: %d route(s) served or changed another organisation's record:\n  %s\n%s"
        % (len(leaks), "\n  ".join("%s  [%s] %s" % (c.key, c.rule.endpoint, c.detail) for c in leaks),
           report))

    harness_errors = [c for c in cases if c.status == "error"]
    assert len(harness_errors) <= MAX_HARNESS_ERRORS, (
        "the sweep itself failed on %d routes (allowed %d):\n  %s"
        % (len(harness_errors), MAX_HARNESS_ERRORS,
           "\n  ".join("%s: %s" % (c.key, c.detail) for c in harness_errors)))

    baseline = _coverage_baseline()
    assert baseline is not None, "%s is missing from verification_baseline.json" % COVERAGE_KEY
    assert percent >= baseline, (
        "isolation coverage fell from %s%% to %s%%. A new or changed identifier-bearing "
        "route is not proven to refuse another organisation.\n%s" % (baseline, percent, report))


# Routes the harness itself could not drive (an unexpected exception while
# seeding or classifying), measured at 6 when this test was written. Each is
# named in the failure message; the ceiling keeps a harness regression from
# hiding behind the coverage figure.
MAX_HARNESS_ERRORS = 6


def test_every_exclusion_names_its_reason():
    """An exclusion or shared-catalogue entry without a reason is not a decision."""
    for table in (EXCLUDED_PARAMS, EXCLUDED_ENDPOINT_PREFIXES, EXCLUDED_ENDPOINTS,
                  SHARED_MODELS, KNOWN_LEAKS):
        blank = [k for k, why in table.items() if not str(why).strip()]
        assert not blank, "exclusions with no reason: %s" % blank


@pytest.mark.parametrize(
    "route",
    [pytest.param(k, marks=pytest.mark.xfail(strict=True, raises=AssertionError, reason=v))
     for k, v in sorted(KNOWN_LEAKS.items())],
)
def test_known_leak_is_still_open(sweep_app, login_as, route):
    """Each handed-off leak, driven on its own: it must refuse once fixed.

    Strict: when the owner's fix lands this XPASSes, which fails the run, and the
    entry comes out of KNOWN_LEAKS.
    """
    cases, _excluded = sweep.run_sweep(sweep_app, login_as, POLICY, only=lambda c: c.key == route)
    if not cases:
        # Not an AssertionError, so it is not absorbed by the expected failure.
        raise LookupError("%s is no longer an identifier-bearing route in the url_map" % route)
    assert all(c.status != sweep.LEAK for c in cases), "; ".join(
        "%s: %s" % (c.key, c.detail) for c in cases)
