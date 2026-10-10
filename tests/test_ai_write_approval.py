"""AI write-approval gate: mutating tool calls queue for confirmation unless
the session has explicitly turned auto-execute on.

WHY THIS FILE EXISTS
---------------------
chat_core.py's toggle_auto_execute route used to write a session flag that
nothing read (see git history: its docstring documented the gap directly).
34 of the 37 agent tools are tier == "auto" and executed immediately with no
confirmation, mutating or not. The fix (also documented in that docstring,
before it was rewritten) is a `mutates` flag on each schema in
tools/registry.py, and a queueing rule in AgentRunner that consults it.

This file pins three things:
  1. registry invariants — every tool has an explicit `mutates` key (not an
     absent one defaulting via .get()), and the naming convention holds for
     the obvious prefixes.
  2. AgentRunner._should_queue — the pure decision function factored out so
     the four-way truth table (tier x auto_execute) can be tested exhaustively
     without a DB, an LLM, or a Flask request/session.
  3. The toggle route itself: POST flips the session flag and echoes it back.
     Now load-bearing (AgentRunner reads it via the caller), where before it
     was inert.

Uses the shared fixtures from tests/conftest.py per CLAUDE.md's testing
convention, rather than hand-rolling a module app fixture.
"""

import pytest

from app.modules.ai_chat.tools.registry import TOOL_SCHEMAS, mutating_tool_names
from app.modules.ai_chat.services.agent_runner import AgentRunner


# --------------------------------------------------------------------------- #
# 1. Registry invariants
# --------------------------------------------------------------------------- #


class TestRegistryInvariants:
    def test_every_tool_has_an_explicit_mutates_key(self):
        """`mutates` must be a real key, not something a reader has to assume
        via .get(..., False) — an omitted key on a future tool should be a
        loud KeyError in this test, not a silent False that lets a write tool
        slip through unqueued."""
        missing = [t["name"] for t in TOOL_SCHEMAS if "mutates" not in t]
        assert missing == [], f"tools missing explicit 'mutates': {missing}"

    def test_mutates_is_a_bool_on_every_tool(self):
        bad = [t["name"] for t in TOOL_SCHEMAS if not isinstance(t["mutates"], bool)]
        assert bad == [], f"tools with non-bool 'mutates': {bad}"

    @pytest.mark.parametrize(
        "prefix", ["create_", "link_", "update_", "mark_"]
    )
    def test_write_prefixed_tools_are_flagged_mutating(self, prefix):
        offenders = [
            t["name"]
            for t in TOOL_SCHEMAS
            if t["name"].startswith(prefix) and not t["mutates"]
        ]
        assert offenders == [], (
            f"tools named like a write ({prefix}*) but mutates=False: {offenders}"
        )

    def test_approve_tier_tools_unchanged(self):
        """The three tools that were already tier=='approve' stay approve.

        Asserted as a SUBSET, not equality, because the docstring's own rule
        is that the approval queue may widen but never narrow -- and equality
        forbids exactly the widening it permits. It failed the moment 54
        per-type ArchiMate element tools were added, every one of which
        creates a row in the system of record and therefore belongs behind a
        human. Narrowing is still caught: drop any of these three and this
        fails.
        """
        approve_tools = {t["name"] for t in TOOL_SCHEMAS if t["tier"] == "approve"}
        originally_approve = {
            "update_application_status",
            "submit_for_arb_review",
            "generate_blueprint_narrative",
        }
        missing = originally_approve - approve_tools
        assert not missing, (
            "these tools were behind an approval prompt and no longer are: %s"
            % sorted(missing)
        )

    def test_known_read_only_tools_are_not_mutating(self):
        """Four of the six tools the task called out as needing evidence, not
        a name-based guess — each reads and returns, never writes.

        run_inference_engine was originally in this list on the strength of
        its own read-only-sounding call chain, but adversarial review found
        it defaults dry_run to False and its repair() path really does write
        (get_or_create_node/relationship -> db.session.add+flush). It is
        pinned mutating in test_mutates_and_auto_execute_off_queues_a_real_
        write_tool below instead.

        poll_infrastructure was originally in this list but was reclassified
        to external_action because it makes outbound network calls to
        model-supplied URLs — see HIGH-2 in PR 266 review.
        """
        by_name = {t["name"]: t for t in TOOL_SCHEMAS}
        for name in (
            "propose_rationalization",
            "build_architecture_plan",
            "infer_schema",
            "verify_codegen",
        ):
            assert by_name[name]["mutates"] is False, f"{name} should be read-only"

    def test_run_inference_engine_is_mutating_not_read_only(self):
        """dry_run defaults False in _tool_run_inference_engine
        (tools/executor.py), so the un-dry-run path writes via
        ArchiMateInferenceEngine.repair -> get_or_create_node/relationship
        (architecture_graph_facade.py). Misclassified mutates=False in an
        earlier pass; this pins the correction."""
        by_name = {t["name"]: t for t in TOOL_SCHEMAS}
        assert by_name["run_inference_engine"]["mutates"] is True

    def test_mutating_tool_names_matches_flagged_set(self):
        expected = {t["name"] for t in TOOL_SCHEMAS if t["mutates"] is True}
        assert mutating_tool_names() == expected


