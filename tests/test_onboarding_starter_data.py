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
    jobs = [j for j in Job.query.filter_by(task=onboarding.STARTER_TASK).all() if j.payload["organization_id"] == two["one"].id]
    assert len(jobs) == 1
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


def test_the_compliance_page_marks_only_the_frameworks_this_organisation_chose(client, login_as, two):
    _choose(client, login_as, two["one_owner"], "", ["iso27001"])
    _choose(client, login_as, two["two_owner"], "", ["gdpr"])
    page = client.get("/dashboard/compliance").get_data(as_text=True)
    assert page.count('data-testid="framework-adopted"') == 1
    assert 'data-source="starter:GDPR"' in page and "starter:ISO 27001" not in page


def test_the_controls_of_an_adopted_framework_load_from_the_address_the_page_asks(client, login_as, two):
    _choose(client, login_as, two["one_owner"], "", ["iso27001"])
    page = client.get("/dashboard/compliance").get_data(as_text=True)
    assert "/dashboard/api/compliance/frameworks/' + frameworkId" in page
    framework_id = _adoptions(two["one"])[0]
    from app import db

    listed = client.get("/dashboard/api/compliance/frameworks/%d/controls" % db.session.execute(
        db.text("SELECT id FROM regulatory_frameworks WHERE code = 'ISO - 27001'")).scalar_one())
    assert listed.status_code == 200 and {c["control_code"] for c in listed.get_json()} >= {"A.8.2", "A.8.24"}
    assert framework_id.source == "starter:ISO 27001"
    assert "Application control mappings" in page and "planned: 3" not in page


def test_removed_starter_rows_leave_the_capability_map_too(client, login_as, two):
    from app import db

    _choose(client, login_as, two["one_owner"], "banking", [])
    _choose(client, login_as, two["one_owner"], "software", [])
    def names():
        return {r[0] for r in db.session.execute(
            db.text("SELECT name FROM unified_capabilities WHERE organization_id = :o"), {"o": two["one"].id})}

    assert {"Sales and Service", "Develop Vision and Strategy"} <= names()
    login_as(client, two["one_owner"])
    assert client.post("/account/welcome/starter/remove", json={"model": "BIAN"}).status_code == 200
    assert "Sales and Service" not in names() and "Payment Order" not in names()
    assert "Develop Vision and Strategy" in names()


def test_a_load_and_a_removal_show_on_the_capability_map_at_once(client, login_as, two):
    """The map keeps an organisation's hierarchy for five minutes; the load and the removal must not wait on it."""
    def on_map():
        login_as(client, two["one_owner"])
        return {c["name"] for d in client.get("/capability-map/api/capabilities/hierarchy").get_json()["domains"] for c in d["capabilities"]}

    _choose(client, login_as, two["one_owner"], "banking", [])
    assert "Sales and Service" in on_map()
    _choose(client, login_as, two["one_owner"], "software", [])
    assert "Develop Vision and Strategy" in on_map()
    assert client.post("/account/welcome/starter/remove", json={"model": "BIAN"}).status_code == 200
    assert "Sales and Service" not in on_map()


def test_starter_rows_are_not_the_persons_own_model_so_the_overview_still_asks_for_a_source(client, login_as, two):
    """Choosing a framework or an industry gives a starting point; the overview still offers to connect a
    source until something the person brought is in the model, and its figure counts only their own."""
    from app.modules.dashboard.services.overview_figures import build_overview

    _choose(client, login_as, two["one_owner"], "banking", ["iso27001"])
    with client.application.test_request_context("/"):
        figures = build_overview(two["one"].id, two["one_owner"])
    assert figures["empty"] is True and figures["sourced"] is None
    from app import db

    kinds = db.session.execute(db.text(
        "SELECT DISTINCT source_kind FROM archimate_elements WHERE organization_id = :o AND deleted_at IS NULL"),
        {"o": two["one"].id}).scalars().all()
    assert kinds == ["starter"], kinds


def _elements(org, names=None):
    from app import db

    rows = db.session.execute(
        db.text("SELECT id, name, type FROM archimate_elements WHERE organization_id = :o AND deleted_at IS NULL"),
        {"o": org.id},
    ).all()
    return {r.name for r in rows} if names is None else {r.name for r in rows} & set(names)


def test_removing_the_old_starter_model_removes_its_model_repository_elements(client, login_as, two):
    from app import db

    for key in ("one", "two"):
        _choose(client, login_as, two["%s_owner" % key], "banking", [])
    old = {"Current Account", "Payment Execution", "Sales and Service"}
    assert old <= _elements(two["one"]) and old <= _elements(two["two"])
    db.session.execute(
        db.text("UPDATE archimate_elements SET description = 'Kept by a person' "
                "WHERE organization_id = :o AND name = 'Savings Account'"),
        {"o": two["one"].id},
    )
    db.session.commit()
    two_before = _elements(two["two"])
    _choose(client, login_as, two["one_owner"], "software", [])
    login_as(client, two["one_owner"])
    assert client.post("/account/welcome/starter/remove", json={"model": "BIAN"}).status_code == 200
    left = _elements(two["one"])
    assert not old & left, "the removed capabilities must leave the model repository too"
    assert "Savings Account" in left, "an element a person changed stays"
    assert "Develop Vision and Strategy" in left
    assert _elements(two["two"]) == two_before, "another organisation's elements are untouched"


