"""Undo on an applied item puts back what was there before, from the copy kept before it was applied.

Two real organisations: the other organisation's administrator can neither see nor undo this one's item.
"""

import uuid

import pytest

from tests.test_change_review import FakeReviewer, PASS_ALL, _application, _ask_for_status, _row, _status_of, _user
from tests.test_proposals_settle_without_a_person import (
    Reviewer, _administrator, _count, _element, _propose, _solution)

pytestmark = pytest.mark.usefixtures("db_session")


def test_undo_restores_the_field_an_applied_change_overwrote(db_session, make_org, tenant_ctx, monkeypatch):
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService
    from app.modules.ai_chat.services import change_review

    org, other = make_org("undo-a"), make_org("undo-b")
    founder = _user(db_session, org.id)
    stranger = _user(db_session, other.id, "Administrator")
    atlas = _application(db_session, org.id, "Atlas %s" % uuid.uuid4().hex[:6], "development")
    FakeReviewer([PASS_ALL]).install(monkeypatch)
    founder.confirmed = stranger.confirmed = True

    with tenant_ctx(org.id):
        approval_id, verdict = _ask_for_status(founder, atlas.name, "production")
        assert verdict["state"] == "applied" and _status_of(atlas.id) == "production"
        row = change_review.record_row(_row(approval_id))
        assert row["label"] == "Applied" and row["can_undo"] is True

        undone = AIChatApprovalService(founder.id).undo_applied(approval_id)

        assert undone["success"] is True, undone
        assert _status_of(atlas.id) == "development"
        after = change_review.record_row(_row(approval_id))
        assert after["label"] == "Undone" and after["can_undo"] is False
        assert "deployment_status" in _row(approval_id).review_reasons["undone"]["what"]
        # A second undo is refused with its reason and changes nothing.
        again = AIChatApprovalService(founder.id).undo_applied(approval_id)
        assert again["success"] is False and "already been undone" in again["error"]
        assert _status_of(atlas.id) == "development"
    with tenant_ctx(other.id):
        foreign = AIChatApprovalService(stranger.id).undo_applied(approval_id)
        assert foreign["success"] is False and foreign["code"] == "NOT_FOUND"
        assert AIChatApprovalService(stranger.id).get_settled_records()["records"] == []


def test_undo_removes_what_a_blueprint_proposal_added_and_refuses_a_link(db_session, make_org, tenant_ctx, monkeypatch):
    from app.models.solution_blueprint_proposal import SolutionBlueprintProposal
    from app.modules.ai_chat.services import change_review
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

    org = make_org("undo-c")
    admin = _administrator(db_session, org.id)
    solution = _solution(db_session, org.id)
    suffix = uuid.uuid4().hex[:5]
    _element(db_session, org.id, "Existing hub %s" % suffix)
    Reviewer(monkeypatch)
    with tenant_ctx(org.id):
        fresh = _propose(db_session, org.id, solution, "Brand new desk %s" % suffix)
        repeat = _propose(db_session, org.id, solution, "Existing hub %s" % suffix)
        before = _count(org.id)
        change_review.settle_proposals(org.id)
        assert _count(org.id, "Brand new desk %s" % suffix, "ApplicationComponent") == 1
        assert _count(org.id) > before
        fresh_id, repeat_id, proposal_id = fresh.id, repeat.id, fresh.source_id

        service = AIChatApprovalService(admin.id)
        undone = service.undo_applied(fresh_id)

        assert undone["success"] is True, undone
        assert _count(org.id, "Brand new desk %s" % suffix, "ApplicationComponent") == 0
        assert _count(org.id) == before, "everything the item added, including what was derived for it, is gone"
        assert SolutionBlueprintProposal.query.filter_by(id=proposal_id).one().status == "proposed"
        # A link added nothing, so there is nothing to undo, and it says so.
        refused = service.undo_applied(repeat_id)
        assert refused["success"] is False and "Nothing was added" in refused["error"]
        assert _count(org.id, "Existing hub %s" % suffix) == 1