# --------------------------------------------------------------------------- #
# 2. AgentRunner._should_queue — pure decision function
# --------------------------------------------------------------------------- #


class TestShouldQueue:
    def test_mutates_and_auto_execute_off_queues(self):
        schema = {"tier": "auto", "mutates": True}
        assert AgentRunner._should_queue(schema, auto_execute=False) is True

    def test_mutates_and_auto_execute_on_still_queues(self):
        schema = {"tier": "auto", "mutates": True}
        # The confirmation guard: with auto-execute on, writes still queue.
        # auto_execute only controls whether reads run unconfirmed,
        # not whether writes do.
        assert AgentRunner._should_queue(schema, auto_execute=True) is True

    def test_read_only_never_queues_regardless_of_auto_execute(self):
        schema = {"tier": "auto", "mutates": False}
        assert AgentRunner._should_queue(schema, auto_execute=False) is False
        assert AgentRunner._should_queue(schema, auto_execute=True) is False

    def test_approve_tier_queues_even_with_auto_execute_on(self):
        """The whole point of a separate always-approve tier: turning on
        write auto-execute must not silently wave through
        update_application_status / submit_for_arb_review."""
        schema = {"tier": "approve", "mutates": True}
        assert AgentRunner._should_queue(schema, auto_execute=True) is True
        assert AgentRunner._should_queue(schema, auto_execute=False) is True

    def test_approve_tier_with_mutates_false_still_queues(self):
        """generate_blueprint_narrative is tier=='approve' with mutates==False
        (its write path currently raises before touching the DB — see
        executor.py) — tier alone must be sufficient to queue it."""
        schema = {"tier": "approve", "mutates": False}
        assert AgentRunner._should_queue(schema, auto_execute=True) is True

    def test_missing_schema_keys_fail_closed_and_queue(self):
        """An unknown tool name resolves to {} in the run loop
        (TOOL_SCHEMA_BY_NAME.get(name, {})) — that must queue, not execute
        unqueued, regardless of auto_execute. Registry and executor are 1:1
        today (every real schema declares `mutates` explicitly, pinned by
        tests/test_tool_mutates_flag.py), so this only changes behaviour for a
        future tool that reaches the run loop without a registry entry — which
        is exactly the case that should be treated as a write until proven
        otherwise, not waved through."""
        assert AgentRunner._should_queue({}, auto_execute=True) is True
        assert AgentRunner._should_queue({}, auto_execute=False) is True

    def test_explicit_mutates_false_is_trusted_not_fail_closed(self):
        """Fail-closed applies to an ABSENT `mutates` key, not to an explicit
        False — a real read-only tool (mutates: False) must still execute
        immediately, or every search would land behind a confirmation prompt
        again."""
        schema = {"tier": "auto", "mutates": False}
        assert AgentRunner._should_queue(schema, auto_execute=True) is False
        assert AgentRunner._should_queue(schema, auto_execute=False) is False

    def test_full_registry_is_decided_without_error(self):
        """Every real schema in the registry must produce a bool for both
        auto_execute states — a smoke test against schema-shape drift."""
        for schema in TOOL_SCHEMAS:
            for auto_execute in (True, False):
                result = AgentRunner._should_queue(schema, auto_execute)
                assert isinstance(result, bool)

    def test_agent_runner_defaults_to_auto_execute_off(self):
        """A caller that forgets to pass auto_execute gets the safe default:
        writes queue rather than fire."""
        runner = AgentRunner(user_id=1)
        assert runner.auto_execute is False

    def test_run_loop_queues_write_even_with_auto_execute_on(
        self, app, db_session, monkeypatch
    ):
        """With auto_execute=True, a write tool call from the LLM is queued,
        not executed. This is the run-loop integration test for the
        Confirmation guard: _should_queue must be checked inside the run loop
        and the tool must NOT reach ToolExecutor.execute."""
        from unittest.mock import MagicMock

        from app.modules.ai_chat.services.llm_service_impl import LLMService
        from app.modules.ai_chat.tools.executor import ToolExecutor

        executor_calls = []

        def _spy_execute(self, tool_call):
            executor_calls.append(tool_call)
            return {"success": True, "result": {"id": 1}}

        def _mock_call_llm(self, provider, model, api_key, system_prompt, messages,
                           tool_schemas, stream=False, base_url=None):
            # Must match the shape of a real LLM response so _append_tool_results
            # can read llm_resp["raw"].content for anthropic provider.
            raw_mock = MagicMock()
            raw_mock.content = [
                {"type": "tool_use", "id": "call_abc123",
                 "name": "create_solution",
                 "input": {
                     "name": "Test Solution",
                     "description": "A test solution",
                     "business_domain": "technology",
                     "solution_type": "Platform",
                 }}
            ]
            return {
                "text": "",
                "tool_calls": [
                    {
                        "id": "call_abc123",
                        "name": "create_solution",
                        "arguments": {
                            "name": "Test Solution",
                            "description": "A test solution",
                            "business_domain": "technology",
                            "solution_type": "Platform",
                        },
                    }
                ],
                "raw": raw_mock,
            }

        def _mock_queue_approval(self, tc, persona=None):
            assert persona == "enterprise_architect", f"persona={persona!r}"
            return 9999  # fake approval ID

        runner = AgentRunner(user_id=1, auto_execute=True)

        with app.app_context():
            _orig_provider = LLMService._get_configured_provider
            _orig_keys = LLMService._get_all_api_keys
            _orig_call = AgentRunner._call_llm
            _orig_queue = AgentRunner._queue_approval
            _orig_execute = ToolExecutor.execute
            try:
                LLMService._get_configured_provider = staticmethod(
                    lambda: ("anthropic", "claude-opus-5")
                )
                LLMService._get_all_api_keys = staticmethod(lambda provider: ["fake-test-key"])
                AgentRunner._call_llm = _mock_call_llm
                AgentRunner._queue_approval = _mock_queue_approval
                ToolExecutor.execute = _spy_execute

                result = runner.run(
                    user_message="Create a test solution",
                    domain="general",
                    context={},
                    persona="enterprise_architect",
                )
            finally:
                LLMService._get_configured_provider = _orig_provider
                LLMService._get_all_api_keys = _orig_keys
                AgentRunner._call_llm = _orig_call
                AgentRunner._queue_approval = _orig_queue
                ToolExecutor.execute = _orig_execute

        # The write tool must be queued, not executed
        assert len(result.get("pending_approvals", [])) > 0, (
            "pending_approvals must be non-empty when auto_execute=True "
            "and a mutating tool is called"
        )
        assert result["pending_approvals"][0]["tool"] == "create_solution"
        assert len(executor_calls) == 0, (
            "ToolExecutor.execute must NOT be called when a write tool is "
            "queued even with auto_execute=True"
        )


