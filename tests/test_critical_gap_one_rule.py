"""A gap is critical when either its severity or its priority says so, and every count reads that one rule."""

from __future__ import annotations

import re
import uuid
from unittest import mock

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _gap(db_session, org, **fields):
    from app.models.implementation_migration import Gap

    gap = Gap(organization_id=org.id, name="Gap %s" % uuid.uuid4().hex[:8], gap_type="coverage", **fields)
    db_session.add(gap)
    db_session.flush()
    return gap


def test_a_gap_critical_by_priority_alone_is_counted_and_listed(db_session, make_org):
    from app.services.gap_register_service import count_gaps, list_gaps

    mine, theirs = make_org("rule-a"), make_org("rule-b")
    by_priority = _gap(db_session, mine, priority="critical", severity="medium")
    by_severity = _gap(db_session, mine, priority="high", severity="critical")
    _gap(db_session, mine, priority="high", severity="high")
    _gap(db_session, theirs, priority="critical", severity="critical")

    assert count_gaps(mine.id, severity="critical") == 2
    assert {g.id for g in list_gaps(mine.id, severity="critical")} == {by_priority.id, by_severity.id}
    assert by_priority.rated_severity == "critical"


def test_update_moves_severity_with_priority_both_ways(db_session, make_org):
    from app.services.gap_archimate_service import gap_archimate_service
    from app.services.gap_register_service import count_gaps

    org = make_org("rule-update")
    gap = _gap(db_session, org, priority="medium", severity="medium")

    gap_archimate_service.update_gap(gap.id, {"priority": "critical"})
    db_session.flush()
    assert (gap.priority, gap.severity) == ("critical", "critical")
    assert count_gaps(org.id, severity="critical") == 1

    gap_archimate_service.update_gap(gap.id, {"priority": "low"})
    db_session.flush()
    assert (gap.priority, gap.severity) == ("low", "low")
    assert count_gaps(org.id, severity="critical") == 0


def test_the_gaps_list_page_counts_critical_by_the_rule(db_session, make_org, client, login_as):
    from tests.test_work_package_writer import _user

    org, other = make_org("rule-list"), make_org("rule-list-other")
    user = _user(db_session, org, "rulelist", role="enterprise_architect")
    _gap(db_session, org, priority="critical", severity="low")
    _gap(db_session, org, priority="high", severity="critical")
    _gap(db_session, other, priority="critical", severity="critical")
    login_as(client, user)
    with mock.patch("app.implementation_planning.routes.render_template", return_value="ok") as render:
        client.get("/implementation/gaps")
    assert render.call_args.kwargs["stats"]["critical"] == 2


def test_capability_health_threshold_words_follow_the_rule(db_session, make_org, client, login_as):
    from app.modules.capabilities.services import capability_health_service
    from tests.test_work_package_writer import _user

    org = make_org("rule-health")
    user = _user(db_session, org, "rulehealth", role="enterprise_architect")
    login_as(client, user)
    with mock.patch.object(capability_health_service, "AT_RISK_BELOW", 45):
        html = client.get("/strategic/capability-health").get_data(as_text=True)
    text = re.sub(r"<[^>]+>", " ", re.sub(r"<(script|style|template).*?</\1>", "", html, flags=re.S))
    assert "below 45%" in text
    assert "below 60%" not in text


def _editor(db_session, make_org, client, login_as, label):
    from tests.test_work_package_writer import _user

    org = make_org(label)
    login_as(client, _user(db_session, org, label.replace("-", ""), role="enterprise_architect"))
    return org


def test_an_edit_that_leaves_the_priority_alone_keeps_the_severity(db_session, make_org, client, login_as):
    from app.models.implementation_migration import Gap

    org = _editor(db_session, make_org, client, login_as, "keep-sev")
    gap = _gap(db_session, org, priority="medium", severity="critical")
    response = client.put(
        "/capability-map/api/roadmap/gaps/%d" % gap.id,
        json={"description": "Only the words changed", "priority": "medium"},
    )
    assert response.status_code == 200
    db_session.expire_all()
    assert Gap.query.filter_by(id=gap.id).one().severity == "critical"


