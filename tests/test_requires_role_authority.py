"""``requires_role`` admits real administrators, not the platform_admin title.

``enterprise_role`` defaults to ``platform_admin`` for every user who never
picked one, so admitting the title as authority opened personal-data requests
and procurement to every member. A title decides what a person sees; authority
(organisation or platform administrator) decides what they may do.
"""

import uuid

import pytest

DSR = "/compliance/data-subject-requests"
PROCUREMENT = "/procurement/contracts"


def _user(db_session, org, role, *, admin=False):
    from app.models import Role
    from app.models.user import User

    base = Role.query.filter_by(name="Administrator" if admin else "User").first() or Role.query.filter_by(
        default=True
    ).first()
    user = User(
        email=f"rra-{uuid.uuid4().hex[:8]}@example.test",
        first_name="Role",
        last_name="Authority",
        organization_id=org.id,
        confirmed=True,
        role=base,
        enterprise_role=role,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    return user


def _get(client, login_as, db_session, user, path):
    from app.models.user import User

    uid = user if isinstance(user, int) else user.id
    db_session.commit()
    db_session.expunge_all()
    login_as(client, db_session.get(User, uid))
    return client.get(path).status_code


@pytest.mark.parametrize("path", [DSR, PROCUREMENT])
def test_default_title_member_is_refused(db_session, make_org, client, login_as, path):
    org = make_org("rra-member")
    member = _user(db_session, org, "platform_admin")  # the column default

    assert _get(client, login_as, db_session, member, path) == 403


@pytest.mark.parametrize("path", [DSR, PROCUREMENT])
def test_organisation_administrator_is_admitted(db_session, make_org, client, login_as, path):
    org = make_org("rra-admin")
    admin = _user(db_session, org, "platform_admin", admin=True)
    admin.is_org_admin = True

    assert _get(client, login_as, db_session, admin, path) == 200


def test_listed_personas_are_still_admitted(db_session, make_org, client, login_as):
    org = make_org("rra-persona")
    security = _user(db_session, org, "security_architect")
    buyer = _user(db_session, org, "procurement")

    security_id, buyer_id = security.id, buyer.id
    assert _get(client, login_as, db_session, security_id, DSR) == 200
    assert _get(client, login_as, db_session, buyer_id, PROCUREMENT) == 200
