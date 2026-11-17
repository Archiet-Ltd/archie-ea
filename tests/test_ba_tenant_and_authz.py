"""Tenant-isolation and authorisation regression tests for the four
Business-Architecture modules shipped in this deploy:

    app/modules/business_case/routes.py          (/business-case)
    app/modules/business_model_canvas/routes.py   (/business-model)
    app/modules/organization/routes.py            (/organization)
    app/modules/capabilities/routes/value_stream_routes.py (/value-streams)

WHY THIS FILE EXISTS
--------------------
tests/test_business_case.py, tests/test_business_model.py,
tests/test_organization.py and tests/test_value_streams.py are thorough on
model shape, service-level logic and Flask *route registration*
(``url_for(...)`` resolves / endpoint is in ``app.url_map``) — but none of
them ever dispatch a real HTTP request through a logged-in session. That
means, despite "112 passed", nothing in the suite proves:

  1. ``@login_required`` actually blocks an anonymous caller on these routes.
  2. Two different organizations cannot read/update/delete each other's
     business cases, canvases, RACI cells or value streams.
  3. Any of the destructive POST routes (create/update/delete) actually
     work end-to-end when called over HTTP by an authenticated user.

This file closes that gap using real ``app.test_client()`` requests with a
faked Flask-Login session (``sess["_user_id"] = str(user_id)``), the
standard Flask-Login test pattern. To keep these reliable in CI (this repo
deliberately avoids rendering full ``layouts/admin_base.html`` pages via
test_client — see business_case/not_found.html etc., which pull in sidebar
navigation/context processors not exercised anywhere else in the suite),
every test here only exercises JSON API endpoints and redirect-only form
endpoints, never a route that renders a full HTML page.

Every helper below hands back plain ids (never live ORM instances) across
a client.post()/get() call, because SQLAlchemy's default
``expire_on_commit=True`` plus each test-client request tearing down its
own scoped session means an ORM object created in one app/request context
is detached (and its attributes unreadable) by the time a *later* context
looks at it. Re-fetching by id in a fresh ``app.app_context()`` sidesteps
that entirely, and mirrors the "recreate/re-query, don't reuse handles
across context boundaries" style already implicit in the other test files
(each of which does all of its object access inside one single
``with app.test_request_context():`` block).

KEY FINDING pinned by TestValueStreamHasNoTenantIsolation below: the
ValueStream / ValueStreamStage / CapabilityValueStreamMapping /
UnifiedCapability models (app/models/unified_capability.py) do NOT inherit
TenantMixin and have no organization_id column at all, so the automatic
tenant-scoping SQLAlchemy event listener (app/middleware/tenant_isolation.py)
cannot and does not apply to them. Every route in value_stream_routes.py
therefore operates on a single globally-shared pool of data across every
organization on the platform. That test intentionally asserts today's
(vulnerable) behaviour so it goes red the moment someone fixes it — treat a
failure there as "great, now update/delete this test", not as a regression.
"""

import uuid

import pytest


@pytest.fixture(scope="module")
def app():
    from app import create_app, db

    app = create_app("testing")
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False

    with app.app_context():
        db.create_all()

    return app


@pytest.fixture
def client(app):
    return app.test_client()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_org_id(db, label):
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:8]
    org = Organization(name=f"{label} Org {suffix}", slug=f"{label.lower()}-org-{suffix}")
    db.session.add(org)
    db.session.flush()
    db.session.commit()
    return org.id


