"""A response's query count must not grow with the number of rows it returns.

The obvious way to gate performance is to assert a page renders in under N
milliseconds. That measures the CI runner's mood: slower on a shared box,
faster on a warm cache, and the usual response to the resulting flake is to
raise the threshold until the check means nothing.

Query count is the honest measurement. It is identical on every machine, and it
catches the thing that actually makes a server-rendered page slow: a loop that
issues one query per row. Thirty applications is fine against a local database;
twelve thousand across a network round trip is a page that never finishes.
The customer's portfolio is the second case.

So rather than pin an absolute number - which would need rebaselining whenever a
feature adds a legitimate query - this renders the same endpoint twice with
different row counts and asserts the difference stays flat.

Two things this file is careful about, both learned by getting them wrong:

  * It measures /api/applications/table-data, not the /applications/ HTML page.
    The HTML page caps how many rows it lists, so its query count cannot grow
    with row count no matter how bad the code is - a test against it passes
    unconditionally. The API serialises every row up to per_page=50, so it is
    the surface where an N+1 actually shows.

  * The first request to any endpoint issues extra one-off statements (metadata
    reflection, feature-flag loading). Measuring it makes the first count higher
    than the second and hides real growth - the first draft of this file
    "passed" while asserting queries had DECREASED. Every measurement is now
    preceded by a discarded warm-up request.

test_the_detector_catches_a_known_n_plus_one is a positive control: a route that
deliberately queries per row. If that test ever passes quietly, the measurement
has stopped working and every other assertion here is worthless.
"""

import json
import uuid

import pytest

pytestmark = pytest.mark.journey

# Both counts stay under the endpoint's per_page of 50, so the comparison is
# between two full result sets rather than against a pagination cap.
FEW = 3
MANY = 30

# Some growth is legitimate: eager-loading can add a query or two once a result
# set stops being empty. What is not legitimate is growth proportional to row
# count - 27 more rows must not cost 27 more queries.
ALLOWED_EXTRA_QUERIES = 4

TABLE_DATA = "/api/applications/table-data"
CONTROL = "/__test__/n_plus_one"


@pytest.fixture(scope="module")
def app():
    import os

    os.environ.setdefault("SECRET_KEY", "x" * 32)
    from app import create_app, db

    application = create_app("testing")
    with application.app_context():
        db.create_all()

    # The positive control: one query per row, on purpose. Registered before any
    # request is served, because Flask refuses to add routes afterwards.
    def _n_plus_one():
        from flask import request

        from app import db as _db

        org_id = request.args.get("org", type=int)
        rows = _db.session.execute(
            _db.text("SELECT id FROM application_components WHERE organization_id = :o"),
            {"o": org_id},
        ).fetchall()
        for _row in rows:
            # A real statement per row - the shape of every N+1 there is.
            _db.session.execute(_db.text("SELECT 1")).scalar()
        return "%d rows" % len(rows)

    application.add_url_rule(CONTROL, "test_n_plus_one", _n_plus_one)
    return application


@pytest.fixture
def tenant(app):
    """An organisation, an admin who can see everything, and a cleanup path."""
    from app import db
    from app.models.organization import Organization
    from app.models.user import Role, User

    marker = uuid.uuid4().hex[:8]
    created = {"apps": [], "marker": marker}
    with app.app_context():
        org = Organization(name="Budget %s" % marker, slug="budget-%s" % marker)
        db.session.add(org)
        db.session.commit()

        role = Role.query.filter_by(name="Administrator").first()
        if role is None:
            role = Role(name="Administrator")
            db.session.add(role)
            db.session.flush()

        user = User(
            email="budget-%s@example.com" % marker,
            first_name="Budget",
            last_name="Probe",
            confirmed=True,
            organization_id=org.id,
            enterprise_role="platform_admin",
        )
        user.role = role
        user.password = "probe-password-%s" % marker
        db.session.add(user)
        db.session.commit()
        created.update(org_id=org.id, user_id=user.id)

    yield created

    # The test database is shared and persistent, so every row made here is
    # removed here.
    with app.app_context():
        from app.services.org_deletion_service import OrganizationDeletionService

        org = db.session.get(Organization, created["org_id"])
        if org is not None:
            OrganizationDeletionService.delete_organization(created["org_id"])
        db.session.commit()


def _add_applications(app, tenant, count):
    from app import db
    from app.models.application_portfolio import ApplicationComponent

    with app.app_context():
        for i in range(count):
            row = ApplicationComponent(
                name="Budget %s %03d" % (tenant["marker"], len(tenant["apps"]) + i),
                organization_id=tenant["org_id"],
            )
            db.session.add(row)
            db.session.flush()
            tenant["apps"].append(row.id)
        db.session.commit()


