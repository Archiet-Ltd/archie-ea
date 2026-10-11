"""Tests for one owner record with a writer.

Two-organisation tests verify that an owner from organisation B cannot be
assigned to organisation A's application, that coverage counts only the
caller's organisation, and that the backfill keeps organisations apart.
"""

from __future__ import annotations

import json
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


# ── helpers ─────────────────────────────────────────────────────────────────


def _make_user(db_session, org, role, label):
    from app.models.user import Role, User

    Role.insert_roles()
    role_obj = Role.query.filter_by(name="User").first()

    user = User(
        email=f"ownertest-{label}-{uuid.uuid4().hex[:8]}@example.com",
        first_name=label.capitalize(),
        last_name="Test",
        organization_id=org.id,
        confirmed=True,
        enterprise_role=role,
        role_id=role_obj.id if role_obj else None,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _make_app(db_session, org, name, **extra):
    from app.models.application_portfolio import ApplicationComponent

    app = ApplicationComponent(
        name=f"{name} {uuid.uuid4().hex[:6]}",
        organization_id=org.id,
        **extra,
    )
    db_session.add(app)
    db_session.flush()
    return app


def _post_json(client, path, data):
    return client.post(
        path,
        data=json.dumps(data),
        content_type="application/json",
    )


def _put_json(client, path, data):
    return client.put(
        path,
        data=json.dumps(data),
        content_type="application/json",
    )


def _delete(client, path):
    return client.delete(path)


# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def two_orgs(db_session, make_org):
    """Two organisations, each with one app manager and one application."""
    org_a = make_org("own-a")
    org_b = make_org("own-b")
    manager_a = _make_user(db_session, org_a, "application_manager", "managera")
    manager_b = _make_user(db_session, org_b, "application_manager", "managerb")
    app_a = _make_app(db_session, org_a, "App A")
    app_b = _make_app(db_session, org_b, "App B")
    return {
        "db_session": db_session,
        "org_a": org_a,
        "org_b": org_b,
        "manager_a": manager_a,
        "manager_b": manager_b,
        "app_a": app_a,
        "app_b": app_b,
    }


# ── 1. Owner writer: add ────────────────────────────────────────────────────


def test_add_owner_success(db_session, make_org, client, login_as):
    """An application manager can add an owner to their own application."""
    org = make_org("own-add")
    manager = _make_user(db_session, org, "application_manager", "addmanager")
    app = _make_app(db_session, org, "Add Test")
    login_as(client, manager)

    resp = _post_json(client, f"/applications/{app.id}/owners", {
        "user_id": manager.id,
        "ownership_type": "primary",
    })
    assert resp.status_code == 201, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["success"] is True
    assert data["owner"]["ownership_type"] == "primary"
    assert data["owner"]["user_id"] == manager.id


def test_add_owner_duplicate_refused(db_session, make_org, client, login_as):
    """Adding the same user with the same type returns 409."""
    org = make_org("own-dup")
    manager = _make_user(db_session, org, "application_manager", "dupmanager")
    app = _make_app(db_session, org, "Dup Test")
    login_as(client, manager)

    _post_json(client, f"/applications/{app.id}/owners", {
        "user_id": manager.id,
        "ownership_type": "backup",
    })
    resp = _post_json(client, f"/applications/{app.id}/owners", {
        "user_id": manager.id,
        "ownership_type": "backup",
    })
    assert resp.status_code == 409, resp.get_data(as_text=True)


def test_add_owner_invalid_type_refused(db_session, make_org, client, login_as):
    """An invalid ownership type returns 400."""
    org = make_org("own-type")
    manager = _make_user(db_session, org, "application_manager", "typemanager")
    app = _make_app(db_session, org, "Type Test")
    login_as(client, manager)

    resp = _post_json(client, f"/applications/{app.id}/owners", {
        "user_id": manager.id,
        "ownership_type": "invalid_type",
    })
    assert resp.status_code == 400, resp.get_data(as_text=True)


# ── 2. Two-organisation isolation ────────────────────────────────────────────


def test_owner_from_another_org_refused(two_orgs, client, login_as):
    """An owner from organisation B cannot be assigned to organisation A's app."""
    login_as(client, two_orgs["manager_a"])

    resp = _post_json(client, f"/applications/{two_orgs['app_a'].id}/owners", {
        "user_id": two_orgs["manager_b"].id,
        "ownership_type": "primary",
    })
    assert resp.status_code == 404, resp.get_data(as_text=True)
    assert "not found in your organisation" in resp.get_json()["error"]


def test_owner_list_scoped_to_caller_org(two_orgs, client, login_as):
    """Listing owners for an app only returns owners from the caller's organisation."""
    from app.models.application_owner import ApplicationOwner

    db_session = two_orgs["db_session"]
    org_a = two_orgs["org_a"]
    org_b = two_orgs["org_b"]

    db_session.add(ApplicationOwner(
        application_id=two_orgs["app_a"].id,
        user_id=two_orgs["manager_a"].id,
        organization_id=org_a.id,
        ownership_type="primary",
    ))
    db_session.add(ApplicationOwner(
        application_id=two_orgs["app_a"].id,
        user_id=two_orgs["manager_b"].id,
        organization_id=org_b.id,
        ownership_type="primary",
    ))
    db_session.flush()

    login_as(client, two_orgs["manager_a"])
    resp = client.get(f"/applications/{two_orgs['app_a'].id}/owners")
    assert resp.status_code == 200
    data = resp.get_json()
    owner_ids = {o["user_id"] for o in data["owners"]}
    assert two_orgs["manager_a"].id in owner_ids
    assert two_orgs["manager_b"].id not in owner_ids


def test_cross_org_application_write_refused(two_orgs, client, login_as):
    """A user cannot write an owner onto another organisation's application."""
    login_as(client, two_orgs["manager_a"])

    # Try to add manager A's user to org B's application
    resp = _post_json(client, f"/applications/{two_orgs['app_b'].id}/owners", {
        "user_id": two_orgs["manager_a"].id,
        "ownership_type": "primary",
    })
    assert resp.status_code == 404, resp.get_data(as_text=True)
    data = resp.get_json()
    assert "Application not found" in data["error"]

    # Verify no orphan row was created
    from app.models.application_owner import ApplicationOwner
    rows = ApplicationOwner.query.filter(
        ApplicationOwner.application_id == two_orgs["app_b"].id,
    ).all()
    assert len(rows) == 0


# ── 3. Owner writer: change type & remove ───────────────────────────────────


def test_change_owner_type(db_session, make_org, client, login_as):
    """An owner's type can be changed."""
    from app.models.application_owner import ApplicationOwner

    org = make_org("own-change")
    manager = _make_user(db_session, org, "application_manager", "changemanager")
    app = _make_app(db_session, org, "Change Test")

    db_session.add(ApplicationOwner(
        application_id=app.id,
        user_id=manager.id,
        organization_id=org.id,
        ownership_type="primary",
    ))
    db_session.flush()
    owner_id = ApplicationOwner.query.filter_by(application_id=app.id).first().id

    login_as(client, manager)
    resp = _put_json(client, f"/applications/{app.id}/owners/{owner_id}", {
        "ownership_type": "business",
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["owner"]["ownership_type"] == "business"


def test_change_owner_type_duplicate_refused(db_session, make_org, client, login_as):
    """Changing into a type the same user already holds returns 409."""
    from app.models.application_owner import ApplicationOwner

    org = make_org("own-change-dup")
    manager = _make_user(db_session, org, "application_manager", "changedupmanager")
    app = _make_app(db_session, org, "Change Dup Test")

    db_session.add(ApplicationOwner(
        application_id=app.id,
        user_id=manager.id,
        organization_id=org.id,
        ownership_type="business",
    ))
    db_session.add(ApplicationOwner(
        application_id=app.id,
        user_id=manager.id,
        organization_id=org.id,
        ownership_type="technical",
    ))
    db_session.flush()
    technical_owner = ApplicationOwner.query.filter_by(
        application_id=app.id,
        ownership_type="technical",
    ).first()

    login_as(client, manager)
    resp = _put_json(client, f"/applications/{app.id}/owners/{technical_owner.id}", {
        "ownership_type": "business",
    })

    assert resp.status_code == 409, resp.get_data(as_text=True)
    assert "already assigned as business owner" in resp.get_json()["error"]


def test_remove_owner(db_session, make_org, client, login_as):
    """An owner can be removed."""
    from app.models.application_owner import ApplicationOwner

    org = make_org("own-remove")
    manager = _make_user(db_session, org, "application_manager", "removemanager")
    app = _make_app(db_session, org, "Remove Test")

    db_session.add(ApplicationOwner(
        application_id=app.id,
        user_id=manager.id,
        organization_id=org.id,
        ownership_type="primary",
    ))
    db_session.flush()
    owner_id = ApplicationOwner.query.filter_by(application_id=app.id).first().id

    login_as(client, manager)
    resp = _delete(client, f"/applications/{app.id}/owners/{owner_id}")
    assert resp.status_code == 200
    assert ApplicationOwner.query.get(owner_id) is None


def test_change_owner_type_not_found(db_session, make_org, client, login_as):
    """Changing a non-existent owner returns 404."""
    org = make_org("own-notfound")
    manager = _make_user(db_session, org, "application_manager", "notfoundmanager")
    app = _make_app(db_session, org, "Not Found")
    login_as(client, manager)

    resp = _put_json(client, f"/applications/{app.id}/owners/99999", {
        "ownership_type": "business",
    })
    assert resp.status_code == 404


# ── 4. Person picker ────────────────────────────────────────────────────────


def test_owner_picker_finds_users(db_session, make_org, client, login_as):
    """The person picker returns users matching the search query."""
    org = make_org("own-picker")
    alice = _make_user(db_session, org, "application_manager", "alice")
    bob = _make_user(db_session, org, "application_manager", "bob")
    _make_app(db_session, org, "Picker Test")
    login_as(client, alice)

    resp = client.get("/api/users?q=alice&limit=20")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["users"]) == 1
    assert data["users"][0]["id"] == alice.id

    resp = client.get("/api/users?q=bob&limit=20")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["users"]) == 1
    assert data["users"][0]["id"] == bob.id


def test_owner_picker_short_query_returns_empty(db_session, make_org, client, login_as):
    """The canonical user picker returns no matches for a non-matching short query."""
    org = make_org("own-short")
    manager = _make_user(db_session, org, "application_manager", "shortmgr")
    _make_app(db_session, org, "Short Query")
    login_as(client, manager)

    resp = client.get("/api/users?q=z&limit=20")
    assert resp.status_code == 200
    assert resp.get_json()["users"] == []


def test_owner_picker_scoped_to_org(two_orgs, client, login_as):
    """The person picker only returns users within the caller's organisation."""
    login_as(client, two_orgs["manager_a"])

    resp = client.get(f"/api/users?q={two_orgs['manager_b'].first_name}&limit=20")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["users"]) == 0, "cross-org user should not appear"


