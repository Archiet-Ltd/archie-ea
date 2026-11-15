"""A stored money fact accepts one change only: being pointed at the fact that replaces it, once."""
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app import db
from app.models.finance_fact import FinanceFact
from app.services import finance_fact_store as store


def _fact(org, **kw):
    values = dict(
        organization_id=org.id, measure="cash_balance", amount=Decimal("100"), currency="USD",
        as_of=date(2026, 11, 14), source_ref="bank statement",
    )
    values.update(kw)
    fact = FinanceFact(**values)
    db.session.add(fact)
    db.session.flush()
    return fact


@pytest.mark.parametrize("column,value", [
    ("amount", Decimal("5")), ("currency", "GBP"), ("as_of", date(2026, 1, 1)), ("source_ref", "other"),
    ("measure", "revenue"), ("note", "edited"),
])
def test_changing_any_column_but_the_replacement_pointer_raises(db_session, make_org, column, value):
    fact = _fact(make_org())
    setattr(fact, column, value)
    with pytest.raises(ValueError, match="append-only"):
        db.session.flush()
    db.session.rollback()


def test_the_replacement_pointer_can_be_set_once_and_not_again(db_session, make_org):
    org = make_org()
    old, first, second = _fact(org), _fact(org, amount=Decimal("90")), _fact(org, amount=Decimal("80"))
    old.superseded_by_id = first.id
    db.session.flush()
    assert old.superseded_by_id == first.id
    old.superseded_by_id = second.id
    with pytest.raises(ValueError, match="replaced again"):
        db.session.flush()
    db.session.rollback()


def test_a_fact_cannot_be_deleted(db_session, make_org):
    fact = _fact(make_org())
    db.session.delete(fact)
    with pytest.raises(ValueError, match="append-only"):
        db.session.flush()
    db.session.rollback()


def test_a_row_with_neither_amount_nor_reason_is_refused_by_the_database(db_session, make_org):
    org = make_org()
    with pytest.raises(IntegrityError, match="ck_finance_fact_amount_or_reason"):
        with db.session.begin_nested():
            _fact(org, amount=None, unavailable_reason="   ")


def test_a_row_with_a_reason_and_no_amount_is_kept(db_session, make_org):
    fact = _fact(make_org(), amount=None, unavailable_reason="bank has not sent the statement")
    assert fact.id is not None and fact.amount is None


@pytest.mark.parametrize("source", ["", "   "])
def test_a_blank_source_is_refused_by_the_database(db_session, make_org, source):
    org = make_org()
    with pytest.raises(IntegrityError, match="ck_finance_fact_source"):
        with db.session.begin_nested():
            _fact(org, source_ref=source)


def test_the_store_refuses_a_blank_source_and_a_negative_amount(db_session, make_org):
    org = make_org()
    with pytest.raises(store.FinanceFactError, match="where the figure comes from"):
        store.record_fact(org.id, "cash_balance", "10", date(2026, 11, 14), "  ", currency="USD")
    with pytest.raises(store.FinanceFactError, match="not negative"):
        store.record_fact(org.id, "cash_balance", "-10", date(2026, 11, 14), "bank", currency="USD")


def test_a_second_replacement_of_the_same_figure_is_refused_even_when_the_first_is_not_yet_loaded(db_session, make_org):
    org = make_org()
    old = _fact(org)
    store.record_fact(org.id, "cash_balance", "90", date(2026, 11, 15), "bank statement", currency="USD", replaces_id=old.id)
    # a second request that read the figure before the first replaced it still holds it as current
    db.session.expire(old, ["superseded_by_id"])
    db.session.execute(db.text("SELECT 1"))
    with pytest.raises(store.FinanceFactError, match="already been replaced"):
        store.record_fact(org.id, "cash_balance", "80", date(2026, 11, 16), "bank statement", currency="USD", replaces_id=old.id)
    assert FinanceFact.query.filter_by(organization_id=org.id).count() == 2


def test_a_stale_copy_of_a_figure_cannot_be_replaced_again(db_session, make_org):
    org = make_org()
    old = _fact(org)
    newer = _fact(org, amount=Decimal("90"))
    stale_id = old.id
    db.session.execute(db.text("UPDATE finance_facts SET superseded_by_id = :n WHERE id = :o"), {"n": newer.id, "o": stale_id})
    assert old.superseded_by_id is None  # the loaded copy has not seen the update
    with pytest.raises(store.FinanceFactError, match="already been replaced"):
        store.record_fact(org.id, "cash_balance", "80", date(2026, 11, 16), "bank statement", currency="USD", replaces_id=stale_id)


def test_supersede_cannot_make_a_cycle(db_session, make_org):
    org = make_org()
    older, newer = _fact(org), _fact(org, amount=Decimal("90"))
    store.supersede_fact(org.id, older.id, newer.id)
    with pytest.raises(store.FinanceFactError):
        store.supersede_fact(org.id, newer.id, older.id)
    assert newer.superseded_by_id is None


def test_supersede_refuses_a_replacement_that_is_itself_replaced(db_session, make_org):
    org = make_org()
    a, b, c = _fact(org), _fact(org, amount=Decimal("90")), _fact(org, amount=Decimal("80"))
    store.supersede_fact(org.id, b.id, c.id)
    with pytest.raises(store.FinanceFactError):
        store.supersede_fact(org.id, a.id, b.id)
