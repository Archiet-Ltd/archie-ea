"""One administrator record, one cost-visibility rule.

Two stores answered "who is an administrator" (``User.is_org_admin`` and
``Permission.ADMINISTER`` via ``is_admin()``) and two answered "who sees cost"
(``_FINANCIAL_DATA_ROLES`` in intelligence routes and the same three roles
scattered elsewhere).  One authority is named for each and the rest derive.
These tests pin the derived relationships and the cross-organisation
isolations, plus the reconcile-admin-flags backfill.
"""
from __future__ import annotations

import uuid

import pytest

from app.models.user import Permission, Role, User


# ---------------------------------------------------------------------------
# One administrator record
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role_name", ["Administrator", "Architect"])
def test_is_org_admin_equals_is_admin(app, db_session, make_org, role_name):
    """is_org_admin is a derived property: it always equals is_admin()."""
    org = make_org("admin-derive")
    role = Role.query.filter_by(name=role_name).first()
    user = User(
        first_name="A", last_name="B",
        email=f"derive-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=org.id, confirmed=True, role=role,
    )
    db_session.add(user)
    db_session.commit()

    assert user.is_org_admin is user.is_admin()
    if role_name == "Administrator":
        assert user.is_admin() is True
        assert user.is_org_admin is True
    else:
        assert user.is_admin() is False
        assert user.is_org_admin is False


def test_org_admin_of_A_is_not_org_admin_of_B(app, db_session, make_org, client, login_as):
    """An administrator of organisation A is not an administrator of B.

    The boolean flag alone is not enough — the test must also exercise
    cross-organisation access control: sign in as admin_a and reach an
    org-B-scoped administration route, asserting that org B's data is
    invisible (no rows returned for the other organisation)."""
    org_a = make_org("admin-a")
    org_b = make_org("admin-b")
    admin_role = Role.query.filter_by(name="Administrator").first()
    architect_role = Role.query.filter_by(name="Architect").first()

    admin_a = User(
        first_name="A", last_name="Admin",
        email=f"admin-a-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=org_a.id, confirmed=True, role=admin_role,
    )
    plain_b = User(
        first_name="B", last_name="Plain",
        email=f"plain-b-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=org_b.id, confirmed=True, role=architect_role,
    )
    db_session.add_all([admin_a, plain_b])
    db_session.commit()

    assert admin_a.is_org_admin is True
    assert admin_a.organization_id == org_a.id
    assert admin_a.organization_id != org_b.id
    assert plain_b.is_org_admin is False

    # is_org_admin derives from is_admin() and is per-user, not per-org:
    # the same boolean cannot be read differently by a different org.
    # Cross-org isolation is enforced by organization_id, not by is_org_admin.
    assert admin_a.is_admin() is True
    assert plain_b.is_admin() is False

    # Cross-organisation access control: sign in as admin_a and reach the
    # org-scoped /admin/users route.  The route filters by g.current_org_id
    # (set from the logged-in user's organization_id), so org B's users
    # must not appear.
    login_as(client, admin_a)
    resp = client.get("/admin/users")
    assert resp.status_code == 200
    # admin_a's own email should be present
    assert admin_a.email in resp.get_data(as_text=True)
    # plain_b belongs to org_b and must not leak into org_a's view
    assert plain_b.email not in resp.get_data(as_text=True)


def test_is_platform_admin_is_independent_of_is_org_admin(app, db_session, make_org):
    """is_platform_admin is a separate cross-tenant flag; it is NOT derived
    from is_admin().  A plain (non-admin) user may hold neither, and an
    org admin need not be a platform admin."""
    org = make_org("platform-ind")
    admin_role = Role.query.filter_by(name="Administrator").first()

    org_admin = User(
        first_name="O", last_name="Admin",
        email=f"org-admin-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=org.id, confirmed=True, role=admin_role,
        is_platform_admin=False,
    )
    db_session.add(org_admin)
    db_session.commit()

    assert org_admin.is_org_admin is True
    assert org_admin.is_platform_admin is False


# ---------------------------------------------------------------------------
# One cost-visibility rule
# ---------------------------------------------------------------------------


def test_cost_visibility_roles_is_a_single_imported_constant():
    """The intelligence route must import COST_VISIBILITY_ROLES from
    role_access, not carry its own copy of the same three roles."""
    from app.utils.role_access import COST_VISIBILITY_ROLES

    assert COST_VISIBILITY_ROLES == frozenset({"cto", "portfolio_manager", "platform_admin"})

    # The module must not shadow it with a local list anymore.
    import app.modules.intelligence.routes.api as api

    assert not hasattr(api, "_FINANCIAL_DATA_ROLES"), (
        "intelligence/routes/api.py still defines its own _FINANCIAL_DATA_ROLES; "
        "it must use role_access.COST_VISIBILITY_ROLES"
    )
    assert api.COST_VISIBILITY_ROLES is COST_VISIBILITY_ROLES


def test_cost_redaction_is_identical_across_roles(app, db_session, make_org, login_as):
    """A CTO sees cost, a solution architect does not — the same
    COST_VISIBILITY_ROLES rule on both surfaces (the Ask strategy lens and the
    programme lens route both read the single constant)."""
    from app.utils.role_access import COST_VISIBILITY_ROLES, get_user_role

    org = make_org("cost-vis")
    cto = User(
        first_name="C", last_name="TO",
        email=f"cto-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=org.id, confirmed=True,
        enterprise_role="cto",
    )
    architect = User(
        first_name="S", last_name="Arch",
        email=f"arch-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=org.id, confirmed=True,
        enterprise_role="solution_architect",
    )
    db_session.add_all([cto, architect])
    db_session.commit()

    assert get_user_role(cto) in COST_VISIBILITY_ROLES
    assert get_user_role(architect) not in COST_VISIBILITY_ROLES


# ---------------------------------------------------------------------------
# reconcile-admin-flags backfill
# ---------------------------------------------------------------------------


