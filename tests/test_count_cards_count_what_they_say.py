"""Each gap and risk count counts what its label says, from the one query, and its list holds those rows.

Two organisations in every test: a count that reached across organisations would be a different number.
"""

from __future__ import annotations

import uuid
from unittest import mock

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _gap(db_session, org, **fields):
    from app.models.implementation_migration import Gap

    fields.setdefault("gap_type", "coverage")
    gap = Gap(organization_id=org.id, name="Gap %s" % uuid.uuid4().hex[:8], **fields)
    db_session.add(gap)
    db_session.flush()
    return gap


def _signed_in(db_session, make_org, client, login_as, label):
    from tests.test_work_package_writer import _user

    mine, theirs = make_org(label), make_org(label + "-other")
    login_as(client, _user(db_session, mine, label.replace("-", ""), role="enterprise_architect"))
    return mine, theirs


def _seed_gaps(db_session, mine, theirs):
    _gap(db_session, mine, priority="medium", severity="critical")                        # open critical
    _gap(db_session, mine, priority="critical", severity="high")                          # open critical, by priority
    _gap(db_session, mine, priority="critical", severity="critical", resolution_status="resolved")
    _gap(db_session, mine, priority="low", severity="low", resolution_status=" Completed ")
    _gap(db_session, mine, priority="low", severity="high", resolution_status="in_progress")
    # the other organisation holds gaps of every kind
    for status in ("identified", "resolved", "in_progress"):
        _gap(db_session, theirs, priority="critical", severity="critical", resolution_status=status)


def test_an_open_count_and_list_leave_out_a_resolved_gap(db_session, make_org):
    from app.services.gap_register_service import count_gaps, list_gaps

    mine, theirs = make_org("open-a"), make_org("open-b")
    _seed_gaps(db_session, mine, theirs)

    assert count_gaps(mine.id, severity="critical") == 3
    assert count_gaps(mine.id, severity="critical", closed=False) == 2
    listed = list_gaps(mine.id, severity="critical", closed=False)
    assert len(listed) == 2 and all(g.resolution_status != "resolved" for g in listed)
    assert count_gaps(mine.id, in_progress=True) == 1
    assert count_gaps(mine.id, closed=True) == 2


def test_the_gap_page_counts_from_queries_when_the_list_is_cut(db_session, make_org, client, login_as):
    mine, theirs = _signed_in(db_session, make_org, client, login_as, "cut-page")
    _seed_gaps(db_session, mine, theirs)
    with mock.patch("app.routes.unified_enterprise_routes._GAP_PAGE_SIZE", 2, create=True), \
            mock.patch("app.routes.unified_enterprise_routes.render_template", return_value="ok") as render:
        client.get("/enterprise/implementation/gap-analysis")
    shown = render.call_args.kwargs
    assert len(shown["gaps"]) == 2
    assert shown["gap_counts"] == {"recorded": 5, "critical": 3, "critical_open": 2, "in_progress": 1, "resolved": 2}


def test_the_gap_page_lists_the_rows_a_card_counts(db_session, make_org, client, login_as):
    mine, theirs = _signed_in(db_session, make_org, client, login_as, "cards-list")
    _seed_gaps(db_session, mine, theirs)
    expected = {"severity=critical": 3, "severity=critical&status=open": 2, "status=closed": 2,
                "status=in_progress": 1, "status=open": 3}
    for query, figure in expected.items():
        with mock.patch("app.routes.unified_enterprise_routes.render_template", return_value="ok") as render:
            client.get("/enterprise/implementation/gap-analysis?" + query)
        shown = render.call_args.kwargs
        assert len(shown["gaps"]) == shown["listed_count"] == figure, (query, len(shown["gaps"]))


def test_the_ai_analysis_open_gap_figures_are_open_gaps(db_session, make_org, client, login_as):
    mine, theirs = _signed_in(db_session, make_org, client, login_as, "ai-open")
    _seed_gaps(db_session, mine, theirs)
    with mock.patch("app.routes.unified_enterprise_routes.render_template", return_value="ok") as render:
        client.get("/enterprise/analysis/ai-architecture")
    shown = render.call_args.kwargs
    assert shown["gaps_by_severity"]["critical"] == 2
    assert shown["gaps_by_severity"]["high"] == 1
    assert shown["total_gaps"] == 3


