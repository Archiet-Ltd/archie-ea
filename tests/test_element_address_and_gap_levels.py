"""An element's address opens the element's page whatever its type, and every gap is under exactly one level."""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _element(db_session, org, name):
    from app.models.archimate_core import ArchiMateElement

    row = ArchiMateElement(name=name, type="Deliverable", layer="implementation", organization_id=org.id)
    db_session.add(row)
    db_session.flush()
    return row


def _gap(db_session, org, **fields):
    from app.models.implementation_migration import Gap

    gap = Gap(organization_id=org.id, name="Gap %s" % uuid.uuid4().hex[:8], gap_type="coverage", **fields)
    db_session.add(gap)
    db_session.flush()
    return gap


@pytest.mark.parametrize("path", [
    "/architecture/implementation/element/{id}",
    "/architecture/implementation/ArchiMateElement/{id}?ae=1",
    "/architecture/implementation/Deliverable/{id}?ae=1",
])
def test_an_element_address_opens_that_elements_page(db_session, make_org, client, login_as, path):
    from tests.test_work_package_writer import _user

    mine, theirs = make_org("addr-a"), make_org("addr-b")
    name = "Handover pack %s" % uuid.uuid4().hex[:6]
    mine_el = _element(db_session, mine, name)
    theirs_el = _element(db_session, theirs, "Foreign pack %s" % uuid.uuid4().hex[:6])
    login_as(client, _user(db_session, mine, "addruser", role="enterprise_architect"))

    page = client.get(path.format(id=mine_el.id))
    assert page.status_code == 200
    assert name in page.get_data(as_text=True)
    assert client.get(path.format(id=theirs_el.id)).status_code == 404


def test_every_gap_is_under_one_level_and_the_levels_add_up(db_session, make_org):
    from app import db
    from app.services.gap_register_service import count_gaps, gap_register_query, list_gaps

    mine, theirs = make_org("lvl-a"), make_org("lvl-b")
    no_severity_high = _gap(db_session, mine, priority="high", severity="low")
    db_session.execute(db.text("UPDATE gaps SET severity = NULL WHERE id = :i"), {"i": no_severity_high.id})
    db_session.expire(no_severity_high)
    sev_high_pri_medium = _gap(db_session, mine, priority="medium", severity="high")
    sev_crit_pri_medium = _gap(db_session, mine, priority="medium", severity="critical")
    _gap(db_session, mine, priority="low", severity="low")
    _gap(db_session, theirs, priority="high", severity="high")

    levels = ("critical", "high", "medium", "low", "unrated")
    listed = {level: {g.id for g in list_gaps(mine.id, severity=level)} for level in levels}
    assert sum(len(ids) for ids in listed.values()) == gap_register_query(mine.id).count() == 4
    assert no_severity_high.id in listed["unrated"]
    assert sev_high_pri_medium.id in listed["high"] and sev_high_pri_medium.id not in listed["medium"]
    assert sev_crit_pri_medium.id in listed["critical"]
    assert sum(count_gaps(mine.id, severity=level) for level in levels) == 4


def test_the_planning_report_counts_gaps_by_the_rating(db_session, make_org, client, login_as):
    from tests.test_work_package_writer import _user

    org = make_org("rep-a")
    _gap(db_session, org, priority="medium", severity="critical")
    _gap(db_session, org, priority="medium", severity="medium")
    login_as(client, _user(db_session, org, "repuser", role="enterprise_architect"))
    resp = client.get("/implementation/reports/generate")
    assert resp.status_code == 200
    assert resp.get_json()["report"]["summary"]["gaps_by_priority"] == {"critical": 1, "medium": 1}


def test_the_roadmap_preview_places_a_gap_by_its_rating(db_session, make_org, client, login_as):
    from tests.test_work_package_writer import _user

    org = make_org("prev-a")
    rated = _gap(db_session, org, priority="medium", severity="critical")
    plain = _gap(db_session, org, priority="medium", severity="medium")
    login_as(client, _user(db_session, org, "prevuser", role="enterprise_architect"))
    resp = client.post("/capability-map/api/roadmap/ai-generate", json={"gap_ids": [rated.id, plain.id]})
    assert resp.status_code == 200
    rows = {r["gap_id"]: r for r in resp.get_json()["preview"]}
    assert rows[rated.id]["suggested_plateau"] == "Plateau 1 — Immediate"
    assert rows[plain.id]["suggested_plateau"] == "Plateau 2 — Near Term"
    assert (rows[rated.id]["suggested_work_package"]["estimated_start_date"]
            < rows[plain.id]["suggested_work_package"]["estimated_start_date"])
