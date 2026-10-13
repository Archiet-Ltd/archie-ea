"""Critical path to a plateau, computed on the one work package store.

A transformation lead sees the critical path to the next plateau computed
from work package schedules and dependencies -- the longest chain, its total
duration, and the blocking item with its owner -- and it recomputes when the
blocking package's planned end date moves. A dependency cycle is reported,
never followed, and a dependency on another organisation's work package is
ignored and reported, never followed.

Everything is read from the one work package store and its work_dependencies
list; there is no second schedule store and no migration.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _user(db_session, org, label, role="enterprise_architect"):
    from app.models.user import Role, User

    Role.insert_roles()
    role_obj = Role.query.filter_by(name="User").first()
    user = User(
        email=f"cp-{label}-{uuid.uuid4().hex[:8]}@example.com",
        first_name=label.capitalize(),
        last_name="Test",
        organization_id=org.id,
        confirmed=True,
        enterprise_role=role,
        role_id=role_obj.id if role_obj else None,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _plateau(db_session, org, name="Target plateau"):
    from app.models.implementation_migration import Plateau

    row = Plateau(name=f"{name} {uuid.uuid4().hex[:6]}", organization_id=org.id)
    db_session.add(row)
    db_session.flush()
    return row


def _wp(db_session, org, name, plateau=None, start=None, end=None, owner=None):
    from app.services import work_package_service as svc

    kwargs = {}
    if plateau is not None:
        kwargs["plateau_id"] = plateau.id
    wp = svc.create_work_package(organization_id=org.id, name=name, **kwargs)
    if start is not None:
        wp.start_date = start
    if end is not None:
        wp.end_date = end
    if owner is not None:
        wp.owner_id = owner.id
    db_session.flush()
    return wp


@pytest.fixture
def two_orgs(db_session, make_org):
    org_a = make_org("cp-a")
    org_b = make_org("cp-b")
    return {
        "a": org_a,
        "b": org_b,
        "user_a": _user(db_session, org_a, "pma"),
        "lead_a": _user(db_session, org_a, "lea"),
        "owner_a": _user(db_session, org_a, "owna"),
        "user_b": _user(db_session, org_b, "pmb"),
    }


# -- the one computation ----------------------------------------------------


def test_critical_path_returns_chain_duration_and_blocking_with_its_owner(
    two_orgs, db_session
):
    from app.services import work_package_service as svc

    a = two_orgs["a"]
    owner = two_orgs["owner_a"]
    plateau = _plateau(db_session, a, "Next plateau")
    w1 = _wp(db_session, a, "W1", plateau, start=datetime(2026, 1, 1), end=datetime(2026, 1, 5))
    w2 = _wp(db_session, a, "W2", plateau, start=datetime(2026, 1, 6), end=datetime(2026, 1, 8))
    w3 = _wp(db_session, a, "W3", plateau, start=datetime(2026, 1, 9), end=datetime(2026, 1, 12))
    w4 = _wp(db_session, a, "W4", plateau, start=datetime(2026, 1, 13),
             end=datetime(2026, 1, 15), owner=owner)
    svc.add_dependency(w2.id, w1.id, organization_id=a.id)
    svc.add_dependency(w3.id, w2.id, organization_id=a.id)
    svc.add_dependency(w4.id, w3.id, organization_id=a.id)

    cp = svc.critical_path_for_plateau(plateau.id, a.id)

    assert cp["plateau_id"] == plateau.id
    assert cp["has_critical_path"] is True
    assert [n["name"] for n in cp["path"]] == ["W1", "W2", "W3", "W4"]
    # 4 + 2 + 3 + 2 planned days
    assert cp["duration_days"] == 11
    assert cp["blocking"]["id"] == w4.id
    assert cp["blocking"]["name"] == "W4"
    assert cp["blocking"]["owner_name"] == "Owna Test"
    assert cp["cycles"] == []
    assert cp["ignored_dependencies"] == []

    # moving the blocking package's planned end date changes the path
    svc.update_work_package(w4.id, organization_id=a.id, end_date=datetime(2026, 1, 20))
    moved = svc.critical_path_for_plateau(plateau.id, a.id)
    assert moved["duration_days"] == 16
    assert moved["blocking"]["end_date"] == "2026-01-20"
    assert moved["path"][-1]["id"] == w4.id


def test_plateau_without_scheduled_work_packages_has_no_critical_path(two_orgs, db_session):
    from app.services import work_package_service as svc

    a = two_orgs["a"]
    plateau = _plateau(db_session, a, "Empty plateau")
    cp = svc.critical_path_for_plateau(plateau.id, a.id)
    assert cp["has_critical_path"] is False
    assert cp["path"] == []
    assert cp["blocking"] is None
    assert cp["duration_days"] == 0


def test_dependency_cycle_is_reported_and_never_followed(two_orgs, db_session):
    from app.services import work_package_service as svc

    a = two_orgs["a"]
    plateau = _plateau(db_session, a, "Cycled")
    w1 = _wp(db_session, a, "Cycle one", plateau)
    w2 = _wp(db_session, a, "Cycle two", plateau)
    w1.work_dependencies = [w2.id]
    w2.work_dependencies = [w1.id]
    db_session.flush()

    cp = svc.critical_path_for_plateau(plateau.id, a.id)

    # the call returned: a cycle never hangs the computation
    assert cp["cycles"] != []
    assert set(cp["cycles"][0]) == {w1.id, w2.id}
    assert cp["has_critical_path"] is True
    # the cycle is not followed: no node appears twice on the path
    names = [n["name"] for n in cp["path"]]
    assert len(names) == len(set(names))


def test_dependency_on_another_organisations_work_package_is_ignored_and_reported(
    two_orgs, db_session
):
    from app.services import work_package_service as svc

    a, b = two_orgs["a"], two_orgs["b"]
    plateau_a = _plateau(db_session, a, "A plateau")
    plateau_b = _plateau(db_session, b, "B plateau")
    mine = _wp(db_session, a, "A task", plateau_a,
               start=datetime(2026, 2, 1), end=datetime(2026, 2, 10))
    theirs = _wp(db_session, b, "B task", plateau_b,
                 start=datetime(2026, 1, 1), end=datetime(2026, 1, 5))
    # planted raw: the id of another organisation's work package
    mine.work_dependencies = [theirs.id]
    db_session.flush()

    cp = svc.critical_path_for_plateau(plateau_a.id, a.id)

    assert [d["dependency_id"] for d in cp["ignored_dependencies"]] == [theirs.id]
    assert [n["name"] for n in cp["path"]] == ["A task"]
    assert cp["blocking"]["id"] == mine.id
    assert cp["blocking"]["owner_name"] is None


def test_critical_paths_for_roadmap_are_organisation_fenced(two_orgs, db_session):
    from app.services import work_package_service as svc

    a, b = two_orgs["a"], two_orgs["b"]
    plateau_a = _plateau(db_session, a, "A next")
    _wp(db_session, a, "A work", plateau_a, start=datetime(2026, 3, 1),
        end=datetime(2026, 3, 5))
    plateau_b = _plateau(db_session, b, "B next")
    _wp(db_session, b, "B work", plateau_b, start=datetime(2026, 3, 1),
        end=datetime(2026, 3, 5))

    paths = svc.critical_paths_for_roadmap(a.id)

    assert [p["plateau_id"] for p in paths] == [plateau_a.id]
    dumped = json.dumps(paths)
    assert "B work" not in dumped
    assert "B next" not in dumped


# -- the plateau roadmap screen ---------------------------------------------


def test_plateau_roadmap_page_shows_critical_path_panel(two_orgs, db_session, client, login_as):
    from app.services import work_package_service as svc

    a = two_orgs["a"]
    owner = two_orgs["owner_a"]
    plateau = _plateau(db_session, a, "Next")
    w1 = _wp(db_session, a, "Foundation", plateau,
             start=datetime(2026, 1, 1), end=datetime(2026, 1, 5))
    w2 = _wp(db_session, a, "Build", plateau,
             start=datetime(2026, 1, 6), end=datetime(2026, 1, 10), owner=owner)
    svc.add_dependency(w2.id, w1.id, organization_id=a.id)
    b = two_orgs["b"]
    plateau_b = _plateau(db_session, b, "B plateau")
    _wp(db_session, b, "B work", plateau_b)

    login_as(client, two_orgs["user_a"])
    resp = client.get("/adm-kanban/plateau-roadmap")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'data-testid="critical-path-panel"' in html
    assert "Foundation" in html and "Build" in html
    # the owner of the blocking item appears only in the panel: the logged-in
    # user is Pma Test, not the blocking package's owner
    assert "Owna Test" in html
    assert "Pmb Test" not in html
    assert "B work" not in html
    assert "B plateau" not in html