"""Who may change the model and who may read every solution, in two organisations.

The screens draw "Import model file", "Edit" and "New Plateau" from one answer
(``can_change_model``) and the routes behind them admit the same people; a solution
opens for anyone the matrix lets read every solution in their own organisation.
"""
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _org(db_session, label):
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:8]
    org = Organization(name="NR %s %s" % (label, suffix), slug="nr-%s-%s" % (label, suffix))
    db_session.add(org)
    db_session.flush()
    return org


def _user(db_session, org, persona=None, platform_role="Viewer"):
    from app.models.org_role import OrgRole
    from app.models.user import Role, User

    user = User(
        email="nr-%s@example.com" % uuid.uuid4().hex[:10], first_name="N", last_name="R",
        organization_id=org.id, role=Role.query.filter_by(name=platform_role).first(),
        confirmed=True, enterprise_role=persona,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    OrgRole.set_role(org.id, user.id, "viewer")
    db_session.flush()
    return user


def _solution(db_session, org, creator):
    from app.models.solution_models import Solution

    row = Solution(name="NR solution %s" % uuid.uuid4().hex[:6], description="x",
                   organization_id=org.id, created_by_id=creator.id, governance_status="draft")
    db_session.add(row)
    db_session.flush()
    return row


# The abilities this change must not move, beyond the ones it names.
PINNED = [
    ("enterprise_architect", "edit", "archimate", True),
    ("business_architect", "edit", "archimate", True),
    ("solution_architect", "edit", "archimate", False),
    ("cto", "edit", "archimate", False),
    ("arb_member", "edit", "archimate", False),
    ("viewer", "edit", "archimate", False),
    ("viewer", "edit", "solution", False),
    ("cto", "view_all", "solution", True),
    ("platform_admin", "access_any", "solution", True),
    ("enterprise_architect", "access_any", "solution", False),
    ("viewer", "access_any", "solution", False),
    ("procurement", "view_all", "solution", False),
    # named by this change: read every solution in the organisation
    ("solution_architect", "view_all", "solution", True),
    ("business_architect", "view_all", "solution", True),
    ("arb_member", "view_all", "solution", True),
    ("viewer", "view_all", "solution", True),
]


@pytest.mark.parametrize("role,action,object_type,expected", PINNED)
def test_role_abilities_are_pinned(role, action, object_type, expected):
    from app.core.auth.permissions import default_allowed

    assert default_allowed(role, action, object_type) is expected


def test_model_change_follows_the_persona_inside_its_own_organisation(db_session):
    from app.core.auth.permissions import can_change_model

    org_a, org_b = _org(db_session, "a"), _org(db_session, "b")
    architect_a = _user(db_session, org_a, "enterprise_architect")
    viewer_a = _user(db_session, org_a)
    viewer_b = _user(db_session, org_b)
    assert can_change_model(architect_a, org_a.id)
    assert not can_change_model(viewer_a, org_a.id)
    # org B's own grant for its people to edit elements does not reach org A
    from app.core.auth.permissions import set_grant

    set_grant(org_b.id, "role", "viewer", "motivation_element", "edit", True)
    assert can_change_model(viewer_b, org_b.id)
    assert not can_change_model(viewer_a, org_a.id)


def test_viewer_grant_in_one_organisation_does_not_open_solutions_in_the_other(db_session):
    from app.core.auth.permissions import can, set_grant

    org_a, org_b = _org(db_session, "a"), _org(db_session, "b")
    viewer_a, viewer_b = _user(db_session, org_a), _user(db_session, org_b)
    set_grant(org_b.id, "role", "viewer", "solution", "view_all", False)
    assert can(viewer_a, "view_all", "solution", org_a.id) is True
    assert can(viewer_b, "view_all", "solution", org_b.id) is False


def test_solution_opens_for_readers_of_their_own_organisation_only(db_session):
    from app.modules.solutions_strategic.v2.routes.solution_design_routes import _check_solution_access

    org_a, org_b = _org(db_session, "a"), _org(db_session, "b")
    owner_a, owner_b = _user(db_session, org_a), _user(db_session, org_b)
    solution_a, solution_b = _solution(db_session, org_a, owner_a), _solution(db_session, org_b, owner_b)
    reader = _user(db_session, org_a)
    for persona in ("enterprise_architect", "solution_architect", "business_architect", "arb_member", None):
        reader.enterprise_role = persona
        db_session.flush()
        assert _check_solution_access(solution_a, reader), persona
        assert not _check_solution_access(solution_b, reader), persona
    reader.enterprise_role = "procurement"
    db_session.flush()
    assert not _check_solution_access(solution_a, reader)


def test_import_and_element_edit_routes_admit_the_architect_persona_only(app, db_session, login_as):
    org_a = _org(db_session, "a")
    architect = _user(db_session, org_a, "enterprise_architect")
    viewer = _user(db_session, org_a)
    client = app.test_client()
    login_as(client, architect)
    assert client.get("/architecture/import/oef").status_code == 200
    assert client.get("/architecture/implementation/Plateau/new").status_code == 200
    login_as(client, viewer)
    assert client.get("/architecture/import/oef").status_code == 403
    assert client.get("/architecture/implementation/Plateau/new").status_code == 403
