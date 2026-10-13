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
    assert len(cp["cycles"]) == 1
    assert set(cp["cycles"][0]) == {w1.id, w2.id}
    assert cp["has_critical_path"] is True
    # the cycle is not followed: no node appears twice on the path
    names = [n["name"] for n in cp["path"]]
    assert len(names) == len(set(names))


def test_three_node_cycle_is_reported_exactly_once(two_orgs, db_session):
    from app.services import work_package_service as svc

    a = two_orgs["a"]
    plateau = _plateau(db_session, a, "Triangle")
    w1 = _wp(db_session, a, "Tri one", plateau)
    w2 = _wp(db_session, a, "Tri two", plateau)
    w3 = _wp(db_session, a, "Tri three", plateau)
    w1.work_dependencies = [w2.id]
    w2.work_dependencies = [w3.id]
    w3.work_dependencies = [w1.id]
    db_session.flush()

    cp = svc.critical_path_for_plateau(plateau.id, a.id)

    # each of the three packages is a root, so the loop is met from three
    # starting points: still one entry, not rotated copies
    assert len(cp["cycles"]) == 1
    assert sorted(cp["cycles"][0]) == sorted([w1.id, w2.id, w3.id])
    assert len(cp["cycles"][0]) == 3


def test_self_loop_and_two_node_cycle_in_one_plateau_are_both_reported(
    two_orgs, db_session
):
    from app.services import work_package_service as svc

    a = two_orgs["a"]
    plateau = _plateau(db_session, a, "Mixed loops")
    w1 = _wp(db_session, a, "Loop alone", plateau)
    w2 = _wp(db_session, a, "Pair one", plateau)
    w3 = _wp(db_session, a, "Pair two", plateau)
    w1.work_dependencies = [w1.id]
    w2.work_dependencies = [w3.id]
    w3.work_dependencies = [w2.id]
    db_session.flush()

    cp = svc.critical_path_for_plateau(plateau.id, a.id)

    assert len(cp["cycles"]) == 2
    assert sorted(sorted(c) for c in cp["cycles"]) == sorted(
        [[w1.id], sorted([w2.id, w3.id])]
    )


def test_self_loop_dependency_is_reported_as_a_cycle(two_orgs, db_session):
    from app.services import work_package_service as svc

    a = two_orgs["a"]
    plateau = _plateau(db_session, a, "Self-loop")
    w1 = _wp(db_session, a, "Self-loop wp", plateau,
             start=datetime(2026, 4, 1), end=datetime(2026, 4, 5))
    # planted raw: the column is JSON read directly (merge, bridge, seed paths)
    w1.work_dependencies = [w1.id]
    db_session.flush()

    cp = svc.critical_path_for_plateau(plateau.id, a.id)

    # the self-loop is reported as a cycle naming the package itself
    assert cp["cycles"] != []
    assert cp["cycles"][0] == [w1.id]
    assert cp["has_critical_path"] is True
    # the cycle is not followed: the path has no duplicate
    names = [n["name"] for n in cp["path"]]
    assert len(names) == len(set(names))


def test_retired_roadmap_row_delegates_to_own_organisation_only(two_orgs, db_session):
    from app.models.roadmap_models import RoadmapWorkPackage
    from app.models.unified_work_package import UnifiedWorkPackage
    from app.services import work_package_service as svc

    a, b = two_orgs["a"], two_orgs["b"]
    user_a = two_orgs["user_a"]
    # A unified work package in org B
    wp_b = _wp(db_session, b, "B wp", plateau=None,
               start=datetime(2026, 5, 1), end=datetime(2026, 5, 5))
    # A retired roadmap row pointing at org B's unified row
    retired = RoadmapWorkPackage(
        name="Retired row", business_capability="Cap",
        created_by=user_a.id, retired_into_id=wp_b.id,
    )
    db_session.add(retired)
    db_session.flush()

    # Under org A's context, the delegation to org B's unified row returns
    # the empty critical path.
    result = retired.get_critical_path_impact(organization_id=a.id)
    assert result["has_critical_path"] is False
    assert result["path"] == []
    assert result["blocking"] is None

    # Unknown organisation: nothing is shown, the copy is not consulted.
    assert retired.get_critical_path_impact()["has_critical_path"] is False
    assert retired.get_critical_path_impact(organization_id=None)["path"] == []


