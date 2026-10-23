"""Deleting a capability's ArchiMate mapping stays inside the caller's active organisation.

The mapping table carries no organisation column; ownership is reached through its capability.
Another organisation's mapping id must answer 404 (the same as a missing id) and delete nothing,
including for a user who belongs to the owning organisation's rival and has switched into it.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text


def _user(db_session, org, label):
    from app.models.user import User

    user = User(email=f"{label}-{uuid.uuid4().hex[:6]}@example.test", first_name="M", last_name="I",
                organization_id=org.id, confirmed=True)
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    return user


def _mapping(db_session, org):
    from app.models.business_capabilities import BusinessCapability
    from app.models.capability_archimate_mapping import CapabilityArchiMateClassification

    cap = BusinessCapability(name=f"Cap {uuid.uuid4().hex[:6]}", organization_id=org.id)
    db_session.add(cap)
    db_session.flush()
    mapping = CapabilityArchiMateClassification(
        capability_id=cap.id, archimate_layer="business", archimate_element_type="BusinessFunction",
    )
    db_session.add(mapping)
    db_session.flush()
    return mapping


def _exists(db_session, mapping_id):
    return db_session.execute(
        text("select count(*) from capability_archimate_classifications where id = :i"),
        {"i": mapping_id},
    ).scalar() == 1


def _delete(client, login_as, user_id, mapping_id):
    from app.models.user import User
    from app import db

    login_as(client, db.session.get(User, user_id))
    return client.delete(f"/capability-map/api/archimate-mappings/{mapping_id}")


def test_another_organisations_mapping_is_refused_with_404_and_kept(
        app, db_session, make_org, client, login_as):
    org_a, org_b = make_org("cm-a"), make_org("cm-b")
    mapping_a = _mapping(db_session, org_a)
    user_b = _user(db_session, org_b, "b")
    db_session.commit()
    mapping_id, user_id = mapping_a.id, user_b.id
    db_session.expunge_all()

    response = _delete(client, login_as, user_id, mapping_id)

    assert response.status_code == 404, response.get_data(as_text=True)
    assert _exists(db_session, mapping_id)


def test_the_owning_organisation_can_still_delete_its_own_mapping(
        app, db_session, make_org, client, login_as):
    org_a = make_org("cm-own")
    mapping_a = _mapping(db_session, org_a)
    user_a = _user(db_session, org_a, "a")
    db_session.commit()
    mapping_id, user_id = mapping_a.id, user_a.id
    db_session.expunge_all()

    response = _delete(client, login_as, user_id, mapping_id)

    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json() == {"success": True, "deleted_id": mapping_id}
    assert not _exists(db_session, mapping_id)


def test_a_user_switched_into_another_organisation_cannot_delete_the_first_ones_mapping(
        app, db_session, make_org, client, login_as):
    from app.models.org_role import OrgRole

    org_a, org_b = make_org("cm-home"), make_org("cm-second")
    mapping_a = _mapping(db_session, org_a)
    user = _user(db_session, org_a, "multi")
    OrgRole.set_role(org_b.id, user.id, "architect", granted_by_id=user.id)
    db_session.commit()
    mapping_id, user_id, org_b_id = mapping_a.id, user.id, org_b.id
    db_session.expunge_all()

    from app.models.user import User

    login_as(client, db_session.get(User, user_id))
    switched = client.post("/account/switch-organization",
                           data={"organization_id": str(org_b_id)}, follow_redirects=False)
    assert switched.status_code == 302
    login_as_cached = client.delete(f"/capability-map/api/archimate-mappings/{mapping_id}")

    assert login_as_cached.status_code == 404, login_as_cached.get_data(as_text=True)
    assert _exists(db_session, mapping_id)


def test_saving_mappings_skips_another_organisations_capability(
        app, db_session, make_org, client, login_as):
    org_a, org_b = make_org("cm-save-a"), make_org("cm-save-b")
    mapping_a = _mapping(db_session, org_a)
    user_b = _user(db_session, org_b, "b")
    db_session.commit()
    cap_a_id = db_session.execute(
        text("select capability_id from capability_archimate_classifications where id = :i"),
        {"i": mapping_a.id}).scalar()
    mapping_id, user_id = mapping_a.id, user_b.id
    db_session.expunge_all()

    from app.models.user import User

    login_as(client, db_session.get(User, user_id))
    response = client.post("/capability-map/api/save-archimate-mappings", json={"mappings": [
        {"capability_id": cap_a_id, "archimate_element_id": 999999, "archimate_layer": "technology"}]})

    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json()["total"] == 0
    count = db_session.execute(
        text("select count(*) from capability_archimate_classifications where capability_id = :c"),
        {"c": cap_a_id}).scalar()
    assert count == 1 and _exists(db_session, mapping_id)


def _element(db_session, org, name="Element"):
    from app.models.archimate_core import ArchiMateElement
    element = ArchiMateElement(name=name, type="BusinessFunction", layer="business", organization_id=org.id)
    db_session.add(element)
    db_session.flush()
    return element


def _capability(db_session, org, name="Cap"):
    from app.models.business_capabilities import BusinessCapability
    cap = BusinessCapability(name=name, organization_id=org.id)
    db_session.add(cap)
    db_session.flush()
    return cap


def test_saving_mapping_skips_another_organisations_element(
        app, db_session, make_org, client, login_as):
    """Org B tries to save a mapping pointing at Org A's element -- skipped, nothing written."""
    org_a, org_b = make_org("cm-elem-a"), make_org("cm-elem-b")
    cap_b = _capability(db_session, org_b, "Cap B")
    elem_a = _element(db_session, org_a, "Element A")
    user_b = _user(db_session, org_b, "b")
    db_session.commit()
    cap_b_id, elem_a_id, user_b_id = cap_b.id, elem_a.id, user_b.id
    db_session.expunge_all()

    from app.models.user import User

    login_as(client, db_session.get(User, user_b_id))
    response = client.post("/capability-map/api/save-archimate-mappings", json={"mappings": [{
        "capability_id": cap_b_id, "archimate_element_id": elem_a_id, "archimate_layer": "business"}]})

    assert response.status_code == 200, response.get_data(as_text=True)
    data = response.get_json()
    assert data["total"] == 0
    assert data["skipped"]["element_not_owned"] == 1
    count = db_session.execute(
        text("select count(*) from capability_archimate_classifications where capability_id = :c"),
        {"c": cap_b_id}).scalar()
    assert count == 0