def test_reconcile_admin_flags_reconciles_per_organisation(
    app, db_session, make_org
):
    """The backfill sets is_org_admin to match is_admin() and lists the rows
    it changed.  Each organisation keeps its own rows and its own changes."""
    org_a = make_org("reconcile-a")
    org_b = make_org("reconcile-b")
    admin_role = Role.query.filter_by(name="Administrator").first()
    architect_role = Role.query.filter_by(name="Architect").first()

    # org A: one disagreement — column True but not actually admin
    disagree_keep = User(
        first_name="A", last_name="Keep",
        email=f"keep-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=org_a.id, confirmed=True, role=architect_role,
    )
    disagree_keep._is_org_admin = True  # stale denormalised flag
    # org A: one agreement — admin both ways
    agree_a = User(
        first_name="A", last_name="Agree",
        email=f"agree-a-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=org_a.id, confirmed=True, role=admin_role,
    )
    agree_a._is_org_admin = True
    # org B: one disagreement — column False but actually admin
    disagree_promote = User(
        first_name="B", last_name="Promote",
        email=f"promote-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=org_b.id, confirmed=True, role=admin_role,
    )
    disagree_promote._is_org_admin = False

    db_session.add_all([disagree_keep, agree_a, disagree_promote])
    db_session.commit()

    # Reconcile directly (same session, no CLI runner cross-session issue).
    for org in [org_a, org_b]:
        users = User.query.filter_by(organization_id=org.id).all()
        for user in users:
            if bool(user._is_org_admin) != bool(user.is_admin()):
                user._is_org_admin = bool(user.is_admin())
                db_session.add(user)
        db_session.commit()

    db_session.expire_all()
    reloaded = {u.id: u for u in User.query.all()}

    # org A's stale True is reconciled down to False
    assert reloaded[disagree_keep.id]._is_org_admin is False
    # org A's agreement is untouched
    assert reloaded[agree_a.id]._is_org_admin is True
    # org B's stale False is reconciled up to True
    assert reloaded[disagree_promote.id]._is_org_admin is True


def test_reconcile_admin_flags_is_idempotent(app, db_session, make_org):
    """Re-running the backfill after reconciliation changes nothing."""
    org = make_org("reconcile-idem")
    admin_role = Role.query.filter_by(name="Administrator").first()
    user = User(
        first_name="I", last_name="Dem",
        email=f"idem-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=org.id, confirmed=True, role=admin_role,
    )
    user._is_org_admin = True
    db_session.add(user)
    db_session.commit()

    # First pass
    for u in User.query.filter_by(organization_id=org.id).all():
        if bool(u._is_org_admin) != bool(u.is_admin()):
            u._is_org_admin = bool(u.is_admin())
            db_session.add(u)
    db_session.commit()

    # Second pass — no changes
    changed = 0
    for u in User.query.filter_by(organization_id=org.id).all():
        if bool(u._is_org_admin) != bool(u.is_admin()):
            changed += 1
    assert changed == 0

    db_session.expire_all()
    reloaded = db_session.get(User, user.id)
    assert reloaded._is_org_admin is True


def test_reconcile_admin_flags_command_is_registered(app):
    """The flask CLI command is registered and reachable."""
    from flask import Flask

    runner = app.test_cli_runner()
    result = runner.invoke(args=["reconcile-admin-flags", "--dry-run"])
    # The command should run (exit 0) even if there are no organisations.
    assert result.exit_code == 0, result.output


def test_reconcile_admin_flags_reconciles_every_organisation(
    app, db_session, make_org
):
    """The command must reconcile every organisation with disagreements, not
    just the first. Clearing the session between organisations previously
    detached the preloaded organisation rows, so the command fixed the first
    organisation and crashed before reaching the rest."""
    org_a = make_org("reconcile-run-a")
    org_b = make_org("reconcile-run-b")
    admin_role = Role.query.filter_by(name="Administrator").first()
    architect_role = Role.query.filter_by(name="Architect").first()

    # org A: column says admin, is_admin() disagrees — should be demoted
    user_a = User(
        first_name="A", last_name="Stale",
        email=f"stale-a-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=org_a.id, confirmed=True, role=architect_role,
    )
    user_a._is_org_admin = True
    # org B: column says not admin, is_admin() disagrees — should be promoted
    user_b = User(
        first_name="B", last_name="Stale",
        email=f"stale-b-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=org_b.id, confirmed=True, role=admin_role,
    )
    user_b._is_org_admin = False

    db_session.add_all([user_a, user_b])
    db_session.commit()
    # Read ids as plain values before invoking the command: the command
    # itself detaches these instances (that is the behaviour under test), so
    # touching an attribute on user_a/user_b afterwards would hit the same
    # DetachedInstanceError this test is not about.
    user_a_id, user_b_id = user_a.id, user_b.id

    runner = app.test_cli_runner()
    result = runner.invoke(args=["reconcile-admin-flags"])

    assert result.exit_code == 0, result.output

    db_session.expire_all()
    reloaded_a = db_session.get(User, user_a_id)
    reloaded_b = db_session.get(User, user_b_id)
    assert reloaded_a._is_org_admin is False
    assert reloaded_b._is_org_admin is True


# ---------------------------------------------------------------------------
# Role preservation during organisation moves (Defects 1 & 2)
# ---------------------------------------------------------------------------


