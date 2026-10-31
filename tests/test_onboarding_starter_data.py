"""The industry and frameworks chosen at sign-up load what they name, for that organisation only.

Two real organisations in every test: what one person chooses is on their own organisation and on
no other. The load runs as a background job the choices queue; these tests run that job directly.
"""

import uuid

import pytest

from app.models.business_capabilities import BusinessCapability
from app.models.regulatory_framework import FrameworkAdoption
from app.modules.account.services import onboarding_service as onboarding

pytestmark = pytest.mark.usefixtures("db_session")


def _owner(db_session, org):
    from app.models.org_role import OrgRole
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(
        email="owner.%s@example.test" % uuid.uuid4().hex[:8], first_name="Own", last_name="Er",
        organization_id=org.id, enterprise_role="solution_architect", confirmed=True,
    )
    user.role = Role.query.filter_by(name="Architect").one()
    user.password = "Sup3r-Secret!pass"
    db_session.add(user)
    db_session.flush()
    OrgRole.set_role(org.id, user.id, "org_admin", granted_by_id=user.id)
    db_session.flush()
    return user


@pytest.fixture
def two(db_session, make_org):
    one, other = make_org("starter-one"), make_org("starter-two")
    return {"one": one, "two": other, "one_owner": _owner(db_session, one), "two_owner": _owner(db_session, other)}


def _choose(client, login_as, user, industry, frameworks):
    login_as(client, user)
    reply = client.post("/account/welcome/choices", json={"industry": industry, "frameworks": frameworks})
    assert reply.status_code == 200, reply.get_data(as_text=True)
    onboarding.run_starter_data(user.organization_id)
    return reply


def _capabilities(org):
    """The organisation's capability rows, read without the signed-in person's own scope so one
    organisation's rows can be checked while another is signed in."""
    from app import db

    return db.session.execute(
        db.text("SELECT id, name, level, discovery_source, parent_capability_id FROM business_capability "
                "WHERE organization_id = :org"), {"org": org.id},
    ).all()


def _adoptions(org):
    from app import db

    return db.session.execute(
        db.text("SELECT f.code, a.source, a.id FROM framework_adoptions a JOIN regulatory_frameworks f "
                "ON f.id = a.framework_id WHERE a.organization_id = :org"), {"org": org.id},
    ).all()


def test_banking_loads_the_bian_starter_capabilities_marked_as_starter(client, login_as, two):
    _choose(client, login_as, two["one_owner"], "banking", [])
    rows = _capabilities(two["one"])
    names = {row.name for row in rows}
    assert {"Customer Offer", "Payment Order", "Current Account"} <= names
    assert {row.discovery_source for row in rows} == {"starter:BIAN"}
    areas = {row.name: row for row in rows if row.level == 1}
    assert areas, "the business areas are the top level"
    child = next(row for row in rows if row.name == "Payment Order")
    assert child.parent_capability_id in {row.id for row in areas.values()}


def test_software_loads_the_apqc_starter_capabilities(client, login_as, two):
    _choose(client, login_as, two["one_owner"], "software", [])
    rows = _capabilities(two["one"])
    names = {row.name for row in rows}
    assert "Develop Vision and Strategy" in names and "Develop business strategy" in names
    assert {row.discovery_source for row in rows} == {"starter:APQC"}
    assert "Customer Offer" not in names


def test_choosing_nothing_loads_nothing(client, login_as, two):
    _choose(client, login_as, two["one_owner"], "", [])
    assert _capabilities(two["one"]) == []
    assert len(_adoptions(two["one"])) == 0


def test_iso_27001_is_adopted_with_its_controls_and_a_source(client, login_as, two):
    _choose(client, login_as, two["one_owner"], "", ["iso27001"])
    adoptions = FrameworkAdoption.query.filter_by(organization_id=two["one"].id).all()
    assert [a.framework.code for a in adoptions] == ["ISO - 27001"]
    assert adoptions[0].source == "starter:ISO 27001"
    assert adoptions[0].adopted_controls.count() == adoptions[0].framework.controls.count() > 0


def test_frameworks_without_a_catalogue_are_not_offered():
    offered = {key for key, _label, _note in onboarding.FRAMEWORKS}
    assert offered == {"iso27001", "soc2", "gdpr"}
    assert set(onboarding.suggested_frameworks("enterprise", "audit")) <= offered
    with pytest.raises(ValueError):
        onboarding.save_choices(None, None, ["togaf"])


def test_two_organisations_get_only_their_own_rows(client, login_as, two):
    _choose(client, login_as, two["one_owner"], "banking", ["iso27001"])
    _choose(client, login_as, two["two_owner"], "software", ["gdpr"])
    one, other = _capabilities(two["one"]), _capabilities(two["two"])
    assert {r.discovery_source for r in one} == {"starter:BIAN"}
    assert {r.discovery_source for r in other} == {"starter:APQC"}
    assert not {r.id for r in one} & {r.id for r in other}
    assert {a.code for a in _adoptions(two["one"])} == {"ISO - 27001"}
    assert {a.code for a in _adoptions(two["two"])} == {"GDPR"}


def test_saving_the_same_choices_again_adds_nothing(client, login_as, two):
    _choose(client, login_as, two["one_owner"], "banking", ["iso27001"])
    first = (len(_capabilities(two["one"])), len(_adoptions(two["one"])))
    _choose(client, login_as, two["one_owner"], "banking", ["iso27001"])
    again = (len(_capabilities(two["one"])), len(_adoptions(two["one"])))
    assert first == again and first[0] > 0 and first[1] == 1


def test_saving_queues_the_load_and_never_waits_for_it(client, login_as, two):
    from app.models.job import Job

    login_as(client, two["one_owner"])
    client.post("/account/welcome/choices", json={"industry": "banking", "frameworks": ["soc2"]})
    jobs = Job.query.filter_by(task=onboarding.STARTER_TASK).all()
    assert [j.payload["organization_id"] for j in jobs] == [two["one"].id]
    assert _capabilities(two["one"]) == [], "nothing is loaded inside the request"


def test_changing_the_industry_adds_the_new_set_and_offers_to_remove_unedited_old_rows(client, login_as, two):
    _choose(client, login_as, two["one_owner"], "banking", [])
    edited = BusinessCapability.query.filter_by(organization_id=two["one"].id, name="Payment Order").one()
    edited.business_owner = "Priya Nair"
    from app import db

    db.session.commit()
    _choose(client, login_as, two["one_owner"], "software", [])
    sources = {r.discovery_source for r in _capabilities(two["one"])}
    assert sources == {"starter:BIAN", "starter:APQC"}, "the old rows stay until the person agrees"
    offer = onboarding.removable_starter_rows(two["one"])
    assert [(o["model"], o["count"]) for o in offer] == [("BIAN", offer[0]["count"])] and offer[0]["count"] > 0

    login_as(client, two["one_owner"])
    removed = client.post("/account/welcome/starter/remove", json={"model": "BIAN"})
    assert removed.status_code == 200 and removed.get_json()["removed"] == offer[0]["count"]
    left = {r.name for r in _capabilities(two["one"]) if r.discovery_source == "starter:BIAN"}
    assert "Payment Order" in left, "a row the person changed is never removed"
    assert "Current Account" not in left


def test_another_organisation_cannot_remove_my_starter_rows(client, login_as, two):
    _choose(client, login_as, two["one_owner"], "banking", [])
    login_as(client, two["two_owner"])
    client.post("/account/welcome/starter/remove", json={"model": "BIAN"})
    assert len(_capabilities(two["one"])) > 0
