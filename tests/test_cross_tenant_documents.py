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


def _csrf_token(client, app):
    """Generate a CSRF token and store it in the client's session.

    Returns the signed token string to pass as the ``csrf_token`` form field.
    """
    import hashlib
    import os as _os

    from itsdangerous import URLSafeTimedSerializer

    raw_token = hashlib.sha256(_os.urandom(64)).hexdigest()
    with client.session_transaction() as sess:
        sess["csrf_token"] = raw_token
    s = URLSafeTimedSerializer(app.secret_key, salt="wtf-csrf-token")
    signed = s.dumps(raw_token)
    _clear_g_cache()
    return signed


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


@pytest.fixture
def _same_org_no_permission_fixture(app):
    """One organisation, a document in it, and a caller with no write
    permission in that same organisation (Viewer role, permissions=0).

    Role rows are seeded by Role.insert_roles() in normal deploys; a fresh
    test database may not have run it, so create-if-missing the same way
    tests/test_r32_ai_permission_gate.py's _make_user helper does.

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
    from app.models.user import Permission, Role, User

    suffix = _uuid.uuid4().hex[:10]

    with app.app_context():
        org = Organization(
            name=f"Test viewer-org {suffix}", slug=f"test-viewer-org-{suffix}"
        )
        db.session.add(org)
        db.session.flush()

        role = Role.query.filter_by(name="Viewer").first()
        if role is None:
            role = Role(name="Viewer", permissions=0, index="main", default=False)
            db.session.add(role)
            db.session.flush()

        viewer = User(
            email=f"viewer-{_uuid.uuid4().hex[:8]}@example.com",
            first_name="Viewer",
            last_name="NoWrite",
            organization_id=org.id,
            confirmed=True,
            enterprise_role="solution_architect",
        )
        viewer.role = role
        db.session.add(viewer)
        db.session.flush()

        app_component = ApplicationComponent(
            name=f"App-Viewer-{_uuid.uuid4().hex[:8]}",
            organization_id=org.id,
        )
        db.session.add(app_component)
        db.session.flush()

        upload_dir = _os.path.join(
            app.instance_path, "uploads", str(org.id), "documents"
        )
        _os.makedirs(upload_dir, exist_ok=True)
        file_path = _os.path.join(upload_dir, f"test-{_uuid.uuid4().hex[:8]}.txt")
        with open(file_path, "w") as fh:
            fh.write("same-organisation, no write permission test file")

        doc = ApplicationDocument(
            organization_id=org.id,
            application_component_id=app_component.id,
            title="Same-org document",
            file_name="same-org.txt",
            file_extension="TXT",
            file_path=file_path,
            file_size=_os.path.getsize(file_path),
            uploaded_by="someone-else",
        )
        db.session.add(doc)
        db.session.flush()

        db.session.commit()

        ids = {
            "org_id": org.id,
            "viewer_id": viewer.id,
            "doc_id": doc.id,
            "file_path": file_path,
        }

    yield ids


def test_delete_refused_same_org_without_general_permission(
    app, _same_org_no_permission_fixture
):
    """A same-organisation caller without Permission.GENERAL is refused (403),
    not the 404 an out-of-tenant or missing document gets, and the document
    is left in place.
    """
    from app import db
    from app.models.miscellaneous import ApplicationDocument

    f = _same_org_no_permission_fixture
    client_viewer = _make_client(app, f["viewer_id"])

    resp = client_viewer.post(
        f"/applications/documents/{f['doc_id']}/delete",
        data={"csrf_token": "test-bypass"},
    )
    assert resp.status_code == 403, (
        f"Unexpected status {resp.status_code}"
    )

    with app.app_context():
        doc_still = db.session.get(ApplicationDocument, f["doc_id"])
        assert doc_still is not None, (
            "Document was destroyed despite the caller lacking write permission"
        )
    assert os.path.exists(f["file_path"]), (
        "Document file was deleted from disk despite the caller lacking write permission"
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


def test_analyze_document_id_non_integer_returns_400(app, _two_org_fixture):
    """A non-integer document_id is refused at the edge, before any query.

    Under psycopg 3 a string id reaching the ApplicationDocument query makes
    PostgreSQL refuse to compare an integer column to varchar (a 500), rather
    than a clean 400.
    """
    f = _two_org_fixture
    client_a = _make_client(app, f["user_a_id"])

    resp = client_a.post(
        f"/dashboard/api/applications/{f['app_a_id']}/analyze-document",
        data={"document_id": "not-an-id"},
    )
    assert resp.status_code == 400, (
        f"Expected 400 for a non-integer document_id; got {resp.status_code}"
    )


def test_analyze_document_id_integer_org_b_returns_404(app, _two_org_fixture):
    """A well-formed integer id for another organisation's document still 404s.

    Guards against the edge-parsing fix above swallowing a valid integer id
    into the 400 path instead of letting it reach the existing organisation
    scoping check.
    """
    f = _two_org_fixture
    client_a = _make_client(app, f["user_a_id"])

    doc_id = f["doc_b_id"]
    assert isinstance(doc_id, int)

    resp = client_a.post(
        f"/dashboard/api/applications/{f['app_a_id']}/analyze-document",
        data={"document_id": str(doc_id)},
    )
    assert resp.status_code == 404, (
        f"Expected 404 for Org B's document; got {resp.status_code}"
    )


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


# ---------------------------------------------------------------------------
# Ownership within one organisation: identity, not display name
# ---------------------------------------------------------------------------
# The defect: delete_document_file compared doc.uploaded_by (a display string)
# to current_user.full_name(), so two users with the same name were
# indistinguishable and a user could rename themselves to match.
#
# Fix: uploaded_by_id (FK to users.id) is set at upload; the delete route
# checks doc.uploaded_by_id == current_user.id. Documents uploaded before
# the column existed have uploaded_by_id=None and can be deleted only by
# admin roles (fail closed).
#
# Test (a): user A2, same org and same name as A1, cannot delete A1's doc.
# Test (b): A1 can delete their own document.
# Test (c): a legacy row with uploaded_by_id=None cannot be deleted by a
#           non-admin whose display name matches uploaded_by.


@pytest.fixture
def _same_name_fixture(app):
    """One organisation, two users with identical first/last names, a
    document uploaded by user A1 (with uploaded_by_id set), and a legacy
    document with uploaded_by_id=None.

    Uses explicit commits so the data is visible to HTTP requests made
    through the test client.
    """
    import os as _os

    from app import db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.miscellaneous import ApplicationDocument
    from app.models.organization import Organization
    from app.models.user import Permission, Role, User

    suffix = _uuid.uuid4().hex[:10]

    with app.app_context():
        org = Organization(
            name=f"Test same-name {suffix}", slug=f"test-same-name-{suffix}"
        )
        db.session.add(org)
        db.session.flush()

        # Ensure the "User" role exists (it carries GENERAL permission).
        user_role = Role.query.filter_by(name="User").first()
        if user_role is None:
            user_role = Role(
                name="User", permissions=Permission.GENERAL, index="main", default=False
            )
            db.session.add(user_role)
            db.session.flush()

        # Ensure the "Administrator" role exists (carries ADMINISTER permission).
        admin_role = Role.query.filter_by(name="Administrator").first()
        if admin_role is None:
            admin_role = Role(
                name="Administrator", permissions=Permission.ADMINISTER, index="main", default=False
            )
            db.session.add(admin_role)
            db.session.flush()

        # Two users with the SAME first and last name, both User role.
        a1 = User(
            email=f"a1-{_uuid.uuid4().hex[:8]}@example.com",
            first_name="Same",
            last_name="Name",
            organization_id=org.id,
            confirmed=True,
            enterprise_role="solution_architect",
        )
        a1.role = user_role
        db.session.add(a1)
        db.session.flush()

        a2 = User(
            email=f"a2-{_uuid.uuid4().hex[:8]}@example.com",
            first_name="Same",
            last_name="Name",
            organization_id=org.id,
            confirmed=True,
            enterprise_role="solution_architect",
        )
        a2.role = user_role
        db.session.add(a2)
        db.session.flush()

        # Administrator user in the same organisation.
        admin_user = User(
            email=f"admin-{_uuid.uuid4().hex[:8]}@example.com",
            first_name="Admin",
            last_name="User",
            organization_id=org.id,
            confirmed=True,
            enterprise_role="solution_architect",
        )
        admin_user.role = admin_role
        db.session.add(admin_user)
        db.session.flush()

        app_component = ApplicationComponent(
            name=f"App-SameName-{_uuid.uuid4().hex[:8]}",
            organization_id=org.id,
        )
        db.session.add(app_component)
        db.session.flush()

        # Document uploaded by A1 (with uploaded_by_id).
        upload_dir = _os.path.join(
            app.instance_path, "uploads", str(org.id), "documents"
        )
        _os.makedirs(upload_dir, exist_ok=True)
        file_path_a1 = _os.path.join(upload_dir, f"test-a1-{_uuid.uuid4().hex[:8]}.txt")
        with open(file_path_a1, "w") as fh:
            fh.write("A1's document")
        file_size_a1 = _os.path.getsize(file_path_a1)

        doc_a1 = ApplicationDocument(
            organization_id=org.id,
            application_component_id=app_component.id,
            title="A1 Document",
            file_name="a1-doc.txt",
            file_extension="TXT",
            file_path=file_path_a1,
            file_size=file_size_a1,
            uploaded_by=a1.full_name(),
            uploaded_by_id=a1.id,
        )
        db.session.add(doc_a1)
        db.session.flush()

        # Legacy document with uploaded_by_id=None (uploaded_by matches A2's name).
        file_path_legacy = _os.path.join(
            upload_dir, f"test-legacy-{_uuid.uuid4().hex[:8]}.txt"
        )
        with open(file_path_legacy, "w") as fh:
            fh.write("Legacy document")
        file_size_legacy = _os.path.getsize(file_path_legacy)

        doc_legacy = ApplicationDocument(
            organization_id=org.id,
            application_component_id=app_component.id,
            title="Legacy Document",
            file_name="legacy-doc.txt",
            file_extension="TXT",
            file_path=file_path_legacy,
            file_size=file_size_legacy,
            uploaded_by=a2.full_name(),
            uploaded_by_id=None,
        )
        db.session.add(doc_legacy)
        db.session.flush()

        db.session.commit()

        ids = {
            "org_id": org.id,
            "a1_id": a1.id,
            "a2_id": a2.id,
            "admin_id": admin_user.id,
            "app_id": app_component.id,
            "doc_a1_id": doc_a1.id,
            "doc_a1_path": file_path_a1,
            "doc_legacy_id": doc_legacy.id,
            "doc_legacy_path": file_path_legacy,
        }

    yield ids


def test_same_name_user_cannot_delete_others_document(app, _same_name_fixture):
    """(a) User A2, same organisation and same name as A1, cannot delete
    A1's document — the delete is refused and the row and file survive."""
    from app import db
    from app.models.miscellaneous import ApplicationDocument

    f = _same_name_fixture
    client_a2 = _make_client(app, f["a2_id"])
    token = _csrf_token(client_a2, app)

    resp = client_a2.post(
        f"/applications/documents/{f['doc_a1_id']}/delete",
        data={"csrf_token": token},
    )
    # A2 is not the uploader (uploaded_by_id != A2.id) and not an admin
    # (User role), so the ownership check redirects with a flash.
    assert resp.status_code == 302, (
        f"Expected redirect (refused); got {resp.status_code}"
    )

    with app.app_context():
        doc_still = db.session.get(ApplicationDocument, f["doc_a1_id"])
        assert doc_still is not None, (
            "A1's document row was destroyed by A2 (same name, different user)"
        )
    assert os.path.exists(f["doc_a1_path"]), (
        "A1's document file was deleted from disk by A2 (same name, different user)"
    )