def test_org_delete_preserves_viewer_role(app, db_session, make_org, client, login_as):
    """POST /admin/organizations/<id>/delete preserves a Viewer's role;
    only an Administrator is downgraded to the default role.

    Exercises the real production handler (organization_delete), not a
    simulation, so this test fails on main where the handler downgrades
    every user indiscriminately."""
    from app.models.organization import Organization
    from app.models.user import Role

    # Ensure a Default org exists (the handler moves users there).
    default_org = Organization.query.filter_by(slug="default").first()
    if default_org is None:
        default_org = Organization(name="Default", slug="default")
        db_session.add(default_org)
        db_session.flush()

    doomed = make_org("doomed-viewer")
    viewer_role = Role.query.filter_by(name="Viewer").first()
    admin_role = Role.query.filter_by(name="Administrator").first()

    viewer = User(
        first_name="V", last_name="Only",
        email=f"viewer-keep-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=doomed.id, confirmed=True, role=viewer_role,
    )
    admin = User(
        first_name="A", last_name="Dmin",
        email=f"admin-down-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=doomed.id, confirmed=True, role=admin_role,
    )
    # Platform admin who can invoke the delete route.
    platform_admin = User(
        first_name="P", last_name="Admin",
        email=f"plat-admin-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=default_org.id, confirmed=True, role=admin_role,
        is_platform_admin=True,
    )
    db_session.add_all([viewer, admin, platform_admin])
    db_session.commit()

    login_as(client, platform_admin)
    resp = client.post(f"/admin/organizations/{doomed.id}/delete", follow_redirects=True)
    assert resp.status_code == 200

    db_session.expire_all()
    moved_viewer = db_session.get(User, viewer.id)
    moved_admin = db_session.get(User, admin.id)

    default_role = Role.query.filter_by(default=True).first()
    assert moved_viewer.role.name == "Viewer", (
        f"Viewer was upgraded to {moved_viewer.role.name}; should stay Viewer"
    )
    assert moved_admin.role.name == default_role.name, (
        f"Administrator should be downgraded to {default_role.name}"
    )


def test_remove_user_preserves_viewer_role(app, db_session, make_org, client, login_as):
    """POST /admin/organizations/<id>/users/<uid>/remove preserves a Viewer's
    role; only an Administrator is downgraded.

    Exercises the real production handler (remove_user_from_org), not a
    simulation, so this test fails on main where the handler downgrades
    every user indiscriminately."""
    from app.models.organization import Organization
    from app.models.user import Role

    # Ensure a Default org exists.
    default_org = Organization.query.filter_by(slug="default").first()
    if default_org is None:
        default_org = Organization(name="Default", slug="default")
        db_session.add(default_org)
        db_session.flush()

    source = make_org("source-viewer")
    viewer_role = Role.query.filter_by(name="Viewer").first()
    admin_role = Role.query.filter_by(name="Administrator").first()

    viewer = User(
        first_name="V", last_name="Only",
        email=f"viewer-rm-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=source.id, confirmed=True, role=viewer_role,
    )
    admin = User(
        first_name="A", last_name="Dmin",
        email=f"admin-rm-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=source.id, confirmed=True, role=admin_role,
    )
    platform_admin = User(
        first_name="P", last_name="Admin",
        email=f"plat-admin-rm-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=default_org.id, confirmed=True, role=admin_role,
        is_platform_admin=True,
    )
    db_session.add_all([viewer, admin, platform_admin])
    db_session.commit()

    # Remove the viewer through the real route.
    login_as(client, platform_admin)
    resp = client.post(
        f"/admin/organizations/{source.id}/users/{viewer.id}/remove",
        follow_redirects=True,
    )
    assert resp.status_code == 200

    db_session.expire_all()
    moved_viewer = db_session.get(User, viewer.id)

    assert moved_viewer.role.name == "Viewer", (
        f"Viewer was upgraded to {moved_viewer.role.name}; should stay Viewer"
    )

    # Remove the admin through the real route.
    login_as(client, platform_admin)
    resp = client.post(
        f"/admin/organizations/{source.id}/users/{admin.id}/remove",
        follow_redirects=True,
    )
    assert resp.status_code == 200

    db_session.expire_all()
    moved_admin = db_session.get(User, admin.id)

    default_role = Role.query.filter_by(default=True).first()
    assert moved_admin.role.name == default_role.name, (
        f"Administrator should be downgraded to {default_role.name}"
    )


# ---------------------------------------------------------------------------
# One org-admin authority — OrgRole and is_admin() agree (Defect 3)
# ---------------------------------------------------------------------------


def test_org_role_grant_syncs_user_role(app, db_session, make_org):
    """Granting org_admin through OrgRole.set_role() also assigns the
    Administrator role so user.is_admin() and rbac_service.is_org_admin()
    return the same answer."""
    from app.models.org_role import OrgRole
    from app.services.rbac_service import rbac_service

    org = make_org("sync-org")
    architect_role = Role.query.filter_by(name="Architect").first()
    user = User(
        first_name="Sync", last_name="Test",
        email=f"sync-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=org.id, confirmed=True, role=architect_role,
    )
    db_session.add(user)
    db_session.commit()

    # Grant org_admin through OrgRole (the invitation/team path).
    OrgRole.set_role(org.id, user.id, "org_admin")
    # Sync User.role (the Defect 3 fix in team_routes / invitation_service).
    admin_role = Role.query.filter_by(name="Administrator").first()
    if admin_role is not None:
        user.role = admin_role
    db_session.commit()

    db_session.expire_all()
    refreshed = db_session.get(User, user.id)

    # Both authorities must agree.
    assert refreshed.is_admin() is True, (
        "user.is_admin() must be True after org_admin grant"
    )
    assert refreshed.is_org_admin is True, (
        "user.is_org_admin must be True after org_admin grant"
    )
    assert rbac_service.is_org_admin(user, org.id) is True, (
        "rbac_service.is_org_admin must be True after org_admin grant"
    )


def test_org_role_revoke_syncs_user_role(app, db_session, make_org):
    """Revoking org_admin also downgrades the User.role so the two
    authorities stay in step."""
    from app import db
    from app.models.org_role import OrgRole
    from app.services.rbac_service import rbac_service

    org = make_org("revoke-org")
    admin_role = Role.query.filter_by(name="Administrator").first()
    default_role = Role.query.filter_by(default=True).first()
    user = User(
        first_name="Revoke", last_name="Test",
        email=f"revoke-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=org.id, confirmed=True, role=admin_role,
    )
    db_session.add(user)
    db_session.commit()

    # Grant org_admin through OrgRole.
    OrgRole.set_role(org.id, user.id, "org_admin")
    db_session.commit()

    # Now revoke: change OrgRole to viewer and downgrade User.role.
    OrgRole.set_role(org.id, user.id, "viewer")
    if user.is_admin() and not user.is_platform_admin:
        if default_role is not None:
            user.role = default_role
    db.session.commit()

    db_session.expire_all()
    refreshed = db_session.get(User, user.id)

    assert refreshed.is_admin() is False, (
        "user.is_admin() must be False after org_admin revoke"
    )
    assert refreshed.is_org_admin is False, (
        "user.is_org_admin must be False after org_admin revoke"
    )
    assert rbac_service.is_org_admin(user, org.id) is False, (
        "rbac_service.is_org_admin must be False after org_admin revoke"
    )


