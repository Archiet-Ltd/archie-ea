"""The one roadmap: the board, its lenses and filters, and every older address.

Two organisations throughout: what one organisation plans never shows on the
other's roadmap, and another organisation's solution or application id is a 404
rather than an empty board.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

import pytest

from app.models.application_portfolio import ApplicationComponent
from app.models.implementation_migration import Plateau
from app.services import work_package_service
from app.models.solution_models import Solution
from app.models.user import User
from app.modules.genome.services.roadmap_board import build_roadmap_board

TODAY = date(2026, 10, 24)


def _user(db_session, org):
    user = User(
        email=f"road-{uuid.uuid4().hex[:10]}@example.test", organization_id=org.id,
        confirmed=True, enterprise_role="enterprise_architect",
    )
    db_session.add(user)
    db_session.flush()
    return user


def _seed(db_session, org, label):
    """Two plateaus (one finished, one in progress), a far-future one, one loose package."""
    app_component = ApplicationComponent(name=f"{label} CRM", organization_id=org.id)
    solution = Solution(name=f"{label} Billing", organization_id=org.id)
    db_session.add_all([app_component, solution])
    db_session.flush()
    done = Plateau(name=f"{label} Baseline", organization_id=org.id, sequence_order=1, target_date=date(2026, 3, 1))
    now = Plateau(name=f"{label} Target", organization_id=org.id, sequence_order=2, target_date=date(2026, 12, 1))
    later = Plateau(name=f"{label} Horizon", organization_id=org.id, sequence_order=3, target_date=TODAY + timedelta(days=900))
    db_session.add_all([done, now, later])
    db_session.flush()
    when = date(2026, 12, 1)
    rows = {
        "done": work_package_service.create_work_package(
            organization_id=org.id, name=f"{label} Retire mainframe", plateau_id=done.id,
            status="completed", progress_percentage=100, end_date=datetime(2026, 3, 1)),
        "crm": work_package_service.create_work_package(
            organization_id=org.id, name=f"{label} Move CRM", plateau_id=now.id,
            status="in_progress", progress_percentage=40, application_component_id=app_component.id,
            end_date=datetime.combine(when, datetime.min.time())),
        "bill": work_package_service.create_work_package(
            organization_id=org.id, name=f"{label} Rebuild billing", plateau_id=now.id,
            status="planned", togaf_phase="D", end_date=datetime.combine(when, datetime.min.time())),
        "loose": work_package_service.create_work_package(
            organization_id=org.id, name=f"{label} Loose item", status="planned"),
    }
    rows["bill"].context = "solution"
    rows["bill"].context_id = solution.id
    db_session.flush()
    return {"app": app_component, "solution": solution, "plateaus": (done, now, later), "wps": rows}


def _names(board):
    return {c["title"] for col in board["columns"] for c in col["cards"]}


def _cols(board):
    return {col["key"]: {c["title"] for c in col["cards"]} for col in board["columns"]}


def test_board_puts_each_plateau_in_its_column(db_session, make_org, tenant_ctx):
    org = make_org("road-cols")
    _seed(db_session, org, "A")
    with tenant_ctx(org.id):
        board = build_roadmap_board(org.id, today=TODAY)
    cols = _cols(board)
    assert cols["completed"] == {"A Baseline"}
    assert cols["in-progress"] == {"A Target"}
    assert cols["future"] == {"A Horizon"}
    assert cols["planned"] == {"Not on a plateau yet"}
    target = next(c for col in board["columns"] for c in col["cards"] if c["title"] == "A Target")
    assert {i["name"] for i in target["items"]} == {"A Move CRM", "A Rebuild billing"}
    assert target["quarter"] == "Q4 2026"


def test_lenses_narrow_the_same_items(db_session, make_org, tenant_ctx):
    org = make_org("road-lens")
    _seed(db_session, org, "A")
    with tenant_ctx(org.id):
        application = build_roadmap_board(org.id, "application", today=TODAY)
        technology = build_roadmap_board(org.id, "technology", today=TODAY)
        capability = build_roadmap_board(org.id, "capability", today=TODAY)
        product = build_roadmap_board(org.id, "product", today=TODAY)
    assert application["work_package_count"] == 1 and _names(application) == {"A Target"}
    assert technology["work_package_count"] == 1
    assert capability["card_count"] == 0 and capability["work_package_total"] == 4
    assert [c["title"] for c in product["columns"]] == ["Now", "Next", "Later"]
    assert "A Baseline" not in _names(product)


def test_filters_narrow_to_a_solution_or_application(db_session, make_org, tenant_ctx):
    org = make_org("road-filter")
    seeded = _seed(db_session, org, "A")
    with tenant_ctx(org.id):
        by_solution = build_roadmap_board(org.id, solution_id=seeded["solution"].id, today=TODAY)
        by_app = build_roadmap_board(org.id, application_id=seeded["app"].id, today=TODAY)
    assert by_solution["filtered"] and by_solution["work_package_count"] == 1
    assert {i["name"] for col in by_solution["columns"] for c in col["cards"] for i in c["items"]} == {"A Rebuild billing"}
    assert {i["name"] for col in by_app["columns"] for c in col["cards"] for i in c["items"]} == {"A Move CRM"}


def test_one_organisation_never_sees_the_others_roadmap(db_session, make_org, tenant_ctx, client, login_as):
    org_a, org_b = make_org("road-iso-a"), make_org("road-iso-b")
    _seed(db_session, org_a, "Alpha")
    seeded_b = _seed(db_session, org_b, "Bravo")
    user_a = _user(db_session, org_a)
    with tenant_ctx(org_a.id):
        board = build_roadmap_board(org_a.id, today=TODAY)
    assert not any("Bravo" in name for name in _names(board))

    login_as(client, user_a)
    html = client.get("/genome/roadmap/").get_data(as_text=True)
    assert "Alpha Move CRM" in html and "Bravo" not in html
    # Another organisation's solution or application is not a filter that works.
    assert client.get(f"/genome/roadmap/?solution={seeded_b['solution'].id}").status_code == 404
    assert client.get(f"/genome/roadmap/?application={seeded_b['app'].id}").status_code == 404
    assert client.get("/genome/roadmap/?solution=abc").status_code == 404


def test_page_names_what_is_missing_and_the_next_step(db_session, make_org, client, login_as):
    org = make_org("road-empty")
    login_as(client, _user(db_session, org))
    html = client.get("/genome/roadmap/").get_data(as_text=True)
    assert "No plateaus or work packages yet" in html
    assert "/implementation/work-packages/create" in html
    assert "Nothing here yet" not in html


def test_a_view_with_no_match_says_why(db_session, make_org, client, login_as):
    org = make_org("road-nomatch")
    work_package_service.create_work_package(organization_id=org.id, name="Plain work", status="planned")
    db_session.flush()
    login_as(client, _user(db_session, org))
    html = client.get("/genome/roadmap/?lens=capability").get_data(as_text=True)
    assert "Nothing in this view matches" in html
    assert "1 work package" in html


def test_hostile_names_are_inert(db_session, make_org, client, login_as):
    org = make_org("road-xss")
    attack = '<script>alert(1)</script>'
    plateau = Plateau(name=attack, organization_id=org.id)
    db_session.add(plateau)
    db_session.flush()
    work_package_service.create_work_package(
        organization_id=org.id, name=attack, plateau_id=plateau.id, status="planned")
    db_session.flush()
    login_as(client, _user(db_session, org))
    html = client.get("/genome/roadmap/").get_data(as_text=True)
    assert attack not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


OLD_ADDRESSES = {
    "/archimate-roadmap": "/genome/roadmap/",
    "/strategic/roadmap": "/genome/roadmap/",
    "/enterprise/strategic/roadmap": "/genome/roadmap/",
    "/governance/roadmap": "/genome/roadmap/",
    "/adm-kanban/roadmap": "/genome/roadmap/",
    "/adm-kanban/plateau-roadmap": "/genome/roadmap/",
    "/roadmaps": "/genome/roadmap/",
    "/roadmap-builder/": "/genome/roadmap/",
    "/strategic/technology-roadmap": "/genome/roadmap/?lens=technology",
    "/enterprise/strategic/technology-roadmap": "/genome/roadmap/?lens=technology",
    "/technology-roadmap": "/genome/roadmap/?lens=technology",
    "/hybrid-roadmap": "/genome/roadmap/?lens=capability",
}


@pytest.mark.parametrize("old,new", sorted(OLD_ADDRESSES.items()))
def test_every_older_address_lands_on_the_one_roadmap(old, new, db_session, make_org, client, login_as):
    org = make_org("road-redirect")
    login_as(client, _user(db_session, org))
    resp = client.get(old)
    assert resp.status_code == 302, old
    assert resp.headers["Location"].endswith(new), (old, resp.headers["Location"])


def test_older_addresses_still_registered_each_once(app):
    rules = [r.rule for r in app.url_map.iter_rules() if r.rule in OLD_ADDRESSES and "GET" in r.methods]
    assert len(rules) == len(set(rules))