def test_choosing_a_new_priority_still_moves_the_severity(db_session, make_org, client, login_as):
    from app.models.implementation_migration import Gap

    org = _editor(db_session, make_org, client, login_as, "move-sev")
    gap = _gap(db_session, org, priority="medium", severity="medium")
    assert client.put("/capability-map/api/roadmap/gaps/%d" % gap.id, json={"priority": " HIGH "}).status_code == 200
    db_session.expire_all()
    stored = Gap.query.filter_by(id=gap.id).one()
    assert (stored.priority, stored.severity) == ("high", "high")


def test_a_priority_outside_the_four_is_refused_on_every_write(db_session, make_org, client, login_as):
    from app.models.implementation_migration import Gap

    org = _editor(db_session, make_org, client, login_as, "bad-prio")
    gap = _gap(db_session, org, priority="medium", severity="medium")
    assert client.put("/capability-map/api/roadmap/gaps/%d" % gap.id, json={"priority": "urgent"}).status_code == 400
    created = client.post("/capability-map/api/roadmap/gaps", json={"name": "Bad priority", "priority": "urgent"})
    assert created.status_code == 400
    assert Gap.query.filter_by(organization_id=org.id, name="Bad priority").count() == 0
    db_session.expire_all()
    assert Gap.query.filter_by(id=gap.id).one().priority == "medium"


def test_a_priority_written_in_capitals_is_stored_plain_and_counted(db_session, make_org, client, login_as):
    from app.models.implementation_migration import Gap
    from app.services.gap_register_service import count_gaps

    org = _editor(db_session, make_org, client, login_as, "caps-prio")
    response = client.post("/capability-map/api/roadmap/gaps", json={"name": "Shouted", "priority": " Critical "})
    assert response.status_code in (200, 201)
    stored = Gap.query.filter_by(organization_id=org.id, name="Shouted").one()
    assert (stored.priority, stored.severity) == ("critical", "critical")
    assert count_gaps(org.id, severity="critical") == 1


def test_a_priority_stored_in_capitals_before_is_still_counted_critical(db_session, make_org):
    from app.services.gap_register_service import count_gaps

    org = make_org("old-caps")
    _gap(db_session, org, priority="Critical", severity="medium")
    assert count_gaps(org.id, severity="critical") == 1


def test_the_gap_page_states_the_same_rating_as_the_register(db_session, make_org, client, login_as):
    org = _editor(db_session, make_org, client, login_as, "page-rating")
    gap = _gap(db_session, org, priority="critical", severity="medium")
    html = client.get("/architecture/implementation/Gap/%d" % gap.id).get_data(as_text=True)
    text = re.sub(r"<[^>]+>", " ", re.sub(r"<(script|style|template).*?</\1>", "", html, flags=re.S))
    assert re.search(r"Rating \(sets priority and severity\)\s+critical", text, re.I)
    assert not re.search(r"(Severity|Priority)\s+medium", text, re.I)


