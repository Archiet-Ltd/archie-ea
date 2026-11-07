"""How long each value stream stage takes, measured from a recorded Jira changelog.

The fixture holds 30 issues with known times: issue N spends 24N hours In
Progress, 10N hours In Review and 3N hours in QA; only issues 1 to 3 also pass
through Ready to Release. The medians are therefore known exactly:
Build 372 h, Review 155 h, Test 46.5 h, Release 3 issues (too few).
Nothing touches the network. Two real organisations are used.
"""

import json
import os

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "jira_stage_times_changelog.json")
STAGES = [
    ("Build", "In Progress", 372.0),
    ("Review", "In Review", 155.0),
    ("Test", "QA", 46.5),
    ("Release", "Ready to Release", None),
]


def _issues():
    with open(FIXTURE) as handle:
        return json.load(handle)["issues"]


# --------------------------------------------------------------------- #
# Arithmetic                                                             #
# --------------------------------------------------------------------- #


@pytest.mark.parametrize("name,status,median", STAGES)
def test_median_equals_the_fixture_value(name, status, median):
    from app.modules.capabilities.services.stage_time_service import measure_stage

    result = measure_stage(_issues(), [status])
    assert result["median_hours"] == median
    assert result["sample_size"] == (3 if median is None else 30)


def test_several_statuses_add_up_inside_one_stage():
    from app.modules.capabilities.services.stage_time_service import hours_in_statuses

    issue = _issues()[3]  # issue 4: 96 h In Progress, 40 h In Review
    assert hours_in_statuses(issue, ["In Progress", "in review"]) == 136.0


def test_fewer_than_five_issues_is_too_few_to_measure():
    from app.modules.capabilities.services.stage_time_service import measure_stage

    result = measure_stage(_issues()[:4], ["In Progress"])
    assert result["median_hours"] is None
    assert result["sample_size"] == 4


def test_an_issue_still_in_the_status_has_no_finished_visit():
    from app.modules.capabilities.services.stage_time_service import hours_in_statuses

    issue = _issues()[0]
    issue["changelog"]["histories"] = issue["changelog"]["histories"][:2]  # ends in In Review
    assert hours_in_statuses(issue, ["In Review"]) is None
    assert hours_in_statuses(issue, ["In Progress"]) == 24.0


def test_changing_one_issue_moves_the_median():
    from app.modules.capabilities.services.stage_time_service import measure_stage

    issues = _issues()
    # Lengthen issue 15's Review visit by 100 hours: it was 150, the 15th value.
    histories = issues[14]["changelog"]["histories"]
    histories[2]["created"] = "2026-03-01T00:00:00.000+0000"
    assert measure_stage(issues, ["In Review"])["median_hours"] != 155.0


# --------------------------------------------------------------------- #
# Writer, typed fallback, isolation                                      #
# --------------------------------------------------------------------- #


@pytest.fixture
def stream(db_session, make_org, tenant_ctx):
    from app.models.unified_capability import ValueStream, ValueStreamStage

    def _make(org):
        with tenant_ctx(org.id):
            vs = ValueStream(name="Flow %s" % org.id, organization_id=org.id)
            db_session.add(vs)
            db_session.flush()
            stages = []
            for order, (name, status, _m) in enumerate(STAGES, 1):
                stage = ValueStreamStage(
                    name=name, value_stream_id=vs.id, stage_order=order,
                    organization_id=org.id, target_duration=10,
                )
                db_session.add(stage)
                stages.append(stage)
            db_session.flush()
        return vs, stages

    return _make


def test_measurement_is_written_with_its_source_and_only_for_its_organisation(
    db_session, make_org, stream, tenant_ctx
):
    from app.modules.capabilities.services import stage_time_service as svc

    org_a, org_b = make_org("a"), make_org("b")
    vs_a, stages_a = stream(org_a)
    vs_b, stages_b = stream(org_b)
    with tenant_ctx(org_a.id):
        svc.save_mapping(org_a.id, vs_a.id, {s.id: status for s, (_n, status, _m) in zip(stages_a, STAGES)})
        outcome = svc.measure_from_issues(org_a.id, _issues(), "https://example.atlassian.net", vs_a.id)
    assert outcome == {"measured": 3, "too_few": 1, "kept_typed": 0}
    build, review, test, release = stages_a
    assert build.duration_median_hours == 372.0 and build.current_duration == 16  # 15.5 days, rounded
    assert build.duration_source == "jira" and build.duration_sample_size == 30
    assert build.duration_measured_at is not None
    assert len(json.loads(build.duration_samples)) == 30
    assert json.loads(build.duration_samples)[0]["url"].startswith("https://example.atlassian.net/browse/FLOW-")
    assert release.duration_source == "jira" and release.duration_median_hours is None
    assert release.current_duration is None and release.duration_sample_size == 3
    # The other organisation's stages were never read or written.
    for stage in stages_b:
        assert stage.duration_source is None and stage.current_duration is None


def test_measuring_one_organisation_leaves_the_others_mapped_stages_alone(db_session, make_org, stream, tenant_ctx):
    from app.modules.capabilities.services import stage_time_service as svc

    org_a, org_b = make_org("a"), make_org("b")
    _vs_a, stages_a = stream(org_a)
    vs_b, stages_b = stream(org_b)
    with tenant_ctx(org_b.id):
        svc.save_mapping(org_b.id, vs_b.id, {stages_b[0].id: "In Progress"})
    with tenant_ctx(org_a.id):
        svc.measure_from_issues(org_a.id, _issues(), None)
    assert stages_b[0].duration_source is None


