"""The application spreadsheet import saves the good rows and names each refused row.

One bad row never rolls back the file, and a row-level problem is never reported as a
whole-file failure.
"""
from __future__ import annotations

import io
import uuid

import pytest

HEADER = "Name,Lifecycle Status,Criticality\n"


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


def test_over_long_unknown_lifecycle_refuses_that_row_only(
    client, db_session, make_org, login_as
):
    org = make_org("rbra")
    user = _admin(db_session, org)
    a, b, c = _names()
    long_value = "x" * 40
    _post(client, login_as, user, HEADER + f"{a},operational,\n{b},{long_value},\n{c},planning,\n")
    apps = _apps(db_session, org, [a, b, c])
    assert set(apps) == {a, c}
    flashes = _flashes(client)
    text = _text(flashes)
    assert "Row 3" in text and b in text and long_value in text, text
    assert not any(m == "Error importing file. Please try again." for _, m in flashes)
    assert "2 created" in text and "1 refused" in text, text


def test_a_row_that_breaks_a_database_limit_is_refused_alone(
    client, db_session, make_org, login_as
):
    org = make_org("rbrb")
    user = _admin(db_session, org)
    a, b, c = _names()
    _post(client, login_as, user, HEADER + f"{a},planning,High\n{b},planning,{"c" * 80}\n{c},planning,Low\n")
    apps = _apps(db_session, org, [a, b, c])
    assert set(apps) == {a, c}
    flashes = _flashes(client)
    text = _text(flashes)
    assert "Row 3" in text and b in text, text
    assert not any(m == "Error importing file. Please try again." for _, m in flashes)


def test_a_known_long_lifecycle_label_is_mapped_and_saved(
    client, db_session, make_org, login_as
):
    org = make_org("rbrc")
    user = _admin(db_session, org)
    a, b, c = _names()
    _post(
        client, login_as, user,
        HEADER + f"{a},In production,\n{b},End of life,\n{c},In development,\n",
    )
    apps = _apps(db_session, org, [a, b, c])
    assert {k: v.lifecycle_status for k, v in apps.items()} == {
        a: "operational", b: "deprecated", c: "development",
    }
    assert "refused" not in _text(_flashes(client))


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
            lifecycle_status="planning", description="old",
        ))
    db_session.flush()
    _post(
        client, login_as, user,
        "Name,Lifecycle Status,Description\n"
        f"{a},{"y" * 40},new\n{b},operational,new\n",
        mode="update",
    )
    apps = _apps(db_session, org, [a, b])
    assert (apps[a].lifecycle_status, apps[a].description) == ("planning", "old")
    assert (apps[b].lifecycle_status, apps[b].description) == ("operational", "new")
    text = _text(_flashes(client))
    assert "Row 2" in text and a in text, text
    assert "1 updated" in text and "1 refused" in text, text


def test_refused_list_is_capped_at_fifty_with_the_total(
    client, db_session, make_org, login_as
):
    org = make_org("rbre")
    user = _admin(db_session, org)
    token = uuid.uuid4().hex[:8]
    rows = "".join(f"Bad{i} {token},{"z" * 30},\n" for i in range(60))
    good = f"Good {token}"
    _post(client, login_as, user, HEADER + rows + f"{good},planning,\n")
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
    _post(client, login_as, user, HEADER + f"{a},planning,\n{b},{"q" * 40},\n{c},planning,\n")
    assert set(_apps(db_session, org_a, [a, b, c])) == {a, c}
    other = db_session.execute(
        sql("SELECT count(*) FROM application_components WHERE organization_id = :o AND name IN (:a, :b, :c)"),
        {"o": org_b.id, "a": a, "b": b, "c": c},
    ).scalar()
    assert other == 0
