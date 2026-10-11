"""Solution Blueprint Proposal — staging area for document-extracted architecture elements."""
# migration-exempt — new columns added via db.create_all() (migration freeze)

from app import db
from app.models.mixins import TenantMixin


class SolutionBlueprintProposal(TenantMixin, db.Model):
    __tablename__ = "solution_blueprint_proposals"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    solution_id = db.Column(db.Integer, db.ForeignKey("solutions.id"), index=True, nullable=False)
    archimate_type = db.Column(db.String(64), nullable=False)
    name = db.Column(db.String(256), nullable=False)
    description = db.Column(db.Text, nullable=True)
    capability_id = db.Column(db.Integer, nullable=True)
    source = db.Column(db.String(32), default="document")
    source_doc_name = db.Column(db.String(256), nullable=True)
    confidence = db.Column(db.Float, default=1.0)
    status = db.Column(db.String(16), default="proposed")
    created_at = db.Column(db.DateTime, default=db.func.now())

    # ACM Domain-Driven Architecture metadata
    acm_domain = db.Column(db.String(10), nullable=True, index=True)
    is_baseline = db.Column(db.Boolean, default=False)
    overlay_code = db.Column(db.String(32), nullable=True)
    match_type = db.Column(db.String(16), nullable=True)
    existing_element_id = db.Column(db.Integer, nullable=True)
    waived = db.Column(db.Boolean, default=False)
    waiver_reason = db.Column(db.Text, nullable=True)
    cross_domain_rule_id = db.Column(db.Integer, nullable=True)
    promoted_element_id = db.Column(db.Integer, nullable=True)
    default_rel_type = db.Column(db.String(64), nullable=True)
    default_rel_target_id = db.Column(db.Integer, nullable=True)
    acm_properties = db.Column(db.JSON, default=dict)
    decision_rationale = db.Column(db.Text, nullable=True)

    # Consolidation: set by `flask backfill-review-queue-approvals` on
    # the row's canonical ai_chat_crud_approvals copy. NULL until backfilled;
    # this table stays readable (never dropped), it just stops gaining new
    # rows once its constructor sites are repointed.
    retired_into_id = db.Column(
        db.Integer, db.ForeignKey("ai_chat_crud_approvals.id"), nullable=True
    )