# ── 5. Backfill command ─────────────────────────────────────────────────────


def test_backfill_command_dry_run(app, db_session):
    """The backfill dry-run reports stats without writing."""
    from app.commands.backfill_application_owners import backfill_owner_data

    stats = backfill_owner_data(dry_run=True)
    assert isinstance(stats, dict)
    assert "legacy_ownership_rows" in stats
    assert "text_owner_fields" in stats
    assert stats["legacy_ownership_rows"] >= 0


def test_backfill_migrates_text_owners(db_session, make_org):
    """Backfill creates ApplicationOwner rows from text owner columns."""
    from app.commands.backfill_application_owners import backfill_owner_data
    from app.models.application_owner import ApplicationOwner

    org = make_org("own-bftext")
    user = _make_user(db_session, org, "application_manager", "bftextuser")
    app = _make_app(db_session, org, "Backfill Text", business_owner=f"{user.first_name} {user.last_name}")

    stats = backfill_owner_data(dry_run=False, organization_ids=[org.id])
    assert stats["text_owner_fields"] >= 1

    rows = ApplicationOwner.query.filter(
        ApplicationOwner.application_id == app.id,
    ).all()
    assert len(rows) >= 1
    assert rows[0].user_id == user.id
    assert rows[0].source_table == "business_owner"
    assert rows[0].source_id == app.id