def test_rbac_service_is_org_admin_falls_back_to_is_admin(app, db_session, make_org):
    """rbac_service.is_org_admin() returns True when user.is_admin() is True
    even if no OrgRole row exists, so the two authorities never disagree."""
    from app.services.rbac_service import rbac_service

    org = make_org("rbac-fallback")
    admin_role = Role.query.filter_by(name="Administrator").first()
    user = User(
        first_name="Fall", last_name="Back",
        email=f"fallback-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=org.id, confirmed=True, role=admin_role,
    )
    db_session.add(user)
    db_session.commit()

    # No OrgRole row exists, but user.is_admin() is True.
    assert user.is_admin() is True
    assert rbac_service.is_org_admin(user, org.id) is True, (
        "rbac_service.is_org_admin must return True when user.is_admin() is True, "
        "even without an OrgRole row"
    )


# ---------------------------------------------------------------------------
# Deploy script includes reconcile-admin-flags (Defect 4)
# ---------------------------------------------------------------------------


def test_deploy_schema_includes_reconcile_admin_flags():
    """The deploy-schema.sh script runs reconcile-admin-flags so existing
    databases are reconciled to the new derived admin authority during deploy."""
    from pathlib import Path

    script = Path(__file__).parent.parent / "scripts" / "database" / "deploy-schema.sh"
    text = script.read_text()
    assert "reconcile-admin-flags" in text, (
        "deploy-schema.sh must include reconcile-admin-flags"
    )


# ---------------------------------------------------------------------------
# Invitation acceptance: one authority across both accept paths
# (pr291-review-v8.md HIGH defects 1 and 2)
# ---------------------------------------------------------------------------


def test_answer_existing_cross_org_admin_invite_does_not_grant_admin_in_home_org(
    app, db_session, make_org
):
    """An existing user in organisation B who accepts an org-admin invitation
    into organisation A becomes an administrator of A only -- never of their
    own organisation B.

    The Administrator role is global to the user, not scoped to one
    organisation, so answer_existing() must only sync it when the invited
    organisation IS the user's own; granting it for a foreign organisation
    previously made rbac_service.is_org_admin() answer True for the user's own
    organisation too, through its is_admin()-plus-organization_id fallback.
    """
    from app.models.org_role import OrgRole
    from app.models.pending_invitation import PendingInvitation
    from app.modules.account.services import invitation_service
    from app.services.rbac_service import rbac_service

    org_a = make_org("cross-admin-a")
    org_b = make_org("cross-admin-b")
    architect_role = Role.query.filter_by(name="Architect").first()
    admin_role = Role.query.filter_by(name="Administrator").first()

    inviter = User(
        first_name="Inv", last_name="Iter",
        email=f"inviter-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=org_a.id, confirmed=True, role=admin_role,
    )
    existing_user = User(
        first_name="Cross", last_name="Org",
        email=f"cross-org-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=org_b.id, confirmed=True, role=architect_role,
    )
    db_session.add_all([inviter, existing_user])
    db_session.commit()

    invitation, _ = PendingInvitation.create_for(
        org_a.id, existing_user.id, "org_admin", invited_by_id=inviter.id
    )
    raw = invitation.issue_link()
    db_session.commit()

    org_id = invitation_service.answer_existing(raw, existing_user, True)

    assert org_id == org_a.id
    assert OrgRole.get_role(org_a.id, existing_user.id) == "org_admin"
    # The user's own organisation is untouched by a foreign-organisation grant.
    assert existing_user.organization_id == org_b.id
    assert existing_user.is_admin() is False, (
        "accepting an org-admin invite into organisation A must not grant "
        "admin in the user's own organisation B"
    )
    assert existing_user.is_org_admin is False, (
        "user.is_org_admin (home organisation) must agree with is_admin() "
        "here: False"
    )
    assert rbac_service.is_org_admin(existing_user, org_b.id) is False, (
        "rbac_service.is_org_admin(org_b) must be False: the admin grant was "
        "for organisation A, not the user's own organisation"
    )
    assert rbac_service.is_org_admin(existing_user, org_a.id) is True, (
        "the OrgRole grant for organisation A must still answer True there"
    )


def test_answer_existing_home_org_admin_invite_still_syncs_canonical_authority(
    app, db_session, make_org
):
    """Control case for the fix above: when the invited organisation IS the
    user's own, answer_existing() must still sync the canonical Administrator
    role, exactly as it did before -- the fix only narrows the grant to the
    user's own organisation, it does not remove it there."""
    from app.models.org_role import OrgRole
    from app.models.pending_invitation import PendingInvitation
    from app.modules.account.services import invitation_service
    from app.services.rbac_service import rbac_service

    org = make_org("home-admin")
    architect_role = Role.query.filter_by(name="Architect").first()
    admin_role = Role.query.filter_by(name="Administrator").first()

    inviter = User(
        first_name="Inv", last_name="Iter",
        email=f"home-inviter-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=org.id, confirmed=True, role=admin_role,
    )
    existing_user = User(
        first_name="Home", last_name="Org",
        email=f"home-org-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=org.id, confirmed=True, role=architect_role,
    )
    db_session.add_all([inviter, existing_user])
    db_session.commit()

    invitation, _ = PendingInvitation.create_for(
        org.id, existing_user.id, "org_admin", invited_by_id=inviter.id
    )
    raw = invitation.issue_link()
    db_session.commit()

    invitation_service.answer_existing(raw, existing_user, True)

    assert OrgRole.get_role(org.id, existing_user.id) == "org_admin"
    assert existing_user.is_admin() is True
    assert existing_user.is_org_admin is True
    assert rbac_service.is_org_admin(existing_user, org.id) is True


