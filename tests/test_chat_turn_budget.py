"""The chat waits a bounded time for a provider and says so plainly when none answers.

A provider double on loopback accepts the call and never replies. Each call gives up after the first-token
limit, the chain moves to the next provider, and the whole turn stops at the turn budget with the one busy
message. The limits are set once, in the AI defaults; these tests shorten them so the suite stays quick, and
one test pins the real numbers.
"""

from __future__ import annotations

import json
import socket
import threading
import time

import pytest

from app.modules.ai_chat.services import agent_runner as ar
from app.modules.ai_chat.services import model_defaults
from app.modules.ai_chat.services.agent_runner import AgentRunner
from app.modules.ai_chat.services.llm_service_impl import LLMService

BUSY = "The assistant is busy right now. Wait a minute, then press Retry."
ANSWER = "Three applications are recorded."


class _Peer:
    """A loopback provider: hangs forever, or answers one chat completion."""

    def __init__(self, answer=None):
        self.answer = answer
        self.calls = 0
        self._held = []
        self._sock = socket.socket()
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(16)
        self.url = "http://127.0.0.1:%d/v1" % self._sock.getsockname()[1]
        self._open = True
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while self._open:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            self.calls += 1
            self._held.append(conn)
            if self.answer is not None:
                threading.Thread(target=self._reply, args=(conn,), daemon=True).start()

    def _reply(self, conn):
        try:
            conn.settimeout(2)
            conn.recv(65536)
            body = json.dumps({
                "id": "x", "object": "chat.completion", "created": 1, "model": "m",
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": self.answer}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }).encode()
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n%s" % (len(body), body))
        except OSError:
            pass

    def close(self):
        self._open = False
        self._sock.close()
        for conn in self._held:
            try:
                conn.close()
            except OSError:
                pass


@pytest.fixture
def peers():
    made = []

    def make(answer=None):
        peer = _Peer(answer)
        made.append(peer)
        return peer

    yield make
    for peer in made:
        peer.close()


@pytest.fixture
def short_limits(monkeypatch):
    monkeypatch.setattr(model_defaults, "PROVIDER_FIRST_TOKEN_SECONDS", 1.0)
    monkeypatch.setattr(model_defaults, "CHAT_TURN_BUDGET_SECONDS", 3.0)
    monkeypatch.setattr(model_defaults, "CHAT_TURN_MARGIN_SECONDS", 0.0, raising=False)


def _runner(monkeypatch, peers_in_order):
    chain = [("openrouter", "m%d" % i, "test-key-%d" % i, p.url) for i, p in enumerate(peers_in_order)]
    monkeypatch.setattr(LLMService, "_get_configured_provider", staticmethod(lambda *a, **k: chain[0][:2]))
    monkeypatch.setattr(LLMService, "_get_all_api_keys", staticmethod(lambda p, **k: ["test-key-0"]))
    monkeypatch.setattr(ar, "build_provider_chain", lambda *a, **k: list(chain))
    runner = AgentRunner(user_id=1)
    monkeypatch.setattr(runner, "_gate_mode", lambda: "off")
    monkeypatch.setattr(runner, "_build_system_prompt", lambda *a, **k: "system")
    monkeypatch.setattr(runner, "_record_run", lambda *a, **k: None)
    monkeypatch.setattr(LLMService, "_guard_provider_call", staticmethod(lambda *a, **k: None))
    return runner


@pytest.mark.parametrize("stream_mode", [False, True], ids=["whole-reply", "streamed"])
def test_a_turn_with_providers_that_never_answer_ends_at_the_budget_with_the_busy_message(
    monkeypatch, peers, short_limits, stream_mode
):
    hanging = [peers() for _ in range(6)]
    runner = _runner(monkeypatch, hanging)
    started = time.monotonic()
    result = runner.run("How many applications do we run?", stream_mode=stream_mode)
    took = time.monotonic() - started
    assert result["response"] == BUSY
    assert result.get("error")
    assert took < 3.0 + 1.5, took
    # The budget, not the length of the chain, ended it: six providers at one second each would take six.
    assert sum(p.calls for p in hanging) < len(hanging)


def test_the_chain_moves_to_the_next_provider_after_the_first_token_limit(monkeypatch, peers, short_limits):
    slow, good = peers(), peers(answer=ANSWER)
    runner = _runner(monkeypatch, [slow, good])
    started = time.monotonic()
    result = runner.run("How many applications do we run?")
    took = time.monotonic() - started
    assert result["response"] == ANSWER
    assert not result.get("error")
    assert slow.calls == 1 and good.calls == 1
    assert 1.0 <= took < 3.0, took


def test_each_call_waits_for_the_first_token_no_longer_than_the_set_limit_and_the_turn_no_longer_than_the_budget():
    assert model_defaults.PROVIDER_FIRST_TOKEN_SECONDS == 25.0
    assert model_defaults.CHAT_TURN_BUDGET_SECONDS == 60.0
    runner = AgentRunner(user_id=1)
    assert runner._call_timeout() == 25.0
    runner._turn_deadline = time.monotonic() + 10
    assert 9.0 < runner._call_timeout() <= 10.0
    runner._turn_deadline = time.monotonic() + 40
    assert runner._call_timeout() == 25.0


def _chunk(text):
    body = {"id": "x", "object": "chat.completion.chunk", "created": 1, "model": "m",
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": text}, "finish_reason": None}]}
    return ("data: " + json.dumps(body) + "\n\n").encode()


class _StreamPeer(_Peer):
    """A loopback provider that opens an event stream and then misbehaves: comment lines only, one token and
    then comment lines, or one token and then silence."""

    def __init__(self, behaviour):
        self.behaviour = behaviour
        super().__init__(answer=None)

    def _serve(self):
        while self._open:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            self.calls += 1
            self._held.append(conn)
            threading.Thread(target=self._stream, args=(conn,), daemon=True).start()

    def _stream(self, conn):
        try:
            conn.settimeout(2)
            conn.recv(65536)
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\r\n")
            if self.behaviour != "comments":
                conn.sendall(_chunk("Partial "))
            if self.behaviour == "token_then_silence":
                return
            while self._open:
                conn.sendall(b": keep-alive\n\n")
                time.sleep(0.1)
        except OSError:
            pass


def _run_in_thread(runner, limit, **kwargs):
    """Run a streamed turn on a thread; return (result or None if it had not ended after `limit`, seconds)."""
    box = {}
    started = time.monotonic()
    worker = threading.Thread(target=lambda: box.update(result=runner.run("How many applications do we run?", **kwargs)), daemon=True)
    worker.start()
    worker.join(limit)
    return box.get("result"), time.monotonic() - started


@pytest.mark.parametrize("behaviour", ["comments", "token_then_comments", "token_then_silence"])
def test_a_provider_that_keeps_the_connection_alive_without_answering_cannot_hold_the_turn_past_the_budget(
    monkeypatch, short_limits, behaviour
):
    streams = [_StreamPeer(behaviour) for _ in range(3)]
    try:
        runner = _runner(monkeypatch, streams)
        result, took = _run_in_thread(runner, 8.0, stream_mode=True)
        assert result is not None, "the turn had not ended after %.1f seconds" % took
        assert result["response"] == BUSY
        assert took < 3.0 + 1.0, took
    finally:
        for peer in streams:
            peer.close()


def test_the_server_ends_its_turn_before_the_minute_so_the_reply_can_reach_the_screen():
    assert 0 < model_defaults.CHAT_TURN_MARGIN_SECONDS < model_defaults.CHAT_TURN_BUDGET_SECONDS / 2
    runner = AgentRunner(user_id=1)
    runner._build_system_prompt = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("stop"))
    started = time.monotonic()
    with pytest.raises(RuntimeError):
        runner.run("hello")
    allowed = runner._turn_deadline - started
    assert allowed <= model_defaults.CHAT_TURN_BUDGET_SECONDS - model_defaults.CHAT_TURN_MARGIN_SECONDS + 0.5, allowed


WRITE_TOOL = "create_goal"


def _write_script(monkeypatch, runner, steps):
    """The model's replies in order; each is a tool call to the write tool, an answer, or an exception."""
    writes = []

    class _Executor:
        def __init__(self, *a, **k):
            pass

        def execute(self, call):
            writes.append((call.name, dict(call.arguments)))
            return {"success": True, "result": {"id": len(writes)}, "message": "Created the application Billing."}

    monkeypatch.setattr("app.modules.ai_chat.tools.executor.ToolExecutor", _Executor)
    monkeypatch.setattr(ar, "_MUTATING_TOOLS", frozenset({WRITE_TOOL}))
    monkeypatch.setattr(AgentRunner, "_should_queue", staticmethod(lambda schema, auto: False))
    pending = list(steps)

    def fake(*a, **k):
        step = pending.pop(0)
        if isinstance(step, Exception):
            raise step
        if step == "write":
            return {"text": None, "raw": None,
                    "tool_calls": [{"id": "t1", "name": WRITE_TOOL, "arguments": {"name": "Billing"}}]}
        return {"text": step, "tool_calls": [], "raw": None}

    monkeypatch.setattr(runner, "_call_llm", fake)
    return writes


def test_a_turn_that_made_a_change_reports_it_whatever_ends_the_turn_and_a_retry_does_not_make_it_again(monkeypatch):
    chain = [("openrouter", "m0", "test-key-0", "http://127.0.0.1:9/v1")]
    monkeypatch.setattr(LLMService, "_get_configured_provider", staticmethod(lambda *a, **k: chain[0][:2]))
    monkeypatch.setattr(LLMService, "_get_all_api_keys", staticmethod(lambda p, **k: ["test-key-0"]))
    monkeypatch.setattr(ar, "build_provider_chain", lambda *a, **k: list(chain))
    runner = AgentRunner(user_id=1)
    monkeypatch.setattr(runner, "_gate_mode", lambda: "off")
    monkeypatch.setattr(runner, "_build_system_prompt", lambda *a, **k: "system")
    monkeypatch.setattr(runner, "_record_run", lambda *a, **k: None)
    writes = _write_script(monkeypatch, runner, ["write", TimeoutError("the turn's time budget is spent")])

    first = runner.run("Create an application called Billing.")
    assert first["response"] == BUSY
    assert len(writes) == 1
    assert [(a["tool"], a["arguments"]) for a in first["actions_taken"]] == [(WRITE_TOOL, {"name": "Billing"})]

    # Retry: the browser sends back what the first try already did.
    writes_after_first = len(writes)
    _write_script_again = ["write", "The application Billing is recorded."]
    monkeypatch.setattr(runner, "_call_llm", _replay(runner, _write_script_again))
    second = runner.run("Create an application called Billing.", already_done=first["actions_taken"])
    assert second["response"] == "The application Billing is recorded."
    assert len(writes) == writes_after_first == 1, writes


def _replay(runner, steps):
    pending = list(steps)

    def fake(*a, **k):
        step = pending.pop(0)
        if step == "write":
            return {"text": None, "raw": None,
                    "tool_calls": [{"id": "t2", "name": WRITE_TOOL, "arguments": {"name": "Billing"}}]}
        return {"text": step, "tool_calls": [], "raw": None}

    return fake


def test_two_organisations_each_keep_their_own_chain(app, two_orgs, monkeypatch):
    org_a, org_b = two_orgs
    monkeypatch.delenv("AI_PROVIDER_ORDER", raising=False)
    with app.app_context():
        chain_a = ar.build_provider_chain("deepseek", "deepseek-chat", organization_id=org_a)
        chain_b = ar.build_provider_chain("deepseek", "deepseek-chat", organization_id=org_b)
    keys_a = {entry[2] for entry in chain_a}
    keys_b = {entry[2] for entry in chain_b}
    assert keys_a and keys_b
    assert "sk-org-a-own-key-never-real-0003" in keys_a and "sk-org-a-own-key-never-real-0003" not in keys_b
    assert "sk-org-b-own-key-never-real-0002" in keys_b and "sk-org-b-own-key-never-real-0002" not in keys_a


from tests.test_llm_deepseek import production_shape, stub  # noqa: E402,F401
from tests.test_llm_org_keys import two_orgs  # noqa: E402,F401


def test_the_lookup_limit_is_read_only_from_what_the_server_gives_the_page():
    """The browser's lookup limit comes from the turn budget's one place: no script or template restates it."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    limit_ms = int((model_defaults.PROVIDER_FIRST_TOKEN_SECONDS + model_defaults.CHAT_TURN_MARGIN_SECONDS) * 1000)
    for name in ("app/static/js/ai_chat/transport.js", "app/templates/ai_chat/index.html"):
        source = (root / name).read_text()
        assert str(limit_ms) not in source, "%s restates the lookup limit %d" % (name, limit_ms)
    views = (root / "app/modules/ai_chat/routes/chat_views.py").read_text()
    assert "PROVIDER_FIRST_TOKEN_SECONDS + model_defaults.CHAT_TURN_MARGIN_SECONDS" in views
