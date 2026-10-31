"""Viewing an impact stores nothing; saving it stores exactly one run.

GET /enterprise/analysis/impact-analysis?element=N computes the answer without
writing; the run is stored only by the CSRF-protected POST to
/enterprise/analysis/impact-analysis/save, and only for an element of the
caller's own organisation. The runs table filters by kind in the query, so
chat answers never hide impact runs.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

PAGE = "/enterprise/analysis/impact-analysis"
SAVE = PAGE + "/save"


def _user(db_session, org):
    from werkzeug.security import generate_password_hash

    from app.models.user import User

    user = User(
        email=f"save-{uuid.uuid4().hex[:10]}@example.com",
        first_name="Save",
        last_name="Tester",
        confirmed=True,
        organization_id=org.id,
        password_hash=generate_password_hash("x"),
    )
    db_session.add(user)
    db_session.flush()
    return user


def _element(db_session, org, name):
    from app.models import ArchiMateElement

    el = ArchiMateElement(name=name, type="ApplicationComponent", layer="Application", organization_id=org.id)
    db_session.add(el)
    db_session.flush()
    return el


def _count(db_session, org, kind=None):
    from app.models.answer_record import AnswerRecord

    query = AnswerRecord.query.filter_by(organization_id=org.id)
    if kind:
        query = query.filter_by(question_kind=kind)
    return query.count()


@pytest.fixture
def csrf_on(app):
    app.config["WTF_CSRF_ENABLED"] = True
    yield
    app.config["WTF_CSRF_ENABLED"] = False


def test_a_post_without_a_csrf_token_is_refused(db_session, make_org, client, login_as, csrf_on):
    org = make_org("save-csrf")
    user = _user(db_session, org)
    element = _element(db_session, org, "Ledger")
    login_as(client, user)
    response = client.post(SAVE, data={"element": element.id})
    assert response.status_code == 400
    assert _count(db_session, org) == 0


def test_a_second_organisation_cannot_save_a_run_for_the_firsts_element(
    db_session, make_org, client, login_as
):
    org_a, org_b = make_org("save-a"), make_org("save-b")
    element_a = _element(db_session, org_a, "Ledger")
    user_b = _user(db_session, org_b)
    login_as(client, user_b)
    client.post(SAVE, data={"element": element_a.id})
    assert _count(db_session, org_a) == 0
    assert _count(db_session, org_b) == 0


def test_get_stores_nothing_and_save_stores_one(db_session, make_org, client, login_as):
    org = make_org("save-one")
    user = _user(db_session, org)
    element = _element(db_session, org, "Ledger")
    login_as(client, user)
    for _ in range(3):
        assert client.get(PAGE, query_string={"element": element.id}).status_code == 200
    assert client.get(
        PAGE, query_string={"element": element.id}, headers={"Sec-Purpose": "prefetch"}
    ).status_code == 200
    assert _count(db_session, org) == 0
    response = client.post(SAVE, data={"element": element.id})
    assert response.status_code == 302
    assert _count(db_session, org, "impact") == 1


def test_impact_runs_are_listed_even_when_the_last_fifty_answers_are_chat(
    db_session, make_org, client, login_as
):
    from app import db
    from app.models.answer_record import AnswerRecord
    from app.modules.intelligence.services import answer_envelope as envelope

    org = make_org("save-kind")
    user = _user(db_session, org)
    element = _element(db_session, org, "Ledger")
    login_as(client, user)
    client.post(SAVE, data={"element": element.id})
    first = AnswerRecord.query.filter_by(organization_id=org.id).one()
    for i in range(55):
        db.session.add(
            AnswerRecord(
                organization_id=org.id,
                address=uuid.uuid4().hex,
                question_kind="risk_propagation",
                params={},
                truth_class=first.truth_class,
                confidence=first.confidence,
                confidence_basis=first.confidence_basis,
                rows=[],
                summary={},
                reasons=[],
                elements={},
                citations=[],
                freshness=first.freshness,
                truncated=False,
                computed_at=first.computed_at,
            )
        )
    db.session.flush()
    login_as(client, user)
    from flask import g

    with client.application.test_request_context("/"):
        g.current_org_id = org.id
        kinds = [r.question_kind for r in envelope.recent_answers(20, kind=envelope.KIND_IMPACT)]
    assert kinds == ["impact"]
    body = client.get(PAGE).get_data(as_text=True)
    assert "impact-run-row" in body
