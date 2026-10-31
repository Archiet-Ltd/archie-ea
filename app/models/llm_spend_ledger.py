"""What the platform has spent on answers, one row per provider per calendar month.

The row is written by the LLM service after every call it pays for itself (a key from the
environment, never an organisation's own key) and read by the admin API settings page. The
service refuses a provider whose month-to-date spend has reached its ceiling, so this table
is the hard stop, not a report.
"""

from datetime import datetime

from app import db


class LlmSpendLedger(db.Model):
    __tablename__ = "llm_spend_ledger"
    __table_args__ = (
        db.UniqueConstraint("provider", "month", name="uq_llm_spend_ledger_provider_month"),
    )

    id = db.Column(db.Integer, primary_key=True)
    provider = db.Column(db.String(32), nullable=False)
    month = db.Column(db.String(7), nullable=False)  # "2026-10", UTC
    usd_spent = db.Column(db.Numeric(14, 6), nullable=False, default=0)
    requests = db.Column(db.Integer, nullable=False, default=0)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def __repr__(self):
        return "<LlmSpendLedger %s %s usd=%s requests=%s>" % (
            self.provider, self.month, self.usd_spent, self.requests,
        )
