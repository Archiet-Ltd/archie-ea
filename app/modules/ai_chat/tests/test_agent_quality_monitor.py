"""
Tests for the answer quality monitor: citation rate, citation-gate refusal
rate per thousand, and refusal rate per persona, each for one organisation
and period against the prior period.

Every figure is computed from recorded rows and scoped to the caller's
organisation. A period with nothing to measure returns None, never a
literal and never a "total or 1" denominator.
"""
from datetime import datetime, timedelta
import json
import uuid

import pytest

from app.models.answer_record import AnswerRecord
from app.models.agent_run_record import AgentRunRecord
from app.models.ai_chat_audit_log import AIChatAuditLog, AuditEventType
from app.models.organization import Organization
from app.models.user import User
from app.modules.ai_chat.services.agent_quality_monitor import (
    get_citation_rate,
    get_refusal_rate,
    REFUSED,
    WOULD_REFUSE,
    get_refusal_rate_per_persona,
    get_refusal_run_ids_by_persona,
)
from app.modules.ai_chat.services.ai_hallucination_detector import Grounding
from app.modules.ai_chat.services.agent_runner import AgentRunner

PERIOD_START = datetime(2026, 1, 1)
PERIOD_END = datetime(2026, 1, 8)


def _make_org(db_session, label):
    suffix = uuid.uuid4().hex[:8]
    org = Organization(name=f"Organisation {label} {suffix}", slug=f"org-{label}-{suffix}")
    db_session.add(org)
    db_session.flush()
    return org


