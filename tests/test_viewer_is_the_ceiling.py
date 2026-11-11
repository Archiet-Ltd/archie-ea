"""The organisation role is the ceiling: a viewer never writes, whatever their platform role or persona.

A member whose organisation role is viewer, whose platform role is Architect and who holds a
governance persona was admitted by the write guard and then by the route's own role check. These
tests send real writes as that person, in two real organisations, and also check the two rules
beside it: a read cell of the matrix never opens a write, and a route that writes with raw SQL is
held to the same route-level check as one that writes through the session.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from test_viewer_cannot_write_anywhere import _name_of, _with_persona
from test_viewer_cannot_write_through_the_api import (
    WRITE_METHODS,
    _application,
    _member,
    _org,
    _table_counts,
    _url,
)

PERSONAS = ["solution_architect", "cto", "arb_member"]


@pytest.fixture
def two_orgs(db_session):
    from app.services.billing_plans import set_contract_plan

    org_a, org_b = _org(db_session, "a"), _org(db_session, "b")
    for org in (org_a, org_b):
        set_contract_plan(org, "enterprise", None)
    db_session.commit()
    _application(db_session, org_a, "Ledger A")
    _application(db_session, org_b, "Ledger B")
    return org_a, org_b


def _ids(org_a, org_b):
    from app.models.application_portfolio import ApplicationComponent

    return (
        ApplicationComponent.query.filter_by(organization_id=org_a.id).first().id,
        ApplicationComponent.query.filter_by(organization_id=org_b.id).first().id,
    )


@pytest.mark.parametrize("persona", PERSONAS)
def test_a_viewer_with_a_persona_cannot_delete_an_application(client, db_session, login_as, two_orgs, persona):
    org_a, org_b = two_orgs
    person = _with_persona(db_session, _member(db_session, org_a, "viewer"), persona)
    mine_id, theirs_id = _ids(org_a, org_b)

    login_as(client, person)
    response = client.delete(f"/api/applications/{mine_id}")
    assert response.status_code == 403, response.get_data(as_text=True)[:200]
    assert response.get_json()["error"] == "Your role is read-only, so it cannot make this change."
    login_as(client, person)
    assert client.delete(f"/api/applications/{theirs_id}").status_code in (403, 404)

    db_session.expire_all()
    assert _name_of(db_session, mine_id) == "Ledger A"
    assert _name_of(db_session, theirs_id) == "Ledger B"


def test_the_same_person_as_an_editor_in_another_organisation_writes_there_only(
    client, db_session, login_as, two_orgs
):
    from app.models.org_role import OrgRole

    org_a, org_b = two_orgs
    person = _with_persona(db_session, _member(db_session, org_a, "viewer"), "solution_architect")
    mine_id, theirs_id = _ids(org_a, org_b)
    OrgRole.set_role(org_b.id, person.id, "architect")
    db_session.commit()

    # In organisation A (viewer) the delete is refused and the row stays.
    login_as(client, person)
    assert client.delete(f"/api/applications/{mine_id}").status_code == 403
    db_session.expire_all()
    assert _name_of(db_session, mine_id) == "Ledger A"

    # Open organisation B, where the person is an editor: the delete there succeeds.
    person.organization_id = org_b.id
    db_session.commit()
    login_as(client, person)
    response = client.delete(f"/api/applications/{theirs_id}")
    assert response.status_code == 200, response.get_data(as_text=True)[:200]
    db_session.expire_all()
    assert _name_of(db_session, theirs_id) is None
    assert _name_of(db_session, mine_id) == "Ledger A"


def _architect_role_write_routes(app):
    routes = []
    for rule in app.url_map.iter_rules():
        roles = getattr(app.view_functions.get(rule.endpoint), "role_guard", None)
        if not roles or "architect" not in roles:
            continue
        for method in sorted(rule.methods & set(WRITE_METHODS)):
            routes.append((method, rule.rule, rule.endpoint))
    return sorted(routes)


@pytest.mark.parametrize("persona", PERSONAS)
def test_a_viewer_with_a_persona_is_refused_every_write_route_that_admits_architects(
    app, client, db_session, login_as, two_orgs, persona
):
    from app._bootstrap.write_access import READ_ONLY_ALLOWED, READ_ONLY_ALLOWED_PAGES

    org_a, _ = two_orgs
    person = _with_persona(db_session, _member(db_session, org_a, "viewer"), persona)
    routes = [
        r
        for r in _architect_role_write_routes(app)
        if r[2] not in READ_ONLY_ALLOWED and r[2] not in READ_ONLY_ALLOWED_PAGES
    ]
    # The scan finds well over a hundred; a sweep over few routes would prove little.
    assert len(routes) >= 100, len(routes)
    before = _table_counts(db_session)
    answers = {}
    for method, rule, _endpoint in routes:
        login_as(client, person)
        answers[(method, rule)] = client.open(_url(rule), method=method, json={}).status_code
    not_refused = {key: status for key, status in answers.items() if status != 403}
    assert not not_refused, f"{len(not_refused)} of {len(routes)} routes did not answer 403: {not_refused}"
    db_session.expire_all()
    assert _table_counts(db_session) == before


def test_a_read_cell_of_the_matrix_never_opens_a_write(app, db_session, two_orgs):
    from types import SimpleNamespace

    from flask import g

    from app._bootstrap.write_access import _route_answers_for_itself

    org_a, _ = two_orgs
    editor = _member(db_session, org_a, "architect")

    def view_for(action):
        def view():
            return None

        view.matrix_guard = (action, "application")
        return view

    stub = SimpleNamespace(view_functions={"reads": view_for("read"), "edits": view_for("edit")})
    with app.test_request_context("/x", method="POST"):
        g._login_user = editor
        g.current_org_id = org_a.id
        assert _route_answers_for_itself(stub, "reads") is False
        assert _route_answers_for_itself(stub, "edits") is True


def test_a_raw_sql_write_by_a_person_admitted_only_for_a_persona_is_refused(app, db_session, two_orgs):
    from flask import g

    from app import db
    from app._bootstrap.write_access import ChangeNotGrantedByRoute

    org_a, _ = two_orgs
    mine_id, _ = _ids(*two_orgs)
    statement = text("UPDATE application_components SET name = 'Renamed by SQL' WHERE id = :id")

    with app.test_request_context("/x", method="POST"):
        g.write_admitted_for_route_check = True
        g.route_granted_write = False
        with pytest.raises(ChangeNotGrantedByRoute):
            db.session.execute(statement, {"id": mine_id})
    db.session.rollback()
    assert _name_of(db_session, mine_id) == "Ledger A"

    # Once the route's own check has granted the change, the same statement is stored.
    with app.test_request_context("/x", method="POST"):
        g.write_admitted_for_route_check = True
        g.route_granted_write = True
        db.session.execute(statement, {"id": mine_id})
    assert _name_of(db_session, mine_id) == "Renamed by SQL"
