"""A duplicate-group detail page is shown only when the caller's organisation owns at least
one of the group's member applications.

``UnifiedDuplicateGroup`` carries no organisation column of its own, and ``simple_group_detail``
loaded a group by id with no ownership check at all. Reproduced before the fix: an organisation
with no member applications in a group could still read that group's full detail, including its
estimated savings figure. ``group.applications`` is a select against ``ApplicationComponent``
(TenantMixin), so it is already fenced to the caller's organisation by the ORM listener; a group
with none of its member applications visible to the caller is now treated as not found.

The same unchecked ``UnifiedDuplicateGroup.query.get(...)`` pattern recurs in roughly a dozen
other routes and services across the v1 and v2 duplicate-detection trees -- this fixes only the
one route reproduced here; the rest are a separate, larger piece of work.
"""

from __future__ import annotations

import uuid


def _org_component(db_session, make_org):
    from app.models.application_portfolio import ApplicationComponent

    org = make_org("dupgroup")
    app = ApplicationComponent(name=f"App {uuid.uuid4().hex[:6]}", organization_id=org.id)
    db_session.add(app)
    db_session.flush()
    return org, app


def _group(db_session, *, name, applications=()):
    from app.models.unified_duplicate_detection import UnifiedDuplicateGroup

    group = UnifiedDuplicateGroup(name=name, similarity_score=0.9, estimated_savings=999999)
    db_session.add(group)
    db_session.flush()
    for app in applications:
        group.applications.append(app)
    db_session.flush()
    return group


def _user(db_session, org):
    from app.models.user import User

    user = User(email=f"dg-{uuid.uuid4().hex[:6]}@example.test", first_name="D", last_name="G",
                organization_id=org.id, confirmed=True)
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    return user


def test_a_group_with_no_member_application_in_the_callers_organisation_is_not_found(
    app, db_session, make_org, client, login_as
):
    org_a, _app_a = _org_component(db_session, make_org)
    _org_b, app_b = _org_component(db_session, make_org)
    group = _group(db_session, name="SECRET-DUPGROUP-B", applications=[app_b])
    user_a = _user(db_session, org_a)
    db_session.commit()
    group_id, user_a_id = group.id, user_a.id
    db_session.expunge_all()

    from app.models.user import User

    login_as(client, db_session.get(User, user_a_id))
    response = client.get(f"/duplicate-detection/simple/group/{group_id}")

    assert response.status_code == 404
    assert "SECRET-DUPGROUP-B" not in response.get_data(as_text=True)


def test_a_group_with_a_member_application_in_the_callers_organisation_is_visible(
    app, db_session, make_org, client, login_as
):
    org_a, app_a = _org_component(db_session, make_org)
    group = _group(db_session, name="Own group", applications=[app_a])
    user_a = _user(db_session, org_a)
    db_session.commit()
    group_id, user_a_id = group.id, user_a.id
    db_session.expunge_all()

    from app.models.user import User

    login_as(client, db_session.get(User, user_a_id))
    response = client.get(f"/duplicate-detection/simple/group/{group_id}")

    assert response.status_code == 200
