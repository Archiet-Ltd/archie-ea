"""R1-B88: unusual export volume is judged against each person's own history,
flagged into the one approval queue, and restricting access works end to end."""

import json
from datetime import datetime, timedelta
import pytest

from tests.test_team_invite_acceptance import _make_org, _make_user


def _member(db_session, org, email=None):
    from app.models.user import Role

    user = _make_user(db_session, org, email=email)
    user.role = Role.query.filter_by(name="Architect").first()
    user.is_org_admin = False
    db_session.flush()
    return user


def _audit(org_id, user_id, created_at, *, action="export", table="export", extra=None):
    from app.models.audit_log import AuditLog

    return AuditLog(
        organization_id=org_id,
        user_id=user_id,
        action=action,
        table_name=table,
        created_at=created_at,
        extra_json=extra,
    )


def _seed_history(db_session, org_id, user_id, per_day, now, *, first_seen_days_ago=40):
    """``per_day[k]`` exports in the k-th whole day before the last 24 hours,
    plus an earlier sign-in so the member's history starts before the baseline."""
    window_start = now - timedelta(hours=24)
    rows = [_audit(org_id, user_id, now - timedelta(days=first_seen_days_ago), action="login", table="users")]
    for k, count in enumerate(per_day):
        for i in range(count):
            at = window_start - timedelta(days=k, hours=1, seconds=i)
            rows.append(
                _audit(org_id, user_id, at, extra={"endpoint": "x.export", "filename": "old-%d-%d.csv" % (k, i)})
            )
    db_session.add_all(rows)
    db_session.flush()


def _seed_recent(db_session, org_id, user_id, count, now):
    rows = [
        _audit(
            org_id,
            user_id,
            now - timedelta(hours=1, seconds=i * 30),
            extra={"endpoint": "reports.download", "filename": "recent-%d.csv" % i, "bytes": 100 + i},
        )
        for i in range(count)
    ]
    db_session.add_all(rows)
    db_session.flush()


def _flags(org_id, user_id=None):
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval

    query = AIChatCRUDApproval.query.filter(
        AIChatCRUDApproval.organization_id == org_id,
        AIChatCRUDApproval.operation_type == "restrict_access",
    )
    if user_id is not None:
        query = query.filter(AIChatCRUDApproval.entity_id == user_id)
    return query.all()


def _setup(db_session, label="A"):
    org = _make_org(db_session, label)
    member = _member(db_session, org)
    return org, member


# ---- the baseline rule ----------------------------------------------------


def test_a_heavy_exporter_is_not_flagged_for_their_normal_volume(app, db_session):
    from app.services.export_anomaly_service import assess_member, scan_organisation

    org, member = _setup(db_session)
    now = datetime.utcnow()
    _seed_history(db_session, org.id, member.id, [190 + (k % 21) for k in range(28)], now)
    _seed_recent(db_session, org.id, member.id, 230, now)

    verdict = assess_member(org.id, member.id, now=now)
    assert verdict["enough_history"] and verdict["history_days"] == 28
    assert 190 < verdict["baseline_mean"] < 215
    assert verdict["count_24h"] == 230
    assert verdict["flag"] is False
    assert scan_organisation(org.id, now=now) == []
    assert _flags(org.id) == []


def test_a_light_exporter_is_flagged_at_forty(app, db_session):
    from app.services.export_anomaly_service import scan_organisation

    org, member = _setup(db_session)
    now = datetime.utcnow()
    _seed_history(db_session, org.id, member.id, [1 + (k % 3) for k in range(28)], now)
    _seed_recent(db_session, org.id, member.id, 40, now)

    created = scan_organisation(org.id, now=now)

    assert len(created) == 1
    flag = _flags(org.id, member.id)[0]
    assert flag.id == created[0]
    assert flag.operation_type == "restrict_access" and flag.entity_type == "user"
    assert flag.source_table == "soc2_audit_log"
    assert "downloaded 40 files in the last 24 hours; usually about 2 a day." in flag.summary
    payload = json.loads(flag.operation_payload)
    assert payload["count_24h"] == 40
    assert payload["baseline_mean"] == pytest.approx(2.0, abs=0.3)
    assert payload["threshold"] < 40 and payload["baseline_std"] >= 0
    assert payload["window_start"] and payload["window_end"]
    exports = payload["exports"]
    assert len(exports) == 40
    assert {"audit_id", "at", "endpoint", "filename", "bytes"} <= set(exports[0])
    assert all(e["filename"].startswith("recent-") for e in exports)
    assert [e["at"] for e in exports] == sorted((e["at"] for e in exports), reverse=True)