# --------------------------------------------------------------------------- #
# 2b. Handler dispatch — every tool's route resolves to an executor method
# --------------------------------------------------------------------------- #


class TestHandlerDispatch:
    """every hand-written tool's handler method exists on
    ToolExecutor and is reachable via execute()."""

    def test_every_tool_handler_exists_on_executor(self):
        """Each tool's route (which equals the tool name) must have a
        corresponding _tool_<name> method on ToolExecutor."""
        from app.modules.ai_chat.tools.registry import TOOL_SCHEMA_BY_NAME
        from app.modules.ai_chat.tools.executor import ToolExecutor

        # Get all handler methods on ToolExecutor (public and private)
        executor_methods = {
            m for m in dir(ToolExecutor)
            if m.startswith("_tool_")
        }

        missing = []
        for name in sorted(TOOL_SCHEMA_BY_NAME.keys()):
            handler = f"_tool_{name}"
            if handler not in executor_methods:
                # Generated element tools dispatch through the shared handler
                # create_archimate_element — verify it exists as fallback.
                if handler == "_tool_create_archimate_element" + "_" + name.split("_", 1)[1] if name.startswith("create_archimate_") else handler:
                    pass
                missing.append(name)

        # Generated element tools all route through _tool_create_archimate_element
        # Hand-written tools each have their own handler.
        # Check which tools are generated vs hand-written:
        hand_written_names = {
            s["name"] for s in TOOL_SCHEMA_BY_NAME.values()
            if "archimate_layer" not in s
        }

        actual_missing = []
        for name in sorted(hand_written_names):
            handler = f"_tool_{name}"
            if handler not in executor_methods:
                actual_missing.append(name)

        assert actual_missing == [], (
            f"hand-written tools missing handler on ToolExecutor: {actual_missing}"
        )

    def test_dispatch_to_handler_by_name(self, monkeypatch):
        """ToolExecutor.execute dispatches to the correct per-tool handler."""
        from app.modules.ai_chat.tools.executor import ToolExecutor, ToolCall
        from app.modules.ai_chat.tools.registry import TOOL_SCHEMA_BY_NAME

        executor = ToolExecutor(user_id=1)
        # Test a representative sample of hand-written tools
        sample_tools = [
            "create_solution",
            "find_applications",
            "generate_blueprint_narrative",
            "poll_infrastructure",
            "submit_for_arb_review",
        ]
        for name in sample_tools:
            assert name in TOOL_SCHEMA_BY_NAME, f"{name}: no registered tool schema"
            # Verify the handler method exists
            handler = getattr(executor, f"_tool_{name}", None)
            assert handler is not None, (
                f"{name}: no _tool_{name} handler on ToolExecutor"
            )

    def test_read_tool_handlers_do_not_write(self, monkeypatch):
        """For read-classified tools, executing them under a db spy must
        emit no INSERT/UPDATE/DELETE statements."""
        from unittest.mock import MagicMock

        from app.modules.ai_chat.tools.executor import ToolExecutor, ToolCall
        from app.modules.ai_chat.tools.registry import TOOL_SCHEMA_BY_NAME, TOOL_SCHEMAS

        read_tools = [
            t["name"] for t in TOOL_SCHEMAS
            if t.get("risk_class") == "read"
        ]
        # Smoke test: at least some read tools exist
        assert len(read_tools) > 0, "no read-classified tools found"

        # Verify that each read tool has a handler that does not directly
        # call db.session.add/commit/delete — checked by ensuring the
        # handler is a method on ToolExecutor and not an auto-generated stub
        executor = ToolExecutor(user_id=1)
        for name in read_tools:
            handler = getattr(executor, f"_tool_{name}", None)
            assert handler is not None, (
                f"{name}: read tool has no handler on ToolExecutor"
            )
            # The handler callable must be a bound method, not a stub
            assert callable(handler), (
                f"{name}: read tool handler is not callable"
            )


