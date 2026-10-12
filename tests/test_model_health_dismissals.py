"""Dismissing a model-health finding as a false positive, with a reason."""
from __future__ import annotations

import uuid

from app.datetime_helpers import utcnow
from app.modules.genome.services import finding_dismissals as fd


def _finding(ftype="orphaned_element", ids=(1,), severity="low"):
    return {
        "type": ftype,
        "severity": severity,
        "why": f"{ftype} {ids}",
        "elements": [
            {"archimate_element_id": i, "name": f"E{i}", "archimate_type": "Node", "role": "subject"}
            for i in ids
        ],
    }


def _report(findings):
    return {
        "findings": findings,
        "signals_scanned": ["orphaned_element", "near_duplicate_elements"],
        "summary": {"total": len(findings), "by_type": {}, "by_severity": {}},
        "spec_hash": "sha256:x",
    }


def test_finding_key_ignores_element_order():
    a = _finding("near_duplicate_elements", (7, 3))
    b = _finding("near_duplicate_elements", (3, 7))
    assert fd.finding_key(a) == fd.finding_key(b)
    assert fd.finding_key(a) != fd.finding_key(_finding("orphaned_element", (3, 7)))


def test_reason_must_say_something():
    assert fd.clean_reason("") is None
    assert fd.clean_reason("  ab ") is None
    assert fd.clean_reason("  Kept for audit  ") == "Kept for audit"
    assert len(fd.clean_reason("x" * 900)) == fd.MAX_REASON_LENGTH


def test_split_report_recounts_what_remains():
    one, two = _finding(ids=(1,), severity="high"), _finding(ids=(2,), severity="low")
    report = _report([one, two])
    open_report, dismissed = fd.split_report(
        report, {fd.finding_key(one): {"reason": "intended", "by": "A", "at": "2026-10-12T00:00:00"}}
    )
    assert open_report["summary"]["total"] == 1
    assert open_report["summary"]["by_severity"] == {"low": 1}
    assert [d["reason"] for d in dismissed] == ["intended"]
    # the stored report itself is never edited
    assert len(report["findings"]) == 2


def _user_and_report(db_session, make_org, label):
    from app.models.drift_report import DriftReport
    from app.models.user import User

    org = make_org(f"health-{label}")
    user = User(
        email=f"health-{label}-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=org.id,
        confirmed=True,
    )
    user.password = "not-used-in-tests-123"
    db_session.add(user)
    finding = _finding(ids=(101 if label == "a" else 202,))
    db_session.add(
        DriftReport(
            organization_id=org.id,
            report_json=_report([finding]),
            spec_hash="sha256:x",
            computed_at=utcnow(),
            finding_count=1,
        )
    )
    db_session.commit()
    return org, user, finding


def test_dismiss_requires_reason_and_is_restorable(app, db_session, make_org, client, login_as):
    from app.models.drift_report import DriftReport

    org, user, finding = _user_and_report(db_session, make_org, "a")
    key = fd.finding_key(finding)
    login_as(client, user)

    # no reason: nothing recorded
    client.post("/genome/model-health/dismiss", data={"finding_key": key, "reason": " "})
    assert DriftReport.for_org(org.id).dismissals in (None, {})

    resp = client.post(
        "/genome/model-health/dismiss",
        data={"finding_key": key, "reason": "Archive copy, kept on purpose"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Dismissed as false positives" in body
    assert "Archive copy, kept on purpose" in body
    assert 'name="reason"' not in body  # no open finding left to dismiss
    db_session.expire_all()
    assert DriftReport.for_org(org.id).dismissals[key]["reason"] == "Archive copy, kept on purpose"

    resp = client.post(
        "/genome/model-health/restore", data={"finding_key": key}, follow_redirects=True
    )
    assert "Dismissed as false positives" not in resp.get_data(as_text=True)
    db_session.expire_all()
    assert DriftReport.for_org(org.id).dismissals == {}


def test_dismissal_survives_a_rescan(app, db_session, make_org):
    from app.models.drift_report import DriftReport

    org, _user, finding = _user_and_report(db_session, make_org, "a")
    stored = DriftReport.for_org(org.id)
    assert fd.dismiss(stored, fd.finding_key(finding), "intended", "A")
    db_session.commit()
    DriftReport.upsert(org.id, _report([finding]))
    db_session.commit()
    db_session.expire_all()
    assert fd.finding_key(finding) in DriftReport.for_org(org.id).dismissals


def test_unknown_finding_is_refused(app, db_session, make_org, client, login_as):
    from app.models.drift_report import DriftReport

    org, user, _finding_a = _user_and_report(db_session, make_org, "a")
    login_as(client, user)
    client.post(
        "/genome/model-health/dismiss",
        data={"finding_key": "orphaned_element:999999", "reason": "not in the report"},
    )
    assert DriftReport.for_org(org.id).dismissals in (None, {})


def test_other_organisation_cannot_dismiss_or_see(app, db_session, make_org, client, login_as):
    from app.extensions import db

    org_a, _user_a, finding_a = _user_and_report(db_session, make_org, "a")
    org_b, user_b, _finding_b = _user_and_report(db_session, make_org, "b")
    key_a = fd.finding_key(finding_a)
    from app.models.drift_report import DriftReport

    stored_a = DriftReport.for_org(org_a.id)
    assert fd.dismiss(stored_a, key_a, "a-only reason", "A")
    db_session.commit()

    login_as(client, user_b)
    page = client.get("/genome/model-health/").get_data(as_text=True)
    assert "a-only reason" not in page

    client.post("/genome/model-health/dismiss", data={"finding_key": key_a, "reason": "from b"})
    client.post("/genome/model-health/restore", data={"finding_key": key_a})
    def stored(org_id):
        # read around the request-scoped tenant filter: both rows are checked
        return db_session.execute(
            db.text("SELECT dismissals FROM drift_reports WHERE organization_id = :o"),
            {"o": org_id},
        ).scalar()

    assert stored(org_a.id)[key_a]["reason"] == "a-only reason"
    assert stored(org_b.id) in (None, {})
