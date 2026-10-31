"""The agentic-gaps API is platform-wide and belongs to no organisation, so only a platform
administrator reaches it; everyone else is told the address does not exist. The old page is
retired and its address returns to the dashboard."""

from __future__ import annotations

import uuid

PASSWORD = "test-password-123"


def _org(db_session, label):
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:8]
    org = Organization(name=f"AF {label} {suffix}", slug=f"af-{label}-{suffix}")
    db_session.add(org)
    db_session.flush()
    return org


def _user(db_session, org, *, platform=False):
    from app.models.org_role import OrgRole
    from app.models.user import Role, User

    Role.insert_roles()
    role = Role.query.filter_by(name="Administrator" if platform else "User").first()
    suffix = uuid.uuid4().hex[:6]
    user = User(
        first_name="AF", last_name=f"User-{suffix}", email=f"af-{suffix}@example.test",
        password=PASSWORD, confirmed=True, organization_id=org.id, role=role,
        is_org_admin=False, is_platform_admin=platform,
    )
    db_session.add(user)
    db_session.flush()
    OrgRole.set_role(org.id, user.id, "viewer", granted_by_id=user.id)
    db_session.flush()
    return user


def _schedule(db_session, name, creator):
    from app.models.agentic_gaps import AgentSchedule

    row = AgentSchedule(name=name, architecture_id=1, schedule_type="daily", enabled=True,
                        created_by_id=creator.id)
    db_session.add(row)
    db_session.flush()
    return row


def test_another_organisation_cannot_read_rename_or_delete_a_schedule(app, db_session, login_as, client):
    from app.models.agentic_gaps import AgentSchedule

    org_a, org_b = _org(db_session, "a"), _org(db_session, "b")
    owner_name = f"Nightly {uuid.uuid4().hex[:6]}"
    row = _schedule(db_session, owner_name, _user(db_session, org_a))
    intruder = _user(db_session, org_b)
    db_session.commit()
    schedule_id = row.id

    login_as(client, intruder)
    listed = client.get("/api/agentic-gaps/schedule")
    renamed = client.put(f"/api/agentic-gaps/schedule/{schedule_id}", json={"name": "taken over"})
    deleted = client.delete(f"/api/agentic-gaps/schedule/{schedule_id}")

    db_session.expire_all()
    kept = db_session.get(AgentSchedule, schedule_id)
    assert (listed.status_code, renamed.status_code, deleted.status_code) == (404, 404, 404)
    assert owner_name not in listed.get_data(as_text=True)
    assert kept is not None and kept.name == owner_name
    assert org_a.id != org_b.id


def test_an_ordinary_member_gets_not_found_on_every_agentic_gaps_route(app, db_session, login_as, client):
    org = _org(db_session, "member")
    user = _user(db_session, org)
    db_session.commit()
    rules = sorted(
        (str(x), sorted(x.methods - {"HEAD", "OPTIONS"}))
        for x in app.url_map.iter_rules()
        if str(x).startswith("/api/agentic-gaps/")
    )
    assert rules

    login_as(client, user)
    for rule, methods in rules:
        path = rule.replace("<int:execution_id>", "1").replace("<int:schedule_id>", "1")
        path = path.replace("<int:gap_id>", "1").replace("<int:work_package_id>", "1")
        path = path.replace("<agent_name>", "x")
        for method in methods:
            assert getattr(client, method.lower())(path, json={}).status_code == 404, (method, path)


def test_a_platform_administrator_still_reaches_the_schedules(app, db_session, login_as, client):
    org = _org(db_session, "pa")
    admin = _user(db_session, org, platform=True)
    name = f"Weekly {uuid.uuid4().hex[:6]}"
    row = _schedule(db_session, name, admin)
    db_session.commit()

    login_as(client, admin)
    listed = client.get("/api/agentic-gaps/schedule")
    renamed = client.put(f"/api/agentic-gaps/schedule/{row.id}", json={"name": f"{name} renamed"})

    assert listed.status_code == 200 and name in listed.get_data(as_text=True)
    assert renamed.status_code == 200


def test_the_old_page_address_goes_to_the_dashboard_and_no_menu_lists_it(app, db_session, login_as, client):
    org = _org(db_session, "nav")
    user = _user(db_session, org)
    db_session.commit()

    login_as(client, user)
    resp = client.get("/agentic-gaps")
    assert resp.status_code in (301, 302, 303, 307, 308)
    assert resp.headers["Location"].rstrip("/").endswith("/dashboard") or "dashboard" in resp.headers["Location"]
    landing = client.get(resp.headers["Location"])
    assert landing.status_code == 200

    shell = client.get(resp.headers["Location"], follow_redirects=True).get_data(as_text=True)
    assert 'href="/agentic-gaps"' not in shell
    assert "Gap Discovery" not in shell
    assert "main.agentic_gaps_ui" not in app.view_functions
