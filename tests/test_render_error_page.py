"""A template error on a signed-in page must show the styled error page.

These tests make a signed-in page fail while it renders, and check what the
person sees: the error page in plain words, with a way back. The last tests
force the last-resort page and read it under the app's real content security
policy.
"""

import pytest

from tests.test_ba_tenant_and_authz import _cleanup_ids, _login, _make_org_id, _make_user_id


@pytest.fixture(scope="module")
def app(worker_database):
    """This file adds a route after boot, which Flask refuses on an app that has
    already served a request, so it boots its own app instead of the shared one."""
    from app import create_app

    application = create_app("testing")
    application.config["TESTING"] = True
    application.config["WTF_CSRF_ENABLED"] = False
    return application


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
    words and has a link back."""
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


def _last_resort_response(app, client, signed_in, monkeypatch, referrer=None):
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
            headers = {"Accept": "text/html"}
            if referrer:
                headers["Referer"] = referrer
            return client.get("/__render_error_probe", headers=headers)
    finally:
        app.config["PROPAGATE_EXCEPTIONS"] = previous


def test_last_resort_page_works_under_the_content_security_policy(app, client, signed_in, monkeypatch):
    """The policy blocks a style block without the request's nonce and any
    javascript: link, so the page must use neither: it links the app's own
    same-origin stylesheet and its way back is a real address."""
    import re

    response = _last_resort_response(app, client, signed_in, monkeypatch)
    html = response.get_data(as_text=True)
    policy = response.headers["Content-Security-Policy"]
    assert response.status_code == 500
    assert "style-src-elem 'self' 'nonce-" in policy
    assert "'unsafe-inline'" not in policy.split("style-src-elem")[1].split(";")[0]
    nonce = re.search(r"style-src-elem 'self' 'nonce-([^']+)'", policy).group(1)
    for tag in re.findall(r"<style\b[^>]*>", html):
        assert 'nonce="%s"' % nonce in tag, "inline style block without the request nonce"
    assert "javascript:" not in html.lower()
    sheet = re.search(r'<link rel="stylesheet" href="([^"]+)"', html)
    assert sheet and sheet.group(1).startswith("/static/"), "no same-origin stylesheet"
    assert client.get(sheet.group(1)).status_code == 200
    assert 'href="/dashboard/overview"' in html


def test_last_resort_go_back_returns_to_the_page_the_person_came_from(app, client, signed_in, monkeypatch):
    html = _last_resort_response(
        app, client, signed_in, monkeypatch, referrer="http://localhost/risks/?sort=title"
    ).get_data(as_text=True)
    assert '<a style="' in html and 'href="/risks/?sort=title">Go back</a>' in html


def test_last_resort_go_back_never_leaves_the_site(app, client, signed_in, monkeypatch):
    html = _last_resort_response(
        app, client, signed_in, monkeypatch, referrer="https://elsewhere.example/phish"
    ).get_data(as_text=True)
    assert "elsewhere.example" not in html
    assert 'href="/dashboard/overview">Go back</a>' in html


def test_last_resort_go_back_refuses_slash_backslash_referrers(app, client, signed_in, monkeypatch):
    for referrer in ("http://localhost/\\evil.example", "http://localhost//evil.example/x", "http://localhost/a\\b"):
        html = _last_resort_response(app, client, signed_in, monkeypatch, referrer=referrer).get_data(as_text=True)
        assert "evil.example" not in html and "a\\b" not in html
        assert 'href="/dashboard/overview">Go back</a>' in html
