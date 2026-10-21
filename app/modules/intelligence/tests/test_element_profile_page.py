"""The stored page of an element profile shows every group, the recorded facts with
their basis and date, and a plain statement for each group with nothing recorded."""

from __future__ import annotations

import uuid


def _user(db_session, org_id):
    from app.models.user import User

    user = User(
        email=f"prof-{uuid.uuid4().hex[:10]}@example.com", first_name="Pro", last_name="File",
        organization_id=org_id, confirmed=True, enterprise_role="enterprise_architect",
    )
    db_session.add(user)
    db_session.flush()
    return user


def test_the_stored_profile_page_lists_each_group(app, db_session, make_org, client, login_as):
    from app.models import ArchiMateElement, ArchiMateRelationship

    org = make_org("prof-page")
    user = _user(db_session, org.id)
    app_el = ArchiMateElement(name="Ledger", type="ApplicationComponent", layer="application", organization_id=org.id)
    proc = ArchiMateElement(name="Month end close", type="BusinessProcess", layer="business", organization_id=org.id)
    db_session.add_all([app_el, proc])
    db_session.flush()
    db_session.add(ArchiMateRelationship(source_id=app_el.id, target_id=proc.id, type="Serving", organization_id=org.id))
    db_session.commit()

    login_as(client, user)
    asked = client.post("/api/v1/intelligence/answers", json={"question": "element_profile", "element_id": app_el.id})
    assert asked.status_code in (200, 201), asked.get_data(as_text=True)
    data = asked.get_json()["data"]

    login_as(client, user)
    page = client.get(data["page_url"])
    html = page.get_data(as_text=True)
    assert page.status_code == 200
    assert "Everything about Ledger" in html
    assert "What breaks if" not in html
    for label in ("Owner", "Cost", "Hosting node", "End of support", "Interfaces", "Risks", "Open gaps"):
        assert label in html, label
    assert html.count("Not recorded") >= 6
    assert "Month end close" in html and "serves" in html
    assert 'data-profile-group="processes"' in html