def _make_user(db_session, org, label, user_id, admin=True):
    user = User(
        email=f"user-{label}-{user_id}@{org.slug}.example.com",
        first_name="Quality",
        last_name="Monitor",
        organization_id=org.id,
        is_org_admin=admin,
        confirmed=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _make_answer(db_session, org, user, address, cited, computed_at):
    answer = AnswerRecord(
        organization_id=org.id,
        address=address,
        question_kind="kind",
        computed_at=computed_at,
        created_by_id=user.id,
        citations=[{"url": "http://example.com"}] if cited else [],
    )
    db_session.add(answer)
    db_session.flush()
    return answer


def _make_run(db_session, org, user, persona, created_at):
    run = AgentRunRecord(
        organization_id=org.id,
        user_id=user.id,
        persona=persona,
        created_at=created_at,
    )
    db_session.add(run)
    db_session.flush()
    return run


def _make_refusal_log(db_session, user, validation_status, created_at, persona="doctor"):
    log = AIChatAuditLog(
        event_type=AuditEventType.ERROR_OCCURRED,
        user_id=user.id,
        validation_status=validation_status,
        validation_details=json.dumps(["unsupported claim"]),
        created_at=created_at,
        persona=persona,
    )
    db_session.add(log)
    db_session.flush()
    return log


def test_empty_organisation_shows_none_not_zero(db_session):
    """An organisation with nothing to measure shows None, not 0."""
    org = _make_org(db_session, "empty")
    _make_user(db_session, org, "empty", 9)

    rate, ids = get_citation_rate(org.id, PERIOD_START, PERIOD_END)
    assert rate is None
    assert ids == []

    rate, ids = get_refusal_rate(org.id, PERIOD_START, PERIOD_END, REFUSED)
    assert rate is None
    assert ids == []

    rates, _ = get_refusal_rate_per_persona(org.id, PERIOD_START, PERIOD_END, REFUSED)
    assert rates == {}


def test_citation_rate_is_cited_over_answers_for_one_organisation(db_session):
    org_a = _make_org(db_session, "a")
    org_b = _make_org(db_session, "b")
    user_a = _make_user(db_session, org_a, "a", 1)
    user_b = _make_user(db_session, org_b, "b", 2)

    _make_answer(db_session, org_a, user_a, "addr-a-cited", True, PERIOD_START)
    uncited_a_1 = _make_answer(db_session, org_a, user_a, "addr-a-uncited-1", False, PERIOD_START + timedelta(hours=1))
    uncited_a_2 = _make_answer(db_session, org_a, user_a, "addr-a-uncited-2", False, PERIOD_START + timedelta(hours=2))
    _make_answer(db_session, org_b, user_b, "addr-b-cited", True, PERIOD_START)

    rate, ids = get_citation_rate(org_a.id, PERIOD_START, PERIOD_END)
    assert rate == pytest.approx(1 / 3)
    assert set(ids) == {uncited_a_1.id, uncited_a_2.id}

    # Organisation B sees only its own answers: one cited answer, no ids.
    rate_b, ids_b = get_citation_rate(org_b.id, PERIOD_START, PERIOD_END)
    assert rate_b == 1.0
    assert ids_b == []


def test_citation_rate_uncited_ids_match_the_uncited_answers(db_session):
    org = _make_org(db_session, "cited-ids")
    user = _make_user(db_session, org, "cited-ids", 3)

    _make_answer(db_session, org, user, "addr-cited", True, PERIOD_START)
    uncited_1 = _make_answer(db_session, org, user, "addr-uncited-1", False, PERIOD_START + timedelta(hours=1))
    uncited_2 = _make_answer(db_session, org, user, "addr-uncited-2", False, PERIOD_START + timedelta(hours=2))

    rate, ids = get_citation_rate(org.id, PERIOD_START, PERIOD_END)
    assert rate == pytest.approx(1 / 3)
    assert set(ids) == {uncited_1.id, uncited_2.id}


def test_refusal_rate_is_per_thousand_for_one_organisation(db_session):
    org_a = _make_org(db_session, "refusal-a")
    org_b = _make_org(db_session, "refusal-b")
    user_a = _make_user(db_session, org_a, "refusal-a", 4)
    user_b = _make_user(db_session, org_b, "refusal-b", 5)

    now = datetime.utcnow()
    run_1 = _make_run(db_session, org_a, user_a, "doctor", now)
    run_2 = _make_run(db_session, org_a, user_a, "doctor", now + timedelta(minutes=1))
    _make_run(db_session, org_b, user_b, "doctor", now)

    _make_refusal_log(db_session, user_a, "would_refuse_uncited", now, "doctor")
    _make_refusal_log(db_session, user_a, "refused_uncited", now + timedelta(minutes=1), "doctor")

    rate, ids = get_refusal_rate(
        org_a.id, now - timedelta(hours=1), now + timedelta(hours=1), REFUSED
    )
    assert rate == 500.0
    assert ids == [run_2.id]

    # Would-have-refused is its own figure, never summed into "refused".
    rate_w, ids_w = get_refusal_rate(
        org_a.id, now - timedelta(hours=1), now + timedelta(hours=1), WOULD_REFUSE
    )
    assert rate_w == 500.0
    assert ids_w == [run_1.id]

    # Organisation B has one run and no refusals: zero, not a dash.
    rate_b, ids_b = get_refusal_rate(
        org_b.id, now - timedelta(hours=1), now + timedelta(hours=1), REFUSED
    )
    assert rate_b == 0.0
    assert ids_b == []


def test_refusal_rate_per_persona(db_session):
    org = _make_org(db_session, "persona")
    user = _make_user(db_session, org, "persona", 6)

    now = datetime.utcnow()
    _make_run(db_session, org, user, "doctor", now)
    _make_run(db_session, org, user, "doctor", now + timedelta(minutes=1))
    _make_run(db_session, org, user, "engineer", now + timedelta(minutes=2))

    _make_refusal_log(db_session, user, "would_refuse_uncited", now, "doctor")
    _make_refusal_log(db_session, user, "refused_uncited", now + timedelta(minutes=1), "doctor")

    rates, _ = get_refusal_rate_per_persona(
        org.id, now - timedelta(hours=1), now + timedelta(hours=1), REFUSED
    )
    assert rates["doctor"] == 500.0
    assert rates["engineer"] == 0.0


def test_refusal_rate_per_persona_ids_match_the_refusal_runs(db_session):
    org = _make_org(db_session, "persona-ids")
    user = _make_user(db_session, org, "persona-ids", 7)

    now = datetime.utcnow()
    _make_run(db_session, org, user, "doctor", now)
    run_2 = _make_run(db_session, org, user, "doctor", now + timedelta(minutes=1))

    _make_refusal_log(db_session, user, "would_refuse_uncited", now, "doctor")
    _make_refusal_log(db_session, user, "refused_uncited", now + timedelta(minutes=1), "doctor")

    rates, ids = get_refusal_rate_per_persona(
        org.id, now - timedelta(hours=1), now + timedelta(hours=1), REFUSED
    )
    assert rates["doctor"] == 500.0
    assert ids == [run_2.id]


def test_prior_period_is_the_same_length_immediately_before(db_session):
    org = _make_org(db_session, "prior")
    user = _make_user(db_session, org, "prior", 8)

    prior_start = datetime(2026, 1, 1)
    prior_end = datetime(2026, 1, 2)
    start = prior_end
    end = start + timedelta(days=1)

    _make_answer(db_session, org, user, "addr-prior", False, prior_start)
    _make_run(db_session, org, user, "doctor", prior_start)
    _make_refusal_log(db_session, user, "would_refuse_uncited", prior_start, "doctor")

    # The current period holds nothing: every figure is None.
    rate, ids = get_citation_rate(org.id, start, end)
    assert rate is None
    assert ids == []

    rate, ids = get_refusal_rate(org.id, start, end, WOULD_REFUSE)
    assert rate is None
    assert ids == []

    rates, _ = get_refusal_rate_per_persona(org.id, start, end, WOULD_REFUSE)
    assert rates.get("doctor") is None

    # The prior period measures the same length of time immediately before.
    prior_rate, prior_ids = get_citation_rate(org.id, prior_start, prior_end)
    assert prior_rate == 0.0
    assert len(prior_ids) == 1

    prior_refusal, _ = get_refusal_rate(org.id, prior_start, prior_end, WOULD_REFUSE)
    assert prior_refusal == 1000.0

    prior_rates, _ = get_refusal_rate_per_persona(org.id, prior_start, prior_end, WOULD_REFUSE)
    assert prior_rates.get("doctor") == 1000.0


def test_gate_refusal_is_persisted_and_counted_for_its_organisation(db_session, app):
    """AC1: a refused reply leaves an audit row the figure counts; on main
    no row exists so this test fails there."""
    org_a = _make_org(db_session, "gate-a")
    org_b = _make_org(db_session, "gate-b")
    user_a = _make_user(db_session, org_a, "gate-a", 101)
    _make_user(db_session, org_b, "gate-b", 102)

    now = datetime.utcnow()
    run_a = _make_run(db_session, org_a, user_a, "doctor", now)

    grounding = Grounding()
    grounding.add_tool_result(
        {
            "success": True,
            "result": {
                "rows": [{"element_id": 7}],
                "elements": {"7": {"id": 7, "name": "Ledger"}},
                "address": "x" * 32,
            },
        }
    )

    runner = AgentRunner(user_id=user_a.id, chat_session_id="thread-1")
    runner._grounding = grounding
    runner._estate_question = "impact"
    runner._gate_mode_now = "enforce"

    with app.test_request_context("/"):
        runner._apply_citation_gate(
            {"response": "Ledger is impacted. Exadata fails nightly.", "sources": []},
            domain="architecture",
            persona="doctor",
        )

    row = (
        AIChatAuditLog.query.filter_by(user_id=user_a.id, validation_status="refused_uncited")
        .order_by(AIChatAuditLog.id.desc())
        .first()
    )
    assert row is not None
    assert "ledger" in row.validation_details.lower()

    rate, ids = get_refusal_rate(
        org_a.id, now - timedelta(hours=1), now + timedelta(hours=1), REFUSED
    )
    assert rate == 1000.0
    assert run_a.id in ids

    # Organisation B sees none of it.
    rate_b, ids_b = get_refusal_rate(
        org_b.id, now - timedelta(hours=1), now + timedelta(hours=1), REFUSED
    )
    assert rate_b is None
    assert ids_b == []


def test_gate_would_refusal_is_persisted_and_counted(db_session, app):
    """A would-refusal in log mode is counted the same way."""
    org = _make_org(db_session, "would-refuse")
    user = _make_user(db_session, org, "would-refuse", 103)

    now = datetime.utcnow()
    _make_run(db_session, org, user, "engineer", now)

    grounding = Grounding()
    grounding.add_tool_result(
        {
            "success": True,
            "result": {
                "rows": [{"element_id": 7}],
                "elements": {"7": {"id": 7, "name": "Ledger"}},
                "address": "x" * 32,
            },
        }
    )

    runner = AgentRunner(user_id=user.id, chat_session_id="thread-2")
    runner._grounding = grounding
    runner._estate_question = "impact"
    runner._gate_mode_now = "log"

    with app.test_request_context("/"):
        runner._apply_citation_gate(
            {"response": "Ledger is impacted. Core Banking fails nightly.", "sources": []},
            domain="architecture",
            persona="engineer",
        )

    row = (
        AIChatAuditLog.query.filter_by(user_id=user.id, validation_status="would_refuse_uncited")
        .order_by(AIChatAuditLog.id.desc())
        .first()
    )
    assert row is not None
    assert "ledger" in row.validation_details.lower()

    rate, _ = get_refusal_rate(
        org.id, now - timedelta(hours=1), now + timedelta(hours=1), WOULD_REFUSE
    )
    assert rate == 1000.0


# ---------------------------------------------------------------------------
# The page: role guard, organisation fence, dash for nothing to measure
# ---------------------------------------------------------------------------

def _seed_org_with_figures(db_session, label, user_id, now):
    org = _make_org(db_session, label)
    user = _make_user(db_session, org, label, user_id)
    run = _make_run(db_session, org, user, f"persona-{label}", now)
    _make_answer(db_session, org, user, f"addr-uncited-{label}", False, now)
    _make_answer(db_session, org, user, f"addr-cited-{label}", True, now)
    _make_refusal_log(db_session, user, "refused_uncited", now, persona=f"persona-{label}")
    db_session.commit()
    return org, user, run


def test_page_shows_own_figures_and_never_another_organisations(client, db_session, login_as):
    now = datetime.utcnow()
    org_a, user_a, run_a = _seed_org_with_figures(db_session, "pa", 201, now)
    org_b, user_b, run_b = _seed_org_with_figures(db_session, "pb", 202, now)

    login_as(client, user_a)
    resp = client.get("/ai-chat/quality-monitor")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "50.0%" in html
    assert "addr-uncited-pa" in html
    assert "persona-pa" in html
    assert f"ids={run_a.id}" in html
    # Organisation B's persona, answer address and run id never appear.
    assert "persona-pb" not in html
    assert "addr-uncited-pb" not in html
    assert f"ids={run_b.id}" not in html


def test_page_shows_a_dash_when_there_is_nothing_to_measure(client, db_session, login_as):
    org = _make_org(db_session, "dash")
    user = _make_user(db_session, org, "dash", 203)
    db_session.commit()

    assert get_citation_rate(org.id, PERIOD_START, PERIOD_END)[0] is None

    login_as(client, user)
    resp = client.get("/ai-chat/quality-monitor")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'data-figure="citation-rate"><span class="text-muted-foreground">' in html
    assert 'data-figure="refused-rate"><span class="text-muted-foreground">' in html
    assert "0.0%" not in html


def test_page_requires_org_admin_like_the_run_record_list(client, db_session, login_as):
    org = _make_org(db_session, "guard")
    user = _make_user(db_session, org, "guard", 204, admin=False)
    db_session.commit()

    login_as(client, user)
    assert client.get("/ai-chat/run-records").status_code == 403
    assert client.get("/ai-chat/quality-monitor").status_code == 403


def test_run_record_list_drill_through_filters_by_ids(client, db_session, login_as):
    now = datetime.utcnow()
    org, user, run_1 = _seed_org_with_figures(db_session, "dt", 205, now)
    run_2 = _make_run(db_session, org, user, "other", now)
    db_session.commit()

    login_as(client, user)
    html = client.get(f"/ai-chat/run-records?ids={run_1.id}").get_data(as_text=True)
    assert f"/run-records/{run_1.id}" in html
    assert f"/run-records/{run_2.id}" not in html


def test_a_user_who_moved_organisation_cannot_pull_a_run_from_the_old_one(db_session):
    org_old = _make_org(db_session, "old")
    org_new = _make_org(db_session, "new")
    user = _make_user(db_session, org_old, "mover", 301)

    now = datetime.utcnow()
    run_old = _make_run(db_session, org_old, user, "doctor", now)
    _make_refusal_log(db_session, user, REFUSED, now, "doctor")
    user.organization_id = org_new.id
    run_new = _make_run(db_session, org_new, user, "doctor", now + timedelta(minutes=4))
    db_session.flush()

    window = (now - timedelta(hours=1), now + timedelta(hours=1))
    _, ids = get_refusal_rate(org_new.id, *window, REFUSED)
    assert ids == [run_new.id]
    assert run_old.id not in ids
    assert get_refusal_run_ids_by_persona(org_new.id, *window, REFUSED) == {"doctor": [run_new.id]}


def test_a_row_on_the_boundary_is_counted_in_one_period_only(db_session):
    org = _make_org(db_session, "boundary")
    user = _make_user(db_session, org, "boundary", 302)

    boundary = datetime(2026, 3, 1)
    prior_start = boundary - timedelta(days=1)
    end = boundary + timedelta(days=1)
    _make_answer(db_session, org, user, "addr-boundary", False, boundary)
    _make_run(db_session, org, user, "doctor", boundary)
    _make_refusal_log(db_session, user, REFUSED, boundary, "doctor")

    # The row belongs to the period that starts on it ...
    assert get_citation_rate(org.id, boundary, end)[0] == 0.0
    assert get_refusal_rate(org.id, boundary, end, REFUSED)[0] == 1000.0
    # ... and not to the prior period that ends on it.
    assert get_citation_rate(org.id, prior_start, boundary)[0] is None
    assert get_refusal_rate(org.id, prior_start, boundary, REFUSED)[0] is None


def test_unknown_refusal_status_is_rejected(db_session):
    with pytest.raises(ValueError):
        get_refusal_rate(1, PERIOD_START, PERIOD_END, "refused")


def _gate_runner(user, mode):
    grounding = Grounding()
    grounding.add_tool_result(
        {
            "success": True,
            "result": {
                "rows": [{"element_id": 7}],
                "elements": {"7": {"id": 7, "name": "Ledger"}},
                "address": "x" * 32,
            },
        }
    )
    runner = AgentRunner(user_id=user.id, chat_session_id="thread-x")
    runner._grounding = grounding
    runner._estate_question = "impact"
    runner._gate_mode_now = mode
    return runner


def test_gate_decision_is_not_counted_when_the_write_is_removed(db_session, app, monkeypatch):
    """The proof that the figure depends on the persisted row: with the audit
    write neutralised, the same refused reply leaves no row and the figure
    stays at zero instead of 1000."""
    from app.utils.ai_chat_audit import AIChatAuditLogger

    org = _make_org(db_session, "noop")
    user = _make_user(db_session, org, "noop", 303)
    now = datetime.utcnow()
    _make_run(db_session, org, user, "doctor", now)
    monkeypatch.setattr(AIChatAuditLogger, "log_citation_gate_decision", lambda self, **kw: None)

    with app.test_request_context("/"):
        _gate_runner(user, "enforce")._apply_citation_gate(
            {"response": "Ledger is impacted. Exadata fails nightly.", "sources": []},
            domain="architecture",
            persona="doctor",
        )

    assert AIChatAuditLog.query.filter_by(user_id=user.id, validation_status=REFUSED).first() is None
    rate, ids = get_refusal_rate(org.id, now - timedelta(hours=1), now + timedelta(hours=1), REFUSED)
    assert rate == 0.0
    assert ids == []


def test_audit_write_failure_does_not_break_the_reply(db_session, app, monkeypatch):
    import app.utils.ai_chat_audit as audit_module

    org = _make_org(db_session, "boom")
    user = _make_user(db_session, org, "boom", 304)

    def boom(*args, **kwargs):
        raise RuntimeError("audit store down")

    monkeypatch.setattr(audit_module, "get_audit_logger", boom)
    result = {"response": "Ledger is impacted. Exadata fails nightly.", "sources": []}
    with app.test_request_context("/"):
        _gate_runner(user, "enforce")._apply_citation_gate(
            result, domain="architecture", persona="doctor"
        )
    assert result["response"] == AgentRunner.CITATION_REFUSAL


def test_page_shows_refused_and_would_have_refused_separately_with_notes(client, db_session, login_as):
    org = _make_org(db_session, "split")
    user = _make_user(db_session, org, "split", 305)
    now = datetime.utcnow() - timedelta(hours=1)
    _make_run(db_session, org, user, "doctor", now)
    _make_run(db_session, org, user, "doctor", now + timedelta(minutes=1))
    _make_refusal_log(db_session, user, REFUSED, now, "doctor")
    db_session.commit()

    login_as(client, user)
    html = client.get("/ai-chat/quality-monitor").get_data(as_text=True)
    assert 'data-figure="refused-rate">500.0' in html
    assert 'data-figure="would-refuse-rate">0.0' in html
    assert "Would have refused" in html
    assert "not per reply" in html
    assert "within five minutes" in html
    assert "current organisation" in html


def test_run_record_list_ignores_hostile_ids(client, db_session, login_as):
    now = datetime.utcnow()
    org, user, run = _seed_org_with_figures(db_session, "hostile", 306, now)
    login_as(client, user)
    for value in ("\u00b2", "9" * 40, "abc,;--", "1 or 1=1", "%00"):
        resp = client.get("/ai-chat/run-records", query_string={"ids": value})
        assert resp.status_code == 200, value
        assert f"/run-records/{run.id}" not in resp.get_data(as_text=True), value
