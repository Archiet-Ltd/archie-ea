"""The connector dashboard points at the Sync that exists, only for a person who can open it."""

import pytest
from flask import render_template

pytestmark = pytest.mark.usefixtures("db_session")


def _render(app, allowed):
    with app.test_request_context("/integrations/connectors"):
        return render_template("integrations/connector_dashboard.html", can_open_connectors=allowed)


def test_link_to_connectors_page_shown_when_the_person_can_open_it(app):
    html = _render(app, True)
    assert "Open the Connectors page" in html
    assert 'href="/admin/connectors"' in html
    assert "cannot be started from this page" not in html
    assert "Manual sync is not available" not in html


def test_person_who_cannot_open_it_is_told_who_can_and_gets_no_link(app):
    html = _render(app, False)
    assert "Only an administrator of your organisation can start a sync" in html
    assert "Open the Connectors page" not in html
    assert 'href="/admin/connectors"' not in html


def test_role_helper_follows_the_connector_routes_rule(app):
    from types import SimpleNamespace

    from app.utils.role_access import CONNECTION_ARCHITECT_ROLES, may_open_connector_pages

    allowed_role = sorted(CONNECTION_ARCHITECT_ROLES)[0]
    with app.test_request_context("/"):
        assert may_open_connector_pages(SimpleNamespace(enterprise_role=allowed_role), None) is True
        assert may_open_connector_pages(SimpleNamespace(enterprise_role="no_such_role"), None) is False
