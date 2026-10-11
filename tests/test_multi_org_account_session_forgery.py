"""Forged-session regression coverage for multi-organisation account access."""

from __future__ import annotations

from tests.test_multi_org_account_switching import _make_application, _make_user


def test_forged_session_org_id_falls_back_to_home_org_for_reads_and_exports(
    app, db_session, make_org, client, login_as
):
    from app.models.org_role import OrgRole
    from app.utils.validators import sanitize_filename

    org_a = make_org("forged-home")
    org_b = make_org("forged-accessible")
    org_c = make_org("forged-foreign")
    user = _make_user(db_session, org_a, email="forged-session@example.test")
    OrgRole.set_role(org_b.id, user.id, "architect", granted_by_id=user.id)
    _make_application(db_session, org_a, "Home Organisation App")
    _make_application(db_session, org_b, "Accessible Secondary App")
    _make_application(db_session, org_c, "Forged Foreign App")
    db_session.commit()

    login_as(client, user)
    with client.session_transaction() as sess:
        sess["current_org_id"] = org_c.id

    manage = client.get("/account/manage")
    assert manage.status_code == 200
    manage_html = manage.get_data(as_text=True)
    assert "Active: %s" % org_a.name in manage_html
    assert org_b.name in manage_html
    assert org_c.name not in manage_html

    export = client.get("/applications/export/csv")
    assert export.status_code == 200
    assert sanitize_filename(org_a.name) in export.headers["Content-Disposition"]
    csv_text = export.get_data(as_text=True)
    assert "Home Organisation App" in csv_text
    assert "Accessible Secondary App" not in csv_text
    assert "Forged Foreign App" not in csv_text

    with client.session_transaction() as sess:
        assert sess.get("current_org_id") != org_c.id
