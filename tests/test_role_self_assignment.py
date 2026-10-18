"""A user must not be able to raise their own privilege by saving a role.

``POST /dashboard/api/onboarding-complete`` stores a caller-chosen
``enterprise_role``. ``platform_admin`` was among the values it accepted, and
the ARB submission fence (``authorise_submit`` and the legacy
``ARBSubmissionService._actor_can_access``) treated the bare role string as
platform-admin authority. Together they let any signed-in user act on another
person's solution in their own organisation.

The platform-admin decision is ``tenant_decorators.is_platform_admin`` (flag AND
Permission.ADMINISTER); the role string alone is not authority.
"""

import uuid

import pytest


def _user(db_session, org, role, *, platform_flag=False, administer=False):
    from app.models.user import Permission, Role, User

    user = User(
        email=f"rsa-{uuid.uuid4().hex[:10]}@example.com",
        first_name="Role",
        last_name="Probe",
        confirmed=True,
    )
    user.organization_id = org.id
    user.enterprise_role = role
    user.is_platform_admin = platform_flag
    if administer:
        user.role = Role.query.filter_by(name="Administrator").first() or Role(
            name="Administrator", permissions=Permission.ADMINISTER
        )
    db_session.add(user)
    db_session.flush()
    return user


def _solution(db_session, org, creator):
    from app.models.solution_models import Solution

    solution = Solution(name=f"rsa-{uuid.uuid4().hex[:8]}", created_by_id=creator.id)
    solution.organization_id = org.id
    db_session.add(solution)
    db_session.flush()
    return solution


def _post_role(client, login_as, user, role):
    login_as(client, user)
    return client.post(
        "/dashboard/api/onboarding-complete",
        json={"enterprise_role": role},
        headers={"X-CSRFToken": "test"},
    )


def test_onboarding_cannot_self_assign_platform_admin(
    db_session, make_org, client, login_as
):
    org = make_org("rsa-onboard")
    user = _user(db_session, org, "solution_architect")
    db_session.commit()

    _post_role(client, login_as, user, "platform_admin")

    db_session.refresh(user)
    assert user.enterprise_role == "solution_architect"


def test_onboarding_still_saves_an_ordinary_role(db_session, make_org, client, login_as):
    org = make_org("rsa-ordinary")
    user = _user(db_session, org, "solution_architect")
    db_session.commit()

    _post_role(client, login_as, user, "application_manager")

    db_session.refresh(user)
    assert user.enterprise_role == "application_manager"


def test_real_platform_admin_keeps_the_platform_admin_choice(
    db_session, make_org, client, login_as
):
    org = make_org("rsa-real")
    user = _user(db_session, org, "cto", platform_flag=True, administer=True)
    db_session.commit()

    _post_role(client, login_as, user, "platform_admin")

    db_session.refresh(user)
    assert user.enterprise_role == "platform_admin"


def test_role_string_alone_does_not_authorise_arb_submission(db_session, make_org):
    from app.modules.transformation_room.arb_submission_service import (
        TypedARBSubmissionService,
    )
    from app.modules.transformation_room.domain import ActorContext, NotAuthorised

    org = make_org("rsa-submit")
    owner = _user(db_session, org, "solution_architect")
    # Role value set directly, as the save call allowed: no flag, no ADMINISTER.
    claimant = _user(db_session, org, "platform_admin")
    solution = _solution(db_session, org, owner)
    actor = ActorContext(claimant.id, org.id, frozenset(), "req-rsa")

    with pytest.raises(NotAuthorised):
        TypedARBSubmissionService.authorise_submit(
            db_session, actor, "solution", solution.id
        )


def test_real_platform_admin_is_authorised_to_submit(db_session, make_org):
    from app.modules.transformation_room.arb_submission_service import (
        TypedARBSubmissionService,
    )
    from app.modules.transformation_room.domain import ActorContext

    org = make_org("rsa-submit-ok")
    owner = _user(db_session, org, "solution_architect")
    admin = _user(db_session, org, "cto", platform_flag=True, administer=True)
    solution = _solution(db_session, org, owner)
    actor = ActorContext(admin.id, org.id, frozenset(), "req-rsa-ok")

    TypedARBSubmissionService.authorise_submit(db_session, actor, "solution", solution.id)


def test_legacy_access_check_ignores_the_role_string(db_session, make_org):
    from app.modules.solutions_strategic.v2.services.arb_submission_service import (
        ARBSubmissionService,
    )

    org = make_org("rsa-legacy")
    owner = _user(db_session, org, "solution_architect")
    claimant = _user(db_session, org, "platform_admin")
    solution = _solution(db_session, org, owner)

    assert ARBSubmissionService._actor_can_access(claimant, solution) is False


def test_onboarding_cannot_self_assign_arb_member(db_session, make_org, client, login_as):
    org = make_org("rsa-arb")
    user = _user(db_session, org, "solution_architect")
    db_session.commit()

    resp = _post_role(client, login_as, user, "arb_member")

    db_session.refresh(user)
    assert resp.status_code == 403
    assert user.enterprise_role == "solution_architect"


def test_existing_arb_member_can_confirm_their_role(db_session, make_org, client, login_as):
    org = make_org("rsa-arb-keep")
    user = _user(db_session, org, "arb_member")
    db_session.commit()

    resp = _post_role(client, login_as, user, "arb_member")

    db_session.refresh(user)
    assert resp.status_code == 200
    assert user.enterprise_role == "arb_member"


@pytest.mark.parametrize(
    "role",
    ["platform_admin", "arb_member", "enterprise_architect", "cto", "portfolio_manager", "procurement"],
)
def test_onboarding_refuses_every_role_that_carries_authority(
    db_session, make_org, client, login_as, role
):
    org = make_org("rsa-authority")
    user = _user(db_session, org, "application_manager")
    db_session.commit()

    resp = _post_role(client, login_as, user, role)

    db_session.refresh(user)
    assert resp.status_code == 403
    assert user.enterprise_role == "application_manager"
