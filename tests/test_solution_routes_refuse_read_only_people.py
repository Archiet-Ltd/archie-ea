"""A person admitted only to read a solution cannot change it through any route.

The routes are taken from the application's own URL map: every rule under /solutions/<id>/ and every Code
Workbench rule that takes a solution id, with POST, PUT, PATCH or DELETE. A route added later is covered without
anyone remembering to list it. Two organisations, each with a solution; the readers belong to the first.
"""
import re
import uuid

import pytest

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
PASSWORD = "Sup3r-secret-pass!"

# (platform role, persona): a Viewer, a Reader whose persona is a business analyst, and two reviewing personas
# the permission matrix lets read every solution.
READERS = [
    ("Viewer", "viewer"),
    ("Viewer", "business_analyst"),
    ("Viewer", "arb_member"),
    ("Viewer", "cto"),
]


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


@pytest.fixture
def two_orgs(db_session, make_org):
    from app.models.solution_models import Solution

    out = {}
    for key in ("home", "other"):
        org = make_org(f"readonly-{key}")
        # One person per role under test is more than the Community plan admits; the plan is
        # recorded where every limit is read from, the subscriptions row.
        from app.services.billing_plans import set_contract_plan

        set_contract_plan(org, "enterprise", None)
        author = _user(db_session, org, f"author-{key}", "User")
        solution = Solution(name=f"Billing platform {key}", organization_id=org.id, created_by_id=author.id,
                            scratchpad_items=[])
        db_session.add(solution)
        db_session.flush()
        out[key] = {"org": org, "author": author, "solution": solution}
    db_session.commit()
    return out


def _solution_rules(app):
    rules = []
    for rule in app.url_map.iter_rules():
        methods = rule.methods & WRITE_METHODS
        if not methods or "solution_id" not in rule.arguments:
            continue
        if rule.rule.startswith("/solutions/<") or rule.endpoint.startswith("codegen."):
            rules.append((rule, sorted(methods)))
    return sorted(rules, key=lambda item: item[0].rule)


def _fill(rule, solution_id):
    def one(match):
        converter, name = match.group(1) or "string", match.group(2)
        if name == "solution_id":
            return str(solution_id)
        if converter == "int":
            return "987654321"
        if converter == "uuid":
            return str(uuid.uuid4())
        return "x"

    return re.sub(r"<(?:(\w+)(?:\([^)]*\))?:)?(\w+)>", one, rule.rule)


def _snapshot(db_session, solution_id):
    from sqlalchemy import inspect as sa_inspect

    from app.models.architecture_decision import ArchitectureDecision
    from app.models.solution_models import Solution

    db_session.expire_all()
    solution = db_session.get(Solution, solution_id)
    columns = {c.key: repr(getattr(solution, c.key)) for c in sa_inspect(Solution).column_attrs
               if c.key not in ("updated_at", "last_modified", "modified_at")}
    decisions = ArchitectureDecision.query.filter_by(solution_id=solution_id).count()  # tenant-scoping-ok: test count by key
    return columns, decisions


def _drive(app, client, login_as, user, solution_id):
    """Send every state-changing request for the solution as `user`; return the answers that were not a refusal."""
    login_as(client, user)
    admitted = []
    for rule, methods in _solution_rules(app):
        path = _fill(rule, solution_id)
        for method in methods:
            response = client.open(path, method=method, json={}, follow_redirects=False)
            if response.status_code not in (403, 404):
                admitted.append(f"{method} {path} -> {response.status_code}")
    return admitted


def test_the_url_map_has_the_routes_the_sweep_is_about(app):
    rules = _solution_rules(app)
    assert len(rules) > 150, "the sweep must reach the solution and Code Workbench routes"
    paths = {rule.rule for rule, _ in rules}
    for needle in ("/decisions", "/scratchpad", "/api/blueprint-scores/recalculate", "/sync-archimate",
                   "/capabilities", "/archimate-elements", "/generate-relationships", "/codegen/generate"):
        assert any(p.endswith(needle) for p in paths), needle