def test_the_implementation_list_of_critical_gaps_holds_open_ones(db_session, make_org, client, login_as):
    mine, theirs = _signed_in(db_session, make_org, client, login_as, "impl-open")
    _seed_gaps(db_session, mine, theirs)
    body = client.get("/implementation/api/gaps?severity=critical&open=1&limit=5").get_json()
    assert body["total"] == 2 and len(body["gaps"]) == 2
    assert all(g["resolution_status"] != "resolved" for g in body["gaps"])
    assert client.get("/implementation/api/gaps?severity=critical").get_json()["total"] == 3


def test_the_old_gap_severity_address_redirects_to_the_register(db_session, make_org, client, login_as):
    mine, theirs = _signed_in(db_session, make_org, client, login_as, "sev-redirect")
    _seed_gaps(db_session, mine, theirs)
    register = "/enterprise/implementation/gap-analysis"
    old = "/enterprise/strategic/risk-assessment"

    plain = client.get(old)
    assert plain.status_code == 301 and plain.headers["Location"].endswith(register)
    for level in ("critical", "high", "medium", "low"):
        mapped = client.get(old + "?severity=" + level)
        assert mapped.status_code == 301 and mapped.headers["Location"].endswith(
            register + "?severity=" + level + "&status=open")
    assert client.get(old + "?severity=Critical").headers["Location"].endswith(
        register + "?severity=critical&status=open")
    dropped = client.get(old + "?severity=bogus")
    assert dropped.status_code == 301 and dropped.headers["Location"].endswith(register)

    landed = client.get(old, follow_redirects=True)
    assert landed.status_code == 200 and "Open critical" in landed.get_data(as_text=True)


def test_the_old_gap_severity_address_needs_a_sign_in(client):
    response = client.get("/enterprise/strategic/risk-assessment")
    assert response.status_code in (301, 302) and "gap-analysis" not in response.headers["Location"]


def test_the_register_counts_open_gaps_by_severity_and_lists_those_rows(db_session, make_org, client, login_as):
    mine, theirs = _signed_in(db_session, make_org, client, login_as, "sev-counts")
    _seed_gaps(db_session, mine, theirs)
    with mock.patch("app.routes.unified_enterprise_routes.render_template", return_value="ok") as render:
        client.get("/enterprise/implementation/gap-analysis")
    open_by_severity = render.call_args.kwargs["open_by_severity"]
    assert open_by_severity == {"critical": 2, "high": 1, "medium": 0, "low": 0, "unrated": 0}
    for level, figure in open_by_severity.items():
        with mock.patch("app.routes.unified_enterprise_routes.render_template", return_value="ok") as render:
            client.get("/enterprise/implementation/gap-analysis?severity=%s&status=open" % level)
        assert render.call_args.kwargs["listed_count"] == figure, level


def test_a_gap_page_past_the_end_shows_the_last_page_not_an_error(db_session, make_org, client, login_as):
    mine, theirs = _signed_in(db_session, make_org, client, login_as, "page-end")
    _seed_gaps(db_session, mine, theirs)
    for number in ("99", str(10 ** 12)):
        with mock.patch("app.routes.unified_enterprise_routes._GAP_PAGE_SIZE", 2), \
                mock.patch("app.routes.unified_enterprise_routes.render_template", return_value="ok") as render:
            response = client.get("/enterprise/implementation/gap-analysis?page=" + number)
        shown = render.call_args.kwargs
        assert response.status_code == 200
        assert shown["page_number"] == 3 and len(shown["gaps"]) == 1, number

    html = client.get("/enterprise/implementation/gap-analysis?page=99").get_data(as_text=True)
    assert "No gaps recorded yet" not in html and "gap-row" in html


def test_a_filter_with_no_rows_names_the_filter_and_offers_to_clear_it(db_session, make_org, client, login_as):
    mine, theirs = _signed_in(db_session, make_org, client, login_as, "filter-empty")
    _seed_gaps(db_session, mine, theirs)
    html = client.get("/enterprise/implementation/gap-analysis?severity=low&status=in_progress").get_data(as_text=True)
    assert "No gap matches this filter (low severity, in progress)" in html
    assert "The register holds 5 gaps" in html and "gaps-filter-empty-clear" in html
    assert "No gaps recorded yet" not in html

    empty = make_org("filter-none")
    from tests.test_work_package_writer import _user
    login_as(client, _user(db_session, empty, "filternone", role="enterprise_architect"))
    html = client.get("/enterprise/implementation/gap-analysis").get_data(as_text=True)
    assert "No gaps recorded yet" in html and "gaps-filter-empty" not in html