def test_saving_mapping_to_own_element_is_written(
        app, db_session, make_org, client, login_as):
    """Org A saves a mapping to its own element -- written successfully."""
    org_a = make_org("cm-own-elem")
    cap_a = _capability(db_session, org_a, "Cap A")
    elem_a = _element(db_session, org_a, "Element A")
    user_a = _user(db_session, org_a, "a")
    db_session.commit()
    cap_a_id, elem_a_id, user_a_id = cap_a.id, elem_a.id, user_a.id
    db_session.expunge_all()

    from app.models.user import User

    login_as(client, db_session.get(User, user_a_id))
    response = client.post("/capability-map/api/save-archimate-mappings", json={"mappings": [{
        "capability_id": cap_a_id, "archimate_element_id": elem_a_id, "archimate_layer": "business"}]})

    assert response.status_code == 200, response.get_data(as_text=True)
    data = response.get_json()
    assert data["total"] == 1
    assert data["created"] == 1
    assert data["skipped"]["element_not_owned"] == 0
    count = db_session.execute(
        text("select count(*) from capability_archimate_classifications where capability_id = :c"),
        {"c": cap_a_id}).scalar()
    assert count == 1


def test_switched_user_skips_another_organisations_element(
        app, db_session, make_org, client, login_as):
    """User switched into Org B tries to save mapping to Org A's element -- skipped."""
    from app.models.org_role import OrgRole

    org_a, org_b = make_org("cm-home-elem"), make_org("cm-second-elem")
    cap_a = _capability(db_session, org_a, "Cap A")
    elem_a = _element(db_session, org_a, "Element A")
    user = _user(db_session, org_a, "multi")
    OrgRole.set_role(org_b.id, user.id, "architect", granted_by_id=user.id)
    db_session.commit()
    cap_a_id, elem_a_id, user_id, org_b_id = cap_a.id, elem_a.id, user.id, org_b.id
    db_session.expunge_all()

    from app.models.user import User

    login_as(client, db_session.get(User, user_id))
    switched = client.post("/account/switch-organization",
                           data={"organization_id": str(org_b_id)}, follow_redirects=False)
    assert switched.status_code == 302
    response = client.post("/capability-map/api/save-archimate-mappings", json={"mappings": [{
        "capability_id": cap_a_id, "archimate_element_id": elem_a_id, "archimate_layer": "business"}]})

    assert response.status_code == 200, response.get_data(as_text=True)
    data = response.get_json()
    assert data["total"] == 0
    # Both capability and element belong to org_a, so both should be skipped
    # when current org is org_b. The first check (capability) catches it.
    assert data["skipped"]["capability_not_owned"] == 1
    assert data["skipped"]["element_not_owned"] == 0
    count = db_session.execute(
        text("select count(*) from capability_archimate_classifications where capability_id = :c"),
        {"c": cap_a_id}).scalar()
    assert count == 0


def test_switched_user_skips_another_organisations_element_with_own_capability(
        app, db_session, make_org, client, login_as):
    """User switched into Org B tries to save mapping with Org B's capability but Org A's element -- skipped."""
    from app.models.org_role import OrgRole

    org_a, org_b = make_org("cm-home-elem2"), make_org("cm-second-elem2")
    cap_b = _capability(db_session, org_b, "Cap B")
    elem_a = _element(db_session, org_a, "Element A")
    user = _user(db_session, org_a, "multi2")
    OrgRole.set_role(org_b.id, user.id, "architect", granted_by_id=user.id)
    db_session.commit()
    cap_b_id, elem_a_id, user_id, org_b_id = cap_b.id, elem_a.id, user.id, org_b.id
    db_session.expunge_all()

    from app.models.user import User

    login_as(client, db_session.get(User, user_id))
    switched = client.post("/account/switch-organization",
                           data={"organization_id": str(org_b_id)}, follow_redirects=False)
    assert switched.status_code == 302
    response = client.post("/capability-map/api/save-archimate-mappings", json={"mappings": [{
        "capability_id": cap_b_id, "archimate_element_id": elem_a_id, "archimate_layer": "business"}]})

    assert response.status_code == 200, response.get_data(as_text=True)
    data = response.get_json()
    assert data["total"] == 0
    # Capability belongs to org_b (current org), element belongs to org_a
    # Element check should catch it
    assert data["skipped"]["capability_not_owned"] == 0
    assert data["skipped"]["element_not_owned"] == 1
    count = db_session.execute(
        text("select count(*) from capability_archimate_classifications where capability_id = :c"),
        {"c": cap_b_id}).scalar()
    assert count == 0
