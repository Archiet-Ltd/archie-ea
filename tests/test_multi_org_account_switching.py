"""Multi-organisation account switching and seat counting regressions."""

from __future__ import annotations

import uuid


def _make_user(db_session, org, *, email=None, enterprise_role="platform_admin"):
    from app.models.user import Role, User

    Role.insert_roles()
    role = Role.query.filter_by(name="Administrator").one()
    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=email or f"multi-org-{suffix}@example.com",
        first_name="Multi",
        last_name="Org",
        organization_id=org.id,
        enterprise_role=enterprise_role,
        confirmed=True,
        role=role,
        is_org_admin=True,
    )
    user.password = "MultiOrg!234"
    db_session.add(user)
    db_session.flush()
    return user


def _make_application(db_session, org, name):
    from app.models.application_portfolio import ApplicationComponent

    app = ApplicationComponent(
        name=name,
        organization_id=org.id,
        lifecycle_status="operational",
    )
    db_session.add(app)
    db_session.flush()
    return app


def _set_team_subscription(db_session, org, seats):
    from app.models.subscription import Subscription, SubscriptionPlan, SubscriptionStatus

    db_session.add(
        Subscription(
            organization_id=org.id,
            plan=SubscriptionPlan.team,
            status=SubscriptionStatus.active,
            seats_purchased=seats,
        )
    )
    db_session.flush()


def test_account_manage_lists_only_accessible_organisations_and_persists_switch(
    app, db_session, make_org, client, login_as
):
    from app.models.org_role import OrgRole
    from app.models.pending_invitation import PendingInvitation

    org_a = make_org("home")
    org_b = make_org("second")
    org_c = make_org("other")
    user = _make_user(db_session, org_a, email="partner@example.test")
    inviter = _make_user(db_session, org_b, email="admin-b@example.test")
    _make_user(db_session, org_c, email="admin-c@example.test")

    invitation, _ = PendingInvitation.create_for(
        org_b.id, user.id, "architect", invited_by_id=inviter.id
    )
    db_session.commit()

    login_as(client, user)
    accept = client.post(f"/account/invitation/{invitation.id}/accept", follow_redirects=False)
    assert accept.status_code == 302
    assert OrgRole.get_role(org_b.id, user.id) == "architect"

    page = client.get("/account/manage")
    assert page.status_code == 200
    html = page.get_data(as_text=True)

    assert 'data-testid="organization-memberships-card"' in html
    assert org_a.name in html
    assert org_b.name in html
    assert org_c.name not in html
    assert "Active: %s" % org_a.name in html

    switched = client.post(
        "/account/switch-organization",
        data={"organization_id": str(org_b.id)},
        follow_redirects=True,
    )
    assert switched.status_code == 200
    switched_html = switched.get_data(as_text=True)

    assert "Active: %s" % org_b.name in switched_html
    assert 'data-testid="active-organization-chip"' in switched_html
    assert org_b.name in switched_html

    reloaded = client.get("/account/manage")
    assert reloaded.status_code == 200
    reloaded_html = reloaded.get_data(as_text=True)
    assert "Active: %s" % org_b.name in reloaded_html
    assert org_c.name not in reloaded_html


def test_switching_organisation_scopes_exports_and_audit_rows(
    app, db_session, make_org, client, login_as, tenant_ctx
):
    from app.models.application_portfolio import ApplicationComponent
    from app.models.audit_log import AuditLog
    from app.models.org_role import OrgRole
    from app.utils.validators import sanitize_filename

    org_a = make_org("audit-a")
    org_b = make_org("audit-b")
    org_b.name = "Résumé & Finance / West"
    user = _make_user(db_session, org_a, email="switcher@example.test")
    OrgRole.set_role(org_b.id, user.id, "architect", granted_by_id=user.id)
    _make_application(db_session, org_a, "Audit Org A App")
    _make_application(db_session, org_b, "Audit Org B Seed")
    db_session.commit()

    login_as(client, user)

    switched = client.post(
        "/account/switch-organization",
        data={"organization_id": str(org_b.id)},
        follow_redirects=False,
    )
    assert switched.status_code == 302

    created_name = "Audit Org B Created %s" % uuid.uuid4().hex[:8]
    created = client.post("/api/applications/", json={"name": created_name})
    assert created.status_code == 201, created.get_data(as_text=True)
    created_id = created.get_json()["id"]

    export_b = client.get("/applications/export/csv")
    assert export_b.status_code == 200
    assert sanitize_filename(org_b.name) in export_b.headers["Content-Disposition"]
    csv_b = export_b.get_data(as_text=True)
    assert created_name in csv_b
    assert "Audit Org A App" not in csv_b

    switched_back = client.post(
        "/account/switch-organization",
        data={"organization_id": str(org_a.id)},
        follow_redirects=False,
    )
    assert switched_back.status_code == 302

    export_a = client.get("/applications/export/csv")
    assert export_a.status_code == 200
    assert sanitize_filename(org_a.name) in export_a.headers["Content-Disposition"]
    csv_a = export_a.get_data(as_text=True)
    assert "Audit Org A App" in csv_a
    assert created_name not in csv_a

    with tenant_ctx(org_b.id):
        created_app = ApplicationComponent.query.filter_by(id=created_id).one()
        assert created_app.organization_id == org_b.id

        org_b_audits = AuditLog.query.filter_by(
            organization_id=org_b.id, record_id=created_id
        ).all()
    assert any(row.table_name == "application_component" and row.action == "create" for row in org_b_audits)
    with tenant_ctx(org_a.id):
        assert AuditLog.query.filter_by(organization_id=org_a.id, record_id=created_id).count() == 0


def test_second_organisation_org_role_counts_as_a_seat_but_viewer_does_not(db_session, make_org):
    from app.models.org_role import OrgRole
    from app.services.billing_plans import user_limit_status

    home = make_org("seat-home")
    target = make_org("seat-target")
    editor = _make_user(db_session, home, email="editor@example.test")
    viewer = _make_user(db_session, home, email="viewer@example.test")

    OrgRole.set_role(target.id, editor.id, "architect", granted_by_id=editor.id)
    OrgRole.set_role(target.id, viewer.id, "viewer", granted_by_id=viewer.id)
    _set_team_subscription(db_session, target, seats=2)
    db_session.commit()

    status = user_limit_status(target.id)
    assert status["plan_key"] == "team"
    assert status["used"] == 1
    assert status["limit"] == 2
    assert status["limit_reached"] is False
