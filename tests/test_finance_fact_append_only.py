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