def _make_user_id(db, org_id, label, enterprise_role=None, role_name="Administrator"):
    """A real User row pinned to *org_id* via an explicit organization_id (the
    User.before_insert listener otherwise silently reassigns unset
    organization_id to the shared 'default' org — see app/models/user.py
    ``_assign_default_organization`` — which would defeat these tests).

    Deliberately leaves is_org_admin / is_platform_admin at their column
    default (False): per app/utils/rbac.py::_get_user_role, a user with both
    flags False and an enterprise_role outside the architect set maps to the
    lowest privilege tier, "viewer". None of these four modules' routes
    check role at all (only @login_required), so this "viewer" can still
    reach every create/update/delete endpoint below — see
    TestNoRoleGateOnDestructiveRoutes.
    """
    suffix = uuid.uuid4().hex[:8]
    from app.models.user import User

    user = User(
        email=f"{label.lower()}-{suffix}@example.com",
        first_name=label,
        last_name="Tester",
        organization_id=org_id,
        confirmed=True,
        enterprise_role=enterprise_role or "procurement",  # deliberately NOT an architect/admin role
    )
    db.session.add(user)
    db.session.flush()

    if role_name is not None:
        from app.models.user import Role

        role = Role.query.filter_by(name=role_name).first()
        if role is None:
            role = Role(name=role_name)
            db.session.add(role)
            db.session.flush()
        user.role = role

    db.session.commit()
    return user.id


def _grant_admin(db, user_id):
    """Attach the Administrator role, which require_roles normalises to "admin".

    Destructive routes are now gated with @require_roles("admin"). Without this,
    a cross-org delete test would be rejected by the ROLE check and never reach
    the tenant filter - passing for the wrong reason and proving nothing about
    isolation.
    """
    from app.models.user import Role, User

    role = Role.query.filter(Role.name.in_(("Administrator", "Admin", "admin"))).first()
    if role is None:  # roles not seeded in this test database
        role = Role(name="Administrator")
        db.session.add(role)
        db.session.flush()
    user = db.session.get(User, user_id)
    user.role = role
    db.session.commit()


def _login(client, user_id):
    """Switch the test client to *user_id*, and make the switch actually take.

    Setting the session cookie is the standard Flask-Login test pattern and is
    not sufficient here. pytest-flask pushes an app AND request context around
    every test using the `app` fixture, so `client.get()` reuses that context
    instead of pushing its own - and Flask-Login caches the resolved user on it
    as `g._login_user`, returning it without ever consulting the cookie.

    The consequence is specific and nasty. In a cross-tenant test the first
    login (the owner) resolves and caches; the second login (the attacker)
    changes the cookie and nothing else, so the "attacker" request runs AS THE
    OWNER - who can of course read their own record. The tests then reported a
    cross-org READ leak that cannot happen. Measured directly: the request saw
    session["_user_id"] == "735" (attacker) while current_user.id was still 734
    (owner), with id(g) identical across both requests. Verified against a real
    WSGI server over HTTP with two genuine logins, where the product correctly
    refuses with 404.

    A test that fails for a reason unrelated to what it asserts is worse than no
    test: it trains people to wave through red isolation results, in the one
    test class that would catch a genuine tenancy leak.

    The cleared set is the union of what both fixes for this bug identified:
    Flask-Login's two caches, plus the tenant context the isolation middleware
    keys off - stale org state would defeat the point of this module just as
    surely as a stale user.
    """
    from tests._session_test_helpers import mint_test_sid
    _sid = mint_test_sid(user_id, app=client.application)
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True
        if _sid:
            sess["_sid"] = _sid

    from flask import g, has_app_context

    if not has_app_context():
        return  # no lingering context; nothing cached to clear
    for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
        if hasattr(g, cached):
            delattr(g, cached)


def _cleanup_ids(db, model, ids):
    for i in ids:
        try:
            obj = db.session.get(model, i)
            if obj is not None:
                db.session.delete(obj)
                db.session.commit()
        except Exception:
            db.session.rollback()


# ---------------------------------------------------------------------------
# 1. login_required actually gates these routes (never verified before)
# ---------------------------------------------------------------------------


