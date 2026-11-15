"""One money store: dated facts about an organisation's cash, spending, revenue, funding and customers.

A ``FinanceFact`` is a figure someone stated about the organisation, true on a date, with where it came
from and who stated it. Rows are never overwritten: a correction is a new row and the old one is pointed
at it (``superseded_by_id``), which is the only change a stored row accepts. Burn and months of cash are
worked out from these rows by ``app/services/finance_fact_store.py`` and are never stored.

Running cost per application, technology or supplier is a different question and stays in ``cost_facts``.
New measures are added to ``MEASURES`` here.
"""

from sqlalchemy import event, inspect

from .. import db
from .mixins import TenantMixin

__all__ = ["FinanceFact", "MEASURES", "COUNT_MEASURES", "KINDS", "SOURCE_KINDS"]

MEASURES = ("cash_balance", "revenue", "spend", "funding_in", "paying_customers")
COUNT_MEASURES = ("paying_customers",)
KINDS = ("actual", "forecast")
SOURCE_KINDS = ("person", "import")


def _in_list(column: str, values) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class FinanceFact(TenantMixin, db.Model):
    __tablename__ = "finance_facts"

    id = db.Column(db.Integer, primary_key=True)
    measure = db.Column(db.String(32), nullable=False)
    kind = db.Column(db.String(16), nullable=False, default="actual", server_default="actual")
    # null: the whole organisation; otherwise one of its Product elements
    subject_element_id = db.Column(
        db.Integer, db.ForeignKey("archimate_elements.id", ondelete="RESTRICT"), nullable=True
    )
    amount = db.Column(db.Numeric(18, 4), nullable=True)
    currency = db.Column(db.String(3), nullable=True)
    unavailable_reason = db.Column(db.Text, nullable=True)
    as_of = db.Column(db.Date, nullable=False)
    period_start = db.Column(db.Date, nullable=True)
    period_end = db.Column(db.Date, nullable=True)
    source_kind = db.Column(db.String(16), nullable=False, default="person", server_default="person")
    source_ref = db.Column(db.String(512), nullable=False)
    stated_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    note = db.Column(db.Text, nullable=True)
    # a forecast that depends on a plateau, work package or goal
    assumes_element_id = db.Column(
        db.Integer, db.ForeignKey("archimate_elements.id", ondelete="SET NULL"), nullable=True
    )
    # active_history: the old value is loaded when this is set, so a second set is seen and refused
    superseded_by_id = db.column_property(
        db.Column(db.Integer, db.ForeignKey("finance_facts.id", ondelete="RESTRICT"), nullable=True),
        active_history=True,
    )
    recorded_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    recorded_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())

    stated_by = db.relationship("User", foreign_keys=[stated_by_user_id])
    superseded_by = db.relationship("FinanceFact", remote_side=[id], foreign_keys=[superseded_by_id])

    __table_args__ = (
        db.Index(
            "ix_finance_facts_org_measure_subject_as_of",
            "organization_id", "measure", "subject_element_id", "as_of",
        ),
        db.CheckConstraint(_in_list("measure", MEASURES), name="ck_finance_fact_measure"),
        db.CheckConstraint(_in_list("kind", KINDS), name="ck_finance_fact_kind"),
        db.CheckConstraint(_in_list("source_kind", SOURCE_KINDS), name="ck_finance_fact_source_kind"),
        db.CheckConstraint(
            "amount IS NOT NULL OR length(btrim(unavailable_reason)) > 0",
            name="ck_finance_fact_amount_or_reason",
        ),
        db.CheckConstraint("length(btrim(source_ref)) > 0", name="ck_finance_fact_source"),
    )


def _reject_finance_fact_change(_mapper, _connection, target):
    """Only ``superseded_by_id`` may change on a stored row, and only from empty to set, once."""
    state = inspect(target)
    for attr in state.mapper.column_attrs:
        history = state.attrs[attr.key].history
        if not history.has_changes():
            continue
        if attr.key != "superseded_by_id":
            raise ValueError("finance facts are append-only: %s cannot be changed" % attr.key)
        if any(old is not None for old in history.deleted):
            raise ValueError("finance facts are append-only: a replaced fact cannot be replaced again")


def _reject_finance_fact_delete(_mapper, _connection, _target):
    raise ValueError("finance facts are append-only")


event.listen(FinanceFact, "before_update", _reject_finance_fact_change)
event.listen(FinanceFact, "before_delete", _reject_finance_fact_delete)