def test_three_days_of_history_is_not_enough_to_flag(app, db_session):
    from app.services.export_anomaly_service import assess_member, scan_organisation

    org, member = _setup(db_session)
    now = datetime.utcnow()
    _seed_history(db_session, org.id, member.id, [2, 2, 2], now, first_seen_days_ago=4)
    _seed_recent(db_session, org.id, member.id, 40, now)

    verdict = assess_member(org.id, member.id, now=now)
    assert verdict["enough_history"] is False
    assert verdict["threshold"] is None
    assert scan_organisation(org.id, now=now) == []


def test_a_second_scan_the_same_day_raises_no_second_flag(app, db_session):
    from app.services.export_anomaly_service import scan_organisation

    org, member = _setup(db_session)
    now = datetime.utcnow()
    _seed_history(db_session, org.id, member.id, [2] * 28, now)
    _seed_recent(db_session, org.id, member.id, 40, now)

    assert len(scan_organisation(org.id, now=now)) == 1
    assert scan_organisation(org.id, now=now) == []
    assert scan_organisation(org.id, now=now + timedelta(hours=1)) == []
    assert len(_flags(org.id, member.id)) == 1


def test_a_dismissed_flag_is_not_raised_again_for_the_same_activity(app, db_session):
    from app.models.ai_chat_crud_approval import ApprovalStatus
    from app.services.export_anomaly_service import scan_organisation

    org, member = _setup(db_session)
    now = datetime.utcnow()
    _seed_history(db_session, org.id, member.id, [2] * 28, now)
    _seed_recent(db_session, org.id, member.id, 40, now)
    scan_organisation(org.id, now=now)
    flag = _flags(org.id, member.id)[0]
    flag.status = ApprovalStatus.REJECTED
    db_session.flush()

    assert scan_organisation(org.id, now=now + timedelta(minutes=30)) == []


def test_ten_is_a_floor_not_a_trigger(app, db_session):
    from app.services.export_anomaly_service import scan_organisation

    org, member = _setup(db_session)
    now = datetime.utcnow()
    _seed_history(db_session, org.id, member.id, [0] * 28, now)
    _seed_recent(db_session, org.id, member.id, 9, now)
    assert scan_organisation(org.id, now=now) == []

    org2, member2 = _setup(db_session, "B")
    _seed_history(db_session, org2.id, member2.id, [0] * 28, now)
    _seed_recent(db_session, org2.id, member2.id, 10, now)
    assert len(scan_organisation(org2.id, now=now)) == 1


def test_only_export_rows_count(app, db_session):
    from app.models.audit_log import AuditLog
    from app.services.export_anomaly_service import assess_member, is_export_row

    org, member = _setup(db_session)
    now = datetime.utcnow()
    rows = [
        _audit(org.id, member.id, now - timedelta(hours=2), action="export", table="export"),
        _audit(org.id, member.id, now - timedelta(hours=2), action="audit_export", table="audit_log"),
        _audit(org.id, member.id, now - timedelta(hours=2), action="export_spike_flag", table="ai_chat_crud_approvals"),
        _audit(org.id, member.id, now - timedelta(hours=2), action="export", table="something_else"),
    ]
    db_session.add_all(rows)
    db_session.flush()

    assert [is_export_row(r) for r in rows] == [True, False, False, False]
    assert assess_member(org.id, member.id, now=now)["count_24h"] == 1
    assert AuditLog.query.filter(AuditLog.org_predicate(org.id)).count() == 4


def test_the_hourly_scan_is_declared_as_a_tenant_job():
    from app.jobs.tenant_safe_job import PLATFORM_JOBS, TENANT_JOBS

    assert "export_anomaly_scan" in TENANT_JOBS
    assert "export_anomaly_scan" not in PLATFORM_JOBS


