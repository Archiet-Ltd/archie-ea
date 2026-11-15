"""Retry reads back only what the turn that failed recorded, never an older turn's or another chat's change.

Each case builds the real rows through the real approval rule (nothing patched off) and presses Retry on a
turn that failed; what the Retry reports is what the failure card names.
"""

from __future__ import annotations

from tests.test_chat_retry_record import (BILLING, PAYROLL, STOP, TOOL, _model_says, _person, _rows, _runner,
                                          _tool_call)


def _names(result):
    return [p["arguments"]["name"] for p in result.get("pending_approvals") or []]


def test_a_retry_in_another_conversation_does_not_name_a_change_an_earlier_chat_recorded(
        app, db_session, make_org, tenant_ctx, monkeypatch):
    org, user = _person(db_session, make_org, "otherchat")
    with tenant_ctx(org.id):
        old = _runner(monkeypatch, user, "chat_user_%d" % user.id)
        _model_says(monkeypatch, old, [_tool_call(1, BILLING), "Queued."])
        old.run("Add the goal Billing.")
        failed = _runner(monkeypatch, user, "thread-today")
        _model_says(monkeypatch, failed, [STOP])
        stopped = failed.run("Which applications depend on SAP?")
        assert stopped["pending_approvals"] == []
        retried = _runner(monkeypatch, user, "thread-today")
        _model_says(monkeypatch, retried, [STOP])
        again = retried.run("Which applications depend on SAP?", retry=True, failed_turn_id=stopped["turn_id"])
        assert again["pending_approvals"] == [], again["pending_approvals"]
        assert _rows(org.id) == [(TOOL, "pending", "Billing")]


def test_a_retry_in_the_same_conversation_does_not_name_an_earlier_turns_change(
        app, db_session, make_org, tenant_ctx, monkeypatch):
    org, user = _person(db_session, make_org, "earlierturn")
    with tenant_ctx(org.id):
        first = _runner(monkeypatch, user, "thread-s")
        _model_says(monkeypatch, first, [_tool_call(1, PAYROLL), "Queued for approval."])
        first.run("Add the goal Payroll.")
        second = _runner(monkeypatch, user, "thread-s")
        _model_says(monkeypatch, second, [STOP])
        stopped = second.run("What is our biggest risk?")
        retried = _runner(monkeypatch, user, "thread-s")
        _model_says(monkeypatch, retried, [STOP])
        again = retried.run("What is our biggest risk?", retry=True, failed_turn_id=stopped["turn_id"])
        assert again["pending_approvals"] == [], again["pending_approvals"]


def test_a_retry_still_names_the_changes_the_failed_turn_itself_recorded(
        app, db_session, make_org, tenant_ctx, monkeypatch):
    org, user = _person(db_session, make_org, "ownturn")
    with tenant_ctx(org.id):
        older = _runner(monkeypatch, user, "thread-o")
        _model_says(monkeypatch, older, [_tool_call(1, {"solution_id": 1, "name": "Archive"}), "Queued."])
        older.run("Add the goal Archive.")
        failed = _runner(monkeypatch, user, "thread-o")
        _model_says(monkeypatch, failed, [_tool_call(2, BILLING), _tool_call(3, PAYROLL), STOP])
        stopped = failed.run("Add the goals Billing and Payroll.")
        retried = _runner(monkeypatch, user, "thread-o")
        _model_says(monkeypatch, retried, [STOP])
        again = retried.run("Add the goals Billing and Payroll.", retry=True, failed_turn_id=stopped["turn_id"])
        assert _names(again) == ["Billing", "Payroll"]
        assert again["pending_approvals"][0]["plain"] == "Create a goal: Billing"


def test_another_organisations_person_naming_this_turn_reads_nothing(
        app, db_session, make_org, tenant_ctx, monkeypatch):
    org_a, user_a = _person(db_session, make_org, "turnowner")
    org_b, user_b = _person(db_session, make_org, "turnthief")
    with tenant_ctx(org_a.id):
        first = _runner(monkeypatch, user_a, "thread-x")
        _model_says(monkeypatch, first, [_tool_call(1, BILLING), STOP])
        stopped = first.run("Add the goal Billing.")
        assert _names(stopped) == ["Billing"]
    with tenant_ctx(org_b.id):
        runner = _runner(monkeypatch, user_b, "thread-x")
        _model_says(monkeypatch, runner, [STOP])
        again = runner.run("Add the goal Billing.", retry=True, failed_turn_id=stopped["turn_id"])
        assert again["pending_approvals"] == []