class TestLoginRequiredAcrossModules:
    """Anonymous requests must be redirected to the login page, never reach
    the view. Uses JSON-API/redirect-only endpoints (no template render)."""

    def test_anonymous_blocked_from_business_case_update(self, client):
        resp = client.post(
            "/business-case/1/update", json={"title": "x"}, follow_redirects=False
        )
        assert resp.status_code in (302, 401)
        if resp.status_code == 302:
            assert "/account/login" in resp.headers.get("Location", "")

    def test_anonymous_blocked_from_business_model_update(self, client):
        resp = client.post(
            "/business-model/1/update", json={"name": "x"}, follow_redirects=False
        )
        assert resp.status_code in (302, 401)
        if resp.status_code == 302:
            assert "/account/login" in resp.headers.get("Location", "")

    def test_anonymous_blocked_from_organization_raci_cell_save(self, client):
        resp = client.post(
            "/organization/raci/api/cell",
            json={
                "stakeholder_type": "user",
                "stakeholder_id": 1,
                "capability_id": 1,
                "raci": "A",
            },
            follow_redirects=False,
        )
        assert resp.status_code in (302, 401)
        if resp.status_code == 302:
            assert "/account/login" in resp.headers.get("Location", "")

    def test_anonymous_blocked_from_value_stream_mapping_api(self, client):
        resp = client.post(
            "/value-streams/api/mapping",
            json={"capability_id": 1, "value_stream_id": 1, "value_stream_stage_id": 1},
            follow_redirects=False,
        )
        assert resp.status_code in (302, 401)
        if resp.status_code == 302:
            assert "/account/login" in resp.headers.get("Location", "")


# ---------------------------------------------------------------------------
# 2. Business Case — tenant isolation over real HTTP + login
# ---------------------------------------------------------------------------


class TestBusinessCaseCrossOrgIsolation:
    def _seed(self, app, db):
        with app.app_context():
            org_a_id = _make_org_id(db, "BCIsoA")
            org_b_id = _make_org_id(db, "BCIsoB")
            user_a_id = _make_user_id(db, org_a_id, "BcOwnerA")
            user_b_id = _make_user_id(db, org_b_id, "BcAttackerB")

            from app.models.business_case import BusinessCase

            case = BusinessCase(title="Org A Confidential Case", organization_id=org_a_id)
            db.session.add(case)
            db.session.commit()
            case_id = case.id
        return org_a_id, org_b_id, user_a_id, user_b_id, case_id

    def _teardown(self, app, db, org_a_id, org_b_id, user_a_id, user_b_id, case_id):
        with app.app_context():
            from app.models.business_case import BusinessCase
            from app.models.organization import Organization
            from app.models.user import User

            _cleanup_ids(db, BusinessCase, [case_id])
            _cleanup_ids(db, User, [user_a_id, user_b_id])
            _cleanup_ids(db, Organization, [org_a_id, org_b_id])

    def test_cross_org_user_cannot_update_then_owner_can(self, app, client):
        from app import db

        org_a_id, org_b_id, user_a_id, user_b_id, case_id = self._seed(app, db)
        try:
            # Attacker: authenticated, but in a different org.
            _login(client, user_b_id)
            resp = client.post(
                f"/business-case/{case_id}/update",
                json={"title": "PWNED BY ORG B"},
            )
            assert resp.status_code == 404
            assert resp.get_json()["error"]["code"] == "NOT_FOUND"

            # The legitimate owner's data must be untouched.
            with app.app_context():
                from app.models.business_case import BusinessCase

                still_there = db.session.get(BusinessCase, case_id)
                assert still_there.title == "Org A Confidential Case"

            # Positive control: the actual owner CAN update it (proves the
            # 404 above is tenant scoping, not a broken route).
            _login(client, user_a_id)
            resp = client.post(
                f"/business-case/{case_id}/update",
                json={"title": "Updated By Owner"},
            )
            assert resp.status_code == 200
            assert resp.get_json()["data"]["title"] == "Updated By Owner"
        finally:
            self._teardown(app, db, org_a_id, org_b_id, user_a_id, user_b_id, case_id)

    def test_cross_org_user_cannot_save_field_or_pull_financials(self, app, client):
        from app import db

        org_a_id, org_b_id, user_a_id, user_b_id, case_id = self._seed(app, db)
        try:
            _login(client, user_b_id)

            resp = client.post(
                f"/business-case/{case_id}/api/field",
                json={"field": "problem_statement", "value": "pwned"},
            )
            assert resp.status_code == 404

            resp = client.post(f"/business-case/{case_id}/pull-financials", json={})
            assert resp.status_code == 404
        finally:
            self._teardown(app, db, org_a_id, org_b_id, user_a_id, user_b_id, case_id)

    def test_cross_org_user_cannot_delete_business_case(self, app, client):
        from app import db

        org_a_id, org_b_id, user_a_id, user_b_id, case_id = self._seed(app, db)
        try:
            _login(client, user_b_id)
            resp = client.post(f"/business-case/{case_id}/delete")
            assert resp.status_code == 404

            with app.app_context():
                from app.models.business_case import BusinessCase

                assert db.session.get(BusinessCase, case_id) is not None
        finally:
            self._teardown(app, db, org_a_id, org_b_id, user_a_id, user_b_id, case_id)