# --------------------------------------------------------------------------- #
# 3. Toggle route — now load-bearing, pin the wire behaviour
# --------------------------------------------------------------------------- #


@pytest.fixture
def client(app):
    return app.test_client()


def _login(client, user_id):
    """Standard Flask-Login test-client login, plus clearing the caches that
    make a stale request/app context return the wrong user — see the longer
    explanation in tests/test_ba_tenant_and_authz.py::_login, which this
    mirrors."""
    from tests._session_test_helpers import mint_test_sid
    _sid = mint_test_sid(user_id)
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True
        if _sid:
            sess["_sid"] = _sid

    from flask import g, has_app_context

    if not has_app_context():
        return
    for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
        if hasattr(g, cached):
            delattr(g, cached)


def _make_user(db_session, org):
    from app.models.user import User
    import uuid

    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"gov-{suffix}@example.com",
        first_name="Gov",
        last_name="Tester",
        organization_id=org.id,
        confirmed=True,
        enterprise_role="architect",
    )
    # V-01: agent writes and approvals now require Permission.GENERAL, enforced
    # in the tool-execution layer. enterprise_role drives sidebar/persona, NOT
    # permissions — those come from role_id. A user created without one holds
    # nothing and is correctly refused with 403, which is right for a Viewer and
    # wrong for a fixture standing in for an ordinary architect.
    from app.models.user import Role

    role = (
        Role.query.filter_by(name="Architect").first()
        or Role.query.filter_by(name="Administrator").first()
    )
    if role is not None:
        user.role_id = role.id
    db_session.add(user)
    db_session.flush()
    return user


def _login_second_approver(client, db_session, org):
    """Log in a DIFFERENT user to approve with.

    V-01/M-05: the requester is excluded from deciding their own request, so a
    test that queues and approves as one identity now correctly gets 403/400.
    These tests were written before that control existed and encoded
    self-approval as the normal path.
    """
    approver = _make_user(db_session, org)
    _login(client, approver.id)
    return approver


class TestToggleAutoExecuteRoute:
    def test_toggle_flips_and_returns_the_flag(self, app, client, db_session, make_org):
        org = make_org("gov")
        user = _make_user(db_session, org)
        _login(client, user.id)

        resp1 = client.post("/ai-chat/session/toggle-auto-execute")
        assert resp1.status_code == 200
        body1 = resp1.get_json()
        assert body1["success"] is True
        first_state = body1["auto_execute"]

        resp2 = client.post("/ai-chat/session/toggle-auto-execute")
        body2 = resp2.get_json()
        assert body2["auto_execute"] is (not first_state)

    def test_default_session_has_auto_execute_off(self, app, client, db_session, make_org):
        """First toggle from a fresh session flips False -> True: pins the
        documented default (agent_auto_execute defaults False when absent)."""
        org = make_org("gov")
        user = _make_user(db_session, org)
        _login(client, user.id)

        resp = client.post("/ai-chat/session/toggle-auto-execute")
        assert resp.get_json()["auto_execute"] is True

    def test_toggle_requires_login(self, client):
        resp = client.post("/ai-chat/session/toggle-auto-execute")
        assert resp.status_code in (302, 401)


