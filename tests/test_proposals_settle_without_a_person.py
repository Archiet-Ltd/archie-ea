"""Every proposal settles on the server, whatever wrote it: nobody approves, rejects or waits.

A solution blueprint's proposals are checked against the elements the workspace already has, judged by a model
other than the drafter, and then applied, linked to what is there, declined with reasons, or retired when they
are past their time. The decisions are made as the workspace's own system actor.

The reviewer is stubbed at the router. Every isolation case uses two real organisations.
"""

import json
import uuid
from datetime import datetime, timedelta

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

PASS_7 = {"criteria": [{"id": n, "passed": True, "reason": "Fine."} for n in range(1, 8)]}


def _unique(label):
    return "%s %s" % (label, uuid.uuid4().hex[:6])


def _administrator(db_session, org_id):
    from app.models.user import Role, User

    role = Role.query.filter_by(name="Administrator").first()
    assert role is not None
    user = User(email="admin-%s@example.com" % uuid.uuid4().hex[:10], first_name="Org", last_name="Admin",
                organization_id=org_id, role=role, confirmed=True)
    db_session.add(user)
    db_session.flush()
    return user


def _solution(db_session, org_id):
    from app.models.solution_models import Solution

    solution = Solution(name=_unique("Blueprint"), organization_id=org_id)
    db_session.add(solution)
    db_session.flush()
    return solution


def _element(db_session, org_id, name, kind="ApplicationComponent", layer="application"):
    from app.models.archimate_core import ArchiMateElement

    row = ArchiMateElement(name=name, type=kind, layer=layer, organization_id=org_id)
    db_session.add(row)
    db_session.flush()
    return row


def _propose(db_session, org_id, solution, name, kind="ApplicationComponent"):
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval
    from app.services.solution_blueprint_service import create_solution_blueprint_proposal

    proposal = create_solution_blueprint_proposal(
        solution_id=solution.id, organization_id=org_id, archimate_type=kind, name=name, source="llm",
        acm_domain="BUS",
    )
    db_session.commit()
    return AIChatCRUDApproval.query.filter_by(
        organization_id=org_id, source_table="solution_blueprint_proposals", source_id=proposal.id).one()


def _row(approval_id):
    from app import db
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval

    db.session.expire_all()
    return AIChatCRUDApproval.query.filter_by(id=approval_id).one()


def _count(org_id, name=None, kind=None):
    from app import db
    from app.models.archimate_core import ArchiMateElement

    db.session.expire_all()
    query = ArchiMateElement.query.filter(ArchiMateElement.organization_id == org_id)
    if name is not None:
        query = query.filter(ArchiMateElement.name == name)
    if kind is not None:
        query = query.filter(ArchiMateElement.type == kind)
    return query.count()


class Reviewer:
    """The model calls behind the router. ``answers`` maps a name in the change to the review it gets."""

    def __init__(self, monkeypatch, answers=None, default=PASS_7, revisions=()):
        from app.modules.ai_chat.services import llm_model_router as router

        self.prompts = []
        self.answers = answers or {}
        self.revisions = list(revisions)
        fake = self

        def select_reviewer(self, drafter_provider, drafter_model):
            return router.ReviewerChoice("deepseek", "deepseek-v4-pro", True)

        def call_model(self, choice, prompt, timeout=90.0):
            if "independent reviewer rejected it" in prompt:
                return json.dumps(fake.revisions.pop(0))
            fake.prompts.append(prompt)
            for name, review in fake.answers.items():
                if name in prompt:
                    return json.dumps(review)
            return json.dumps(default)

        monkeypatch.setattr(router.LLMModelRouter, "select_reviewer", select_reviewer)
        monkeypatch.setattr(router.LLMModelRouter, "call_model", call_model)
        monkeypatch.setattr("app.modules.ai_chat.services.change_review._month_has_room", lambda c, p: True)


def _saved(approval):
    return approval.review_reasons or {}


