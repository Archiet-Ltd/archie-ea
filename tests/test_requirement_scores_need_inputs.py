"""A backlog score is worked out from recorded inputs; a requirement without them has no score, not a made-up one."""
from app import db
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


def _architect(db_session, org, label):
    import uuid

    from app.models.user import Role, User

    Role.insert_roles()
    user = User(
        email="%s.%s@example.com" % (label, uuid.uuid4().hex[:8]), first_name="A", last_name="B",
        organization_id=org.id, enterprise_role="enterprise_architect", confirmed=True,
        role=Role.query.filter_by(name="Viewer").first())
    user.password = "Passw0rd!x"
    db_session.add(user)
    db_session.flush()
    return user


def test_a_recorded_one_or_a_reach_of_zero_is_a_score_not_a_missing_input(db_session, make_org):
    org = make_org("scores-real")
    ones = _requirement(db_session, org, "All ones", business_value=1, time_criticality=1, risk_reduction=1, job_size=1)
    no_reach = _requirement(db_session, org, "No reach", reach=0, impact=3, confidence=80, story_points=2)
    assert ones.wsjf_score == 3.0
    assert no_reach.rice_score == 0.0


def test_a_save_that_leaves_an_input_out_is_refused_and_stores_no_stand_in(db_session, make_org, client, login_as):
    ours, theirs = make_org("save-ours"), make_org("save-theirs")
    user = _architect(db_session, ours, "save")
    mine = _requirement(db_session, ours, "Mine")
    other = _requirement(db_session, theirs, "Theirs")

    login_as(client, user)
    refused = client.patch("/api/backlog/%d/wsjf" % mine.id, json={"business_value": 5, "time_criticality": 3})
    assert refused.status_code == 400
    assert "risk reduction" in refused.get_json()["error"] and "job size" in refused.get_json()["error"]
    login_as(client, user)
    refused_rice = client.patch("/api/backlog/%d/rice" % mine.id, json={"reach": 10})
    assert refused_rice.status_code == 400
    assert "impact" in refused_rice.get_json()["error"] and "confidence" in refused_rice.get_json()["error"]
    login_as(client, user)
    no_points = client.patch("/api/backlog/%d/rice" % mine.id, json={"reach": 10, "impact": 3, "confidence": 80})
    assert no_points.status_code == 400 and "story points" in no_points.get_json()["error"]
    db_session.expire_all()
    assert mine.reach is None and mine.story_points is None
    db_session.expire_all()
    assert (mine.business_value, mine.time_criticality, mine.risk_reduction, mine.job_size) == (None,) * 4
    assert (mine.reach, mine.impact, mine.confidence) == (None,) * 3

    # a full save keeps a recorded 1 and a recorded reach of 0, and reads back as a score
    login_as(client, user)
    saved = client.patch("/api/backlog/%d/wsjf" % mine.id, json={
        "business_value": 1, "time_criticality": 1, "risk_reduction": 1, "job_size": 1})
    assert saved.status_code == 200 and saved.get_json()["wsjf_score"] == 3.0
    login_as(client, user)
    saved = client.patch("/api/backlog/%d/rice" % mine.id, json={
        "reach": 0, "impact": 3, "confidence": 80, "story_points": 2})
    assert saved.status_code == 200 and saved.get_json()["rice_score"] == 0.0

    # another organisation's requirement is not ours to change
    login_as(client, user)
    assert client.patch("/api/backlog/%d/wsjf" % other.id, json={
        "business_value": 1, "time_criticality": 1, "risk_reduction": 1, "job_size": 1}).status_code == 404
    db_session.expire_all()
    assert other.business_value is None


def test_the_revision_clears_only_the_stand_in_values_in_every_organisation(db_session, make_org):
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "migrations" / "versions" / "20261107_clear_standin_scores.py"
    spec = importlib.util.spec_from_file_location("clear_standin_scores", path)
    revision = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(revision)

    rows = {}
    for label in ("one", "two"):
        org = make_org("clear-" + label)
        rows[label] = (
            _requirement(db_session, org, "Old defaults", business_value=1, time_criticality=1, risk_reduction=1,
                         job_size=1, reach=0, impact=1, confidence=100),
            _requirement(db_session, org, "Recorded", business_value=1, time_criticality=1, risk_reduction=1,
                         job_size=2, reach=0, impact=3, confidence=100, story_points=2),
        )
    # a score a person saved afterwards (updated_at moves past created_at) is theirs, even when it is all ones
    saved_ones = _requirement(db_session, make_org("clear-saved"), "Saved ones", business_value=1,
                              time_criticality=1, risk_reduction=1, job_size=1)
    db_session.execute(db.text(
        "UPDATE requirements SET updated_at = created_at + interval '1 hour' WHERE id = :id"), {"id": saved_ones.id})
    for statement in (revision._WSJF, revision._RICE):
        db_session.execute(db.text(statement))
    db_session.expire_all()
    assert saved_ones.wsjf_score == 3.0

    for stale, recorded in rows.values():
        assert (stale.business_value, stale.time_criticality, stale.risk_reduction, stale.job_size) == (None,) * 4
        assert (stale.reach, stale.impact, stale.confidence) == (None,) * 3
        assert recorded.wsjf_score == 1.5 and recorded.rice_score == 0.0


def test_the_revision_follows_the_head_of_the_revision_line():
    from pathlib import Path

    versions = Path(__file__).resolve().parent.parent / "migrations" / "versions"
    text = (versions / "20261107_clear_standin_scores.py").read_text()
    assert 'down_revision = "20261108_impact_capability"' in text