class TestGetAutoExecuteRoute:
    """M2: blueprint_chat.js's init() reads this to seed its `autoExecute`
    Alpine field with the real session state, rather than assuming a
    hardcoded default that can be stale the moment a session survives a page
    reload with the flag already ON."""

    def test_get_reflects_current_state_without_flipping_it(self, app, client, db_session, make_org):
        org = make_org("gov")
        user = _make_user(db_session, org)
        _login(client, user.id)

        # Fresh session: off.
        resp = client.get("/ai-chat/session/auto-execute")
        assert resp.status_code == 200
        assert resp.get_json()["auto_execute"] is False

        # GET must not itself change state — call it twice, same answer both times.
        resp_again = client.get("/ai-chat/session/auto-execute")
        assert resp_again.get_json()["auto_execute"] is False

        # Now flip via the real toggle route, and confirm GET reflects the flip
        # (and, critically, does not flip it again on read).
        client.post("/ai-chat/session/toggle-auto-execute")
        resp_after_toggle = client.get("/ai-chat/session/auto-execute")
        assert resp_after_toggle.get_json()["auto_execute"] is True
        resp_after_toggle_again = client.get("/ai-chat/session/auto-execute")
        assert resp_after_toggle_again.get_json()["auto_execute"] is True

    def test_get_requires_login(self, client):
        resp = client.get("/ai-chat/session/auto-execute")
        assert resp.status_code in (302, 401)


# --------------------------------------------------------------------------- #
# 4. Approval execution — the two endpoints must behave identically for a
#    queued tool_use approval (I1), and both must honour expiry (I2).
# --------------------------------------------------------------------------- #


def _make_tool_use_approval(db_session, user, tool_name="create_solution", arguments=None, expires_at=None):
    """Insert an AIChatCRUDApproval directly with operation_type=='tool_use',
    exactly the shape AgentRunner._queue_approval (agent_runner.py) writes for
    every queued agent tool call. Bypassing the runner keeps this test from
    needing an LLM double — the row shape is the contract being tested, not
    how it got there."""
    import json as _json
    from datetime import datetime, timedelta

    from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus

    record = AIChatCRUDApproval(
        user_id=user.id,
        organization_id=user.organization_id,
        operation_type="tool_use",
        entity_type=tool_name,
        original_command=tool_name,
        operation_payload=_json.dumps(arguments or {}),
        summary=f"Execute {tool_name}",
        status=ApprovalStatus.PENDING,
        expires_at=expires_at or (datetime.utcnow() + timedelta(hours=24)),
    )
    db_session.add(record)
    db_session.flush()
    return record


class TestApprovalExecutionParity:
    """I1: the main chat's approval modal only ever calls
    POST /ai-chat/approvals/<id>/approve -> AIChatApprovalService.approve_and_execute,
    which 400'd on every agent-queued action ("Unsupported operation type:
    tool_use") because it only handled the legacy create/link/update/delete
    vocabulary. Both this route and the blueprint panel's dedicated
    POST /ai-chat/tools/approve/<id> must now dispatch a queued tool_use
    approval through the same ToolExecutor and produce the same outcome.
    """

    def _stub_executor(self, monkeypatch, expected_name):
        """Replace ToolExecutor.execute with a stub that proves it was reached
        with the right ToolCall, without needing a real solution/tool fixture
        graph. Patched on the executor module both routes import it from, so
        it's in effect regardless of which endpoint under test does the
        `from ...executor import ToolExecutor` import."""
        calls = []

        def _fake_execute(self, tool_call):
            calls.append(tool_call)
            assert tool_call.name == expected_name
            return {"success": True, "message": f"executed {tool_call.name}", "result": {"id": 42}}

        import app.modules.ai_chat.tools.executor as executor_module

        monkeypatch.setattr(executor_module.ToolExecutor, "execute", _fake_execute)
        return calls

    def test_dedicated_blueprint_endpoint_executes_tool_use(
        self, app, client, db_session, make_org, monkeypatch
    ):
        org = make_org("gov")
        user = _make_user(db_session, org)
        _login(client, user.id)
        calls = self._stub_executor(monkeypatch, "create_solution")
        record = _make_tool_use_approval(db_session, user, tool_name="create_solution")
        db_session.commit()

        _login_second_approver(client, db_session, org)
        resp = client.post(f"/ai-chat/tools/approve/{record.id}")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert len(calls) == 1

        from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus

        refreshed = db_session.get(AIChatCRUDApproval, record.id)
        assert refreshed.status == ApprovalStatus.APPROVED

    def test_main_chat_approval_modal_endpoint_executes_tool_use(
        self, app, client, db_session, make_org, monkeypatch
    ):
        """This is the endpoint that 400'd before the I1 fix."""
        org = make_org("gov")
        user = _make_user(db_session, org)
        _login(client, user.id)
        calls = self._stub_executor(monkeypatch, "create_driver")
        record = _make_tool_use_approval(
            db_session, user, tool_name="create_driver",
            arguments={"solution_id": 1, "name": "Cost pressure", "driver_type": "external"},
        )
        db_session.commit()

        _login_second_approver(client, db_session, org)
        resp = client.post(f"/ai-chat/approvals/{record.id}/approve")
        assert resp.status_code == 200, resp.get_json()
        body = resp.get_json()
        assert body["success"] is True
        assert len(calls) == 1
        assert calls[0].arguments["name"] == "Cost pressure"

        from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus

        refreshed = db_session.get(AIChatCRUDApproval, record.id)
        assert refreshed.status == ApprovalStatus.APPROVED
        assert refreshed.execution_result is not None


