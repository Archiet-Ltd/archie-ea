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


def _ask_and_open(client, login_as, user, element_id):
    login_as(client, user)
    asked = client.post("/api/v1/intelligence/answers", json={"question": "element_profile", "element_id": element_id})
    assert asked.status_code in (200, 201), asked.get_data(as_text=True)
    login_as(client, user)
    return client.get(asked.get_json()["data"]["page_url"]).get_data(as_text=True)


def test_a_group_cut_short_is_not_shown_as_not_recorded(app, db_session, make_org, client, login_as, monkeypatch):
    from app.models import ArchiMateElement, ArchiMateRelationship
    from app.modules.intelligence.services import answer_envelope as ae

    org = make_org("prof-page-cut")
    user = _user(db_session, org.id)
    app_el = ArchiMateElement(name="Ledger", type="ApplicationComponent", layer="application", organization_id=org.id)
    db_session.add(app_el)
    db_session.flush()
    for i in range(3):
        peer = ArchiMateElement(name=f"Peer{i}", type="ApplicationComponent", layer="application", organization_id=org.id)
        db_session.add(peer)
        db_session.flush()
        db_session.add(ArchiMateRelationship(source_id=app_el.id, target_id=peer.id, type="Triggering", organization_id=org.id))
    db_session.commit()
    monkeypatch.setattr(ae, "SET_IMPACT_MAX_ROWS", 2)

    html = _ask_and_open(client, login_as, user, app_el.id)
    start = html.index('data-profile-group="processes"')
    section = html[start:html.index("</section>", start)]
    assert 'data-status="not_shown"' in section
    assert "Not shown: there were more connections than fit on one page." in section
    assert "Not recorded." not in section


def test_a_cost_reached_through_a_worked_out_link_says_so(app, db_session, make_org, client, login_as, monkeypatch):
    from app.models import ArchiMateElement, ArchiMateRelationship
    from app.models.application_portfolio import ApplicationComponent
    from app.modules.intelligence.services import answer_envelope as ae

    org = make_org("prof-page-derived")
    user = _user(db_session, org.id)
    cap = ArchiMateElement(name="Billing capability", type="Capability", layer="strategy", organization_id=org.id)
    crm = ArchiMateElement(name="CRM", type="ApplicationComponent", layer="application", organization_id=org.id)
    db_session.add_all([cap, crm])
    db_session.flush()
    db_session.add(ApplicationComponent(name="CRM", organization_id=org.id, archimate_element_id=crm.id,
                                        total_cost_of_ownership=5.0))
    db_session.add(ArchiMateRelationship(source_id=crm.id, target_id=cap.id, type="Realization", organization_id=org.id))
    db_session.commit()
    real = ae._profile_connections

    def worked_out(element_id):
        found, cut, derived_cut = real(element_id)
        for item in found:
            item["derived"], item["derived_id"] = True, 1
        return found, cut, derived_cut

    monkeypatch.setattr(ae, "_profile_connections", worked_out)
    html = _ask_and_open(client, login_as, user, cap.id)
    assert "Cost as entered; this application is linked to Billing capability only by a worked-out link." in html
