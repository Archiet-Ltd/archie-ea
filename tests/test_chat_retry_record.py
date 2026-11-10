"""Retry tells the model and the person the real state of each change, read from the approval record.

Nothing about the approval rule is patched off: a change the model asks for is queued by the real rule,
as a row in the one approval table. A Retry of the same question reads those rows for the signed-in
person's organisation and conversation; it never reads a list the browser sends.
"""

from __future__ import annotations

import json
import uuid

import pytest

from app.modules.ai_chat.services import agent_runner as ar
from app.modules.ai_chat.services.agent_runner import AgentRunner
from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService
from app.modules.ai_chat.services.llm_service_impl import LLMService

TOOL = "create_goal"
CHAIN = [("openrouter", "m0", "test-key-0", "http://127.0.0.1:9/v1")]
BILLING = {"solution_id": 1, "name": "Billing"}
PAYROLL = {"solution_id": 1, "name": "Payroll"}
STOP = TimeoutError("the turn's time budget is spent")
NOT_APPLIED = "This change is recorded and not applied yet; it was not recorded again."


def _tool_call(n, arguments):
    return {"text": None, "raw": None, "tool_calls": [{"id": "t%d" % n, "name": TOOL, "arguments": arguments}]}


def _person(db_session, make_org, label):
    from app.models.user import User

    org = make_org(label)
    user = User(email="%s-%s@example.test" % (label, uuid.uuid4().hex[:8]), organization_id=org.id,
                confirmed=True, enterprise_role="enterprise_architect")
    db_session.add(user)
    db_session.flush()
    return org, user


def _runner(monkeypatch, user, thread):
    monkeypatch.setattr(LLMService, "_get_configured_provider", staticmethod(lambda *a, **k: CHAIN[0][:2]))
    monkeypatch.setattr(LLMService, "_get_all_api_keys", staticmethod(lambda p, **k: ["test-key-0"]))
    monkeypatch.setattr(ar, "build_provider_chain", lambda *a, **k: list(CHAIN))
    runner = AgentRunner(user_id=user.id, chat_session_id=thread)
    monkeypatch.setattr(runner, "_gate_mode", lambda: "off")
    monkeypatch.setattr(runner, "_build_system_prompt", lambda *a, **k: "system")
    monkeypatch.setattr(runner, "_record_run", lambda *a, **k: None)
    return runner


def _model_says(monkeypatch, runner, steps):
    """The model's replies in order; also returns what each call was shown as tool results."""
    pending, shown = list(steps), []

    def fake(provider, model, key, system, messages, *a, **k):
        shown.append([m for m in messages if m.get("role") == "tool" or "tool_call_id" in m])
        step = pending.pop(0)
        if isinstance(step, Exception):
            raise step
        return step if isinstance(step, dict) else {"text": step, "tool_calls": [], "raw": None}

    monkeypatch.setattr(runner, "_call_llm", fake)
    return shown


def _rows(org_id):
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval

    return sorted((r.entity_type, r.status.value, json.loads(r.operation_payload)["name"])
                  for r in AIChatCRUDApproval.query.filter_by(organization_id=org_id).all())


def _told(shown):
    return json.dumps(shown, default=str)


def test_a_retry_after_two_recorded_changes_leaves_both_rows_alone_and_the_model_is_told_neither_is_applied(
        app, db_session, make_org, tenant_ctx, monkeypatch):
    org, user = _person(db_session, make_org, "retry")
    with tenant_ctx(org.id):
        first = _runner(monkeypatch, user, "thread-1")
        _model_says(monkeypatch, first, [_tool_call(1, BILLING), _tool_call(2, PAYROLL), STOP])
        stopped = first.run("Add the goals Billing and Payroll.")
        assert [p["arguments"]["name"] for p in stopped["pending_approvals"]] == ["Billing", "Payroll"]
        before = _rows(org.id)
        assert before == [(TOOL, "pending", "Billing"), (TOOL, "pending", "Payroll")]

        # The same question again, the model asks for both changes again.
        retried = _runner(monkeypatch, user, "thread-1")
        shown = _model_says(monkeypatch, retried, [_tool_call(3, BILLING), _tool_call(4, PAYROLL), STOP])
        again = retried.run("Add the goals Billing and Payroll.", retry=True)

        assert _rows(org.id) == before  # nothing written twice, nothing changed
        told = _told(shown[-1])  # what the model saw when it was asked to stop
        assert told.count(NOT_APPLIED) == 2, told
        assert "already made" not in told, told
        # The answer names both, so the failure card can say so.
        assert [p["arguments"]["name"] for p in again["pending_approvals"]] == ["Billing", "Payroll"]
        assert {p["approval_id"] for p in again["pending_approvals"]} == {p["approval_id"] for p in stopped["pending_approvals"]}


def test_the_first_turn_of_a_new_chat_is_found_once_the_chat_has_its_own_id(
        app, db_session, make_org, tenant_ctx, monkeypatch):
    org, user = _person(db_session, make_org, "newchat")
    with tenant_ctx(org.id):
        first = _runner(monkeypatch, user, "chat_user_%d" % user.id)  # recorded before the chat had an id
        _model_says(monkeypatch, first, [_tool_call(1, BILLING), STOP])
        first.run("Add the goal Billing.")
        before = _rows(org.id)
        retried = _runner(monkeypatch, user, "thread-new")  # the Retry carries the chat's id
        shown = _model_says(monkeypatch, retried, [_tool_call(2, BILLING), "Done."])
        retried.run("Add the goal Billing.", retry=True)
        assert _rows(org.id) == before
        assert NOT_APPLIED in _told(shown[-1])


