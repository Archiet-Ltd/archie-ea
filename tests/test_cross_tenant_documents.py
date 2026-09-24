"""A tenant must not be able to destroy another tenant's documents.

ApplicationDocument carries organization_id but is a plain db.Model, not a
TenantMixin one, so no WHERE organization_id = ... is injected and
.query.get_or_404() returns any tenant's row. Every handler therefore has to
check ownership itself.

Until 2026-07-31 the read path did and the delete path did not:

    download_document_file()  -> verify_file_access(parent_app.organization_id)
    delete_document_file()    -> (nothing)

so any authenticated user could walk integer ids and permanently remove another
tenant's document - the database row and the file on disk. Deletion is not
recoverable, which makes the omission worse on this path than on the read one
that was protected.

Both layouts register a delete route - /applications/... from app/modules/ and
/dashboard/... from the legacy app/application_mgmt/ - so fixing one left the
same hole open under a different URL. This asserts against the URL map for that
reason: it is the only view that sees both.
"""

import inspect
import os
import re

import pytest

pytestmark = pytest.mark.journey

# Handlers reached via these paths mutate a model with no automatic tenant filter,
# so each must scope by organisation itself.
UNSCOPED_MODELS = (
    "ApplicationDocument",
    "ApplicationCapabilityMapping",
    "ApplicationOwner",
    "LicenseEntitlement",
    "ContractApplication",
)

# An explicit ownership/authorisation check in the handler source. Any one of
# these on its own scopes a handler: it compares against the caller's
# organisation (verify_file_access, organization_id, current_user.organization,
# _check_access) or aborts the request.
EXPLICIT_OWNERSHIP_MARKERS = (
    "verify_file_access",
    "organization_id",
    "current_user.organization",
    "_check_access",
    "abort(403",
)

# Loading the parent ApplicationComponent scopes a handler ONLY when the
# unscoped child is queried back to that parent through a foreign key (a marker
# in FK_SCOPED_CHILD_MARKERS). ApplicationComponent is a TenantMixin model, so
# .query.get_or_404() is filtered and a cross-tenant caller gets 404 before the
# child is reached — but a bare parent load followed by loading the child by its
# own primary key does NOT propagate that scope to the child.
#
# Deliberately NOT counting db.session.get(ApplicationComponent, ...): that can
# be served from the identity map without emitting a SELECT, so the filter is
# not guaranteed to run.
PARENT_SCOPED_MARKERS = (
    "ApplicationComponent.query.get_or_404(",
    "ApplicationComponent.query.get(",
)

# Foreign-key columns that tie an unscoped child back to the ApplicationComponent
# parent (or an entity reachable only through one). A handler that relies on
# PARENT_SCOPED_MARKERS must also filter the child by one of these for the parent's
# tenant scope to actually reach the child row.
FK_SCOPED_CHILD_MARKERS = (
    "application_component_id=",
    "application_id=",
    "capability_id=",
    "business_capability_id=",
)

# Kept for test_every_document_delete_route_checks_tenancy: every
# document-delete handler also carries an explicit verify_file_access check, so
# accepting a parent load as scoping is safe there.
OWNERSHIP_MARKERS = EXPLICIT_OWNERSHIP_MARKERS + PARENT_SCOPED_MARKERS


def _unwrap(view):
    """Strip login_required/audit_log wrappers to reach the real handler."""
    seen = 0
    while getattr(view, "__wrapped__", None) and seen < 10:
        view = view.__wrapped__
        seen += 1
    return view


def test_every_document_delete_route_checks_tenancy(app):
    """Named explicitly because this one destroys data irreversibly."""
    unchecked = []
    for rule in app.url_map.iter_rules():
        if "documents/" not in str(rule) or "delete" not in str(rule):
            continue
        view = _unwrap(app.view_functions.get(rule.endpoint))
        try:
            src = inspect.getsource(view)
        except (OSError, TypeError):
            continue
        if not any(marker in src for marker in OWNERSHIP_MARKERS):
            unchecked.append(str(rule))

    assert not unchecked, (
        "document delete route(s) with no tenant check: %s\n"
        "ApplicationDocument has no TenantMixin, so get_or_404() will return "
        "another tenant's row and the handler will delete it." % unchecked
    )