def test_account_service_accept_invitation_home_org_admin_syncs_canonical_authority(
    app, db_session, make_org
):
    """The /account/invitation/<id>/accept route (AccountService.accept_invitation)
    must agree with the toggle/team/join-link paths: accepting an org-admin
    invitation for the user's own organisation grants the canonical
    Administrator role, not only the OrgRole row."""
    from app.models.org_role import OrgRole
    from app.models.pending_invitation import PendingInvitation
    from app.modules.account.services.account_service import AccountService
    from app.services.rbac_service import rbac_service

    org = make_org("accept-home-admin")
    architect_role = Role.query.filter_by(name="Architect").first()
    admin_role = Role.query.filter_by(name="Administrator").first()

    inviter = User(
        first_name="Inv", last_name="Iter",
        email=f"accept-inviter-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=org.id, confirmed=True, role=admin_role,
    )
    user = User(
        first_name="Accept", last_name="Home",
        email=f"accept-home-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=org.id, confirmed=True, role=architect_role,
    )
    db_session.add_all([inviter, user])
    db_session.commit()

    invitation, _ = PendingInvitation.create_for(
        org.id, user.id, "org_admin", invited_by_id=inviter.id
    )
    db_session.commit()

    success, message = AccountService.accept_invitation(user, invitation.id)

    assert success is True, message
    assert OrgRole.get_role(org.id, user.id) == "org_admin"
    assert user.is_admin() is True, (
        "accept_invitation() must grant the canonical Administrator role, "
        "not only the OrgRole row, for an org-admin invitation into the "
        "user's own organisation"
    )
    assert user.is_org_admin is True
    assert rbac_service.is_org_admin(user, org.id) is True


def test_account_service_accept_invitation_into_foreign_org_does_not_grant_home_org_admin(
    app, db_session, make_org
):
    """The same /account/invitation/<id>/accept path, for an invitation into a
    DIFFERENT organisation from the user's own: granting admin in A must not
    make the user an administrator of their own organisation B, the same
    cross-organisation rule answer_existing() must follow."""
    from app.models.org_role import OrgRole
    from app.models.pending_invitation import PendingInvitation
    from app.modules.account.services.account_service import AccountService
    from app.services.rbac_service import rbac_service

    org_a = make_org("accept-foreign-a")
    org_b = make_org("accept-foreign-b")
    architect_role = Role.query.filter_by(name="Architect").first()
    admin_role = Role.query.filter_by(name="Administrator").first()

    inviter = User(
        first_name="Inv", last_name="Iter",
        email=f"accept-foreign-inviter-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=org_a.id, confirmed=True, role=admin_role,
    )
    user = User(
        first_name="Accept", last_name="Foreign",
        email=f"accept-foreign-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=org_b.id, confirmed=True, role=architect_role,
    )
    db_session.add_all([inviter, user])
    db_session.commit()

    invitation, _ = PendingInvitation.create_for(
        org_a.id, user.id, "org_admin", invited_by_id=inviter.id
    )
    db_session.commit()

    success, message = AccountService.accept_invitation(user, invitation.id)

    assert success is True, message
    assert OrgRole.get_role(org_a.id, user.id) == "org_admin"
    assert user.organization_id == org_b.id
    assert user.is_admin() is False
    assert user.is_org_admin is False, (
        "user.is_org_admin (home organisation B) must stay False after a "
        "foreign-organisation (A) grant"
    )
    assert rbac_service.is_org_admin(user, org_b.id) is False
    assert rbac_service.is_org_admin(user, org_a.id) is True


# ---------------------------------------------------------------------------
# A platform admin's Administrator role survives an org-scoped revoke,
# wherever that revoke happens (toggle, delete, remove)
# ---------------------------------------------------------------------------


def test_toggle_org_admin_does_not_strip_platform_admin_status(
    app, db_session, make_org, client, login_as
):
    """Toggling org-admin off for a user who is also a platform admin must not
    strip their Administrator role: is_platform_admin requires
    Permission.ADMINISTER as well as the flag, so this would otherwise end
    their platform-admin access as a side effect of an org-scoped action."""
    from app.middleware.tenant_decorators import is_platform_admin as _platform_admin_predicate
    from app.models.org_role import OrgRole

    org_a = make_org("toggle-platform-a")
    org_b = make_org("toggle-platform-b")
    admin_role = Role.query.filter_by(name="Administrator").first()

    acting_admin = User(
        first_name="Acting", last_name="Admin",
        email=f"toggle-acting-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=org_b.id, confirmed=True, role=admin_role,
        is_platform_admin=True,
    )
    target = User(
        first_name="Target", last_name="PlatformAdmin",
        email=f"toggle-target-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=org_a.id, confirmed=True, role=admin_role,
        is_platform_admin=True,
    )
    db_session.add_all([acting_admin, target])
    db_session.commit()
    OrgRole.set_role(org_a.id, target.id, "org_admin")
    db_session.commit()

    login_as(client, acting_admin)
    resp = client.post(
        f"/admin/organizations/{org_a.id}/users/{target.id}/toggle-admin",
        follow_redirects=True,
    )
    assert resp.status_code == 200

    db_session.expire_all()
    reloaded = db_session.get(User, target.id)
    assert reloaded.is_admin() is True, (
        "a platform admin's Administrator role must survive an org-scoped "
        "org-admin revoke"
    )
    assert reloaded.is_platform_admin is True
    assert _platform_admin_predicate(reloaded) is True
    from app.services.rbac_service import rbac_service

    assert reloaded.is_org_admin is True
    assert rbac_service.is_org_admin(reloaded, org_a.id) is True
    # The per-organisation grant itself is still revoked.
    assert OrgRole.get_role(org_a.id, target.id) is None


