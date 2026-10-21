"""An approved connector sync writes what it fetched into the model.

The Jira and ServiceNow connectors fetch and map records. These tests prove the
step after that: the records become elements of the calling organisation's model
through the one element writer, each linked to its source record through the
identifier crosswalk, with counts that equal the rows actually written.

Everything is fed by recorded JSON responses (``tests/fixtures``); nothing
touches the network. Two real organisations are used throughout.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

# The trigger and guard installers other suites use, so the event log and the
# entity history are live for these tests exactly as they are in production.
from tests.test_entity_history_trigger import _entity_history_trigger  # noqa: F401
from tests.test_event_log import _install_guards  # noqa: F401

pytestmark = pytest.mark.usefixtures("db_session")

_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
SECRET = "s3cr3t-token-do-not-log-7731"

_KINDS = {
    "jira": {
        "connector_type": "jira",
        "config": {
            "instance_url": "https://example.atlassian.net",
            "email": "svc@example.com",
            "default_project_key": "EA",
        },
        "credential_type": "api_token",
        "good": "jira_search_response.json",
        "malformed": "jira_search_malformed.json",
        "expected": [
            ("10001", "Migrate payroll to the cloud platform", "WorkPackage", "Implementation"),
            ("10002", "Retire legacy CRM", "WorkPackage", "Implementation"),
            ("10003", "Retire legacy CRM", "WorkPackage", "Implementation"),
        ],
        "malformed_good_id": "20001",
        "near_suffix": "s",
    },
    "servicenow": {
        "connector_type": "servicenow",
        "config": {
            "instance_url": "https://example.service-now.com",
            "client_id": "client-123",
        },
        "credential_type": "client_secret",
        "good": "servicenow_cmdb_ci_response.json",
        "malformed": "servicenow_cmdb_ci_malformed.json",
        "expected": [
            ("a1b2c3d4e5f60718293a4b5c6d7e8f90", "Payroll Application", "ApplicationComponent", "Application"),
            ("0f9e8d7c6b5a49382716f5e4d3c2b1a0", "app-server-01", "Node", "Technology"),
        ],
        "malformed_good_id": "11111111111111111111111111111111",
        "near_suffix": " System",
    },
}


def _load(name):
    with open(os.path.join(_FIXTURES, name)) as handle:
        return json.load(handle)


# --------------------------------------------------------------------- #
# Fakes: replay recorded responses instead of calling the network        #
# --------------------------------------------------------------------- #


class _Response:
    def __init__(self, status, body):
        self.status = status
        self._body = body

    async def json(self):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Session:
    def __init__(self, route, calls):
        self._route = route
        self._calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get(self, url, headers=None, params=None, **kw):
        self._calls.append(("GET", url, dict(headers or {}), dict(params or {}), None))
        return _Response(*self._route("GET", url, dict(params or {}), None))

    def post(self, url, headers=None, data=None, **kw):
        self._calls.append(("POST", url, dict(headers or {}), {}, data))
        return _Response(*self._route("POST", url, {}, data))


@pytest.fixture
def replay(monkeypatch):
    """Serve recorded Jira / ServiceNow pages: the first page has the records,
    every later page is empty. Returns the list of requests made."""
    from app.services.connector_framework import BaseConnector

    state = {"calls": [], "body": None, "kind": None, "mutate": None}

    def install(kind, fixture_name, mutate=None):
        body = _load(fixture_name)
        if mutate is not None:
            mutate(body)
        state["body"], state["kind"] = body, kind

    def route(method, url, params, data):
        if method == "POST":
            return 200, {"access_token": "recorded-token"}
        if state["kind"] == "jira":
            if int(params.get("startAt", 0)) == 0:
                return 200, state["body"]
            return 200, {"issues": []}
        if int(params.get("sysparm_offset", 0)) == 0:
            return 200, state["body"]
        return 200, {"result": []}

    monkeypatch.setattr(
        BaseConnector,
        "_outbound_session",
        lambda self, **kw: _Session(route, state["calls"]),
    )
    install.calls = state["calls"]
    return install


# --------------------------------------------------------------------- #
# Helpers                                                                #
# --------------------------------------------------------------------- #


@pytest.fixture(params=sorted(_KINDS))
def kind(request):
    return dict(_KINDS[request.param], name=request.param)


@pytest.fixture
def org_a(make_org):
    return make_org("persist-a")


@pytest.fixture
def org_b(make_org):
    return make_org("persist-b")


@pytest.fixture
def connect(db_session, tenant_ctx):
    """Create a connector row for an organisation with its credential in the vault."""

    def _connect(org, kind):
        from app.models.connector_config import ConnectorConfig
        from app.modules.codegen.services.credential_vault import OrgCredentialVault

        cfg = ConnectorConfig(
            organization_id=org.id,
            connector_type=kind["connector_type"],
            name=f"{kind['name']} for {org.id}",
            config=dict(kind["config"]),
            status="active",
        )
        db_session.add(cfg)
        db_session.flush()
        with tenant_ctx(org.id):
            OrgCredentialVault().store(org.id, kind["connector_type"], kind["credential_type"], SECRET)
        return cfg

    return _connect


@pytest.fixture
def sync(db_session, tenant_ctx):
    """Run the one sync path for an organisation (delegated, so no queue)."""

    def _sync(org, cfg):
        from app.services.connector_framework import run_connector_sync

        with tenant_ctx(org.id):
            result = run_connector_sync(org_id=org.id, connector_id=cfg.id, delegated=True)
        db_session.flush()
        return result

    return _sync


def _count(db_session, table, org_id, **where):
    clauses = "".join(f" AND {k} = :{k}" for k in where)
    return db_session.execute(
        text(f"SELECT count(*) FROM {table} WHERE organization_id = :o{clauses}"),
        {"o": org_id, **where},
    ).scalar()


def _elements(db_session, org_id):
    rows = db_session.execute(
        text(
            "SELECT id, name, type, layer, description FROM archimate_elements "
            "WHERE organization_id = :o ORDER BY id"
        ),
        {"o": org_id},
    ).fetchall()
    return [tuple(r) for r in rows]


def _links(db_session, org_id, source):
    rows = db_session.execute(
        text(
            "SELECT external_id, element_id FROM external_identity_crosswalk "
            "WHERE organization_id = :o AND source_system = :s ORDER BY external_id"
        ),
        {"o": org_id, "s": source},
    ).fetchall()
    return [tuple(r) for r in rows]


def _a_sync_log(db_session, cfg):
    from app.models.connector_config import SyncLog

    return (
        SyncLog.query.filter_by(connector_id=cfg.id).order_by(SyncLog.started_at.desc()).first()
    )


# --------------------------------------------------------------------- #
# Acceptance 1 and 2 - a fixture sync creates the elements, once         #
# --------------------------------------------------------------------- #


def test_sync_creates_the_mapped_elements_and_links_for_org_a_only(
    db_session, org_a, org_b, kind, replay, connect, sync
):
    cfg_a = connect(org_a, kind)
    connect(org_b, kind)
    replay(kind["name"], kind["good"])

    result = sync(org_a, cfg_a)

    expected = kind["expected"]
    assert result["success"] is True
    assert result["records_created"] == len(expected)
    assert result["records_updated"] == 0
    assert result["records_skipped"] == 0
    assert result["records_failed"] == 0

    rows = _elements(db_session, org_a.id)
    # The model stores layer names in lower case.
    assert sorted((r[1], r[2], r[3]) for r in rows) == sorted(
        (name, etype, layer.lower()) for _ext, name, etype, layer in expected
    )

    links = _links(db_session, org_a.id, kind["connector_type"])
    assert [ext for ext, _el in links] == sorted(ext for ext, *_ in expected)
    assert {el for _ext, el in links} == {r[0] for r in rows}, (
        "every created element must be linked to its source record"
    )

    # Nothing was written for the other organisation.
    assert _elements(db_session, org_b.id) == []
    assert _links(db_session, org_b.id, kind["connector_type"]) == []


def test_two_tickets_with_the_same_title_stay_two_elements(
    db_session, org_a, replay, connect, sync
):
    kind = dict(_KINDS["jira"], name="jira")
    cfg = connect(org_a, kind)
    replay("jira", kind["good"])
    sync(org_a, cfg)
    rows = [r for r in _elements(db_session, org_a.id) if r[1] == "Retire legacy CRM"]
    assert len(rows) == 2, "two source records are two elements even when titles match"


def test_second_run_changes_nothing(db_session, org_a, kind, replay, connect, sync):
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval

    cfg = connect(org_a, kind)
    replay(kind["name"], kind["good"])
    first = sync(org_a, cfg)
    elements_before = _elements(db_session, org_a.id)
    links_before = _links(db_session, org_a.id, kind["connector_type"])
    approvals_before = AIChatCRUDApproval.query.filter_by(organization_id=org_a.id).count()

    second = sync(org_a, cfg)

    assert first["records_created"] == len(kind["expected"])
    assert second["records_created"] == 0
    assert second["records_updated"] == 0
    assert second["records_skipped"] == len(kind["expected"])
    assert second["records_failed"] == 0
    assert _elements(db_session, org_a.id) == elements_before
    assert _links(db_session, org_a.id, kind["connector_type"]) == links_before
    assert AIChatCRUDApproval.query.filter_by(organization_id=org_a.id).count() == approvals_before


def test_a_change_at_the_source_updates_the_element_in_place(
    db_session, org_a, kind, replay, connect, sync
):
    cfg = connect(org_a, kind)
    replay(kind["name"], kind["good"])
    sync(org_a, cfg)
    ids_before = {r[0] for r in _elements(db_session, org_a.id)}

    def rename(body):
        records = body.get("issues") or body.get("result")
        for record in records:
            if "fields" in record:
                record["fields"]["summary"] = record["fields"]["summary"] + " v2"
            else:
                record["name"] = record["name"] + " v2"

    replay(kind["name"], kind["good"], mutate=rename)
    result = sync(org_a, cfg)

    assert result["records_updated"] == len(kind["expected"])
    assert result["records_created"] == 0
    rows = _elements(db_session, org_a.id)
    assert {r[0] for r in rows} == ids_before, "an update keeps the same elements"
    assert all(r[1].endswith(" v2") for r in rows)


def test_same_external_id_in_two_organisations_stays_separate(
    db_session, org_a, org_b, kind, replay, connect, sync
):
    cfg_a = connect(org_a, kind)
    cfg_b = connect(org_b, kind)
    replay(kind["name"], kind["good"])

    sync(org_a, cfg_a)
    a_elements = _elements(db_session, org_a.id)
    a_links = _links(db_session, org_a.id, kind["connector_type"])

    result_b = sync(org_b, cfg_b)

    assert result_b["records_created"] == len(kind["expected"]), (
        "B's sync must create its own elements, not reuse A's"
    )
    b_elements = _elements(db_session, org_b.id)
    b_links = _links(db_session, org_b.id, kind["connector_type"])
    assert {r[0] for r in a_elements}.isdisjoint({r[0] for r in b_elements})
    assert [e for e, _ in a_links] == [e for e, _ in b_links], "same external ids in both"
    assert {el for _e, el in b_links} == {r[0] for r in b_elements}
    # B's run left A untouched.
    assert _elements(db_session, org_a.id) == a_elements
    assert _links(db_session, org_a.id, kind["connector_type"]) == a_links


def test_a_sync_run_outside_a_request_still_writes_only_to_its_own_organisation(
    db_session, org_a, org_b, kind, replay, connect
):
    """The scheduler has no request, so no ambient tenant: the organisation must
    come from the connector, not from the request."""
    from app.services.connector_framework import run_connector_sync

    cfg_a = connect(org_a, kind)
    replay(kind["name"], kind["good"])

    run_connector_sync(org_id=org_a.id, connector_id=cfg_a.id, delegated=True)

    assert len(_elements(db_session, org_a.id)) == len(kind["expected"])
    assert _elements(db_session, org_b.id) == []


# --------------------------------------------------------------------- #
# Acceptance 3 - the event log and entity history see the writes         #
# --------------------------------------------------------------------- #


def test_created_elements_appear_in_the_event_log_and_in_history(
    db_session, org_a, org_b, kind, replay, connect, sync
):
    from app.modules.intelligence.services.history_service import HistoryService
    from app.services.event_log_service import read_from_offset, relay_outbox_batch

    cfg = connect(org_a, kind)
    replay(kind["name"], kind["good"])
    sync(org_a, cfg)
    created_ids = {r[0] for r in _elements(db_session, org_a.id)}
    assert created_ids

    relay_outbox_batch()

    events = read_from_offset(org_a.id, 0, limit=1000)
    logged = {
        e["entity_id"] for e in events if e["entity_type"] == "archimate_element"
    }
    assert created_ids <= logged, "each synced element must reach the event log"
    assert read_from_offset(org_b.id, 0, limit=1000) == []

    history = HistoryService(db_session, org_a.id).as_of(datetime.utcnow() + timedelta(minutes=5))
    assert created_ids <= {e.id for e in history.elements}, (
        "each synced element must be in the model as of now"
    )


# --------------------------------------------------------------------- #
# Acceptance 4 - a bad record is counted, never aborts, never leaks      #
# --------------------------------------------------------------------- #


def test_malformed_records_are_counted_and_do_not_abort_the_run(
    db_session, org_a, kind, replay, connect, sync
):
    cfg = connect(org_a, kind)
    replay(kind["name"], kind["malformed"])

    result = sync(org_a, cfg)

    assert result["records_processed"] == 3
    assert result["records_created"] == 1
    assert result["records_failed"] == 2
    assert result["status"] == "partial"
    assert result["success"] is True
    assert [ext for ext, _el in _links(db_session, org_a.id, kind["connector_type"])] == [
        kind["malformed_good_id"]
    ]
    assert len(_elements(db_session, org_a.id)) == 1, "a failed record leaves no half-written row"

    log = _a_sync_log(db_session, cfg)
    assert log.records_created == 1
    assert "2" in (log.error_message or ""), "the failure count must be on the sync log"


def test_credentials_never_reach_the_logs_or_the_sync_log(
    db_session, org_a, kind, replay, connect, sync, caplog
):
    cfg = connect(org_a, kind)
    replay(kind["name"], kind["malformed"])
    caplog.set_level(logging.DEBUG)

    result = sync(org_a, cfg)
    assert result["records_created"] == 1, "the run must have written, or the check below proves nothing"

    needles = [
        SECRET,
        base64.b64encode(f"svc@example.com:{SECRET}".encode()).decode(),
    ]
    log = _a_sync_log(db_session, cfg)
    haystack = "\n".join([caplog.text, json.dumps(result, default=str), log.error_message or ""])
    for needle in needles:
        assert needle not in haystack


# --------------------------------------------------------------------- #
# Acceptance 5 - the counts are the rows                                 #
# --------------------------------------------------------------------- #


def test_counts_returned_equal_the_rows_written(db_session, org_a, kind, replay, connect, sync):
    cfg = connect(org_a, kind)
    replay(kind["name"], kind["good"])
    before_e = _count(db_session, "archimate_elements", org_a.id)
    before_l = _count(db_session, "external_identity_crosswalk", org_a.id)

    result = sync(org_a, cfg)

    after_e = _count(db_session, "archimate_elements", org_a.id)
    after_l = _count(db_session, "external_identity_crosswalk", org_a.id)
    assert after_e - before_e == result["records_created"]
    assert after_l - before_l == result["records_created"]
    assert len(result["elements_touched"]) == result["records_created"]
    log = _a_sync_log(db_session, cfg)
    assert (log.records_created, log.records_updated) == (result["records_created"], 0)
    assert log.records_processed == result["records_processed"]


# --------------------------------------------------------------------- #
# Identity: the matcher decides, the review queue is the existing one    #
# --------------------------------------------------------------------- #


def _manual_element(db_session, org_id, name, etype, layer):
    from app.models.archimate_core import ArchiMateElement

    el = ArchiMateElement(
        organization_id=org_id, name=name, type=etype, layer=layer, scope="enterprise"
    )
    db_session.add(el)
    db_session.flush()
    return el


def test_an_existing_element_with_the_same_name_is_linked_not_duplicated(
    db_session, org_a, org_b, kind, replay, connect, sync
):
    _ext, name, etype, layer = kind["expected"][0]
    mine = _manual_element(db_session, org_a.id, name, etype, layer)
    theirs = _manual_element(db_session, org_b.id, name, etype, layer)
    cfg = connect(org_a, kind)
    replay(kind["name"], kind["good"])

    result = sync(org_a, cfg)

    assert result["records_created"] == len(kind["expected"]) - 1
    assert result["records_updated"] == 1
    named = [r for r in _elements(db_session, org_a.id) if r[1] == name]
    assert [r[0] for r in named] == [mine.id], "no second element for the same thing"
    assert dict(_links(db_session, org_a.id, kind["connector_type"]))[kind["expected"][0][0]] == mine.id
    assert _links(db_session, org_b.id, kind["connector_type"]) == []
    assert [r[0] for r in _elements(db_session, org_b.id)] == [theirs.id]


def test_a_near_duplicate_goes_to_the_review_queue_once_and_is_not_written(
    db_session, org_a, kind, replay, connect, sync
):
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval

    _ext, name, etype, layer = kind["expected"][0]
    near = _manual_element(db_session, org_a.id, name + kind["near_suffix"], etype, layer)
    cfg = connect(org_a, kind)
    replay(kind["name"], kind["good"])

    first = sync(org_a, cfg)
    second = sync(org_a, cfg)

    pending = AIChatCRUDApproval.query.filter_by(
        organization_id=org_a.id, source_table="matcher_service"
    ).all()
    assert len(pending) == 1, "the same uncertain match is queued once, not once per run"
    assert pending[0].source_id == near.id
    assert first["records_skipped"] == 1 and first["records_failed"] == 0
    assert second["records_failed"] == 0 and second["records_created"] == 0
    assert dict(_links(db_session, org_a.id, kind["connector_type"])).get(kind["expected"][0][0]) is None
    assert len(_elements(db_session, org_a.id)) == len(kind["expected"])  # the near one + the others


# --------------------------------------------------------------------- #
# Fail closed, and the approval gate stays                               #
# --------------------------------------------------------------------- #


def test_a_connector_with_no_organisation_writes_nothing(db_session, kind, replay):
    from app.models.connector_config import ConnectorConfig
    from app.services.connector_framework import (
        ConnectorWriteRefused,
        _register_concrete_connectors,
        _CONNECTOR_CLASSES,
    )

    _register_concrete_connectors()
    config = dict(kind["config"])
    config.update(
        base_url=config["instance_url"],
        username="svc@example.com",
        api_token=SECRET,
        project_keys=["EA"],
        client_secret=SECRET,
    )
    connector = _CONNECTOR_CLASSES[kind["connector_type"]](
        ConnectorConfig(
            id=str(uuid.uuid4()),
            connector_type=kind["connector_type"],
            name="no org",
            organization_id=None,
            config=config,
        )
    )
    replay(kind["name"], kind["good"])
    before = db_session.execute(text("SELECT count(*) FROM archimate_elements")).scalar()

    with pytest.raises(ConnectorWriteRefused):
        asyncio.run(connector.batch_sync())

    assert db_session.execute(text("SELECT count(*) FROM archimate_elements")).scalar() == before


def test_sync_is_still_behind_the_approval_gate(
    db_session, org_a, kind, replay, connect, sync, client, login_as
):
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService
    from app.services.connector_framework import (
        ConnectorWriteRefused,
        propose_or_apply_connector_change,
        run_connector_sync,
    )
    from tests.test_connector_approval_flow import _make_admin

    cfg = connect(org_a, kind)
    replay(kind["name"], kind["good"])

    # Neither approved nor delegated: refused, nothing written.
    with pytest.raises(ConnectorWriteRefused):
        run_connector_sync(org_id=org_a.id, connector_id=cfg.id)
    assert _elements(db_session, org_a.id) == []

    # Proposed: queued, still nothing written.
    outcome = propose_or_apply_connector_change(
        org_id=org_a.id,
        action="sync",
        connector_type=kind["connector_type"],
        summary=f"Run sync for connector '{cfg.name}'",
        payload={"connector_id": cfg.id},
    )
    assert outcome.get("queued") is True
    assert _elements(db_session, org_a.id) == []

    # Approved: the sync runs and writes.
    approver = _make_admin(db_session, org_a.id, "PersistApprover")
    db_session.commit()
    proposal = AIChatCRUDApproval.query.filter_by(
        organization_id=org_a.id, entity_type="connectors", operation_type="sync",
        status=ApprovalStatus.PENDING,
    ).first()
    login_as(client, approver)
    result = AIChatApprovalService(user_id=approver.id).approve_and_execute(proposal.id)
    assert result.get("success") is True, result
    assert len(_elements(db_session, org_a.id)) == len(kind["expected"])


# --------------------------------------------------------------------- #
# ServiceNow can authenticate with what the connector form collects      #
# --------------------------------------------------------------------- #


def test_servicenow_asks_for_a_client_credentials_token_when_no_password_is_stored(
    org_a, replay
):
    from app.connectors.servicenow import ServiceNowCMDBConnector
    from app.models.connector_config import ConnectorConfig

    replay("servicenow", _KINDS["servicenow"]["good"])
    connector = ServiceNowCMDBConnector(
        ConnectorConfig(
            id=str(uuid.uuid4()),
            connector_type="servicenow",
            name="sn",
            organization_id=org_a.id,
            config={
                "base_url": "https://example.service-now.com",
                "client_id": "client-123",
                "client_secret": SECRET,
            },
        )
    )

    token = asyncio.run(connector._get_oauth_token())

    assert token == "recorded-token"
    method, url, _headers, _params, data = replay.calls[-1]
    assert (method, url.endswith("/oauth_token.do")) == ("POST", True)
    assert data["grant_type"] == "client_credentials"
    assert "password" not in data and "username" not in data
