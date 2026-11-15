"""The relationships page reads one page of rows, element routes stay inside the organisation, the impact graph fences in one query.

Two real organisations: A holds the signed-in architect and B holds elements the architect must never reach. Some
relationship rows belong to A but end on an element of B (a crossing row).
"""

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import event

from tests.test_ba_tenant_and_authz import _login


def _person(db, org, role_name):
    from app.models.user import Role, User

    user = User(email="rel-%s@example.com" % uuid.uuid4().hex[:10], first_name="Rel", last_name="Tester",
                organization_id=org.id, confirmed=True, enterprise_role="enterprise_architect")
    role = Role.query.filter_by(name=role_name).first() or Role(name=role_name)
    db.session.add(role)
    db.session.add(user)
    db.session.flush()
    user.role = role
    db.session.flush()
    return user


def _element(db, org, name):
    from app.models.archimate_core import ArchiMateElement

    row = ArchiMateElement(organization_id=org.id, name=name, type="BusinessService", layer="Business")
    db.session.add(row)
    db.session.flush()
    return row


def _relate(db, org, source, target, type_="Serving"):
    from app.models.archimate_core import ArchiMateRelationship

    row = ArchiMateRelationship(organization_id=org.id, source_id=source.id, target_id=target.id, type=type_)
    db.session.add(row)
    db.session.flush()
    return row


@pytest.fixture
def estate(db_session, make_org):
    from app import db

    org_a, org_b = make_org("rel-a"), make_org("rel-b")
    tag = uuid.uuid4().hex[:6]
    hub = _element(db, org_a, "Hub A %s" % tag)
    mine = [_element(db, org_a, "Mine A %s %02d" % (tag, i)) for i in range(45)]
    for target in mine:
        _relate(db, org_a, hub, target)
    other_hub = _element(db, org_b, "Hub B %s" % tag)
    theirs = [_element(db, org_b, "Theirs B %s %02d" % (tag, i)) for i in range(10)]
    for target in theirs:
        _relate(db, org_b, other_hub, target)
    crossing = _relate(db, org_a, hub, other_hub)
    admin = _person(db, org_a, "Administrator")
    db.session.commit()
    return SimpleNamespace(org_a=org_a, org_b=org_b, hub=hub, mine=mine, other_hub=other_hub, theirs=theirs,
                           crossing=crossing, admin=admin, tag=tag)


def test_the_relationships_view_reads_one_page_of_rows(client, estate):
    from app.models.archimate_core import ArchiMateRelationship
    from app import db

    _login(client, estate.admin.id)
    loaded = []
    statements = []

    def counted(target, context):
        loaded.append(target.id)

    def seen(conn, cursor, statement, params, context, many):
        if "FROM archimate_relationships" in statement:
            statements.append(statement)

    event.listen(ArchiMateRelationship, "load", counted)
    event.listen(db.engine, "before_cursor_execute", seen)
    try:
        response = client.get("/architecture/relationships")
    finally:
        event.remove(ArchiMateRelationship, "load", counted)
        event.remove(db.engine, "before_cursor_execute", seen)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Page 1 of 3" in html
    assert len(loaded) <= 20, "the view loaded %d relationship rows for one page" % len(loaded)
    assert len(statements) <= 2, "the view ran %d relationship queries" % len(statements)
    assert estate.tag in html and "Theirs B %s" % estate.tag not in html


def test_an_element_with_no_organisation_lists_the_signed_in_organisations_neighbours(app, estate):
    from flask import g

    from app.modules.architecture.routes.archimate_crud.routes import _get_element_relationships

    unowned = SimpleNamespace(organization_id=None, archimate_element_id=estate.hub.id)
    with app.test_request_context("/"):
        g.current_org_id = estate.org_a.id
        found = _get_element_relationships(unowned, estate.hub.id)
    names = {r["target"]["name"] for r in found if r["direction"] == "outgoing"}
    assert names == {e.name for e in estate.mine}
    assert estate.other_hub.name not in names


@pytest.mark.parametrize("method,path", [
    ("get", "/architecture/elements/{b}"),
    ("get", "/architecture/elements/{b}/edit"),
    ("put", "/architecture/elements/{b}"),
    ("delete", "/architecture/elements/{b}"),
    ("get", "/architecture/api/elements/{b}/relationships"),
])
def test_cross_org_element_routes_answer_not_found(client, estate, method, path):
    from app.models.archimate_core import ArchiMateElement
    from app import db

    _login(client, estate.admin.id)
    target = estate.other_hub.id
    response = getattr(client, method)(path.format(b=target), **({"json": {"name": "Taken"}} if method == "put" else {}))
    assert response.status_code == 404
    assert db.session.get(ArchiMateElement, target) is not None


@pytest.mark.parametrize("method", ["put", "delete"])
def test_cross_org_relationship_routes_answer_not_found(client, estate, method):
    _login(client, estate.admin.id)
    other = _relation_of_b(estate)
    response = getattr(client, method)("/architecture/relationships/%d" % other, **({"json": {"type": "Flow"}} if method == "put" else {}))
    assert response.status_code == 404


def _relation_of_b(estate):
    from app.models.archimate_core import ArchiMateRelationship

    return ArchiMateRelationship.query.filter_by(organization_id=estate.org_b.id).first().id


def test_the_impact_graph_fences_every_level_in_one_query(app, db_session, make_org, tenant_ctx):
    from app import db
    from app.services.impact_graph import build_impact_graph

    org = make_org("rel-graph")
    tag = uuid.uuid4().hex[:6]

    def measure(count):
        centre = _element(db, org, "Centre %s %d" % (tag, count))
        for i in range(count):
            _relate(db, org, centre, _element(db, org, "Leaf %s %d %d" % (tag, count, i)))
        db.session.commit()
        db.session.expire_all()
        seen = []

        def record(conn, cursor, statement, params, context, many):
            if "FROM archimate_elements" in statement:
                seen.append(statement)

        event.listen(db.engine, "before_cursor_execute", record)
        try:
            with tenant_ctx(org.id):
                graph = build_impact_graph(centre.id, depth=2)
        finally:
            event.remove(db.engine, "before_cursor_execute", record)
        assert len(graph["nodes"]) == count + 1
        return len(seen)

    small, large = measure(3), measure(30)
    assert large == small, "30 nodes took %d element queries, 3 nodes took %d" % (large, small)
    assert large <= 6