def test_every_removed_starter_element_has_a_delete_audit_row(client, login_as, two):
    from app import db

    for key in ("one", "two"):
        _choose(client, login_as, two["%s_owner" % key], "banking", [])
    ids = [r.id for r in db.session.execute(
        db.text("SELECT id FROM archimate_elements WHERE organization_id = :o AND source_kind = 'starter'"),
        {"o": two["one"].id}).all()]
    assert len(ids) > 5
    other_ids = [r.id for r in db.session.execute(
        db.text("SELECT id FROM archimate_elements WHERE organization_id = :o"), {"o": two["two"].id}).all()]
    _choose(client, login_as, two["one_owner"], "software", [])
    login_as(client, two["one_owner"])
    assert client.post("/account/welcome/starter/remove", json={"model": "BIAN"}).status_code == 200
    gone = [i for i in ids if not db.session.execute(
        db.text("SELECT 1 FROM archimate_elements WHERE id = :i"), {"i": i}).first()]
    assert len(gone) > 5
    audited = {r.record_id for r in db.session.execute(
        db.text("SELECT record_id FROM soc2_audit_log WHERE action = 'delete' AND table_name = 'archimate_elements' "
                "AND organization_id = :o"), {"o": two["one"].id}).all()}
    assert set(gone) <= audited, "removed elements with no delete audit row: %s" % sorted(set(gone) - audited)
    assert not audited & set(other_ids), "another organisation's elements were not removed"


def test_the_bian_starter_names_are_service_domains_of_the_published_landscape():
    from app.seed_data.bian_starter import BIAN_STARTER

    areas = {area: [name for name, _d in domains] for area, domains in BIAN_STARTER}
    every = {name for names in areas.values() for name in names}
    assert "Fraud Detection" not in every and "Fraud Evaluation" in every
    assert "Product Design" not in every
    assert set(areas) <= PUBLISHED_BIAN_BUSINESS_AREAS, sorted(set(areas) - PUBLISHED_BIAN_BUSINESS_AREAS)
    assert "Bank Treasury" not in areas and "Corporate Treasury" not in every


# The business areas of the BIAN service landscape v10, as the landscape names them (it writes "&" where
# the product writes "and").
PUBLISHED_BIAN_BUSINESS_AREAS = {
    "Sales and Service", "Operations and Execution", "Risk and Compliance", "Business Support",
    "Reference Data", "Business Direction",
}


def test_a_domain_is_keyed_by_its_framework_code_not_by_list_position(monkeypatch):
    from app.seed_data import bian_starter

    before = {e["name"]: e["domain_code"] for e in onboarding._bian_entries() if e["level"] == 1}
    monkeypatch.setattr(bian_starter, "BIAN_STARTER", tuple(reversed(bian_starter.BIAN_STARTER)))
    after = {e["name"]: e["domain_code"] for e in onboarding._bian_entries() if e["level"] == 1}
    assert before == after and len(set(after.values())) == len(after)


def test_two_organisations_loading_at_once_create_one_domain(app):
    import threading

    from app import db

    code, results, barrier = "T%s" % uuid.uuid4().hex[:8], [], threading.Barrier(2)

    def load(name):
        with app.app_context():
            with db.engine.connect() as conn:
                barrier.wait()
                try:
                    conn.execute(onboarding._insert_domain(code, name))
                    conn.commit()
                    results.append("ok")
                except Exception as exc:  # pragma: no cover - the failure being tested for
                    results.append(repr(exc))

    threads = [threading.Thread(target=load, args=(n,)) for n in ("First", "Second")]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        assert results == ["ok", "ok"], results
        with db.engine.connect() as conn:
            rows = conn.execute(db.text("SELECT name FROM business_domains WHERE code = :c"), {"c": code}).all()
        assert len(rows) == 1
    finally:
        with db.engine.connect() as conn:
            conn.execute(db.text("DELETE FROM business_domains WHERE code = :c"), {"c": code})
            conn.commit()


def test_a_top_level_capability_shows_its_framework_never_its_own_name(client, login_as, two):
    for key, industry, label in (("one", "banking", "BIAN business area"), ("two", "software", "APQC category")):
        _choose(client, login_as, two["%s_owner" % key], industry, [])
        login_as(client, two["%s_owner" % key])
        caps = client.get("/capability-map/api/capabilities/hierarchy").get_json()
        rows = [c for d in caps["domains"] for c in d["capabilities"]]
        tops = [c for c in rows if c["level"] == 1]
        assert tops and all(c["domainLabel"] == label for c in tops), [c["domainLabel"] for c in tops]
        assert all(c["domainLabel"] != c["name"] for c in rows)
        for c in rows:
            if c["level"] == 2:
                assert c["domainLabel"] in {t["name"] for t in tops}


