"""One cost fact store.

``CostFact`` holds every recorded cost as a typed row: organisation, element,
category, amount, currency, period, kind and source. ``ExchangeRate`` is the
dated rate table that lets a total across currencies be converted, or reported
as missing when no rate is on file. Rates are a platform reference table: an
organisation reads them and never writes them.
"""

from datetime import datetime

from flask import has_request_context

from .. import db
from .mixins import TenantMixin

__all__ = ["CostFact", "ExchangeRate"]

# What a cost fact is about. New element types are added here, not invented
# at a call site.
ELEMENT_APPLICATION = "application"
ELEMENT_CAPABILITY = "capability"
ELEMENT_TYPES = frozenset({ELEMENT_APPLICATION, ELEMENT_CAPABILITY})

KIND_ACTUAL = "actual"
KIND_BUDGET = "budget"
KIND_FORECAST = "forecast"
KINDS = frozenset({KIND_ACTUAL, KIND_BUDGET, KIND_FORECAST})

CATEGORY_TOTAL = "total"

PERIOD_ANNUAL = "annual"


class CostFact(TenantMixin, db.Model):
    """A single recorded cost, in the currency it was recorded in."""

    __tablename__ = "cost_facts"
    __table_args__ = (
        db.UniqueConstraint(
            "organization_id", "element_type", "element_id", "category", "kind",
            "source", "source_id",
            name="uq_cost_fact_identity",
        ),
        db.Index("ix_cost_fact_element", "organization_id", "element_type", "element_id"),
    )

    id = db.Column(db.Integer, primary_key=True)
    element_type = db.Column(db.String(32), nullable=False)
    element_id = db.Column(db.Integer, nullable=False)
    category = db.Column(db.String(48), nullable=False, default=CATEGORY_TOTAL)
    kind = db.Column(db.String(16), nullable=False, default=KIND_ACTUAL)
    amount = db.Column(db.Numeric(18, 4), nullable=False)
    currency = db.Column(db.String(3), nullable=False)
    period = db.Column(db.String(16), nullable=False, default=PERIOD_ANNUAL)
    period_start = db.Column(db.Date)
    period_end = db.Column(db.Date)
    # Who wrote the fact, and the record it was copied from (ADR 0008: a copy
    # declares itself). source_id is '' rather than NULL so the identity
    # constraint above holds for rows with no source record.
    source = db.Column(db.String(48), nullable=False)
    source_table = db.Column(db.String(64))
    source_id = db.Column(db.String(64), nullable=False, default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ExchangeRate(db.Model):  # platform reference table, deliberately not TenantMixin
    """Units of ``to_currency`` per one unit of ``from_currency`` from ``effective_date``."""

    __tablename__ = "exchange_rates"
    __table_args__ = (
        db.UniqueConstraint("from_currency", "to_currency", "effective_date",
                            name="uq_exchange_rate_day"),
    )

    id = db.Column(db.Integer, primary_key=True)
    from_currency = db.Column(db.String(3), nullable=False, index=True)
    to_currency = db.Column(db.String(3), nullable=False, index=True)
    rate = db.Column(db.Numeric(18, 8), nullable=False)
    effective_date = db.Column(db.Date, nullable=False)
    source = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


def _refuse_non_platform_write(mapper, connection, target):
    """An organisation user, administrator or not, never writes a rate.

    Seeders and CLI commands have no request and no signed-in user, so they
    pass; inside a request only a platform administrator does.
    """
    if not has_request_context():
        return
    from flask_login import current_user

    if not getattr(current_user, "is_authenticated", False):
        return
    from app.middleware.tenant_decorators import is_platform_admin

    if not is_platform_admin(current_user):
        raise PermissionError("exchange rates are written by the platform, not by an organisation")


db.event.listen(ExchangeRate, "before_insert", _refuse_non_platform_write)
db.event.listen(ExchangeRate, "before_update", _refuse_non_platform_write)
db.event.listen(ExchangeRate, "before_delete", _refuse_non_platform_write)