def test_gaps_with_no_work_package_are_the_open_ones_and_equal_the_boards_figure(
    db_session, make_org, client, login_as
):
    from app.services import work_package_service
    from app.services.gap_register_service import count_gaps, get_gaps_not_addressed

    mine, theirs = _signed_in(db_session, make_org, client, login_as, "no-wp")
    covered = [_gap(db_session, mine, priority="low", severity="low") for _ in range(4)]
    open_bare = [_gap(db_session, mine, priority="low", severity="low") for _ in range(2)]
    closed_bare = _gap(db_session, mine, priority="low", severity="low", resolution_status="completed")
    _gap(db_session, theirs, priority="low", severity="low")
    _gap(db_session, theirs, priority="low", severity="low", resolution_status="resolved")
    for gap in covered:
        package = work_package_service.create_work_package(organization_id=mine.id, name="Close %s" % gap.name)
        work_package_service.link_gap(package.id, gap.id, organization_id=mine.id)

    listed = get_gaps_not_addressed(mine.id)
    assert {g.id for g in listed} == {g.id for g in open_bare}
    assert closed_bare.id not in {g.id for g in listed}
    assert len(listed) == count_gaps(mine.id, has_work_package=False, closed=False)
    assert [g.id for g in get_gaps_not_addressed(theirs.id)] != []
    assert len(get_gaps_not_addressed(theirs.id)) == count_gaps(theirs.id, has_work_package=False, closed=False) == 1

    with mock.patch("app.modules.architecture.routes.adm_kanban_view_routes.render_template",
                    return_value="ok") as render:
        client.get("/adm-kanban/gap-analysis")
    assert render.call_args.kwargs["unclosed_count"] == len(listed) == 2

    html = client.get("/enterprise/implementation/gap-analysis").get_data(as_text=True)
    section = html[html.index('data-testid="gaps-not-addressed"'):]
    # What a person reads: the section says how many gaps have no work package, and lists exactly those rows.
    assert 'data-testid="gaps-not-addressed-total">2 gaps have no work package.<' in section
    assert section.count('data-testid="not-addressed-gap-row"') == len(listed) == 2
    for gap in open_bare:
        assert 'data-testid="not-addressed-gap-link-%d"' % gap.id in section
        assert gap.name in section
    for gap in covered + [closed_bare]:
        assert 'data-testid="not-addressed-gap-link-%d"' % gap.id not in section


def test_the_boards_gap_signals_figure_opens_the_roadmap_that_counts_them(db_session, make_org, client, login_as):
    mine, theirs = _signed_in(db_session, make_org, client, login_as, "signals-link")
    with mock.patch("app.modules.capabilities.routes.roadmap_routes.compute_gap_signals", return_value=[1, 2, 3]):
        html = client.get("/adm-kanban/gap-analysis").get_data(as_text=True)
    assert 'href="/capability-roadmap#gap-signals"' in html and 'data-testid="gap-signals">3<' in html
    with mock.patch("app.modules.capabilities.routes.roadmap_routes.compute_gap_signals", return_value=[]):
        html = client.get("/adm-kanban/gap-analysis").get_data(as_text=True)
    assert 'data-testid="gap-signals">0<' in html and 'href="/capability-roadmap"' not in html


def test_the_board_counts_no_work_planned_among_open_gaps_only(db_session, make_org, client, login_as):
    mine, theirs = _signed_in(db_session, make_org, client, login_as, "board-open")
    _seed_gaps(db_session, mine, theirs)
    with mock.patch("app.modules.architecture.routes.adm_kanban_view_routes.render_template",
                    return_value="ok") as render:
        client.get("/adm-kanban/gap-analysis")
    shown = render.call_args.kwargs
    assert shown["recorded_count"] == 5 and shown["closed_count"] == 2
    assert shown["unclosed_count"] + shown["covered_count"] + shown["closed_count"] == shown["recorded_count"]
    assert shown["unclosed_count"] == 3


def _closed_and_open_gaps(db_session, make_org):
    mine, theirs = make_org("closed-a"), make_org("closed-b")
    open_gap = _gap(db_session, mine, priority="low", severity="low")
    _gap(db_session, mine, priority="low", severity="low", resolution_status="completed")
    _gap(db_session, mine, priority="low", severity="low", resolution_status=" Complete ")
    _gap(db_session, theirs, priority="low", severity="low")
    return mine, open_gap