def test_mutating_routes_on_unfiltered_models_scope_themselves(app):
    """The general form of the same defect.

    A model without TenantMixin gets no injected filter, so any handler that
    writes to one and does not scope by organisation is reachable across the
    tenant boundary.
    """
    findings = []
    for rule in app.url_map.iter_rules():
        if not (rule.methods - {"HEAD", "OPTIONS", "GET"}):
            continue
        view = _unwrap(app.view_functions.get(rule.endpoint))
        if view is None:
            continue
        try:
            src = inspect.getsource(view)
        except (OSError, TypeError):
            continue
        # \b on the left matters: a plain substring test matches
        # UnifiedApplicationCapabilityMapping.query when looking for
        # ApplicationCapabilityMapping.query. They are different models, and the
        # Unified pair are shared reference data with no organization_id at all -
        # so the unfiltered query there is correct, and reporting it trains the
        # reader to ignore this test.
        touches = [m for m in UNSCOPED_MODELS if re.search(r"\b%s\.query" % m, src)]
        if not touches:
            continue
        if any(marker in src for marker in EXPLICIT_OWNERSHIP_MARKERS):
            continue
        parent_scoped = any(marker in src for marker in PARENT_SCOPED_MARKERS)
        if parent_scoped and any(marker in src for marker in FK_SCOPED_CHILD_MARKERS):
            continue
        findings.append("%s -> %s (%s)" % (rule, rule.endpoint, ",".join(touches)))

    assert not findings, (
        "%d mutating route(s) touch a model with no automatic tenant filter and "
        "do not scope by organisation:\n  %s\n\nEither add an ownership check or "
        "give the model TenantMixin." % (len(findings), "\n  ".join(sorted(findings)))
    )


# ---------------------------------------------------------------------------
# Integration tests: a tenant administrator must not be able to delete or
# download another organisation's document.
# ---------------------------------------------------------------------------

import uuid as _uuid


def _clear_g_cache():
    """Drop cached flask_login/tenant state from Flask's ``g``."""
    from flask import g as _g, has_app_context as _hac

    if not _hac():
        return
    for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
        if hasattr(_g, cached):
            delattr(_g, cached)


def _login(client, user):
    """Log a test client in as *user* (a User instance or an int id)."""
    from tests._session_test_helpers import mint_test_sid

    user_id = getattr(user, "id", user)
    org_id = getattr(user, "organization_id", None) if hasattr(user, "id") else None
    sid = mint_test_sid(user_id, organization_id=org_id)
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True
        sess["_sid"] = sid
    _clear_g_cache()


