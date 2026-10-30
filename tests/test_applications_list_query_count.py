"""The applications list issues a fixed number of statements however many rows it shows.

Every row of the list used to cost its own permission lookups (the actions menu and the
owner cell ask the same row-independent question once per row, on the table and again on
the phone card) and its own owner count (the completeness ring reads it up to three
times a row). The statement count grew with the page. These tests fail if that returns.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import event


def _seed(db_session, make_org, n_apps):
    from app.models.application_portfolio import ApplicationComponent
    from app.models.application_owner import ApplicationOwner
    from app.models.user import Role, User

    org = make_org("applist")
    Role.insert_roles()
    viewer = User(
        email="applist-%s@example.com" % uuid.uuid4().hex[:10],
        first_name="List", last_name="Viewer", organization_id=org.id, confirmed=True,
    )
    viewer.role = Role.query.filter_by(name="Architect").one()
    viewer.enterprise_role = "cto"
    db_session.add(viewer)
    db_session.flush()
    for i in range(n_apps):
        a = ApplicationComponent(name="Listed app %s %d" % (uuid.uuid4().hex[:6], i), organization_id=org.id)
        db_session.add(a)
        db_session.flush()
        if i % 2 == 0:
            db_session.add(ApplicationOwner(application_id=a.id, user_id=viewer.id, organization_id=org.id,
                                            ownership_type="primary"))
    db_session.flush()
    return org, viewer


def _statements_for_list(app, db_session, client, login_as, user):
    from app import db

    seen = []

    def _count(conn, cursor, statement, params, context, executemany):
        seen.append(" ".join(statement.split())[:80])

    login_as(client, user)
    assert client.get("/applications/").status_code == 200  # warm-up: per-process one-offs
    login_as(client, user)
    event.listen(db.engine, "before_cursor_execute", _count)
    try:
        resp = client.get("/applications/")
    finally:
        event.remove(db.engine, "before_cursor_execute", _count)
    assert resp.status_code == 200
    return seen, resp


def test_list_statement_count_does_not_grow_with_rows(app, db_session, make_org, client, login_as):
    _, small_user = _seed(db_session, make_org, 3)
    small, resp_small = _statements_for_list(app, db_session, client, login_as, small_user)
    _, big_user = _seed(db_session, make_org, 18)
    big, resp_big = _statements_for_list(app, db_session, client, login_as, big_user)
    assert b"Listed app" in resp_big.data
    assert len(big) <= len(small) + 3, (
        "applications list: %d statements for 3 rows, %d for 18 rows" % (len(small), len(big))
    )


def test_list_reads_owner_counts_in_one_statement(app, db_session, make_org, client, login_as):
    _, user = _seed(db_session, make_org, 12)
    seen, _ = _statements_for_list(app, db_session, client, login_as, user)
    owner_counts = [s for s in seen if s.startswith("SELECT count(*) AS count_1 FROM (SELECT application_owners")]
    # one for the page's summary figure at most, never one per listed row
    assert len(owner_counts) <= 3, "owner counts per request: %d" % len(owner_counts)