def test_no_gap_reader_reads_the_severity_column_outside_the_register():
    """A reader of the gap model asks the register for a gap's rating; only the register and the writers
    that set the column may touch `severity` themselves."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent / "app"
    direct = re.compile(r"\b(?:Gap|ImplementationGap)\.severity\b|\bgap\.severity\b|\bgap_row\.severity\b")
    # these name a different kind of record that happens to be called a gap
    other_kinds = {
        "services/agents/gap_analysis_agent.py": "a dataclass the agent builds, not a register row",
        "modules/solutions_strategic/v2/routes/journey_v2_routes.py": "reasoning findings, not register rows",
    }
    # the writers: they set the column from the priority, they do not read it as a rating
    writers = {"modules/architecture/services/gap_archimate_service.py"}
    offenders = []
    for path in root.rglob("*.py"):
        rel = path.relative_to(root).as_posix()
        if rel == "services/gap_register_service.py" or rel in writers or rel in other_kinds:
            continue
        if rel.startswith("models/"):
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if direct.search(line) and not line.lstrip().startswith("#"):
                offenders.append("%s:%d" % (rel, number))
    assert offenders == [], "read a gap's rating through effective_severity: %s" % offenders


def test_architecture_intelligence_and_prioritised_actions_rate_by_the_rule(db_session, make_org, client, login_as):
    from app.services import priority_action_service
    from app.services.gap_register_service import count_gaps, effective_severity
    from tests.test_work_package_writer import _user

    org, other = make_org("rule-ai"), make_org("rule-ai-other")
    user = _user(db_session, org, "ruleai", role="enterprise_architect")
    by_priority = _gap(db_session, org, priority="critical", severity="medium")
    _gap(db_session, org, priority="high", severity="high")
    _gap(db_session, other, priority="critical", severity="critical")
    assert effective_severity(by_priority) == "critical"
    login_as(client, user)
    with mock.patch("app.routes.unified_enterprise_routes.render_template", return_value="ok") as render:
        client.get("/enterprise/analysis/ai-architecture")
    assert render.call_args.kwargs["gaps_by_severity"] == {"critical": 1, "high": 1, "medium": 0, "low": 0}
    assert count_gaps(org.id, severity="critical") == 1

    rows = priority_action_service.prioritised_actions(org.id, limit=50)["rows"]
    mine = next(r for r in rows if r["kind"] == "gap" and r["record_id"] == by_priority.id)
    assert mine["severity"].lower() == "critical"
    assert "critical severity" in mine["why"].lower()


def test_severity_filter_and_sort_follow_the_rule(db_session, make_org, client, login_as):
    from tests.test_work_package_writer import _user

    org = make_org("rule-sort")
    user = _user(db_session, org, "rulesort", role="enterprise_architect")
    by_priority = _gap(db_session, org, priority="critical", severity="medium")
    plain_high = _gap(db_session, org, priority="high", severity="high")
    plain_medium = _gap(db_session, org, priority="medium", severity="medium")
    login_as(client, user)

    def listed(**args):
        with mock.patch("app.routes.unified_enterprise_routes.render_template", return_value="ok") as render:
            client.get("/enterprise/implementation/gap-analysis", query_string=args)
        return [g.id for g in render.call_args.kwargs["gaps"]]

    assert listed(severity="medium") == [plain_medium.id]
    assert listed(severity="critical") == [by_priority.id]
    assert listed(sort="severity", dir="desc")[:2] == [by_priority.id, plain_high.id]


def test_total_of_the_gaps_api_is_the_full_count_when_limited(db_session, make_org, client, login_as):
    from tests.test_work_package_writer import _user

    org = make_org("rule-total")
    user = _user(db_session, org, "ruletotal", role="enterprise_architect")
    for _ in range(3):
        _gap(db_session, org, priority="low", severity="low")
    login_as(client, user)
    body = client.get("/implementation/api/gaps?limit=2").get_json()
    assert len(body["gaps"]) == 2
    assert body["total"] == 3


def test_every_roadmap_write_route_refuses_a_viewer(db_session, make_org, client, login_as, app):
    from app.models.user import Role, User

    org = make_org("rule-viewer")
    Role.insert_roles()
    viewer = User(
        email="viewer-%s@example.com" % uuid.uuid4().hex[:8], first_name="View", last_name="Er",
        organization_id=org.id, confirmed=True, enterprise_role="viewer",
    )
    viewer.role = Role.query.filter_by(name="Viewer").one()
    db_session.add(viewer)
    db_session.flush()
    login_as(client, viewer)
    writes = [
        (rule.rule.replace("<int:work_package_id>", "1").replace("<int:deliverable_id>", "1").replace("<int:gap_id>", "1"), method)
        for rule in app.url_map.iter_rules()
        if rule.endpoint.startswith("roadmap_api.")
        for method in rule.methods & {"POST", "PUT", "PATCH", "DELETE"}
    ]
    assert len(writes) >= 12
    refused = {(path, method): client.open(path, method=method, json={}).status_code for path, method in writes}
    assert {k: v for k, v in refused.items() if v != 403} == {}


def test_no_page_script_reads_a_gap_rating_from_the_record_itself():
    """Templates and scripts show the rating the server sends (`severity` already holds the one rule); a page
    that decides a register gap's rating from its own `priority` or from the raw record can disagree."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent / "app"
    direct = re.compile(r"\bgap\.priority\b(?!\s*(?:\|\||\?\?|!==|\)|,))|\bgap\[['\"]priority['\"]\]")
    # these hold a different kind of record called a gap, or fill the edit form from the record
    allowed = {
        "templates/solutions/partials/_business_elements.html": "a journey gap's rank number, not a register rating",
        "static/js/capability_map/index.js": "fills the Edit Gap form from the record",
        "templates/capability_roadmap/capability_roadmap.html": "fills the gap form from the record",
    }
    offenders = []
    for path in list(root.rglob("*.html")) + list(root.rglob("*.js")):
        rel = path.relative_to(root).as_posix()
        if rel in allowed or "/vendor/" in rel or "/bundles/" in rel:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if direct.search(line):
                offenders.append("%s:%d" % (rel, number))
    assert offenders == [], "show a gap's rating from rated_severity or the server's severity: %s" % offenders


