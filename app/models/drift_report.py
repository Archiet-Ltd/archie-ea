"""Stored model-health / drift report per organisation (ADR 0009).

One row per organisation — the last completed drift scan. The scheduled job
overwrites it on each run; the page reads it so the page load is a single-row
lookup rather than a live scan of the whole genome.

The report is stored as JSONB so the page can render it without re-running the
detector, and the ``computed_at`` timestamp tells the user when the scan last ran.
"""
from __future__ import annotations

from app import db
from app.models.mixins import TenantMixin


class DriftReport(TenantMixin, db.Model):
    """The last completed model-health scan for one organisation."""

    __tablename__ = "drift_reports"

    id = db.Column(db.Integer, primary_key=True)
    report_json = db.Column(db.JSON, nullable=False)
    spec_hash = db.Column(db.String(128), nullable=False)
    computed_at = db.Column(db.DateTime, nullable=False)
    finding_count = db.Column(db.Integer, nullable=False, default=0, server_default="0")

    __table_args__ = (
        db.UniqueConstraint("organization_id", name="uq_drift_reports_org"),
    )

    @classmethod
    def for_org(cls, org_id: int, session=None):
        """Return the stored report for *org_id*, or None."""
        if session is None:
            session = db.session
        return (
            session.query(cls)
            .filter(cls.organization_id == org_id)
            .first()
        )

    @classmethod
    def upsert(cls, org_id: int, report: dict, session=None):
        """Store (insert or update) the drift report for *org_id*."""
        if session is None:
            session = db.session
        existing = cls.for_org(org_id, session=session)
        import datetime as _dt

        if existing is not None:
            existing.report_json = report
            existing.spec_hash = report.get("spec_hash", "")
            existing.computed_at = _dt.datetime.now(_dt.UTC)
            existing.finding_count = report.get("summary", {}).get("total", 0)
        else:
            row = cls(
                organization_id=org_id,
                report_json=report,
                spec_hash=report.get("spec_hash", ""),
                computed_at=_dt.datetime.now(_dt.UTC),
                finding_count=report.get("summary", {}).get("total", 0),
            )
            session.add(row)


__all__ = ["DriftReport"]