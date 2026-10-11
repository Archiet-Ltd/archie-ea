"""
AI Suggestion and User Preference Models

Supports hybrid manual/automated workflows by tracking AI-generated suggestions
and user preferences for entry modes across the platform.
"""

from datetime import datetime

from app import db


class UserPreference(db.Model):
    """
    User preferences for platform behavior including entry modes and AI assistance.
    """

    __tablename__ = "user_preferences"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), unique=True, nullable=False)

    # Default entry mode: 'manual', 'assisted', 'automated'
    default_entry_mode = db.Column(db.String(20), default="assisted")

    # AI assistance toggles
    show_ai_suggestions = db.Column(db.Boolean, default=True)
    auto_derive_relationships = db.Column(db.Boolean, default=True)
    auto_map_apqc_processes = db.Column(db.Boolean, default=True)
    auto_link_capabilities = db.Column(db.Boolean, default=True)

    # Approval settings
    require_approval_for_ai = db.Column(db.Boolean, default=True)
    auto_accept_high_confidence = db.Column(db.Boolean, default=False)
    high_confidence_threshold = db.Column(db.Float, default=0.85)

    # Notification preferences
    notify_on_suggestions = db.Column(db.Boolean, default=True)
    notify_on_gaps_detected = db.Column(db.Boolean, default=True)
    notify_on_policy_violations = db.Column(db.Boolean, default=True)
    # Service-status subscription: tell this user when a platform incident
    # opens or resolves. NULL (never chosen) reads as not subscribed.
    notify_on_service_status = db.Column(db.Boolean, nullable=True)

    # Per-workflow mode overrides (JSON)
    workflow_mode_overrides = db.Column(db.JSON, default=dict)
    # Example: {'application_onboarding': 'manual', 'gap_analysis': 'automated'}

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user = db.relationship("User", backref=db.backref("preferences", uselist=False))

    def __repr__(self):
        return f"<UserPreference user_id={self.user_id} mode={self.default_entry_mode}>"

    def get_mode_for_workflow(self, workflow_name):
        """Get the entry mode for a specific workflow, with fallback to default."""
        if self.workflow_mode_overrides and workflow_name in self.workflow_mode_overrides:
            return self.workflow_mode_overrides[workflow_name]
        return self.default_entry_mode

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "default_entry_mode": self.default_entry_mode,
            "show_ai_suggestions": self.show_ai_suggestions,
            "auto_derive_relationships": self.auto_derive_relationships,
            "auto_map_apqc_processes": self.auto_map_apqc_processes,
            "auto_link_capabilities": self.auto_link_capabilities,
            "require_approval_for_ai": self.require_approval_for_ai,
            "auto_accept_high_confidence": self.auto_accept_high_confidence,
            "high_confidence_threshold": self.high_confidence_threshold,
            "notify_on_suggestions": self.notify_on_suggestions,
            "notify_on_gaps_detected": self.notify_on_gaps_detected,
            "notify_on_policy_violations": self.notify_on_policy_violations,
            "workflow_mode_overrides": self.workflow_mode_overrides or {},
        }

    @classmethod
    def get_or_create(cls, user_id):
        """Get existing preferences or create default ones for a user."""
        pref = cls.query.filter_by(user_id=user_id).first()
        if not pref:
            pref = cls(user_id=user_id)
            db.session.add(pref)
            db.session.commit()
        return pref