# ---- restricting access ---------------------------------------------------


def _flag_for(db_session, org, member):
    from app.services.export_anomaly_service import scan_organisation

    now = datetime.utcnow()
    _seed_history(db_session, org.id, member.id, [2] * 28, now)
    _seed_recent(db_session, org.id, member.id, 40, now)
    (flag_id,) = scan_organisation(org.id, now=now)
    return flag_id


def _live_session(user):
    from tests._session_test_helpers import mint_test_sid

    return mint_test_sid(user.id, organization_id=user.organization_id)


def test_approving_a_flag_reduces_the_member_to_view_only_and_signs_them_out(app, db_session):
    from app.models.audit_log import AuditLog
    from app.models.org_role import OrgRole
    from app.models.user_session import UserSession
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

    org, member = _setup(db_session)
    admin = _make_user(db_session, org, org_admin=True)
    OrgRole.set_role(org.id, member.id, "architect")
    flag_id = _flag_for(db_session, org, member)
    sid = _live_session(member)
    db_session.commit()

    result = AIChatApprovalService(user_id=admin.id).approve_and_execute(flag_id, approving_user_id=admin.id)

    assert result["success"] is True, result
    assert OrgRole.get_role(org.id, member.id) is None
    session_row = UserSession.query.filter_by(sid=sid).one()
    assert session_row.revoked_at is not None
    assert session_row.revoked_reason == "export_spike_restricted"
    restricted = AuditLog.query.filter(
        AuditLog.org_predicate(org.id), AuditLog.action == "access_restricted"
    ).all()
    assert len(restricted) == 1 and restricted[0].user_id == admin.id
    assert restricted[0].extra_json["member_id"] == member.id


def test_the_flagged_member_cannot_decide_their_own_flag(app, db_session):
    from app.models.ai_chat_crud_approval import ApprovalStatus
    from app.models.org_role import OrgRole
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

    org, member = _setup(db_session)
    OrgRole.set_role(org.id, member.id, "architect")
    flag_id = _flag_for(db_session, org, member)
    db_session.commit()

    service = AIChatApprovalService(user_id=member.id)
    approved = service.approve_and_execute(flag_id, approving_user_id=member.id)
    rejected = service.reject_approval(flag_id, reason="it was me")

    assert approved["success"] is False and approved["code"] == "FORBIDDEN"
    assert rejected["success"] is False and rejected["code"] == "FORBIDDEN"
    flag = _flags(org.id, member.id)[0]
    assert flag.status == ApprovalStatus.PENDING
    assert OrgRole.get_role(org.id, member.id) == "architect"


def test_a_non_administrator_cannot_decide_a_flag(app, db_session):
    from app.models.ai_chat_crud_approval import ApprovalStatus
    from app.models.org_role import OrgRole
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

    org, member = _setup(db_session)
    colleague = _member(db_session, org)
    OrgRole.set_role(org.id, member.id, "architect")
    OrgRole.set_role(org.id, colleague.id, "architect")
    flag_id = _flag_for(db_session, org, member)
    db_session.commit()

    service = AIChatApprovalService(user_id=colleague.id)
    approved = service.approve_and_execute(flag_id, approving_user_id=colleague.id)
    rejected = service.reject_approval(flag_id, reason="no")

    assert approved["success"] is False and approved["code"] == "FORBIDDEN"
    assert rejected["success"] is False and rejected["code"] == "FORBIDDEN"
    assert _flags(org.id, member.id)[0].status == ApprovalStatus.PENDING
    assert OrgRole.get_role(org.id, member.id) == "architect"


def test_dismissing_a_flag_leaves_access_unchanged(app, db_session):
    from app.models.ai_chat_crud_approval import ApprovalStatus
    from app.models.org_role import OrgRole
    from app.models.user_session import UserSession
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

    org, member = _setup(db_session)
    admin = _make_user(db_session, org, org_admin=True)
    OrgRole.set_role(org.id, member.id, "architect")
    flag_id = _flag_for(db_session, org, member)
    sid = _live_session(member)
    db_session.commit()

    result = AIChatApprovalService(user_id=admin.id).reject_approval(flag_id, reason="Dismissed: expected")

    assert result["success"] is True
    flag = _flags(org.id, member.id)[0]
    assert flag.status == ApprovalStatus.REJECTED and "expected" in flag.rejected_reason
    assert OrgRole.get_role(org.id, member.id) == "architect"
    assert UserSession.query.filter_by(sid=sid).one().revoked_at is None


