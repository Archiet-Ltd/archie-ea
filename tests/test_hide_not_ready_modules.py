"""Every entry the product offers for navigation must open to a finished page.

Walks, as several personas, every link in the sidebar zones and every row of the
All-modules directory and global search, requests it with a signed-in client and
asserts the answer is a real page: not an error, not 404 or 403, and not a page
that says it is still being built or needs setup. Pages that are not ready for
the soft launch are listed on the All-modules page with their recorded reason as a
one-line limit (T-launch-02): they are excluded from the "opens a ready page" walk
because the page states they are not ready. ``HIDE_NOT_READY_MODULES`` defaults off
and no longer drops them from the directory or search; their routes stay.
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
        links = [item for z in get_sidebar_zones(user) for item in z["links"]] + visible_module_links()
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
    from app.utils.role_access import NOT_READY_ENDPOINTS

    bad = []
    for endpoint, url in sorted(_offered_urls(app, user).items()):
        if endpoint in NOT_READY_ENDPOINTS:
            continue  # listed with its limit line, not claimed as ready
        if endpoint == "modules_directory.index":
            continue  # this page prints those limit lines, so the "not ready" wording is expected here
        resp = client.get(url, follow_redirects=True)
        body = resp.get_data(as_text=True)
        if resp.status_code != 200:
            bad.append(f"{endpoint} {url} -> {resp.status_code}")
        elif any(t in body for t in NOT_READY_TEXT):
            bad.append(f"{endpoint} {url} -> says not ready")
    assert not bad, "offered but not ready:\n" + "\n".join(bad)


def test_not_ready_entries_are_listed_in_the_directory_and_search(app, db_session, make_org):
    from app.utils.role_access import NOT_READY_ENDPOINTS

    client, user = _client_for(app, db_session, make_org, "platform_admin", "Administrator")
    offered = _offered_urls(app, user)
    assert set(NOT_READY_ENDPOINTS) & set(app.view_functions) <= set(offered)
    html = client.get("/modules").get_data(as_text=True)
    for label in ("Agentic Gaps", "Model Registry", "Implementation Planning"):
        assert label in html


def test_switch_defaults_off_and_routes_stay(app, db_session, make_org):
    from app.utils.role_access import NOT_READY_ENDPOINTS, is_hidden_not_ready

    assert NOT_READY_ENDPOINTS
    with app.test_request_context("/"):
        assert not any(is_hidden_not_ready(e) for e in NOT_READY_ENDPOINTS)
    registered = [e for e in NOT_READY_ENDPOINTS if e in app.view_functions]
    assert registered
