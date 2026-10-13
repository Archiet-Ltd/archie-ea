"""Security finding register (platform level).

One row per weakness found by testing the platform itself: what it is, how bad,
which kind of testing found it, the pull request that fixed it, when it was
re-tested and where it stands. These are facts about the platform, not about
any customer organisation, so the table carries no organisation column and is
listed in ``scripts/unfenced_tables.txt``. It is written only by security
architects and platform administrators and read through
``app/modules/trust_centre/services.py``.

Testing is recorded honestly. Each finding names its own source and the sources
are kept apart: an internal scan, a manual checklist run and an external
firm's report are three different kinds of evidence, and the register never
presents one as another. A source with no findings recorded is shown as such,
not as a clean result.

Status moves open -> fixed_unretested -> closed. A finding can only be closed
by a passing re-test, so ``fixed_unretested`` is the visible "fixed without
re-test" state.
"""

from datetime import date, datetime

from .. import db

SEVERITIES = ("critical", "high", "medium", "low", "informational")
SEVERITY_LABELS = {
    "critical": "Critical",
    "high": "High",
    "medium": "Medium",
    "low": "Low",
    "informational": "Informational",
}

SOURCES = ("internal_scan", "manual_checklist", "external_report")
SOURCE_LABELS = {
    "internal_scan": "Internal scan (OWASP ZAP baseline and authenticated)",
    "manual_checklist": "Manual checklist (ASVS Level 2)",
    "external_report": "External firm's report",
}

STATUSES = ("open", "fixed_unretested", "closed")
STATUS_LABELS = {
    "open": "Open",
    "fixed_unretested": "Fixed, not yet re-tested",
    "closed": "Closed after re-test",
}


class SecurityFinding(db.Model):
    """A weakness found by testing the platform, tracked to a re-tested fix."""

    __tablename__ = "security_findings"
    __table_args__ = ({"extend_existing": True},)

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(300), nullable=False)
    description = db.Column(db.Text, nullable=True)
    severity = db.Column(db.String(20), nullable=False, index=True)
    source = db.Column(db.String(30), nullable=False, index=True)
    # The rule id, checklist item or report section the finding came from.
    source_reference = db.Column(db.String(300), nullable=True)
    discovered_on = db.Column(db.Date, nullable=False)
    fix_pr_url = db.Column(db.String(500), nullable=True)
    retest_scheduled_on = db.Column(db.Date, nullable=True)
    retested_on = db.Column(db.Date, nullable=True)
    # "passed" | "failed" | NULL (not re-tested).
    retest_result = db.Column(db.String(10), nullable=True)
    status = db.Column(db.String(20), nullable=False, default="open", index=True)
    closed_on = db.Column(db.Date, nullable=True)
    published_at = db.Column(db.DateTime, nullable=True, index=True)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=True)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=True
    )

    @property
    def fixed_without_retest(self) -> bool:
        """A fix is recorded but no passing re-test has closed the finding."""
        return self.status == "fixed_unretested"

    @property
    def retest_overdue(self) -> bool:
        return bool(
            self.fixed_without_retest
            and self.retest_scheduled_on
            and self.retest_scheduled_on < date.today()
        )

    @property
    def severity_label(self) -> str:
        return SEVERITY_LABELS.get(self.severity, self.severity)

    @property
    def source_label(self) -> str:
        return SOURCE_LABELS.get(self.source, self.source)

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)
