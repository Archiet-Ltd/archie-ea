"""A capability's "Record its importance" step opens the record the capabilities page edits, and saving
importance changes only importance.

Two organisations; two capabilities from different creators whose raw ids collide with other records.
"""
import uuid

import pytest

STEP = "Record its importance"


def _org_with_people(db_session, make_org):
    from app.models.user import Role, User

    Role.insert_roles()
    org = make_org("invest-step")
    people = {}
    # A person whose persona may edit capabilities on the page but whose role the save refuses.
    for key, role_name, persona in (("architect", "Architect", "enterprise_architect"), ("plain", "User", "cto")):
        user = User(email="%s.%s@example.com" % (key, uuid.uuid4().hex[:8]), first_name="T", last_name=key,
                    organization_id=org.id, enterprise_role=persona, confirmed=True)
        user.role = Role.query.filter_by(name=role_name).one()
        user.password = "Passw0rd!x1"
        db_session.add(user)
        people[key] = user
    db_session.flush()
    return org, people


def _scores(client, login_as, user):
    login_as(client, user)
    resp = client.get("/strategic/api/investment-analysis")
    assert resp.status_code == 200, resp.status_code
    body = resp.get_json()
    body = body.get("data", body)
    return {r["capability_name"]: r for r in body["capability_scores"]}


def _seed(db_session, org, tag):
    """One capability made on the capabilities page, two adopted ones with no record on that page, and a decoy record whose id equals the adopted capability's id."""
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capabilities import BusinessCapability
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping
    from app.models.unified_capability import UnifiedCapability
    from app.modules.capabilities.services.capability_service import ensure_capability_record

    listed_bc = BusinessCapability(name="Listed %s" % tag, organization_id=org.id)
    db_session.add(listed_bc)
    db_session.flush()
    # Saving a capability on the capabilities page projects its unified record.
    listed = UnifiedCapability.query.filter_by(
        source_table="business_capability", source_id=str(listed_bc.id)).one()
    adopted, _ = ensure_capability_record(name="Adopted %s" % tag, level=1, organization_id=org.id, scope="tenant")
    orphan, _ = ensure_capability_record(name="Orphan %s" % tag, level=1, organization_id=org.id, scope="tenant")
    db_session.flush()
    # A different capability whose id equals the adopted capability's id in the other table.
    decoy = BusinessCapability(id=adopted.id, name="Decoy %s" % tag, organization_id=org.id)
    db_session.add(decoy)
    app_row = ApplicationComponent(name="App %s" % tag, organization_id=org.id, business_criticality="high")
    db_session.add(app_row)
    db_session.flush()
    for cap in (listed, adopted, orphan):
        db_session.add(UnifiedApplicationCapabilityMapping(
            unified_capability_id=cap.id, application_component_id=app_row.id))
    db_session.flush()
    return {"listed_bc": listed_bc.id, "decoy": decoy.id,
            "adopted": adopted.id, "names": (listed.name, adopted.name, orphan.name, decoy.name)}


def _edit_id(row):
    href = (row.get("strategic_step") or {}).get("href")
    return href and href.split("edit=")[1]


def test_each_step_opens_its_own_capabilitys_record_and_not_a_colliding_id(
        client, login_as, db_session, make_org):
    org, people = _org_with_people(db_session, make_org)
    other_org, _ = _org_with_people(db_session, make_org)
    mine = _seed(db_session, org, "m" + uuid.uuid4().hex[:5])
    _seed(db_session, other_org, "f" + uuid.uuid4().hex[:5])
    db_session.commit()
    listed, adopted, orphan, _decoy = mine["names"]

    rows = _scores(client, login_as, people["architect"])
    # Both are derived from the application's criticality, and both offer the step.
    assert rows[listed]["strategic_basis"] == "derived"
    assert rows[adopted]["strategic_basis"] == "derived"
    assert rows[listed]["strategic_step"]["text"] == STEP
    assert _edit_id(rows[listed]) == str(mine["listed_bc"])
    # The adopted capability's raw id equals the decoy record's id; its step must not open the decoy.
    assert str(mine["adopted"]) == str(mine["decoy"])
    assert _edit_id(rows[adopted]) is None
    # Adopted capabilities are on no record the page edits: no form to open, so no link.
    assert _edit_id(rows[orphan]) is None


def test_a_person_who_cannot_save_is_offered_no_step(client, login_as, db_session, make_org):
    org, people = _org_with_people(db_session, make_org)
    mine = _seed(db_session, org, "p" + uuid.uuid4().hex[:5])
    db_session.commit()
    listed = mine["names"][0]
    assert _edit_id(_scores(client, login_as, people["architect"])[listed])
    assert _edit_id(_scores(client, login_as, people["plain"])[listed]) is None
    # and the save is refused to that person, which is why no step is offered
    login_as(client, people["plain"])
    resp = client.put("/enterprise/capabilities/%s" % mine["listed_bc"],
                      json={"name": listed, "strategic_importance": "high"})
    assert resp.status_code == 403


def test_saving_only_importance_leaves_an_empty_category_empty(client, login_as, db_session, make_org):
    from app.models.business_capabilities import BusinessCapability

    org, people = _org_with_people(db_session, make_org)
    mine = _seed(db_session, org, "c" + uuid.uuid4().hex[:5])
    db_session.commit()
    bc = db_session.get(BusinessCapability, mine["listed_bc"])
    assert not bc.category
    login_as(client, people["architect"])
    resp = client.put("/enterprise/capabilities/%s" % bc.id,
                      json={"name": bc.name, "type": "", "level": 1, "strategic_importance": "high"})
    assert resp.status_code == 200, resp.get_json()
    db_session.expire_all()
    bc = db_session.get(BusinessCapability, mine["listed_bc"])
    assert bc.strategic_importance == "high"
    assert not bc.category


def test_no_investment_page_prints_its_own_withheld_caption():
    """The withheld sentence is printed once per page from the one constant; no caption repeats it."""
    import pathlib

    for template in pathlib.Path("app/templates/strategic").glob("*.html"):
        text = template.read_text()
        assert "left out of this page" not in text, template
        assert "costs are not shown" not in text, template
