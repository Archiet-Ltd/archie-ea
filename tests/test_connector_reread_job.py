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
