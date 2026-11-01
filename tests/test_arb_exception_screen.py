"""Exceptions to a standard: the request rules, the refusal route, isolation and the page."""

from __future__ import annotations

import uuid

import pytest

from tests.test_arb_write_fences import _exception, _exception_state, _review, _user

pytestmark = pytest.mark.usefixtures("db_session")


def _standard(db_session):
    from app.models.architecture_review_board import ARBGovernanceStandard

    row = ARBGovernanceStandard(
        code=f"STD-T-{uuid.uuid4().hex[:8]}", name="Test standard", status="active"
    )
    db_session.add(row)
    db_session.commit()
    return row


@pytest.mark.parametrize("days", [0, -3])
def test_a_duration_under_one_day_is_refused_with_400(
    app, client, db_session, make_org, login_as, days
):
    org = make_org("arb-dur")
    member = _user(db_session, org, "member", "solution_architect")
    standard = _standard(db_session)
    login_as(client, member)
    resp = client.post(
        "/arb/api/exceptions",
        json={"standard_id": standard.id, "exception_reason": "r", "requested_duration_days": days},
    )
    assert resp.status_code == 400
    assert "at least 1 day" in resp.get_json()["error"]


def test_a_duration_of_one_day_is_stored_as_one_day(app, client, db_session, make_org, login_as):
    org = make_org("arb-dur")
    member = _user(db_session, org, "member", "solution_architect")
    standard = _standard(db_session)
    login_as(client, member)
    resp = client.post(
        "/arb/api/exceptions",
        json={"standard_id": standard.id, "exception_reason": "r", "requested_duration_days": 1},
    )
    assert resp.status_code == 201


def test_refusing_needs_a_reason_and_records_it(app, client, db_session, make_org, login_as):
    org = make_org("arb-deny")
    member = _user(db_session, org, "member", "solution_architect")
    approver = _user(db_session, org, "approver", "enterprise_architect")
    exc_id = _exception(db_session, org, member).id
    login_as(client, approver)

    assert client.put(f"/arb/api/exceptions/{exc_id}/deny", json={}).status_code == 400
    assert client.put(
        f"/arb/api/exceptions/{exc_id}/deny", json={"denial_reason": "  "}
    ).status_code == 400
    assert _exception_state(db_session, exc_id).status == "requested"

    resp = client.put(f"/arb/api/exceptions/{exc_id}/deny", json={"denial_reason": "Not justified"})
    assert resp.status_code == 200
    db_session.expire_all()
    assert _exception_state(db_session, exc_id).status == "denied"


def test_a_member_without_the_role_cannot_refuse(app, client, db_session, make_org, login_as):
    org = make_org("arb-deny")
    member = _user(db_session, org, "member", "solution_architect")
    exc_id = _exception(db_session, org, member).id
    login_as(client, member)
    resp = client.put(f"/arb/api/exceptions/{exc_id}/deny", json={"denial_reason": "no"})
    assert resp.status_code == 403
    assert _exception_state(db_session, exc_id).status == "requested"


def test_another_organisation_cannot_list_or_decide_an_exception(
    app, client, db_session, make_org, login_as
):
    org_a, org_b = make_org("arb-a"), make_org("arb-b")
    member_a = _user(db_session, org_a, "a", "solution_architect")
    approver_b = _user(db_session, org_b, "b", "enterprise_architect")
    exc = _exception(db_session, org_a, member_a)
    exc_id, number = exc.id, exc.exception_number

    login_as(client, approver_b)
    listed = client.get("/arb/api/exceptions").get_json()
    assert number not in str(listed)
    assert client.put(f"/arb/api/exceptions/{exc_id}/approve", json={}).status_code == 404
    assert client.put(
        f"/arb/api/exceptions/{exc_id}/deny", json={"denial_reason": "x"}
    ).status_code == 404
    assert _exception_state(db_session, exc_id).status == "requested"


def test_the_review_page_shows_its_exceptions_only_to_its_own_organisation(
    app, client, db_session, make_org, login_as
):
    org_a, org_b = make_org("arb-a"), make_org("arb-b")
    member_a = _user(db_session, org_a, "a", "solution_architect")
    member_b = _user(db_session, org_b, "b", "solution_architect")
    review = _review(db_session, org_a, member_a, status="submitted")
    exc = _exception(db_session, org_a, member_a)
    exc.review_item_id = review.id
    db_session.commit()
    number, review_id = exc.exception_number, review.id

    login_as(client, member_a)
    page = client.get(f"/arb/reviews/{review_id}").get_data(as_text=True)
    assert number in page
    assert "Request an exception" in page
    assert "Waiting for a board approver" in page  # no Approve / Refuse for this role
    assert 'data-testid="exception-approve"' not in page

    login_as(client, member_b)
    assert client.get(f"/arb/reviews/{review_id}").status_code == 404


def test_an_approver_is_offered_approve_and_refuse(app, client, db_session, make_org, login_as):
    org = make_org("arb-btn")
    member = _user(db_session, org, "member", "solution_architect")
    approver = _user(db_session, org, "approver", "enterprise_architect")
    review = _review(db_session, org, member, status="submitted")
    exc = _exception(db_session, org, member)
    exc.review_item_id = review.id
    db_session.commit()
    review_id = review.id

    login_as(client, approver)
    page = client.get(f"/arb/reviews/{review_id}").get_data(as_text=True)
    assert 'data-testid="exception-approve"' in page
    assert 'data-testid="exception-refuse"' in page


def test_an_empty_review_board_list_says_why_and_offers_the_next_step(
    app, client, db_session, make_org, login_as
):
    org = make_org("arb-empty")
    member = _user(db_session, org, "member", "enterprise_architect")
    login_as(client, member)

    none_yet = client.get("/arb/sessions").get_data(as_text=True)
    assert "No sessions yet" in none_yet
    assert "Schedule a session" in none_yet
    assert "match the current filters" not in none_yet

    filtered = client.get("/arb/sessions?status=completed").get_data(as_text=True)
    assert "No sessions match these filters" in filtered
    assert "Clear filters" in filtered
    assert 'href="/arb/sessions"' in filtered
