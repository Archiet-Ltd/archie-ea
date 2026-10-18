"""Surfaces retired rather than folded: no model facts behind the page, no
buyer question it answers on its own.

/application-management/ is a 302 alias onto the Application Management
dashboard the Applications list already covers under its own name; it keeps
answering the redirect this release, it just stops being offered a second
time in the modules directory and global search. The other surface here was
a template-only page for a feature the roadmap now designs a different way;
its route, template and script are gone outright.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _make_user(db_session, org, *, enterprise_role="enterprise_architect"):
    from app.models.user import User

    suffix = uuid.uuid4().hex[:8]
    user = User(email=f"{enterprise_role}-{suffix}@retired-surfaces.test")
    user.organization_id = org.id
    user.enterprise_role = enterprise_role
    user.confirmed = True
    user.password = "RetiredSurfaces!12345"
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture
def org(make_org):
    return make_org("retired-surfaces")


# ---------------------------------------------------------------------------
# The alias: still answers, no longer offered twice.
# ---------------------------------------------------------------------------


def test_application_management_redirects_to_applications_dashboard(
    app, db_session, org, client, login_as
):
    from flask import url_for

    user = _make_user(db_session, org)
    login_as(client, user)

    with app.test_request_context():
        target = url_for("application_mgmt.dashboard")

    response = client.get("/application-management/")
    assert response.status_code == 302
    assert response.headers["Location"].endswith(target)


def test_application_management_in_not_rendered_and_hidden_from_visible_links(
    app, db_session, org, client, login_as
):
    from app.modules.modules_directory.routes import _NOT_RENDERED, visible_module_links
    from flask_login import login_user

    user = _make_user(db_session, org)

    assert "application_management" in _NOT_RENDERED

    with app.test_request_context("/"):
        login_user(user)
        searchable = {link["endpoint"] for link in visible_module_links()}
    assert "application_management" not in searchable


def test_application_management_url_map_has_exactly_one_rule(app):
    rules = [
        rule
        for rule in app.url_map.iter_rules()
        if rule.rule == "/application-management/"
    ]
    assert len(rules) == 1, f"expected exactly one rule, found {rules}"


# ---------------------------------------------------------------------------
# The other retired surface: gone outright, not just hidden.
# ---------------------------------------------------------------------------


def test_agent_implementation_endpoint_is_gone(app):
    assert "main.agentic_gaps_ui" not in app.view_functions


def test_agent_implementation_old_path_answers_404(app, db_session, org, client, login_as):
    user = _make_user(db_session, org)
    login_as(client, user)

    response = client.get("/agentic-gaps")
    assert response.status_code == 404


def test_modules_page_contains_neither_retired_label(app, db_session, org, client, login_as):
    user = _make_user(db_session, org)
    login_as(client, user)

    html = client.get("/modules").get_data(as_text=True)
    assert "Application Management" not in html
    assert "Agentic Gaps" not in html
