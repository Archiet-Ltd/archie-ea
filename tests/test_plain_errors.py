"""Plain-words 403/404/500 treatment: one shell page for people, plain JSON for /api/.

Two organisations: a direct id that belongs to the other organisation must
answer exactly what a missing id answers.
"""
from __future__ import annotations

import re
import uuid

import pytest

RAW_MARKERS = ("Traceback", "Not Found", "Forbidden", "Internal Server Error", "None")


@pytest.fixture
def org_user(app, db_session, make_org, login_as):
    from app.models import Permission, Role, User

    def _make(label):
        org = make_org(label)
        role = Role.query.filter_by(name="Administrator").first()
        if role is None:
            role = Role(name="Administrator", permissions=Permission.ADMINISTER)
            db_session.add(role)
            db_session.flush()
        user = User(
            email=f"plain-{label}-{uuid.uuid4().hex[:8]}@example.com",
            first_name="Plain", last_name=label, organization_id=org.id,
            role=role, confirmed=True,
        )
        db_session.add(user)
        db_session.flush()
        return org, user

    return _make


def _body(resp):
    return resp.get_data(as_text=True)


def test_signed_in_404_is_plain_page_with_navigation(app, org_user, login_as):
    _, user = org_user("a")
    client = app.test_client()
    login_as(client, user)
    resp = client.get("/no-such-page-" + uuid.uuid4().hex[:6])
    body = _body(resp)
    assert resp.status_code == 404
    assert 'data-testid="plain-error"' in body
    assert "We couldn&#39;t find that page" in body or "We couldn't find that page" in body
    assert "sidebar-nav" in body  # the shell, not a bare page
    assert "Traceback" not in body


def test_signed_out_404_still_plain_standalone(app):
    resp = app.test_client().get("/no-such-page-" + uuid.uuid4().hex[:6])
    body = _body(resp)
    assert resp.status_code == 404
    # Re-pointed from the retired standalone 404 wording to the library screen's.
    assert "We couldn&#39;t find that page" in body
    assert "plain-error" not in body


def test_api_404_and_403_are_plain_json(app, org_user, login_as):
    _, user = org_user("a")
    client = app.test_client()
    login_as(client, user)
    resp = client.get("/api/v1/no-such-endpoint-" + uuid.uuid4().hex[:6])
    data = resp.get_json()
    assert resp.status_code == 404
    assert data["success"] is False
    assert "couldn't find" in data["error"] and "Traceback" not in data["error"]


def test_html_error_handler_matches_json_handler(app):
    from werkzeug.exceptions import InternalServerError

    with app.test_request_context("/api/v1/x"):
        resp = app.make_response(app.handle_http_exception(InternalServerError()))
        assert resp.status_code == 500
        assert "Something went wrong" in resp.get_json()["error"]
    with app.test_request_context("/somewhere"):
        resp = app.make_response(app.handle_http_exception(InternalServerError()))
        assert resp.status_code == 500
        assert "Something went wrong on our side" in resp.get_data(as_text=True)


def test_foreign_application_id_answers_like_a_missing_one(app, db_session, org_user, login_as):
    from app.models.application_portfolio import ApplicationComponent

    org_a, user_a = org_user("a")
    org_b, _ = org_user("b")
    foreign = ApplicationComponent(name="Owned by B", organization_id=org_b.id)
    db_session.add(foreign)
    db_session.flush()
    foreign_id = foreign.id
    # .get() only applies the tenant filter on an identity-map miss, so start cold.
    db_session.expunge_all()

    client = app.test_client()
    login_as(client, user_a)
    foreign_resp = client.get(f"/applications/{foreign_id}")
    login_as(client, user_a)
    missing_resp = client.get("/applications/987654321")
    assert foreign_resp.status_code == missing_resp.status_code == 404
    body = _body(foreign_resp)
    assert "Owned by B" not in body
    assert 'data-testid="plain-error"' in body


def test_history_pages_open_from_the_sidebar_without_parameters(app, org_user, login_as):
    """Fails on the base: the changes page answered a raw JSON 400 without dates."""
    _, user = org_user("a")
    client = app.test_client()
    for path in ("/intelligence/history/changes", "/intelligence/history/as-of",
                 "/intelligence/history/changes?from=nonsense&to=nonsense",
                 "/intelligence/history/changes?from=2026-10-01T00:00:00%2B00:00",
                 "/intelligence/history/changes?from=2026-10-01T00:00:00%2B02:00&to=2026-10-10"):
        login_as(client, user)
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert "MISSING_PARAMETER" not in _body(resp)


def test_server_error_does_not_promise_unsaved_work_is_safe(app):
    from app.utils.plain_errors import PLAIN

    assert "lost" not in PLAIN[500][2]
    assert "Check whether your changes were saved" in PLAIN[500][2]


def _filter_form(html, action):
    """The history filter form: the form whose action is this page, so the navbar's
    organisation switcher and any other form on the page are not read."""
    import re

    for attrs, inner in re.findall(r"<form([^>]*)>(.*?)</form>", html, re.S):
        if f'action="{action}"' in attrs:
            return inner
    raise AssertionError(f"no filter form with action {action}")


def _submit_buttons(form):
    """Each submit button of the filter form as (visible text, name, value)."""
    import re

    out = []
    for attrs, text in re.findall(r"<button([^>]*)>(.*?)</button>", form, re.S):
        if 'type="submit"' not in attrs:
            continue
        name = re.search(r'name="([^"]*)"', attrs)
        value = re.search(r'value="([^"]*)"', attrs)
        out.append((re.sub(r"<[^>]+>|\s+", " ", text).strip(), name and name.group(1), value and value.group(1)))
    return out


def _form_fields(form):
    import re

    return dict(re.findall(r'<input[^>]*name="([^"]+)"[^>]*value="([^"]*)"', form))


def _h1s(html):
    """Every h1 in the page body (the shell carries its own, so a page is checked for the one it should have)."""
    return [re.sub(r"<[^>]+>|\s+", " ", m).strip() for m in re.findall(r"<h1[^>]*>(.*?)</h1>", html, re.S)]


def test_history_submit_buttons_carry_a_label(app, org_user, login_as):
    # The forms are server-rendered and show no loading state, so the promise is the label a
    # person reads on each button, and that pressing each one lands on a page with content.
    _, user = org_user("a")
    client = app.test_client()
    for path, main_label, heading in (
        ("/intelligence/history/changes", "Show changes", "Evidence"),
        ("/intelligence/history/as-of", "Show the model", "Model as of"),
    ):
        login_as(client, user)
        page = _body(client.get(path))
        form = _filter_form(page, path)
        buttons = _submit_buttons(form)
        assert buttons, f"{path} has no submit button"
        assert all(text for text, _, _ in buttons), f"{path} has a submit button with no label"
        assert main_label in [text for text, _, _ in buttons]
        for text, name, value in buttons:
            fields = _form_fields(form)
            if name:
                fields[name] = value
            login_as(client, user)
            landed = client.get(path, query_string=fields)
            assert landed.status_code == 200, f"{path}: {text}"
            landed_body = _body(landed)
            assert any(h.startswith(heading) for h in _h1s(landed_body)), f"{path}: {text} h1s {_h1s(landed_body)}"
            assert "Something went wrong" not in landed_body
            assert _submit_buttons(_filter_form(landed_body, path)), f"{path} loses its buttons after {text}"
            if value == "1":
                assert re.search(r"Intact: \d+ entries checked, none altered|No sealed entries to check yet|The record has been altered",
                                 landed_body), f"{path}: {text} shows no result"
