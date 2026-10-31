"""A gap and a work package are linked once, and the link is seen from both sides.

The one link record is the ArchiMate association between a work package's element
and a gap's element, read through work_package_service. These tests hold the
two-organisation fence (no link, no read across organisations), the write role
(a Viewer cannot link), CSRF on the POSTs, and every place the link is shown:
the work package detail, the roadmap work package list (its Gap column), the gap
analysis page, the plateau page and the "not addressed" list.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _user(db_session, org, label, role_name="Editor", enterprise_role="enterprise_architect"):
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(
        email=f"gwp-{label}-{uuid.uuid4().hex[:8]}@example.com",
        first_name=label.capitalize(),
        last_name="Tester",
        organization_id=org.id,
        confirmed=True,
        enterprise_role=enterprise_role,
        role=Role.query.filter_by(name=role_name).first(),
    )
    user.password = "Test!12345"
    db_session.add(user)
    db_session.flush()
    return user


def _gap(db_session, org, name=None):
    from app.services.gap_register_service import create_gap

    gap, _ = create_gap(org.id, name or f"Gap {uuid.uuid4().hex[:6]}", gap_type="coverage")
    db_session.flush()
    return gap


def _wp(db_session, org, name=None, **extra):
    from app.services import work_package_service

    wp = work_package_service.create_work_package(
        organization_id=org.id, name=name or f"WP {uuid.uuid4().hex[:6]}", **extra)
    db_session.flush()
    return wp


def _plateau(db_session, org, name=None):
    from app.models.implementation_migration import Plateau

    plateau = Plateau(name=name or f"Plateau {uuid.uuid4().hex[:6]}", organization_id=org.id,
                      sequence_order=1)
    db_session.add(plateau)
    db_session.flush()
    return plateau


@pytest.fixture
def world(db_session, make_org):
    a, b = make_org("gwp-a"), make_org("gwp-b")
    return {
        "a": a, "b": b,
        "editor_a": _user(db_session, a, "edita"),
        "viewer_a": _user(db_session, a, "viewa", role_name="Viewer"),
        "editor_b": _user(db_session, b, "editb"),
    }


def _post(client, login_as, user, url):
    login_as(client, user)
    return client.post(url)


def _link_url(gap, wp, verb="link"):
    return f"/capability-map/api/roadmap/gaps/{gap.id}/{verb}-work-package/{wp.id}"


# -- the service: one link, two directions -------------------------------------


def test_link_is_read_from_both_sides_and_removes_the_gap_from_not_addressed(db_session, world):
    from app.services import work_package_service as svc
    from app.services.gap_register_service import count_gaps, get_gaps_not_addressed

    a = world["a"]
    gap, other = _gap(db_session, a), _gap(db_session, a)
    wp = _wp(db_session, a)
    assert {g.id for g in get_gaps_not_addressed(a.id)} == {gap.id, other.id}

    svc.link_gap(wp.id, gap.id, organization_id=a.id)
    db_session.commit()

    assert [g.id for g in get_gaps_not_addressed(a.id)] == [other.id]
    assert count_gaps(a.id, has_work_package=True) == 1
    assert count_gaps(a.id, has_work_package=False) == 1
    assert svc.gaps_for_work_packages([wp], a.id)[wp.id] == [{"id": gap.id, "name": gap.name}]
    assert [w.id for w in svc.work_packages_for_gaps([gap.id], a.id)[gap.id]] == [wp.id]
    assert svc.to_roadmap_dict(wp, a.id)["gaps"] == [{"id": gap.id, "name": gap.name}]


def test_linking_twice_changes_nothing_and_a_second_gap_is_added(db_session, world):
    from app.services import work_package_service as svc

    a = world["a"]
    g1, g2 = _gap(db_session, a), _gap(db_session, a)
    wp = _wp(db_session, a)
    svc.link_gap(wp.id, g1.id, organization_id=a.id)
    svc.link_gap(wp.id, g1.id, organization_id=a.id)
    svc.link_gap(wp.id, g2.id, organization_id=a.id)
    assert sorted(svc.work_package_ids_for_gap(g1.id, a.id)) == [wp.id]
    assert {g["id"] for g in svc.gaps_for_work_packages([wp], a.id)[wp.id]} == {g1.id, g2.id}


def test_unlink_removes_only_that_link(db_session, world):
    from app.services import work_package_service as svc
    from app.services.gap_register_service import get_gaps_not_addressed

    a = world["a"]
    g1, g2 = _gap(db_session, a), _gap(db_session, a)
    wp = _wp(db_session, a)
    svc.link_gap(wp.id, g1.id, organization_id=a.id)
    svc.link_gap(wp.id, g2.id, organization_id=a.id)
    assert svc.unlink_gap(wp.id, g1.id, organization_id=a.id) == 1
    assert svc.unlink_gap(wp.id, g1.id, organization_id=a.id) == 0
    assert [g["id"] for g in svc.gaps_for_work_packages([wp], a.id)[wp.id]] == [g2.id]
    assert [g.id for g in get_gaps_not_addressed(a.id)] == [g1.id]


def test_no_link_and_no_read_across_organisations(db_session, world):
    from app.services import work_package_service as svc
    from app.services.gap_register_service import get_gaps_not_addressed

    a, b = world["a"], world["b"]
    gap_a, gap_b = _gap(db_session, a), _gap(db_session, b)
    wp_a, wp_b = _wp(db_session, a), _wp(db_session, b)

    with pytest.raises(svc.WorkPackageNotFound):
        svc.link_gap(wp_a.id, gap_b.id, organization_id=a.id)      # B's gap, A's package
    with pytest.raises(svc.WorkPackageNotFound):
        svc.link_gap(wp_b.id, gap_a.id, organization_id=a.id)      # A's gap, B's package
    assert svc.gaps_for_work_packages([wp_a], a.id)[wp_a.id] == []

    svc.link_gap(wp_b.id, gap_b.id, organization_id=b.id)
    db_session.commit()
    # B's link is invisible to A, and does not make A's gaps addressed.
    assert svc.work_packages_for_gaps([gap_b.id], a.id)[gap_b.id] == []
    assert svc.gaps_for_work_packages([wp_b], a.id)[wp_b.id] == []
    assert [g.id for g in get_gaps_not_addressed(a.id)] == [gap_a.id]
    assert get_gaps_not_addressed(b.id) == []


# -- the routes: role, CSRF, organisation -------------------------------------------


def test_editor_links_and_unlinks_through_the_route(db_session, world, client, login_as):
    from app.services import work_package_service as svc

    a = world["a"]
    gap, wp = _gap(db_session, a), _wp(db_session, a)
    resp = _post(client, login_as, world["editor_a"], _link_url(gap, wp))
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["success"] is True
    assert body["work_package"]["gaps"] == [{"id": gap.id, "name": gap.name}]
    assert svc.work_package_ids_for_gap(gap.id, a.id) == [wp.id]

    resp = _post(client, login_as, world["editor_a"], _link_url(gap, wp, "unlink"))
    assert resp.status_code == 200 and resp.get_json()["removed"] == 1
    assert svc.work_package_ids_for_gap(gap.id, a.id) == []


def test_a_viewer_cannot_link_or_unlink(db_session, world, client, login_as):
    from app.services import work_package_service as svc

    a = world["a"]
    gap, wp = _gap(db_session, a), _wp(db_session, a)
    assert _post(client, login_as, world["viewer_a"], _link_url(gap, wp)).status_code == 403
    assert svc.work_package_ids_for_gap(gap.id, a.id) == []
    svc.link_gap(wp.id, gap.id, organization_id=a.id)
    assert _post(client, login_as, world["viewer_a"], _link_url(gap, wp, "unlink")).status_code == 403
    assert svc.work_package_ids_for_gap(gap.id, a.id) == [wp.id]


def test_a_gap_or_package_of_another_organisation_is_not_found(db_session, world, client, login_as):
    from app.services import work_package_service as svc

    a, b = world["a"], world["b"]
    gap_a, gap_b = _gap(db_session, a), _gap(db_session, b)
    wp_a, wp_b = _wp(db_session, a), _wp(db_session, b)
    assert _post(client, login_as, world["editor_a"], _link_url(gap_b, wp_a)).status_code == 404
    assert _post(client, login_as, world["editor_a"], _link_url(gap_a, wp_b)).status_code == 404
    assert svc.work_package_ids_for_gap(gap_b.id, b.id) == []
    assert svc.work_package_ids_for_gap(gap_a.id, a.id) == []


def test_link_posts_need_a_csrf_token(app, db_session, world, client, login_as):
    from app.services import work_package_service as svc

    a = world["a"]
    gap, wp = _gap(db_session, a), _wp(db_session, a)
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        for verb in ("link", "unlink"):
            resp = _post(client, login_as, world["editor_a"], _link_url(gap, wp, verb))
            assert resp.status_code == 400, (verb, resp.status_code)
    finally:
        app.config["WTF_CSRF_ENABLED"] = False
    assert svc.work_package_ids_for_gap(gap.id, a.id) == []


def test_pickers_search_only_the_callers_organisation(db_session, world, client, login_as):
    a, b = world["a"], world["b"]
    mine = _gap(db_session, a, "Zebra shortfall")
    _gap(db_session, b, "Zebra elsewhere")
    mine_wp = _wp(db_session, a, "Zebra rollout")
    _wp(db_session, b, "Zebra other")

    login_as(client, world["editor_a"])
    gaps = client.get("/capability-map/api/roadmap/gaps/search?q=zebra").get_json()["gaps"]
    assert [g["id"] for g in gaps] == [mine.id]
    login_as(client, world["editor_a"])
    wps = client.get("/capability-map/api/roadmap/work-packages/search?q=zebra").get_json()
    assert [w["id"] for w in wps["work_packages"]] == [mine_wp.id]


# -- where the link is shown ------------------------------------------------------------


def test_work_package_detail_shows_gap_owner_and_progress(db_session, world, client, login_as):
    from app.services import work_package_service as svc

    a = world["a"]
    owner = _user(db_session, a, "owner", enterprise_role="portfolio_manager")
    gap = _gap(db_session, a, "Payroll has no owner")
    wp = _wp(db_session, a, "Appoint payroll owner", owner_id=owner.id, progress_percentage=40)
    svc.link_gap(wp.id, gap.id, organization_id=a.id)
    db_session.commit()

    login_as(client, world["editor_a"])
    resp = client.get(f"/implementation/work-packages/{wp.id}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Payroll has no owner" in html
    assert "Owner Tester" in html
    assert 'data-testid="wp-progress"' in html and "40%" in html
    assert 'data-testid="gap-picker-input"' in html


def test_work_package_detail_shows_a_dash_for_what_is_missing(db_session, world, client, login_as):
    a = world["a"]
    wp = _wp(db_session, a, "Bare package")
    wp.progress_percentage = None
    db_session.commit()
    login_as(client, world["editor_a"])
    html = client.get(f"/implementation/work-packages/{wp.id}").get_data(as_text=True)
    import re

    cell = lambda tid: re.sub(r"\s+", " ", re.search(  # noqa: E731
        r'data-testid="%s"[^>]*>(.*?)</dd>' % tid, html, re.S).group(1)).strip()
    assert cell("wp-progress") == "—"
    # a missing owner is said in words with the way to name one, never a dash
    assert "No owner yet" in cell("wp-owner") and 'data-testid="wp-assign-owner"' in cell("wp-owner")
    assert 'data-testid="wp-no-gaps"' in html


def test_a_viewer_sees_the_detail_without_the_link_control(db_session, world, client, login_as):
    a = world["a"]
    wp = _wp(db_session, a)
    login_as(client, world["viewer_a"])
    html = client.get(f"/implementation/work-packages/{wp.id}").get_data(as_text=True)
    assert 'data-testid="gap-picker-input"' not in html


def test_enterprise_list_rows_open_the_detail_and_show_the_gap(db_session, world, client, login_as):
    from app.services import work_package_service as svc

    a, b = world["a"], world["b"]
    gap = _gap(db_session, a, "Visible in the list")
    wp = _wp(db_session, a, "Listed package", progress_percentage=40)
    svc.link_gap(wp.id, gap.id, organization_id=a.id)
    wp_b = _wp(db_session, b, "Other tenant package")
    db_session.commit()

    login_as(client, world["editor_a"])
    rows = client.get("/enterprise/api/work-packages").get_json()["work_packages"]
    assert [r["id"] for r in rows] == [wp.id]
    assert rows[0]["detail_url"] == f"/implementation/work-packages/{wp.id}"
    assert rows[0]["gaps"] == [{"id": gap.id, "name": gap.name}]
    assert rows[0]["percent_complete"] == 40
    assert wp_b.id not in [r["id"] for r in rows]


def test_roadmap_gap_column_is_filled_from_the_link(db_session, world, client, login_as):
    from app.services import work_package_service as svc

    a = world["a"]
    gap = _gap(db_session, a, "Roadmap gap name")
    wp = _wp(db_session, a, "Roadmap package")
    svc.link_gap(wp.id, gap.id, organization_id=a.id)
    db_session.commit()
    login_as(client, world["editor_a"])
    data = client.get("/capability-map/api/roadmap/work-packages").get_json()
    row = next(w for w in data["work_packages"] if w["id"] == wp.id)
    assert row["gaps"] == [{"id": gap.id, "name": "Roadmap gap name"}]
    html = client.get("/capability-roadmap").get_data(as_text=True)
    assert 'data-testid="roadmap-wp-gaps"' in html


def test_gap_analysis_lists_the_work_packages_of_each_gap(db_session, world, client, login_as):
    from app.services import work_package_service as svc

    a, b = world["a"], world["b"]
    gap = _gap(db_session, a, "Gap with a package")
    wp = _wp(db_session, a, "Closing package")
    svc.link_gap(wp.id, gap.id, organization_id=a.id)
    wp_b = _wp(db_session, b, "Foreign closing package")
    gap_b = _gap(db_session, b, "Foreign gap")
    svc.link_gap(wp_b.id, gap_b.id, organization_id=b.id)
    db_session.commit()

    login_as(client, world["editor_a"])
    html = client.get("/enterprise/implementation/gap-analysis").get_data(as_text=True)
    assert f'data-testid="gap-work-packages-{gap.id}"' in html
    assert "Closing package" in html
    assert "Foreign closing package" not in html and "Foreign gap" not in html
    assert f"/implementation/work-packages/{wp.id}" in html


def test_plateau_lists_linked_gaps_and_gaps_of_the_packages_that_deliver_it(db_session, world):
    from app.services import work_package_service as svc
    from app.services.gap_register_service import get_plateau_gap_rows, link_gap_to_plateau

    a, b = world["a"], world["b"]
    plateau = _plateau(db_session, a)
    direct, via_wp, unrelated = _gap(db_session, a), _gap(db_session, a), _gap(db_session, a)
    link_gap_to_plateau(direct.id, plateau.id, a.id)
    wp = _wp(db_session, a, plateau_id=plateau.id)
    svc.link_gap(wp.id, via_wp.id, organization_id=a.id)
    # the same wiring in organisation B must not reach A's plateau
    plateau_b = _plateau(db_session, b)
    gap_b = _gap(db_session, b)
    wp_b = _wp(db_session, b, plateau_id=plateau_b.id)
    svc.link_gap(wp_b.id, gap_b.id, organization_id=b.id)
    db_session.commit()

    rows = get_plateau_gap_rows([plateau.id, plateau_b.id], a.id)
    assert {(r["id"], r["via"]) for r in rows[plateau.id]} == {
        (direct.id, "linked"), (via_wp.id, "work package")}
    assert unrelated.id not in {r["id"] for r in rows[plateau.id]}
    assert rows[plateau_b.id] == []


def test_plateau_page_shows_its_gaps(db_session, world, client, login_as):
    from app.services import work_package_service as svc

    a = world["a"]
    plateau = _plateau(db_session, a, "Target state 2027")
    gap = _gap(db_session, a, "Gap shown on the plateau")
    wp = _wp(db_session, a, plateau_id=plateau.id)
    svc.link_gap(wp.id, gap.id, organization_id=a.id)
    db_session.commit()
    login_as(client, world["editor_a"])
    html = client.get("/enterprise/implementation/plateaus").get_data(as_text=True)
    assert f'data-testid="plateau-gaps-{plateau.id}"' in html
    assert "Gap shown on the plateau" in html
