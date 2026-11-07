"""GitHub and Jira connections are read again on their own, each for its own organisation.

The recorded GitHub organisation of the connector suite stands in for GitHub; nothing touches the
network. Two real organisations hold a connection each.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tests.test_entity_history_trigger import _entity_history_trigger  # noqa: F401
from tests.test_event_log import _install_guards  # noqa: F401
from tests.test_github_connector import (  # noqa: F401
    _sync_log,
    connect,
    github,
    org_a,
    org_b,
)

pytestmark = pytest.mark.usefixtures("db_session")


def _delegate_sync(org, on=True):
    from app.extensions import db

    settings = dict(org.settings or {})
    settings["connector_action_delegation"] = {"sync": on}
    org.settings = settings
    db.session.flush()


@pytest.fixture(autouse=True)
def _both_organisations_delegate_sync(org_a, org_b):
    """The reads below run on delegation; the organisations that have not delegated have their own tests."""
    _delegate_sync(org_a)
    _delegate_sync(org_b)


def _logs(cfg):
    from app.models.connector_config import SyncLog

    return SyncLog.query.filter_by(connector_id=cfg.id).order_by(SyncLog.started_at).all()


def _reread(tenant_ctx, org, now=None):
    from app.extensions import db
    from app.services.connector_schedule import reread_due_connectors

    with tenant_ctx(org.id):
        outcome = reread_due_connectors(org.id, now=now)
    db.session.flush()
    return outcome


def test_each_organisation_is_read_for_itself_only(db_session, github, connect, tenant_ctx, org_a, org_b):
    cfg_a, cfg_b = connect(org_a), connect(org_b)

    outcome = _reread(tenant_ctx, org_a)

    assert outcome["read"] == [cfg_a.id]
    assert [log.sync_type for log in _logs(cfg_a)] == ["scheduled"]
    assert _logs(cfg_b) == []  # the other organisation's connection was not touched
    assert _reread(tenant_ctx, org_b)["read"] == [cfg_b.id]
    assert len(_logs(cfg_a)) == 1


def test_a_connection_still_being_read_is_skipped(db_session, github, connect, tenant_ctx, org_a):
    from app.models.connector_config import SyncLog

    cfg = connect(org_a)
    db_session.add(SyncLog(connector_id=cfg.id, sync_type="scheduled", status="running", started_at=datetime.utcnow()))
    db_session.flush()

    outcome = _reread(tenant_ctx, org_a)

    assert outcome["running"] == [cfg.id] and outcome["read"] == []
    assert len(_logs(cfg)) == 1


def test_a_read_that_began_long_ago_and_never_finished_does_not_block_forever(
    db_session, github, connect, tenant_ctx, org_a
):
    from app.models.connector_config import SyncLog

    cfg = connect(org_a)
    long_ago = datetime.utcnow() - timedelta(hours=3)
    db_session.add(SyncLog(connector_id=cfg.id, sync_type="scheduled", status="running", started_at=long_ago))
    db_session.flush()

    assert _reread(tenant_ctx, org_a)["read"] == [cfg.id]


def test_a_connection_is_read_once_an_hour_and_unchanged_source_writes_nothing(
    db_session, github, connect, tenant_ctx, org_a
):
    cfg = connect(org_a)
    start = datetime.utcnow()
    assert _reread(tenant_ctx, org_a, now=start)["read"] == [cfg.id]

    soon = _reread(tenant_ctx, org_a, now=start + timedelta(minutes=30))
    assert soon["waiting"] == [cfg.id] and len(_logs(cfg)) == 1

    later = _reread(tenant_ctx, org_a, now=start + timedelta(hours=1, minutes=1))
    assert later["read"] == [cfg.id]
    second = _logs(cfg)[-1]
    assert (second.records_created, second.records_updated) == (0, 0)


def test_failures_back_off_and_a_success_returns_to_the_hour(db_session, github, connect, tenant_ctx, org_a):
    from app.services.connector_schedule import next_read_at

    cfg = connect(org_a)
    github.overrides["orgs/acme/repos"] = (500, {"message": "down"}, {})
    start = datetime.utcnow()
    assert _reread(tenant_ctx, org_a, now=start)["failed"] == [cfg.id]
    first = _logs(cfg)[-1]
    assert first.status == "error"
    assert next_read_at(cfg) - first.started_at == timedelta(hours=2)

    # one hour on it is still waiting; at two hours it is read, fails again and waits four
    assert _reread(tenant_ctx, org_a, now=first.started_at + timedelta(hours=1, minutes=1))["waiting"] == [cfg.id]
    assert _reread(tenant_ctx, org_a, now=first.started_at + timedelta(hours=2, minutes=1))["failed"] == [cfg.id]
    assert next_read_at(cfg) - _logs(cfg)[-1].started_at == timedelta(hours=4)

    github.overrides.clear()
    ok_at = _logs(cfg)[-1].started_at + timedelta(hours=4, minutes=1)
    assert _reread(tenant_ctx, org_a, now=ok_at)["read"] == [cfg.id]
    assert next_read_at(cfg) - _logs(cfg)[-1].started_at == timedelta(hours=1)


def test_the_connection_page_view_says_when_it_read_what_changed_and_when_next(
    db_session, github, connect, tenant_ctx, org_a
):
    from app.services.connector_framework import _connector_view

    cfg = connect(org_a)
    _reread(tenant_ctx, org_a)
    with tenant_ctx(org_a.id):
        view = _connector_view(cfg, org_a.id)["read_schedule"]

    assert view["last_read_at"] is not None
    assert view["what_changed"].startswith("Read 12 repositories")
    assert view["next_read_at"] - view["last_read_at"] == timedelta(hours=1)


def test_the_job_is_registered_and_declared_per_organisation():
    import inspect

    from app._bootstrap import extensions
    from app.jobs.tenant_safe_job import TENANT_JOBS

    assert "connector_reread" in TENANT_JOBS
    source = inspect.getsource(extensions.init_scheduler)
    assert 'id="connector_reread"' in source and "max_instances=1" in source


def _pending_syncs(org_id, cfg):
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus

    rows = AIChatCRUDApproval.query.filter_by(
        organization_id=org_id, entity_type="connectors", operation_type="sync", status=ApprovalStatus.PENDING
    ).all()
    return [row for row in rows if cfg.id in row.operation_payload]


def test_an_organisation_that_has_not_delegated_gets_one_proposal_and_no_read(
    db_session, github, connect, tenant_ctx, org_a, org_b
):
    _delegate_sync(org_a, on=False)
    cfg_a, cfg_b = connect(org_a), connect(org_b)  # the other organisation delegates

    first = _reread(tenant_ctx, org_a)
    second = _reread(tenant_ctx, org_a)

    assert first["proposed"] == [cfg_a.id] and first["read"] == []
    assert second["proposed"] == [] and second["waiting"] == [cfg_a.id]
    assert _logs(cfg_a) == []  # nothing was read from GitHub
    assert len(_pending_syncs(org_a.id, cfg_a)) == 1
    # the other organisation is read as before and gets no proposal
    assert _reread(tenant_ctx, org_b)["read"] == [cfg_b.id]
    assert _pending_syncs(org_b.id, cfg_b) == []


def test_a_second_read_of_one_connector_is_refused_while_the_first_holds_it(
    db_session, github, connect, tenant_ctx, org_a
):
    from app.jobs.tenant_safe_job import job_lock
    from app.services.connector_framework import run_connector_sync

    cfg = connect(org_a)
    with job_lock(f"connector_read:{cfg.id}", required=False) as held:  # its own connection, not this session
        assert held
        with tenant_ctx(org_a.id):
            refused = run_connector_sync(org_id=org_a.id, connector_id=cfg.id, delegated=True)
        outcome = _reread(tenant_ctx, org_a)

    assert refused["busy"] and not refused["success"]
    assert outcome["running"] == [cfg.id] and outcome["read"] == [] and outcome["failed"] == []
    assert _logs(cfg) == []  # neither attempt read or recorded anything
    assert _reread(tenant_ctx, org_a)["read"] == [cfg.id]  # once released it is read


def test_the_page_states_a_schedule_only_where_one_will_happen(db_session, github, connect, tenant_ctx, org_a):
    from app.services.connector_framework import _connector_view

    cfg = connect(org_a)

    def view():
        with tenant_ctx(org_a.id):
            return _connector_view(cfg, org_a.id)["read_schedule"]

    assert view()["read_automatically"] and not view()["waits_for_approval"]
    _delegate_sync(org_a, on=False)
    assert view()["read_automatically"] and view()["waits_for_approval"]
    _delegate_sync(org_a)
    for status in ("inactive", "maintenance"):
        cfg.status = status
        db_session.flush()
        assert not view()["read_automatically"], status
    cfg.status = "error"
    db_session.flush()
    assert view()["read_automatically"]


def test_a_scheduled_read_records_its_writes_as_the_connector(db_session, github, connect, tenant_ctx, org_a, monkeypatch):
    from app.services import connector_framework
    from app.services.change_source import declared_source

    cfg = connect(org_a)
    seen = []
    real = connector_framework._run_connector_sync_locked

    def watch(**kwargs):
        seen.append(declared_source())
        return real(**kwargs)

    monkeypatch.setattr(connector_framework, "_run_connector_sync_locked", watch)
    assert _reread(tenant_ctx, org_a)["read"] == [cfg.id]
    assert [source for source, _user in seen] == ["connector"]
    # proposing a read writes nothing to the model, so it declares no source of its own
    assert not hasattr(connector_framework.propose_connector_sync, "records_changes_as")


def _reject_pending_syncs(tenant_ctx, org_id, cfg):
    from app.models.ai_chat_crud_approval import ApprovalStatus
    from app.extensions import db

    with tenant_ctx(org_id):
        rows = _pending_syncs(org_id, cfg)
    assert rows, "there is a proposal to decline"
    for row in rows:
        row.status = ApprovalStatus.REJECTED
        row.rejected_reason = "Not now"
    db.session.flush()
    return rows


def _all_syncs(org_id, cfg):
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval

    rows = AIChatCRUDApproval.query.filter_by(
        organization_id=org_id, entity_type="connectors", operation_type="sync"
    ).all()
    return [row for row in rows if cfg.id in row.operation_payload]


def test_a_rejected_read_is_not_proposed_again_until_the_next_hourly_slot(
    db_session, github, connect, tenant_ctx, org_a, org_b
):
    _delegate_sync(org_a, on=False)
    _delegate_sync(org_b, on=False)
    cfg_a, cfg_b = connect(org_a), connect(org_b)
    start = datetime.utcnow() + timedelta(hours=2)  # both connections are due by then

    assert _reread(tenant_ctx, org_a, now=start)["proposed"] == [cfg_a.id]
    assert _reread(tenant_ctx, org_b, now=start)["proposed"] == [cfg_b.id]
    _reject_pending_syncs(tenant_ctx, org_a.id, cfg_a)  # only the first organisation's approver declines

    ticks = [_reread(tenant_ctx, org_a, now=start + timedelta(minutes=10 * n)) for n in (1, 2, 3)]
    assert all(tick["proposed"] == [] for tick in ticks), ticks
    with tenant_ctx(org_a.id):
        assert len(_all_syncs(org_a.id, cfg_a)) == 1  # one proposal in total, not one per tick
    assert _logs(cfg_a) == [] or all(log.status != "success" for log in _logs(cfg_a))  # nothing was read
    # the other organisation's pending proposal is untouched and still only one
    with tenant_ctx(org_b.id):
        assert len(_all_syncs(org_b.id, cfg_b)) == 1 and len(_pending_syncs(org_b.id, cfg_b)) == 1

    later = _reread(tenant_ctx, org_a, now=start + timedelta(hours=1, minutes=15))
    assert later["proposed"] == [cfg_a.id]  # the next hourly slot asks again, once
    with tenant_ctx(org_a.id):
        assert len(_all_syncs(org_a.id, cfg_a)) == 2


def test_the_page_says_the_last_read_was_declined_and_when_it_asks_again(
    db_session, github, connect, tenant_ctx, org_a
):
    from app.services.connector_framework import _connector_view

    _delegate_sync(org_a, on=False)
    cfg = connect(org_a)
    start = datetime.utcnow() + timedelta(hours=2)
    _reread(tenant_ctx, org_a, now=start)
    _reject_pending_syncs(tenant_ctx, org_a.id, cfg)
    _reread(tenant_ctx, org_a, now=start + timedelta(minutes=10))

    with tenant_ctx(org_a.id):
        plan = _connector_view(cfg, org_a.id)["read_schedule"]
    assert plan["what_changed"] == "The last read was declined by an approver"
    assert plan["declined"] is True
    assert plan["next_read_at"] >= start + timedelta(minutes=10) + timedelta(hours=1) - timedelta(seconds=5)


def test_a_manual_sync_refused_because_a_read_is_under_way_is_a_notice_not_a_failure(
    db_session, github, connect, org_a, login_as, app
):
    from app.jobs.tenant_safe_job import job_lock
    from tests.test_connector_sync_route_honesty import _make_admin

    cfg = connect(org_a)
    admin = _make_admin(db_session, org_a.id, "Admin")
    db_session.commit()
    client = app.test_client()
    login_as(client, admin)
    with job_lock(f"connector_read:{cfg.id}", required=False) as held:
        assert held
        client.post(f"/admin/connectors/{cfg.id}/sync")
        with client.session_transaction() as sess:
            flashed = list(sess.get("_flashes", []))
    assert flashed, "the refusal says something"
    assert not any("failed" in message.lower() or category == "error" for category, message in flashed), flashed
    assert any("already" in message.lower() and "read" in message.lower() for _c, message in flashed), flashed


def test_the_read_is_still_claimed_when_it_is_committed(db_session, github, connect, tenant_ctx, org_a, monkeypatch):
    """A second reader on its own connection cannot start between the end of a read and its commit."""
    from app.extensions import db
    from app.jobs.tenant_safe_job import job_lock
    from app.services.connector_framework import run_connector_sync

    cfg = connect(org_a)
    probes = []
    real_commit = db.session.commit

    def commit_and_probe():
        with job_lock(f"connector_read:{cfg.id}", required=False) as other_session_got_it:
            probes.append(other_session_got_it)
        return real_commit()

    monkeypatch.setattr(db.session, "commit", commit_and_probe)
    with tenant_ctx(org_a.id):
        assert run_connector_sync(org_id=org_a.id, connector_id=cfg.id, delegated=True)["success"]
    assert probes, "the read commits its own record inside the claim"
    assert probes[-1] is False, "a second session could take the connector at the moment of the commit"


def test_an_old_decline_is_not_recorded_again_after_a_later_read_succeeds(
    db_session, github, connect, tenant_ctx, org_a, org_b
):
    from app.extensions import db
    from app.models.ai_chat_crud_approval import ApprovalStatus
    from app.models.connector_config import SyncLog
    from app.services.connector_framework import _connector_view

    _delegate_sync(org_a, on=False)
    _delegate_sync(org_b, on=False)
    cfg_a, cfg_b = connect(org_a), connect(org_b)
    start = datetime.utcnow() + timedelta(hours=2)
    _reread(tenant_ctx, org_a, now=start)
    _reread(tenant_ctx, org_b, now=start)
    _reject_pending_syncs(tenant_ctx, org_a.id, cfg_a)
    _reread(tenant_ctx, org_a, now=start + timedelta(minutes=10))  # the decline is noted once

    # the next hourly ask is approved and its read succeeds
    assert _reread(tenant_ctx, org_a, now=start + timedelta(hours=1, minutes=15))["proposed"] == [cfg_a.id]
    from app.services.connector_framework import execute_connector_proposal

    read_at = start + timedelta(hours=1, minutes=20)
    with tenant_ctx(org_a.id):
        for row in _pending_syncs(org_a.id, cfg_a):
            row.status = ApprovalStatus.APPROVED  # the approval service's claim
            db.session.commit()
            assert execute_connector_proposal(row)["success"]  # the real approved read
        # the clock of this test is ahead of the real one: place the real read's record in its hour
        reads = SyncLog.query.filter(SyncLog.connector_id == cfg_a.id, SyncLog.status.notin_(["declined", "running"])).all()
        assert reads, "the approved read left its record"
        for log in reads:
            log.started_at = log.completed_at = read_at
        db.session.commit()

    # the next due tick must not invent a decline from the old rejection
    tick = _reread(tenant_ctx, org_a, now=start + timedelta(hours=2, minutes=30))
    statuses = [log.status for log in _logs(cfg_a)]
    assert statuses.count("declined") == 1, statuses
    assert tick["proposed"] == [cfg_a.id], tick  # the hour's request is made, not skipped
    with tenant_ctx(org_a.id):
        plan = _connector_view(cfg_a, org_a.id)["read_schedule"]
    assert plan["declined"] is False
    assert "declined" not in plan["what_changed"]
    # the other organisation, which never declined anything, has no declined record
    assert [log.status for log in _logs(cfg_b)].count("declined") == 0


def test_an_approved_sync_waits_for_a_read_under_way_instead_of_using_up_its_approval(
    db_session, github, connect, tenant_ctx, org_a, monkeypatch
):
    import contextlib
    import time

    from app.jobs.tenant_safe_job import job_lock
    from app.services import connector_framework
    from app.services.connector_framework import run_connector_sync

    cfg = connect(org_a)
    holder = contextlib.ExitStack()
    assert holder.enter_context(job_lock(f"connector_read:{cfg.id}", required=False))
    waits = []

    def finish_the_other_read(_seconds):
        waits.append(1)
        holder.close()  # the read under way ends while the approved sync waits

    monkeypatch.setattr(time, "sleep", finish_the_other_read)
    try:
        with tenant_ctx(org_a.id):
            approval = type("Approved", (), {"id": 1, "organization_id": org_a.id})()
            monkeypatch.setattr(connector_framework, "_run_connector_sync_locked", lambda **_kw: {"success": True})
            result = run_connector_sync(org_id=org_a.id, connector_id=cfg.id, approval=approval)
    finally:
        holder.close()
    assert waits, "it waited for the read under way"
    assert result == {"success": True}, result
    # a caller with no approval behind it is still refused at once
    with job_lock(f"connector_read:{cfg.id}", required=False):
        with tenant_ctx(org_a.id):
            assert run_connector_sync(org_id=org_a.id, connector_id=cfg.id, delegated=True)["busy"] is True


def test_an_approved_sync_is_answered_by_a_read_still_under_way_after_the_wait(
    db_session, github, connect, tenant_ctx, org_a, monkeypatch
):
    import time

    from app.jobs.tenant_safe_job import job_lock
    from app.services import connector_framework
    from app.services.connector_framework import run_connector_sync

    cfg = connect(org_a)
    monkeypatch.setattr(connector_framework, "APPROVED_READ_WAIT_SECONDS", 0.2)
    monkeypatch.setattr(connector_framework, "READ_WAIT_STEP_SECONDS", 0.05)
    monkeypatch.setattr(connector_framework, "_run_connector_sync_locked", lambda **_kw: {"success": True, "ran": True})
    approval = type("Approved", (), {"id": 1, "organization_id": org_a.id})()
    with job_lock(f"connector_read:{cfg.id}", required=False):
        with tenant_ctx(org_a.id):
            started = time.monotonic()
            result = run_connector_sync(org_id=org_a.id, connector_id=cfg.id, approval=approval)
    assert time.monotonic() - started < 5
    # the approval is answered, not failed and not used up for nothing: the read under way is the read
    assert result["success"] is True and result["busy"] is True and "ran" not in result, result
