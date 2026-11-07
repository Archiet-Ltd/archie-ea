"""A backlog score is worked out from recorded inputs; a requirement without them has no score, not a made-up one."""
from app.models.models import Requirement
from app.services import prioritisation_service


def _requirement(db_session, org, title, **fields):
    row = Requirement(title=title, type="functional", organization_id=org.id, **fields)
    db_session.add(row)
    db_session.flush()
    return row


def test_a_requirement_with_no_inputs_has_no_score_and_ranks_after_the_scored(db_session, make_org, tenant_ctx):
    ours, theirs = make_org("scores-ours"), make_org("scores-theirs")
    bare = _requirement(db_session, ours, "Bare")
    scored = _requirement(
        db_session, ours, "Scored", business_value=8, time_criticality=5, risk_reduction=3, job_size=4,
        reach=200, impact=3, confidence=50, story_points=5)
    partial = _requirement(db_session, ours, "Partial", business_value=8, time_criticality=5)
    _requirement(db_session, theirs, "Theirs", business_value=13, time_criticality=13, risk_reduction=13, job_size=1)

    assert bare.wsjf_score is None and bare.rice_score is None
    assert partial.wsjf_score is None
    assert scored.wsjf_score == 4.0
    assert scored.rice_score == 60.0

    with tenant_ctx(ours.id):
        for method, key in (("wsjf", "wsjf_score"), ("rice", "rice_score")):
            ranked = prioritisation_service.get_backlog_prioritised(method=method)
            assert [item["title"] for item in ranked][0] == "Scored"
            assert {item["title"] for item in ranked} == {"Bare", "Scored", "Partial"}
            assert [item[key] for item in ranked][1:] == [None, None]


def test_rice_needs_story_points(db_session, make_org):
    row = _requirement(db_session, make_org("scores-points"), "No points", reach=100, impact=2, confidence=80)
    assert row.rice_score is None
    row.story_points = 4
    assert row.rice_score == 40.0