def test_uploader_can_delete_own_document(app, _same_name_fixture):
    """(b) A User-role (non-admin) uploader deletes their own document —
    the row is removed and the file is deleted from disk."""
    from app import db
    from app.models.miscellaneous import ApplicationDocument

    f = _same_name_fixture
    client_a1 = _make_client(app, f["a1_id"])
    token = _csrf_token(client_a1, app)

    resp = client_a1.post(
        f"/applications/documents/{f['doc_a1_id']}/delete",
        data={"csrf_token": token},
    )
    # A1 is the uploader (uploaded_by_id matches), so the delete succeeds.
    assert resp.status_code == 302, (
        f"Expected redirect (success); got {resp.status_code}"
    )

    with app.app_context():
        doc_gone = db.session.get(ApplicationDocument, f["doc_a1_id"])
        assert doc_gone is None, (
            "A1's document row should have been deleted"
        )
    assert not os.path.exists(f["doc_a1_path"]), (
        "A1's document file should have been deleted from disk"
    )


def test_legacy_document_without_uploader_id_cannot_be_deleted_by_name_match(
    app, _same_name_fixture
):
    """(c) A legacy row with uploaded_by_id=None and uploaded_by matching
    A2's display name: A2's delete is refused."""
    from app import db
    from app.models.miscellaneous import ApplicationDocument

    f = _same_name_fixture
    client_a2 = _make_client(app, f["a2_id"])
    token = _csrf_token(client_a2, app)

    resp = client_a2.post(
        f"/applications/documents/{f['doc_legacy_id']}/delete",
        data={"csrf_token": token},
    )
    # A2 is not the uploader (uploaded_by_id is None) and not an admin,
    # so the ownership check redirects with a flash.
    assert resp.status_code == 302, (
        f"Expected redirect (refused); got {resp.status_code}"
    )

    with app.app_context():
        doc_still = db.session.get(ApplicationDocument, f["doc_legacy_id"])
        assert doc_still is not None, (
            "Legacy document row was destroyed by A2 despite uploaded_by_id=None"
        )
    assert os.path.exists(f["doc_legacy_path"]), (
        "Legacy document file was deleted from disk by A2 despite uploaded_by_id=None"
    )