# ---------------------------------------------------------------------------
# 3. Business Model Canvas — tenant isolation over real HTTP + login
# ---------------------------------------------------------------------------


class TestBusinessModelCanvasCrossOrgIsolation:
    def _seed(self, app, db):
        with app.app_context():
            org_a_id = _make_org_id(db, "BmcIsoA")
            org_b_id = _make_org_id(db, "BmcIsoB")
            user_a_id = _make_user_id(db, org_a_id, "BmcOwnerA")
            user_b_id = _make_user_id(db, org_b_id, "BmcAttackerB")

            from app.models.business_model import BusinessModelCanvas

            canvas = BusinessModelCanvas(name="Org A Strategy Canvas", organization_id=org_a_id)
            db.session.add(canvas)
            db.session.commit()
            canvas_id = canvas.id
        return org_a_id, org_b_id, user_a_id, user_b_id, canvas_id

    def _teardown(self, app, db, org_a_id, org_b_id, user_a_id, user_b_id, canvas_id):
        with app.app_context():
            from app.models.business_model import BusinessModelCanvas
            from app.models.organization import Organization
            from app.models.user import User

            _cleanup_ids(db, BusinessModelCanvas, [canvas_id])
            _cleanup_ids(db, User, [user_a_id, user_b_id])
            _cleanup_ids(db, Organization, [org_a_id, org_b_id])

    def test_cross_org_user_cannot_update_or_save_block_then_owner_can(self, app, client):
        from app import db

        org_a_id, org_b_id, user_a_id, user_b_id, canvas_id = self._seed(app, db)
        try:
            _login(client, user_b_id)

            resp = client.post(
                f"/business-model/{canvas_id}/update", json={"name": "PWNED"}
            )
            assert resp.status_code == 404

            resp = client.post(
                f"/business-model/{canvas_id}/entries/add",
                data={"box": "key_partners", "name": "Attacker partner"},
            )
            assert resp.status_code == 404

            with app.app_context():
                from app.models.archimate_core import ArchiMateElement
                from app.models.business_model import BusinessModelCanvas

                canvas = db.session.get(BusinessModelCanvas, canvas_id)
                assert canvas.name == "Org A Strategy Canvas"
                assert ArchiMateElement.query.filter_by(name="Attacker partner").count() == 0

            # Positive control: the real owner succeeds.
            _login(client, user_a_id)
            resp = client.post(
                f"/business-model/{canvas_id}/update", json={"name": "Renamed By Owner"}
            )
            assert resp.status_code == 200
            assert resp.get_json()["data"]["name"] == "Renamed By Owner"
        finally:
            self._teardown(app, db, org_a_id, org_b_id, user_a_id, user_b_id, canvas_id)

    def test_cross_org_user_cannot_delete_canvas(self, app, client):
        from app import db

        org_a_id, org_b_id, user_a_id, user_b_id, canvas_id = self._seed(app, db)
        try:
            _login(client, user_b_id)
            resp = client.post(f"/business-model/{canvas_id}/delete")
            assert resp.status_code == 404

            with app.app_context():
                from app.models.business_model import BusinessModelCanvas

                assert db.session.get(BusinessModelCanvas, canvas_id) is not None
        finally:
            self._teardown(app, db, org_a_id, org_b_id, user_a_id, user_b_id, canvas_id)


# ---------------------------------------------------------------------------
# 4. Organization RACI matrix — tenant isolation over real HTTP + login
# ---------------------------------------------------------------------------