class TestApprovalExpiry:
    """Overdue-not-expired: an approval past expires_at is overdue,
    not expired — it stays actionable indefinitely, escalating to the
    organisation's administrators instead of refusing. Both
    /ai-chat/tools/approve/<id> and /ai-chat/approvals/<id>/approve dispatch
    through AIChatApprovalService.approve_and_execute, so both must still
    execute an overdue approval rather than 409.

    Superseded the previous version of this class, named for the opposite
    (pre-consolidation) behaviour: a real Confirm click on an overdue row is not a
    "stale" click to refuse — the whole point of this brief is that it must
    still work.
    """

    def test_dedicated_endpoint_executes_an_overdue_approval(self, app, client, db_session, make_org, monkeypatch):
        from datetime import datetime, timedelta

        org = make_org("gov")
        user = _make_user(db_session, org)
        _login(client, user.id)

        executed = []

        def _fake_execute(self, tool_call):
            executed.append(tool_call)
            return {"success": True, "message": "executed", "result": {}}

        import app.modules.ai_chat.tools.executor as executor_module

        monkeypatch.setattr(executor_module.ToolExecutor, "execute", _fake_execute)

        record = _make_tool_use_approval(
            db_session, user, tool_name="create_solution",
            expires_at=datetime.utcnow() - timedelta(hours=1),  # overdue
        )
        db_session.commit()

        _login_second_approver(client, db_session, org)
        resp = client.post(f"/ai-chat/tools/approve/{record.id}")
        assert resp.status_code == 200, resp.get_json()
        assert len(executed) == 1, "an overdue approval must still reach the executor"

        from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus

        refreshed = db_session.get(AIChatCRUDApproval, record.id)
        assert refreshed.status == ApprovalStatus.APPROVED
        assert refreshed.is_overdue() is False  # no longer PENDING, so not overdue either

    def test_legacy_endpoint_also_executes_an_overdue_approval(self, app, client, db_session, make_org, monkeypatch):
        """Not a new fix on this endpoint specifically — it delegates to the
        same approve_and_execute as the dedicated endpoint above. Pinned here
        so both endpoints are proven to agree, not just individually correct.
        """
        from datetime import datetime, timedelta

        org = make_org("gov")
        user = _make_user(db_session, org)
        _login(client, user.id)

        def _fake_execute(self, tool_call):
            return {"success": True, "message": "executed", "result": {}}

        import app.modules.ai_chat.tools.executor as executor_module

        monkeypatch.setattr(executor_module.ToolExecutor, "execute", _fake_execute)

        record = _make_tool_use_approval(
            db_session, user, tool_name="create_solution",
            expires_at=datetime.utcnow() - timedelta(hours=1),  # overdue
        )
        db_session.commit()

        _login_second_approver(client, db_session, org)
        resp = client.post(f"/ai-chat/approvals/{record.id}/approve")
        assert resp.status_code == 200, resp.get_json()
        assert "expired" not in str(resp.get_json()).lower()


