"""The one prioritised action list: ordering, tenancy, rule sentence, bounded queries."""

from __future__ import annotations

import uuid
from datetime import date, datetime

import pytest
from sqlalchemy import event

from app.services import priority_action_service as svc

pytestmark = pytest.mark.usefixtures("db_session")

TODAY = date(2026, 10, 17)


def _gap(db_session, org, name, severity="medium", status="identified"):
    from app.models.implementation_migration import Gap

    gap = Gap(name=name, severity=severity, resolution_status=status, organization_id=org.id)
    db_session.add(gap)
    db_session.flush()
    if severity is None:  # the column default would otherwise record "medium" on insert
        gap.severity = None
        db_session.flush()
    return gap


def _wp(db_session, org, name, end=None, progress=0.0, status="in_progress", priority="medium"):
    from app.models.unified_work_package import UnifiedWorkPackage

    wp = UnifiedWorkPackage(
        name=name, organization_id=org.id, status=status, priority=priority,
        progress_percentage=progress, end_date=end,
    )
    db_session.add(wp)
    db_session.flush()
    if progress is None:  # the column default would otherwise record 0 on insert
        wp.progress_percentage = None
        db_session.flush()
    return wp


def _risk(db_session, org, title, likelihood, impact, status=None):
    from app.models.risk import Risk, RiskStatus

    risk = Risk(
        title=title, likelihood=likelihood, impact=impact, organization_id=org.id,
        status=status or RiskStatus.OPEN,
    )
    db_session.add(risk)
    db_session.flush()
    return risk


def _names(result):
    return [r["name"] for r in result["rows"]]


def test_order_across_the_three_kinds_with_ties(db_session, make_org, tenant_ctx):
    org = make_org("a")
    with tenant_ctx(org.id):
        _gap(db_session, org, "Zeta gap", "critical")
        _gap(db_session, org, "Alpha gap", "critical")
        _gap(db_session, org, "Beta gap", "high")
        _wp(db_session, org, "Late critical work", end=datetime(2026, 10, 7), progress=40, priority="critical")
        _wp(db_session, org, "Later critical work", end=datetime(2026, 9, 17), progress=10, priority="critical")
        _wp(db_session, org, "Late high work", end=datetime(2026, 10, 1), progress=58, priority="high")
        _risk(db_session, org, "Critical risk", 4, 5)
        _risk(db_session, org, "High risk", 3, 4)
        result = svc.prioritised_actions(org.id, today=TODAY)
    assert _names(result) == [
        # critical: gaps with no work (by name), then work (most overdue first), then risks
        "Alpha gap", "Zeta gap", "Later critical work", "Late critical work", "Critical risk",
        # high: gap, work, risk
        "Beta gap", "Late high work", "High risk",
    ]
    assert [r["position"] for r in result["rows"]] == list(range(1, 9))
    assert result["total"] == 8


def test_row_wording_and_em_dash_for_unrecorded_values(db_session, make_org, tenant_ctx):
    org = make_org("a")
    with tenant_ctx(org.id):
        _gap(db_session, org, "Data quality gap", "medium")
        _wp(db_session, org, "Deploy mobile field app", end=datetime(2026, 10, 7), progress=58, priority="high")
        _wp(db_session, org, "No progress recorded", end=datetime(2026, 10, 7), progress=None, priority="high")
        result = svc.prioritised_actions(org.id, today=TODAY)
    by_name = {r["name"]: r for r in result["rows"]}
    assert by_name["Data quality gap"]["action"] == "Link a work package to the Data quality gap"
    assert by_name["Deploy mobile field app"]["action"] == (
        "Finish Deploy mobile field app: due 7 Oct, 58 percent done"
    )
    assert "10 days overdue" in by_name["Deploy mobile field app"]["why"]
    assert "— percent done" in by_name["No progress recorded"]["action"]


def test_unmeasurable_severity_sorts_last_and_says_why(db_session, make_org, tenant_ctx):
    org = make_org("a")
    with tenant_ctx(org.id):
        _gap(db_session, org, "Aardvark gap", severity=None)
        _gap(db_session, org, "Low gap", severity="low")
        result = svc.prioritised_actions(org.id, today=TODAY)
    assert _names(result) == ["Low gap", "Aardvark gap"]
    last = result["rows"][-1]
    assert last["severity"] is None
    assert "No severity is recorded" in last["why"]


def test_work_package_states(db_session, make_org, tenant_ctx):
    org = make_org("a")
    with tenant_ctx(org.id):
        _wp(db_session, org, "Done and late", end=datetime(2026, 1, 1), progress=100, status="completed")
        _wp(db_session, org, "Not due yet", end=datetime(2026, 12, 1), progress=10)
        _wp(db_session, org, "Parked", status="on_hold", progress=20)
        result = svc.prioritised_actions(org.id, today=TODAY)
    assert _names(result) == ["Parked"]
    assert result["rows"][0]["action"] == "Restart Parked: on hold at 20 percent done"