def test_administrator_can_delete_legacy_and_others_document_unified_route(
    app, _same_name_fixture
):
    """(d) An Administrator (role with ADMINISTER, same organisation) deletes a
    legacy row and another user's row on the unified route — both succeed."""
    from app import db
    from app.models.miscellaneous import ApplicationDocument

    f = _same_name_fixture
    client_admin = _make_client(app, f["admin_id"])
    token = _csrf_token(client_admin, app)

    # Delete the legacy document (uploaded_by_id=None).
    resp = client_admin.post(
        f"/applications/documents/{f['doc_legacy_id']}/delete",
        data={"csrf_token": token},
    )
    assert resp.status_code == 302, (
        f"Expected redirect (admin deletes legacy); got {resp.status_code}"
    )
    with app.app_context():
        doc_gone = db.session.get(ApplicationDocument, f["doc_legacy_id"])
        assert doc_gone is None, (
            "Administrator should be able to delete a legacy document"
        )

    # Delete A1's document (uploaded_by_id=A1, not admin).
    token2 = _csrf_token(client_admin, app)
    resp2 = client_admin.post(
        f"/applications/documents/{f['doc_a1_id']}/delete",
        data={"csrf_token": token2},
    )
    assert resp2.status_code == 302, (
        f"Expected redirect (admin deletes another user's doc); got {resp2.status_code}"
    )
    with app.app_context():
        doc_gone2 = db.session.get(ApplicationDocument, f["doc_a1_id"])
        assert doc_gone2 is None, (
            "Administrator should be able to delete another user's document"
        )


