"""PR 430 split (fixed routes only, no guard machinery): D-03 and D-05 from
the lead's follow-up review (``review-pr430-v2.md``).

D-03: a sweep for unguarded raw-SQL/bulk writes on genuinely global tables
found three real gaps beyond the originally-named duplicate-detection
cleanup route --
``acm_hybrid_routes``'s seed/update/delete on the shared
``technical_capabilities`` catalogue, and
``UnifiedDuplicateDetectionService.delete_duplicates_keep_one`` deleting a
foreign organisation's real ``ApplicationComponent`` rows via a
guessed/foreign ``group_id`` (``UnifiedDuplicateGroup`` carries no
``organization_id`` at all).

D-05: the v1 admin blueprint's user-management routes judged admin authority
as "admin of ANY organisation" rather than the ACTIVE session organisation,
so switching active org exposed another organisation's users to someone who
is only an admin at home.
"""

from __future__ import annotations

import uuid

import pytest


def _user(db_session, org, *, platform=False, org_admin=True):
    from app.models import Role
    from app.models.user import User

    role = Role.query.filter_by(name="Administrator" if org_admin else "Architect").first()
    if role is None:
        pytest.skip("no seeded role matches in this database")
    user = User(
        email=f"d03d05-{uuid.uuid4().hex[:8]}@example.test",
        first_name="D03D05",
        last_name="Tester",
        organization_id=org.id,
        confirmed=True,
        role=role,
    )
    user.password = uuid.uuid4().hex
    user.is_org_admin = org_admin
    user.is_platform_admin = platform
    db_session.add(user)
    db_session.flush()
    return user


def _login(db_session, client, login_as, user_id):
    from app.models.user import User

    db_session.expunge_all()
    login_as(client, db_session.get(User, user_id))


# ---------------------------------------------------------------------------
# D-03: duplicate-detection cleanup (v1 + v2) and acm_hybrid, platform-admin-only
# ---------------------------------------------------------------------------

# app/api/acm_hybrid_routes.py's acm_hybrid_bp is not registered by
# app/_bootstrap/blueprints.py on this branch or on main (confirmed via the
# running app's url_map -- /api/acm-hybrid/* 404s rather than reaching the
# view at all, independently of this split's platform_admin_required fix).
# That gap pre-exists this change and is out of scope here; the acm_hybrid
# routes are still covered directly below, not through the HTTP layer.
D03_PLATFORM_ADMIN_ONLY_ROUTES = [
    ("post", "/duplicate-detection/simple/cleanup"),
]


@pytest.mark.parametrize("method,path", D03_PLATFORM_ADMIN_ONLY_ROUTES)
def test_d03_global_catalogue_write_refuses_a_tenant_administrator(
    app, db_session, make_org, client, login_as, method, path
):
    org_a = make_org("d03-a")
    org_b = make_org("d03-b")
    admin_a = _user(db_session, org_a)
    admin_b = _user(db_session, org_b)
    db_session.commit()

    for admin_id in (admin_a.id, admin_b.id):
        _login(db_session, client, login_as, admin_id)
        response = getattr(client, method)(path)
        assert response.status_code == 403, (
            f"{method.upper()} {path} should refuse a plain tenant admin; "
            f"got {response.status_code}"
        )


@pytest.mark.parametrize("method,path", D03_PLATFORM_ADMIN_ONLY_ROUTES)
def test_d03_global_catalogue_write_admits_a_platform_administrator(
    app, db_session, make_org, client, login_as, method, path
):
    org = make_org("d03-platform")
    platform_admin = _user(db_session, org, platform=True)
    db_session.commit()

    _login(db_session, client, login_as, platform_admin.id)
    response = getattr(client, method)(path)

    assert response.status_code not in (401, 403), (
        f"{method.upper()} {path} refused a genuine platform admin "
        f"({response.status_code})"
    )


def test_d03_acm_hybrid_seed_requires_platform_admin(app, db_session, make_org):
    """acm_hybrid_bp is not wired into app/_bootstrap/blueprints.py (pre-existing,
    out of scope here -- see the note above D03_PLATFORM_ADMIN_ONLY_ROUTES), so
    this calls the decorated view directly rather than through the HTTP layer."""
    from flask import g
    from flask_login import login_user

    from app.api.acm_hybrid_routes import seed_capabilities

    org_a = make_org("d03-acm-a")
    tenant_admin = _user(db_session, org_a)
    platform_admin = _user(db_session, make_org("d03-acm-platform"), platform=True)
    db_session.commit()

    with app.test_request_context("/api/acm-hybrid/seed", method="POST"):
        login_user(tenant_admin)
        g.current_org_id = org_a.id
        result = seed_capabilities()
        status = result[1] if isinstance(result, tuple) else result.status_code
        assert status == 403, f"expected 403 for a tenant admin, got {status}"

    with app.test_request_context("/api/acm-hybrid/seed", method="POST", json={}):
        login_user(platform_admin)
        g.current_org_id = platform_admin.organization_id
        result = seed_capabilities()
        status = result[1] if isinstance(result, tuple) else result.status_code
        assert status != 403, "a genuine platform admin must not be refused"


