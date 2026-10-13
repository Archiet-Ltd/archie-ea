"""Onboarding writes people/teams and their capability assignments to the real tables."""
import pytest

from app.modules.onboarding.services import capabilities as caps
from app.modules.onboarding.services import people

pytestmark = pytest.mark.usefixtures("db_session")

STAGE, BAND = "early_revenue", "micro"


def _with_capability(key="customer_acquisition"):
    caps.save([{"key": key, "maturity": 2}], stage=STAGE, size_band=BAND)


def _person(name="Priya", **overrides):
    entry = {"name": name, "kind": "person", "assignments": [{"key": "customer_acquisition", "role": "R", "proficiency": 3}]}
    entry.update(overrides)
    return entry


def test_a_person_becomes_a_business_actor_with_an_archimate_element(db_session, make_org, tenant_ctx):
    from app.models.archimate_core import ArchiMateElement
    from app.models.business_layer import BusinessActor

    org = make_org("actor")
    with tenant_ctx(org.id):
        _with_capability()
        result = people.save([_person()], stage=STAGE, size_band=BAND)
        actor = BusinessActor.query.filter_by(name="Priya").one()
        element = db_session.get(ArchiMateElement, actor.archimate_element_id)

    assert result == {"people": 1, "assignments": 1}
    assert actor.actor_type == "Individual" and actor.headcount == 1 and actor.organization_id == org.id
    assert element is not None and element.organization_id == org.id


def test_the_assignment_is_a_real_raci_cell_on_the_unified_capability(db_session, make_org, tenant_ctx):
    from app.models.business_capabilities import BusinessCapability
    from app.models.organization_model import CapabilityProficiency, EnterpriseRaciAssignment
    from app.models.unified_capability import UnifiedCapability

    org = make_org("raci")
    with tenant_ctx(org.id):
        _with_capability()
        people.save([_person()], stage=STAGE, size_band=BAND)
        source = BusinessCapability.query.filter_by(name="Customer acquisition").one()
        unified = UnifiedCapability.query.filter_by(source_table="business_capability", source_id=str(source.id)).one()
        cell = EnterpriseRaciAssignment.query.one()
        proficiency = CapabilityProficiency.query.one()

    assert (cell.stakeholder_type, cell.raci, cell.stakeholder_name) == ("actor", "R", "Priya")
    assert cell.capability_id == unified.id and cell.organization_id == org.id
    assert proficiency.raci_assignment_id == cell.id and proficiency.level == 3 and proficiency.organization_id == org.id


def test_unrated_proficiency_is_not_stored(db_session, make_org, tenant_ctx):
    from app.models.organization_model import CapabilityProficiency

    org = make_org("unrated")
    with tenant_ctx(org.id):
        _with_capability()
        people.save([_person(assignments=[{"key": "customer_acquisition", "role": "R"}])], stage=STAGE, size_band=BAND)
        assert CapabilityProficiency.query.count() == 0


def test_a_team_carries_its_headcount(db_session, make_org, tenant_ctx):
    from app.models.business_layer import BusinessActor

    org = make_org("team")
    with tenant_ctx(org.id):
        _with_capability()
        people.save([_person("Support", kind="team", headcount=14)], stage=STAGE, size_band=BAND)
        actor = BusinessActor.query.filter_by(name="Support").one()

    assert actor.actor_type == "Team" and actor.headcount == 14


def test_a_team_without_a_headcount_does_not_invent_one(db_session, make_org, tenant_ctx):
    from app.models.business_layer import BusinessActor

    org = make_org("nohead")
    with tenant_ctx(org.id):
        _with_capability()
        people.save([_person("Support", kind="team", headcount="lots")], stage=STAGE, size_band=BAND)
        # The column's own default stores 0 for "unknown"; read() must show that as blank.
        assert not BusinessActor.query.filter_by(name="Support").one().headcount
        assert people.read(STAGE, BAND)["people"][0]["headcount"] is None


def test_saving_twice_updates_rather_than_duplicates(db_session, make_org, tenant_ctx):
    from app.models.business_layer import BusinessActor
    from app.models.organization_model import CapabilityProficiency, EnterpriseRaciAssignment

    org = make_org("twice")
    with tenant_ctx(org.id):
        _with_capability()
        people.save([_person()], stage=STAGE, size_band=BAND)
        people.save([_person(assignments=[{"key": "customer_acquisition", "role": "A", "proficiency": 4}])], stage=STAGE, size_band=BAND)
        assert BusinessActor.query.filter_by(name="Priya").count() == 1
        cell = EnterpriseRaciAssignment.query.one()
        assert cell.raci == "A" and CapabilityProficiency.query.one().level == 4


