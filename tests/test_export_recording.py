"""R1-B88: every file download is one row in the audit trail."""

from unittest import mock

from tests.test_team_invite_acceptance import _make_org, _make_user


def _export_rows(org_id, user_id=None):
    from app.models.audit_log import AuditLog

    query = AuditLog.query.filter(
        AuditLog.org_predicate(org_id), AuditLog.action == "export", AuditLog.table_name == "export"
    )
    if user_id is not None:
        query = query.filter(AuditLog.user_id == user_id)
    return query.order_by(AuditLog.id).all()


def test_a_signed_in_csv_download_writes_exactly_one_export_row(app, db_session, login_as, client):
    org = _make_org(db_session, "A")
    admin = _make_user(db_session, org, org_admin=True)
    db_session.commit()
    org_id, admin_id = org.id, admin.id

    login_as(client, admin)
    resp = client.get("/admin/audit-log?export=csv")

    assert resp.status_code == 200
    assert resp.headers["Content-Disposition"].startswith("attachment")
    rows = _export_rows(org_id)
    assert len(rows) == 1
    row = rows[0]
    assert row.organization_id == org_id and row.user_id == admin_id
    assert row.extra_json["filename"] and row.extra_json["filename"].endswith(".csv")
    assert row.extra_json["path"] == "/admin/audit-log"
    assert row.extra_json["endpoint"].endswith("audit_log_viewer")
    assert row.extra_json["content_type"] == "text/csv"
    assert "bytes" in row.extra_json


def test_a_json_response_and_a_404_write_no_export_row(app, db_session, login_as, client):
    org = _make_org(db_session, "A")
    admin = _make_user(db_session, org, org_admin=True)
    db_session.commit()
    org_id = org.id

    login_as(client, admin)
    assert client.get("/admin/team/persona-preview?persona=finance&role=viewer").status_code == 200
    assert client.get("/admin/this-does-not-exist-b88").status_code == 404

    assert _export_rows(org_id) == []


def test_an_anonymous_download_writes_no_export_row(app, db_session, client):
    org = _make_org(db_session, "A")
    db_session.commit()
    before = len(_export_rows(org.id))

    resp = client.get("/admin/audit-log?export=csv")

    assert resp.status_code in (302, 401, 403)
    assert len(_export_rows(org.id)) == before


def test_a_refused_download_writes_no_export_row(app, db_session, login_as, client):
    from app.models.user import Role

    org = _make_org(db_session, "A")
    member = _make_user(db_session, org)
    member.role = Role.query.filter_by(name="Architect").first()
    member.is_org_admin = False
    db_session.commit()
    org_id = org.id

    login_as(client, member)
    resp = client.get("/admin/audit-log?export=csv")

    assert resp.status_code == 403
    assert _export_rows(org_id) == []


def test_a_failure_in_the_hook_never_changes_the_response(app, db_session, login_as, client):
    org = _make_org(db_session, "A")
    admin = _make_user(db_session, org, org_admin=True)
    db_session.commit()

    login_as(client, admin)
    with mock.patch(
        "app.models.audit_log.AuditLog.log", side_effect=RuntimeError("audit store down")
    ):
        resp = client.get("/admin/audit-log?export=csv")

    assert resp.status_code == 200
    assert resp.headers["Content-Disposition"].startswith("attachment")
    assert resp.get_data(as_text=True)


def test_one_row_per_download_and_scoped_to_the_downloading_organisation(app, db_session, login_as, client):
    org_a = _make_org(db_session, "A")
    org_b = _make_org(db_session, "B")
    admin_a = _make_user(db_session, org_a, org_admin=True)
    admin_b = _make_user(db_session, org_b, org_admin=True)
    db_session.commit()
    a_id, b_id = org_a.id, org_b.id

    login_as(client, admin_a)
    client.get("/admin/audit-log?export=csv")
    client.get("/admin/audit-log?export=csv")
    login_as(client, admin_b)
    client.get("/admin/audit-log?export=csv")

    assert len(_export_rows(a_id)) == 2
    assert len(_export_rows(b_id)) == 1