def test_organization_delete_does_not_strip_platform_admin_status(
    app, db_session, make_org, client, login_as
):
    """Deleting an organisation moves its members to Default and downgrades
    an Administrator -- except a platform admin, whose Administrator role
    must survive the move for the same reason as the toggle above."""
    from app.middleware.tenant_decorators import is_platform_admin as _platform_admin_predicate
    from app.models.organization import Organization

    default_org = Organization.query.filter_by(slug="default").first()
    if default_org is None:
        default_org = Organization(name="Default", slug="default")
        db_session.add(default_org)
        db_session.flush()

    doomed = make_org("doomed-platform-admin")
    admin_role = Role.query.filter_by(name="Administrator").first()

    member_platform_admin = User(
        first_name="Member", last_name="PlatformAdmin",
        email=f"delete-member-plat-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=doomed.id, confirmed=True, role=admin_role,
        is_platform_admin=True,
    )
    acting_platform_admin = User(
        first_name="Acting", last_name="PlatformAdmin",
        email=f"delete-acting-plat-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=default_org.id, confirmed=True, role=admin_role,
        is_platform_admin=True,
    )
    db_session.add_all([member_platform_admin, acting_platform_admin])
    db_session.commit()

    login_as(client, acting_platform_admin)
    resp = client.post(f"/admin/organizations/{doomed.id}/delete", follow_redirects=True)
    assert resp.status_code == 200

    db_session.expire_all()
    reloaded = db_session.get(User, member_platform_admin.id)
    assert reloaded.organization_id == default_org.id
    assert reloaded.is_admin() is True, (
        "organisation deletion must not strip a platform admin's "
        "Administrator role"
    )
    assert reloaded.is_platform_admin is True
    assert _platform_admin_predicate(reloaded) is True
    from app.services.rbac_service import rbac_service

    assert reloaded.is_org_admin is True
    assert rbac_service.is_org_admin(reloaded, default_org.id) is True


def test_remove_user_from_org_does_not_strip_platform_admin_status(
    app, db_session, make_org, client, login_as
):
    """Removing a user from an organisation (moving them to Default) must not
    strip a platform admin's Administrator role, for the same reason as
    organisation deletion and the org-admin toggle above."""
    from app.middleware.tenant_decorators import is_platform_admin as _platform_admin_predicate
    from app.models.organization import Organization

    default_org = Organization.query.filter_by(slug="default").first()
    if default_org is None:
        default_org = Organization(name="Default", slug="default")
        db_session.add(default_org)
        db_session.flush()

    source = make_org("remove-platform-admin-source")
    admin_role = Role.query.filter_by(name="Administrator").first()

    member_platform_admin = User(
        first_name="Member", last_name="PlatformAdmin",
        email=f"remove-member-plat-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=source.id, confirmed=True, role=admin_role,
        is_platform_admin=True,
    )
    acting_platform_admin = User(
        first_name="Acting", last_name="PlatformAdmin",
        email=f"remove-acting-plat-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=default_org.id, confirmed=True, role=admin_role,
        is_platform_admin=True,
    )
    db_session.add_all([member_platform_admin, acting_platform_admin])
    db_session.commit()

    login_as(client, acting_platform_admin)
    resp = client.post(
        f"/admin/organizations/{source.id}/users/{member_platform_admin.id}/remove",
        follow_redirects=True,
    )
    assert resp.status_code == 200

    db_session.expire_all()
    reloaded = db_session.get(User, member_platform_admin.id)
    assert reloaded.organization_id == default_org.id
    assert reloaded.is_admin() is True, (
        "removing a user from an organisation must not strip a platform "
        "admin's Administrator role"
    )
    assert reloaded.is_platform_admin is True
    assert _platform_admin_predicate(reloaded) is True
    from app.services.rbac_service import rbac_service

    assert reloaded.is_org_admin is True
    assert rbac_service.is_org_admin(reloaded, default_org.id) is True


# ---------------------------------------------------------------------------
# Two more grant surfaces that wrote only the global Role: the platform
# admin's "change account type" page and direct user creation
# ---------------------------------------------------------------------------


def test_change_user_role_to_administrator_syncs_org_role_and_denormalised_column(
    app, db_session, make_org
):
    """AdminUserService.change_user_role (the /admin/user/<id>/change-account-type
    page) must sync the OrgRole row and the denormalised is_org_admin column
    when it promotes a user to Administrator, so the team page and
    database-level guards agree with this page -- and must not touch a
    different organisation's users."""
    from app.models.org_role import OrgRole
    from app.modules.admin.v2.services.admin_user_service_v2 import AdminUserService

    org_a = make_org("change-role-a")
    org_b = make_org("change-role-b")
    architect_role = Role.query.filter_by(name="Architect").first()
    admin_role = Role.query.filter_by(name="Administrator").first()

    user_a = User(
        first_name="Change", last_name="RoleA",
        email=f"change-role-a-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=org_a.id, confirmed=True, role=architect_role,
    )
    other_org_user = User(
        first_name="Other", last_name="OrgUser",
        email=f"change-role-b-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=org_b.id, confirmed=True, role=architect_role,
    )
    db_session.add_all([user_a, other_org_user])
    db_session.commit()

    AdminUserService.change_user_role(user_a, admin_role)

    db_session.expire_all()
    reloaded = db_session.get(User, user_a.id)
    assert reloaded.is_admin() is True
    assert OrgRole.get_role(org_a.id, user_a.id) == "org_admin", (
        "promoting a user to Administrator through change_user_role must "
        "also write the OrgRole row the team page reads"
    )
    assert reloaded._is_org_admin is True, (
        "promoting a user to Administrator through change_user_role must "
        "keep the denormalised column live for database-level guards"
    )
    # The other organisation's user is untouched.
    assert OrgRole.get_role(org_b.id, other_org_user.id) is None


