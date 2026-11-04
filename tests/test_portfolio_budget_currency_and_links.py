"""The Portfolio states budgets in the organisation's currency, says why a benefit is empty, and
the demonstration organisation's initiatives each serve a goal and claim a benefit and its
contracts state an annual cost. Another organisation gains none of it."""
from __future__ import annotations

import os
import re
import uuid

import pytest

from app.commands.seed_demo_company import seed_demo_company
from tests.test_vendor_pages_agree import _admin_client, _forget_cached_user

pytestmark = pytest.mark.usefixtures("db_session")


def _initiative(db_session, org, name, budget=None):
    from app.models.vendor.vendor_organization import EnterpriseInitiative

    i = EnterpriseInitiative(name=name, code="T-" + uuid.uuid4().hex[:8], organization_id=org.id,
                             status="active", approved_budget=budget, spent_to_date=budget and budget / 2)
    db_session.add(i)
    db_session.flush()
    return i


def test_budgets_carry_the_currency_and_an_empty_benefit_says_what_to_do(app, db_session, make_org):
    mine = make_org("pf-a")
    mine.reporting_currency = "EUR"
    db_session.flush()
    first = _initiative(db_session, mine, "Alpha " + uuid.uuid4().hex[:4], 2595000)
    client = _admin_client(app, db_session, mine)
    _forget_cached_user()
    resp = client.get("/portfolio/")
    assert resp.status_code == 200
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", resp.get_data(as_text=True)))
    assert re.search(r"€\s?2[.,]595[.,]000", text), text[:1500]
    assert "None recorded: add one" in text
    html = resp.get_data(as_text=True)
    assert f"/portfolio/{first.id}" in html


@pytest.fixture
def seeded(app, db_session, make_org):
    os.environ["DEMO_USER_PASSWORD"] = "test-password"
    try:
        seed_demo_company()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]
    from app.models.organization import Organization

    demo_id = db_session.query(Organization.id).filter_by(slug="lantern-quay").scalar()
    other = make_org("pf-other")
    db_session.commit()
    return {"demo": demo_id, "other": other.id}


def test_demo_initiatives_serve_goals_claim_benefits_and_contracts_state_cost(seeded, db_session):
    from sqlalchemy import text

    demo, other = seeded["demo"], seeded["other"]
    rows = db_session.execute(text("""
        SELECT i.name,
               (SELECT count(*) FROM initiative_goals g WHERE g.initiative_id = i.id) AS goals,
               (SELECT count(*) FROM benefits b WHERE b.initiative_id = i.id) AS benefits
        FROM enterprise_initiatives i WHERE i.organization_id = :o"""), {"o": demo}).all()
    assert len(rows) == 7
    assert all(r.goals >= 1 and r.benefits >= 1 for r in rows), rows
    contracts = db_session.execute(text(
        "SELECT count(*) FILTER (WHERE annual_cost IS NULL), count(*) FROM vendor_contracts "
        "WHERE organization_id = :o AND status = 'active'"), {"o": demo}).one()
    assert contracts[1] > 0 and contracts[0] == 0
    assert db_session.execute(text("SELECT count(*) FROM benefits WHERE organization_id = :o"),
                              {"o": other}).scalar() == 0
