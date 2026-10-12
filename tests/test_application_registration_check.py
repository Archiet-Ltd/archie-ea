"""The duplicate check shown before a new application is registered.

Matching is the intelligence matcher's (identifier, normalised name, near
duplicate); these tests pin what the registration screen is shown and that one
organisation never sees another's applications.
"""

import uuid

import pytest


def _clear_login_cache():
    from flask import g, has_app_context

    if not has_app_context():
        return
    for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
        if hasattr(g, cached):
            delattr(g, cached)


def _client_for(app, db_session, org, role="platform_admin"):
    from app.models.user import User
    from tests._session_test_helpers import mint_test_sid

    user = User(
        email=f"regcheck-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Reg",
        last_name="Check",
        organization_id=org.id,
        confirmed=True,
        enterprise_role=role,
    )
    db_session.add(user)
    db_session.flush()
    client = app.test_client()
    sid = mint_test_sid(user.id)
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True
        if sid:
            sess["_sid"] = sid
    _clear_login_cache()
    return client, user


@pytest.fixture
def two_orgs(app, db_session, make_org):
    org_a, org_b = make_org("regcheck-a"), make_org("regcheck-b")
    client_a, user_a = _client_for(app, db_session, org_a)
    client_b, user_b = _client_for(app, db_session, org_b)
    return {"a": (org_a, client_a, user_a), "b": (org_b, client_b, user_b)}


def _create(client, user, **payload):
    _clear_login_cache()
    resp = client.post("/applications/create", json=payload)
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()["id"]


def _check(client, **params):
    _clear_login_cache()
    return client.get("/applications/api/registration-check", query_string=params)


def test_same_name_is_reported_as_same_name(two_orgs):
    _, client, user = two_orgs["a"]
    app_id = _create(client, user, name="Orbit Billing Platform")
    resp = _check(client, name="  orbit   billing platform ")
    assert resp.status_code == 200
    matches = resp.get_json()["matches"]
    assert [m["id"] for m in matches] == [app_id]
    assert matches[0]["match"] == "same_name"
    assert matches[0]["url"].endswith("/%d" % app_id)


def test_near_duplicate_name_is_reported_as_similar(two_orgs):
    _, client, user = two_orgs["a"]
    app_id = _create(client, user, name="Orbit Billing Platform")
    matches = _check(client, name="Orbit Billing Platform v2").get_json()["matches"]
    assert [(m["id"], m["match"]) for m in matches] == [(app_id, "similar_name")]


def test_same_application_code_is_reported_first(two_orgs):
    _, client, user = two_orgs["a"]
    code_id = _create(client, user, name="Ledger Core", application_code="LEDGER-1")
    _create(client, user, name="Ledger Archive")
    matches = _check(client, name="Ledger Archive", application_code="ledger-1").get_json()[
        "matches"
    ]
    assert matches[0]["id"] == code_id
    assert matches[0]["match"] == "same_code"


def test_unrelated_name_has_no_matches(two_orgs):
    _, client, user = two_orgs["a"]
    _create(client, user, name="Orbit Billing Platform")
    resp = _check(client, name="Zephyr Telemetry Gateway")
    assert resp.status_code == 200
    assert resp.get_json()["matches"] == []


def test_blank_query_has_no_matches(two_orgs):
    _, client, _ = two_orgs["a"]
    assert _check(client).get_json() == {"success": True, "matches": []}


def test_other_organisation_never_sees_matches(two_orgs):
    _, client_a, user_a = two_orgs["a"]
    _, client_b, _ = two_orgs["b"]
    _create(client_a, user_a, name="Orbit Billing Platform", application_code="ORB-1")
    body = _check(client_b, name="Orbit Billing Platform", application_code="ORB-1").get_json()
    assert body["matches"] == []


def test_failed_check_is_an_error_not_an_empty_list(two_orgs, monkeypatch):
    _, client, _ = two_orgs["a"]

    def boom(*args, **kwargs):
        raise RuntimeError("matcher down")

    monkeypatch.setattr(
        "app.modules.applications.services.registration_check_service.MatcherService.match_by_name",
        boom,
    )
    resp = _check(client, name="Orbit Billing Platform")
    assert resp.status_code == 500
    assert resp.get_json()["success"] is False


def test_requires_sign_in(app, db_session):
    _clear_login_cache()
    resp = app.test_client().get("/applications/api/registration-check?name=x")
    assert resp.status_code in (302, 401)


def test_read_only_role_is_refused_the_check_and_the_create(app, db_session, make_org):
    role_name = "Viewer"
    from app.models.user import Role

    Role.insert_roles()
    org = make_org("regcheck-role-%s" % role_name.lower())
    client, user = _client_for(app, db_session, org, role="solution_architect")
    user.role = Role.query.filter_by(name=role_name).one()
    db_session.flush()
    resp = _check(client, name="Orbit Billing Platform")
    assert resp.status_code == 403
    # the create route is gated the same way, so the check never offers more than the save allows
    _clear_login_cache()
    assert client.post("/applications/create", json={"name": "Refused %s" % role_name}).status_code == 403