# ---------------------------------------------------------------------------
# D-03: delete_duplicates_keep_one must not delete another organisation's
# real ApplicationComponent rows via a foreign/guessed group_id
# ---------------------------------------------------------------------------


def _group_with_apps(db_session, applications, *, name):
    from app.models.unified_duplicate_detection import UnifiedDuplicateGroup

    group = UnifiedDuplicateGroup(name=name, similarity_score=0.9, status="pending")
    db_session.add(group)
    db_session.flush()
    for app in applications:
        group.applications.append(app)
    db_session.flush()
    return group


def _component(db_session, org, *, name):
    from app.models.application_portfolio import ApplicationComponent

    comp = ApplicationComponent(name=name, organization_id=org.id)
    db_session.add(comp)
    db_session.flush()
    return comp


def test_d03_delete_duplicates_keep_one_refuses_a_foreign_group(
    app, db_session, make_org, client, login_as
):
    """An ordinary user in org A must not be able to delete org B's real
    ApplicationComponent rows by passing org B's group_id."""
    org_a = make_org("d03-del-a")
    org_b = make_org("d03-del-b")

    keep_app = _component(db_session, org_b, name=f"Keep {uuid.uuid4().hex[:6]}")
    dup_app = _component(db_session, org_b, name=f"Dup {uuid.uuid4().hex[:6]}")
    group = _group_with_apps(db_session, [keep_app, dup_app], name="D03-foreign-group")

    admin_a = _user(db_session, org_a)
    db_session.commit()

    group_id = group.id
    keep_app_id = keep_app.id
    dup_app_id = dup_app.id

    _login(db_session, client, login_as, admin_a.id)
    response = client.post(
        f"/duplicate-detection/simple/delete-duplicates/{group_id}",
        json={"keep_app_id": keep_app_id},
    )

    assert response.get_json().get("success") is False, (
        "a user with no relationship to org B must not be able to resolve "
        "org B's duplicate group"
    )

    # Raw SQL, not the ORM: ApplicationComponent is TenantMixin, and this
    # assertion runs outside any request context (no g.current_org_id), where
    # the do_orm_execute tenant filter would otherwise make an ORM read of a
    # foreign organisation's row look exactly like a deleted one.
    from sqlalchemy import text

    row = db_session.execute(
        text("SELECT id FROM application_components WHERE id = :id"),
        {"id": dup_app_id},
    ).first()
    assert row is not None, (
        "org B's duplicate application must still exist -- it must not be "
        "deletable via a guessed/foreign group_id"
    )


def test_d03_delete_duplicates_keep_one_admits_the_owning_organisation(
    app, db_session, make_org, client, login_as
):
    org_a = make_org("d03-own-a")
    keep_app = _component(db_session, org_a, name=f"Keep {uuid.uuid4().hex[:6]}")
    dup_app = _component(db_session, org_a, name=f"Dup {uuid.uuid4().hex[:6]}")
    group = _group_with_apps(db_session, [keep_app, dup_app], name="D03-own-group")

    admin_a = _user(db_session, org_a)
    db_session.commit()

    group_id = group.id
    keep_app_id = keep_app.id
    dup_app_id = dup_app.id

    _login(db_session, client, login_as, admin_a.id)
    response = client.post(
        f"/duplicate-detection/simple/delete-duplicates/{group_id}",
        json={"keep_app_id": keep_app_id},
    )

    assert response.status_code == 200
    assert response.get_json().get("success") is True, response.get_json()

    from sqlalchemy import text

    row = db_session.execute(
        text("SELECT id FROM application_components WHERE id = :id"),
        {"id": dup_app_id},
    ).first()
    assert row is None, (
        "the owning organisation's own admin must still be able to resolve "
        "their own duplicate group"
    )


# ---------------------------------------------------------------------------
# D-05 / DEF-1 (review-pr430-v3.md): every admin user-management route --
# in BOTH the v1 modular blueprint (app/modules/admin/routes/admin_routes.py)
# and the v2 guardrail blueprint that actually serves /admin/* by default
# (app/_bootstrap/blueprints.py defaults USE_ADMIN_GUARDRAILS=true) -- must
# judge admin authority in the ACTIVE session organisation
# (g.current_org_id), not the caller's home organisation. DEF-6: no new
# decorator -- every fix below is the canonical
# app.middleware.tenant_decorators.require_org_or_platform_admin(org_id),
# called inline, the same mechanism change_user_email/set_user_password/
# delete_user/api_bulk_delete_users in v2 already used (commit 7ae1b168,
# already on main before this split).
#
# These hit the real, live HTTP routes (not a direct function call), because
# v2 -- not v1 -- is what actually serves /admin/* in this process, and
# DEF-1 specifically asked for "a test of the switched-org viewer against
# the live endpoints."
# ---------------------------------------------------------------------------


def _make_org_admin(db_session, org, *, platform=False):
    return _user(db_session, org, platform=platform, org_admin=True)