@pytest.fixture
def _two_org_fixture(app):
    """Create two organisations, each with a user, plus an app+doc in org B.

    Uses explicit commits so the data is visible to HTTP requests made through
    the test client (the db_session fixture wraps everything in a transaction
    that is never committed, so data created inside it is invisible to the
    request-handling connection).
    """
    import os as _os

    from app import db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.miscellaneous import ApplicationDocument
    from app.models.organization import Organization
    from app.models.user import User

    suffix = _uuid.uuid4().hex[:10]

    with app.app_context():
        org_a = Organization(
            name=f"Test delete-a {suffix}", slug=f"test-delete-a-{suffix}"
        )
        org_b = Organization(
            name=f"Test delete-b {suffix}", slug=f"test-delete-b-{suffix}"
        )
        db.session.add_all([org_a, org_b])
        db.session.flush()

        user_a = User(
            email=f"delete-a-{_uuid.uuid4().hex[:8]}@example.com",
            first_name="A",
            last_name="Admin",
            organization_id=org_a.id,
            confirmed=True,
            enterprise_role="platform_admin",
        )
        db.session.add(user_a)
        db.session.flush()

        app_b = ApplicationComponent(
            name=f"App-B-{_uuid.uuid4().hex[:8]}",
            organization_id=org_b.id,
        )
        db.session.add(app_b)
        db.session.flush()

        # An application owned by org A, so org A's user can pass the
        # tenant-scoped parent lookup on the analyze-document route and reach
        # the document-level tenant check.
        app_a = ApplicationComponent(
            name=f"App-A-{_uuid.uuid4().hex[:8]}",
            organization_id=org_a.id,
        )
        db.session.add(app_a)
        db.session.flush()

        # Create a real file on disk so the delete path tries to remove it.
        upload_dir = _os.path.join(
            app.instance_path, "uploads", str(org_b.id), "documents"
        )
        _os.makedirs(upload_dir, exist_ok=True)
        file_path = _os.path.join(upload_dir, f"test-{_uuid.uuid4().hex[:8]}.txt")
        with open(file_path, "w") as f:
            f.write("cross-tenant test file")

        doc_b = ApplicationDocument(
            organization_id=org_b.id,
            application_component_id=app_b.id,
            title="Org B Secret Document",
            file_name="secret.txt",
            file_extension="TXT",
            file_path=file_path,
            file_size=_os.path.getsize(file_path),
            uploaded_by="b-admin",
        )
        db.session.add(doc_b)
        db.session.flush()

        db.session.commit()

        ids = {
            "org_a_id": org_a.id,
            "org_b_id": org_b.id,
            "user_a_id": user_a.id,
            "app_a_id": app_a.id,
            "app_b_id": app_b.id,
            "doc_b_id": doc_b.id,
            "file_path": file_path,
        }

    yield ids


def _make_client(app, user_id):
    """Create a test client logged in as the given user."""
    from app import db
    from app.models.user import User

    client = app.test_client()
    with app.app_context():
        user = db.session.get(User, user_id)
        _login(client, user)
    return client


def test_cross_tenant_delete_refused_legacy_route(app, _two_org_fixture):
    """Org A's admin POSTs /dashboard/documents/<B's doc>/delete → refused."""
    from app import db
    from app.models.miscellaneous import ApplicationDocument

    f = _two_org_fixture
    client_a = _make_client(app, f["user_a_id"])

    resp = client_a.post(f"/dashboard/documents/{f['doc_b_id']}/delete")
    # The query now filters by organization_id so a foreign document is simply
    # not found (404) rather than being loaded and then denied.
    assert resp.status_code == 404, (
        f"Unexpected status {resp.status_code}"
    )

    # Document row must still exist — this is the assertion that fails red.
    with app.app_context():
        doc_still = db.session.get(ApplicationDocument, f["doc_b_id"])
        assert doc_still is not None, (
            "Org B's document row was destroyed by Org A's delete"
        )

    # File must still exist.
    assert os.path.exists(f["file_path"]), (
        "Org B's document file was deleted from disk by Org A's delete"
    )


def test_cross_tenant_delete_refused_unified_route(app, _two_org_fixture):
    """Org A's admin POSTs /applications/documents/<B's doc>/delete → refused."""
    from app import db
    from app.models.miscellaneous import ApplicationDocument

    f = _two_org_fixture
    client_a = _make_client(app, f["user_a_id"])

    resp = client_a.post(
        f"/applications/documents/{f['doc_b_id']}/delete",
        data={"csrf_token": "test-bypass"},
    )
    # The query now filters by organization_id so a foreign document is simply
    # not found (404) — the CSRF/owner checks that follow are never reached.
    assert resp.status_code == 404, (
        f"Unexpected status {resp.status_code}"
    )

    with app.app_context():
        doc_still = db.session.get(ApplicationDocument, f["doc_b_id"])
        assert doc_still is not None, (
            "Org B's document row was destroyed by Org A's delete"
        )
    assert os.path.exists(f["file_path"]), (
        "Org B's document file was deleted from disk by Org A's delete"
    )


