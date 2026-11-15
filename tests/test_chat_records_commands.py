"""The two commands that prepare an existing database for the chat's goal and risk records.

The report changes nothing. The backfill writes a goal's date from its one readable date property, only for the
organisation that owns the goal, and lists every goal it cannot read instead of guessing.
"""
import uuid
from datetime import date


def _goal_element(db_session, org_id, name, properties):
    from app.models.archimate_core import ArchiMateElement

    element = ArchiMateElement(name=name, type="Goal", layer="Motivation", organization_id=org_id,
                               custom_properties=properties)
    db_session.add(element)
    db_session.flush()
    return element


def _world(db_session, make_org):
    tag = uuid.uuid4().hex[:6]
    org_a, org_b = make_org("cmda"), make_org("cmdb")
    ok = _goal_element(db_session, org_a.id, "German market %s" % tag, {"target_date": "2027-04-30"})
    two = _goal_element(db_session, org_a.id, "Two dates %s" % tag, {"target_date": "2027-01-01", "due": "2027-02-02"})
    vague = _goal_element(db_session, org_a.id, "Vague %s" % tag, {"deadline": "soon"})
    other = _goal_element(db_session, org_b.id, "Other org %s" % tag, {"target_date": "2028-05-05"})
    return org_a, org_b, ok, two, vague, other


def test_the_report_changes_nothing_and_counts_each_organisation_separately(app, db_session, make_org):
    from app.commands.chat_records_report import chat_records_report, organisation_report
    from app.models.motivation import Goal

    org_a, org_b, *_ = _world(db_session, make_org)
    before = db_session.query(Goal).count()
    result = app.test_cli_runner().invoke(chat_records_report, ["--organization-id", str(org_a.id)])
    assert result.exit_code == 0, result.output
    assert "Organisation %d" % org_a.id in result.output and "Organisation %d" % org_b.id not in result.output
    assert "Goal elements: 3, with no goals row: 3, with a date-like property: 3" in result.output
    assert "property 'target_date' on 2 goal(s)" in result.output
    assert organisation_report(org_b.id)["goal_elements"] == 1
    assert db_session.query(Goal).count() == before


def test_the_backfill_writes_only_a_readable_single_date_and_lists_the_rest(app, db_session, make_org):
    from app.commands.backfill_goal_dates import backfill_goal_dates
    from app.services import goal_store

    org_a, org_b, ok, two, vague, other = _world(db_session, make_org)
    runner = app.test_cli_runner()

    dry = runner.invoke(backfill_goal_dates, ["--dry-run", "--organization-id", str(org_a.id)])
    assert dry.exit_code == 0, dry.output
    assert "would write goal %d" % ok.id in dry.output and "Would write 1, left 2" in dry.output
    assert goal_store.goal_for_element(org_a.id, ok.id) is None

    done = runner.invoke(backfill_goal_dates, ["--organization-id", str(org_a.id)])
    assert done.exit_code == 0, done.output
    assert "more than one date-like property" in done.output and "is not a date it can read" in done.output
    assert goal_store.goal_for_element(org_a.id, ok.id).target_date == date(2027, 4, 30)
    assert goal_store.goal_for_element(org_a.id, two.id) is None
    assert goal_store.goal_for_element(org_a.id, vague.id) is None
    assert goal_store.goal_for_element(org_b.id, other.id) is None, "another organisation's goal is not touched"