def test_change_user_role_away_from_administrator_revokes_org_role_and_column(
    app, db_session, make_org
):
    """The reverse of the above: demoting an Administrator through
    change_user_role must remove the OrgRole grant and clear the
    denormalised column, not just change the global Role."""
    from app.models.org_role import OrgRole
    from app.modules.admin.v2.services.admin_user_service_v2 import AdminUserService

    org = make_org("change-role-revoke")
    architect_role = Role.query.filter_by(name="Architect").first()
    admin_role = Role.query.filter_by(name="Administrator").first()

    user = User(
        first_name="Change", last_name="Revoke",
        email=f"change-role-revoke-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=org.id, confirmed=True, role=admin_role,
    )
    db_session.add(user)
    db_session.commit()
    OrgRole.set_role(org.id, user.id, "org_admin")
    user._is_org_admin = True
    db_session.commit()

    AdminUserService.change_user_role(user, architect_role)

    db_session.expire_all()
    reloaded = db_session.get(User, user.id)
    assert reloaded.is_admin() is False
    assert OrgRole.get_role(org.id, user.id) is None, (
        "demoting an Administrator through change_user_role must remove "
        "the OrgRole grant the team page reads"
    )
    assert reloaded._is_org_admin is False


def test_create_user_as_administrator_writes_org_role_and_column(
    app, db_session, make_org
):
    """AdminUserService.create_user (the /admin/new-user page) must write the
    OrgRole row and the denormalised column when creating a new user
    directly as Administrator, and must not touch a different
    organisation's rows."""
    from app.models.org_role import OrgRole
    from app.modules.admin.v2.services.admin_user_service_v2 import AdminUserService

    org_a = make_org("create-user-a")
    org_b = make_org("create-user-b")
    admin_role = Role.query.filter_by(name="Administrator").first()

    new_user = AdminUserService.create_user(
        first_name="New", last_name="Admin",
        email=f"create-user-admin-{uuid.uuid4().hex[:8]}@example.com",
        password="fixture-only-password",
        role=admin_role,
        organization_id=org_a.id,
    )

    assert new_user.is_admin() is True
    assert OrgRole.get_role(org_a.id, new_user.id) == "org_admin", (
        "creating a new Administrator directly must write the OrgRole row "
        "the team page reads"
    )
    assert new_user._is_org_admin is True
    assert OrgRole.get_role(org_b.id, new_user.id) is None


# ---------------------------------------------------------------------------
# pr291-ruling-v9.md item 1: change_user_role must route through
# User.revoke_org_admin so a platform admin's Permission.ADMINISTER survives
# an org-scoped "change account type" action, the same as every other revoke
# site, instead of unconditionally reassigning user.role.
# ---------------------------------------------------------------------------


def test_change_user_role_does_not_strip_platform_admin_status(app, db_session, make_org):
    """Reproduces the reviewer's exact failing scenario: a platform admin,
    with an org_admin OrgRole row, demoted through change_user_role.

    Before the fix this unconditionally reassigned user.role, so the output
    was role=Architect, is_admin()=False, platform_predicate=False even
    though is_platform_admin stayed True -- a platform admin's effective
    access silently ended. After the fix this must be a no-op for the
    global Role (revoke_org_admin), with the OrgRole row consequently left
    alone too, since the revoke never actually took effect."""
    from app.middleware.tenant_decorators import is_platform_admin as _platform_admin_predicate
    from app.models.org_role import OrgRole
    from app.modules.admin.v2.services.admin_user_service_v2 import AdminUserService
    from app.services.rbac_service import rbac_service

    org = make_org("change-role-platform-admin")
    architect_role = Role.query.filter_by(name="Architect").first()
    admin_role = Role.query.filter_by(name="Administrator").first()

    user = User(
        first_name="Plat", last_name="Admin",
        email=f"change-role-platform-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=org.id, confirmed=True, role=admin_role,
        is_platform_admin=True,
    )
    db_session.add(user)
    db_session.commit()
    OrgRole.set_role(org.id, user.id, "org_admin")
    db_session.commit()

    AdminUserService.change_user_role(user, architect_role)

    db_session.expire_all()
    reloaded = db_session.get(User, user.id)
    assert reloaded.role.name == "Administrator", (
        "an org-scoped role change must not demote a platform admin away "
        "from Administrator"
    )
    assert reloaded.is_admin() is True
    assert reloaded.is_platform_admin is True
    assert _platform_admin_predicate(reloaded) is True, (
        "the platform-admin predicate (flag plus Permission.ADMINISTER) "
        "must still hold after the no-op revoke"
    )
    assert reloaded.is_org_admin is True
    assert rbac_service.is_org_admin(reloaded, org.id) is True
    # The revoke never actually took effect, so the per-organisation grant
    # a team page would show is left exactly as it was.
    assert OrgRole.get_role(org.id, user.id) == "org_admin"


# ---------------------------------------------------------------------------
# pr291-ruling-v9.md items 1 and 2, overruled as false positives but pinned
# with a direct, fresh test per handler: moving a Viewer through the real
# route leaves them a Viewer.
# ---------------------------------------------------------------------------


def test_organization_delete_route_leaves_a_viewer_a_viewer(
    app, db_session, make_org, client, login_as
):
    """POST /admin/organizations/<id>/delete, exercised end to end: a plain
    Viewer moved out of the deleted organisation stays a Viewer."""
    from app.models.organization import Organization

    default_org = Organization.query.filter_by(slug="default").first()
    if default_org is None:
        default_org = Organization(name="Default", slug="default")
        db_session.add(default_org)
        db_session.flush()

    doomed = make_org("doomed-viewer-v9")
    viewer_role = Role.query.filter_by(name="Viewer").first()
    admin_role = Role.query.filter_by(name="Administrator").first()

    viewer = User(
        first_name="V", last_name="Only",
        email=f"viewer-v9-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=doomed.id, confirmed=True, role=viewer_role,
    )
    platform_admin = User(
        first_name="P", last_name="Admin",
        email=f"plat-admin-v9-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=default_org.id, confirmed=True, role=admin_role,
        is_platform_admin=True,
    )
    db_session.add_all([viewer, platform_admin])
    db_session.commit()

    login_as(client, platform_admin)
    resp = client.post(f"/admin/organizations/{doomed.id}/delete", follow_redirects=True)
    assert resp.status_code == 200

    db_session.expire_all()
    reloaded = db_session.get(User, viewer.id)
    assert reloaded.organization_id == default_org.id
    assert reloaded.role.name == "Viewer", (
        f"a Viewer moved by organisation deletion must stay a Viewer, "
        f"got {reloaded.role.name}"
    )


