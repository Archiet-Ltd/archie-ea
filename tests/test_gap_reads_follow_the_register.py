"""The roadmap's gap reads agree with the register, and the uncovered-gaps section reaches every row.

Two organisations in every test: a read that reached across organisations would show the other one's gap.
"""

from __future__ import annotations

from unittest import mock

import pytest

from tests.test_gap_one_severity_reading import LEVELS, _estate, _gap, _login

pytestmark = pytest.mark.usefixtures("db_session")

PREVIEW = "/capability-map/api/roadmap/ai-generate"


def test_the_preview_answers_a_plateau_transition_and_another_organisations_gap_like_any_id_that_is_no_gap(
    db_session, make_org, client, login_as
):
    mine, _theirs, gaps, foreign = _estate(db_session, make_org)
    _login(client, login_as, db_session, mine, "followpreview")
    for not_a_gap in (gaps["plateau"].id, foreign.id, 2147483000):
        resp = client.post(PREVIEW, json={"gap_ids": [not_a_gap]})
        assert resp.status_code == 404, (not_a_gap, resp.get_json())
        assert resp.get_json()["error"] == "No gaps found for the provided IDs"
    mixed = client.post(PREVIEW, json={"gap_ids": [gaps["plateau"].id, foreign.id, gaps["crit"].id]})
    assert mixed.status_code == 200
    assert [row["gap_id"] for row in mixed.get_json()["preview"]] == [gaps["crit"].id]


def test_every_row_the_roadmap_lists_under_a_level_reads_that_level(db_session, make_org, client, login_as):
    mine, _theirs, gaps, foreign = _estate(db_session, make_org)
    _login(client, login_as, db_session, mine, "followrows")
    expected = {"critical": {gaps["crit"].id}, "medium": {gaps["plain"].id}, "low": {gaps["closed"].id},
                "high": set(), "unrated": {gaps["unrated"].id}}
    assert set(expected) == set(LEVELS)
    for level in LEVELS:
        for query in ("priority", "severity"):
            rows = client.get("/api/roadmap/gaps?%s=%s" % (query, level)).get_json()["gaps"]
            assert {row["id"] for row in rows} == expected[level], (query, level)
            assert {row["priority"] for row in rows} <= {level}, (query, level, [r["priority"] for r in rows])
    everything = client.get("/api/roadmap/gaps").get_json()["gaps"]
    assert {row["id"]: row["priority"] for row in everything} == {
        gaps["crit"].id: "critical", gaps["plain"].id: "medium",
        gaps["unrated"].id: "unrated", gaps["closed"].id: "low"}
    assert foreign.id not in {row["id"] for row in everything}


def test_the_uncovered_section_links_to_the_full_list_when_it_shows_only_a_page(
    db_session, make_org, client, login_as
):
    from app.services import work_package_service

    mine, theirs = make_org("follow-all"), make_org("follow-all-other")
    from tests.test_work_package_writer import _user

    login_as(client, _user(db_session, mine, "followall", role="enterprise_architect"))
    covered = [_gap(db_session, mine, "Covered %d" % n, priority="low", severity="low") for n in range(6)]
    bare = [_gap(db_session, mine, "Bare %d" % n, priority="low", severity="low") for n in range(5)]
    closed_bare = _gap(db_session, mine, "Closed bare", priority="low", severity="low", resolution_status="closed")
    foreign = _gap(db_session, theirs, "Foreign bare", priority="low", severity="low")
    for gap in covered:
        package = work_package_service.create_work_package(organization_id=mine.id, name="Close %s" % gap.name)
        work_package_service.link_gap(package.id, gap.id, organization_id=mine.id)

    # A page of three stands in for the 500 of the real page: the section shows three of the five.
    with mock.patch("app.routes.unified_enterprise_routes._GAP_PAGE_SIZE", 3):
        html = client.get("/enterprise/implementation/gap-analysis").get_data(as_text=True)
        section = html[html.index('data-testid="gaps-not-addressed"'):]
        assert "Showing gaps 1 to 3 of 5 with no work package." in section
        assert section.count('data-testid="not-addressed-gap-row"') == 3
        assert 'href="/enterprise/implementation/gap-analysis?status=open&amp;work=none"' in section
        assert "See all 5 gaps with no work package" in section

        landed = client.get("/enterprise/implementation/gap-analysis?status=open&work=none")
        assert landed.status_code == 200
        first = landed.get_data(as_text=True)
        assert "Showing gaps 1 to 3 of 5." in first
        second = client.get("/enterprise/implementation/gap-analysis?status=open&work=none&page=2").get_data(
            as_text=True)
        assert "Showing gaps 4 to 5 of 5." in second
    listed = first + second
    assert all(gap.name in listed for gap in bare)
    assert closed_bare.name not in listed and foreign.name not in listed
    assert not any(gap.name in listed for gap in covered)

    # With every row on the section, there is nothing more to reach, so no link.
    html = client.get("/enterprise/implementation/gap-analysis").get_data(as_text=True)
    assert "5 gaps have no work package." in html and "gaps-not-addressed-all" not in html