def test_backfill_keeps_organisations_apart(db_session, make_org):
    """Backfill processes each organisation independently."""
    from app.commands.backfill_application_owners import backfill_owner_data
    from app.models.application_owner import ApplicationOwner
    from app.models.application_portfolio import ApplicationComponent

    org_a = make_org("own-bfa")
    org_b = make_org("own-bfb")
    user_a = _make_user(db_session, org_a, "application_manager", "bfusera")
    user_b = _make_user(db_session, org_b, "application_manager", "bfuserb")
    _make_app(db_session, org_a, "OrgA App", business_owner=f"{user_a.first_name} {user_a.last_name}")
    _make_app(db_session, org_b, "OrgB App", business_owner=f"{user_b.first_name} {user_b.last_name}")

    backfill_owner_data(dry_run=False, organization_ids=[org_a.id, org_b.id])

    rows_a = (
        ApplicationOwner.query
        .join(ApplicationComponent, ApplicationOwner.application_id == ApplicationComponent.id)
        .filter(ApplicationComponent.organization_id == org_a.id)
        .all()
    )
    rows_b = (
        ApplicationOwner.query
        .join(ApplicationComponent, ApplicationOwner.application_id == ApplicationComponent.id)
        .filter(ApplicationComponent.organization_id == org_b.id)
        .all()
    )

    assert len(rows_a) >= 1
    assert len(rows_b) >= 1
    for r in rows_a:
        assert r.user_id == user_a.id, "org A row points to wrong user"
    for r in rows_b:
        assert r.user_id == user_b.id, "org B row points to wrong user"


def test_backfill_does_not_crash_on_unresolved_name(db_session, make_org):
    """An unresolved text owner does NOT cause a NotNullViolation crash.

    Instead the name is only appended to the unresolved list, and no
    ApplicationOwner row is created.
    """
    from app.commands.backfill_application_owners import backfill_owner_data
    from app.models.application_owner import ApplicationOwner

    org = make_org("own-unresolved")
    user = _make_user(db_session, org, "application_manager", "unresmgr")
    _make_app(db_session, org, "Unresolved Test",
              business_owner=f"{user.first_name} {user.last_name}",
              technical_owner="Nobody Known Here")

    # Should not raise
    stats = backfill_owner_data(dry_run=False, organization_ids=[org.id])

    # Should have created 1 row (for the resolved business_owner) and 0 for unresolved
    rows = ApplicationOwner.query.filter(
        ApplicationOwner.organization_id == org.id,
    ).all()
    assert len(rows) == 1, "only the resolved name should create a row"
    assert rows[0].user_id == user.id
    assert rows[0].source_table == "business_owner"

    # Unresolved should be reported
    assert stats["unresolved_orgs"] == 1


def test_backfill_unresolved_name_goes_to_list_only(db_session, make_org):
    """An unresolved name creates no row, only an unresolved entry."""
    from app.commands.backfill_application_owners import backfill_owner_data, _record_unresolved
    from app.models.application_owner import ApplicationOwner

    org = make_org("own-unres2")
    _make_app(db_session, org, "No Match App", business_owner="Completely Unknown Person")

    stats = backfill_owner_data(dry_run=False, organization_ids=[org.id])
    rows = ApplicationOwner.query.filter(
        ApplicationOwner.organization_id == org.id,
    ).all()
    assert len(rows) == 0, "no row should be created for an unresolved name"
    assert stats["text_owner_fields"] == 0
    assert stats["unresolved_orgs"] == 1


def test_backfill_legacy_unknown_type_goes_to_unresolved(db_session, make_org):
    """Unknown legacy ownership_type goes to the unresolved list, not guessed."""
    from app.commands.backfill_application_owners import backfill_owner_data
    from app.models.application_owner import ApplicationOwner
    from app.models.enterprise_intelligence import ApplicationOwnership, OrganizationUnit

    org = make_org("own-unktype")
    user = _make_user(db_session, org, "application_manager", "unktypemanager")
    app = _make_app(db_session, org, "Unknown Type App")
    unit = OrganizationUnit(name="Test Unit", organization_id=org.id)
    db_session.add(unit)
    db_session.flush()
    db_session.add(ApplicationOwnership(
        application_id=app.id,
        organization_id=org.id,
        organization_unit_id=unit.id,
        ownership_type="Some random type",
        primary_contact=f"{user.first_name} {user.last_name}",
    ))
    db_session.flush()

    stats = backfill_owner_data(dry_run=False, organization_ids=[org.id])

    rows = ApplicationOwner.query.filter(
        ApplicationOwner.application_id == app.id,
    ).all()
    assert len(rows) == 0, "unknown type should not create an owner row"
    assert stats["unresolved_orgs"] == 1