def test_cross_tenant_download_refused_legacy_route(app, _two_org_fixture):
    """Org A's admin GETs /dashboard/documents/<B's doc>/download → refused."""
    f = _two_org_fixture
    client_a = _make_client(app, f["user_a_id"])

    resp = client_a.get(
        f"/dashboard/documents/{f['doc_b_id']}/download",
        follow_redirects=True,
    )
    # The query now filters by organization_id so a foreign document is simply
    # not found (404) — the file is never served.
    assert resp.status_code == 404, (
        f"Unexpected status {resp.status_code}"
    )


def test_cross_tenant_download_refused_unified_route(app, _two_org_fixture):
    """Org A's admin GETs /applications/documents/<B's doc>/download → refused."""
    f = _two_org_fixture
    client_a = _make_client(app, f["user_a_id"])

    resp = client_a.get(
        f"/applications/documents/{f['doc_b_id']}/download",
        follow_redirects=True,
    )
    # The query now filters by organization_id so a foreign document is simply
    # not found (404) — the file is never served.
    assert resp.status_code == 404, (
        f"Unexpected status {resp.status_code}"
    )


# ---------------------------------------------------------------------------
# verify_file_access fail-closed behaviour (D-1/D-2) and the analysis route
# (D-3). Unit-level checks of the helper, plus one integration check of the
# analysis route's tenant guard.
# ---------------------------------------------------------------------------


def test_verify_file_access_denies_missing_org_inside_request(app):
    """Inside a request, a caller with no tenant context is refused (not allowed)."""
    from flask import g

    from app.middleware.tenant_files import verify_file_access

    with app.test_request_context("/"):
        # current_org_id not set at all.
        assert verify_file_access(5) is False
        # current_org_id present but None.
        g.current_org_id = None
        assert verify_file_access(5) is False


def test_verify_file_access_denies_null_org_doc_to_non_admin(app):
    """A document whose organization_id is None is refused to a non-admin (D-2).

    The old helper granted access to an org-less document whenever the caller
    also lacked a tenant context. A non-admin must be refused either way.
    """
    from flask import g

    from app.middleware.tenant_files import verify_file_access

    with app.test_request_context("/"):
        # Non-admin caller WITH an organisation cannot read an org-less document.
        g.current_org_id = 5
        assert verify_file_access(None) is False

    with app.test_request_context("/"):
        # Non-admin caller with no organisation either.
        g.current_org_id = None
        assert verify_file_access(None) is False


def test_verify_file_access_allows_outside_request(app):
    """Outside a request (CLI / background job) access stays allowed, even for None."""
    from app.middleware.tenant_files import verify_file_access

    # Application context but no request context: matches CLI and background-job
    # callers, where there is no tenant context to enforce against.
    with app.app_context():
        assert verify_file_access(5) is True
        assert verify_file_access(None) is True


def test_analyze_refuses_org_b_document(app, _two_org_fixture):
    """Org A's user asks the analysis route to analyze Org B's document → refused."""
    f = _two_org_fixture
    client_a = _make_client(app, f["user_a_id"])

    # doc_b belongs to org B (its application_component_id is app_b, org B).
    # The URL uses app_a (org A) so org A's user passes the tenant-scoped parent
    # lookup, and the document-level organisation check must refuse the doc.
    resp = client_a.post(
        f"/dashboard/api/applications/{f['app_a_id']}/analyze-document",
        data={"document_id": str(f["doc_b_id"])},
    )
    # The query now filters by organization_id so a foreign document is simply
    # not found (404) rather than loaded and then denied by verify_file_access.
    assert resp.status_code == 404, (
        f"Expected 404 for Org B's document; got {resp.status_code}"
    )
    assert "Document not found" in resp.get_data(as_text=True)


