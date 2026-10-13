"""Onboarding writes goals as tenant-scoped ArchiMate Goal elements and changes as work packages."""
import pytest

from app.modules.onboarding.services import goals_changes as gc

pytestmark = pytest.mark.usefixtures("db_session")


def test_a_goal_becomes_a_tenant_scoped_archimate_goal_and_no_global_goal_row(db_session, make_org, tenant_ctx):
    from app.models.archimate_core import ArchiMateElement
    from app.models.motivation import Goal

    org = make_org("goal")
    goals_before = Goal.query.count()
    with tenant_ctx(org.id):
        result = gc.save([{"name": "First ten customers", "by_when": "2027-06-30", "measure": "10 signed"}], [], org=org)
        element = ArchiMateElement.query.filter_by(name="First ten customers").one()

    assert result == {"goals": 1, "changes": 0, "links": 0}
    assert (element.type, element.layer, element.organization_id) == ("Goal", "Motivation", org.id)
    assert element.custom_properties["target_date"] == "2027-06-30" and element.custom_properties["measure"] == "10 signed"
    assert Goal.query.count() == goals_before, "the goals table has no tenant column; onboarding must not write to it"


def test_a_change_becomes_a_work_package_with_its_element_and_a_link_to_its_goal(db_session, make_org, tenant_ctx):
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.unified_work_package import UnifiedWorkPackage as WorkPackage

    org = make_org("change")
    with tenant_ctx(org.id):
        result = gc.save(
            [{"name": "Grow revenue"}],
            [{"name": "Launch sign-up", "status": "in_progress", "by_when": "2027-01-15", "goal": "Grow revenue"}],
            org=org,
        )
        work = WorkPackage.query.filter_by(name="Launch sign-up").one()
        element = db_session.get(ArchiMateElement, work.archimate_element_id)
        goal = ArchiMateElement.query.filter_by(name="Grow revenue").one()
        link = ArchiMateRelationship.query.filter_by(source_id=element.id, target_id=goal.id).one()

    assert result == {"goals": 1, "changes": 1, "links": 1}
    assert work.status == "in_progress" and work.end_date.date().isoformat() == "2027-01-15"
    assert element.type == "WorkPackage" and element.organization_id == org.id
    assert link.type == "realization" and link.organization_id == org.id


def test_saving_twice_reuses_rows(db_session, make_org, tenant_ctx):
    from app.models.archimate_core import ArchiMateElement
    from app.models.unified_work_package import UnifiedWorkPackage as WorkPackage

    org = make_org("twice")
    payload = ([{"name": "Grow revenue"}], [{"name": "Launch sign-up", "goal": "Grow revenue"}])
    with tenant_ctx(org.id):
        gc.save(*payload, org=org)
        again = gc.save(*payload, org=org)
        assert ArchiMateElement.query.filter_by(name="Grow revenue").count() == 1
        assert WorkPackage.query.filter_by(name="Launch sign-up").count() == 1

    assert again["links"] == 1


def test_blank_values_do_not_erase_what_is_already_recorded(db_session, make_org, tenant_ctx):
    from app.models.archimate_core import ArchiMateElement

    org = make_org("keep")
    with tenant_ctx(org.id):
        gc.save([{"name": "Grow revenue", "by_when": "2027-06-30", "measure": "2x"}], [], org=org)
        gc.save([{"name": "Grow revenue", "by_when": "", "measure": ""}], [], org=org)
        props = ArchiMateElement.query.filter_by(name="Grow revenue").one().custom_properties

    assert props["target_date"] == "2027-06-30" and props["measure"] == "2x"


def test_bad_dates_statuses_and_unnamed_entries_are_ignored(db_session, make_org, tenant_ctx):
    from app.models.unified_work_package import UnifiedWorkPackage as WorkPackage

    org = make_org("bad")
    with tenant_ctx(org.id):
        result = gc.save(
            [{"name": "  "}, {"name": "Goal", "by_when": "soon"}],
            [{"name": ""}, {"name": "Thing", "status": "bogus", "by_when": "never"}],
            org=org,
        )
        work = WorkPackage.query.filter_by(name="Thing").one()

    assert result["goals"] == 1 and result["changes"] == 1
    assert work.status == "planned" and work.end_date is None


def test_a_change_naming_an_unknown_goal_is_saved_unlinked(db_session, make_org, tenant_ctx):
    org = make_org("unlinked")
    with tenant_ctx(org.id):
        result = gc.save([], [{"name": "Thing", "goal": "No such goal"}], org=org)

    assert result == {"goals": 0, "changes": 1, "links": 0}


def test_read_returns_what_was_saved_including_the_goal_a_change_serves(db_session, make_org, tenant_ctx):
    org = make_org("read")
    with tenant_ctx(org.id):
        gc.save([{"name": "Grow revenue", "by_when": "2027-06-30"}], [{"name": "Launch", "status": "completed", "goal": "Grow revenue"}], org=org)
        view = gc.read(org.id)

    assert view["goals"][0]["name"] == "Grow revenue" and view["goals"][0]["by_when"] == "2027-06-30"
    assert view["changes"][0]["status"] == "completed" and view["changes"][0]["goal"] == "Grow revenue"


def test_two_organisations_never_see_each_others_goals_or_changes(db_session, make_org, tenant_ctx):
    a, b = make_org("a"), make_org("b")
    with tenant_ctx(a.id):
        gc.save([{"name": "A goal"}], [{"name": "A change", "goal": "A goal"}], org=a)
    with tenant_ctx(b.id):
        gc.save([{"name": "B goal"}], [{"name": "B change"}], org=b)
        b_view = gc.read(b.id)
    with tenant_ctx(a.id):
        a_view = gc.read(a.id)

    assert [g["name"] for g in b_view["goals"]] == ["B goal"] and [c["name"] for c in b_view["changes"]] == ["B change"]
    assert [g["name"] for g in a_view["goals"]] == ["A goal"] and [c["name"] for c in a_view["changes"]] == ["A change"]