def test_backfill_is_idempotent(db_session, make_org):
    """Running backfill twice creates the same number of rows."""
    from app.commands.backfill_application_owners import backfill_owner_data
    from app.models.application_owner import ApplicationOwner

    org = make_org("own-idem")
    user = _make_user(db_session, org, "application_manager", "idemmanager")
    _make_app(db_session, org, "Idempotent App", business_owner=f"{user.first_name} {user.last_name}")

    _ = backfill_owner_data(dry_run=False, organization_ids=[org.id])
    rows1 = ApplicationOwner.query.filter(
        ApplicationOwner.organization_id == org.id,
    ).all()

    stats2 = backfill_owner_data(dry_run=False, organization_ids=[org.id])
    rows2 = ApplicationOwner.query.filter(
        ApplicationOwner.organization_id == org.id,
    ).all()

    assert len(rows1) == len(rows2), "second run should create no additional rows"
    assert stats2["text_owner_fields"] == 0  # all skipped
    assert stats2["skipped_existing"] >= 1


# ── 6. Ownership coverage view ──────────────────────────────────────────────


def test_coverage_view_loads(db_session, make_org, client, login_as):
    """The coverage view renders for a CTO user."""
    org = make_org("own-cov")
    cto = _make_user(db_session, org, "cto", "ctocoverage")
    _make_app(db_session, org, "Covered App")
    login_as(client, cto)

    resp = client.get("/applications/ownership-coverage")
    assert resp.status_code == 200


def test_coverage_view_forbidden_for_procurement(db_session, make_org, client, login_as):
    """Procurement role gets 403 on coverage view."""
    org = make_org("own-cov403")
    proc = _make_user(db_session, org, "procurement", "procurementcov")
    login_as(client, proc)

    resp = client.get("/applications/ownership-coverage")
    assert resp.status_code == 403


def test_coverage_view_overall_dash_when_no_apps(db_session, make_org, client, login_as):
    """An org with no applications shows em-dash for overall coverage."""
    org = make_org("own-covdash")
    cto = _make_user(db_session, org, "cto", "ctodash")
    login_as(client, cto)

    resp = client.get("/applications/ownership-coverage")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "&mdash;" in html or "—" in html


def test_coverage_view_org_isolation(two_orgs, client, login_as):
    """Coverage counts only the caller's organisation."""
    from app.models.enterprise_intelligence import OrganizationUnit

    db_session = two_orgs["db_session"]
    org_a = two_orgs["org_a"]

    db_session.add(OrganizationUnit(
        name="Finance",
        organization_id=org_a.id,
    ))
    _make_app(db_session, org_a, "Finance App", business_domain="Finance")
    db_session.flush()

    # Create a CTO user in org A - the coverage route requires cto role
    cto_a = _make_user(db_session, org_a, "cto", "ctoaiso")

    login_as(client, cto_a)
    resp = client.get("/applications/ownership-coverage")
    assert resp.status_code == 200
    assert b"Finance" in resp.data


# ── 7. Application owner records on the fact sheet ──────────────────────────


def test_fact_sheet_shows_owners(db_session, make_org, client, login_as):
    """The fact sheet includes application owners from ApplicationOwner rows."""
    from app.models.application_owner import ApplicationOwner

    org = make_org("own-fs")
    manager = _make_user(db_session, org, "application_manager", "fsmanager")
    app = _make_app(db_session, org, "FS Test")
    db_session.add(ApplicationOwner(
        application_id=app.id,
        user_id=manager.id,
        organization_id=org.id,
        ownership_type="primary",
    ))
    db_session.flush()

    login_as(client, manager)
    resp = client.get(f"/applications/{app.id}/fact-sheet")
    assert resp.status_code == 200
    assert manager.first_name.encode() in resp.data


def test_edit_page_cross_org_returns_404(two_orgs, client, login_as):
    """Edit page returns 404 for another organisation's application."""
    editor = _make_user(
        two_orgs["db_session"],
        two_orgs["org_a"],
        "architect",
        "crossorgeditor",
    )
    login_as(client, editor)

    resp = client.get(f"/applications/{two_orgs['app_b'].id}/edit")

    assert resp.status_code == 404


def test_fact_sheet_cross_org_returns_404(two_orgs, client, login_as):
    """Fact sheet returns 404 for another organisation's application."""
    login_as(client, two_orgs["manager_a"])

    resp = client.get(f"/applications/{two_orgs['app_b'].id}/fact-sheet")

    assert resp.status_code == 404


# ── 8. Edit form shows legacy text fields as read-only ──────────────────────


def test_edit_form_has_readonly_text_owners(db_session, make_org, client, login_as):
    """The edit form renders business_owner and technical_owner as read-only."""
    org = make_org("own-ro")
    manager = _make_user(db_session, org, "application_manager", "romanager")
    app = _make_app(db_session, org, "RO Test", business_owner="Jane Legacy", technical_owner="John Legacy")
    login_as(client, manager)

    resp = client.get(f"/applications/{app.id}/edit")
    assert resp.status_code == 200, resp.get_data(as_text=True)[:2000]

    html = resp.get_data(as_text=True)
    assert "readonly" in html
    assert "Jane Legacy" in html
    assert "John Legacy" in html
    assert "Migrated to owner records" in html