def _login(client, user_id):
    from tests._session_test_helpers import mint_test_sid
    _sid = mint_test_sid(user_id)
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True
        if _sid:
            sess["_sid"] = _sid


def _measure(app, client, path):
    """Return (query count, statements, body) for a warmed request.

    Counts at the cursor level, so it sees every statement the request issues -
    including ones the ORM emits lazily while a template renders, which is
    exactly where N+1s hide.
    """
    from sqlalchemy import event

    from app import db

    client.get(path)  # warm-up, discarded: see the module docstring

    statements = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        statements.append(" ".join(statement.split()))

    engine = db.engine
    event.listen(engine, "before_cursor_execute", _record)
    try:
        response = client.get(path)
    finally:
        event.remove(engine, "before_cursor_execute", _record)

    assert response.status_code == 200, (
        "%s returned HTTP %s - cannot measure a response that did not render"
        % (path, response.status_code))
    return len(statements), statements, response.get_data(as_text=True)


def _worst_repeat(statements):
    """The statement issued most often, which is an N+1's signature."""
    counts = {}
    for statement in statements:
        counts[statement] = counts.get(statement, 0) + 1
    if not counts:
        return "", 0
    return max(counts.items(), key=lambda kv: kv[1])


def _rows_in(body):
    try:
        payload = json.loads(body)
    except ValueError:
        return None
    data = payload.get("data", payload)
    if isinstance(data, list):
        return len(data)
    for key in ("applications", "items", "data", "rows"):
        if isinstance(data.get(key), list):
            return len(data[key])
    return None


def test_table_data_query_count_is_flat(app, tenant):
    """The portfolio table is the surface that meets the largest table first."""
    with app.app_context():
        client = app.test_client()
        _login(client, tenant["user_id"])

        _add_applications(app, tenant, FEW)
        few_count, _, few_body = _measure(app, client, TABLE_DATA)

        _add_applications(app, tenant, MANY - FEW)
        many_count, many_statements, many_body = _measure(app, client, TABLE_DATA)

    # If the endpoint did not actually return more rows, the comparison proves
    # nothing - that is the trap the /applications/ HTML page fell into.
    few_rows, many_rows = _rows_in(few_body), _rows_in(many_body)
    assert few_rows and many_rows and many_rows > few_rows, (
        "expected the response to carry more rows the second time (got %s then "
        "%s). Without that, a flat query count says nothing about N+1."
        % (few_rows, many_rows))

    growth = many_count - few_count
    statement, repeats = _worst_repeat(many_statements)

    assert growth <= ALLOWED_EXTRA_QUERIES, (
        "%s issued %d queries for %d rows and %d for %d - %d more queries for "
        "%d more rows.\n\n"
        "That is per-row querying: invisible on a small test database and "
        "quadratic in production. The statement repeated most often ran %d "
        "times:\n  %s\n\n"
        "Eager-load the relationship (joinedload/selectinload) instead of "
        "letting the serialiser walk it row by row."
        % (TABLE_DATA, few_count, few_rows, many_count, many_rows, growth,
           many_rows - few_rows, repeats, statement[:200]))


def test_the_detector_catches_a_known_n_plus_one(app, tenant):
    """Positive control - the whole file rests on this.

    A measurement that silently records nothing passes every budget assertion
    ever written. So point it at a route that provably queries per row and
    require it to notice. If this test goes quiet, the detector is broken and
    test_table_data_query_count_is_flat is decorative.
    """
    with app.app_context():
        client = app.test_client()
        _login(client, tenant["user_id"])
        path = "%s?org=%d" % (CONTROL, tenant["org_id"])

        _add_applications(app, tenant, FEW)
        few_count, _, _ = _measure(app, client, path)

        _add_applications(app, tenant, MANY - FEW)
        many_count, many_statements, _ = _measure(app, client, path)

    growth = many_count - few_count
    _statement, repeats = _worst_repeat(many_statements)

    assert growth > ALLOWED_EXTRA_QUERIES, (
        "a route that issues one query per row grew by only %d queries when %d "
        "rows were added. The measurement is not seeing statements, so every "
        "other query-count assertion in this file is vacuous."
        % (growth, MANY - FEW))
    assert repeats >= MANY, (
        "expected the per-row statement to repeat at least %d times, saw %d"
        % (MANY, repeats))
