"""R1-B55 review fix: the board's AI design decision cannot be made, remade or
deleted through the generic decision routes, and the generic decision routes
no longer cross organisations.

Four existing routes could write a decision row: the register's edit and delete
pages and the ARB API's PATCH and supersede. For a row linked to an AI system
all four now refuse, and an ORM-level guard refuses the same writes from any
route added later. Every lookup in them names the caller's active organisation.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.models.architecture_decision import AIDesignDecisionLocked, ArchitectureDecision
from app.modules.ai_chat.services import ai_design_conformance_service as svc
from tests.test_ai_design_conformance import CLEAN, _raw_status, _submit, _system, _user


def _plain_decision(db_session, org, title="Plain ADR"):
    row = ArchitectureDecision(
        organization_id=org.id, decision_id=f"AD-L{uuid.uuid4().hex[:6]}", title=title,
        status="proposed",
    )
    db_session.add(row)
    db_session.flush()
    return row


def _raw(db_session, decision_id):
    return db_session.execute(
        text("SELECT status, decided_by_id, approved_by_id, rejection_reason, rationale "
             "FROM architecture_decisions WHERE id = :i"), {"i": decision_id}).one_or_none()


def _call(client, route, review_id, other_id):
    if route == "edit":
        return client.post(f"/architecture/decisions/{review_id}/edit",
                           data={"title": "Edited", "status": "accepted"})
    if route == "delete":
        return client.post(f"/architecture/decisions/{review_id}/delete")
    if route == "patch":
        return client.patch(f"/arb/api/decisions/{review_id}", json={"status": "accepted"})
    return client.post(f"/arb/api/decisions/{review_id}/supersede",
                       json={"new_decision_id": other_id})


ROUTES = ["edit", "delete", "patch", "supersede"]


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("actor", ["submitter", "third_party"])
@pytest.mark.parametrize("state", ["open", "decided"])
def test_the_generic_routes_cannot_decide_remake_or_delete_an_ai_design_review(
    app, db_session, make_org, client, login_as, route, actor, state
):
    org = make_org("lk1")
    submitter = _user(db_session, org, "Architect", "sub", "solution_architect")
    third = _user(db_session, org, "Architect", "third", "arb_member")
    system = _system(db_session, org)
    review = _submit(system, org, submitter)
    other = _plain_decision(db_session, org, "Successor")
    if state == "decided":
        svc.record_decision(decision_id=review.id, organization_id=org.id, decider_id=third.id,
                            outcome="rejected", rationale="Wrong pattern.")
    before = _raw(db_session, review.id)

    login_as(client, submitter if actor == "submitter" else third)
    response = _call(client, route, review.id, other.id)

    assert response.status_code in (302, 403), response.status_code
    if route in ("edit", "delete"):
        assert response.status_code == 302
        assert f"/architecture/ai-design-reviews/{review.id}" in response.headers["Location"]
    db_session.expire_all()
    assert _raw(db_session, review.id) == before  # present and unchanged
    if state == "open":
        assert _raw_status(db_session, review.id) == "under_review"
        # The one-open-submission rule still stands: the review was not deleted.
        with pytest.raises(svc.DesignReviewError, match="already under review"):
            _submit(system, org, submitter, title="Again")


def test_the_orm_refuses_a_status_change_or_delete_of_an_ai_design_review(db_session, make_org):
    """The guard is on the model, so a route added later is covered too."""
    org = make_org("lk2")
    sub = _user(db_session, org, "Architect", "sub")
    review = _submit(_system(db_session, org), org, sub)
    review.status = "accepted"
    with pytest.raises(AIDesignDecisionLocked):
        db_session.flush()
    db_session.rollback()

    org = make_org("lk2b")
    sub = _user(db_session, org, "Architect", "sub2")
    review = _submit(_system(db_session, org), org, sub)
    db_session.delete(review)
    with pytest.raises(AIDesignDecisionLocked):
        db_session.flush()
    db_session.rollback()


def test_the_orm_guard_leaves_ordinary_decisions_and_the_service_alone(db_session, make_org):
    org = make_org("lk3")
    sub = _user(db_session, org, "Architect", "sub")
    third = _user(db_session, org, "Architect", "third", "arb_member")
    plain = _plain_decision(db_session, org)
    plain.status = "accepted"
    db_session.flush()
    assert _raw_status(db_session, plain.id) == "accepted"
    review = _submit(_system(db_session, org), org, sub, dict(CLEAN))
    review.title = "Retitled by the submitter"  # not a decision field
    db_session.flush()
    svc.record_decision(decision_id=review.id, organization_id=org.id, decider_id=third.id,
                        outcome="rejected", rationale="No.")
    assert _raw_status(db_session, review.id) == "rejected"


def _cross_org_calls(client, target_id, other_id):
    return {
        "view": client.get(f"/architecture/decisions/{target_id}"),
        "edit_get": client.get(f"/architecture/decisions/{target_id}/edit"),
        "edit": client.post(f"/architecture/decisions/{target_id}/edit",
                            data={"title": "Hijacked", "status": "accepted"}),
        "delete": client.post(f"/architecture/decisions/{target_id}/delete"),
        "outcome": client.post(f"/architecture/decisions/{target_id}/record-outcome",
                               data={"review_outcome": "took over"}),
        "patch": client.patch(f"/arb/api/decisions/{target_id}", json={"status": "accepted"}),
        "supersede": client.post(f"/arb/api/decisions/{target_id}/supersede",
                                 json={"new_decision_id": other_id}),
        "link": client.post(f"/arb/api/decisions/{target_id}/link-capability",
                            json={"capability_id": 1}),
    }


@pytest.mark.parametrize("kind", ["ai_design_review", "ordinary_decision"])
def test_another_organisation_gets_404_from_every_generic_decision_route(
    app, db_session, make_org, client, login_as, kind
):
    org_a, org_b = make_org("lk4a"), make_org("lk4b")
    sub_a = _user(db_session, org_a, "Architect", "suba")
    user_b = _user(db_session, org_b, "Architect", "userb", "arb_member")
    if kind == "ai_design_review":
        target = _submit(_system(db_session, org_a), org_a, sub_a)
    else:
        target = _plain_decision(db_session, org_a)
    mine = _plain_decision(db_session, org_b, "B's own")
    before = _raw(db_session, target.id)

    login_as(client, user_b)
    for name, response in _cross_org_calls(client, target.id, mine.id).items():
        assert response.status_code == 404, (name, response.status_code)
    # B's own decision as the replacement for A's, and A's as B's replacement.
    assert client.post(f"/arb/api/decisions/{mine.id}/supersede",
                       json={"new_decision_id": target.id}).status_code == 404
    db_session.expire_all()
    assert _raw(db_session, target.id) == before  # A's row exists, unchanged
    assert _raw_status(db_session, mine.id) == "proposed"


def test_the_generic_routes_answer_404_when_the_organisation_is_unknown(
    app, db_session, make_org, client, login_as, monkeypatch
):
    import app.models.architecture_decision as module

    org = make_org("lk5")
    user = _user(db_session, org, "Architect", "u", "arb_member")
    plain = _plain_decision(db_session, org)
    login_as(client, user)
    monkeypatch.setattr(module, "_active_org_id", lambda: None)
    for name, response in _cross_org_calls(client, plain.id, plain.id).items():
        assert response.status_code == 404, (name, response.status_code)
    assert _raw_status(db_session, plain.id) == "proposed"


def test_the_ordinary_register_still_edits_and_deletes_an_ordinary_decision(
    app, db_session, make_org, client, login_as
):
    org = make_org("lk6")
    user = _user(db_session, org, "Architect", "u", "arb_member")
    plain = _plain_decision(db_session, org)
    login_as(client, user)
    assert client.post(f"/architecture/decisions/{plain.id}/edit",
                       data={"title": "Renamed", "status": "accepted"}).status_code == 302
    db_session.expire_all()
    assert _raw_status(db_session, plain.id) == "accepted"
    assert client.patch(f"/arb/api/decisions/{plain.id}", json={"status": "deprecated"}).status_code == 200
    assert client.post(f"/architecture/decisions/{plain.id}/delete").status_code == 302
    db_session.expire_all()
    assert _raw(db_session, plain.id) is None


def test_case_counts_are_capped_with_a_message_naming_the_field(db_session, make_org):
    from app.modules.ai_chat.services import ai_evaluation_service as ev
    from app.modules.enterprise_genome.services.ai_systems_seed import register_ai_system

    org = make_org("lk7")
    el = register_ai_system(db_session, org.id, name="Cap system", provider="anthropic",
                            model_id="claude-opus-5")
    db_session.flush()
    with pytest.raises(ev.EvaluationError, match="test set size is limited"):
        ev.set_plan(element_id=el.id, organization_id=org.id, test_set_name="s",
                    test_set_size=99999999999999, thresholds={"groundedness": 0.5})
    ev.set_plan(element_id=el.id, organization_id=org.id, test_set_name="s",
                test_set_size=ev.MAX_CASES, thresholds={"groundedness": 0.5})
    with pytest.raises(ev.EvaluationError, match="cases run is limited"):
        ev.record_run(element_id=el.id, organization_id=org.id,
                      metrics={"groundedness": 0.9}, cases_run=99999999999999)
    assert ev.record_run(element_id=el.id, organization_id=org.id,
                         metrics={"groundedness": 0.9}, cases_run=ev.MAX_CASES).cases_run == ev.MAX_CASES