def test_edit_post_leaves_owner_text_columns_unchanged(app, db_session, make_org, client, login_as):
    """Posting the edit form leaves legacy owner text columns untouched."""
    from app.models.application_portfolio import ApplicationComponent
    from app.models.user import Role
    from tests.test_cross_tenant_documents import _csrf_token

    org = make_org("own-edit-legacy")
    Role.insert_roles()
    role_obj = Role.query.filter_by(name="Administrator").first() or Role.query.filter_by(name="User").first()
    editor = _make_user(db_session, org, "architect", "legacyeditor")
    editor.role_id = role_obj.id if role_obj else None
    app_obj = _make_app(
        db_session,
        org,
        "Legacy Edit",
        business_owner="Jane Legacy",
        technical_owner="John Legacy",
        business_domain="Finance",
        technology_stack="Python",
    )
    db_session.commit()

    login_as(client, editor)
    csrf = _csrf_token(client, app)
    resp = client.post(
        f"/applications/{app_obj.id}/edit",
        data={
            "csrf_token": csrf,
            "updated_at": app_obj.updated_at.isoformat() if app_obj.updated_at else "",
            "name": app_obj.name,
            "description": app_obj.description or "",
            "application_code": app_obj.application_code or "",
            "application_type": app_obj.component_type or "",
            "criticality": app_obj.business_criticality or "",
            "technology_stack": "Python 3.12",
            "business_owner": "Changed Legacy Business",
            "technical_owner": "Changed Legacy Technical",
            "business_purpose": app_obj.business_purpose or "",
            "deployment_status": app_obj.deployment_status or "",
            "lifecycle_status": app_obj.lifecycle_status or "",
            "business_domain": app_obj.business_domain or "",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302, resp.get_data(as_text=True)

    db_session.expire_all()
    saved = ApplicationComponent.query.get(app_obj.id)
    assert saved.business_owner == "Jane Legacy"
    assert saved.technical_owner == "John Legacy"
    assert saved.technology_stack == "Python 3.12"


def test_edit_post_cross_org_returns_404_and_leaves_foreign_row_unchanged(app, two_orgs, client, login_as):
    """Cross-org POST to the edit form 404s and does not mutate the foreign row."""
    from app.models.application_portfolio import ApplicationComponent
    from tests.test_cross_tenant_documents import _csrf_token

    db_session = two_orgs["db_session"]
    editor = _make_user(db_session, two_orgs["org_a"], "architect", "crossorgpost")
    foreign_app = two_orgs["app_b"]
    foreign_app.technology_stack = "OldStack"
    db_session.commit()

    login_as(client, editor)
    csrf = _csrf_token(client, app)
    resp = client.post(
        f"/applications/{foreign_app.id}/edit",
        data={
            "csrf_token": csrf,
            "updated_at": foreign_app.updated_at.isoformat() if foreign_app.updated_at else "",
            "name": foreign_app.name,
            "description": foreign_app.description or "",
            "application_code": foreign_app.application_code or "",
            "application_type": foreign_app.component_type or "",
            "criticality": foreign_app.business_criticality or "",
            "technology_stack": "NewStack",
            "business_purpose": foreign_app.business_purpose or "",
            "deployment_status": foreign_app.deployment_status or "",
            "lifecycle_status": foreign_app.lifecycle_status or "",
            "business_domain": foreign_app.business_domain or "",
        },
        follow_redirects=False,
    )

    assert resp.status_code == 404
    db_session.expire_all()
    saved = ApplicationComponent.query.get(foreign_app.id)
    assert saved.technology_stack == "OldStack"


def test_edit_form_has_business_purpose(db_session, make_org, client, login_as):
    """Business purpose input is present in the edit form."""
    org = make_org("own-bp")
    manager = _make_user(db_session, org, "application_manager", "bpmanager")
    app = _make_app(db_session, org, "BP Test")
    login_as(client, manager)

    resp = client.get(f"/applications/{app.id}/edit")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'id="business_purpose"' in html
    assert 'name="business_purpose"' in html


@pytest.mark.parametrize(
    ("path_template", "form_data"),
    [
        ("/applications/{id}/overview-update", {"technology_stack": "OverviewStack"}),
        ("/applications/{id}/health-quality-update", {"technical_debt_hours": "42"}),
        ("/applications/{id}/governance-update", {"contains_pii": "true"}),
        ("/applications/{id}/resources-update", {"business_domain": "Finance"}),
        ("/applications/{id}/layers/strategy-update", {}),
    ],
)
def test_update_routes_cross_org_return_404_and_leave_foreign_row_unchanged(
    app,
    two_orgs,
    client,
    login_as,
    path_template,
    form_data,
):
    """Changed application update routes 404 for foreign ids before mutating."""
    from app.models.application_portfolio import ApplicationComponent
    from tests.test_cross_tenant_documents import _csrf_token

    db_session = two_orgs["db_session"]
    foreign_app = two_orgs["app_b"]
    foreign_app.technology_stack = "OldStack"
    db_session.commit()

    login_as(client, two_orgs["manager_a"])
    payload = dict(form_data)
    if path_template in {
        "/applications/{id}/governance-update",
        "/applications/{id}/resources-update",
        "/applications/{id}/layers/strategy-update",
    }:
        payload["csrf_token"] = _csrf_token(client, app)

    resp = client.post(
        path_template.format(id=foreign_app.id),
        data=payload,
        follow_redirects=False,
    )

    assert resp.status_code == 404
    db_session.expire_all()
    saved = ApplicationComponent.query.get(foreign_app.id)
    assert saved.technology_stack == "OldStack"


# ── 9. application_owners store-agreement concept ──────────────────────────


def test_can_assign_and_read_back_all_owner_types(db_session, make_org, client, login_as):
    """All four owner types can be assigned and read back."""
    from app.models.application_owner import ApplicationOwner

    org = make_org("own-types")
    manager = _make_user(db_session, org, "application_manager", "typesmgr")
    app = _make_app(db_session, org, "Types Test")
    login_as(client, manager)

    for otype in ["primary", "backup", "technical", "business"]:
        resp = _post_json(client, f"/applications/{app.id}/owners", {
            "user_id": manager.id,
            "ownership_type": otype,
        })
        assert resp.status_code == 201, f"Failed to add {otype}: {resp.get_data(as_text=True)}"

    rows = ApplicationOwner.query.filter_by(application_id=app.id).all()
    assert len(rows) == 4
    types = {r.ownership_type for r in rows}
    assert types == {"primary", "backup", "technical", "business"}


def test_cross_org_owner_not_counted_in_coverage(two_orgs, client, login_as):
    """Coverage does not count another organisation's owner records."""
    from app.models.application_owner import ApplicationOwner
    from app.models.enterprise_intelligence import OrganizationUnit

    db_session = two_orgs["db_session"]
    org_a = two_orgs["org_a"]
    org_b = two_orgs["org_b"]

    db_session.add(ApplicationOwner(
        application_id=two_orgs["app_b"].id,
        user_id=two_orgs["manager_b"].id,
        organization_id=org_b.id,
        ownership_type="primary",
    ))
    db_session.add(OrganizationUnit(
        name="Finance",
        organization_id=org_a.id,
    ))
    db_session.flush()

    # Use a CTO user for org A
    cto_a = _make_user(db_session, org_a, "cto", "ctocross")
    login_as(client, cto_a)
    resp = client.get("/applications/ownership-coverage")
    assert resp.status_code == 200


def test_app_owner_writer_assigns_back_to_user(two_orgs, client, login_as):
    """An owner assigned via the writer shows in that user's My Applications."""

    login_as(client, two_orgs["manager_a"])
    resp = _post_json(client, f"/applications/{two_orgs['app_a'].id}/owners", {
        "user_id": two_orgs["manager_b"].id,
        "ownership_type": "primary",
    })
    assert resp.status_code == 404, "cross-org assignment must be refused"

    resp = _post_json(client, f"/applications/{two_orgs['app_a'].id}/owners", {
        "user_id": two_orgs["manager_a"].id,
        "ownership_type": "primary",
    })
    assert resp.status_code == 201

    login_as(client, two_orgs["manager_a"])
    resp = client.get("/my-applications/")
    assert resp.status_code == 200
    assert two_orgs["app_a"].name.encode() in resp.data


# ── 10. Owner list endpoint requires app in caller's org ───────────────────


def test_list_owners_cross_org_app_refused(two_orgs, client, login_as):
    """Listing owners for another org's application returns 404."""
    login_as(client, two_orgs["manager_a"])

    resp = client.get(f"/applications/{two_orgs['app_b'].id}/owners")
    assert resp.status_code == 404, resp.get_data(as_text=True)


def test_remove_owner_cross_org_app_refused(two_orgs, client, login_as):
    """Removing an owner from another org's application returns 404."""
    login_as(client, two_orgs["manager_a"])

    resp = client.delete(f"/applications/{two_orgs['app_b'].id}/owners/1")
    assert resp.status_code == 404, resp.get_data(as_text=True)


def test_change_owner_type_cross_org_app_refused(two_orgs, client, login_as):
    """Changing owner type on another org's application returns 404."""
    login_as(client, two_orgs["manager_a"])

    resp = _put_json(client, f"/applications/{two_orgs['app_b'].id}/owners/1", {
        "ownership_type": "business",
    })
    assert resp.status_code == 404


# ── 11. Picker ILIKE escaping ──────────────────────────────────────────────


def test_owner_picker_escapes_special_chars(db_session, make_org, client, login_as):
    """Picker search handles % and _ in the query literally."""
    org = make_org("own-esc")
    manager = _make_user(db_session, org, "application_manager", "escmgr")
    unique = "te_st_user_100"
    manager.first_name = unique
    db_session.flush()
    _make_app(db_session, org, "Escape Test")
    login_as(client, manager)

    # Searching with % should not match everything
    resp = client.get("/api/users?q=%&limit=20")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["users"]) == 0, "% wildcard should not match all users"

    # Searching with _ should be literal
    resp = client.get(f"/api/users?q={unique}&limit=20")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["users"]) == 1, "should match the exact name"


