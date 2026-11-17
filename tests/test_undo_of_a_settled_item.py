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


def test_undo_refuses_to_overwrite_a_field_edited_after_the_change(db_session, make_org, tenant_ctx, monkeypatch):
    from app import db
    from app.models.application_portfolio import ApplicationComponent
    from app.modules.ai_chat.services import change_review
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

    org, other = make_org("undo-d"), make_org("undo-e")
    founder = _user(db_session, org.id)
    founder.confirmed = True
    name = "Atlas %s" % uuid.uuid4().hex[:6]
    atlas = _application(db_session, org.id, name, "development")
    theirs = _application(db_session, other.id, name, "development")
    FakeReviewer([PASS_ALL]).install(monkeypatch)

    with tenant_ctx(org.id):
        approval_id, verdict = _ask_for_status(founder, name, "production")
        assert verdict["state"] == "applied" and _status_of(atlas.id) == "production"
        # A person edits the same field afterwards.
        ApplicationComponent.query.filter_by(id=atlas.id, organization_id=org.id).one().deployment_status = "decommissioned"
        db.session.commit()

        refused = AIChatApprovalService(founder.id).undo_applied(approval_id)

        assert refused["success"] is False and refused["code"] == "CONFLICT", refused
        assert name in refused["error"] and "deployment status" in refused["error"] and "overwrite" in refused["error"]
        assert _status_of(atlas.id) == "decommissioned", "the later edit is still there"
        assert change_review.record_row(_row(approval_id))["label"] == "Applied"
    with tenant_ctx(other.id):
        assert _status_of(theirs.id) == "development", "the other organisation's record was not touched"