def test_a_blueprint_batch_settles_with_no_person_and_adds_only_what_is_new(db_session, make_org, tenant_ctx, monkeypatch):
    from app.models.ai_chat_crud_approval import ApprovalStatus
    from app.models.archimate_core import ArchiMateElement
    from app.modules.ai_chat.services import change_review

    org = make_org("blueprint")
    _administrator(db_session, org.id)
    solution = _solution(db_session, org.id)
    suffix = uuid.uuid4().hex[:5]
    canonical = _element(db_session, org.id, "Canonical model API %s" % suffix)
    enforcer = _element(db_session, org.id, "Policy enforcer %s" % suffix, "TechnologyService", "technology")
    marketing = _element(db_session, org.id, "MarketingIQ %s" % suffix)
    code_gen = _element(db_session, org.id, "ifiok code-generation engine %s" % suffix)
    _element(db_session, org.id, "Billing %s" % suffix, "BusinessFunction", "business")
    Reviewer(monkeypatch, answers={
        # the near match the reviewer says is the same thing
        "ifiok code generation engine %s" % suffix: {"criteria": [
            {"id": n, "passed": n != 7, "reason": "It is the same engine."} for n in range(1, 8)]
            [:6] + [{"id": 7, "passed": False, "reason": "It is the same engine.", "link_to": code_gen.id}]},
    })

    with tenant_ctx(org.id):
        exact = [
            _propose(db_session, org.id, solution, "Canonical model API %s" % suffix),
            _propose(db_session, org.id, solution, "policy  ENFORCER %s" % suffix, "TechnologyService"),
            _propose(db_session, org.id, solution, "MarketingIQ %s" % suffix),
        ]
        near = [
            _propose(db_session, org.id, solution, "ifiok code generation engine %s" % suffix),
            _propose(db_session, org.id, solution, "Billing %s" % suffix, "ApplicationComponent"),
        ]
        brand_new = _propose(db_session, org.id, solution, "Ledger sync %s" % suffix)
        twice = [
            _propose(db_session, org.id, solution, "Audit trail %s" % suffix),
            _propose(db_session, org.id, solution, "Audit trail %s" % suffix),
        ]
        ids = [a.id for a in exact + near + [brand_new] + twice]
        before = _count(org.id)
        seen_before = {r.id for r in ArchiMateElement.query.filter_by(organization_id=org.id)}

        counts = change_review.settle_proposals(org.id)

        # Nothing is left waiting for a person.
        assert all(_row(i).status != ApprovalStatus.PENDING for i in ids), counts
        # Exact repeats are linked to the element that is there; no second element of that name exists.
        for approval, existing in zip(exact, [canonical, enforcer, marketing]):
            row = _row(approval.id)
            assert _saved(row)["outcome"] == "linked", _saved(row)
            assert _saved(row)["linked_to"]["id"] == existing.id
            assert existing.name in " ".join(_saved(row)["outcome_reasons"])
            assert _count(org.id, existing.name) == 1
        # A near match the reviewer judged to be the same thing is linked, with its reason on the record.
        assert _saved(_row(near[0].id))["outcome"] == "linked"
        assert _saved(_row(near[0].id))["linked_to"]["id"] == code_gen.id
        assert _saved(_row(near[0].id))["rounds"][-1]["criteria"][-1]["reason"] == "It is the same engine."
        # The same name under a different type was put to the reviewer, who passed it as a different thing.
        assert _saved(_row(near[1].id))["outcome"] == "applied"
        assert _saved(_row(near[1].id))["rounds"][-1]["criteria"][-1]["id"] == 7
        # The pair listed twice is one element.
        assert _saved(_row(twice[0].id))["outcome"] == "applied"
        assert _saved(_row(twice[1].id))["outcome"] == "linked"
        assert _count(org.id, "Audit trail %s" % suffix, "ApplicationComponent") == 1
        assert _count(org.id, "Ledger sync %s" % suffix, "ApplicationComponent") == 1
        assert _count(org.id, "Billing %s" % suffix, "ApplicationComponent") == 1
        assert _count(org.id, "Billing %s" % suffix, "BusinessFunction") == 1
        for name in ("Canonical model API", "Policy enforcer", "MarketingIQ", "ifiok code-generation engine"):
            assert _count(org.id, "%s %s" % (name, suffix)) == 1
        # The model grew only by the three genuinely new items (the accepted element and what the journey's own
        # inference chain derives for it carries its name); nothing is named after a repeat.
        grown = [r for r in ArchiMateElement.query.filter_by(organization_id=org.id).all()
                 if r.id not in seen_before]
        assert {r.name for r in grown} == {"Billing %s" % suffix, "Ledger sync %s" % suffix, "Audit trail %s" % suffix}
        assert len(grown) == _count(org.id) - before
        assert counts["linked"] == 5 and counts["declined"] == 0 and counts["in_review"] == 0, counts