class TestOrganizationRaciCrossOrgIsolation:
    def test_cross_org_user_cannot_delete_another_orgs_raci_cell(self, app, client):
        from app import db

        with app.app_context():
            org_a_id = _make_org_id(db, "RaciIsoA")
            org_b_id = _make_org_id(db, "RaciIsoB")
            user_a_id = _make_user_id(db, org_a_id, "RaciOwnerA")
            user_b_id = _make_user_id(db, org_b_id, "RaciAttackerB")

            # EnterpriseRaciAssignment.capability_id is a real FK to
            # unified_capabilities.id (ON DELETE CASCADE) — needs a real row.
            from app.models.unified_capability import UnifiedCapability

            suffix = uuid.uuid4().hex[:8]
            capability = UnifiedCapability(
                name=f"RACI Test Capability {suffix}", code=f"RTC-{suffix}", level=1
            )
            db.session.add(capability)
            db.session.commit()
            capability_id = capability.id

        try:
            # Org A creates the cell over real HTTP.
            _login(client, user_a_id)
            resp = client.post(
                "/organization/raci/api/cell",
                json={
                    "stakeholder_type": "user",
                    "stakeholder_id": user_a_id,
                    "stakeholder_name": "Owner A",
                    "capability_id": capability_id,
                    "raci": "A",
                },
            )
            assert resp.status_code == 200

            # Org B tries to delete the SAME (stakeholder_type, stakeholder_id,
            # capability_id) cell — must not find/affect org A's row.
            _login(client, user_b_id)
            resp = client.delete(
                "/organization/raci/api/cell",
                json={
                    "stakeholder_type": "user",
                    "stakeholder_id": user_a_id,
                    "capability_id": capability_id,
                },
            )
            assert resp.status_code == 404

            # Confirm org A's assignment is still intact.
            with app.app_context():
                from app.models.organization_model import EnterpriseRaciAssignment

                assignment = EnterpriseRaciAssignment.query.filter_by(
                    stakeholder_type="user", stakeholder_id=user_a_id, capability_id=capability_id
                ).first()
                assert assignment is not None
                assert assignment.raci == "A"
        finally:
            with app.app_context():
                from app.models.organization import Organization
                from app.models.organization_model import EnterpriseRaciAssignment
                from app.models.unified_capability import UnifiedCapability
                from app.models.user import User

                leftover = EnterpriseRaciAssignment.query.filter_by(
                    capability_id=capability_id
                ).all()
                _cleanup_ids(db, EnterpriseRaciAssignment, [a.id for a in leftover])
                _cleanup_ids(db, UnifiedCapability, [capability_id])
                _cleanup_ids(db, User, [user_a_id, user_b_id])
                _cleanup_ids(db, Organization, [org_a_id, org_b_id])


# ---------------------------------------------------------------------------
# 5. Value Stream module — NO tenant isolation at all (KNOWN GAP, pinned)
# ---------------------------------------------------------------------------