def test_a_platform_administrator_cannot_be_restricted_by_a_flag(app, db_session):
    from app.models.ai_chat_crud_approval import ApprovalStatus
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

    org, member = _setup(db_session)
    admin = _make_user(db_session, org, org_admin=True)
    flag_id = _flag_for(db_session, org, member)
    member.is_platform_admin = True
    db_session.commit()

    result = AIChatApprovalService(user_id=admin.id).approve_and_execute(flag_id, approving_user_id=admin.id)

    assert result["success"] is False
    assert _flags(org.id, member.id)[0].status == ApprovalStatus.PENDING


def test_team_remove_member_behaves_as_before(app, db_session, login_as, client):
    from app.models.org_role import OrgRole
    from app.models.user_session import UserSession

    org, member = _setup(db_session)
    admin = _make_user(db_session, org, org_admin=True)
    OrgRole.set_role(org.id, member.id, "architect")
    sid = _live_session(member)
    db_session.commit()

    login_as(client, admin)
    resp = client.delete("/admin/team/member/%d" % member.id)

    assert resp.status_code == 200 and resp.get_json() == {"status": "removed"}
    assert OrgRole.get_role(org.id, member.id) is None
    # Unlike a restriction, the Team page's Remove does not sign the person out.
    assert UserSession.query.filter_by(sid=sid).one().revoked_at is None

    login_as(client, admin)
    assert client.delete("/admin/team/member/%d" % admin.id).status_code == 400


# ---- the investigation screen --------------------------------------------


def test_the_screen_lists_the_flag_and_restricts_through_the_approval_service(app, db_session, login_as, client):
    from app.models.org_role import OrgRole

    org, member = _setup(db_session)
    admin = _make_user(db_session, org, org_admin=True)
    OrgRole.set_role(org.id, member.id, "architect")
    flag_id = _flag_for(db_session, org, member)
    db_session.commit()

    login_as(client, admin)
    listing = client.get("/admin/access/exports").get_data(as_text=True)
    assert "downloaded 40 files in the last 24 hours" in listing

    login_as(client, admin)
    detail = client.get("/admin/access/exports/%d" % flag_id).get_data(as_text=True)
    assert "recent-0.csv" in detail and "reports.download" in detail
    assert "Restrict access" in detail and "Dismiss" in detail
    assert "/admin/audit-log?export=csv" in detail.replace("&amp;", "&")
    assert "action=export" in detail and "user_email=" in detail

    login_as(client, admin)
    resp = client.post("/admin/access/exports/%d/decision" % flag_id, data={"decision": "restrict"})
    assert resp.status_code == 302
    assert OrgRole.get_role(org.id, member.id) is None


def test_scan_now_button_raises_a_flag_for_the_callers_organisation(app, db_session, login_as, client):
    org, member = _setup(db_session)
    admin = _make_user(db_session, org, org_admin=True)
    now = datetime.utcnow()
    _seed_history(db_session, org.id, member.id, [2] * 28, now)
    _seed_recent(db_session, org.id, member.id, 40, now)
    db_session.commit()

    login_as(client, admin)
    resp = client.post("/admin/access/exports/scan")

    assert resp.status_code == 302
    assert len(_flags(org.id, member.id)) == 1


def test_a_non_administrator_cannot_scan_or_decide(app, db_session, login_as, client):
    org, member = _setup(db_session)
    other = _member(db_session, org)
    flag_id = _flag_for(db_session, org, member)
    db_session.commit()

    login_as(client, other)
    assert client.post("/admin/access/exports/scan").status_code == 403
    login_as(client, other)
    assert client.post("/admin/access/exports/%d/decision" % flag_id, data={"decision": "restrict"}).status_code == 403
