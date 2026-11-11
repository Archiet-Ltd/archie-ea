"""Every screen and API rates a gap by the one reading, so the level counts agree: the register filter, both gap
APIs, the planning report and the roadmap preview. A gap with no rating is unrated everywhere."""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

LEVELS = ("critical", "high", "medium", "low", "unrated")


def _gap(db_session, org, name=None, **fields):
    from app.models.implementation_migration import Gap

    gap = Gap(organization_id=org.id, name=name or "Gap %s" % uuid.uuid4().hex[:8], gap_type="coverage", **fields)
    db_session.add(gap)
    db_session.flush()
    return gap


def _estate(db_session, make_org):
    """One organisation with a severity-critical medium-priority gap, a plain gap, an unrated gap that carries a
    stored high priority, a closed gap and a plateau transition; a second organisation with its own gap."""
    from app import db
    from app.models.archimate_core import ArchitectureModel
    from app.models.implementation_migration import GAP_KIND_PLATEAU_TRANSITION, Plateau

    mine, theirs = make_org("one-a"), make_org("one-b")
    tag = uuid.uuid4().hex[:6]
    architecture = ArchitectureModel(name="Architecture %s" % tag, organization_id=mine.id)
    db_session.add(architecture)
    db_session.flush()
    as_is, to_be = (Plateau(name="%s %s" % (name, tag), organization_id=mine.id, architecture_id=architecture.id,
                            sequence_order=order) for name, order in (("As-is", 1), ("To-be", 2)))
    db_session.add_all([as_is, to_be])
    db_session.flush()
    gaps = {
        "crit": _gap(db_session, mine, "Crit %s" % tag, priority="medium", severity="critical"),
        "plain": _gap(db_session, mine, "Plain %s" % tag, priority="medium", severity="medium"),
        "unrated": _gap(db_session, mine, "Unrated %s" % tag, priority="high", severity="low"),
        "closed": _gap(db_session, mine, "Closed %s" % tag, priority="low", severity="low", resolution_status="closed"),
        "plateau": _gap(db_session, mine, "Plateau %s" % tag, priority="critical", severity="critical",
                        gap_kind=GAP_KIND_PLATEAU_TRANSITION, originating_plateau_id=as_is.id,
                        target_plateau_id=to_be.id),
    }
    foreign = _gap(db_session, theirs, "Foreign %s" % tag, priority="high", severity="high")
    db_session.execute(db.text("UPDATE gaps SET severity = NULL WHERE id = :i"), {"i": gaps["unrated"].id})
    db_session.expire_all()
    return mine, theirs, gaps, foreign


def _login(client, login_as, db_session, org, name):
    from tests.test_work_package_writer import _user

    login_as(client, _user(db_session, org, name, role="enterprise_architect"))


def test_every_surface_gives_the_same_count_for_each_level(db_session, make_org, client, login_as):
    from app.services.gap_register_service import count_gaps

    mine, _theirs, gaps, foreign = _estate(db_session, make_org)
    _login(client, login_as, db_session, mine, "oneuser")
    names = {k: g.name for k, g in gaps.items()}
    register = {level: count_gaps(mine.id, severity=level) for level in LEVELS}
    assert register == {"critical": 1, "high": 0, "medium": 1, "low": 1, "unrated": 1}
    assert sum(register.values()) == count_gaps(mine.id) == 4  # the plateau transition is not in it

    for level in LEVELS:
        for query in ("priority", "severity"):
            body = client.get("/implementation/api/gaps?%s=%s&limit=100" % (query, level)).get_json()
            assert len(body["gaps"]) == body["total"] == register[level], (query, level, body["total"])
            assert names["plateau"] not in str(body) and foreign.name not in str(body)
        for query in ("priority", "severity"):
            body = client.get("/api/roadmap/gaps?%s=%s" % (query, level)).get_json()
            assert len(body["gaps"]) == register[level], (query, level)
            assert names["plateau"] not in str(body) and foreign.name not in str(body)
    assert names["unrated"] in str(client.get("/api/roadmap/gaps?priority=unrated").get_json())
    assert len(client.get("/api/roadmap/gaps").get_json()["gaps"]) == 4


def test_the_register_filters_unrated_by_the_same_expression(db_session, make_org, client, login_as):
    mine, _theirs, gaps, _foreign = _estate(db_session, make_org)
    _login(client, login_as, db_session, mine, "oneunrated")
    response = client.get("/enterprise/implementation/gap-analysis")
    assert response.status_code == 200, (response.status_code, response.headers.get("Location"))
    everything = response.get_data(as_text=True)
    assert all(gaps[k].name in everything for k in ("crit", "plain", "unrated")), "the page lists no gaps"
    page = client.get("/enterprise/implementation/gap-analysis?severity=unrated").get_data(as_text=True)
    assert gaps["unrated"].name in page
    for key in ("crit", "plain", "closed", "plateau"):
        assert gaps[key].name not in page, key


def test_the_planning_report_and_the_preview_read_an_unrated_gap_as_unrated(db_session, make_org, client, login_as):
    mine, _theirs, gaps, _foreign = _estate(db_session, make_org)
    _login(client, login_as, db_session, mine, "onereport")
    report = client.get("/implementation/reports/generate").get_json()["report"]
    assert report["summary"]["gaps_by_priority"] == {"critical": 1, "medium": 1, "low": 1, "unrated": 1}
    assert report["summary"]["total_gaps"] == 4

    resp = client.post("/capability-map/api/roadmap/ai-generate",
                       json={"gap_ids": [gaps["crit"].id, gaps["plain"].id, gaps["unrated"].id]})
    assert resp.status_code == 200
    rows = {r["gap_id"]: r for r in resp.get_json()["preview"]}
    assert rows[gaps["unrated"].id]["gap_priority"] == "unrated"
    assert rows[gaps["unrated"].id]["suggested_plateau"] == "Plateau 3 — Long Term"
    assert rows[gaps["unrated"].id]["suggested_work_package"]["priority"] is None
    assert rows[gaps["crit"].id]["gap_priority"] == "critical"
    assert rows[gaps["plain"].id]["gap_priority"] == "medium"


@pytest.mark.parametrize("body,named", [
    ({"gap_ids": [99999999999999999999]}, "99999999999999999999"),
    ({"gap_ids": "12"}, "list"),
    ({"gap_ids": [1.5]}, "1.5"),
    ({"gap_ids": [True]}, "true"),
    ({"gap_ids": ["1.5"]}, "1.5"),
    ({"gap_ids": ["-3"]}, "-3"),
    ({"gap_ids": [0]}, "0"),
    ({"gap_ids": [None]}, "null"),
    ({"gap_ids": [{"id": 1}]}, "id"),
])
def test_gap_numbers_must_be_whole_numbers_in_range(db_session, make_org, client, login_as, body, named):
    mine, _theirs, gaps, _foreign = _estate(db_session, make_org)
    _login(client, login_as, db_session, mine, "onenumbers")
    resp = client.post("/capability-map/api/roadmap/ai-generate", json=body)
    assert resp.status_code == 400, resp.get_json()
    assert named in resp.get_json()["error"]
    ok = client.post("/capability-map/api/roadmap/ai-generate",
                     json={"gap_ids": [gaps["crit"].id, str(gaps["plain"].id)]})
    assert ok.status_code == 200 and len(ok.get_json()["preview"]) == 2
