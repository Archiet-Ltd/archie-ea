"""TEMPORARY PROBE - delete after run."""
import uuid

PASSWORD = "Sup3r-secret-pass!"


def _user(db_session, org, label, role_name, persona=None, **extra):
    from app.models.user import Role, User

    role = Role.query.filter_by(name=role_name).first()
    if role is None:
        Role.insert_roles()
        role = Role.query.filter_by(name=role_name).one()
    user = User(
        email=f"{label}-{uuid.uuid4().hex[:8]}@example.test", first_name=label.title(), last_name="Tester",
        organization_id=org.id, confirmed=True, enterprise_role=persona, role=role, **extra,
    )
    user.password = PASSWORD
    db_session.add(user)
    db_session.flush()
    return user


def test_probe(app, db_session, client, login_as, make_org):
    from app.models.solution_models import Solution
    from app.services.billing_plans import set_contract_plan

    org = make_org("probe")
    set_contract_plan(org, "enterprise", None)
    author = _user(db_session, org, "author", "User")
    solution = Solution(name="Probe", organization_id=org.id, created_by_id=author.id, scratchpad_items=[])
    db_session.add(solution)
    db_session.flush()
    viewer = _user(db_session, org, "viewer", "Viewer", "viewer")
    db_session.commit()
    sid = solution.id
    login_as(client, viewer)
    for path in (f"/solutions/{sid}/codegen/generate", f"/solutions/{sid}/decisions",
                 f"/solutions/{sid}/scratchpad", f"/solutions/{sid}/codegen/get-config",
                 f"/solutions/{sid}/edit"):
        r = client.post(path, json={})
        print("PROBE", path, r.status_code, r.get_data(as_text=True)[:300])
    r = client.get(f"/solutions/{sid}/codegen/get-config")
    print("PROBE GET config", r.status_code, r.get_data(as_text=True)[:200])

    from app.modules.codegen.routes._helpers import _check_access
    from app.modules.solutions_strategic.v2.routes.solution_design_routes import (
        _check_solution_access,
        _may_edit_solution,
    )

    with app.test_request_context("/"):
        from flask_login import login_user

        login_user(viewer)
        print("PROBE codegen read:", _check_access(solution))
        print("PROBE codegen write:", _check_access(solution, write=True))
        print("PROBE design read:", _check_solution_access(solution))
        print("PROBE design write:", _may_edit_solution(solution))
        from app.core.auth.permissions import can

        print("PROBE can view_all:", can(viewer, "view_all", "solution", org.id))
        print("PROBE viewer org:", viewer.organization_id, "solution org:", solution.organization_id)
