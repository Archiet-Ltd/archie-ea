"""Published API Spec model — enterprise API contract registry.  # migration-exempt

Stores versioned OpenAPI/AsyncAPI specs generated from solution blueprints,
enabling cross-team discovery and consumption of API contracts.

Table created via db.create_all() — no Alembic migration needed (migration freeze).
"""

from datetime import datetime

from app import db
from app.models.mixins import TenantMixin


class PublishedAPISpec(TenantMixin, db.Model):
    __tablename__ = "published_api_specs"
    __tenant_parent__ = (('solution_id', 'solutions'),)
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    solution_id = db.Column(
        db.Integer, db.ForeignKey("solutions.id"), nullable=False, index=True
    )
    spec_type = db.Column(db.String(20), nullable=False)  # openapi, asyncapi
    spec_version = db.Column(db.String(20), nullable=False)  # semver like 1.0.0
    spec_hash = db.Column(db.String(64))
    spec_content = db.Column(db.JSON, nullable=False)
    title = db.Column(db.String(200))
    description = db.Column(db.Text)
    endpoint_count = db.Column(db.Integer, default=0)
    published_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    published_at = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(20), default="draft")  # draft, published, deprecated

    # Relationships (lazy, no backref to avoid mapper overhead)
    solution = db.relationship("Solution", foreign_keys=[solution_id], lazy="select")

    def to_dict(self):
        return {
            "id": self.id,
            "solution_id": self.solution_id,
            "spec_type": self.spec_type,
            "spec_version": self.spec_version,
            "spec_hash": self.spec_hash,
            "title": self.title,
            "description": self.description,
            "endpoint_count": self.endpoint_count,
            "status": self.status,
            "published_at": self.published_at.isoformat() if self.published_at else None,
        }
