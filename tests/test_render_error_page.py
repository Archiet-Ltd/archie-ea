"""A template error on a signed-in page must show the styled error page.

A page whose template fails to render used to answer with the bare server
"Internal Server Error" text. These tests make a signed-in page fail while it
renders, and check what the person sees: the styled page, in plain words, with
a way back.
"""

import pytest

from tests.test_ba_tenant_and_authz import _cleanup_ids, _login, _make_org_id, _make_user_id


@pytest.fixture
def signed_in(app, client):
    from app import db
    from app.models.organization import Organization
    from app.models.user import User

    with app.app_context():
        org = _make_org_id(db, "RenderErr")
        uid = _make_user_id(db, org, "RenderErrEA", enterprise_role="enterprise_architect")
        yield uid
        _cleanup_ids(db, User, [uid])
        _cleanup_ids(db, Organization, [org])


def _add_broken_page(app):
    from flask import render_template_string

    name = "render_error_probe"
    if name in app.view_functions:
        return

    def probe():
        # A macro called with an argument it does not take: a render-time error.
        return render_template_string(
            "{% from 'components/button.html' import button %}"
            "{{ button(text='Go', not_an_argument='x') }}"
        )

    app.add_url_rule("/__render_error_probe", name, probe)


def test_render_error_on_signed_in_page_shows_styled_error_page(app, client, signed_in):
    _add_broken_page(app)
    previous = app.config.get("PROPAGATE_EXCEPTIONS")
    app.config["PROPAGATE_EXCEPTIONS"] = False
    try:
        with app.app_context():
            _login(client, signed_in)
            response = client.get("/__render_error_probe", headers={"Accept": "text/html"})
    finally:
        app.config["PROPAGATE_EXCEPTIONS"] = previous
    html = response.get_data(as_text=True)
    assert response.status_code == 500
    assert 'data-testid="plain-error"' in html
    assert "Something went wrong on our side" in html
    assert 'href="/dashboard/overview"' in html
    assert "Internal Server Error" not in html


def test_error_page_still_has_a_way_back_when_both_error_templates_fail(app, client, signed_in, monkeypatch):
    """If even the error templates cannot render, the person still reads plain
    words and has a link back, not the bare server text."""
    import app.utils.plain_errors as plain_errors

    _add_broken_page(app)

    real = plain_errors.render_template

    def failing(name, **context):
        if name.startswith("errors/"):
            raise RuntimeError("error template cannot render")
        return real(name, **context)

    monkeypatch.setattr(plain_errors, "render_template", failing)
    previous = app.config.get("PROPAGATE_EXCEPTIONS")
    app.config["PROPAGATE_EXCEPTIONS"] = False
    try:
        with app.app_context():
            _login(client, signed_in)
            response = client.get("/__render_error_probe", headers={"Accept": "text/html"})
    finally:
        app.config["PROPAGATE_EXCEPTIONS"] = previous
    html = response.get_data(as_text=True)
    assert response.status_code == 500
    assert "Something went wrong on our side" in html
    assert 'href="/dashboard/overview"' in html
    assert "Go back" in html