def _fail(criterion, reason):
    return {"criteria": [{"id": n, "passed": n != criterion, "reason": reason if n == criterion else "Fine."}
                         for n in range(1, 8)]}


def test_a_proposal_past_its_time_is_checked_again_and_never_declined_for_being_late(
    db_session, make_org, tenant_ctx, monkeypatch
):
    """Nobody looked at it before its date, so lateness says nothing about it: it is checked again, now, and
    settles on its merits."""
    from app.models.ai_chat_crud_approval import ApprovalStatus
    from app.modules.ai_chat.services import change_review

    org = make_org("expired")
    _administrator(db_session, org.id)
    solution = _solution(db_session, org.id)
    reviewer = Reviewer(monkeypatch)
    name = _unique("Old idea")
    with tenant_ctx(org.id):
        approval = _propose(db_session, org.id, solution, name)
        approval.expires_at = datetime(2026, 10, 19, 9, 0)
        db_session.commit()
        before = _count(org.id)

        counts = change_review.settle_proposals(org.id)

        row = _row(approval.id)
        assert row.status == ApprovalStatus.APPROVED
        assert _saved(row)["outcome"] == "applied" and counts["applied"] == 1 and counts["retired"] == 0
        reasons = " ".join(c["reason"] for c in _saved(row)["checks"])
        assert "checked again now" in reasons and "19 Oct 2026" in reasons
        assert "Expired" not in reasons and not (row.rejected_reason or "")
        assert _count(org.id, name, "ApplicationComponent") == 1 and _count(org.id) > before
        assert change_review.describe(row)["verdict"] != "Retired"


def test_an_item_the_reviewer_fails_twice_is_declined_with_the_reason_and_leaves_nothing_waiting(
    db_session, make_org, tenant_ctx, monkeypatch
):
    from app.models.ai_chat_crud_approval import ApprovalStatus
    from app.modules.ai_chat.services import change_review

    org = make_org("failing")
    _administrator(db_session, org.id)
    solution = _solution(db_session, org.id)
    name = _unique("Invented service")
    reviewer = Reviewer(monkeypatch, default=_fail(2, "Nothing in the cited source names this service."),
                        revisions=[{"arguments": {"name": name, "archimate_type": "ApplicationComponent",
                                                  "acm_domain": "TECH"}, "summary": "Blueprint proposal: " + name},
                                   {"arguments": {"name": name, "archimate_type": "ApplicationComponent",
                                                  "acm_domain": "DATA"}, "summary": "Blueprint proposal: " + name}])
    with tenant_ctx(org.id):
        approval = _propose(db_session, org.id, solution, name)
        approval.drafter_model = "deepseek/deepseek-flash"
        db_session.commit()
        before = _count(org.id)

        counts = change_review.settle_proposals(org.id)

        row = _row(approval.id)
        assert row.status == ApprovalStatus.REJECTED
        assert _saved(row)["outcome"] == "declined"
        assert len(_saved(row)["rounds"]) == 3 and row.review_rounds == 3
        assert "Nothing in the cited source names this service." in row.rejected_reason
        assert len(reviewer.prompts) == 3 and not reviewer.revisions
        assert _count(org.id) == before
        assert counts == {"applied": 0, "linked": 0, "declined": 1, "retired": 0, "in_review": 0}
        # Nothing of this workspace is left pending.
        from app.models.ai_chat_crud_approval import AIChatCRUDApproval

        assert AIChatCRUDApproval.query.filter_by(organization_id=org.id, status=ApprovalStatus.PENDING).count() == 0