def test_legacy_route_ownership_check(app, _same_name_fixture):
    """(e) On the legacy route /dashboard/documents/<id>/delete, a non-admin
    who is not the uploader is refused (row and file survive); the uploader
    succeeds."""
    from app import db
    from app.models.miscellaneous import ApplicationDocument

    f = _same_name_fixture

    # A2 (non-admin, not the uploader) is refused.
    client_a2 = _make_client(app, f["a2_id"])
    resp = client_a2.post(f"/dashboard/documents/{f['doc_a1_id']}/delete")
    # Refused with redirect (flash).
    assert resp.status_code == 302, (
        f"Expected redirect (refused); got {resp.status_code}"
    )
    with app.app_context():
        doc_still = db.session.get(ApplicationDocument, f["doc_a1_id"])
        assert doc_still is not None, (
            "A2 (non-admin, not uploader) should not delete via legacy route"
        )
    assert os.path.exists(f["doc_a1_path"]), (
        "A2 should not delete file via legacy route"
    )

    # A1 (uploader) succeeds on the legacy route.
    client_a1 = _make_client(app, f["a1_id"])
    resp2 = client_a1.post(f"/dashboard/documents/{f['doc_a1_id']}/delete")
    assert resp2.status_code == 302, (
        f"Expected redirect (uploader succeeds); got {resp2.status_code}"
    )
    with app.app_context():
        doc_gone = db.session.get(ApplicationDocument, f["doc_a1_id"])
        assert doc_gone is None, (
            "A1 (uploader) should be able to delete their own document via legacy route"
        )


def test_real_upload_saves_organization_and_uploader_id(app, _same_name_fixture):
    """(f) A real upload through the unified route by a signed-in user creates
    exactly one row with organization_id equal to the application's and
    uploaded_by_id equal to the user's id."""
    import io

    from app import db
    from app.models.miscellaneous import ApplicationDocument

    f = _same_name_fixture
    client_a1 = _make_client(app, f["a1_id"])
    token = _csrf_token(client_a1, app)

    upload_data = {
        "csrf_token": token,
        "title": "Test upload for org-id check",
        "file": (io.BytesIO(b"test content for upload"), "test-upload.txt"),
    }
    resp = client_a1.post(
        f"/applications/{f['app_id']}/upload-document",
        data=upload_data,
        content_type="multipart/form-data",
    )
    assert resp.status_code == 302, (
        f"Expected redirect (upload success); got {resp.status_code}"
    )

    with app.app_context():
        doc = ApplicationDocument.query.filter_by(
            application_component_id=f["app_id"],
            title="Test upload for org-id check",
        ).first()
        assert doc is not None, "Uploaded document row should exist"
        assert doc.organization_id is not None, (
            "organization_id must not be None"
        )
        assert doc.organization_id == f["org_id"], (
            f"Expected organization_id={f['org_id']}, got {doc.organization_id}"
        )
        assert doc.uploaded_by_id is not None, (
            "uploaded_by_id must not be None"
        )
        assert doc.uploaded_by_id == f["a1_id"], (
            f"Expected uploaded_by_id={f['a1_id']}, got {doc.uploaded_by_id}"
        )
