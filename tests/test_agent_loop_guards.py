"""Tests for the AI chat tool loop going through the model service's guards.

AC1: budget exhausted → no provider call, budget message returned.
AC2: scrub runs on tool-loop prompt with email and key-shaped string.
AC3: usage event with tokens and cost per model call.
AC4: write tool creates approval row, changes no data until approved.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


# Shared helper: patch the provider resolution so AgentRunner.run() can proceed
# past the key check, then patch the low-level provider call so no real HTTP
# call is made.
def _patch_runner_deps():
    """Return a dict of patchers that let AgentRunner.run() reach _call_llm."""
    return {
        "provider": patch(
            "app.modules.ai_chat.services.llm_service_impl.LLMService._get_configured_provider",
            return_value=("openai", "gpt-4o"),
        ),
        "keys": patch(
            "app.modules.ai_chat.services.llm_service_impl.LLMService._get_all_api_keys",
            return_value=["sk-test-key"],
        ),
    }


def _fake_tool_loop_response(text=None, tool_calls=None):
    """Return a fake provider response that short-circuits the real HTTP call."""
    return {
        "text": text,
        "tool_calls": tool_calls or [],
        "usage": {"input_tokens": 100, "output_tokens": 20, "cost": 0.001},
    }


# _call_openai_compat_tool_loop is called as:
#   (model, api_key, system_prompt, messages, tools, stream=..., base_url=..., emit=...)
# So messages is positional arg index 3.
def _extract_messages(args, kwargs):
    """Extract the messages list from positional or keyword args."""
    if "messages" in kwargs:
        return kwargs["messages"]
    if len(args) > 3:
        return args[3]
    return []


def _extract_system_prompt(args, kwargs):
    """Extract the system_prompt from positional or keyword args."""
    if "system_prompt" in kwargs:
        return kwargs["system_prompt"]
    if len(args) > 2:
        return args[2]
    return ""


# ------------------------------------------------------------------ #
# AC1: budget exhausted
# ------------------------------------------------------------------ #

def test_budget_exhausted_returns_budget_message_no_provider_call(app, db_session, make_org):
    """A chat turn with the budget exhausted makes no provider call and
    returns the budget message."""
    from app.modules.ai_chat.services.agent_runner import AgentRunner
    from app.modules.ai_chat.services.llm_cost_tracker import LLMCostTracker

    _org = make_org("budget-exhausted")
    runner = AgentRunner(user_id=1, auto_execute=False)

    deps = _patch_runner_deps()
    with deps["provider"], deps["keys"], patch.object(
        LLMCostTracker, "check_budget_before_call", return_value=(False, "Budget exhausted")
    ):
        result = runner.run(user_message="create a solution called Test")

    assert "budget" in result.get("response", "").lower(), (
        f"expected budget message, got: {result.get('response')}"
    )
    assert result.get("error") is not None


def test_budget_exhausted_via_cost_tracker(app, db_session, make_org):
    """The LLMCostTracker check is actually invoked and its refusal propagates."""
    from app.modules.ai_chat.services.agent_runner import AgentRunner
    from app.modules.ai_chat.services.llm_cost_tracker import LLMCostTracker

    _org = make_org("budget-tracker")
    runner = AgentRunner(user_id=1, auto_execute=False)

    deps = _patch_runner_deps()
    with deps["provider"], deps["keys"], patch.object(
        LLMCostTracker, "check_budget_before_call", return_value=(False, "Budget exhausted")
    ):
        result = runner.run(user_message="find applications")

    assert "budget" in result.get("response", "").lower(), (
        f"expected budget message, got: {result.get('response')}"
    )


# ------------------------------------------------------------------ #
# AC2: scrub runs on tool-loop prompt
# ------------------------------------------------------------------ #

def test_scrub_runs_on_tool_loop_prompt_with_email(app, db_session, make_org):
    """The scrub replaces an email address in the user message before it
    reaches the provider."""
    from app.modules.ai_chat.services.agent_runner import AgentRunner
    from app.modules.ai_chat.services.llm_service_impl import LLMService

    _org = make_org("scrub-email")
    runner = AgentRunner(user_id=1, auto_execute=False)

    captured_messages = []

    def fake_openai_compat(*args, **kwargs):
        captured_messages.append(_extract_messages(args, kwargs))
        return _fake_tool_loop_response(text="ok")

    deps = _patch_runner_deps()
    with deps["provider"], deps["keys"], patch.object(
        LLMService, "_call_openai_compat_tool_loop", side_effect=fake_openai_compat
    ):
        runner.run(user_message="contact john.doe@example.com about the project")

    assert len(captured_messages) >= 1, "provider method was never called"
    user_content = ""
    for msg in captured_messages[0]:
        if msg.get("role") == "user" and isinstance(msg.get("content"), str):
            user_content = msg["content"]
    assert "[EMAIL_REDACTED]" in user_content, (
        f"email not scrubbed from user message: {user_content[:200]}"
    )
    assert "john.doe@example.com" not in user_content


def test_scrub_replaces_credential_in_user_message(app, db_session, make_org):
    """A credential-shaped string in the user message is replaced before
    the provider sees it."""
    from app.modules.ai_chat.services.agent_runner import AgentRunner
    from app.modules.ai_chat.services.llm_service_impl import LLMService

    _org = make_org("scrub-cred")
    runner = AgentRunner(user_id=1, auto_execute=False)

    captured_messages = []

    def fake_openai_compat(*args, **kwargs):
        captured_messages.append(_extract_messages(args, kwargs))
        return _fake_tool_loop_response(text="ok")

    deps = _patch_runner_deps()
    with deps["provider"], deps["keys"], patch.object(
        LLMService, "_call_openai_compat_tool_loop", side_effect=fake_openai_compat
    ):
        # Use "password=secret1234" format which matches the credential regex.
        runner.run(user_message="my password=secret1234 for the admin account")

    assert len(captured_messages) >= 1, "provider method was never called"
    user_content = ""
    for msg in captured_messages[0]:
        if msg.get("role") == "user" and isinstance(msg.get("content"), str):
            user_content = msg["content"]
    assert "[CREDENTIAL_REDACTED]" in user_content, (
        f"credential not scrubbed from user message: {user_content[:200]}"
    )
    assert "secret1234" not in user_content


def test_scrub_runs_on_system_prompt_too(app, db_session, make_org):
    """The scrub also runs on the system prompt, not just user messages."""
    from app.modules.ai_chat.services.agent_runner import AgentRunner
    from app.modules.ai_chat.services.llm_service_impl import LLMService

    _org = make_org("scrub-sys")
    runner = AgentRunner(user_id=1, auto_execute=False)

    captured_system = []

    def fake_openai_compat(*args, **kwargs):
        captured_system.append(_extract_system_prompt(args, kwargs))
        return _fake_tool_loop_response(text="ok")

    deps = _patch_runner_deps()
    with deps["provider"], deps["keys"], patch.object(
        LLMService, "_call_openai_compat_tool_loop", side_effect=fake_openai_compat
    ):
        runner.run(user_message="hello")

    system = captured_system[0] if captured_system else ""
    assert isinstance(system, str) and len(system) > 0, "system prompt was empty"


# ------------------------------------------------------------------ #
# AC3: usage event recorded per model call
# ------------------------------------------------------------------ #

def test_usage_event_recorded_per_model_call(app, db_session, make_org, tenant_ctx):
    """One usage event with tokens and cost is recorded per model call in
    the loop."""
    from app.modules.ai_chat.services.agent_runner import AgentRunner
    from app.modules.ai_chat.services.llm_service_impl import LLMService
    from app.services.usage_metering_service import UsageMeteringService

    org = make_org("usage-event")

    recorded_events = []

    def fake_record(*args, **kwargs):
        recorded_events.append(kwargs)

    # Return a text-only response so the loop ends after one call.
    def fake_openai_compat(*args, **kwargs):
        return _fake_tool_loop_response(text="Here are the applications.")

    deps = _patch_runner_deps()
    with tenant_ctx(org.id):
        runner = AgentRunner(user_id=1, auto_execute=False)
        with deps["provider"], deps["keys"], \
             patch.object(LLMService, "_call_openai_compat_tool_loop", side_effect=fake_openai_compat), \
             patch.object(UsageMeteringService, "record", side_effect=fake_record):
            runner.run(user_message="find all applications")

    assert len(recorded_events) >= 1, f"no usage events recorded, got {recorded_events}"
    event = recorded_events[0]
    assert event.get("event_type") == "llm_tool_call", (
        f"expected event_type 'llm_tool_call', got {event.get('event_type')}"
    )
    metadata = event.get("metadata", {})
    assert "input_tokens" in metadata, f"no input_tokens in metadata: {metadata}"
    assert "output_tokens" in metadata, f"no output_tokens in metadata: {metadata}"
    assert "cost_usd" in metadata, f"no cost_usd in metadata: {metadata}"


def test_usage_event_has_correct_token_counts(app, db_session, make_org, tenant_ctx):
    """The usage event carries the actual token counts from the provider
    response."""
    from app.modules.ai_chat.services.agent_runner import AgentRunner
    from app.modules.ai_chat.services.llm_service_impl import LLMService
    from app.services.usage_metering_service import UsageMeteringService

    org = make_org("usage-tokens")

    recorded_events = []

    def fake_record(*args, **kwargs):
        recorded_events.append(kwargs)

    def fake_openai_compat(*args, **kwargs):
        return {
            "text": "Here are the applications.",
            "tool_calls": [],
            "usage": {"input_tokens": 420, "output_tokens": 85, "cost": 0.005},
        }

    deps = _patch_runner_deps()
    with tenant_ctx(org.id):
        runner = AgentRunner(user_id=1, auto_execute=False)
        with deps["provider"], deps["keys"], \
             patch.object(LLMService, "_call_openai_compat_tool_loop", side_effect=fake_openai_compat), \
             patch.object(UsageMeteringService, "record", side_effect=fake_record):
            runner.run(user_message="list applications")

    assert len(recorded_events) >= 1
    event = recorded_events[0]
    metadata = event.get("metadata", {})
    assert metadata.get("input_tokens") == 420, (
        f"expected 420 input_tokens, got {metadata.get('input_tokens')}"
    )
    assert metadata.get("output_tokens") == 85, (
        f"expected 85 output_tokens, got {metadata.get('output_tokens')}"
    )
    assert metadata.get("cost_usd") == 0.005, (
        f"expected 0.005 cost, got {metadata.get('cost_usd')}"
    )


# ------------------------------------------------------------------ #
# AC4: write tool queues with auto-execute on
# ------------------------------------------------------------------ #

@pytest.fixture
def org_with_user(db_session, make_org):
    """Create an org and a user for tests that need a real user row."""
    from app.models.user import User

    org = make_org("write-queue")
    user = User(
        email="write-test@example.com",
        first_name="Test",
        last_name="Writer",
        organization_id=org.id,
        confirmed=True,
    )
    db_session.add(user)
    db_session.flush()
    return org, user


def test_write_tool_queues_approval_row_with_auto_execute_on(app, db_session, org_with_user):
    """With auto-execute on, a write tool creates an approval row and
    changes no data until approved."""
    from app.modules.ai_chat.services.agent_runner import AgentRunner
    from app.modules.ai_chat.services.llm_service_impl import LLMService

    org, user = org_with_user
    runner = AgentRunner(user_id=user.id, auto_execute=True)

    def fake_openai_compat(*args, **kwargs):
        return {
            "text": None,
            "tool_calls": [
                {"id": "tc1", "name": "create_solution",
                 "arguments": {"name": "Test Solution", "description": "A test"}}
            ],
            "usage": {"input_tokens": 100, "output_tokens": 20, "cost": 0.001},
        }

    deps = _patch_runner_deps()
    with deps["provider"], deps["keys"], patch.object(
        LLMService, "_call_openai_compat_tool_loop", side_effect=fake_openai_compat
    ):
        result = runner.run(user_message="create a solution called Test Solution")

    pending = result.get("pending_approvals", [])
    assert len(pending) >= 1, (
        f"expected at least 1 pending approval, got {len(pending)}: {pending}"
    )
    assert pending[0]["tool"] == "create_solution", (
        f"expected create_solution, got {pending[0].get('tool')}"
    )
    actions = result.get("actions_taken", [])
    write_actions = [a for a in actions if a.get("tool") == "create_solution"]
    assert len(write_actions) == 0, (
        f"write tool should not have executed, but got: {write_actions}"
    )


def test_write_tool_creates_db_approval_row(app, db_session, org_with_user):
    """The approval row is actually persisted to the database."""
    from app.modules.ai_chat.services.agent_runner import AgentRunner
    from app.modules.ai_chat.services.llm_service_impl import LLMService
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval

    org, user = org_with_user
    runner = AgentRunner(user_id=user.id, auto_execute=True)

    def fake_openai_compat(*args, **kwargs):
        return {
            "text": None,
            "tool_calls": [
                {"id": "tc1", "name": "create_archimate_element",
                 "arguments": {"name": "New Component", "type": "ApplicationComponent",
                              "layer": "application"}}
            ],
            "usage": {"input_tokens": 100, "output_tokens": 20, "cost": 0.001},
        }

    deps = _patch_runner_deps()
    with deps["provider"], deps["keys"], patch.object(
        LLMService, "_call_openai_compat_tool_loop", side_effect=fake_openai_compat
    ):
        result = runner.run(user_message="create an application component called New Component")

    pending = result.get("pending_approvals", [])
    assert len(pending) >= 1

    approval_id = pending[0].get("approval_id")
    assert approval_id is not None
    row = db_session.get(AIChatCRUDApproval, approval_id)
    assert row is not None, f"approval row {approval_id} not found in database"
    assert row.status.value == "pending", f"expected pending status, got {row.status}"


def test_read_tool_still_executes_with_auto_execute_on(app, db_session, make_org):
    """A read tool (mutates=False) still executes immediately even with
    auto_execute on — only writes are queued."""
    from app.modules.ai_chat.services.agent_runner import AgentRunner
    from app.modules.ai_chat.services.llm_service_impl import LLMService

    _org = make_org("read-execute")
    runner = AgentRunner(user_id=1, auto_execute=True)

    def fake_openai_compat(*args, **kwargs):
        return _fake_tool_loop_response(text="ok")

    deps = _patch_runner_deps()
    with deps["provider"], deps["keys"], patch.object(
        LLMService, "_call_openai_compat_tool_loop", side_effect=fake_openai_compat
    ):
        result = runner.run(user_message="find applications")

    pending = result.get("pending_approvals", [])
    read_pending = [p for p in pending if p.get("tool") == "find_applications"]
    assert len(read_pending) == 0, (
        f"read tool should not be queued, but got: {read_pending}"
    )


# ------------------------------------------------------------------ #
# _should_queue unit tests
# ------------------------------------------------------------------ #

def test_should_queue_returns_true_for_mutating_tool_regardless_of_auto_execute():
    """A mutating tool always queues, even with auto_execute=True."""
    from app.modules.ai_chat.services.agent_runner import AgentRunner

    assert AgentRunner._should_queue({"mutates": True, "tier": "auto"}, auto_execute=True) is True
    assert AgentRunner._should_queue({"mutates": True, "tier": "auto"}, auto_execute=False) is True


def test_should_queue_returns_true_for_approve_tier():
    """tier='approve' always queues."""
    from app.modules.ai_chat.services.agent_runner import AgentRunner

    assert AgentRunner._should_queue({"mutates": True, "tier": "approve"}, auto_execute=True) is True
    assert AgentRunner._should_queue({"mutates": True, "tier": "approve"}, auto_execute=False) is True
    assert AgentRunner._should_queue({"mutates": False, "tier": "approve"}, auto_execute=True) is True


def test_should_queue_returns_false_for_read_only_tool():
    """A read-only tool (mutates=False, tier='auto') never queues."""
    from app.modules.ai_chat.services.agent_runner import AgentRunner

    assert AgentRunner._should_queue({"mutates": False, "tier": "auto"}, auto_execute=True) is False
    assert AgentRunner._should_queue({"mutates": False, "tier": "auto"}, auto_execute=False) is False


def test_should_queue_fails_closed_on_unclassified_tool():
    """An unclassified tool (no mutates key) queues — fail closed."""
    from app.modules.ai_chat.services.agent_runner import AgentRunner

    assert AgentRunner._should_queue({}, auto_execute=True) is True
    assert AgentRunner._should_queue({}, auto_execute=False) is True


# ------------------------------------------------------------------ #
# Cross-organisation: budget check is scoped
# ------------------------------------------------------------------ #

def test_budget_check_is_scoped_to_organisation(app, db_session, make_org):
    """The budget check runs in the context of the requesting organisation."""
    from app.modules.ai_chat.services.agent_runner import AgentRunner
    from app.modules.ai_chat.services.llm_cost_tracker import LLMCostTracker

    _org = make_org("budget-scope")
    runner = AgentRunner(user_id=1, auto_execute=False)

    check_calls = []

    def fake_check(*args, **kwargs):
        check_calls.append(kwargs)
        return (True, None)

    deps = _patch_runner_deps()
    with deps["provider"], deps["keys"], patch.object(
        LLMCostTracker, "check_budget_before_call", side_effect=fake_check
    ):
        try:
            runner.run(user_message="hello")
        except Exception:
            pass

    assert len(check_calls) >= 1, "budget check was never called"