def test_a_null_role_clears_the_assignment_but_never_deletes_the_person(db_session, make_org, tenant_ctx):
    from app.models.business_layer import BusinessActor
    from app.models.organization_model import CapabilityProficiency, EnterpriseRaciAssignment

    org = make_org("clear")
    with tenant_ctx(org.id):
        _with_capability()
        people.save([_person()], stage=STAGE, size_band=BAND)
        people.save([_person(assignments=[{"key": "customer_acquisition", "role": None}])], stage=STAGE, size_band=BAND)
        assert EnterpriseRaciAssignment.query.count() == 0
        assert CapabilityProficiency.query.count() == 0
        assert BusinessActor.query.filter_by(name="Priya").count() == 1


def test_a_capability_that_is_not_recorded_yet_is_ignored(db_session, make_org, tenant_ctx):
    from app.models.organization_model import EnterpriseRaciAssignment

    org = make_org("notyet")
    with tenant_ctx(org.id):
        result = people.save([_person()], stage=STAGE, size_band=BAND)
        assert EnterpriseRaciAssignment.query.count() == 0

    assert result == {"people": 1, "assignments": 0}


def test_unnamed_entries_and_bad_values_are_ignored(db_session, make_org, tenant_ctx):
    from app.models.business_layer import BusinessActor
    from app.models.organization_model import CapabilityProficiency, EnterpriseRaciAssignment

    org = make_org("bad")
    with tenant_ctx(org.id):
        _with_capability()
        result = people.save(
            [
                {"name": "  ", "assignments": []},
                _person("Bad role", assignments=[{"key": "customer_acquisition", "role": "Z"}]),
                _person("Bad level", assignments=[{"key": "customer_acquisition", "role": "R", "proficiency": 9}]),
            ],
            stage=STAGE,
            size_band=BAND,
        )
        assert BusinessActor.query.count() == 2
        assert EnterpriseRaciAssignment.query.count() == 1
        assert CapabilityProficiency.query.count() == 0

    assert result["people"] == 2


def test_an_existing_department_cannot_be_renamed_through_this_screen(db_session, make_org, tenant_ctx):
    from app.models.business_layer import BusinessActor

    org = make_org("dept")
    with tenant_ctx(org.id):
        _with_capability()
        dept = BusinessActor(name="Finance Department", actor_type="Department")
        db_session.add(dept)
        db_session.flush()
        people.save([_person("Hijack", id=dept.id)], stage=STAGE, size_band=BAND)
        db_session.refresh(dept)
        assert dept.name == "Finance Department" and dept.actor_type == "Department"


def test_two_organisations_never_see_each_others_people(db_session, make_org, tenant_ctx):
    a, b = make_org("a"), make_org("b")
    with tenant_ctx(a.id):
        _with_capability()
        people.save([_person("Org A person")], stage=STAGE, size_band=BAND)
    with tenant_ctx(b.id):
        _with_capability()
        people.save(
            [_person("Org B person", assignments=[{"key": "customer_acquisition", "role": "C", "proficiency": 1}])],
            stage=STAGE,
            size_band=BAND,
        )
        view = people.read(STAGE, BAND)
    with tenant_ctx(a.id):
        a_view = people.read(STAGE, BAND)

    assert [p["name"] for p in view["people"]] == ["Org B person"]
    assert [p["name"] for p in a_view["people"]] == ["Org A person"]
    assert a_view["people"][0]["assignments"] == [{"key": "customer_acquisition", "role": "R", "proficiency": 3}]


def test_read_offers_only_capabilities_already_recorded_and_defaults_by_size(db_session, make_org, tenant_ctx):
    org = make_org("read")
    with tenant_ctx(org.id):
        _with_capability()
        view = people.read(STAGE, BAND)
        large = people.read("established", "large")

    assert [c["key"] for c in view["capabilities"]] == ["customer_acquisition"]
    assert view["default_kind"] == "person" and large["default_kind"] == "team"


def test_the_tell_us_more_team_section_creates_the_same_people_the_people_step_edits(db_session, make_org):
    from app.models.business_layer import BusinessActor
    from app.modules.onboarding.services import tell_us_more

    org = make_org("tum-team")
    tell_us_more.save_section(org, "team", {"people": [{"name": "Jo Lee", "does": "Sales"}, {"name": "  ", "does": "x"}]})
    tell_us_more.save_section(org, "team", {"people": [{"name": "Jo Lee", "does": "Sales"}]})

    actors = BusinessActor.query.filter_by(organization_id=org.id).all()
    assert [(a.name, a.actor_type, a.description) for a in actors] == [("Jo Lee", "Individual", "Sales")]


def test_the_team_section_never_touches_another_organisations_person(db_session, make_org):
    from app.models.business_layer import BusinessActor
    from app.modules.onboarding.services import tell_us_more

    a, b = make_org("tum-a"), make_org("tum-b")
    tell_us_more.save_section(a, "team", {"people": [{"name": "Jo Lee", "does": "Sales"}]})
    tell_us_more.save_section(b, "team", {"people": [{"name": "Jo Lee", "does": "Ops"}]})

    assert BusinessActor.query.filter_by(organization_id=a.id).one().description == "Sales"
    assert BusinessActor.query.filter_by(organization_id=b.id).one().description == "Ops"
