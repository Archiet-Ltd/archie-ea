"""One organisation's money facts are never seen, replaced or referred to by another."""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from app import db
from app.models.archimate_core import ArchiMateElement
from app.services import finance_fact_store as store
from tests.test_ba_tenant_and_authz import _login
from tests.test_change_review import _user


def _product(org, name="Plan"):
    element = ArchiMateElement(organization_id=org.id, name="%s %s" % (name, uuid.uuid4().hex[:6]), type="Product", layer="business")
    db.session.add(element)
    db.session.flush()
    return element


@pytest.fixture
def two_orgs(db_session, make_org):
    org_a, org_b = make_org("money-a"), make_org("money-b")
    ada = _user(db_session, org_a.id, "Administrator")
    ben = _user(db_session, org_b.id, "Administrator")
    ada.confirmed = ben.confirmed = True
    fact_b = store.record_fact(org_b.id, "cash_balance", "50000", date(2026, 11, 14), "bank statement B",
                               currency="USD", stated_by_user_id=ben.id, recorded_by_id=ben.id)
    return org_a, org_b, ada, ben, fact_b


def test_another_organisations_cash_is_never_on_this_page_or_in_its_history(client, two_orgs):
    org_a, _org_b, ada, _ben, fact_b = two_orgs
    store.record_fact(org_a.id, "cash_balance", "1200", date(2026, 11, 13), "bank statement A", currency="USD",
                      stated_by_user_id=ada.id)
    _login(client, ada.id)
    html = client.get("/procurement/spend/money").get_data(as_text=True)
    assert "1,200 USD" in html and "bank statement A" in html
    assert "50,000" not in html and "bank statement B" not in html
    figures = store.derived_figures(org_a.id)
    assert figures["cash"]["amount"] == Decimal("1200")
    rows, total = store.history(org_a.id)
    assert fact_b.id not in [r.id for r in rows] and total == 1


def test_a_post_that_replaces_another_organisations_fact_is_refused_and_changes_nothing(client, two_orgs):
    _org_a, _org_b, ada, _ben, fact_b = two_orgs
    _login(client, ada.id)
    response = client.post("/procurement/spend/money", data={
        "measure": "cash_balance", "amount": "1", "currency": "USD", "as_of": "2026-11-14",
        "source_ref": "attack", "replaces_id": str(fact_b.id)})
    assert response.status_code == 422
    assert "not one of your organisation" in response.get_data(as_text=True)
    pointer = db.session.execute(text("SELECT superseded_by_id FROM finance_facts WHERE id = :i"), {"i": fact_b.id}).scalar_one()
    assert pointer is None
    assert db.session.execute(text("SELECT count(*) FROM finance_facts WHERE source_ref = 'attack'")).scalar() == 0


def test_the_store_refuses_to_replace_another_organisations_fact(two_orgs):
    org_a, _org_b, _ada, _ben, fact_b = two_orgs
    mine = store.record_fact(org_a.id, "cash_balance", "5", date(2026, 11, 14), "mine", currency="USD")
    with pytest.raises(store.FinanceFactError):
        store.supersede_fact(org_a.id, fact_b.id, mine.id)
    with pytest.raises(store.FinanceFactError):
        store.record_fact(org_a.id, "cash_balance", "6", date(2026, 11, 15), "mine", currency="USD", replaces_id=fact_b.id)


def test_a_product_from_another_organisation_is_refused_as_the_subject(db_session, two_orgs):
    org_a, org_b, _ada, _ben, _fact_b = two_orgs
    theirs, mine = _product(org_b), _product(org_a)
    with pytest.raises(store.FinanceFactError, match="your own products"):
        store.record_fact(org_a.id, "revenue", "10", date(2026, 11, 14), "x", currency="USD", subject_element_id=theirs.id)
    ok = store.record_fact(org_a.id, "revenue", "10", date(2026, 11, 14), "x", currency="USD", subject_element_id=mine.id)
    assert ok.subject_element_id == mine.id


def test_a_raw_select_with_the_session_set_to_one_organisation_returns_none_of_the_others(db_session, two_orgs):
    """The row-level security policy, read as a role the policy binds."""
    org_a, org_b, ada, _ben, fact_b = two_orgs
    id_a, id_b = org_a.id, org_b.id
    store.record_fact(id_a, "cash_balance", "7", date(2026, 11, 14), "a", currency="USD", stated_by_user_id=ada.id)
    role = "money_rls_%s" % uuid.uuid4().hex[:8]
    db_session.execute(text("CREATE ROLE %s NOSUPERUSER NOBYPASSRLS" % role))
    db_session.execute(text("GRANT SELECT ON finance_facts TO %s" % role))
    db_session.execute(text("SET LOCAL ROLE %s" % role))
    try:
        db_session.execute(text("SELECT set_config('archie.organization_id', :o, true)"), {"o": str(id_a)})
        seen = {r[0] for r in db_session.execute(text("SELECT organization_id FROM finance_facts"))}
        assert seen == {id_a}
        db_session.execute(text("SELECT set_config('archie.organization_id', :o, true)"), {"o": str(id_b)})
        seen_b = {r[0] for r in db_session.execute(text("SELECT organization_id FROM finance_facts"))}
        assert seen_b == {id_b}
        db_session.execute(text("SELECT set_config('archie.organization_id', '', true)"))
        assert db_session.execute(text("SELECT count(*) FROM finance_facts")).scalar() == 0
    finally:
        db_session.execute(text("RESET ROLE"))