def test_when_the_review_budget_is_spent_the_checks_still_run_and_nothing_waits(
    db_session, make_org, tenant_ctx, monkeypatch
):
    from app.modules.ai_chat.services import change_review

    org = make_org("spent")
    _administrator(db_session, org.id)
    solution = _solution(db_session, org.id)
    suffix = uuid.uuid4().hex[:5]
    existing = _element(db_session, org.id, "Payments hub %s" % suffix)
    reviewer = Reviewer(monkeypatch)
    monkeypatch.setattr(change_review, "_month_has_room", lambda choice, prompt: False)
    with tenant_ctx(org.id):
        repeat = _propose(db_session, org.id, solution, "Payments hub %s" % suffix)
        fresh = _propose(db_session, org.id, solution, "Refund desk %s" % suffix)

        counts = change_review.settle_proposals(org.id)

        assert _saved(_row(repeat.id))["outcome"] == "linked"
        assert _saved(_row(repeat.id))["linked_to"]["id"] == existing.id
        assert _saved(_row(fresh.id))["outcome"] == "applied"
        assert change_review.SPENT_MONTH_SENTENCE in _saved(_row(fresh.id))["outcome_reasons"]
        assert "the reviewer was not called because this month's review budget is spent" in \
            change_review.SPENT_MONTH_SENTENCE.lower()
        assert reviewer.prompts == []
        assert _count(org.id, "Refund desk %s" % suffix, "ApplicationComponent") == 1
        assert _count(org.id, "Payments hub %s" % suffix) == 1
        assert counts["in_review"] == 0 and counts["applied"] == 1 and counts["linked"] == 1


def test_proposals_settle_inside_their_own_organisation_only(db_session, make_org, tenant_ctx, monkeypatch):
    from app.models.ai_chat_crud_approval import ApprovalStatus
    from app.modules.ai_chat.services import change_review

    mine, theirs = make_org("mine"), make_org("theirs")
    _administrator(db_session, mine.id)
    _administrator(db_session, theirs.id)
    my_solution, their_solution = _solution(db_session, mine.id), _solution(db_session, theirs.id)
    shared = _unique("Shared name")
    their_element = _element(db_session, theirs.id, shared)
    secret = _unique("Their private plan")
    _element(db_session, theirs.id, secret)
    reviewer = Reviewer(monkeypatch)
    with tenant_ctx(theirs.id):
        their_waiting = _propose(db_session, theirs.id, their_solution, _unique("Their waiting item"))
        their_count = _count(theirs.id)
    with tenant_ctx(mine.id):
        same_name = _propose(db_session, mine.id, my_solution, shared)

        change_review.settle_proposals(mine.id)

        row = _row(same_name.id)
        # The other organisation's element of that name is not "already in the model" for this one.
        assert _saved(row)["outcome"] == "applied" and not _saved(row).get("linked_to")
        assert _count(mine.id, shared, "ApplicationComponent") == 1
        assert reviewer.prompts, "the reviewer was asked about my proposal"
        assert all(secret not in prompt for prompt in reviewer.prompts)
    with tenant_ctx(theirs.id):
        assert _row(their_waiting.id).status == ApprovalStatus.PENDING  # not touched by my run
        assert _count(theirs.id) == their_count
        assert their_element.id is not None