def test_backfill_mismatched_legacy_row_goes_to_unresolved(db_session, make_org):
    """A legacy row pointing at another organisation's app is not backfilled."""
    from app.commands.backfill_application_owners import backfill_owner_data
    from app.models.application_owner import ApplicationOwner
    from app.models.enterprise_intelligence import ApplicationOwnership, OrganizationUnit

    org_a = make_org("own-mismatch-a")
    org_b = make_org("own-mismatch-b")
    user_a = _make_user(db_session, org_a, "application_manager", "mismatcha")
    app_b = _make_app(db_session, org_b, "Foreign App")
    unit_a = OrganizationUnit(name="Mismatch Unit", organization_id=org_a.id)
    db_session.add(unit_a)
    db_session.flush()
    db_session.add(ApplicationOwnership(
        application_id=app_b.id,
        organization_id=org_a.id,
        organization_unit_id=unit_a.id,
        ownership_type="Business Owner",
        primary_contact=f"{user_a.first_name} {user_a.last_name}",
    ))
    db_session.commit()

    stats = backfill_owner_data(dry_run=False, organization_ids=[org_a.id])

    rows = ApplicationOwner.query.filter(ApplicationOwner.organization_id == org_a.id).all()
    assert rows == []
    assert stats["legacy_ownership_rows"] == 0
    assert stats["unresolved_orgs"] == 1


