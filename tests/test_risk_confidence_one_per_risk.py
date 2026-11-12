"""A risk's confidence is the same on the simulations page and on the risk register.

The register works out a ranked risk's confidence from its score and the elements it reaches; the
prioritised action list beside the simulation screen worked out its own from the score and the level
alone, so a risk with nothing linked read Medium there and Low on the register at the same time. Every
screen that lists the actions now takes each risk's footer from the register's ranking.
"""
import re
import uuid

import pytest


@pytest.fixture
def seeded(db_session, make_org):
    from app.models.risk import Risk
    from app.models.user import Role, User

    def _org(label, title):
        org = make_org(label)
        user = User(email="conf-%s@example.com" % uuid.uuid4().hex[:10], first_name="Risk", last_name="Reader",
                    organization_id=org.id, confirmed=True, enterprise_role="enterprise_architect")
        user.role = Role.query.filter_by(name="Architect").first()
        db_session.add(user)
        risk = Risk(title=title, likelihood=4, impact=4, organization_id=org.id, owner="Ops lead")
        db_session.add(risk)
        db_session.flush()
        return org, user, risk

    suffix = uuid.uuid4().hex[:6]
    return (_org("confmine", "Generated code is unverified %s" % suffix),
            _org("conftheirs", "Their only risk %s" % suffix))


def _confidence_after(html, marker, title):
    """The confidence of the first result footer after ``title`` inside the section opened by ``marker``."""
    section = html.split(marker, 1)[1]
    after = section.split(title, 1)[1]
    found = re.search(r'data-testid="result-footer" data-confidence="(\w+)"', after)
    shown = re.search(r'data-testid="result-footer-confidence".*?>(low|medium|high)</span>\s*([^<]+)</p>', after, re.S)
    assert shown.group(1) == found.group(1)
    return found.group(1), " ".join(shown.group(2).split())


def test_the_register_and_the_simulations_page_show_one_confidence(app, client, login_as, seeded, monkeypatch):
    (org, user, risk), (_, _, their_risk) = seeded

    login_as(client, user)
    register = client.get("/risks/").get_data(as_text=True)
    # The simulation screen opens in the third release.
    monkeypatch.setenv("RELEASE_WAVE", "3")
    app.extensions.pop("release_waves_endpoints", None)
    login_as(client, user)
    simulations = client.get("/simulations")
    assert simulations.status_code == 200
    page = simulations.get_data(as_text=True)

    on_register = _confidence_after(register, 'id="ranked-risks"', risk.title)
    on_simulations = _confidence_after(page, 'data-testid="priority-actions"', risk.title)
    assert on_register == on_simulations
    assert on_register[0] == "low"
    assert "Linked elements: none recorded" in on_register[1]
    assert their_risk.title not in page and their_risk.title not in register


def test_every_screen_listing_actions_takes_the_registers_footer(app, db_session, tenant_ctx, seeded):
    from app.services import priority_action_service, recommendation_service, risk_service

    (org, _, risk), _ = seeded
    with tenant_ctx(org.id):
        ranked = {row["risk_id"]: row["footer"] for row in risk_service.ranked_risks()["rows"]}
        footers = risk_service.footers_by_risk(org.id)
        actions = priority_action_service.prioritised_actions(org.id, risk_footers=footers)["rows"]
        advice = recommendation_service.recommendations(org.id)["rows"]
    (action,) = [a for a in actions if a["kind"] == "risk" and a["record_id"] == risk.id]
    (advised,) = [a for a in advice if a["record_name"] == risk.title]
    for footer in (action["footer"], advised["footer"]):
        assert footer["confidence"] == ranked[risk.id]["confidence"]
        assert footer["confidence_reason"] == ranked[risk.id]["confidence_reason"]