def test_a_completed_gap_is_closed_in_the_roadmap_graph_and_summary(db_session, make_org, tenant_ctx):
    from app.modules.solutions_strategic.v2.services.roadmap_builder_service import RoadmapBuilderService

    mine, open_gap = _closed_and_open_gaps(db_session, make_org)
    with tenant_ctx(mine.id):
        nodes = RoadmapBuilderService()._generate_gap_graph_elements(0)
        summary = RoadmapBuilderService().get_roadmap_summary()
    assert [n["data"]["gapId"] for n in nodes] == [open_gap.id]
    assert summary["gaps"]["open"] == 1


def test_the_served_roadmap_summary_and_graph_count_the_registers_open_gaps(
    db_session, make_org, client, login_as
):
    from app.services.gap_register_service import count_gaps

    mine, theirs = _signed_in(db_session, make_org, client, login_as, "served-roadmap")
    open_gap = _gap(db_session, mine, priority="low", severity="low")
    no_status = _gap(db_session, mine, priority="low", severity="low", resolution_status=None)
    _gap(db_session, mine, priority="low", severity="low", resolution_status="completed")
    _gap(db_session, mine, priority="low", severity="low", resolution_status=" Resolved ")
    _gap(db_session, theirs, priority="low", severity="low")
    _gap(db_session, theirs, priority="low", severity="low", resolution_status="completed")

    summary = client.get("/api/roadmap-builder/summary").get_json()["data"]
    graph = client.get("/api/roadmap-builder/dependency-graph?include_gaps=true").get_json()["data"]
    gap_nodes = sorted(n["data"]["gapId"] for n in graph["nodes"] if n.get("type") == "gap")
    assert summary["gaps"]["open"] == count_gaps(mine.id, closed=False) == 2
    assert gap_nodes == sorted([open_gap.id, no_status.id])


def test_a_padded_closed_status_is_closed_in_the_posture_lens(db_session, make_org, tenant_ctx):
    from app.modules.solutions_strategic.v2.services.enterprise_posture_service import EnterprisePostureService

    mine, _ = _closed_and_open_gaps(db_session, make_org)
    with tenant_ctx(mine.id):
        lens, _attention = EnterprisePostureService._roadmap_lens()
    open_measure = next(m for m in lens["measures"] if m["label"] == "Open gaps")
    assert (open_measure["value"], open_measure["of"]) == (1, 3)


def _risk(db_session, org, status, likelihood, impact):
    from app.models.risk import Risk, RiskStatus

    risk = Risk(title="Risk %s" % uuid.uuid4().hex[:8], description="x", likelihood=likelihood, impact=impact,
                status=RiskStatus(status), owner="Owner", mitigation_plan="Plan", organization_id=org.id)
    db_session.add(risk)
    db_session.flush()
    return risk


def test_a_risk_count_card_opens_the_register_rows_it_counts(db_session, make_org, client, login_as):
    mine, theirs = _signed_in(db_session, make_org, client, login_as, "risk-cards")
    _risk(db_session, mine, "open", 5, 5)
    _risk(db_session, mine, "open", 4, 4)
    _risk(db_session, mine, "open", 1, 1)
    _risk(db_session, mine, "mitigated", 5, 5)
    _risk(db_session, mine, "closed", 5, 5)
    _risk(db_session, theirs, "open", 5, 5)
    expected = {"open": 3, "critical": 2, "mitigated": 1}
    for show, figure in expected.items():
        with mock.patch("app.modules.architecture.routes.risk_routes.render_template", return_value="ok") as render:
            client.get("/risks/?show=" + show)
        rows = render.call_args.kwargs["risk_rows"]
        assert len(rows) == figure, (show, len(rows))
        assert render.call_args.kwargs["total"] == 5
    with mock.patch("app.modules.architecture.routes.risk_routes.render_template", return_value="ok") as render:
        client.get("/risks/")
    assert len(render.call_args.kwargs["risk_rows"]) == 5


