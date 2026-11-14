"""Every tool the chat is offered is one the review loop knows, so a typed element is settled in the same turn."""

import uuid

from tests.test_change_review import PASS_ALL, FakeReviewer, _row, _user


def _ask_for_element(user, tool, name, drafter=("deepseek", "deepseek-flash")):
    from app.modules.ai_chat.services.agent_runner import AgentRunner
    from app.modules.ai_chat.services.change_review import ReviewBudget
    from app.modules.ai_chat.tools.executor import ToolCall

    runner = AgentRunner(user_id=user.id, chat_session_id="element-%s" % uuid.uuid4().hex[:6])
    runner._turn_id = "turn-1"
    runner._review_budget = ReviewBudget()
    runner._drafter = (drafter[0], drafter[1], "Add %s" % name)
    approval_id = runner._queue_approval(ToolCall(id="1", name=tool, arguments={"name": name}))
    return approval_id, runner._verdicts.get(approval_id)


def _elements_named(name):
    from app import db
    from app.models.archimate_core import ArchiMateElement

    db.session.expire_all()
    return ArchiMateElement.query.filter_by(name=name).all()


def test_every_tool_offered_to_the_chat_is_in_the_lookup():
    from app.modules.ai_chat.tools.registry import TOOL_SCHEMA_BY_NAME, TOOL_SCHEMAS

    assert set(TOOL_SCHEMA_BY_NAME) == {s["name"] for s in TOOL_SCHEMAS}
    assert "create_assessment" in TOOL_SCHEMA_BY_NAME
    assert "create_stakeholder" in TOOL_SCHEMA_BY_NAME


def test_the_risk_classifier_sees_the_element_tools():
    from app.modules.ai_chat.services import tool_risk_classifier
    from app.modules.ai_chat.tools.registry import TOOL_SCHEMA_BY_NAME

    assert tool_risk_classifier.TOOL_SCHEMA_BY_NAME is TOOL_SCHEMA_BY_NAME
    assert "create_assessment" in tool_risk_classifier.TOOL_SCHEMA_BY_NAME


def test_a_queued_element_creation_is_in_the_review_loop(db_session, make_org, tenant_ctx, monkeypatch):
    from app.modules.ai_chat.services import change_review

    org = make_org("scope")
    user = _user(db_session, org.id)
    FakeReviewer([], choice=None).install(monkeypatch)
    with tenant_ctx(org.id):
        approval_id, _verdict = _ask_for_element(user, "create_assessment", "Scope %s" % uuid.uuid4().hex[:6])
        assert change_review.in_scope(_row(approval_id)) is True


def test_a_requested_assessment_is_reviewed_and_applied_in_the_same_turn(db_session, make_org, tenant_ctx, monkeypatch):
    from app.models.ai_chat_crud_approval import ApprovalStatus

    org = make_org("assess")
    user = _user(db_session, org.id)
    name = "Toolmap check %s" % uuid.uuid4().hex[:6]
    reviewer = FakeReviewer([PASS_ALL]).install(monkeypatch)
    with tenant_ctx(org.id):
        approval_id, verdict = _ask_for_element(user, "create_assessment", name)
        assert verdict["state"] == "applied", verdict
        assert verdict["record_url"].endswith("approval=%d" % approval_id)
        found = _elements_named(name)
        assert len(found) == 1
        assert found[0].organization_id == org.id and found[0].type == "assessment"
        assert _row(approval_id).status == ApprovalStatus.APPROVED
    assert len(reviewer.review_prompts) == 1 and name in reviewer.review_prompts[0][1]
