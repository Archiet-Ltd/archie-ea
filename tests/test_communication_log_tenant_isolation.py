"""Stakeholder communications (solution notes) stay inside their organisation (T-sec02).

POST and GET /api/communications accepted any solution_id with only a login check, and the
table had no organization_id, so organisation B could plant a note on A's solution or list
A's notes. Every route now requires the solution in the caller's active organisation (404
otherwise, never revealing existence) and writes stamp organization_id and logged_by from
the signed-in user.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text


def _solution(db_session, org):
    from app.models.solution_models import Solution

    sol = Solution(name=f"Sol {uuid.uuid4().hex[:6]}", organization_id=org.id)
    db_session.add(sol)
    db_session.flush()
    return sol


def _user(db_session, org):
    from app.models.user import User

    user = User(email=f"cl-{uuid.uuid4().hex[:8]}@example.test", first_name="C", last_name="L",
                organization_id=org.id, confirmed=True)
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    return user


def _setup(db_session, make_org):
    org_a, org_b = make_org("cl-a"), make_org("cl-b")
    sol_a, sol_b = _solution(db_session, org_a), _solution(db_session, org_b)
    user_a, user_b = _user(db_session, org_a), _user(db_session, org_b)
    db_session.commit()
    ids = {"org_a": org_a.id, "org_b": org_b.id, "sol_a": sol_a.id, "sol_b": sol_b.id,
           "user_a": user_a.id, "user_b": user_b.id}
    db_session.expunge_all()
    return ids


def _login(client, login_as, db_session, user_id):
    from app.models.user import User

    login_as(client, db_session.get(User, user_id))


def _post(client, solution_id, subject="Kick-off"):
    return client.post("/api/communications", json={
        "solution_id": solution_id, "comm_type": "meeting", "subject": subject,
        "logged_by": 999999, "organization_id": 999999})


def _count(db_session, solution_id):
    return db_session.execute(
        text("select count(*) from stakeholder_communications where solution_id = :s"),
        {"s": solution_id}).scalar()


def test_other_organisation_cannot_write_a_note_on_my_solution(app, db_session, make_org, client, login_as):
    ids = _setup(db_session, make_org)
    _login(client, login_as, db_session, ids["user_b"])
    resp = _post(client, ids["sol_a"], "planted")
    assert resp.status_code == 404, resp.get_json()
    assert _count(db_session, ids["sol_a"]) == 0


def test_other_organisation_cannot_list_my_notes(app, db_session, make_org, client, login_as):
    ids = _setup(db_session, make_org)
    _login(client, login_as, db_session, ids["user_a"])
    assert _post(client, ids["sol_a"], "secret plan").status_code == 201
    _login(client, login_as, db_session, ids["user_b"])
    resp = client.get(f"/api/communications?solution_id={ids['sol_a']}")
    assert resp.status_code == 404, resp.get_json()
    assert b"secret plan" not in resp.data


def test_other_organisation_cannot_open_my_notes_page(app, db_session, make_org, client, login_as):
    ids = _setup(db_session, make_org)
    _login(client, login_as, db_session, ids["user_a"])
    assert _post(client, ids["sol_a"], "secret plan").status_code == 201
    _login(client, login_as, db_session, ids["user_b"])
    resp = client.get(f"/solutions/{ids['sol_a']}/communications")
    assert resp.status_code == 404
    assert b"secret plan" not in resp.data


def test_unknown_solution_is_the_same_404(app, db_session, make_org, client, login_as):
    ids = _setup(db_session, make_org)
    _login(client, login_as, db_session, ids["user_b"])
    assert _post(client, 2_000_000_000).status_code == 404
    assert client.get("/api/communications?solution_id=2000000000").status_code == 404


def test_missing_solution_id_is_refused_without_a_row(app, db_session, make_org, client, login_as):
    ids = _setup(db_session, make_org)
    _login(client, login_as, db_session, ids["user_a"])
    resp = client.post("/api/communications", json={"comm_type": "meeting", "subject": "x"})
    assert resp.status_code in (400, 404)


def test_own_organisation_still_reads_and_writes_and_is_stamped_from_the_user(
        app, db_session, make_org, client, login_as):
    ids = _setup(db_session, make_org)
    _login(client, login_as, db_session, ids["user_a"])
    resp = _post(client, ids["sol_a"], "Kick-off")
    assert resp.status_code == 201, resp.get_json()
    body = resp.get_json()
    assert body["logged_by"] == ids["user_a"], "logged_by must be the signed-in user, not the body"
    row = db_session.execute(
        text("select organization_id, logged_by from stakeholder_communications where id = :i"),
        {"i": body["id"]}).one()
    assert row.organization_id == ids["org_a"]
    assert row.logged_by == ids["user_a"]

    _login(client, login_as, db_session, ids["user_a"])
    listed = client.get(f"/api/communications?solution_id={ids['sol_a']}")
    assert listed.status_code == 200
    assert [e["subject"] for e in listed.get_json()] == ["Kick-off"]

    _login(client, login_as, db_session, ids["user_a"])
    # the old log page now sends its own organisation on to the notes on the solution record
    page = client.get(f"/solutions/{ids['sol_a']}/communications")
    assert page.status_code == 302
    assert page.headers["Location"].endswith(f"/solutions/{ids['sol_a']}#record-notes")


def test_table_filters_reads_by_organisation(app, db_session, make_org, tenant_ctx):
    """The table itself is fenced: a read inside B's context never returns A's note."""
    from app.models.stakeholder_communication import CommunicationType, StakeholderCommunication

    org_a, org_b = make_org("cl-t-a"), make_org("cl-t-b")
    sol_a = _solution(db_session, org_a)
    db_session.add(StakeholderCommunication(
        organization_id=org_a.id, solution_id=sol_a.id,
        comm_type=CommunicationType.EMAIL, subject="a-only"))
    db_session.commit()
    a_id, b_id = org_a.id, org_b.id
    with tenant_ctx(b_id):
        assert StakeholderCommunication.query.filter_by(subject="a-only").count() == 0
    with tenant_ctx(a_id):
        assert StakeholderCommunication.query.filter_by(subject="a-only").count() == 1