@pytest.mark.parametrize("role_name,persona", READERS)
def test_a_reader_cannot_change_a_solution_through_any_route(
    app, db_session, client, login_as, two_orgs, role_name, persona
):
    home = two_orgs["home"]
    reader = _user(db_session, home["org"], "reader", role_name, persona)
    db_session.commit()
    before = _snapshot(db_session, home["solution"].id)

    admitted = _drive(app, client, login_as, reader, home["solution"].id)
    assert not admitted, "a reader changed or reached a write route:\n" + "\n".join(admitted[:40])
    assert _snapshot(db_session, home["solution"].id) == before


def test_a_viewer_cannot_change_another_organisations_solution_through_any_route(
    app, db_session, client, login_as, two_orgs
):
    home, other = two_orgs["home"], two_orgs["other"]
    reader = _user(db_session, home["org"], "reader", "Viewer", "viewer")
    db_session.commit()
    before = _snapshot(db_session, other["solution"].id)

    admitted = _drive(app, client, login_as, reader, other["solution"].id)
    assert not admitted, "a reader reached the other organisation:\n" + "\n".join(admitted[:40])
    assert _snapshot(db_session, other["solution"].id) == before


def test_a_reader_still_reads_the_solution_and_cannot_save_a_decision_or_a_note(
    app, db_session, client, login_as, two_orgs
):
    home = two_orgs["home"]
    reader = _user(db_session, home["org"], "reader", "Viewer", "viewer")
    db_session.commit()
    sid = home["solution"].id
    login_as(client, reader)
    assert client.get(f"/solutions/{sid}").status_code == 200
    login_as(client, reader)
    assert client.get(f"/solutions/{sid}/decisions").status_code == 200
    for path, body in ((f"/solutions/{sid}/decisions", {"title": "Use a queue"}),
                       (f"/solutions/{sid}/scratchpad", {"type": "note", "data": {"text": "x"}})):
        login_as(client, reader)
        assert client.post(path, json=body).status_code in (403, 404), path
    assert _snapshot(db_session, sid)[1] == 0


def test_a_reader_cannot_open_or_post_the_edit_page(app, db_session, client, login_as, two_orgs):
    home = two_orgs["home"]
    reader = _user(db_session, home["org"], "reader", "Viewer", "viewer")
    db_session.commit()
    sid = home["solution"].id
    before = _snapshot(db_session, sid)
    login_as(client, reader)
    assert client.get(f"/solutions/{sid}/edit").status_code in (302, 403, 404)
    login_as(client, reader)
    response = client.post(f"/solutions/{sid}/edit", data={"name": "Renamed by a reader"})
    assert response.status_code in (302, 403, 404)
    assert _snapshot(db_session, sid) == before


@pytest.mark.parametrize("label", ["author", "editor", "administrator", "platform"])
def test_the_author_an_editor_an_administrator_and_a_platform_administrator_still_write(
    app, db_session, client, login_as, two_orgs, label
):
    home = two_orgs["home"]
    org = home["org"]
    if label == "author":
        user = home["author"]
    elif label == "editor":
        user = _user(db_session, org, "editor", "Architect", "enterprise_architect")
    elif label == "administrator":
        user = _user(db_session, org, "orgadmin", "Administrator")
    else:
        user = _user(db_session, org, "platform", "Administrator", "platform_admin", is_platform_admin=True)
    db_session.commit()
    sid = home["solution"].id
    login_as(client, user)
    saved = client.post(f"/solutions/{sid}/decisions", json={"title": f"Decision by {label}"})
    assert saved.status_code == 201, (label, saved.status_code, saved.get_data(as_text=True)[:200])
    login_as(client, user)
    note = client.post(f"/solutions/{sid}/scratchpad", json={"type": "note", "data": {"text": label}})
    assert note.status_code == 201, (label, note.status_code)
    assert _snapshot(db_session, sid)[1] == 1
    assert [i["data"]["text"] for i in _snapshot_scratchpad(db_session, sid)] == [label]


def _snapshot_scratchpad(db_session, solution_id):
    from app.models.solution_models import Solution

    db_session.expire_all()
    return db_session.get(Solution, solution_id).scratchpad_items
