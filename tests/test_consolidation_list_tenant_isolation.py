"""``ConsolidationListEntry`` (app/modules/governance/models/consolidation_list.py) carries no
organisation column of its own — it is scoped only through ``application_id``, a foreign key to
``ApplicationComponent`` (which does have one).

The routes in ``app/modules/governance/routes/consolidation_list_routes.py`` (the only registered
implementation — every registration tier in ``app/_bootstrap/blueprints.py::_register_governance``
imports this same file) fetched entries by id with plain ``ConsolidationListEntry.query.get(...)``/
``.get_or_404(...)``, with no join back to the owning application and therefore no tenant fence at
all: any authenticated user of any organisation who knew or guessed another organisation's entry id
could read, edit or delete it. The list endpoint (``/api/entries``) used a LEFT OUTER join to
``ApplicationComponent`` — the automatic tenant filter installed in
``app/middleware/tenant_isolation.py`` pushes its predicate into the join's ON clause for an outer
join precisely so it does not exclude the left (unmatched) side, so another organisation's entries
still came back, just with null application columns.

The fix scopes every entry lookup through an INNER join to ``ApplicationComponent``, reusing the
existing ``TenantMixin``/``do_orm_execute`` fence on that model (``app/middleware/tenant_isolation.py``)
rather than adding a second, hand-rolled organisation check.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text


def _user(db_session, org):
    from app.models import Role
    from app.models.user import User

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        Role.insert_roles()
        role = Role.query.filter_by(name="Administrator").first()
    user = User(
        email=f"cl-{uuid.uuid4().hex[:6]}@example.test",
        first_name="Consolidation",
        last_name="Tester",
        organization_id=org.id,
        confirmed=True,
        role=role,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    return user


def _login(db_session, client, login_as, user_id):
    from app.models.user import User

    db_session.expunge_all()
    login_as(client, db_session.get(User, user_id))


def _world(db_session, make_org):
    from app.models.application_portfolio import ApplicationComponent
    from app.modules.governance.models.consolidation_list import ConsolidationListEntry

    org_a = make_org("consolidation-a")
    org_b = make_org("consolidation-b")
    user_a = _user(db_session, org_a)
    user_b = _user(db_session, org_b)

    app_a = ApplicationComponent(name="Org A's app", organization_id=org_a.id)
    db_session.add(app_a)
    db_session.flush()

    entry_a = ConsolidationListEntry(
        application_id=app_a.id,
        source_group_name="probe",
        recommended_action="pending_review",
        priority="high",
        notes="org A's private note",
    )
    db_session.add(entry_a)
    db_session.commit()

    return user_a.id, user_b.id, entry_a.id, app_a.id


def test_the_entry_list_does_not_include_another_organisations_entry(
    app, db_session, make_org, client, login_as
):
    _user_a_id, user_b_id, entry_a_id, _app_a_id = _world(db_session, make_org)

    _login(db_session, client, login_as, user_b_id)
    response = client.get("/consolidation-list/api/entries")

    assert response.status_code == 200
    ids = [e["id"] for e in response.get_json()["entries"]]
    assert entry_a_id not in ids


def test_a_user_cannot_read_another_organisations_entry_detail_by_id(
    app, db_session, make_org, client, login_as
):
    _user_a_id, user_b_id, entry_a_id, _app_a_id = _world(db_session, make_org)

    _login(db_session, client, login_as, user_b_id)
    response = client.get(f"/consolidation-list/api/entry/{entry_a_id}/detail")

    assert response.status_code == 404


def test_a_user_cannot_update_another_organisations_entry(
    app, db_session, make_org, client, login_as
):
    _user_a_id, user_b_id, entry_a_id, _app_a_id = _world(db_session, make_org)

    _login(db_session, client, login_as, user_b_id)
    response = client.put(
        f"/consolidation-list/api/entry/{entry_a_id}", json={"priority": "low"}
    )

    assert response.status_code == 404
    assert (
        db_session.execute(
            text("select priority from consolidation_list_entries where id = :i"),
            {"i": entry_a_id},
        ).scalar()
        == "high"
    )


def test_a_user_cannot_delete_another_organisations_entry(
    app, db_session, make_org, client, login_as
):
    _user_a_id, user_b_id, entry_a_id, _app_a_id = _world(db_session, make_org)

    _login(db_session, client, login_as, user_b_id)
    response = client.delete(f"/consolidation-list/api/entry/{entry_a_id}")

    assert response.status_code == 404
    assert (
        db_session.execute(
            text("select count(*) from consolidation_list_entries where id = :i"),
            {"i": entry_a_id},
        ).scalar()
        == 1
    )


def test_bulk_action_cannot_touch_another_organisations_entry(
    app, db_session, make_org, client, login_as
):
    _user_a_id, user_b_id, entry_a_id, _app_a_id = _world(db_session, make_org)

    _login(db_session, client, login_as, user_b_id)
    response = client.post(
        "/consolidation-list/api/bulk-action",
        json={"entry_ids": [entry_a_id], "action": "remove"},
    )

    assert response.status_code == 200
    assert response.get_json()["updated_count"] == 0
    assert (
        db_session.execute(
            text("select count(*) from consolidation_list_entries where id = :i"),
            {"i": entry_a_id},
        ).scalar()
        == 1
    )


def test_the_owning_organisation_can_still_read_update_and_delete_its_own_entry(
    app, db_session, make_org, client, login_as
):
    user_a_id, _user_b_id, entry_a_id, _app_a_id = _world(db_session, make_org)

    _login(db_session, client, login_as, user_a_id)
    listed = client.get("/consolidation-list/api/entries")
    detail = client.get(f"/consolidation-list/api/entry/{entry_a_id}/detail")
    updated = client.put(
        f"/consolidation-list/api/entry/{entry_a_id}", json={"priority": "low"}
    )

    assert listed.status_code == 200
    assert entry_a_id in [e["id"] for e in listed.get_json()["entries"]]
    assert detail.status_code == 200
    assert updated.status_code == 200
    assert (
        db_session.execute(
            text("select priority from consolidation_list_entries where id = :i"),
            {"i": entry_a_id},
        ).scalar()
        == "low"
    )