class AISuggestion(db.Model):
    """
    Tracks AI-generated suggestions awaiting user review.
    Supports the hybrid manual/automated workflow pattern.
    """

    __tablename__ = "ai_suggestions"

    id = db.Column(db.Integer, primary_key=True)

    # Target entity (what this suggestion is for)
    entity_type = db.Column(db.String(50), nullable=False, index=True)
    # e.g., 'application', 'capability', 'vendor', 'relationship', 'gap'
    entity_id = db.Column(db.Integer, nullable=True, index=True)
    # null if suggesting a new entity

    # Suggestion details
    suggestion_type = db.Column(db.String(50), nullable=False)
    # 'field_value', 'relationship', 'new_entity', 'mapping', 'classification'

    field_name = db.Column(db.String(100), nullable=True)
    # The specific field being suggested (e.g., 'vendor_id', 'apqc_process_id')

    suggested_value = db.Column(db.JSON, nullable=False)
    # The AI's suggested value (can be simple value, object, or array)

    current_value = db.Column(db.JSON, nullable=True)
    # The current value (for comparison, if entity exists)

    # Confidence and reasoning
    confidence = db.Column(db.Float, nullable=False, default=0.5)
    confidence_factors = db.Column(db.JSON, nullable=True)
    # Breakdown of confidence score: {'semantic_match': 0.9, 'pattern_match': 0.7, ...}

    reasoning = db.Column(db.Text, nullable=True)
    # Human-readable explanation of why this was suggested

    # Source of suggestion
    source = db.Column(db.String(100), nullable=False)
    # 'document_analysis', 'relationship_derivation', 'pattern_matching',
    # 'apqc_mapping', 'capability_discovery', 'gap_detection', 'vendor_matching'

    source_reference = db.Column(db.JSON, nullable=True)
    # Reference to source: {'document_id': 123, 'page': 5} or {'rule_id': 'R001'}

    # Workflow context
    workflow_name = db.Column(db.String(100), nullable=True)
    workflow_step = db.Column(db.String(100), nullable=True)
    batch_id = db.Column(db.String(50), nullable=True, index=True)
    # Groups related suggestions from same operation

    # Status tracking
    status = db.Column(db.String(20), default="pending", index=True)
    # 'pending', 'accepted', 'rejected', 'modified', 'expired', 'auto_accepted'

    # Review information
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    review_notes = db.Column(db.Text, nullable=True)

    # Final outcome
    final_value = db.Column(db.JSON, nullable=True)
    # What was actually saved (may differ from suggestion if modified)

    was_helpful = db.Column(db.Boolean, nullable=True)
    # User feedback: was this suggestion useful?

    # Priority and categorization
    priority = db.Column(db.String(20), default="normal")
    # 'critical', 'high', 'normal', 'low'

    category = db.Column(db.String(50), nullable=True)
    # Additional categorization: 'data_quality', 'compliance', 'optimization'

    # Architect verdict (confidence calibration)
    architect_verdict = db.Column(db.String(20), nullable=True)  # accepted, modified, rejected
    verdict_note = db.Column(db.Text, nullable=True)  # Optional architect comment
    verdict_at = db.Column(db.DateTime, nullable=True)  # When verdict was recorded

    # Expiration
    expires_at = db.Column(db.DateTime, nullable=True)
    # Suggestions can expire if entity changes

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    reviewed_by = db.relationship("User", backref="reviewed_suggestions")

    __table_args__ = (
        db.Index("ix_ai_suggestions_entity", "entity_type", "entity_id"),
        db.Index("ix_ai_suggestions_status_created", "status", "created_at"),
        db.Index("ix_ai_suggestions_batch", "batch_id", "status"),
    )

    def __repr__(self):
        return f"<AISuggestion {self.id} {self.entity_type}.{self.field_name} ({self.status}, {self.confidence:.0%})>"

    def to_dict(self):
        return {
            "id": self.id,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "suggestion_type": self.suggestion_type,
            "field_name": self.field_name,
            "suggested_value": self.suggested_value,
            "current_value": self.current_value,
            "confidence": self.confidence,
            "confidence_percent": f"{self.confidence * 100:.0f}%",
            "confidence_factors": self.confidence_factors,
            "reasoning": self.reasoning,
            "source": self.source,
            "source_reference": self.source_reference,
            "workflow_name": self.workflow_name,
            "workflow_step": self.workflow_step,
            "batch_id": self.batch_id,
            "status": self.status,
            "reviewed_by_id": self.reviewed_by_id,
            "reviewed_by_name": self.reviewed_by.email if self.reviewed_by else None,
            "reviewed_at": self.reviewed_at.isoformat() if self.reviewed_at else None,
            "review_notes": self.review_notes,
            "final_value": self.final_value,
            "was_helpful": self.was_helpful,
            "priority": self.priority,
            "category": self.category,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "architect_verdict": self.architect_verdict,
            "verdict_note": self.verdict_note,
            "verdict_at": self.verdict_at.isoformat() if self.verdict_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def to_summary_dict(self):
        """Lightweight dict for list views."""
        return {
            "id": self.id,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "field_name": self.field_name,
            "suggested_value": self.suggested_value,
            "confidence": self.confidence,
            "confidence_percent": f"{self.confidence * 100:.0f}%",
            "source": self.source,
            "status": self.status,
            "priority": self.priority,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def accept(self, user_id, final_value=None, notes=None):
        """Accept this suggestion."""
        self.status = "accepted"
        self.reviewed_by_id = user_id
        self.reviewed_at = datetime.utcnow()
        self.review_notes = notes
        self.final_value = final_value if final_value is not None else self.suggested_value
        return self

    def reject(self, user_id, notes=None):
        """Reject this suggestion."""
        self.status = "rejected"
        self.reviewed_by_id = user_id
        self.reviewed_at = datetime.utcnow()
        self.review_notes = notes
        return self

    def modify(self, user_id, final_value, notes=None):
        """Accept with modifications."""
        self.status = "modified"
        self.reviewed_by_id = user_id
        self.reviewed_at = datetime.utcnow()
        self.review_notes = notes
        self.final_value = final_value
        return self

    def mark_helpful(self, helpful: bool):
        """Record user feedback on suggestion quality."""
        self.was_helpful = helpful
        return self

    @classmethod
    def get_pending_for_entity(cls, entity_type, entity_id):
        """Get all pending suggestions for a specific entity."""
        return (
            cls.query.filter(
                cls.entity_type == entity_type, cls.entity_id == entity_id, cls.status == "pending"
            )
            .order_by(cls.confidence.desc())
            .all()
        )

    @classmethod
    def get_pending_for_user(cls, user_id=None, limit=50):
        """Get pending suggestions, optionally filtered by reviewer assignment."""
        query = cls.query.filter(cls.status == "pending")
        if user_id:
            query = query.filter(cls.reviewed_by_id == user_id)
        return (
            query.order_by(cls.priority.desc(), cls.confidence.desc(), cls.created_at.desc())
            .limit(limit)
            .all()
        )

    @classmethod
    def get_by_batch(cls, batch_id):
        """Get all suggestions in a batch."""
        return cls.query.filter(cls.batch_id == batch_id).all()

    @classmethod
    def get_statistics(cls):
        """Get suggestion statistics for dashboard."""
        from sqlalchemy import func

        total = cls.query.count()
        pending = cls.query.filter(cls.status == "pending").count()
        accepted = cls.query.filter(cls.status == "accepted").count()
        rejected = cls.query.filter(cls.status == "rejected").count()
        modified = cls.query.filter(cls.status == "modified").count()
        auto_accepted = cls.query.filter(cls.status == "auto_accepted").count()

        # Acceptance rate
        reviewed = accepted + rejected + modified
        acceptance_rate = (accepted + modified) / reviewed if reviewed > 0 else 0

        # Average confidence by status
        avg_confidence = (
            db.session.query(cls.status, func.avg(cls.confidence)).group_by(cls.status).all()
        )

        # By entity type
        by_entity = (
            db.session.query(cls.entity_type, func.count(cls.id))
            .filter(cls.status == "pending")
            .group_by(cls.entity_type)
            .all()
        )

        # By source
        by_source = (
            db.session.query(cls.source, func.count(cls.id))
            .filter(cls.status == "pending")
            .group_by(cls.source)
            .all()
        )

        # Helpfulness score
        helpful_count = cls.query.filter(cls.was_helpful == True).count()
        unhelpful_count = cls.query.filter(cls.was_helpful == False).count()
        feedback_total = helpful_count + unhelpful_count
        helpfulness_rate = helpful_count / feedback_total if feedback_total > 0 else None

        return {
            "total": total,
            "pending": pending,
            "accepted": accepted,
            "rejected": rejected,
            "modified": modified,
            "auto_accepted": auto_accepted,
            "acceptance_rate": round(acceptance_rate * 100, 1),
            "avg_confidence_by_status": {status: round(conf, 2) for status, conf in avg_confidence},
            "pending_by_entity_type": dict(by_entity),
            "pending_by_source": dict(by_source),
            "helpfulness_rate": round(helpfulness_rate * 100, 1) if helpfulness_rate else None,
            "feedback_count": feedback_total,
        }

    @classmethod
    def bulk_accept_high_confidence(cls, user_id, threshold=0.85, entity_type=None):
        """Auto-accept all high-confidence pending suggestions."""
        query = cls.query.filter(cls.status == "pending", cls.confidence >= threshold)
        if entity_type:
            query = query.filter(cls.entity_type == entity_type)

        suggestions = query.all()
        count = 0
        for suggestion in suggestions:
            suggestion.status = "auto_accepted"
            suggestion.reviewed_by_id = user_id
            suggestion.reviewed_at = datetime.utcnow()
            suggestion.final_value = suggestion.suggested_value
            suggestion.review_notes = f"Auto-accepted (confidence >= {threshold:.0%})"
            count += 1

        return count

    @classmethod
    def bulk_reject_low_confidence(cls, user_id, threshold=0.5, entity_type=None):
        """Reject all low-confidence pending suggestions."""
        query = cls.query.filter(cls.status == "pending", cls.confidence < threshold)
        if entity_type:
            query = query.filter(cls.entity_type == entity_type)

        suggestions = query.all()
        count = 0
        for suggestion in suggestions:
            suggestion.status = "rejected"
            suggestion.reviewed_by_id = user_id
            suggestion.reviewed_at = datetime.utcnow()
            suggestion.review_notes = f"Bulk rejected (confidence < {threshold:.0%})"
            count += 1

        return count


class SuggestionFeedback(db.Model):
    """
    Aggregated feedback on suggestions for improving AI quality over time.
    """

    __tablename__ = "suggestion_feedback"

    id = db.Column(db.Integer, primary_key=True)

    # Aggregation key
    entity_type = db.Column(db.String(50), nullable=False)
    field_name = db.Column(db.String(100), nullable=True)
    source = db.Column(db.String(100), nullable=False)

    # Metrics
    total_suggestions = db.Column(db.Integer, default=0)
    accepted_count = db.Column(db.Integer, default=0)
    rejected_count = db.Column(db.Integer, default=0)
    modified_count = db.Column(db.Integer, default=0)
    helpful_count = db.Column(db.Integer, default=0)
    unhelpful_count = db.Column(db.Integer, default=0)

    # Calculated rates
    acceptance_rate = db.Column(db.Float, default=0.0)
    modification_rate = db.Column(db.Float, default=0.0)
    helpfulness_rate = db.Column(db.Float, default=0.0)

    # Average confidence at different outcomes
    avg_confidence_accepted = db.Column(db.Float, nullable=True)
    avg_confidence_rejected = db.Column(db.Float, nullable=True)

    # Period
    period_start = db.Column(db.DateTime, nullable=False)
    period_end = db.Column(db.DateTime, nullable=False)

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint(
            "entity_type", "field_name", "source", "period_start", name="uq_suggestion_feedback_key"
        ),
    )

    def __repr__(self):
        return f"<SuggestionFeedback {self.entity_type}.{self.field_name} from {self.source}>"

    @classmethod
    def aggregate_feedback(cls, start_date, end_date):
        """Aggregate suggestion feedback for a time period."""
        from sqlalchemy import func

        results = (
            db.session.query(
                AISuggestion.entity_type,
                AISuggestion.field_name,
                AISuggestion.source,
                func.count(AISuggestion.id).label("total"),
                func.sum(db.case((AISuggestion.status == "accepted", 1), else_=0)).label(
                    "accepted"
                ),
                func.sum(db.case((AISuggestion.status == "rejected", 1), else_=0)).label(
                    "rejected"
                ),
                func.sum(db.case((AISuggestion.status == "modified", 1), else_=0)).label(
                    "modified"
                ),
                func.sum(db.case((AISuggestion.was_helpful == True, 1), else_=0)).label("helpful"),
                func.sum(db.case((AISuggestion.was_helpful == False, 1), else_=0)).label(
                    "unhelpful"
                ),
                func.avg(
                    db.case((AISuggestion.status == "accepted", AISuggestion.confidence))
                ).label("avg_conf_accepted"),
                func.avg(
                    db.case((AISuggestion.status == "rejected", AISuggestion.confidence))
                ).label("avg_conf_rejected"),
            )
            .filter(
                AISuggestion.created_at >= start_date,
                AISuggestion.created_at < end_date,
                AISuggestion.status.in_(["accepted", "rejected", "modified", "auto_accepted"]),
            )
            .group_by(AISuggestion.entity_type, AISuggestion.field_name, AISuggestion.source)
            .all()
        )

        feedback_records = []
        for r in results:
            reviewed = r.accepted + r.rejected + r.modified
            feedback_total = r.helpful + r.unhelpful

            record = cls(
                entity_type=r.entity_type,
                field_name=r.field_name,
                source=r.source,
                total_suggestions=r.total,
                accepted_count=r.accepted,
                rejected_count=r.rejected,
                modified_count=r.modified,
                helpful_count=r.helpful,
                unhelpful_count=r.unhelpful,
                acceptance_rate=(r.accepted + r.modified) / reviewed if reviewed > 0 else 0,
                modification_rate=r.modified / reviewed if reviewed > 0 else 0,
                helpfulness_rate=r.helpful / feedback_total if feedback_total > 0 else 0,
                avg_confidence_accepted=r.avg_conf_accepted,
                avg_confidence_rejected=r.avg_conf_rejected,
                period_start=start_date,
                period_end=end_date,
            )
            feedback_records.append(record)

        return feedback_records