def test_blocked_by_another_programme_is_listed(db_session, make_org, tenant_ctx):
    from app.models.vendor.vendor_organization import EnterpriseInitiative
    from app.services import work_package_service

    org = make_org("a")
    with tenant_ctx(org.id):
        first = EnterpriseInitiative(name="P1 %s" % uuid.uuid4().hex[:6], organization_id=org.id)
        second = EnterpriseInitiative(name="P2 %s" % uuid.uuid4().hex[:6], organization_id=org.id)
        db_session.add_all([first, second])
        db_session.flush()
        waiting = _wp(db_session, org, "Waiting work", end=datetime(2027, 1, 1))
        blocker = _wp(db_session, org, "Blocking work", end=datetime(2027, 1, 1))
        waiting.enterprise_initiative_id, blocker.enterprise_initiative_id = first.id, second.id
        work_package_service.add_dependency(waiting.id, blocker.id, organization_id=org.id)
        db_session.flush()
        result = svc.prioritised_actions(org.id, today=TODAY)
    assert _names(result) == ["Waiting work"]
    assert result["rows"][0]["action"] == "Unblock Waiting work: waiting on Blocking work"


def test_covered_gaps_follow_uncovered_ones_and_closed_gaps_drop_out(db_session, make_org, tenant_ctx):
    from app.services import work_package_service

    org = make_org("a")
    with tenant_ctx(org.id):
        uncovered = _gap(db_session, org, "Uncovered gap", "high")
        covered = _gap(db_session, org, "Covered gap", "high")
        _gap(db_session, org, "Closed gap", "critical", status="resolved")
        work_package_service.create_work_package(
            organization_id=org.id, name="Linked wp", gap_id=covered.id)
        result = svc.prioritised_actions(org.id, today=TODAY)
    assert _names(result) == [uncovered.name, covered.name]
    assert result["rows"][1]["action"].startswith("Close the Covered gap")


def test_unlinking_through_the_writer_uncovers_the_gap(db_session, make_org, tenant_ctx):
    from app.services import work_package_service

    org = make_org("a")
    with tenant_ctx(org.id):
        gap = _gap(db_session, org, "Gap to unlink", "high")
        wp = work_package_service.create_work_package(
            organization_id=org.id, name="Linked wp", gap_id=gap.id)
        linked = svc.prioritised_actions(org.id, today=TODAY)
        work_package_service.update_work_package(wp.id, organization_id=org.id, gap_id=None)
        unlinked = svc.prioritised_actions(org.id, today=TODAY)
    assert linked["rows"][0]["action"].startswith("Close the Gap to unlink")
    assert unlinked["rows"][0]["action"].startswith("Link a work package to the Gap to unlink")


def test_another_organisations_link_does_not_cover_a_gap(db_session, make_org, tenant_ctx):
    from app.services import work_package_service

    org, other = make_org("a"), make_org("b")
    with tenant_ctx(other.id):
        foreign_gap = _gap(db_session, other, "Foreign gap", "high")
        work_package_service.create_work_package(
            organization_id=other.id, name="Foreign wp", gap_id=foreign_gap.id)
    with tenant_ctx(org.id):
        own = _gap(db_session, org, "Own gap", "high")
        result = svc.prioritised_actions(org.id, today=TODAY)
    assert _names(result) == [own.name]
    assert result["rows"][0]["action"].startswith("Link a work package to the Own gap")


@pytest.mark.parametrize("done", ["Completed", "completed", "Cancelled", "Canceled"])
def test_form_spellings_of_finished_work_are_not_listed(db_session, make_org, tenant_ctx, done):
    org = make_org("a")
    with tenant_ctx(org.id):
        _wp(db_session, org, "Finished late", end=datetime(2026, 1, 1), progress=10, status=done)
        result = svc.prioritised_actions(org.id, today=TODAY)
    assert result["rows"] == []


@pytest.mark.parametrize("spelling", ["On Hold", "on-hold", "ON HOLD", "on_hold"])
def test_form_spellings_of_on_hold_are_listed(db_session, make_org, tenant_ctx, spelling):
    org = make_org("a")
    with tenant_ctx(org.id):
        _wp(db_session, org, "Parked", status=spelling, progress=20)
        result = svc.prioritised_actions(org.id, today=TODAY)
    assert _names(result) == ["Parked"]
    assert result["rows"][0]["action"] == "Restart Parked: on hold at 20 percent done"
    assert "on hold" in result["rows"][0]["why"]