def test_retired_roadmap_row_in_same_organisation_returns_the_computed_path(
    two_orgs, db_session
):
    from app.models.roadmap_models import RoadmapWorkPackage

    a, b = two_orgs["a"], two_orgs["b"]
    plateau = _plateau(db_session, a, "Delegated plateau")
    first = _wp(db_session, a, "Delegated first", plateau,
                start=datetime(2026, 6, 1), end=datetime(2026, 6, 3))
    last = _wp(db_session, a, "Delegated last", plateau,
               start=datetime(2026, 6, 4), end=datetime(2026, 6, 8))
    last.work_dependencies = [first.id]
    db_session.flush()
    retired = RoadmapWorkPackage(
        name="Retired row same org", business_capability="Cap",
        created_by=two_orgs["user_a"].id, retired_into_id=last.id,
    )
    db_session.add(retired)
    db_session.flush()

    result = retired.get_critical_path_impact(organization_id=a.id)

    assert result["has_critical_path"] is True
    assert [n["name"] for n in result["path"]] == ["Delegated first", "Delegated last"]
    assert result["blocking"]["id"] == last.id
    # an unknown organisation shows nothing even though the path exists
    assert retired.get_critical_path_impact()["has_critical_path"] is False
    assert retired.get_critical_path_impact(organization_id=None)["path"] == []
    # the same row seen from the other organisation shows nothing
    assert retired.get_critical_path_impact(organization_id=b.id)["path"] == []


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


def test_critical_paths_for_roadmap_with_unknown_organisation_shows_nothing(
    two_orgs, db_session, monkeypatch
):
    from app.models.implementation_migration import Plateau
    from app.services import work_package_service as svc

    a = two_orgs["a"]
    plateau = _plateau(db_session, a, "Exists for A")
    _wp(db_session, a, "A real work", plateau, start=datetime(2026, 3, 1),
        end=datetime(2026, 3, 5))
    assert svc.critical_paths_for_roadmap(a.id) != []  # the row exists

    class _NoQuery:
        def __getattr__(self, name):
            raise AssertionError("unknown organisation must not query plateaus")

    monkeypatch.setattr(Plateau, "query", _NoQuery())

    assert svc.critical_paths_for_roadmap(None) == []


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



# -- long chains, query counts, panel isolation, order independence ----------


def test_chain_of_eleven_hundred_packages_does_not_overflow_the_stack(two_orgs, db_session):
    from app.models.unified_work_package import UnifiedWorkPackage
    from app.services import work_package_service as svc

    a = two_orgs["a"]
    plateau = _plateau(db_session, a, "Long chain")
    rows = [
        UnifiedWorkPackage(name=f"Link {i:04d}", organization_id=a.id, duration_days=1)
        for i in range(1100)
    ]
    db_session.add_all(rows)
    db_session.flush()
    for earlier, later in zip(rows, rows[1:]):
        later.work_dependencies = [earlier.id]
    head = _wp(db_session, a, "Head of chain", plateau)
    head.duration_days = 1
    head.work_dependencies = [rows[-1].id]
    db_session.flush()

    cp = svc.critical_path_for_plateau(plateau.id, a.id)

    assert cp["has_critical_path"] is True
    assert len(cp["path"]) == 1101
    assert cp["path"][0]["name"] == "Link 0000"
    assert cp["path"][-1]["id"] == head.id
    assert cp["duration_days"] == 1101


def _count_statements():
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    class _Counter:
        def __init__(self):
            self.n = 0

        def __call__(self, *args, **kwargs):
            self.n += 1

        def __enter__(self):
            event.listen(Engine, "before_cursor_execute", self)
            return self

        def __exit__(self, *exc):
            event.remove(Engine, "before_cursor_execute", self)

    return _Counter()


def _populate_plateaus(db_session, org, owner, plateaus, packages_each):
    from app.services import work_package_service as svc

    for p in range(plateaus):
        plateau = _plateau(db_session, org, f"Plateau {p}")
        previous = None
        for k in range(packages_each):
            wp = _wp(db_session, org, f"P{p} W{k}", plateau,
                     start=datetime(2026, 1, 1 + k), end=datetime(2026, 1, 2 + k),
                     owner=owner)
            if previous is not None:
                svc.add_dependency(wp.id, previous.id, organization_id=org.id)
            previous = wp


