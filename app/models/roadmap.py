from datetime import datetime

from app import db
from app.models.mixins.core import TenantMixin


class RoadmapTask(TenantMixin, db.Model):
    """
    Task model for work packages - ArchiMate 3.2 Implementation Event aligned.
    Tasks represent granular work items within a work package.

    Pre-TenantMixin, this table (core roadmap/portfolio-planning data) had no
    tenant boundary at all: any org could read/edit every other org's roadmap
    tasks via routes_capability_roadmap.py, migration_planner.py and
    consolidation_list_routes.py. Pre-existing rows are given their organisation
    by `flask backfill-layer-tenancy`, derived from the creating user, the work
    package's creator, or the consolidation entry's application; rows with none
    of those stay NULL and are reported.
    """

    __tablename__ = "roadmap_tasks"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.BigInteger, primary_key=True)
    title = db.Column(db.String(255), nullable=False, index=True)
    description = db.Column(db.Text)
    start_date = db.Column(db.Date, index=True)
    end_date = db.Column(db.Date, index=True)
    status = db.Column(db.String(50), default="planned", index=True)
    dependencies = db.Column(db.Text)  # JSON list of task ids
    owners = db.Column(db.Text)  # JSON list of user ids
    archimate_element_id = db.Column(db.Integer, nullable=True)
    migration_type = db.Column(db.String(50), nullable=True)
    plateau_from = db.Column(db.Integer, nullable=True)
    plateau_to = db.Column(db.Integer, nullable=True)
    percent_complete = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # === Work Package Relationship (links task to UnifiedWorkPackage) ===
    # Using BigInteger to match database schema, no FK constraint for flexibility
    unified_work_package_id = db.Column(db.BigInteger, nullable=True, index=True)

    # === Capability Level (typically L3 for tasks) ===
    capability_level = db.Column(db.String(10), default="L3", index=True)  # L1, L2, L3, L4

    # === ArchiMate 3.2 Compliance - Implementation Event ===
    archimate_element_type = db.Column(db.String(50), default="ImplementationEvent")

    # === Priority and Effort ===
    priority = db.Column(db.String(20), default="medium")  # low, medium, high, critical
    estimated_hours = db.Column(db.Float, default=0.0)
    actual_hours = db.Column(db.Float, default=0.0)

    # === Assignment ===
    assigned_to = db.Column(db.String(255), index=True)

    # === Audit Fields ===
    created_by = db.Column(db.Integer, nullable=True)
    updated_by = db.Column(db.Integer, nullable=True)

    def to_dict(self, include_work_package=False):
        """Convert task to dictionary representation"""
        result = {
            "id": str(self.id) if self.id else None,  # String for JavaScript BigInt safety
            "title": self.title,
            "description": self.description,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "status": self.status,
            "dependencies": self.dependencies,
            "owners": self.owners,
            "archimate_element_id": str(self.archimate_element_id)
            if self.archimate_element_id
            else None,
            "archimate_element_type": self.archimate_element_type,
            "migration_type": self.migration_type,
            "plateau_from": self.plateau_from,
            "plateau_to": self.plateau_to,
            "percent_complete": self.percent_complete,
            "unified_work_package_id": str(self.unified_work_package_id)
            if self.unified_work_package_id
            else None,
            "capability_level": self.capability_level,
            "priority": self.priority,
            "estimated_hours": self.estimated_hours,
            "actual_hours": self.actual_hours,
            "assigned_to": self.assigned_to,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

        if include_work_package and self.work_package:
            result["work_package"] = {
                "id": str(self.work_package.id) if self.work_package.id else None,
                "name": self.work_package.name,
            }

        return result

    def calculate_duration_days(self):
        """Calculate task duration in days"""
        if self.start_date and self.end_date:
            return (self.end_date - self.start_date).days + 1
        return 0

    def is_overdue(self):
        """Check if task is overdue"""
        if self.end_date and self.status not in ["completed", "cancelled"]:
            from datetime import date

            return date.today() > self.end_date
        return False