def test_a_typed_duration_is_labelled_typed_and_never_measured(db_session, make_org, stream, tenant_ctx):
    from app.modules.capabilities.services import stage_time_service as svc

    org = make_org("a")
    _vs, stages = stream(org)
    stage = stages[0]
    with tenant_ctx(org.id):
        svc.record_typed(stage, 4, "days", "Ada Lovelace")
        info = svc.describe_stage(stage)
    assert info["typed"] and not info["measured"]
    assert info["figure"] == "4 days"
    assert info["provenance"].startswith("Typed by Ada Lovelace, ")
    assert info["median_hours"] is None and info["samples"] == []


def test_too_few_issues_do_not_erase_a_typed_figure(db_session, make_org, stream, tenant_ctx):
    from app.modules.capabilities.services import stage_time_service as svc

    org = make_org("a")
    vs, stages = stream(org)
    with tenant_ctx(org.id):
        svc.save_mapping(org.id, vs.id, {stages[3].id: "Ready to Release"})
        svc.record_typed(stages[3], 2, "days", "Ada Lovelace")
        outcome = svc.measure_from_issues(org.id, _issues(), None, vs.id)
    assert outcome["kept_typed"] == 1
    assert stages[3].duration_source == "typed" and stages[3].current_duration == 2


def test_only_the_slowest_of_two_or_more_measured_stages_is_flagged(db_session, make_org, stream, tenant_ctx):
    from app.modules.capabilities.services import stage_time_service as svc

    org = make_org("a")
    vs, stages = stream(org)
    with tenant_ctx(org.id):
        svc.save_mapping(org.id, vs.id, {s.id: status for s, (_n, status, _m) in zip(stages, STAGES)})
        svc.measure_from_issues(org.id, _issues(), None, vs.id)
        info = svc.describe_stages(stages)
    assert [info[s.id]["slowest"] for s in stages] == [True, False, False, False]
    assert info[stages[3].id]["figure"] is None and info[stages[3].id]["too_few"]
    assert info[stages[0].id]["figure"] == "15.5 days"
    assert info[stages[0].id]["target"] == "10 days"


def test_editing_a_stage_with_an_unchanged_figure_keeps_it_measured(db_session, make_org, stream, tenant_ctx):
    from app.modules.capabilities.services import stage_time_service as svc
    from app.modules.capabilities.services import value_stream_service as vs_service

    org = make_org("a")
    vs, stages = stream(org)
    with tenant_ctx(org.id):
        svc.save_mapping(org.id, vs.id, {stages[0].id: "In Progress"})
        svc.measure_from_issues(org.id, _issues(), None, vs.id)
        vs_service.update_stage(stages[0].id, {"name": "Build", "current_duration": "16"}, "Someone")
        assert stages[0].duration_source == "jira"
        vs_service.update_stage(stages[0].id, {"current_duration": "20"}, "Someone")
        assert stages[0].duration_source == "typed" and stages[0].duration_median_hours is None


# --------------------------------------------------------------------- #
# Connector: fake Jira, the changelog is requested, the sync measures    #
# --------------------------------------------------------------------- #


class _Response:
    status = 200

    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Session:
    def __init__(self, calls):
        self._calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get(self, url, headers=None, params=None, **kw):
        self._calls.append(dict(params or {}))
        if int((params or {}).get("startAt", 0)) == 0:
            with open(FIXTURE) as handle:
                return _Response(json.load(handle))
        return _Response({"issues": []})


@pytest.fixture
def fake_jira(monkeypatch):
    from app.services.connector_framework import BaseConnector

    calls = []
    monkeypatch.setattr(BaseConnector, "_outbound_session", lambda self, **kw: _Session(calls))
    return calls


def test_the_connector_asks_for_the_changelog_and_the_sync_writes_the_measurement(
    db_session, make_org, stream, tenant_ctx, fake_jira
):
    from app.connectors.jira import JiraALMConnector
    from app.models.connector_config import ConnectorConfig
    from app.modules.capabilities.services import stage_time_service as svc
    from app.services.connector_framework import run_coroutine_sync

    org_a, org_b = make_org("a"), make_org("b")
    vs_a, stages_a = stream(org_a)
    _vs_b, stages_b = stream(org_b)
    with tenant_ctx(org_a.id):
        svc.save_mapping(org_a.id, vs_a.id, {stages_a[0].id: "In Progress"})
    config = ConnectorConfig(
        id="jira-%s" % org_a.id, connector_type="jira", name="Jira", organization_id=org_a.id,
        config={"base_url": "https://example.atlassian.net", "username": "u", "api_token": "t",
                "project_keys": ["FLOW"]},
    )
    connector = JiraALMConnector(config)
    with tenant_ctx(org_a.id):
        result = run_coroutine_sync(connector.batch_sync())
    assert all(call.get("expand") == "changelog" for call in fake_jira)
    assert result["stage_times"]["measured"] == 1
    assert stages_a[0].duration_median_hours == 372.0
    assert stages_b[0].duration_source is None