def test_another_organisations_gap_answers_not_found_on_get_put_and_delete(db_session, make_org, client, login_as):
    mine = _editor(db_session, make_org, client, login_as, "scope-mine")
    theirs = make_org("scope-theirs")
    foreign = _gap(db_session, theirs, priority="medium", severity="medium")
    foreign_id = foreign.id
    url = "/capability-map/api/roadmap/gaps/%d" % foreign_id

    assert client.get(url).status_code == 404
    assert client.put(url, json={"description": "Taken over", "priority": "critical"}).status_code == 404
    assert client.delete(url).status_code == 404

    from app import db

    kept = db.session.execute(
        db.text("SELECT priority, severity, organization_id FROM gaps WHERE id = :id"), {"id": foreign_id}
    ).one()
    assert tuple(kept) == ("medium", "medium", theirs.id)
    assert mine.id != theirs.id


def test_a_severity_is_stored_plain_and_one_outside_the_four_is_refused(db_session, make_org, client, login_as):
    from app.models.implementation_migration import Gap
    from app.services.gap_register_service import count_gaps, normalise_severity

    assert normalise_severity("Critical") == "critical"
    assert normalise_severity(" low ") == "low"
    assert normalise_severity("  ") is None
    with pytest.raises(ValueError):
        normalise_severity("urgent")

    org = _editor(db_session, make_org, client, login_as, "sev-words")
    gap = _gap(db_session, org, priority="medium", severity="medium")
    url = "/capability-map/api/roadmap/gaps/%d" % gap.id

    assert client.put(url, json={"severity": "Critical"}).status_code == 200
    db_session.expire_all()
    stored = Gap.query.filter_by(id=gap.id).one()
    assert stored.severity == "critical"
    assert count_gaps(org.id, severity="critical") == 1

    assert client.put(url, json={"severity": " low "}).status_code == 200
    db_session.expire_all()
    assert Gap.query.filter_by(id=gap.id).one().severity == "low"

    assert client.put(url, json={"severity": "urgent"}).status_code == 400
    db_session.expire_all()
    assert Gap.query.filter_by(id=gap.id).one().severity == "low"


def test_an_explicit_severity_wins_over_a_critical_priority(db_session, make_org, client, login_as):
    from app.models.implementation_migration import Gap
    from app.services.gap_register_service import count_gaps, effective_severity

    org = _editor(db_session, make_org, client, login_as, "sev-wins")
    gap = _gap(db_session, org, priority="critical", severity="critical")
    assert client.put("/capability-map/api/roadmap/gaps/%d" % gap.id, json={"severity": "low"}).status_code == 200
    db_session.expire_all()
    stored = Gap.query.filter_by(id=gap.id).one()
    assert effective_severity(stored) == "low"
    assert count_gaps(org.id, severity="critical") == 0


