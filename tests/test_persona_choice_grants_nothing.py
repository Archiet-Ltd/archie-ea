"""A persona a person picks for themselves grants nothing.

The first-sign-in picker posted a role to /dashboard/api/onboarding-complete,
which wrote it to users.enterprise_role -- the column the ARB, solution,
ArchiMate and portfolio abilities read. A Viewer could make themselves an ARB
member or platform admin persona with one request. Release-rule lines 1 and 2.
"""
import uuid

import pytest

PICKER = [
    "solution_architect", "enterprise_architect", "business_architect",
    "arb_member", "portfolio_manager", "platform_admin", "cto",
    "application_manager", "procurement", "security_architect", "data_architect",
]


def _abilities(u):
    return (u.can_vote_arb(), u.can_edit_solutions(), u.can_edit_archimate(), u.can_manage_portfolio())


def _viewer(db_session, org, persona=None):
    from app.models.user import Role, User

    u = User(email=f"v-{uuid.uuid4().hex[:8]}@perm.test", organization_id=org.id,
             enterprise_role=persona, confirmed=True, is_platform_admin=False)
    db_session.add(u)
    db_session.flush()
    Role.insert_roles()
    u.role = Role.query.filter_by(name="Viewer").first()
    db_session.flush()
    return u


def _admin(db_session, org):
    from app.models.user import Role, User

    Role.insert_roles()
    u = User(email=f"a-{uuid.uuid4().hex[:8]}@perm.test", organization_id=org.id,
             confirmed=True, role=Role.query.filter_by(name="Administrator").first())
    u.password = uuid.uuid4().hex
    u.is_org_admin = True
    db_session.add(u)
    db_session.flush()
    return u


@pytest.mark.parametrize("picked", PICKER)
def test_viewer_posting_any_picker_value_gains_nothing(app, db_session, make_org, client, login_as, picked):
    from app.models.user import User

    org_a, org_b = make_org("perm-a-" + picked), make_org("perm-b-" + picked)
    viewer = _viewer(db_session, org_a)
    bystander = _viewer(db_session, org_b)
    db_session.commit()
    before = _abilities(viewer)
    assert before == (False, False, False, False)

    login_as(client, viewer)
    r = client.post("/dashboard/api/onboarding-complete", json={"enterprise_role": picked, "role_archetype": "architect"})
    assert r.status_code == 200

    fresh = db_session.get(User, viewer.id)
    db_session.refresh(fresh)
    assert fresh.enterprise_role is None, "a self-chosen value was written to the permission column"
    assert getattr(fresh, "role_archetype", None) in (None, "")
    assert _abilities(fresh) == before
    assert fresh.onboarding_completed_at is not None
    assert fresh.persona_lens == picked  # kept as a preference only
    assert not fresh.is_platform_admin
    other = db_session.get(User, bystander.id)
    assert other.enterprise_role is None and other.persona_lens is None


def test_organisation_admin_keeps_every_ability_without_any_persona(app, db_session, make_org):
    org = make_org("perm-admin-own")
    admin = _admin(db_session, org)
    db_session.commit()
    assert admin.enterprise_role is None
    assert _abilities(admin) == (True, True, True, True)


def test_admin_role_page_still_sets_a_role_and_it_carries_the_ability(app, db_session, make_org, client, login_as):
    from app.models.user import User

    org = make_org("perm-rolepage")
    admin = _admin(db_session, org)
    member = _viewer(db_session, org)
    db_session.commit()
    assert not member.can_vote_arb()
    login_as(client, admin)
    r = client.post(f"/admin/user/{member.id}/role", data={"enterprise_role": "arb_member"})
    assert r.status_code in (200, 302)
    fresh = db_session.get(User, member.id)
    db_session.refresh(fresh)
    assert fresh.enterprise_role == "arb_member"
    assert fresh.can_vote_arb() is True
    assert fresh.can_manage_portfolio() is False


def test_admin_set_personas_keep_what_they_always_carried(app, db_session, make_org):
    """A persona never grants authority; authority is the account's role.

    This replaces the earlier rule that a persona an administrator set kept
    what it always carried. That rule let an administrator hand the widest
    persona, platform_admin, to an account that is not a platform
    administrator, which is the defect this branch closes. Every other persona
    keeps its abilities. platform_admin carries them only for an account that
    is a platform administrator, and nothing for an account that is not,
    whoever set the persona.
    """
    expect = {
        "solution_architect": (False, True, False, False),
        "enterprise_architect": (True, True, True, True),
        "business_architect": (True, True, True, True),
        "arb_member": (True, False, False, False),
        "portfolio_manager": (False, False, False, True),
        "cto": (False, False, False, False),
    }
    for persona, want in expect.items():
        u = _viewer(db_session, make_org("perm-defaults-" + persona), persona)
        assert _abilities(u) == want, persona

    # An account that is not a platform administrator gains nothing from the persona.
    plain = _viewer(db_session, make_org("perm-defaults-platform-plain"), "platform_admin")
    assert _abilities(plain) == (False, False, False, False)

    # A platform administrator (flag and Administrator role) carrying the persona keeps every ability.
    holder = _admin(db_session, make_org("perm-defaults-platform-holder"))
    holder.enterprise_role = "platform_admin"
    holder.is_platform_admin = True
    db_session.flush()
    assert _abilities(holder) == (True, True, True, True)


def test_a_new_account_has_no_persona_by_default(app, db_session, make_org):
    from app.models.user import User

    org = make_org("perm-default-null")
    u = User(email=f"d-{uuid.uuid4().hex[:8]}@perm.test", organization_id=org.id, confirmed=True)
    db_session.add(u)
    db_session.flush()
    assert u.enterprise_role is None