def test_completeness_counts_application_owner_rows_not_legacy_text(db_session, make_org, tenant_ctx):
    """Completeness is satisfied by owner rows and not by legacy owner text alone."""
    from app.models.application_owner import ApplicationOwner
    from app.models.application_portfolio import ApplicationComponent
    from app.services.application_fact_sheet import compute_completeness

    org = make_org("own-complete-owner")
    owner_user = _make_user(db_session, org, "application_manager", "completeowner")
    with tenant_ctx(org.id):
        text_only = ApplicationComponent(
            name="Legacy Owner Text",
            organization_id=org.id,
            application_owner="Jane Doe",
        )
        owner_row_app = ApplicationComponent(
            name="Owner Row App",
            organization_id=org.id,
        )
        db_session.add_all([text_only, owner_row_app])
        db_session.flush()
        db_session.add(ApplicationOwner(
            application_id=owner_row_app.id,
            user_id=owner_user.id,
            organization_id=org.id,
            ownership_type="primary",
        ))
        db_session.commit()

        text_only_score = compute_completeness(text_only)
        owner_row_score = compute_completeness(owner_row_app)

        assert "Owner" in text_only_score["missing"]
        assert "Owner" not in owner_row_score["missing"]
        assert owner_row_score["pct"] > text_only_score["pct"]


def test_coverage_view_matches_business_domains_with_normalized_text(db_session, make_org, client, login_as):
    """Coverage groups apps when the domain differs only by case and whitespace."""
    from app.models.application_owner import ApplicationOwner
    from app.models.enterprise_intelligence import OrganizationUnit

    org = make_org("own-cov-normalized")
    cto = _make_user(db_session, org, "cto", "ctonormalized")
    manager = _make_user(db_session, org, "application_manager", "ownernormalized")
    app_obj = _make_app(db_session, org, "Finance App", business_domain="  finance  ")
    db_session.add(OrganizationUnit(name="Finance", organization_id=org.id))
    db_session.add(ApplicationOwner(
        application_id=app_obj.id,
        user_id=manager.id,
        organization_id=org.id,
        ownership_type="primary",
    ))
    db_session.commit()

    login_as(client, cto)
    resp = client.get("/applications/ownership-coverage")
    html = resp.get_data(as_text=True)

    assert resp.status_code == 200
    assert "Finance" in html
    assert ">1<" in html or "1%" in html


# ── 12. Backfill collision / merge handling ─────────────────────────────────


def test_backfill_legacy_and_text_same_owner(db_session, make_org):
    """Backfill does not crash when legacy and text columns name the same owner.

    A legacy row "Business Owner" with contact "Jane Test" and a text column
    business_owner = "Jane Test" on the same application share the same
    (application_id, user_id, ownership_type). The backfill must not raise
    a UniqueViolation; it should skip the second occurrence.
    """
    from app.commands.backfill_application_owners import backfill_owner_data
    from app.models.application_owner import ApplicationOwner
    from app.models.enterprise_intelligence import ApplicationOwnership, OrganizationUnit

    org = make_org("own-merge1")
    user = _make_user(db_session, org, "application_manager", "mergemgr")
    app = _make_app(db_session, org, "Merge App",
                    business_owner=f"{user.first_name} {user.last_name}")

    unit = OrganizationUnit(name="Test", organization_id=org.id)
    db_session.add(unit)
    db_session.flush()
    db_session.add(ApplicationOwnership(
        application_id=app.id,
        organization_id=org.id,
        organization_unit_id=unit.id,
        ownership_type="Business Owner",
        primary_contact=f"{user.first_name} {user.last_name}",
    ))
    db_session.flush()

    # Should not raise UniqueViolation
    stats = backfill_owner_data(dry_run=False, organization_ids=[org.id])

    rows = ApplicationOwner.query.filter(
        ApplicationOwner.application_id == app.id,
    ).all()
    # One row for the legacy path, text column is a merge (same user+type)
    assert len(rows) == 1, "only one row should be created for the merged case"
    assert rows[0].user_id == user.id
    assert stats["legacy_ownership_rows"] == 1
    assert stats["text_owner_fields"] == 0
    assert stats["skipped_existing"] >= 1


def test_backfill_ui_owner_then_text(db_session, make_org):
    """Backfill does not crash when owner was already added through the picker.

    An owner added via the writer (source_table NULL) then backfilled from a
    text column for the same application produces the same
    (application_id, user_id, ownership_type). The backfill must not raise.
    """
    from app.commands.backfill_application_owners import backfill_owner_data
    from app.models.application_owner import ApplicationOwner

    org = make_org("own-merge2")
    user = _make_user(db_session, org, "application_manager", "mergemgr2")
    app = _make_app(db_session, org, "Merge UI App",
                    business_owner=f"{user.first_name} {user.last_name}")

    # Pre-create the owner row as if it was added through the picker
    db_session.add(ApplicationOwner(
        application_id=app.id,
        user_id=user.id,
        organization_id=org.id,
        ownership_type="business",
    ))
    db_session.flush()

    # Backfill should skip the text column since the row already exists
    stats = backfill_owner_data(dry_run=False, organization_ids=[org.id])

    rows = ApplicationOwner.query.filter(
        ApplicationOwner.application_id == app.id,
    ).all()
    assert len(rows) == 1, "should not create a duplicate row"
    assert stats["text_owner_fields"] == 0
    assert stats["skipped_existing"] >= 1


