"""The application spreadsheet import saves the good rows and names each refused row.

One bad row never rolls back the file, and a row-level problem is never reported as a
whole-file failure. Reasons shown to the person never carry database internals, and
lifecycle values are always one of the canonical stages.
"""
from __future__ import annotations

import io
import uuid

import pytest

HEADER = "Name,Lifecycle Status,Criticality\n"
FORBIDDEN = ['"', "relation", "constraint", "varchar", "psycopg", "character varying"]


@pytest.fixture(autouse=True)
def _clean_cache():
    from app.modules.import_batch.services import import_orchestrator as mod

    mod._import_idempotency_cache.clear()
    yield
    mod._import_idempotency_cache.clear()


def _admin(db_session, org):
    from app.models.org_role import OrgRole
    from app.models.user import Role, User

    user = User(
        email=f"rbr-{uuid.uuid4().hex[:10]}@example.com",
        first_name="Row",
        last_name="Importer",
        organization_id=org.id,
        role=Role.query.filter_by(name="User").first(),
        confirmed=True,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    OrgRole.set_role(org.id, user.id, "org_admin")
    user.grant_org_admin()
    db_session.flush()
    return user


def _names(n=3):
    token = uuid.uuid4().hex[:8]
    return [f"App{i} {token}" for i in range(n)]


def _post(client, login_as, user, text, mode="skip"):
    login_as(client, user)
    data = {"file": (io.BytesIO(text.encode()), "apps.csv"), "import_mode": mode}
    return client.post(
        "/applications/import", data=data, content_type="multipart/form-data",
        follow_redirects=False,
    )


def _apps(db_session, org, names):
    from app.models.application_layer import ApplicationComponent

    db_session.expire_all()
    rows = ApplicationComponent.query.filter(
        ApplicationComponent.organization_id == org.id,
        ApplicationComponent.name.in_(names),
    ).all()
    return {r.name: r for r in rows}


def _flashes(client):
    with client.session_transaction() as sess:
        return [(c, m) for c, m in sess.get("_flashes", [])]


def _text(flashes):
    return "\n".join(m for _, m in flashes)


def _no_internals(text):
    leaked = [word for word in FORBIDDEN if word in text]
    assert not leaked, (leaked, text)


def test_unknown_lifecycle_refuses_that_row_only_and_lists_the_stages(
    client, db_session, make_org, login_as
):
    org = make_org("rbra")
    user = _admin(db_session, org)
    a, b, c = _names()
    long_value = "x" * 40
    _post(client, login_as, user, HEADER + f"{a},2.2 tactical,\n{b},{long_value},\n{c},3. sunset,\n")
    apps = _apps(db_session, org, [a, b, c])
    assert set(apps) == {a, c}
    flashes = _flashes(client)
    text = _text(flashes)
    assert "Row 3" in text and b in text and long_value in text, text
    assert "2.1 strategic" in text and "5. decommissioned" in text, text
    assert not any(m == "Error importing file. Please try again." for _, m in flashes)
    assert "2 created" in text and "1 refused" in text, text


def test_a_row_that_breaks_a_database_limit_is_refused_alone_and_names_the_column(
    client, db_session, make_org, login_as
):
    org = make_org("rbrb")
    user = _admin(db_session, org)
    a, b, c = _names()
    long_cell = "c" * 80
    _post(
        client, login_as, user,
        HEADER + f"{a},2.2 tactical,High\n{b},2.2 tactical,{long_cell}\n{c},2.2 tactical,Low\n",
    )
    apps = _apps(db_session, org, [a, b, c])
    assert set(apps) == {a, c}
    flashes = _flashes(client)
    text = _text(flashes)
    assert "Row 3" in text and b in text, text
    assert "Criticality" in text and "50" in text, text
    _no_internals(text)
    assert not any(m == "Error importing file. Please try again." for _, m in flashes)


@pytest.mark.parametrize(
    "label,stage",
    [
        ("Decommissioned", "5. decommissioned"),
        ("Sunset", "3. sunset"),
        ("2.2 TACTICAL", "2.2 tactical"),
        ("4.3 Read-Only", "4.3 read-only"),
    ],
)
def test_known_labels_and_canonical_values_are_saved_as_canonical_stages(
    client, db_session, make_org, login_as, label, stage
):
    org = make_org("rbrc")
    user = _admin(db_session, org)
    a, b, _ = _names()
    _post(client, login_as, user, HEADER + f"{a},{label},\n{b},2.1 strategic,\n")
    apps = _apps(db_session, org, [a, b])
    assert apps[a].lifecycle_status == stage
    assert "refused" not in _text(_flashes(client))


@pytest.mark.parametrize(
    "value",
    ["End of life", "Retiring", "Planning", "operational", "In production", "Production",
     "Live", "In service", "bogus", "z" * 40],
)
def test_any_other_lifecycle_value_is_refused_with_the_allowed_stages(
    client, db_session, make_org, login_as, value
):
    org = make_org("rbrh")
    user = _admin(db_session, org)
    a, b, _ = _names()
    _post(client, login_as, user, HEADER + f"{a},{value},\n{b},2.2 tactical,\n")
    assert set(_apps(db_session, org, [a, b])) == {b}
    text = _text(_flashes(client))
    assert "Row 2" in text and value in text, text
    for stage in ("1. undetermined", "2.1 strategic", "3. sunset", "5. decommissioned"):
        assert stage in text, text
    _no_internals(text)


def test_refused_rows_in_update_mode_leave_the_stored_row_unchanged(
    client, db_session, make_org, login_as
):
    from app.models.application_layer import ApplicationComponent

    org = make_org("rbrd")
    user = _admin(db_session, org)
    a, b, _ = _names()
    for n in (a, b):
        db_session.add(ApplicationComponent(
            name=n, organization_id=org.id, deployment_status="planned",
            lifecycle_status="2.2 tactical", description="old",
        ))
    db_session.flush()
    bad = "y" * 40
    _post(
        client, login_as, user,
        "Name,Lifecycle Status,Description\n"
        f"{a},{bad},new\n{b},3. sunset,new\n",
        mode="update",
    )
    apps = _apps(db_session, org, [a, b])
    assert (apps[a].lifecycle_status, apps[a].description) == ("2.2 tactical", "old")
    assert (apps[b].lifecycle_status, apps[b].description) == ("3. sunset", "new")
    text = _text(_flashes(client))
    assert "Row 2" in text and a in text, text
    assert "1 updated" in text and "1 refused" in text, text


def test_refused_list_is_capped_at_fifty_with_the_total(
    client, db_session, make_org, login_as
):
    org = make_org("rbre")
    user = _admin(db_session, org)
    token = uuid.uuid4().hex[:8]
    filler = "z" * 30
    rows = "".join(f"Bad{i} {token},{filler},\n" for i in range(60))
    good = f"Good {token}"
    _post(client, login_as, user, HEADER + rows + f"{good},2.2 tactical,\n")
    assert set(_apps(db_session, org, [good])) == {good}
    text = _text(_flashes(client))
    assert "60 refused" in text, text
    assert f"Bad49 {token}" in text and f"Bad50 {token}" not in text, text
    assert "first 50" in text, text


def test_good_rows_land_in_the_importing_organisation_only(
    client, db_session, make_org, login_as
):
    from sqlalchemy import text as sql

    org_a, org_b = make_org("rbrf"), make_org("rbrg")
    user = _admin(db_session, org_a)
    a, b, c = _names()
    bad = "q" * 40
    _post(client, login_as, user, HEADER + f"{a},2.2 tactical,\n{b},{bad},\n{c},2.2 tactical,\n")
    assert set(_apps(db_session, org_a, [a, b, c])) == {a, c}
    other = db_session.execute(
        sql("SELECT count(*) FROM application_components WHERE organization_id = :o AND name IN (:a, :b, :c)"),
        {"o": org_b.id, "a": a, "b": b, "c": c},
    ).scalar()
    assert other == 0


def test_the_import_stops_after_five_database_errors_in_a_row(
    client, db_session, make_org, login_as
):
    from sqlalchemy import event
    from sqlalchemy.exc import OperationalError

    from app.models.application_layer import ApplicationComponent

    org = make_org("rbri")
    user = _admin(db_session, org)
    token = uuid.uuid4().hex[:8]
    good = [f"Fine{i} {token}" for i in range(2)]

    def boom(mapper, connection, target):
        if (target.name or "").startswith("Boom"):
            raise OperationalError("insert", {}, Exception("server closed the connection"))

    event.listen(ApplicationComponent, "before_insert", boom)
    try:
        rows = f"{good[0]},2.2 tactical,\n"
        rows += "".join(f"Boom{i} {token},2.2 tactical,\n" for i in range(8))
        rows += f"{good[1]},2.2 tactical,\n"
        _post(client, login_as, user, HEADER + rows)
    finally:
        event.remove(ApplicationComponent, "before_insert", boom)
    assert set(_apps(db_session, org, good)) == {good[0]}
    text = _text(_flashes(client))
    assert "stopped" in text and "1 created" in text, text
    assert "5 database errors in a row" in text, text
    _no_internals(text)


def test_database_errors_are_described_by_type_not_by_text():
    from sqlalchemy.exc import DataError, IntegrityError

    from app.modules.applications.routes.import_export_routes import _database_reason

    class Orig(Exception):
        def __init__(self, pgcode):
            super().__init__('duplicate key value violates unique constraint "ix_secret"')
            self.pgcode = pgcode

    unique = _database_reason(IntegrityError("s", {}, Orig("23505")), 4)
    missing = _database_reason(IntegrityError("s", {}, Orig("23502")), 4)
    toolong = _database_reason(DataError("s", {}, Orig("22001")), 4)
    other = _database_reason(RuntimeError("relation x does not exist"), 4)
    assert "already exists" in unique
    assert "required value is missing" in missing
    assert "too long or in the wrong format" in toolong
    assert "row 4" in other
    for reason in (unique, missing, toolong, other):
        _no_internals(reason)


def test_update_mode_names_the_refused_row_and_a_blank_cell_never_blanks_the_good_row(
    client, db_session, make_org, login_as
):
    from app.models.application_layer import ApplicationComponent

    org = make_org("rbre")
    user = _admin(db_session, org)
    a, b, _ = _names()
    for n in (a, b):
        db_session.add(ApplicationComponent(
            name=n, organization_id=org.id, deployment_status="production",
            lifecycle_status="2.2 tactical", description="kept description",
            business_owner="Pat Owner", version="1.0",
        ))
    db_session.flush()
    _post(
        client, login_as, user,
        "Name,Lifecycle Status,Description,Owner,Status,Version\n"
        f"{a},{'y' * 40},new,new owner,retired,9.9\n"
        f"{b},,,,,2.0\n",
        mode="update",
    )
    apps = _apps(db_session, org, [a, b])
    # The good row saved its new version and kept every value its blank cells left out.
    assert apps[b].version == "2.0"
    assert apps[b].description == "kept description"
    assert apps[b].business_owner == "Pat Owner"
    assert apps[b].deployment_status == "production"
    assert apps[b].lifecycle_status == "2.2 tactical"
    # The refused row changed nothing and is named with its reason.
    assert (apps[a].version, apps[a].business_owner) == ("1.0", "Pat Owner")
    text = _text(_flashes(client))
    assert "Row 2" in text and a in text, text
    assert "1 updated" in text and "1 refused" in text, text
