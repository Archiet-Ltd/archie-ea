"""A new workspace's Ask page keeps its questions and says plainly that nothing is
modelled yet; the other gated pages still show only the setup state."""

from __future__ import annotations

import re
import uuid

import pytest

from app.modules.intelligence.tests.test_ui_routes import (
    _main_html,
    _patch_counts,
    _user,
    _visible_text,
)

EMPTY = {"applications": 0, "elements": 0, "capabilities": 0, "vendors": 0}
SENTENCE = "Add your systems and teams, and this is where you will ask questions about them."


def test_a_new_workspaces_ask_page_has_the_questions_and_the_empty_sentence(
    app, db_session, make_org, client, login_as, monkeypatch
):
    _patch_counts(monkeypatch, EMPTY)
    org = make_org("ask-empty-questions")
    login_as(client, _user(db_session, org.id))
    html = _main_html(client.get("/intelligence/ask").get_data(as_text=True))
    text = _visible_text(html)
    assert "Nothing is modelled yet" in text
    assert SENTENCE in text
    assert "Set up your workspace" in text
    keys = re.findall(r'data-question="([a-z]+)"', html)
    assert len(keys) == 8 and "impact" in keys
    assert "What breaks if" in text
    for key in keys:
        assert f'id="ask-question-{key}"' in html
    assert 'role="combobox"' in html
    # the sentence sits above the questions
    assert html.index("Nothing is modelled yet") < html.index('data-question="impact"')


def test_twin_map_still_shows_only_the_setup_state(app, db_session, make_org, client, login_as, monkeypatch):
    _patch_counts(monkeypatch, EMPTY)
    org = make_org("gated-" + uuid.uuid4().hex[:6])
    login_as(client, _user(db_session, org.id))
    html = _main_html(client.get("/intelligence/twin-map").get_data(as_text=True))
    assert "Nothing is modelled yet" in _visible_text(html)
    assert 'role="combobox"' not in html


@pytest.mark.parametrize("path,marker", [
    ("/intelligence/answers", "answers-ask-heading"),
    ("/intelligence/traceability", None),
])
def test_answers_and_traceability_pages_render_as_before_for_an_empty_workspace(
    app, db_session, make_org, client, login_as, monkeypatch, path, marker
):
    """Neither route passes the empty-workspace flag, so the page body shows and
    no setup state is added; the gate change does not reach them."""
    _patch_counts(monkeypatch, EMPTY)
    org = make_org("plain-" + uuid.uuid4().hex[:6])
    login_as(client, _user(db_session, org.id))
    html = _main_html(client.get(path).get_data(as_text=True))
    assert "Nothing is modelled yet" not in html
    if marker:
        assert marker in html


@pytest.mark.parametrize("show_body,expected", [(None, False), (True, True)])
def test_the_gate_keeps_the_body_only_when_asked(app, show_body, expected):
    from flask import render_template_string

    argument = "" if show_body is None else ", show_body_when_empty=true"
    source = (
        "{% from 'intelligence/_workspace_gate.html' import workspace_gate with context %}"
        "{% call workspace_gate(none" + argument + ") %}BODY-TEXT{% endcall %}"
    )
    with app.test_request_context("/"):
        html = render_template_string(source, workspace_is_empty=True)
    assert ("BODY-TEXT" in html) is expected
    assert "Nothing is modelled yet" in html


def test_asking_about_an_element_in_an_empty_workspace_is_a_plain_not_found_not_a_made_up_answer(
    app, db_session, make_org, client, login_as
):
    org = make_org("ask-empty-api")
    login_as(client, _user(db_session, org.id))
    response = client.get("/api/intelligence/impact/999999")
    assert response.status_code == 404