def test_backfill_legacy_no_contact_is_listed(db_session, make_org):
    """A legacy row with no contact name is reported as unresolved."""
    from app.commands.backfill_application_owners import backfill_owner_data
    from app.models.application_owner import ApplicationOwner
    from app.models.enterprise_intelligence import ApplicationOwnership, OrganizationUnit

    org = make_org("own-nocontact")
    app = _make_app(db_session, org, "No Contact App")
    unit = OrganizationUnit(name="Test", organization_id=org.id)
    db_session.add(unit)
    db_session.flush()

    # Legacy row with no primary_contact and no contact_email
    db_session.add(ApplicationOwnership(
        application_id=app.id,
        organization_id=org.id,
        organization_unit_id=unit.id,
        ownership_type="Business Owner",
        primary_contact="",
    ))
    db_session.flush()

    stats = backfill_owner_data(dry_run=False, organization_ids=[org.id])

    rows = ApplicationOwner.query.filter(
        ApplicationOwner.application_id == app.id,
    ).all()
    assert len(rows) == 0, "no row should be created for a nameless contact"
    # Should be reported as unresolved with "(no contact)"
    assert stats["unresolved_orgs"] == 1, "should be counted as unresolved"
    assert stats["legacy_ownership_rows"] == 0


def test_backfill_two_legacy_rows_same_type_same_user(db_session, make_org):
    """Two legacy rows mapping to the same type and user do not cause a crash.

    "Business Owner" and "Budget Holder" both map to ownership_type "business".
    If both name the same contact, the backfill should create one row and
    record the second as a merge.
    """
    from app.commands.backfill_application_owners import backfill_owner_data
    from app.models.application_owner import ApplicationOwner
    from app.models.enterprise_intelligence import ApplicationOwnership, OrganizationUnit

    org = make_org("own-merge3")
    user = _make_user(db_session, org, "application_manager", "mergemgr3")
    app = _make_app(db_session, org, "Merge Two Legacy")

    unit = OrganizationUnit(name="Test", organization_id=org.id)
    db_session.add(unit)
    db_session.flush()

    db_session.add(ApplicationOwnership(
        application_id=app.id,
        organization_id=org.id,
        organization_unit_id=unit.id,
        ownership_type="Business Owner",
        primary_contact=f"{user.first_name} {user.last_name}",
    ))
    db_session.add(ApplicationOwnership(
        application_id=app.id,
        organization_id=org.id,
        organization_unit_id=unit.id,
        ownership_type="Budget Holder",
        primary_contact=f"{user.first_name} {user.last_name}",
    ))
    db_session.flush()

    stats = backfill_owner_data(dry_run=False, organization_ids=[org.id])

    rows = ApplicationOwner.query.filter(
        ApplicationOwner.application_id == app.id,
    ).all()
    assert len(rows) == 1, "only one row for the same user+type"
    assert stats["legacy_ownership_rows"] == 1
    assert stats["skipped_existing"] >= 1
    assert stats["merged_legacy_rows"] >= 1


def test_backfill_merged_legacy_rows_are_idempotent(db_session, make_org):
    """A second backfill run leaves merged legacy rows fully retired."""
    from app.commands.backfill_application_owners import backfill_owner_data
    from app.models.enterprise_intelligence import ApplicationOwnership, OrganizationUnit

    org = make_org("own-merge4")
    user = _make_user(db_session, org, "application_manager", "mergemgr4")
    app = _make_app(db_session, org, "Merge Idempotent")

    unit = OrganizationUnit(name="Test", organization_id=org.id)
    db_session.add(unit)
    db_session.flush()

    row1 = ApplicationOwnership(
        application_id=app.id,
        organization_id=org.id,
        organization_unit_id=unit.id,
        ownership_type="Business Owner",
        primary_contact=f"{user.first_name} {user.last_name}",
    )
    row2 = ApplicationOwnership(
        application_id=app.id,
        organization_id=org.id,
        organization_unit_id=unit.id,
        ownership_type="Budget Holder",
        primary_contact=f"{user.first_name} {user.last_name}",
    )
    db_session.add_all([row1, row2])
    db_session.commit()

    first_run = backfill_owner_data(dry_run=False, organization_ids=[org.id])
    db_session.expire_all()

    retired_one = ApplicationOwnership.query.get(row1.id).retired_into_id
    retired_two = ApplicationOwnership.query.get(row2.id).retired_into_id

    second_run = backfill_owner_data(dry_run=False, organization_ids=[org.id])
    db_session.expire_all()

    assert first_run["merged_legacy_rows"] >= 2
    assert retired_one is not None
    assert retired_two is not None
    assert retired_one == retired_two
    assert ApplicationOwnership.query.get(row1.id).retired_into_id == retired_one
    assert ApplicationOwnership.query.get(row2.id).retired_into_id == retired_two
    assert second_run["legacy_ownership_rows"] == 0
    assert second_run["merged_legacy_rows"] == 0