# ---------------------------------------------------------------------------
# D-1: Platform administrator cross-org access
# ---------------------------------------------------------------------------


@pytest.fixture
def _platform_admin_fixture(app):
    """Create two orgs, a platform admin in org A, a tenant admin in org A,
    and a document in org B.

    Uses explicit commits so the data is visible to HTTP requests made through
    the test client.
    """
    import os as _os

    from app import db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.miscellaneous import ApplicationDocument
    from app.models.organization import Organization
    from app.models.user import User

    suffix = _uuid.uuid4().hex[:10]

    with app.app_context():
        org_a = Organization(
            name=f"Test pa-a {suffix}", slug=f"test-pa-a-{suffix}"
        )
        org_b = Organization(
            name=f"Test pa-b {suffix}", slug=f"test-pa-b-{suffix}"
        )
        db.session.add_all([org_a, org_b])
        db.session.flush()

        platform_admin = User(
            email=f"pa-{_uuid.uuid4().hex[:8]}@example.com",
            first_name="Platform",
            last_name="Admin",
            organization_id=org_a.id,
            confirmed=True,
            is_platform_admin=True,
        )
        tenant_admin = User(
            email=f"ta-{_uuid.uuid4().hex[:8]}@example.com",
            first_name="Tenant",
            last_name="Admin",
            organization_id=org_a.id,
            confirmed=True,
            is_platform_admin=False,
        )
        db.session.add_all([platform_admin, tenant_admin])
        db.session.flush()

        app_b = ApplicationComponent(
            name=f"App-B-{_uuid.uuid4().hex[:8]}",
            organization_id=org_b.id,
        )
        db.session.add(app_b)
        db.session.flush()

        # Create a real file on disk under the configured upload folder so the
        # path-traversal check in the download handler passes.
        upload_base = app.config.get("UPLOAD_FOLDER", "uploads")
        upload_dir = _os.path.join(upload_base, str(org_b.id), "documents")
        _os.makedirs(upload_dir, exist_ok=True)
        file_path = _os.path.join(upload_dir, f"test-{_uuid.uuid4().hex[:8]}.txt")
        with open(file_path, "w") as f:
            f.write("platform admin cross-org test file")

        doc_b = ApplicationDocument(
            organization_id=org_b.id,
            application_component_id=app_b.id,
            title="Org B Secret Document",
            file_name="secret.txt",
            file_extension="TXT",
            file_path=file_path,
            file_size=_os.path.getsize(file_path),
            uploaded_by="b-admin",
        )
        db.session.add(doc_b)
        db.session.flush()

        db.session.commit()

        ids = {
            "org_a_id": org_a.id,
            "org_b_id": org_b.id,
            "platform_admin_id": platform_admin.id,
            "tenant_admin_id": tenant_admin.id,
            "app_b_id": app_b.id,
            "doc_b_id": doc_b.id,
            "file_path": file_path,
        }

    yield ids


def test_platform_admin_can_download_cross_org_document(app, _platform_admin_fixture):
    """A platform administrator can download another organisation's document."""
    f = _platform_admin_fixture
    client_pa = _make_client(app, f["platform_admin_id"])

    resp = client_pa.get(
        f"/applications/documents/{f['doc_b_id']}/download",
        follow_redirects=False,
    )
    assert resp.status_code == 200, (
        f"Platform admin should reach org B's document; got {resp.status_code}"
    )


def test_tenant_admin_cannot_download_cross_org_document(app, _platform_admin_fixture):
    """A tenant administrator of organisation A cannot download org B's document."""
    f = _platform_admin_fixture
    client_ta = _make_client(app, f["tenant_admin_id"])

    resp = client_ta.get(
        f"/applications/documents/{f['doc_b_id']}/download",
        follow_redirects=True,
    )
    assert resp.status_code == 404, (
        f"Tenant admin must not reach org B's document; got {resp.status_code}"
    )
