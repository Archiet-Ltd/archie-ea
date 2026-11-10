"""A person without write permission cannot change anything through the API.

The test walks the URL map: every route that accepts POST, PUT, PATCH or DELETE under an ``/api/``
path is called by a Viewer (a member whose organisation role is viewer, and an account whose platform
role carries no write permission), and each must answer 403 and leave every table as it was. An
Editor (organisation role architect) and an organisation administrator are then walked over the same
routes: neither may be turned away by the write check.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")

from app._bootstrap.write_access import READ_ONLY_ALLOWED as VIEWER_ALLOWED  # endpoint -> reason


def _is_api_path(rule: str) -> bool:
    return rule.startswith("/api/") or "/api/" in rule


def _write_routes(app):
    routes = []
    for rule in app.url_map.iter_rules():
        if rule.endpoint == "static" or not _is_api_path(rule.rule):
            continue
        for method in sorted(rule.methods & set(WRITE_METHODS)):
            routes.append((method, rule.rule, rule.endpoint))
    return sorted(routes)


def _url(rule: str) -> str:
    import re

    def fill(match):
        converter = (match.group(1) or "string").split(":")[0].split("(")[0]
        return "1" if converter in ("int", "float", "number") else "00000000-0000-0000-0000-000000000001" if converter == "uuid" else "x"

    return re.sub(r"<(?:([^:>]+):)?[^>]+>", lambda m: fill(m), rule)


def _org(db_session, label):
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:8]
    org = Organization(name=f"VW {label} {suffix}", slug=f"vw-{label}-{suffix}")
    db_session.add(org)
    db_session.flush()
    return org


def _member(db_session, org, org_role, platform_role="Architect"):
    from app.models.org_role import OrgRole
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(
        email=f"vw-{uuid.uuid4().hex[:10]}@example.com",
        first_name="Walk",
        last_name=org_role,
        organization_id=org.id,
        role=Role.query.filter_by(name=platform_role).first(),
        confirmed=True,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    OrgRole.set_role(org.id, user.id, org_role)
    if org_role == "org_admin":
        user.grant_org_admin()
    db_session.commit()
    return user


def _application(db_session, org, name):
    from app.models.application_portfolio import ApplicationComponent

    application = ApplicationComponent(name=name, organization_id=org.id)
    db_session.add(application)
    db_session.commit()
    return application


# Tables that grow when a person is turned away, by design: the sign-in the test makes for each call,
# and the audit entry that records the refusal. Neither is the model.
BOOKKEEPING_TABLES = ("user_sessions", "soc2_audit_log")


def _table_counts(db_session):
    """Row count of every table except the bookkeeping ones, so a write anywhere else shows up."""
    db_session.expire_all()
    names = [
        row[0]
        for row in db_session.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = current_schema()")
        )
    ]
    counts = {}
    for name in sorted(set(names) - set(BOOKKEEPING_TABLES)):
        counts[name] = db_session.execute(text(f'SELECT count(*) FROM "{name}"')).scalar()
    return counts


@pytest.fixture
def two_orgs(db_session):
    org_a, org_b = _org(db_session, "a"), _org(db_session, "b")
    _application(db_session, org_a, "Ledger A")
    _application(db_session, org_b, "Ledger B")
    return org_a, org_b


def _guard_answer(app, user, method, rule):
    """What the write guard says to ``user`` for this route: None (let through) or its refusal."""
    from flask import g

    hook = next(
        f for f in app.before_request_funcs[None] if f.__name__ == "_refuse_api_write_without_permission"
    )
    with app.test_request_context(_url(rule), method=method, json={}):
        g._login_user = user
        g.current_org_id = user.organization_id
        return hook()


def _sweep_guard(app, user, routes):
    return {(m, rule): _guard_answer(app, user, m, rule) for m, rule, _ in routes}


def _real_requests(client, login_as, user, routes):
    return {
        (m, rule): client.open(_url(rule), method=m, json={}).status_code
        for m, rule, _ in routes
        if (login_as(client, user) or True)
    }


def test_the_url_map_has_write_routes_to_walk(app):
    assert len(_write_routes(app)) > 100


SAMPLE_EVERY = 25


def _viewer(db_session, org, kind):
    if kind == "organisation viewer":
        return _member(db_session, org, "viewer")
    return _member(db_session, org, "viewer", platform_role="Viewer")


def test_the_guard_is_registered_on_the_app(app):
    names = [f.__name__ for f in app.before_request_funcs[None]]
    assert names.count("_refuse_api_write_without_permission") == 1


@pytest.mark.parametrize("kind", ["organisation viewer", "platform viewer"])
def test_the_guard_refuses_a_viewer_on_every_api_write_route(app, db_session, two_orgs, kind):
    viewer = _viewer(db_session, two_orgs[0], kind)
    routes = [r for r in _write_routes(app) if r[2] not in VIEWER_ALLOWED]

    answers = _sweep_guard(app, viewer, routes)

    from app.core.auth.permissions import READ_ONLY_CODE

    leaking = sorted(
        f"{m} {rule}"
        for (m, rule), answer in answers.items()
        if answer is None or answer[1] != 403 or READ_ONLY_CODE not in answer[0].get_data(as_text=True)
    )
    assert not leaking, f"{len(leaking)} of {len(routes)} write routes would not refuse a Viewer:\n" + "\n".join(leaking)


@pytest.mark.parametrize("kind", ["organisation viewer", "platform viewer"])
def test_a_sample_of_real_api_writes_by_a_viewer_is_refused_and_changes_nothing(
    app, client, db_session, login_as, two_orgs, kind
):
    viewer = _viewer(db_session, two_orgs[0], kind)
    routes = [r for r in _write_routes(app) if r[2] not in VIEWER_ALLOWED][::SAMPLE_EVERY]
    before = _table_counts(db_session)

    statuses = _real_requests(client, login_as, viewer, routes)

    leaking = sorted(f"{m} {rule} -> {code}" for (m, rule), code in statuses.items() if code != 403)
    assert not leaking, f"{len(leaking)} of {len(routes)} sampled write routes did not refuse a Viewer:\n" + "\n".join(leaking)
    assert _table_counts(db_session) == before


def test_the_allow_list_names_a_real_route_and_a_reason_for_every_entry(app):
    known = {endpoint for _, _, endpoint in _write_routes(app)}
    for endpoint, reason in VIEWER_ALLOWED.items():
        assert endpoint in known, f"{endpoint} is no longer a write route"
        assert len(reason) > 20, endpoint


def test_a_viewer_may_still_call_each_allow_listed_route(app, db_session, two_orgs):
    viewer = _member(db_session, two_orgs[0], "viewer")
    routes = [r for r in _write_routes(app) if r[2] in VIEWER_ALLOWED]
    assert {r[2] for r in routes} == set(VIEWER_ALLOWED)

    answers = _sweep_guard(app, viewer, routes)

    assert all(answer is None for answer in answers.values())


@pytest.mark.parametrize("org_role", ["architect", "org_admin"])
def test_an_editor_and_an_administrator_are_not_turned_away(app, db_session, two_orgs, org_role):
    person = _member(db_session, two_orgs[0], org_role)

    answers = _sweep_guard(app, person, _write_routes(app))

    turned_away = sorted(f"{m} {rule}" for (m, rule), answer in answers.items() if answer is not None)
    assert not turned_away, turned_away


def test_an_editor_creates_renames_and_deletes_an_application(client, db_session, login_as, two_orgs):
    org_a, _ = two_orgs
    editor = _member(db_session, org_a, "architect")
    login_as(client, editor)
    created = client.post("/api/v1/applications/", json={"name": "Editor made"})
    assert created.status_code in (200, 201), created.get_data(as_text=True)[:300]
    body = created.get_json()
    data = body.get("data", body)
    app_id = data.get("id") or data.get("application", {}).get("id")
    assert app_id
    login_as(client, editor)
    renamed = client.put(f"/api/v1/applications/{app_id}", json={"name": "Editor renamed"})
    assert renamed.status_code == 200, renamed.get_data(as_text=True)[:300]
    login_as(client, editor)
    removed = client.delete(f"/api/v1/applications/{app_id}")
    assert removed.status_code == 200, removed.get_data(as_text=True)[:300]


def test_a_viewer_cannot_create_rename_or_delete_an_application(client, db_session, login_as, two_orgs):
    from app.models.application_portfolio import ApplicationComponent

    org_a, _ = two_orgs
    viewer = _member(db_session, org_a, "viewer")
    target = ApplicationComponent.query.filter_by(organization_id=org_a.id).first()
    login_as(client, viewer)
    assert client.post("/api/v1/applications/", json={"name": "Viewer made"}).status_code == 403
    login_as(client, viewer)
    assert client.put(f"/api/v1/applications/{target.id}", json={"name": "Viewer renamed"}).status_code == 403
    login_as(client, viewer)
    assert client.delete(f"/api/v1/applications/{target.id}").status_code == 403
    db_session.expire_all()
    assert db_session.get(ApplicationComponent, target.id).name == "Ledger A"