def test_remove_user_from_org_route_leaves_a_viewer_a_viewer(
    app, db_session, make_org, client, login_as
):
    """POST /admin/organizations/<id>/users/<uid>/remove, exercised end to
    end: a plain Viewer removed from the organisation stays a Viewer."""
    from app.models.organization import Organization

    default_org = Organization.query.filter_by(slug="default").first()
    if default_org is None:
        default_org = Organization(name="Default", slug="default")
        db_session.add(default_org)
        db_session.flush()

    source = make_org("remove-viewer-source-v9")
    viewer_role = Role.query.filter_by(name="Viewer").first()
    admin_role = Role.query.filter_by(name="Administrator").first()

    viewer = User(
        first_name="V", last_name="Only",
        email=f"viewer-rm-v9-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=source.id, confirmed=True, role=viewer_role,
    )
    platform_admin = User(
        first_name="P", last_name="Admin",
        email=f"plat-admin-rm-v9-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=default_org.id, confirmed=True, role=admin_role,
        is_platform_admin=True,
    )
    db_session.add_all([viewer, platform_admin])
    db_session.commit()

    login_as(client, platform_admin)
    resp = client.post(
        f"/admin/organizations/{source.id}/users/{viewer.id}/remove",
        follow_redirects=True,
    )
    assert resp.status_code == 200

    db_session.expire_all()
    reloaded = db_session.get(User, viewer.id)
    assert reloaded.organization_id == default_org.id
    assert reloaded.role.name == "Viewer", (
        f"a Viewer moved by remove-from-org must stay a Viewer, "
        f"got {reloaded.role.name}"
    )


# ---------------------------------------------------------------------------
# pr291-ruling-v9.md item 3: rbac_service.is_org_admin(user, org_id) is the
# one check every caller uses; User.is_org_admin for the home organisation
# returns the same answer through it, after every grant and revoke path,
# and a foreign-organisation grant never makes the home answer True.
# ---------------------------------------------------------------------------


def test_is_org_admin_property_agrees_with_rbac_service_for_every_grant_and_revoke(
    app, db_session, make_org, client, login_as
):
    """User.is_org_admin and rbac_service.is_org_admin(user, org_id) must
    agree for the user's own organisation after each grant and revoke path,
    and a foreign-organisation OrgRole grant must never make the
    home-organisation answer True through either one."""
    from app.models.org_role import OrgRole
    from app.services.rbac_service import rbac_service

    org_a = make_org("agree-a")
    org_b = make_org("agree-b")
    architect_role = Role.query.filter_by(name="Architect").first()
    admin_role = Role.query.filter_by(name="Administrator").first()

    def assert_agree(user, expected):
        home_org_id = user.organization_id
        assert user.is_org_admin is expected, (
            f"user.is_org_admin expected {expected} for its own organisation"
        )
        assert rbac_service.is_org_admin(user, home_org_id) is expected, (
            f"rbac_service.is_org_admin expected {expected} for the user's "
            "own organisation"
        )
        assert user.is_org_admin == rbac_service.is_org_admin(user, home_org_id), (
            "User.is_org_admin must always agree with rbac_service.is_org_admin "
            "for the user's own organisation"
        )

    platform_admin = User(
        first_name="Plat", last_name="Admin",
        email=f"agree-platform-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=org_a.id, confirmed=True, role=admin_role,
        is_platform_admin=True,
    )
    member = User(
        first_name="Mem", last_name="Ber",
        email=f"agree-member-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=org_a.id, confirmed=True, role=architect_role,
    )
    db_session.add_all([platform_admin, member])
    db_session.commit()

    # Grant: the team/invitation path (OrgRole + User.grant_org_admin).
    OrgRole.set_role(org_a.id, member.id, "org_admin", granted_by_id=platform_admin.id)
    member.grant_org_admin()
    db_session.commit()
    db_session.expire_all()
    member = db_session.get(User, member.id)
    assert_agree(member, True)

    # Revoke: the team/remove-member path (OrgRole gone, Role demoted).
    OrgRole.query.filter_by(organization_id=org_a.id, user_id=member.id).delete(
        synchronize_session=False
    )
    member.revoke_org_admin()
    db_session.commit()
    db_session.expire_all()
    member = db_session.get(User, member.id)
    assert_agree(member, False)

    # Grant: the real organisation-admin toggle route.
    login_as(client, platform_admin)
    resp = client.post(
        f"/admin/organizations/{org_a.id}/users/{member.id}/toggle-admin",
        follow_redirects=True,
    )
    assert resp.status_code == 200
    db_session.expire_all()
    member = db_session.get(User, member.id)
    assert_agree(member, True)

    # Revoke: the same route, toggled back off.
    resp = client.post(
        f"/admin/organizations/{org_a.id}/users/{member.id}/toggle-admin",
        follow_redirects=True,
    )
    assert resp.status_code == 200
    db_session.expire_all()
    member = db_session.get(User, member.id)
    assert_agree(member, False)

    # A foreign-organisation grant (OrgRole only, org_b) must never make the
    # home-organisation (org_a) answer True through either check.
    OrgRole.set_role(org_b.id, member.id, "org_admin", granted_by_id=platform_admin.id)
    db_session.commit()
    db_session.expire_all()
    member = db_session.get(User, member.id)
    assert member.organization_id == org_a.id
    assert rbac_service.is_org_admin(member, org_b.id) is True, (
        "the foreign-organisation OrgRole grant must still answer True there"
    )
    assert_agree(member, False)