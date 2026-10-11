"""A capability mapping is updated only when it belongs to the application in the URL.

``update_capability_mapping`` loaded the application (``ApplicationComponent``, tenant-fenced) from
``id``, then loaded ``UnifiedApplicationCapabilityMapping`` from a SEPARATE path parameter,
``mapping_id``, with no check that the mapping belongs to that application. The mapping has no
organisation column of its own, so a caller who owns any application in their own organisation
could pass any other organisation's ``mapping_id`` and update it (support_level,
coverage_percentage, maturity_level, notes). The sibling delete route,
``application_capability_mapping_delete``, already carries this exact check
(``mapping.application_component_id != app.id``); the update route now carries the same one.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text


def _org_component(db_session, make_org):
    from app.models.application_portfolio import ApplicationComponent

    org = make_org("cap-map")
    app = ApplicationComponent(name=f"App {uuid.uuid4().hex[:6]}", organization_id=org.id)
    db_session.add(app)
    db_session.flush()
    return org, app


def _capability(db_session):
    from app.models.unified_capability import UnifiedCapability

    cap = UnifiedCapability(name=f"Capability {uuid.uuid4().hex[:6]}")
    db_session.add(cap)
    db_session.flush()
    return cap


def _mapping(db_session, app, capability, **fields):
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping

    mapping = UnifiedApplicationCapabilityMapping(
        application_component_id=app.id, unified_capability_id=capability.id, **fields,
    )
    db_session.add(mapping)
    db_session.flush()
    return mapping


def _user(db_session, org):
    from app.models.user import User

    user = User(email=f"cm-{uuid.uuid4().hex[:6]}@example.test", first_name="C", last_name="M",
                organization_id=org.id, confirmed=True)
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    return user


def test_a_foreign_mapping_id_is_refused_not_updated(app, db_session, make_org, client, login_as):
    org_a, app_a = _org_component(db_session, make_org)
    _org_b, app_b = _org_component(db_session, make_org)
    capability = _capability(db_session)
    _mapping(db_session, app_a, capability, support_level="partial", notes="own")
    foreign_mapping = _mapping(db_session, app_b, capability, support_level="partial", notes="foreign")
    user = _user(db_session, org_a)
    db_session.commit()
    app_a_id, foreign_mapping_id, user_id = app_a.id, foreign_mapping.id, user.id
    db_session.expunge_all()

    from app.models.user import User

    login_as(client, db_session.get(User, user_id))
    response = client.post(
        f"/dashboard/applications/{app_a_id}/capability-mapping/{foreign_mapping_id}/update",
        data={"support_level": "full", "notes": "overwritten"},
    )

    assert response.status_code in (302, 303)
    stored = db_session.execute(
        text("select support_level, notes from unified_application_capability_mapping where id = :i"),
        {"i": foreign_mapping_id},
    ).one()
    assert stored.support_level == "partial" and stored.notes == "foreign"


def test_the_owning_applications_own_mapping_can_still_be_updated(app, db_session, make_org, client, login_as):
    org_a, app_a = _org_component(db_session, make_org)
    capability = _capability(db_session)
    own_mapping = _mapping(db_session, app_a, capability, support_level="partial")
    user = _user(db_session, org_a)
    db_session.commit()
    app_a_id, own_mapping_id, user_id = app_a.id, own_mapping.id, user.id
    db_session.expunge_all()

    from app.models.user import User

    login_as(client, db_session.get(User, user_id))
    response = client.post(
        f"/dashboard/applications/{app_a_id}/capability-mapping/{own_mapping_id}/update",
        data={"support_level": "full", "notes": "updated"},
    )

    assert response.status_code in (302, 303)
    stored = db_session.execute(
        text("select support_level, notes from unified_application_capability_mapping where id = :i"),
        {"i": own_mapping_id},
    ).one()
    assert stored.support_level == "full" and stored.notes == "updated"
