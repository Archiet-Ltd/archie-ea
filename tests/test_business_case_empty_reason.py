"""A business case section does not say it is empty when its own box already holds text.

The section cards of the business case printed "<section> is empty. Write it in the box below." above a box
that was filled, so a person reading the case was told it had nothing in it. The line now appears only for a
section whose box is empty.
"""
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _page(db_session, make_org, login_as, client, **fields):
    from app import db
    from app.models.business_case import BusinessCase
    from app.models.user import User

    org = make_org("bcempty")
    user = User(email="b-%s@example.com" % uuid.uuid4().hex[:8], first_name="B", last_name="C",
                organization_id=org.id, confirmed=True, enterprise_role="enterprise_architect")
    db.session.add(user)
    case = BusinessCase(title="Case", organization_id=org.id, **fields)
    db.session.add(case)
    db.session.flush()
    login_as(client, user)
    return client.get("/business-case/%d" % case.id).get_data(as_text=True)


def test_a_filled_section_does_not_claim_to_be_empty(db_session, make_org, login_as, client):
    html = _page(db_session, make_org, login_as, client, problem_statement="Queues lose messages.",
                 options_considered="Keep it or move it.", expected_benefits="No lost messages.")

    assert "Reasons (strategic context) is empty" not in html
    assert "Business options is empty" not in html
    assert "Expected benefits is empty" not in html
    # the sections no box feeds still say so
    assert "Timescale is empty" in html


def test_an_empty_section_still_says_it_is_empty(db_session, make_org, login_as, client):
    html = _page(db_session, make_org, login_as, client)

    assert "Reasons (strategic context) is empty" in html
    assert "Business options is empty" in html


def test_filled_key_risks_and_costs_do_not_claim_to_be_empty(db_session, make_org, login_as, client):
    html = _page(db_session, make_org, login_as, client, key_risks="Supplier slips the delivery date.",
                 capex=120000, opex_annual=45000, tco_3yr=255000)

    assert "Major risks is empty" not in html
    assert "Costs is empty" not in html


def test_empty_key_risks_and_costs_still_say_so(db_session, make_org, login_as, client):
    html = _page(db_session, make_org, login_as, client)

    assert "Major risks is empty" in html
    assert "Costs is empty" in html


def test_money_fields_name_their_currency(db_session, make_org, login_as, client):
    html = _page(db_session, make_org, login_as, client, capex=120000, opex_annual=45000, tco_3yr=255000)

    for label in ("Capex (", "Opex, annual (", "3-year total cost (", "Annual financial benefit (", "NPV ("):
        assert label in html, label
    assert "Capex ()" not in html