def test_a_completed_dependency_does_not_block(db_session, make_org, tenant_ctx):
    from app.models.vendor.vendor_organization import EnterpriseInitiative
    from app.services import work_package_service

    org = make_org("a")
    with tenant_ctx(org.id):
        first = EnterpriseInitiative(name="P1 %s" % uuid.uuid4().hex[:6], organization_id=org.id)
        second = EnterpriseInitiative(name="P2 %s" % uuid.uuid4().hex[:6], organization_id=org.id)
        db_session.add_all([first, second])
        db_session.flush()
        waiting = _wp(db_session, org, "Waiting work", end=datetime(2027, 1, 1))
        done = _wp(db_session, org, "Done work", end=datetime(2027, 1, 1), status="Completed")
        waiting.enterprise_initiative_id, done.enterprise_initiative_id = first.id, second.id
        work_package_service.add_dependency(waiting.id, done.id, organization_id=org.id)
        db_session.flush()
        result = svc.prioritised_actions(org.id, today=TODAY)
    assert result["rows"] == []


def test_only_open_risks_and_at_most_five(db_session, make_org, tenant_ctx):
    from app.models.risk import RiskStatus

    org = make_org("a")
    with tenant_ctx(org.id):
        for index in range(7):
            _risk(db_session, org, "Risk %d" % index, 3, 3 + (index % 2))
        _risk(db_session, org, "Mitigated risk", 5, 5, status=RiskStatus.MITIGATED)
        result = svc.prioritised_actions(org.id, today=TODAY)
    assert len(result["rows"]) == svc.RISK_LIMIT
    assert "Mitigated risk" not in _names(result)


def test_another_organisations_items_never_appear(db_session, make_org, tenant_ctx):
    mine, theirs = make_org("mine"), make_org("theirs")
    with tenant_ctx(theirs.id):
        _gap(db_session, theirs, "Foreign gap", "critical")
        _wp(db_session, theirs, "Foreign work", end=datetime(2026, 1, 1), priority="critical")
        _risk(db_session, theirs, "Foreign risk", 5, 5)
    with tenant_ctx(mine.id):
        _gap(db_session, mine, "Own gap", "low")
        result = svc.prioritised_actions(mine.id, today=TODAY)
        # Even when asked with the other organisation's id inside this tenant context.
        leaked = svc.prioritised_actions(theirs.id, today=TODAY)
    assert _names(result) == ["Own gap"]
    assert not any("Foreign" in name for name in _names(result))
    assert not any(r["name"] == "Own gap" for r in leaked["rows"])


def test_statement_count_is_bounded_whatever_the_row_count(db_session, make_org, tenant_ctx):
    from app import db

    org = make_org("a")
    with tenant_ctx(org.id):
        for index in range(30):
            _gap(db_session, org, "Gap %02d" % index, "high")
            _wp(db_session, org, "Work %02d" % index, end=datetime(2026, 9, 1), priority="high")
            _risk(db_session, org, "Risk %02d" % index, 3, 3)
        statements = []

        def _count(conn, cursor, statement, *args):
            if statement.startswith("SELECT") and "set_config" not in statement:  # our reads only
                statements.append(statement)

        event.listen(db.engine, "before_cursor_execute", _count)
        try:
            result = svc.prioritised_actions(org.id, today=TODAY)
        finally:
            event.remove(db.engine, "before_cursor_execute", _count)
    assert result["total"] == 30 + 30 + svc.RISK_LIMIT
    assert len(statements) <= 5, statements  # gaps, their links, work packages, dependencies, risks


def test_rule_sentence_is_one_sentence():
    assert svc.RANKING_RULE.count(". ") == 0 and svc.RANKING_RULE.endswith(".")


@pytest.fixture
def _user_in(db_session, make_org):
    from app.models.user import Role, User

    def _make(org):
        Role.insert_roles()
        user = User(
            email="pa.%s@example.com" % uuid.uuid4().hex[:8], first_name="P", last_name="A",
            organization_id=org.id, enterprise_role="enterprise_architect", confirmed=True,
        )
        user.role = Role.query.filter_by(name="Architect").one()
        user.password = "Passw0rd!x1"
        db_session.add(user)
        db_session.flush()
        return user

    return _make


@pytest.mark.parametrize("path", ["/strategic/impact-analysis", "/enterprise/analysis/ai-architecture"])
def test_both_screens_render_the_rule_and_only_own_rows(path, client, login_as, db_session, make_org, _user_in):
    mine, theirs = make_org("mine"), make_org("theirs")
    _gap(db_session, mine, "Own visible gap", "critical")
    _gap(db_session, theirs, "Foreign hidden gap", "critical")
    user = _user_in(mine)
    login_as(client, user)
    response = client.get(path)
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert svc.RANKING_RULE in body
    assert "Link a work package to the Own visible gap" in body
    assert "Foreign hidden gap" not in body


def test_foreign_gap_record_answers_404(client, login_as, db_session, make_org, _user_in):
    mine, theirs = make_org("mine"), make_org("theirs")
    foreign = _gap(db_session, theirs, "Foreign hidden gap", "critical")
    login_as(client, _user_in(mine))
    foreign_id = foreign.id
    db_session.expunge_all()  # .get() answers from the identity map on a hit, which a request never has
    response = client.get("/architecture/implementation/Gap/%d" % foreign_id)
    assert response.status_code == 404