EDIT_URL = "/architecture/implementation/Gap/%d/edit"


def test_the_gap_edit_page_saves_a_new_priority_and_keeps_the_rating_on_a_description_edit(
    db_session, make_org, client, login_as
):
    from app.models.implementation_migration import Gap
    from app.services.gap_register_service import count_gaps

    org = _editor(db_session, make_org, client, login_as, "edit-page")
    gap = _gap(db_session, org, priority="medium", severity="critical")

    reply = client.post(EDIT_URL % gap.id, json={
        "name": gap.name, "description": "Only the words changed", "priority": "critical", "resolution_status": "",
    })
    assert reply.status_code == 200 and reply.get_json()["success"] is True
    db_session.expire_all()
    stored = Gap.query.filter_by(id=gap.id).one()
    assert (stored.description, stored.priority, stored.severity) == ("Only the words changed", "medium", "critical")
    assert count_gaps(org.id, severity="critical") == 1

    assert client.post(EDIT_URL % gap.id, json={"name": gap.name, "priority": " Low "}).status_code == 200
    db_session.expire_all()
    stored = Gap.query.filter_by(id=gap.id).one()
    assert (stored.priority, stored.severity) == ("low", "low")
    assert count_gaps(org.id, severity="critical") == 0

    assert client.post(EDIT_URL % gap.id, json={"name": gap.name, "priority": "urgent"}).status_code == 400
    db_session.expire_all()
    assert Gap.query.filter_by(id=gap.id).one().priority == "low"


def test_another_organisations_gap_answers_not_found_on_its_edit_page_and_save(db_session, make_org, client, login_as):
    _editor(db_session, make_org, client, login_as, "edit-mine")
    theirs = make_org("edit-theirs")
    foreign = _gap(db_session, theirs, priority="medium", severity="medium")
    foreign_id = foreign.id

    assert client.get(EDIT_URL % foreign_id).status_code == 404
    assert client.post(EDIT_URL % foreign_id, json={"name": "Taken", "priority": "critical"}).status_code == 404

    from app import db

    kept = db.session.execute(
        db.text("SELECT name, priority, severity FROM gaps WHERE id = :id"), {"id": foreign_id}
    ).one()
    assert kept[1:] == ("medium", "medium") and kept[0] != "Taken"


def test_gap_lists_filter_and_order_by_the_rule_not_by_the_priority_word(db_session, make_org, client, login_as):
    from app.modules.architecture.services.gap_archimate_service import GapArchiMateService
    from tests.test_work_package_writer import _user

    org, other = make_org("rule-lists"), make_org("rule-lists-other")
    user = _user(db_session, org, "rulelists", role="enterprise_architect")
    by_severity = _gap(db_session, org, priority="high", severity="critical")
    by_priority = _gap(db_session, org, priority="critical", severity="medium")
    plain_low = _gap(db_session, org, priority="low", severity="low")
    plain_medium = _gap(db_session, org, priority="medium", severity="medium")
    _gap(db_session, other, priority="critical", severity="critical")
    login_as(client, user)

    def page_listing(**args):
        with mock.patch("app.implementation_planning.routes.render_template", return_value="ok") as render:
            client.get("/implementation/gaps", query_string=args)
        return [g.id for g in render.call_args.kwargs["gaps"].items]

    assert set(page_listing(priority="critical")) == {by_severity.id, by_priority.id}
    assert set(page_listing()[:2]) == {by_severity.id, by_priority.id}
    assert page_listing()[2:] == [plain_medium.id, plain_low.id]

    roadmap = GapArchiMateService()
    mine = {by_severity.id, by_priority.id, plain_low.id, plain_medium.id}
    assert {g.id for g in roadmap.get_gaps_for_roadmap({"priority": "critical"})} & mine == {by_severity.id, by_priority.id}
    ordered = [g.id for g in roadmap.get_gaps_for_roadmap() if g.id in mine]
    assert set(ordered[:2]) == {by_severity.id, by_priority.id} and ordered[2:] == [plain_medium.id, plain_low.id]