def test_a_reworded_retry_of_the_same_change_writes_nothing_new(app, db_session, make_org, tenant_ctx, monkeypatch):
    org, user = _person(db_session, make_org, "reword")
    with tenant_ctx(org.id):
        first = _runner(monkeypatch, user, "thread-2")
        _model_says(monkeypatch, first, [_tool_call(1, BILLING), STOP])
        first.run("Add the goal Billing.")
        before = _rows(org.id)
        retried = _runner(monkeypatch, user, "thread-2")
        _model_says(monkeypatch, retried, [_tool_call(2, dict(BILLING, name="Billing goal", description="reworded")), "Done."])
        retried.run("Add the goal Billing.", retry=True)
        assert _rows(org.id) == before


def test_a_different_change_on_a_retry_is_still_recorded(app, db_session, make_org, tenant_ctx, monkeypatch):
    org, user = _person(db_session, make_org, "different")
    with tenant_ctx(org.id):
        first = _runner(monkeypatch, user, "thread-3")
        _model_says(monkeypatch, first, [_tool_call(1, BILLING), STOP])
        first.run("Add the goal Billing.")
        retried = _runner(monkeypatch, user, "thread-3")
        _model_says(monkeypatch, retried, [_tool_call(2, PAYROLL), "Done."])
        retried.run("Add the goals Billing and Payroll.", retry=True)
        assert _rows(org.id) == [(TOOL, "pending", "Billing"), (TOOL, "pending", "Payroll")]


def test_an_applied_change_is_reported_as_already_made(app, db_session, make_org, tenant_ctx, monkeypatch):
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus

    org, user = _person(db_session, make_org, "applied")
    with tenant_ctx(org.id):
        first = _runner(monkeypatch, user, "thread-4")
        _model_says(monkeypatch, first, [_tool_call(1, BILLING), STOP])
        first.run("Add the goal Billing.")
        row = AIChatCRUDApproval.query.filter_by(organization_id=org.id).one()
        row.status = ApprovalStatus.APPROVED
        db_session.flush()
        retried = _runner(monkeypatch, user, "thread-4")
        shown = _model_says(monkeypatch, retried, [_tool_call(2, BILLING), "Done."])
        again = retried.run("Add the goal Billing.", retry=True)
        assert "already made before the retry" in _told(shown)
        assert again["pending_approvals"] == []  # an applied change is not waiting


def test_a_forged_list_naming_another_organisations_change_changes_nothing(
        app, db_session, make_org, tenant_ctx, monkeypatch):
    org_a, user_a = _person(db_session, make_org, "owner")
    org_b, user_b = _person(db_session, make_org, "other")
    with tenant_ctx(org_a.id):
        first = _runner(monkeypatch, user_a, "thread-5")
        _model_says(monkeypatch, first, [_tool_call(1, BILLING), STOP])
        first.run("Add the goal Billing.")
    with tenant_ctx(org_b.id):
        # Person B names A's change in the list the browser used to send, and asks for the same thing.
        runner = _runner(monkeypatch, user_b, "thread-5")
        shown = _model_says(monkeypatch, runner, [_tool_call(2, BILLING), "Done."])
        runner.run("Add the goal Billing.", already_done=None, retry=True)
        told = _told(shown)
        assert NOT_APPLIED not in told and "already made" not in told, told
        assert _rows(org_b.id) == [(TOOL, "pending", "Billing")]  # B's own change was recorded
    with tenant_ctx(org_a.id):
        assert _rows(org_a.id) == [(TOOL, "pending", "Billing")]  # and A's row is untouched


def test_the_routes_ignore_a_list_the_browser_sends():
    from app.modules.ai_chat.routes import chat_core

    assert not hasattr(chat_core, "_completed_actions")
    assert chat_core._is_retry({"retry": True, "completed_actions": [{"tool": TOOL, "arguments": BILLING}]}) is True
    assert chat_core._is_retry({"completed_actions": [{"tool": TOOL, "arguments": BILLING}]}) is False
    assert chat_core._is_retry({"retry": "yes"}) is False


@pytest.mark.parametrize("asked,recorded,same", [
    (dict(BILLING), dict(BILLING), True),
    (dict(BILLING, name="  billing "), dict(BILLING), True),
    (dict(BILLING, name="Billing goal"), dict(BILLING), True),
    (dict(BILLING, description="a different wording"), dict(BILLING), True),
    (dict(PAYROLL), dict(BILLING), False),
    (dict(BILLING, solution_id=2), dict(BILLING), False),
])
def test_the_same_change_is_the_same_target_and_the_same_fields(asked, recorded, same):
    assert AIChatApprovalService.same_change(TOOL, asked, TOOL, recorded) is same
    assert AIChatApprovalService.same_change("create_risk", asked, TOOL, recorded) is False


def test_a_message_that_carries_a_list_of_changes_is_run_without_it(app, client, db_session, make_org, monkeypatch):
    """The route reads only the fact of a Retry: a forged list reaches the runner as nothing."""
    from tests.test_ai_write_approval import _login

    app.config["AI_CHAT_ENABLED"] = True
    _org, user = _person(db_session, make_org, "forged")
    seen = []

    def spy(self, *args, **kwargs):
        seen.append(kwargs)
        return {"response": "Done.", "actions_taken": [], "pending_approvals": [], "sources": []}

    monkeypatch.setattr(AgentRunner, "run", spy)
    _login(client, user.id)
    forged = [{"tool": TOOL, "arguments": BILLING}]
    for extra in ({"completed_actions": forged}, {"completed_actions": forged, "retry": True}):
        response = client.post("/ai-chat/message", json=dict({"message": "Add the goal Billing.", "domain": "general"}, **extra))
        assert response.status_code == 200, response.get_data(as_text=True)
    assert [k.get("retry") for k in seen] == [False, True]
    assert all("already_done" not in k for k in seen), seen