def test_roadmap_query_count_is_flat_in_the_number_of_plateaus(two_orgs, db_session):
    from app.services import work_package_service as svc

    a, b = two_orgs["a"], two_orgs["b"]
    owner_a = two_orgs["owner_a"]
    owner_b = _user(db_session, b, "ownb")
    _populate_plateaus(db_session, a, owner_a, plateaus=2, packages_each=3)
    _populate_plateaus(db_session, b, owner_b, plateaus=7, packages_each=3)
    svc.critical_paths_for_roadmap(a.id)  # warm: first-use lazy loads

    with _count_statements() as few:
        small = svc.critical_paths_for_roadmap(a.id)
    with _count_statements() as many:
        large = svc.critical_paths_for_roadmap(b.id)

    assert len(small) == 2 and len(large) == 7
    assert all(cp["has_critical_path"] for cp in large)
    assert large[0]["blocking"]["owner_name"] == "Ownb Test"
    # reads are per organisation, not per plateau or per package
    assert many.n == few.n


def test_panel_failure_keeps_the_plateau_cards(two_orgs, db_session, client, login_as,
                                               monkeypatch):
    from app.models.adm_kanban import ADMPhase, KanbanBoard, KanbanCard
    from app.services import work_package_service as svc

    a = two_orgs["a"]
    user = two_orgs["user_a"]
    suffix = uuid.uuid4().hex[:8]
    phase = ADMPhase(name=f"Phase {suffix}", code=suffix[:10], order=1)
    db_session.add(phase)
    db_session.flush()
    board = KanbanBoard(name="Board", board_type="architecture_development",
                        created_by_id=user.id, organization_id=a.id)
    db_session.add(board)
    db_session.flush()
    card = KanbanCard(
        title="Kept plateau card", adm_phase_id=phase.id, board_id=board.id,
        card_type="task", status="todo", arch_element_type="Plateau",
        created_by_id=user.id, organization_id=a.id,
    )
    db_session.add(card)
    db_session.flush()

    def _boom(organization_id):
        raise RuntimeError("critical path exploded")

    monkeypatch.setattr(svc, "critical_paths_for_roadmap", _boom)
    login_as(client, user)
    resp = client.get("/adm-kanban/plateau-roadmap")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Kept plateau card" in html
    assert 'data-testid="critical-path-panel"' not in html


def test_nameless_blocking_owner_shows_no_email(two_orgs, db_session, client, login_as):
    from app.services import work_package_service as svc

    a = two_orgs["a"]
    nameless = two_orgs["owner_a"]
    nameless.first_name = None
    nameless.last_name = None
    db_session.flush()
    plateau = _plateau(db_session, a, "Nameless owner plateau")
    _wp(db_session, a, "Owned by nobody named", plateau,
        start=datetime(2026, 1, 1), end=datetime(2026, 1, 3), owner=nameless)

    cp = svc.critical_path_for_plateau(plateau.id, a.id)
    assert cp["blocking"]["owner_name"] is None
    assert nameless.email not in json.dumps(cp)

    login_as(client, two_orgs["user_a"])
    html = client.get("/adm-kanban/plateau-roadmap").get_data(as_text=True)
    assert "Owned by nobody named" in html
    assert nameless.email not in html


def test_path_beside_a_cycle_does_not_depend_on_row_id_order(two_orgs, db_session):
    from app.services import work_package_service as svc

    a = two_orgs["a"]

    def build(order, label):
        plateau = _plateau(db_session, a, label)
        made = {}
        for name in order:
            made[name] = _wp(db_session, a, name, plateau if name in ("R1", "R2") else None)
        made["X"].work_dependencies = [made["Y"].id]
        made["Y"].work_dependencies = [made["X"].id]
        made["R1"].work_dependencies = [made["X"].id]
        made["R2"].work_dependencies = [made["Y"].id]
        db_session.flush()
        return svc.critical_path_for_plateau(plateau.id, a.id)

    forward = build(["X", "Y", "R1", "R2"], "Forward")
    backward = build(["Y", "X", "R2", "R1"], "Backward")

    assert [n["name"] for n in forward["path"]] == [n["name"] for n in backward["path"]]
    assert len(forward["cycles"]) == len(backward["cycles"]) == 1
