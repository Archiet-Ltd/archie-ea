"""Application metrics snapshot model for tracking trends over time"""
from datetime import datetime

from sqlalchemy.orm import relationship

from app import db


class ApplicationMetricsSnapshot(db.Model):
    """Store daily/weekly snapshots of application portfolio metrics for trend analysis"""

    __tablename__ = "application_metrics_snapshots"

    id = db.Column(db.Integer, primary_key=True)
    snapshot_date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)

    # Core metrics
    total_count = db.Column(db.Integer, default=0)
    production_count = db.Column(db.Integer, default=0)
    in_development_count = db.Column(db.Integer, default=0)
    critical_count = db.Column(db.Integer, default=0)

    # Additional metrics for future use
    planned_count = db.Column(db.Integer, default=0)
    retired_count = db.Column(db.Integer, default=0)
    high_criticality_count = db.Column(db.Integer, default=0)

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    notes = db.Column(
        db.Text
    )  # Optional: reason for snapshot (e.g., "Daily snapshot", "Pre-migration")

    # Note: relationship() connections to ArchiMate elements can be added as needed

    def __repr__(self):
        return f"<ApplicationMetricsSnapshot {self.snapshot_date}: {self.total_count} apps>"

    @classmethod
    def create_snapshot(cls, notes=None):
        """Create a new snapshot with current metrics"""
        from app.models.application_layer import ApplicationComponent
        from app.services.application_status import words_for_status

        total = ApplicationComponent.query.count()
        production = ApplicationComponent.query.filter(
            ApplicationComponent.deployment_status.in_(["production", "done"])
        ).count()
        in_dev = ApplicationComponent.query.filter(
            ApplicationComponent.deployment_status.in_(
                ["development", "testing", "staging", "in_process"]
            )
        ).count()
        critical = ApplicationComponent.query.filter(
            ApplicationComponent.business_criticality.in_(["High", "Critical"])
        ).count()
        # Planned is the list's first stage, Design, and the older words that now
        # mean it. Development, testing and staging are counted as in development
        # above, so the planned, in-development and production counts never overlap.
        planned = ApplicationComponent.query.filter(
            db.func.lower(db.func.trim(ApplicationComponent.deployment_status)).in_(
                [*words_for_status("design"), "not_started"]
            )
        ).count()
        high_crit = ApplicationComponent.query.filter(
            ApplicationComponent.business_criticality == "High"
        ).count()

        snapshot = cls(
            total_count=total,
            production_count=production,
            in_development_count=in_dev,
            critical_count=critical,
            planned_count=planned,
            high_criticality_count=high_crit,
            notes=notes,
        )

        db.session.add(snapshot)
        db.session.commit()

        return snapshot

    @classmethod
    def get_latest(cls):
        """Get the most recent snapshot"""
        return cls.query.order_by(cls.snapshot_date.desc()).first()

    @classmethod
    def get_snapshot_before(cls, days_ago=30):
        """Get the most recent snapshot from N days ago"""
        from datetime import timedelta

        target_date = datetime.utcnow() - timedelta(days=days_ago)
        return (
            cls.query.filter(cls.snapshot_date <= target_date)
            .order_by(cls.snapshot_date.desc())
            .first()
        )
