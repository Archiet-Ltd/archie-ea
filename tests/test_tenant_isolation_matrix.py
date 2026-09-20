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
    "SSOConfig": "read during authentication, before a tenant is known",
    "Subscription": "billing is administered platform-side",
    "UsageEvent": "metering is aggregated platform-side",
    "OrgRole": "role definitions are resolved during authorisation setup",
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
