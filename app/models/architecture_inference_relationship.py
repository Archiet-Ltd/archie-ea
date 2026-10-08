"""Provenance-tagged relationship table for the ArchiMate Inference Engine.
Augments ArchiMateRelationship with cross-model-type support and audit fields.
"""
# migration-exempt — table created via db.create_all() per migration-freeze policy
from app import db
from app.models.mixins.core import TenantMixin


class ArchitectureInferenceRelationship(TenantMixin, db.Model):
    """Inferred/wizard-written ArchiMate relationship edges.

    Each row belongs to one organisation and is only visible to that
    organisation's users; the tenant filter applies through ``TenantMixin``.
    """

    __tablename__ = "architecture_inference_relationship"

    id = db.Column(db.Integer, primary_key=True)
    architecture_id = db.Column(db.Integer, index=True, nullable=False)

    # Nullable so reconcile-schema can add the column to an existing table (it
    # only adds nullable columns). The mixin still applies the tenant filter, and a
    # row with no organisation matches no organisation, so it is listed for
    # nobody. New rows take the organisation of the writing request/service; there
    # is no default owner.
    organization_id = db.Column(
        db.Integer,
        db.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    source_type = db.Column(db.String(64), nullable=False)
    source_id = db.Column(db.Integer, nullable=False)
    target_type = db.Column(db.String(64), nullable=False)
    target_id = db.Column(db.Integer, nullable=False)

    rel_type = db.Column(db.String(64), nullable=False)

    source_tag = db.Column(db.String(32), default="rule")
    confidence = db.Column(db.Float, default=1.0)
    inference_pass = db.Column(db.Integer, default=1)
    rule_name = db.Column(db.String(128), nullable=True)

    created_at = db.Column(db.DateTime, default=db.func.now())
    updated_at = db.Column(db.DateTime, default=db.func.now(), onupdate=db.func.now())

    __table_args__ = (
        db.UniqueConstraint(
            "architecture_id", "source_type", "source_id",
            "target_type", "target_id", "rel_type",
            name="uq_inference_rel_unique_edge",
        ),
    )

    def __repr__(self):
        return (
            f"<InferenceRel {self.source_type}:{self.source_id}"
            f" --{self.rel_type}--> "
            f"{self.target_type}:{self.target_id}>"
        )
