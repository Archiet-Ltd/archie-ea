"""The impact answer names the owner of the element it was asked about.

The Twin map writes an owner under every element it draws, the centre
included. Row owners already come from the one owner resolution the answer
uses; the centre's owner rides the same batch, so the two cannot disagree, and
it is fenced to the caller's organisation the same way.
"""

from __future__ import annotations

import uuid

# Fixtures (app, db_session, make_org, client, login_as) come from this
# directory's conftest, which re-exports the shared ones.


def _user(db_session, org_id):
    from app.models.user import User

    user = User(
        email=f"centre-{uuid.uuid4().hex[:10]}@example.com",
        first_name="Centre",
        last_name="Owner",
        organization_id=org_id,
        confirmed=True,
        enterprise_role="arb_member",
    )
    db_session.add(user)
    db_session.flush()
    return user


def _pair(db_session, org_id, prefix, owner_name=None):
    """Two connected elements; the first owned by a unit called owner_name."""
    from app.models import ArchiMateElement, ArchiMateRelationship
    from app.models.application_portfolio import ApplicationComponent
    from app.models.enterprise_intelligence import ApplicationOwnership, OrganizationUnit

    centre = ArchiMateElement(name=f"{prefix} centre", type="ApplicationComponent",
                              layer="application", organization_id=org_id)
    other = ArchiMateElement(name=f"{prefix} other", type="ApplicationComponent",
                             layer="application", organization_id=org_id)
    db_session.add_all([centre, other])
    db_session.flush()
    db_session.add(ArchiMateRelationship(source_id=centre.id, target_id=other.id, type="Serving",
                                         organization_id=org_id))
    if owner_name:
        component = ApplicationComponent(name=f"{prefix} app", organization_id=org_id,
                                         archimate_element_id=centre.id)
        db_session.add(component)
        db_session.flush()
        unit = OrganizationUnit(organization_id=org_id, name=owner_name)
        db_session.add(unit)
        db_session.flush()
        db_session.add(ApplicationOwnership(organization_id=org_id, application_id=component.id,
                                            organization_unit_id=unit.id, ownership_type="Business Owner"))
    db_session.flush()
    return centre


def _impact(client, element_id, **params):
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return client.get(f"/api/v1/intelligence/impact/{element_id}" + (f"?{query}" if query else ""))


def test_the_centre_owner_is_each_organisations_own(app, db_session, make_org, client, login_as):
    org_a = make_org("centre-owner-a")
    org_b = make_org("centre-owner-b")
    user_a = _user(db_session, org_a.id)
    user_b = _user(db_session, org_b.id)
    centre_a = _pair(db_session, org_a.id, "Alpha", owner_name="Alpha Finance")
    centre_b = _pair(db_session, org_b.id, "Beta", owner_name="Beta Operations")
    db_session.commit()

    login_as(client, user_a)
    body = _impact(client, centre_a.id).get_json()
    assert body["data"]["centre_owner"]["name"] == "Alpha Finance"
    # Another organisation's element is not found at all, owner included.
    assert _impact(client, centre_b.id).status_code == 404

    login_as(client, user_b)
    body = _impact(client, centre_b.id).get_json()
    assert body["data"]["centre_owner"]["name"] == "Beta Operations"
    assert "Alpha" not in str(body["data"])
    assert _impact(client, centre_a.id).status_code == 404


def test_no_owner_reads_as_none_never_an_invented_one(app, db_session, make_org, client, login_as):
    org = make_org("centre-owner-none")
    user = _user(db_session, org.id)
    centre = _pair(db_session, org.id, "Gamma")
    db_session.commit()

    login_as(client, user)
    data = _impact(client, centre.id).get_json()["data"]
    assert "centre_owner" in data
    assert data["centre_owner"] is None


def test_the_centre_owner_is_not_resolved_when_owners_are_not_asked_for(
    app, db_session, make_org, client, login_as
):
    org = make_org("centre-owner-off")
    user = _user(db_session, org.id)
    centre = _pair(db_session, org.id, "Delta", owner_name="Delta Finance")
    db_session.commit()

    login_as(client, user)
    data = _impact(client, centre.id, with_owner="false").get_json()["data"]
    assert data["centre_owner"] is None
