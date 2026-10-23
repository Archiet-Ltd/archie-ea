"""GET /api/v1/answers and GET /api/v1/answers/<address> with a read-scoped
token: the calling organisation's stored answers, nothing of another
organisation's, not found for a foreign address, refusal without an
organisation.
"""

from __future__ import annotations

import datetime as _dt


def _make_answer(db_session, org_id, address):
    """Store one answer row directly, bypassing the question computation."""
    from app.models.answer_record import AnswerRecord

    record = AnswerRecord(
        organization_id=org_id,
        address=address,
        question_kind="impact",
        params={"element_id": 1},
        truth_class="drawn",
        confidence=None,
        confidence_basis="drawn_facts_only",
        rows=[],
        summary={"total": 0},
        reasons=[],
        elements={},
        citations=[],
        freshness={"computed_at": "2026-01-01T00:00:00Z"},
        truncated=False,
        computed_at=_dt.datetime(2026, 1, 1, 12, 0, 0),
    )
    db_session.add(record)
    db_session.flush()
    return record


def _addresses(payload):
    return [item["address"] for item in payload["data"]["answers"]]


def test_list_answers_returns_only_the_calling_organisation_s_answers(
    app, db_session, client, org_pair, reader_tokens
):
    org_a, org_b = org_pair
    token_a, token_b = reader_tokens[org_a.id], reader_tokens[org_b.id]

    _make_answer(db_session, org_a.id, "a1" * 16)
    _make_answer(db_session, org_b.id, "b1" * 16)

    from .conftest import clear_identity

    clear_identity(app, client)
    response = client.get(
        "/api/v1/answers",
        headers={"Authorization": "Bearer %s" % token_a},
    )

    assert response.status_code == 200
    assert _addresses(response.get_json()) == ["a1" * 16]

    clear_identity(app, client)
    response = client.get(
        "/api/v1/answers",
        headers={"Authorization": "Bearer %s" % token_b},
    )
    assert response.status_code == 200
    assert _addresses(response.get_json()) == ["b1" * 16]


def test_fetch_answer_returns_own_answer(app, db_session, client, org_pair, reader_tokens):
    org_a, _org_b = org_pair
    token_a = reader_tokens[org_a.id]

    _make_answer(db_session, org_a.id, "a2" * 16)

    from .conftest import clear_identity

    clear_identity(app, client)
    response = client.get(
        "/api/v1/answers/%s" % ("a2" * 16),
        headers={"Authorization": "Bearer %s" % token_a},
    )

    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["address"] == "a2" * 16
    assert data["question_kind"] == "impact"


def test_fetch_answer_returns_404_for_another_organisation_s_address(
    app, db_session, client, org_pair, reader_tokens
):
    org_a, org_b = org_pair
    _token_a, token_b = reader_tokens[org_a.id], reader_tokens[org_b.id]

    _make_answer(db_session, org_a.id, "a3" * 16)

    from .conftest import clear_identity

    clear_identity(app, client)
    response = client.get(
        "/api/v1/answers/%s" % ("a3" * 16),
        headers={"Authorization": "Bearer %s" % token_b},
    )

    assert response.status_code == 404
    body = response.get_json()
    assert body["success"] is False
    assert body["error"]["code"] == "NOT_FOUND"


def test_fetch_answer_returns_404_for_a_malformed_address(
    app, db_session, client, org_pair, reader_tokens
):
    org_a, _org_b = org_pair
    token_a = reader_tokens[org_a.id]

    from .conftest import clear_identity

    clear_identity(app, client)
    response = client.get(
        "/api/v1/answers/not-an-address",
        headers={"Authorization": "Bearer %s" % token_a},
    )

    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "NOT_FOUND"


def test_answers_refuse_without_an_organisation(app, client, org_pair, reader_tokens):
    _org_a, _org_b = org_pair
    _token_a = reader_tokens[org_pair[0].id]

    response = client.get("/api/v1/answers")
    assert response.status_code == 401
    assert response.get_json()["success"] is False

    response = client.get("/api/v1/answers/%s" % ("a4" * 16))
    assert response.status_code == 401
    assert response.get_json()["success"] is False