def _switch_active_org(client, org_id):
    with client.session_transaction() as sess:
        sess["current_org_id"] = org_id


def _grant_viewer(db_session, org_id, user_id):
    from app.models.org_role import OrgRole

    OrgRole.set_role(org_id, user_id, "viewer", granted_by_id=user_id)


LIVE_V2_ROUTES_NEEDING_ACTIVE_ORG_ADMIN = [
    ("get", "/admin/users"),
    ("get", "/admin/user/{victim_id}"),
    ("get", "/admin/user/{victim_id}/delete"),
    ("get", "/admin/api/users"),
]


@pytest.mark.parametrize("method,path_template", LIVE_V2_ROUTES_NEEDING_ACTIVE_ORG_ADMIN)
def test_def1_switched_org_viewer_is_refused_on_live_v2_admin_routes(
    app, db_session, make_org, client, login_as, method, path_template
):
    """DEF-1: these routes were still plain admin_required on the live (v2)
    tree -- an admin of org A holding only a Viewer OrgRole in org B could
    switch their active session into org B and reach them anyway."""
    org_a = make_org("def1-a")
    org_b = make_org("def1-b")
    attacker = _make_org_admin(db_session, org_a)
    victim = _make_org_admin(db_session, org_b)
    db_session.commit()

    attacker_id = attacker.id
    victim_id = victim.id
    org_b_id = org_b.id

    _grant_viewer(db_session, org_b_id, attacker_id)
    db_session.commit()

    _login(db_session, client, login_as, attacker_id)
    switched = client.post(
        "/account/switch-organization",
        data={"organization_id": str(org_b_id)},
        follow_redirects=False,
    )
    assert switched.status_code == 302, (
        "fixture setup: the switch itself must succeed (a Viewer OrgRole is "
        "sufficient) so the refusal below comes from the route's own guard"
    )

    path = path_template.format(victim_id=victim_id)
    response = getattr(client, method)(path)
    assert response.status_code == 403, (
        f"{method.upper()} {path} should refuse an admin of org A who only "
        f"holds a Viewer OrgRole in org B, after switching into org B; got "
        f"{response.status_code}"
    )


def test_def1_genuine_active_org_admin_still_reaches_live_v2_routes(
    app, db_session, make_org, client, login_as
):
    org = make_org("def1-genuine")
    admin = _make_org_admin(db_session, org)
    db_session.commit()

    _login(db_session, client, login_as, admin.id)
    assert client.get("/admin/users").status_code == 200
    assert client.get("/admin/api/users").status_code == 200


def test_def1_platform_admin_is_never_refused_on_live_v2_routes(
    app, db_session, make_org, client, login_as
):
    org = make_org("def1-platform")
    other_org = make_org("def1-platform-other")
    platform_admin = _make_org_admin(db_session, org, platform=True)
    db_session.commit()

    platform_admin_id = platform_admin.id
    other_org_id = other_org.id

    _login(db_session, client, login_as, platform_admin_id)
    _switch_active_org(client, other_org_id)

    assert client.get("/admin/users").status_code == 200
    assert client.get("/admin/api/users").status_code == 200


def test_def1_governance_gate_create_is_scoped_to_the_active_organisation(
    app, db_session, make_org, client, login_as
):
    """DEF-1: /admin/api/governance-gates POST (v2) was plain admin_required.
    GovernanceGate is TenantMixin (the write itself is already org-scoped),
    but an admin of org A with only a Viewer OrgRole in org B could still
    switch into org B and plant a gate there before this fix."""
    org_a = make_org("def1-gate-a")
    org_b = make_org("def1-gate-b")
    attacker = _make_org_admin(db_session, org_a)
    db_session.commit()

    attacker_id = attacker.id
    org_b_id = org_b.id
    _grant_viewer(db_session, org_b_id, attacker_id)
    db_session.commit()

    _login(db_session, client, login_as, attacker_id)
    client.post(
        "/account/switch-organization",
        data={"organization_id": str(org_b_id)},
        follow_redirects=False,
    )

    response = client.post(
        "/admin/api/governance-gates",
        json={"gate_name": f"def1-probe-{uuid.uuid4().hex[:8]}"},
    )
    assert response.status_code == 403


def test_def1_api_bulk_delete_users_is_scoped_to_the_active_organisation(
    app, db_session, make_org, client, login_as
):
    """Already fixed on main before this split (commit 7ae1b168) -- a
    no-regression control, since DEF-1's own route list named this one too."""
    from app.models.user import User

    org_a = make_org("def1-bulk-a")
    org_b = make_org("def1-bulk-b")
    admin_a = _make_org_admin(db_session, org_a)
    victim_b = _user(db_session, org_b, org_admin=False)
    db_session.commit()

    admin_a_id = admin_a.id
    victim_b_id = victim_b.id

    _login(db_session, client, login_as, admin_a_id)
    response = client.delete(
        "/admin/api/users/bulk",
        json={"ids": [victim_b_id]},
    )
    assert response.status_code == 200
    assert response.get_json().get("deleted") == 0

    db_session.expunge_all()
    assert User.query.get(victim_b_id) is not None
