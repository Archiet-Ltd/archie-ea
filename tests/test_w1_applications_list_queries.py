"""W1 performance: the applications list asks the database a fixed number of questions, not a few dozen per row.

Before: 480 statements for a 38-row page (about 200 ms in the database, the rest ORM overhead; p50 718 ms in the
sandbox run). Two causes, both per row: `can('edit'|'delete', 'application')` asked inside the row macros (it does not
depend on the row; 4 to 8 statements each) and `completeness_score` counting a row's owners with its own query every
time the template read it (three reads a row). The assertions compare a short page with a longer one, so the count may
not grow with the rows, and pin the figures a person sees as unchanged.
"""
import re
import uuid

from sqlalchemy import event
from sqlalchemy.engine import Engine


def _person(db_session, make_org):
    from app.models.user import Role, User

    org = make_org("appquery")
    Role.insert_roles()
    user = User(email="appquery-%s@example.com" % uuid.uuid4().hex[:8], first_name="App", last_name="Query",
                organization_id=org.id, confirmed=True, enterprise_role="architect")
    user.password = "Sup3rSecret!23"
    user.role = Role.query.filter_by(name="Architect").one()  # not an administrator: permissions are looked up
    db_session.add(user)
    db_session.flush()
    return org, user


def _add_applications(db_session, org, user, how_many, start=0):
    from app.models.application_owner import ApplicationOwner
    from app.models.application_portfolio import ApplicationComponent

    rows = []
    for i in range(start, start + how_many):
        row = ApplicationComponent(name="Perf app %02d" % i, organization_id=org.id,
                                   description="described" if i % 2 else None)
        db_session.add(row)
        db_session.flush()
        if i % 3 == 0:
            db_session.add(ApplicationOwner(application_id=row.id, organization_id=org.id, user_id=user.id))
        rows.append(row)
    db_session.commit()
    return rows


PER_ROW_TABLES = ("application_", "org_roles", "permission_grants")


def _statements_for(client, path):
    """(every statement, those touching the tables a per-row query would hit, the page)."""
    seen = []

    def count(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(Engine, "before_cursor_execute", count)
    try:
        response = client.get(path)
    finally:
        event.remove(Engine, "before_cursor_execute", count)
    assert response.status_code == 200, response.status_code
    touching = [st for st in seen if any(t in st for t in PER_ROW_TABLES)]
    return len(seen), len(touching), response.get_data(as_text=True)


def test_the_applications_list_does_not_ask_the_database_more_for_every_extra_row(
        app, db_session, make_org, client, login_as):
    org, user = _person(db_session, make_org)
    first = _add_applications(db_session, org, user, 3)
    login_as(client, user)
    _statements_for(client, "/applications/")  # warm the per-process caches

    _all, few, _html = _statements_for(client, "/applications/")
    first += _add_applications(db_session, org, user, 9, start=3)
    login_as(client, user)
    everything, many, _html = _statements_for(client, "/applications/")

    assert many - few <= 1, "3 rows took %d application, owner and permission statements and 12 rows took %d: the page asks per row" % (few, many)
    assert everything < 160, "%d statements for a 12-row page" % everything


def test_the_completeness_a_row_shows_is_still_the_one_the_record_calculates(
        app, db_session, make_org, client, login_as):
    org, user = _person(db_session, make_org)
    rows = _add_applications(db_session, org, user, 6)
    login_as(client, user)
    _all, _touching, html = _statements_for(client, "/applications/")

    for row in rows:
        shown = set(re.findall(r"Record (\d+)% complete", html))
        assert str(row.completeness_score) in shown, (row.name, row.completeness_score, shown)
    assert len(set(re.findall(r"Record (\d+)% complete", html))) >= 2, "rows with and without owners should differ"