class TestValueStreamCrossOrgIsolation:
    """Value streams must be scoped to a single organisation.

    Regression test for the gap found on 2026-07-30. ValueStream,
    ValueStreamStage and CapabilityValueStreamMapping shipped with no
    organization_id at all. The tenant filter in
    app/middleware/tenant_isolation.py works by attaching
    ``organization_id == g.current_org_id`` to SELECTs on TenantMixin models, so
    a model without that column could not be filtered even in principle - any
    authenticated user of any tenant could read, rename and delete any other
    tenant's value streams. Those models now use TenantMixin.

    Assertions are on DATABASE STATE rather than HTTP status, because the
    value-stream routes catch exceptions broadly and redirect rather than
    returning 404. What matters is that org B's request cannot change org A's row.
    """

    def test_cross_org_user_cannot_read_edit_or_delete_another_orgs_value_stream(self, app, client):
        from app import db

        with app.app_context():
            org_a_id = _make_org_id(db, "VsIsoA")
            org_b_id = _make_org_id(db, "VsIsoB")
            user_a_id = _make_user_id(db, org_a_id, "VsOwnerA")
            user_b_id = _make_user_id(db, org_b_id, "VsAttackerB")
            # Give the ATTACKER admin rights deliberately, so that if the request
            # is blocked it can only be tenancy doing it, not the role gate.
            _grant_admin(db, user_b_id)

        vs_id = None
        try:
            suffix = uuid.uuid4().hex[:8]
            original_name = "Org A Value Stream " + suffix

            _login(client, user_a_id)
            resp = client.post(
                "/value-streams/create",
                data={"name": original_name, "code": "VSA-" + suffix},
                follow_redirects=False,
            )
            assert resp.status_code == 302, "owner should be able to create"
            vs_id = int(resp.headers["Location"].rstrip("/").rsplit("/", 1)[-1])

            # org B must not READ it
            _login(client, user_b_id)
            resp = client.get("/value-streams/%d/grid" % vs_id)
            if resp.status_code == 200:
                payload = resp.get_json() or {}
                leaked = (payload.get("value_stream") or {}).get("id")
                assert leaked != vs_id, "cross-org READ leak"

            # org B must not RENAME it
            client.post(
                "/value-streams/%d/edit" % vs_id,
                data={"name": "Renamed By A Different Organization"},
                follow_redirects=False,
            )
            with app.app_context():
                from app.models.unified_capability import ValueStream

                vs = db.session.get(ValueStream, vs_id)
                assert vs is not None and vs.name == original_name, "cross-org WRITE leak"

            # org B must not DELETE it
            client.post("/value-streams/%d/delete" % vs_id, follow_redirects=False)
            with app.app_context():
                from app.models.unified_capability import ValueStream

                assert db.session.get(ValueStream, vs_id) is not None, "cross-org DELETE leak"

            # positive control: the genuine owner, with the required role, can
            with app.app_context():
                _grant_admin(db, user_a_id)
            _login(client, user_a_id)
            resp = client.post("/value-streams/%d/delete" % vs_id, follow_redirects=False)
            assert resp.status_code == 302
            with app.app_context():
                from app.models.unified_capability import ValueStream

                assert db.session.get(ValueStream, vs_id) is None, "owner delete should succeed"
                vs_id = None
        finally:
            with app.app_context():
                from app.models.organization import Organization
                from app.models.unified_capability import ValueStream
                from app.models.user import User

                if vs_id is not None:
                    _cleanup_ids(db, ValueStream, [vs_id])
                _cleanup_ids(db, User, [user_a_id, user_b_id])
                _cleanup_ids(db, Organization, [org_a_id, org_b_id])


# ---------------------------------------------------------------------------
# 6. No role gate on destructive routes (authorisation gap, pinned)
# ---------------------------------------------------------------------------


class TestRoleGateOnDestructiveRoutes:
    """Destructive routes must require a privileged role.

    Regression test for the gap found on 2026-07-30: all four
    Business-Architecture modules guarded every mutating route with
    @login_required alone, so the lowest-privilege user could delete records.
    The sibling app/modules/capabilities/routes/enterprise_crud_routes.py
    already gated deletes with @require_roles("admin"); these modules now match.
    """

    def test_lowest_privilege_user_cannot_delete_a_business_case(self, app, client):
        from app import db

        with app.app_context():
            org_id = _make_org_id(db, "RbacGate")
            user_id = _make_user_id(
                db, org_id, "ViewerRole", enterprise_role="procurement", role_name="Viewer"
            )

            from app.models.business_case import BusinessCase

            case = BusinessCase(title="Should Survive", organization_id=org_id)
            db.session.add(case)
            db.session.commit()
            case_id = case.id

        try:
            _login(client, user_id)
            resp = client.post("/business-case/%d/delete" % case_id, follow_redirects=False)
            assert resp.status_code == 403, "expected 403, got %s" % resp.status_code

            with app.app_context():
                from app.models.business_case import BusinessCase

                assert db.session.get(BusinessCase, case_id) is not None, "record deleted anyway"
        finally:
            with app.app_context():
                from app.models.business_case import BusinessCase
                from app.models.organization import Organization
                from app.models.user import User

                _cleanup_ids(db, BusinessCase, [case_id])
                _cleanup_ids(db, User, [user_id])
                _cleanup_ids(db, Organization, [org_id])
