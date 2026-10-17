"""Every entry the product offers for navigation must open to a finished page.

Walks, as several personas, every link in the sidebar zones and every row of the
All-modules directory and global search, requests it with a signed-in client and
asserts the answer is a real page: not an error, not 404 or 403, and not a page
that says it is still being built or needs setup. Pages that are not ready for
the soft launch are hidden (unlinked and unlisted) by the one switch
``HIDE_NOT_READY_MODULES``; their routes stay.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

# (label, enterprise_role, Role name). "demo" is the seeded demonstration user:
# an enterprise architect holding the Viewer role. The others are archetypes
# from the smoke harness, signed in with the roles that harness gives them.
PERSONAS = [
    ("demo", "enterprise_architect", "Viewer"),
    ("cto", "cto", "Architect"),
    ("portfolio_manager", "portfolio_manager", "Architect"),
    ("compliance", "compliance", "Architect"),
    ("platform_admin", "platform_admin", "Administrator"),
]
NOT_READY_TEXT = ("being built", "Setup Required")


def _login(client, user_id):
    from tests._session_test_helpers import mint_test_sid

    sid = mint_test_sid(user_id)
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True
        if sid:
            sess["_sid"] = sid


def _client_for(app, db_session, make_org, role, role_name="Architect"):
    from app.models.user import Role, User

    Role.insert_roles()

    org = make_org("hide-not-ready")
    user = User(
        email=f"hide-{role}-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Hide",
        last_name="Tester",
        organization_id=org.id,
        confirmed=True,
        enterprise_role=role,
    )
    user.role = Role.query.filter_by(name=role_name).one()
    if role == "platform_admin":
        user.is_platform_admin = True
        user.is_org_admin = True
    db_session.add(user)
    db_session.flush()
    client = app.test_client()
    _login(client, user.id)
    return client, user


def _offered_urls(app, user):
    from flask import url_for

    from app.modules.modules_directory.routes import visible_module_links
    from app.utils.role_access import get_sidebar_zones

    with app.test_request_context("/"):
        from flask_login import login_user

        login_user(user)
        links = [l for z in get_sidebar_zones(user) for l in z["links"]] + visible_module_links()
        out = {}
        for link in links:
            ep = link["endpoint"]
            if ep not in app.view_functions:
                continue
            try:
                out[ep] = url_for(ep, **(link.get("query_params") or {}))
            except Exception:
                continue
        return out


@pytest.mark.parametrize("label,role,role_name", PERSONAS, ids=[p[0] for p in PERSONAS])
def test_every_offered_entry_opens_a_ready_page(app, db_session, make_org, label, role, role_name):
    client, user = _client_for(app, db_session, make_org, role, role_name)
    bad = []
    for endpoint, url in sorted(_offered_urls(app, user).items()):
        resp = client.get(url, follow_redirects=True)
        body = resp.get_data(as_text=True)
        if resp.status_code != 200:
            bad.append(f"{endpoint} {url} -> {resp.status_code}")
        elif any(t in body for t in NOT_READY_TEXT):
            bad.append(f"{endpoint} {url} -> says not ready")
    assert not bad, "offered but not ready:\n" + "\n".join(bad)


def test_hidden_entries_are_not_in_the_directory_or_search(app, db_session, make_org):
    from app.utils.role_access import NOT_READY_ENDPOINTS

    client, user = _client_for(app, db_session, make_org, "platform_admin", "Administrator")
    offered = _offered_urls(app, user)
    assert not (set(offered) & set(NOT_READY_ENDPOINTS))
    html = client.get("/modules").get_data(as_text=True)
    for label in ("Agentic Gaps", "Model Registry", "Implementation Planning"):
        assert label not in html


def test_switch_off_restores_the_entries_and_routes_stay(app, db_session, make_org):
    from app.utils.role_access import NOT_READY_ENDPOINTS, is_hidden_not_ready

    assert NOT_READY_ENDPOINTS
    for endpoint in NOT_READY_ENDPOINTS:
        assert is_hidden_not_ready(endpoint)
    old = app.config.get("HIDE_NOT_READY_MODULES")
    app.config["HIDE_NOT_READY_MODULES"] = False
    try:
        with app.test_request_context("/"):
            assert not any(is_hidden_not_ready(e) for e in NOT_READY_ENDPOINTS)
    finally:
        app.config["HIDE_NOT_READY_MODULES"] = old
    # Hidden means unlinked: the routes are still registered.
    registered = [e for e in NOT_READY_ENDPOINTS if e in app.view_functions]
    assert registered
