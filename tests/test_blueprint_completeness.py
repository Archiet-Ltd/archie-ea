"""One completeness computation behind every blueprint surface (S-02 / S-03).

Before this, a single load of /solutions/<id> could show four disagreeing
figures — a 25% ring, "6 of 14 sections", "13 of 14 sections need work" and
"8 not started + 1 incomplete" — because the ring, the caption, the gap
advisor and the header strip each re-derived completeness from their own
hardcoded copy of the section list, all with a literal denominator of 14. The
document renders 15 sections: "Architecture Decisions" was in none of the four
lists, so a solution could read 14-of-14 complete with no ADRs recorded.

These tests pin both halves: the denominator is derived from the canonical
section catalogue (15, including architecture_decisions), the three state
buckets are exhaustive, and the four rendered surfaces agree at 0%, partial
and 100%.

Uses the shared fixtures in tests/conftest.py.
"""

import re
import uuid

import pytest

from app.modules.solutions_strategic.v2.services.blueprint_completeness_service import (
    BLUEPRINT_SECTION_IDS,
    BLUEPRINT_SECTIONS,
    compute_blueprint_completeness,
)


def _scores(pct_by_id=None, default=0):
    """Build a section_scores dict shaped like score_all()'s output."""
    pct_by_id = pct_by_id or {}
    return {
        sid: {"overall": pct_by_id.get(sid, default)}
        for sid in BLUEPRINT_SECTION_IDS
    }


# ── The computation itself ────────────────────────────────────────────────

def test_denominator_is_the_rendered_section_count_not_fourteen():
    result = compute_blueprint_completeness(_scores())
    assert result["total_sections"] == len(BLUEPRINT_SECTIONS) == 15
    assert len(result["sections"]) == 15


def test_architecture_decisions_is_counted():
    assert "architecture_decisions" in BLUEPRINT_SECTION_IDS
    result = compute_blueprint_completeness(_scores(default=100))
    ids = [s["id"] for s in result["sections"]]
    assert "architecture_decisions" in ids
    # Every other section complete but ADRs empty must not read 100%.
    partial = compute_blueprint_completeness(
        _scores(default=100, pct_by_id={"architecture_decisions": 0})
    )
    assert partial["sections_complete"] == 14
    assert partial["percent"] < 100


@pytest.mark.parametrize(
    "scores",
    [
        _scores(),                                       # nothing started
        _scores(default=100),                            # everything done
        _scores(default=50),                             # all partial
        _scores(pct_by_id={"executive_summary": 90, "gap_analysis": 45}),
        {},                                              # no scores at all
    ],
)
def test_buckets_are_exhaustive(scores):
    r = compute_blueprint_completeness(scores)
    assert (
        r["sections_complete"] + r["sections_partial"] + r["sections_not_started"]
        == r["total_sections"]
    )
    assert r["sections_needing_work"] == r["sections_partial"] + r["sections_not_started"]
    assert r["sections_needing_work"] == r["total_sections"] - r["sections_complete"]
    assert 0 <= r["percent"] <= 100


def test_partial_sections_earn_fractional_credit():
    """The dial is the mean of section scores, not the complete/total ratio."""
    r = compute_blueprint_completeness(_scores(default=50))
    assert r["percent"] == 50
    assert r["sections_complete"] == 0
    assert r["sections_partial"] == 15


def test_zero_and_hundred_endpoints():
    empty = compute_blueprint_completeness(_scores())
    assert empty["percent"] == 0
    assert empty["sections_not_started"] == 15
    assert empty["sections_complete"] == 0

    full = compute_blueprint_completeness(_scores(default=100))
    assert full["percent"] == 100
    assert full["sections_complete"] == 15
    assert full["sections_needing_work"] == 0


def test_missing_score_counts_as_not_started_not_excluded():
    r = compute_blueprint_completeness({"executive_summary": {"overall": 100}})
    assert r["total_sections"] == 15
    assert r["sections_complete"] == 1
    assert r["sections_not_started"] == 14


# ── The four rendered surfaces ────────────────────────────────────────────

def _login(client, user_id):
    from tests._session_test_helpers import mint_test_sid
    _sid = mint_test_sid(user_id)
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True
        if _sid:
            sess["_sid"] = _sid
    from flask import g, has_app_context

    if not has_app_context():
        return
    for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
        if hasattr(g, cached):
            delattr(g, cached)


@pytest.fixture
def blueprint_client(app, db_session, make_org):
    from app.models.user import User

    org = make_org("blueprint")
    user = User(
        email=f"blueprint-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Blue",
        last_name="Print",
        organization_id=org.id,
        confirmed=True,
        enterprise_role="platform_admin",
    )
    db_session.add(user)
    db_session.flush()

    # NB: log in LAST, after every row this test needs is flushed.
    # ``client.session_transaction()`` runs a throwaway request whose teardown
    # takes db.session with it; a flush issued afterwards leaves the login
    # unable to resolve the user and the request 302s to /account/login. Seeding
    # first and calling _login() immediately before the real request is the
    # order that holds — see the per-test call below.
    client = app.test_client()
    return client, org, user, db_session


def _seed_solution(db_session, org, user, scores):
    from app.models.solution_models import Solution

    solution = Solution(
        name=f"Completeness Fixture {uuid.uuid4().hex[:6]}",
        organization_id=org.id,
        created_by_id=user.id,
        section_scores=scores,
    )
    db_session.add(solution)
    db_session.flush()
    return solution


def _surfaces(html):
    """Pull the four figures back out of the rendered page."""
    ring_pcts = re.findall(r'text-sm font-bold[^>]*>\s*(\d+)%', html)
    caption = re.search(r"(\d+) of (\d+) sections complete", html)
    need_work = re.search(r"(\d+) of (\d+) sections need work", html)
    not_started = re.search(r"(\d+) not started", html)
    incomplete = re.search(r"(\d+) incomplete", html)
    return {
        "ring": int(ring_pcts[0]) if ring_pcts else None,
        "caption": (int(caption.group(1)), int(caption.group(2))) if caption else None,
        "need_work": (int(need_work.group(1)), int(need_work.group(2))) if need_work else None,
        "not_started": int(not_started.group(1)) if not_started else 0,
        "incomplete": int(incomplete.group(1)) if incomplete else 0,
    }


@pytest.mark.parametrize("default_pct", [0, 45, 100])
def test_the_record_shows_one_design_score_and_none_of_the_section_figures(blueprint_client, default_pct):
    """The solution page is the record now: one design score, "n of 10", from the
    one service, and none of the fifteen-section percentages (the completeness
    ring, "N of M sections complete", "N of M sections need work")."""
    client, org, user, session = blueprint_client
    scores = _scores(default=default_pct)
    solution = _seed_solution(session, org, user, scores)
    _login(client, user.id)

    resp = client.get(f"/solutions/{solution.id}")
    assert resp.status_code == 200, resp.headers.get("Location")
    html = resp.get_data(as_text=True)
    assert "bp-governance-panel" not in html
    assert 'data-testid="solution-record"' in html

    from app.services.solution_design_score_service import compute
    expected = compute(solution).label
    assert expected.endswith("of 10")
    assert re.search(r'data-design-score="%s"' % re.escape(expected), html)
    got = _surfaces(html)
    assert got["caption"] is None and got["need_work"] is None
    # The section percentages stay available to the API the editor reads.
    assert compute_blueprint_completeness(scores)["total_sections"] == 15