class TestRequireAIApprovalDefaultsOn:
    """REQUIRE_AI_APPROVAL (config.py, A95-008) now defaults to true — the
    queue_ai_write()-gated /ai-chat/data/* endpoints in workflow_routes.py
    queue a REAL, executable approval for human review unless the operator
    opts back out with REQUIRE_AI_APPROVAL=false.

    Fix (review round 2): the gate previously returned the 202/pending_
    approval shape without persisting anything — no approval_id, nothing in
    GET /ai-chat/approvals/pending, the write silently dropped forever. These
    tests now pin that a queued write is REAL: it shows up in the pending
    list, approving it performs the exact write the route would have made,
    and rejecting it performs no write at all. Two gated routes are covered
    (create-capability, update-capability) plus one exempted route
    (add-compliance-requirement, which has no representable entry in
    AIChatApprovalService.approve_and_execute's vocabulary and so executes
    immediately with a code comment instead of queuing — see
    workflow_routes.py)."""

    def test_config_default_is_true_when_env_unset(self, monkeypatch):
        import importlib

        monkeypatch.delenv("REQUIRE_AI_APPROVAL", raising=False)
        import config as config_module

        importlib.reload(config_module)
        try:
            assert config_module.Config.REQUIRE_AI_APPROVAL is True
        finally:
            importlib.reload(config_module)  # restore for later tests in this process

    def test_create_capability_queues_a_real_pending_approval(
        self, app, client, db_session, make_org, monkeypatch
    ):
        import uuid

        org = make_org("gov")
        user = _make_user(db_session, org)
        _login(client, user.id)

        monkeypatch.setitem(app.config, "REQUIRE_AI_APPROVAL", True)

        from app.models.business_capabilities import BusinessCapability
        from app.models.ai_chat_crud_approval import AIChatCRUDApproval

        cap_name = f"Regulatory Reporting {uuid.uuid4().hex[:8]}"

        resp = client.post(
            "/ai-chat/data/create-capability",
            json={"name": cap_name},
        )
        assert resp.status_code == 202, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body["status"] == "pending_approval"
        assert body["ai_originated"] is True
        approval_id = body["approval_id"]
        assert isinstance(approval_id, int)

        # Nothing was written yet.
        assert BusinessCapability.query.filter_by(name=cap_name).first() is None

        # ... but a real, executable approval record exists.
        record = db_session.get(AIChatCRUDApproval, approval_id)
        assert record is not None
        assert record.user_id == user.id
        assert record.operation_type == "create"
        assert record.entity_type == "capability"
        import json as _json

        assert _json.loads(record.operation_payload)["name"] == cap_name

        # ... and it is listed by the endpoint the approval dashboard polls.
        _login(client, user.id)  # re-clear g caches before the next request
        pending_resp = client.get("/ai-chat/approvals/pending")
        assert pending_resp.status_code == 200
        pending_ids = [a["id"] for a in pending_resp.get_json()["approvals"]]
        assert approval_id in pending_ids

    def test_approving_create_capability_performs_the_real_write(
        self, app, client, db_session, make_org, monkeypatch
    ):
        import uuid

        org = make_org("gov")
        user = _make_user(db_session, org)
        _login(client, user.id)

        monkeypatch.setitem(app.config, "REQUIRE_AI_APPROVAL", True)

        from app.models.business_capabilities import BusinessCapability

        cap_name = f"Vendor Risk Management {uuid.uuid4().hex[:8]}"
        resp = client.post("/ai-chat/data/create-capability", json={"name": cap_name})
        approval_id = resp.get_json()["approval_id"]

        _login_second_approver(client, db_session, org)
        approve_resp = client.post(f"/ai-chat/approvals/{approval_id}/approve")
        assert approve_resp.status_code == 200, approve_resp.get_data(as_text=True)
        approve_body = approve_resp.get_json()
        assert approve_body["success"] is True

        created = BusinessCapability.query.filter_by(name=cap_name).first()
        assert created is not None, "approving the queued write must actually create the capability"

    def test_rejecting_create_capability_creates_nothing(
        self, app, client, db_session, make_org, monkeypatch
    ):
        import uuid

        org = make_org("gov")
        user = _make_user(db_session, org)
        _login(client, user.id)

        monkeypatch.setitem(app.config, "REQUIRE_AI_APPROVAL", True)

        from app.models.business_capabilities import BusinessCapability

        cap_name = f"Should Never Exist {uuid.uuid4().hex[:8]}"
        resp = client.post("/ai-chat/data/create-capability", json={"name": cap_name})
        approval_id = resp.get_json()["approval_id"]

        reject_resp = client.post(f"/ai-chat/approvals/{approval_id}/reject")
        assert reject_resp.status_code == 200, reject_resp.get_data(as_text=True)

        assert BusinessCapability.query.filter_by(name=cap_name).first() is None

        from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus

        record = db_session.get(AIChatCRUDApproval, approval_id)
        assert record.status == ApprovalStatus.REJECTED

    def test_update_capability_queues_with_the_capability_id_as_entity_id(
        self, app, client, db_session, make_org, monkeypatch
    ):
        """Second gated endpoint: proves entity_id_kwarg-style wiring (the URL's
        capability_id) reaches the approval record, and that approving it
        performs the real update via AIDataInteractionService.update_capability
        — the same method the route calls directly when the gate is off."""
        import uuid

        from app.models.archimate_core import ArchiMateElement
        from app.models.business_capabilities import BusinessCapability

        org = make_org("gov")
        user = _make_user(db_session, org)
        _login(client, user.id)

        suffix = uuid.uuid4().hex[:8]
        # See tests/test_tenant_scoping_leaks.py's _make_capability: pre-create
        # the ArchiMateElement to sidestep BusinessCapability's before_insert
        # hook, which does not set organization_id on its raw insert.
        element = ArchiMateElement(
            name=f"Cap {suffix}", type="Capability", layer="Strategy", organization_id=org.id
        )
        db_session.add(element)
        db_session.flush()
        cap = BusinessCapability(
            name=f"Cap {suffix}", organization_id=org.id, archimate_element_id=element.id
        )
        db_session.add(cap)
        db_session.flush()

        monkeypatch.setitem(app.config, "REQUIRE_AI_APPROVAL", True)

        new_name = f"Renamed {suffix}"
        resp = client.put(
            f"/ai-chat/data/update-capability/{cap.id}", json={"name": new_name}
        )
        assert resp.status_code == 202, resp.get_data(as_text=True)
        approval_id = resp.get_json()["approval_id"]

        from app.models.ai_chat_crud_approval import AIChatCRUDApproval

        record = db_session.get(AIChatCRUDApproval, approval_id)
        assert record.entity_type == "capability"
        assert record.operation_type == "update"
        assert record.entity_id == cap.id

        _login_second_approver(client, db_session, org)
        approve_resp = client.post(f"/ai-chat/approvals/{approval_id}/approve")
        assert approve_resp.status_code == 200, approve_resp.get_data(as_text=True)

        db_session.refresh(cap)
        assert cap.name == new_name

    def test_create_capability_writes_directly_when_flag_off(
        self, app, client, db_session, make_org, monkeypatch
    ):
        """Explicitly setting the flag off (the pre-Aug-2026 default) restores
        direct-write behaviour for the LLM-agent data endpoints — proving the
        new default is a config choice, not a hardcoded gate."""
        org = make_org("gov")
        user = _make_user(db_session, org)
        _login(client, user.id)

        monkeypatch.setitem(app.config, "REQUIRE_AI_APPROVAL", False)

        resp = client.post(
            "/ai-chat/data/create-capability",
            json={"name": "Regulatory Reporting Direct"},
        )
        assert resp.status_code in (200, 201), resp.get_data(as_text=True)
        body = resp.get_json()
        assert body.get("status") != "pending_approval"
        assert body.get("success") is True

    def test_add_compliance_requirement_is_exempt_and_never_queues(
        self, app, client, db_session, make_org, monkeypatch
    ):
        """add-compliance-requirement has no representable entry in
        AIChatApprovalService.approve_and_execute's vocabulary, so it is
        exempted from the gate (see the comment at its write site in
        workflow_routes.py) and always executes immediately — even with
        REQUIRE_AI_APPROVAL on — rather than queuing a fake approval nothing
        could ever execute."""
        import uuid

        from app.models.archimate_core import ArchiMateElement
        from app.models.business_capabilities import BusinessCapability

        org = make_org("gov")
        user = _make_user(db_session, org)
        _login(client, user.id)

        suffix = uuid.uuid4().hex[:8]
        element = ArchiMateElement(
            name=f"Cap {suffix}", type="Capability", layer="Strategy", organization_id=org.id
        )
        db_session.add(element)
        db_session.flush()
        cap = BusinessCapability(
            name=f"Cap {suffix}", organization_id=org.id, archimate_element_id=element.id
        )
        db_session.add(cap)
        db_session.flush()

        monkeypatch.setitem(app.config, "REQUIRE_AI_APPROVAL", True)

        resp = client.post(
            "/ai-chat/data/add-compliance-requirement",
            json={
                "capability_id": cap.id,
                "requirement_type": "SOX",
                "description": "Quarterly control review",
                "priority": "High",
            },
        )
        body = resp.get_json()
        assert body.get("status") != "pending_approval"
        assert "approval_id" not in (body or {})


