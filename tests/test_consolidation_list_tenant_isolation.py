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

import contextlib
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


def _two_org_world(db_session, make_org):
    """Two organisations, each with an application and a consolidation entry.

    Org A's application carries a known TCO so ``recalculate_savings`` has a
    deterministic value to write; org B's entry carries a sentinel
    ``estimated_savings`` that must survive untouched when the caller is org A.
    """
    from app.models.application_portfolio import ApplicationComponent
    from app.modules.governance.models.consolidation_list import ConsolidationListEntry

    org_a = make_org("recalc-a")
    org_b = make_org("recalc-b")
    user_a = _user(db_session, org_a)
    user_b = _user(db_session, org_b)

    app_a = ApplicationComponent(
        name="Org A's app", organization_id=org_a.id, total_cost_of_ownership=1000.0
    )
    app_b = ApplicationComponent(
        name="Org B's app", organization_id=org_b.id, total_cost_of_ownership=2000.0
    )
    db_session.add_all([app_a, app_b])
    db_session.flush()

    entry_a = ConsolidationListEntry(
        application_id=app_a.id,
        source_group_name="probe",
        recommended_action="pending_review",
        priority="high",
    )
    entry_b = ConsolidationListEntry(
        application_id=app_b.id,
        source_group_name="probe",
        recommended_action="pending_review",
        priority="high",
        estimated_savings=999.0,
    )
    db_session.add_all([entry_a, entry_b])
    db_session.commit()

    return user_a.id, user_b.id, entry_a.id, entry_b.id, app_a.id, app_b.id


@contextlib.contextmanager
def _loaded_entry_ids():
    """A context manager that records every ``ConsolidationListEntry`` id the code under
    test loads from the database, so a test can assert another organisation's entry was
    never even read.

    ``recalculate_savings`` and ``score_all_entries`` used to iterate
    ``ConsolidationListEntry.query.all()`` — an unfenced read that loads every
    organisation's entries into memory before the per-entry application lookup skips the
    foreign ones. The org-scoped query helper (``_entries_query``, an INNER join to the
    tenant-fenced ``ApplicationComponent``) excludes them at the query level instead. The
    ``load`` event fires on the class regardless of which session issues the query, so it
    observes that cross-tenant read directly rather than relying on the write-back (which
    the inner ``ApplicationComponent.query.get`` happens to fence).
    """
    from sqlalchemy import event

    from app.modules.governance.models.consolidation_list import ConsolidationListEntry

    loaded = []

    def _on_load(target, _context):
        loaded.append(target.id)

    event.listen(ConsolidationListEntry, "load", _on_load)
    try:
        yield loaded
    finally:
        event.remove(ConsolidationListEntry, "load", _on_load)


def test_recalculate_savings_does_not_read_or_write_another_organisations_entry(
    app, db_session, make_org, client, login_as
):
    user_a_id, _user_b_id, entry_a_id, entry_b_id, _app_a_id, _app_b_id = _two_org_world(
        db_session, make_org
    )

    _login(db_session, client, login_as, user_a_id)
    with _loaded_entry_ids() as loaded:
        response = client.post("/consolidation-list/api/recalculate-savings")

    assert response.status_code == 200
    # Org A's own entry is recalculated from its application's TCO.
    assert (
        db_session.execute(
            text("select estimated_savings from consolidation_list_entries where id = :i"),
            {"i": entry_a_id},
        ).scalar()
        == 1000.0
    )
    # Org B's entry keeps its sentinel value: the recalculation never touched it.
    assert (
        db_session.execute(
            text("select estimated_savings from consolidation_list_entries where id = :i"),
            {"i": entry_b_id},
        ).scalar()
        == 999.0
    )
    # And it was never even read: the org-scoped query excluded it at the source.
    assert entry_a_id in loaded
    assert entry_b_id not in loaded


def test_score_all_entries_does_not_read_or_score_another_organisations_entry(
    app, db_session, make_org, client, login_as
):
    user_a_id, _user_b_id, entry_a_id, entry_b_id, app_a_id, app_b_id = _two_org_world(
        db_session, make_org
    )

    _login(db_session, client, login_as, user_a_id)
    with _loaded_entry_ids() as loaded:
        response = client.post("/consolidation-list/api/score-all")

    assert response.status_code == 200
    # Org A's own entry gained a rationalization score.
    assert (
        db_session.execute(
            text(
                "select count(*) from application_rationalization_scores "
                "where application_component_id = :i"
            ),
            {"i": app_a_id},
        ).scalar()
        == 1
    )
    # Org B's entry gained no score and its action is untouched.
    assert (
        db_session.execute(
            text(
                "select count(*) from application_rationalization_scores "
                "where application_component_id = :i"
            ),
            {"i": app_b_id},
        ).scalar()
        == 0
    )
    assert (
        db_session.execute(
            text("select recommended_action from consolidation_list_entries where id = :i"),
            {"i": entry_b_id},
        ).scalar()
        == "pending_review"
    )
    # And it was never even read: the org-scoped query excluded it at the source.
    assert entry_a_id in loaded
    assert entry_b_id not in loaded