def test_the_old_gap_severity_address_lists_the_open_gaps_its_figure_counted(db_session, make_org, client, login_as):
    mine, theirs = _signed_in(db_session, make_org, client, login_as, "sev-open-rows")
    _seed_gaps(db_session, mine, theirs)
    with mock.patch("app.routes.unified_enterprise_routes.render_template", return_value="ok") as render:
        client.get("/enterprise/strategic/risk-assessment?severity=critical", follow_redirects=True)
    shown = render.call_args.kwargs
    assert shown["open_by_severity"]["critical"] == 2
    assert shown["listed_count"] == len(shown["gaps"]) == 2
    assert all(g.resolution_status != "resolved" for g in shown["gaps"])


def test_a_second_organisations_person_reaches_only_their_own_gaps_through_the_old_address(
        db_session, make_org, client, login_as):
    from tests.test_work_package_writer import _user

    mine, theirs = make_org("old-a"), make_org("old-b")
    _seed_gaps(db_session, mine, theirs)
    mine_names = {g.name for g in _all_gaps(mine)}
    their_names = {g.name for g in _all_gaps(theirs)}
    visitor = _user(db_session, theirs, "oldb", role="enterprise_architect")
    login_as(client, visitor)
    with mock.patch("app.routes.unified_enterprise_routes.render_template", return_value="ok") as render:
        response = client.get("/enterprise/strategic/risk-assessment?severity=critical", follow_redirects=True)
    assert response.status_code == 200
    shown = render.call_args.kwargs
    listed = {g.name for g in shown["gaps"]}
    assert listed and listed <= their_names and not (listed & mine_names)
    # their critical gaps: one identified, one in progress (open), one resolved
    assert shown["listed_count"] == shown["open_by_severity"]["critical"] == 2


def _all_gaps(org):
    from app.models.implementation_migration import Gap

    return Gap.query.filter(Gap.organization_id == org.id).all()


def test_the_not_addressed_section_lists_every_such_gap_not_the_pages_rows(db_session, make_org, client, login_as):
    from app.services import work_package_service

    mine, theirs = _signed_in(db_session, make_org, client, login_as, "not-addr")
    covered = [_gap(db_session, mine, priority="low", severity="low") for _ in range(6)]
    bare = [_gap(db_session, mine, priority="low", severity="low") for _ in range(3)]
    _gap(db_session, theirs, priority="low", severity="low")
    for gap in covered:
        wp = work_package_service.create_work_package(organization_id=mine.id, name="Close %s" % gap.name)
        work_package_service.link_gap(wp.id, gap.id, organization_id=mine.id)
    db_session.flush()
    with mock.patch("app.routes.unified_enterprise_routes._GAP_PAGE_SIZE", 4, create=True), \
            mock.patch("app.routes.unified_enterprise_routes.render_template", return_value="ok") as render:
        client.get("/enterprise/implementation/gap-analysis?page=2")
    shown = render.call_args.kwargs
    assert {g.id for g in shown["not_addressed_gaps"]} == {g.id for g in bare}
    assert len(shown["gaps"]) < shown["listed_count"] == 9
    assert [g.name for g in shown["not_addressed_gaps"]] == sorted(g.name for g in bare)


def test_the_workflow_step_reports_the_work_packages_it_wrote(app, db_session, make_org):
    from types import SimpleNamespace

    from flask import g
    from flask_login import login_user

    from app.models.unified_work_package import UnifiedWorkPackage
    from app.modules.solutions_strategic.v2.services.ea_workflow_engine import EAWorkflowEngine
    from tests.test_work_package_writer import _user

    mine, theirs = make_org("engine"), make_org("engine-other")
    person = _user(db_session, mine, "engine", role="enterprise_architect")
    engine = EAWorkflowEngine()
    instance = SimpleNamespace(instance_code="WF-T", context={})
    items = [{"name": "Close %d %s" % (i, uuid.uuid4().hex[:6])} for i in range(3)]
    with app.test_request_context("/"):
        login_user(person)
        g.current_org_id = mine.id
        out = engine._handle_roadmap_creation(instance, {}, {"approved_items": items})
        rows = UnifiedWorkPackage.query.filter(UnifiedWorkPackage.id.in_(out["work_package_ids"])).all()
    assert out["roadmap_items_created"] == len(out["work_package_ids"]) == 3
    assert {r.name for r in rows} == {i["name"] for i in items}
    assert {r.organization_id for r in rows} == {mine.id}
    assert UnifiedWorkPackage.query.filter(UnifiedWorkPackage.organization_id == theirs.id).count() == 0