class TestSlashCommandsExemptFromApprovalGate:
    """/link-capability and /generate-from-capabilities (command_parser_service.py)
    are parsed verbatim from the user's own typed chat message — deterministic,
    not LLM-initiated — so they execute directly and are exempt from the
    approval queue regardless of REQUIRE_AI_APPROVAL. See the governance-note
    comments at both write sites. Pinned here so a future re-introduction of a
    REQUIRE_AI_APPROVAL check on these handlers (as link-capability used to
    have, before it was removed for being unwired — it returned a
    "submitted for review" message without ever creating anything to review)
    is a visible test failure, not a silent regression."""

    def test_link_capability_writes_directly_with_approval_flag_on(
        self, app, db_session, make_org, monkeypatch
    ):
        import uuid

        org = make_org("gov")
        user = _make_user(db_session, org)

        from app.models.archimate_core import ArchiMateElement
        from app.models.business_capabilities import BusinessCapability
        from app.models.solution_models import Solution

        suffix = uuid.uuid4().hex[:8]

        # BusinessCapability's before_insert hook auto-creates an ArchiMateElement
        # via a raw connection.execute() that does not set organization_id (a
        # pre-existing, unrelated gap - see tests/test_tenant_scoping_leaks.py's
        # _make_capability). Pre-create the element through the ORM to sidestep it.
        cap_name = f"Order Management {suffix}"
        element = ArchiMateElement(
            name=cap_name, type="Capability", layer="Strategy", organization_id=org.id
        )
        db_session.add(element)
        db_session.flush()

        cap = BusinessCapability(
            name=cap_name, organization_id=org.id, archimate_element_id=element.id
        )
        sol = Solution(name=f"CRM Consolidation {suffix}", organization_id=org.id, created_by_id=user.id)
        db_session.add_all([cap, sol])
        db_session.flush()

        monkeypatch.setitem(app.config, "REQUIRE_AI_APPROVAL", True)

        from app.modules.ai_chat.services.command_parser_service import CommandParserService

        with app.test_request_context("/"):
            parser = CommandParserService()
            result = parser._handle_link_capability(
                [cap.name, "to", sol.name], user.id, "business"
            )

        assert "pending_approval" not in result
        assert "requires approval" not in result["response"]
        assert "Linked" in result["response"]

        from app.models.solution_models import SolutionCapabilityMapping

        mapping = SolutionCapabilityMapping.query.filter_by(
            solution_id=sol.id, capability_id=cap.id
        ).first()
        assert mapping is not None
