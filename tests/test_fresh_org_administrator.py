"""A test organisation is never left without an Administrator, and the persona keeps its roles."""

from tests.smoke.fresh_org import create_fresh_org


def _members(org_id):
    from app import create_app, db
    from app.models.user import User

    with create_app("testing").app_context():
        rows = [(u.id, u.enterprise_role, bool(u.is_org_admin)) for u in User.query.filter_by(organization_id=org_id)]
        db.session.remove()
    return rows


def _unadministered_ids():
    from sqlalchemy import create_engine

    from scripts.check_org_administrators import database_url, find_organisations

    with create_engine(database_url()).begin() as connection:
        return {row["id"] for row in find_organisations(connection)}


def test_a_non_administrator_persona_gets_a_separate_administrator():
    org = create_fresh_org("solution_architect", extra_roles=("arb_member",))
    assert org["org_id"] not in _unadministered_ids()
    members = {uid: (role, admin) for uid, role, admin in _members(org["org_id"])}
    persona = org["user_ids"]["solution_architect"]
    assert members[persona] == ("solution_architect", False)
    assert members[org["user_ids"]["arb_member"]] == ("arb_member", False)
    assert members[org["administrator_id"]][1] is True
    assert len(members) == 3


def test_an_administrator_persona_is_the_only_administrator_added():
    org = create_fresh_org("platform_admin", org_admin=True)
    assert org["org_id"] not in _unadministered_ids()
    assert "administrator_id" not in org
    assert len(_members(org["org_id"])) == 1


def test_the_administrator_is_added_even_when_the_personas_fill_the_community_plan():
    org = create_fresh_org("solution_architect", extra_roles=("arb_member", "security_architect"))
    members = _members(org["org_id"])
    assert len(members) == 4
    assert org["administrator_id"] in {uid for uid, _, _ in members}
    assert org["org_id"] not in _unadministered_ids()