def _map_rows(client, login_as, user):
    login_as(client, user)
    return [c for d in client.get("/capability-map/api/capabilities/hierarchy").get_json()["domains"] for c in d["capabilities"]]


def test_starter_capabilities_have_a_domain_and_no_invented_assessment(client, login_as, two):
    _choose(client, login_as, two["one_owner"], "banking", [])
    rows = _map_rows(client, login_as, two["one_owner"])
    assert rows
    assert {c["domainName"] for c in rows} == {c["name"] for c in rows if c["level"] == 1}
    assert "Uncategorised" not in {c["domainName"] for c in rows}
    for cap in rows:
        assert cap["healthScore"] is None and cap["notAssessed"] is True, cap["name"]
    _choose(client, login_as, two["two_owner"], "software", [])
    other = _map_rows(client, login_as, two["two_owner"])
    assert "Uncategorised" not in {c["domainName"] for c in other}
    assert all(c["healthScore"] is None for c in other)


def test_a_capability_somebody_assessed_keeps_its_figures(client, login_as, two):
    from app import db

    _choose(client, login_as, two["one_owner"], "banking", [])
    row = BusinessCapability.query.filter_by(organization_id=two["one"].id, name="Sales and Service").one()
    row.current_maturity_level, row.target_maturity_level = 2, 3
    db.session.commit()
    cap = next(c for c in _map_rows(client, login_as, two["one_owner"]) if c["name"] == "Sales and Service")
    assert cap["notAssessed"] is False and cap["healthScore"] is not None


def test_a_partly_loaded_standard_says_how_much_of_it_is_loaded(client, login_as, two):
    _choose(client, login_as, two["one_owner"], "", ["iso27001"])
    compliance = client.get("/dashboard/compliance").get_data(as_text=True)
    gallery = client.get("/frameworks/").get_data(as_text=True)
    for name, page in (("compliance", compliance), ("gallery", gallery)):
        assert "3 of 93" in page, name
    assert "Adopted in full" not in compliance


def test_the_adoption_source_is_set_before_the_adoption_is_saved(client, login_as, two):
    from sqlalchemy import event

    seen = []

    def record(mapper, connection, target):
        seen.append(target.source)

    event.listen(FrameworkAdoption, "after_insert", record)
    try:
        _choose(client, login_as, two["one_owner"], "", ["iso27001"])
    finally:
        event.remove(FrameworkAdoption, "after_insert", record)
    assert seen == ["starter:ISO 27001"]


def test_the_frameworks_gallery_reads_the_adoptions_once(client, login_as, two):
    from sqlalchemy import event

    from app import db
    from app.modules.industry_apqc.routes import frameworks_gallery_routes as gallery

    _choose(client, login_as, two["one_owner"], "", ["iso27001"])
    statements = []

    def count(conn, cursor, statement, *rest):
        if "FROM framework_adoptions" in statement:
            statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", count)
    try:
        gallery._compliance_cards(two["one"].id)
    finally:
        event.remove(db.engine, "before_cursor_execute", count)
    assert len(statements) == 1


def test_a_starter_capability_an_application_is_mapped_to_is_never_offered_for_removal(client, login_as, two):
    from app import db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping
    from app.models.unified_capability import UnifiedCapability

    for key in ("one", "two"):
        _choose(client, login_as, two["%s_owner" % key], "banking", [])
    login_as(client, two["one_owner"])
    application = ApplicationComponent(name="Core ledger", organization_id=two["one"].id)
    db.session.add(application)
    db.session.flush()
    mapped = BusinessCapability.query.filter_by(organization_id=two["one"].id, name="Current Account").one()
    unified = UnifiedCapability.query.filter_by(
        organization_id=two["one"].id, source_table="business_capability", source_id=str(mapped.id)
    ).one()
    db.session.add(UnifiedApplicationCapabilityMapping(
        unified_capability_id=unified.id, application_component_id=application.id
    ))
    db.session.commit()
    _choose(client, login_as, two["one_owner"], "software", [])

    login_as(client, two["one_owner"])
    reply = client.post("/account/welcome/starter/remove", json={"model": "BIAN"})
    assert reply.status_code == 200, reply.get_data(as_text=True)
    names = {r.name for r in _capabilities(two["one"]) if r.discovery_source == "starter:BIAN"}
    assert "Current Account" in names, "a capability an application is mapped to stays"
    assert db.session.get(UnifiedCapability, unified.id) is not None
    assert UnifiedApplicationCapabilityMapping.query.filter_by(unified_capability_id=unified.id).count() == 1
    assert "Payment Execution" not in names, "the unmapped rows are still removed"
    other = {r.name for r in _capabilities(two["two"]) if r.discovery_source == "starter:BIAN"}
    assert "Current Account" in other, "the other organisation's rows are untouched"
