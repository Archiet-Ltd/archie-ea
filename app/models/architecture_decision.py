"""Architecture Decision Records (ADRs) for EA practitioners.  # migration-exempt

Records decisions about the enterprise architecture itself, linked to
ArchiMate elements and ARB sessions.  New columns added via db.create_all()
per migration-freeze policy.
"""

from datetime import datetime

from .. import db
from .mixins import TenantMixin

VALID_HORIZONS = ['strategic', 'tactical', 'operational']
VALID_AUTHORITY_LEVELS = ['enterprise_arb', 'domain_arb', 'project_deviation']
VALID_DECISION_TYPES = ['platform', 'integration', 'vendor', 'standard', 'deviation', 'decommission']
VALID_STATUSES = ['proposed', 'under_review', 'accepted', 'rejected', 'deprecated', 'superseded', 'expired']


class ArchitectureDecision(TenantMixin, db.Model):
    __tablename__ = "architecture_decisions"

    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    decision_id = db.Column(db.String(50), unique=True, nullable=True)  # e.g. 'AD-001' (nullable for solution-level ADRs)
    title = db.Column(db.String(200), nullable=False)
    status = db.Column(db.String(30), default="proposed")  # proposed, accepted, deprecated, superseded

    # TOGAF ADM phase this decision belongs to
    adm_phase = db.Column(db.String(10), nullable=True)  # A-H

    # Decision content (markdown)
    context = db.Column(db.Text, nullable=True)       # Why was this decision needed?
    decision = db.Column(db.Text, nullable=True)       # What was decided?
    consequences = db.Column(db.Text, nullable=True)   # What are the consequences?
    alternatives = db.Column(db.Text, nullable=True)   # What alternatives were considered? (text or JSON)
    rationale = db.Column(db.Text, nullable=True)      # Why this option was chosen
    constraints = db.Column(db.JSON, nullable=True)    # [{constraint_name, impact}]
    related_element_ids = db.Column(db.JSON, nullable=True)  # [element_id, ...] generic element refs

    # Links
    archimate_element_ids = db.Column(db.JSON, default=list)  # IDs of impacted ArchiMate elements
    arb_session_id = db.Column(
        db.Integer, db.ForeignKey("architecture_review_boards.id"), nullable=True
    )  # if backed by ARB review

    # Metadata
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # GOV-02: Decision approval tracking
    decided_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    decided_at = db.Column(db.DateTime, nullable=True)
    approved_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    approved_at = db.Column(db.DateTime, nullable=True)
    rejection_reason = db.Column(db.Text, nullable=True)

    # Enterprise vs solution-level
    enterprise_level = db.Column(db.Boolean, default=True, nullable=False)
    solution_id = db.Column(db.Integer, db.ForeignKey('solutions.id', ondelete='SET NULL'), nullable=True)

    # Lifecycle
    superseded_by_id = db.Column(db.Integer, db.ForeignKey('architecture_decisions.id'), nullable=True)
    valid_from = db.Column(db.DateTime, nullable=True)
    valid_until = db.Column(db.DateTime, nullable=True)

    # Provenance (ADR-0008: "a copy declares itself"). A row projected from
    # another store carries where it came from, so "why does this exist?" is
    # answered by query rather than by reading code. Both columns are nullable
    # so `reconcile-schema` can add them to existing databases (ADR-0002), and a
    # hand-authored ADR simply leaves them NULL.
    source_table = db.Column(db.String(64), nullable=True, index=True)
    source_id = db.Column(db.Integer, nullable=True, index=True)

    # Classification
    horizon = db.Column(db.String(20), nullable=True, default='strategic')
    authority_level = db.Column(db.String(30), nullable=True, default='enterprise_arb')
    decision_type = db.Column(db.String(30), nullable=True)

    # Fields the AI chat, workbench and solution-options-advisor creation
    # paths set that had no home here before they were repointed to write
    # this table directly instead of the superseded architecture_decision_records
    # (so the canonical table could become the only writer): affected systems identified for a chat-recorded
    # decision, free-text assumptions from a workbench-generated one, and an
    # estimated_effort/business_value pair plus a free-text decider label for
    # an AI-solution-architect-authored one, where the "decider" is not a
    # users.id row so decided_by_id cannot carry it.
    affected_systems = db.Column(db.JSON, nullable=True)
    assumptions = db.Column(db.Text, nullable=True)
    estimated_effort = db.Column(db.String(50), nullable=True)
    business_value = db.Column(db.String(50), nullable=True)
    decided_by_label = db.Column(db.Text, nullable=True)

    # Review and outcome: a decision can carry a
    # future date it must be looked at again -- a vendor contract renewal, a
    # deviation granted "for now" -- and the outcome once that review happens.
    # Both nullable: most decisions never set a review date, and one that does
    # has no outcome until the review actually happens.
    review_date = db.Column(db.Date, nullable=True, index=True)
    review_outcome = db.Column(db.Text, nullable=True)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    # Relationships
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    decided_by = db.relationship("User", foreign_keys=[decided_by_id])
    approved_by = db.relationship("User", foreign_keys=[approved_by_id])
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_id])
    superseded_by = db.relationship('ArchitectureDecision', foreign_keys=[superseded_by_id], remote_side='ArchitectureDecision.id', uselist=False)

    def to_dict(self):
        return {
            "id": self.id,
            "decision_id": self.decision_id,
            "title": self.title,
            "status": self.status,
            "adm_phase": self.adm_phase,
            "context": self.context,
            "decision": self.decision,
            "rationale": self.rationale,
            "alternatives": self.alternatives,
            "constraints": self.constraints or [],
            "consequences": self.consequences,
            "related_element_ids": self.related_element_ids or [],
            "archimate_element_ids": self.archimate_element_ids or [],
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "decided_by_id": self.decided_by_id,
            "decided_at": self.decided_at.isoformat() if self.decided_at else None,
            "approved_by_id": self.approved_by_id,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "rejection_reason": self.rejection_reason,
            "enterprise_level": self.enterprise_level,
            "solution_id": self.solution_id,
            "superseded_by_id": self.superseded_by_id,
            "valid_from": self.valid_from.isoformat() if self.valid_from else None,
            "valid_until": self.valid_until.isoformat() if self.valid_until else None,
            "horizon": self.horizon,
            "authority_level": self.authority_level,
            "decision_type": self.decision_type,
            "source_table": self.source_table,
            "source_id": self.source_id,
            "affected_systems": self.affected_systems or [],
            "assumptions": self.assumptions,
            "estimated_effort": self.estimated_effort,
            "business_value": self.business_value,
            "decided_by_label": self.decided_by_label,
            "review_date": self.review_date.isoformat() if self.review_date else None,
            "review_outcome": self.review_outcome,
            "reviewed_at": self.reviewed_at.isoformat() if self.reviewed_at else None,
            "reviewed_by_id": self.reviewed_by_id,
        }

    @property
    def is_due_for_review(self):
        """A review date has arrived with no outcome recorded for *this*
        cycle yet.

        Not simply ``review_outcome is None``: recording an outcome together
        with a next review date (a recurring review, the normal case) leaves
        ``review_outcome`` set from the just-finished cycle while
        ``review_date`` moves into the future — checking for "any outcome at
        all" would then hide the decision from every later cycle forever,
        once it has been reviewed even a single time. A review recorded
        before the *current* ``review_date`` belongs to a past cycle and
        does not count; one recorded on or after it does.
        """
        from datetime import date

        return (
            self.review_date is not None
            and self.review_date <= date.today()
            and (self.reviewed_at is None or self.reviewed_at.date() < self.review_date)
        )

    @classmethod
    def next_decision_id(cls):
        """Auto-generate the next AD-XXX id.

        ``decision_id`` is UNIQUE across the whole table, but this class is a
        ``TenantMixin`` — so the ORM query this used to run was narrowed to the
        caller's organisation by ``do_orm_execute``. Any second tenant's first
        ADR therefore computed ``AD-001``, collided with the unique index and
        500'd ``POST /architecture/decisions/new`` while persisting nothing.
        Allocate over the same domain the index enforces.
        """
        from app.utils.reference_numbers import next_reference

        return next_reference("architecture_decisions", "decision_id", "AD-")

    @classmethod
    def _element_match_clause(cls, element_ids):
        """The shared ``archimate_element_ids``/``related_element_ids``
        JSONB-contains predicate, or ``None`` when ``element_ids`` yields no
        usable id. One place, so ``affecting_elements`` and
        ``precedent_search`` can never silently drift onto two different
        answers for "is this decision recorded against this element" --
        found as a defect (duplicated matching logic) in PR 318 review.

        Two writers record the link in two columns: the decision form writes
        ``archimate_element_ids`` and the solution-design decision API writes
        ``related_element_ids``. Both are matched here. Ids are stored as
        numbers by the decision form; a string id written by an older path
        still names the same element, so both variants are matched.
        """
        from sqlalchemy.dialects.postgresql import JSONB

        ids = set()
        for raw in element_ids or ():
            try:
                ids.add(int(raw))
            except (TypeError, ValueError):
                continue
        if not ids:
            return None
        matches = []
        for column in (cls.archimate_element_ids, cls.related_element_ids):
            stored = db.cast(column, JSONB)
            matches += [stored.contains([i]) for i in sorted(ids)]
            matches += [stored.contains([str(i)]) for i in sorted(ids)]
        return db.or_(*matches)

    @classmethod
    def affecting_elements(cls, element_ids, organization_id):
        """The decisions of ``organization_id`` recorded against any of ``element_ids``.

        This is how a decision is found from the things it governs: the element
        page, the decision list filtered by element and the explanation of a
        worked-out connection all read it, so they cannot disagree. The
        organisation predicate is explicit so the answer is the same with or
        without a request's tenant context. Newest first.
        """
        clause = cls._element_match_clause(element_ids)
        if clause is None or organization_id is None:
            return []
        stmt = (
            db.select(cls)
            .where(cls.organization_id == organization_id)
            .where(clause)
            .order_by(cls.created_at.desc(), cls.id.desc())
        )
        return db.session.execute(stmt).scalars().all()

    @classmethod
    def due_for_review(cls, organization_id):
        """The caller's decisions whose review date has arrived with no
        outcome recorded for *this* cycle yet, earliest due date first.

        Matches ``is_due_for_review``'s own predicate, not a bare
        ``review_outcome IS NULL``: a recurring review (an outcome recorded
        together with a next review date) must become due again once that
        next date arrives, even though ``review_outcome`` already holds the
        previous cycle's text. ``reviewed_at`` before the current
        ``review_date`` means that outcome is stale, from a past cycle.
        """
        from datetime import date

        if organization_id is None:
            return []
        stmt = (
            db.select(cls)
            .where(cls.organization_id == organization_id)
            .where(cls.review_date.isnot(None))
            .where(cls.review_date <= date.today())
            .where(
                db.or_(
                    cls.reviewed_at.is_(None),
                    db.cast(cls.reviewed_at, db.Date) < cls.review_date,
                )
            )
            .order_by(cls.review_date.asc(), cls.id.asc())
        )
        return db.session.execute(stmt).scalars().all()

    @classmethod
    def precedent_search(cls, query_text, organization_id, element_ids=None):
        """The caller's decisions whose title/context/decision/rationale match
        ``query_text`` (case-insensitive substring), optionally narrowed to
        decisions recorded against any of ``element_ids``. An architect
        searches for precedent before ruling on a new case: what did
        we decide last time something like this came up, and against what.

        Newest first, matching ``affecting_elements``'s own ordering so the
        two surfaces never disagree on how precedent is ranked.
        """
        if organization_id is None:
            return []
        text = (query_text or "").strip()
        if not text and not element_ids:
            return []
        stmt = db.select(cls).where(cls.organization_id == organization_id)
        if text:
            like = f"%{text}%"
            stmt = stmt.where(
                db.or_(
                    cls.title.ilike(like),
                    cls.context.ilike(like),
                    cls.decision.ilike(like),
                    cls.rationale.ilike(like),
                )
            )
        if element_ids:
            clause = cls._element_match_clause(element_ids)
            if clause is not None:
                stmt = stmt.where(clause)
        stmt = stmt.order_by(cls.created_at.desc(), cls.id.desc())
        return db.session.execute(stmt).scalars().all()

    def record_review_outcome(self, outcome_text, reviewed_by_id):
        """Record the outcome of a due review: what review_date asked for has
        now happened. Does not touch review_date itself -- the caller (the
        record-outcome route's own "next review date" field) sets that
        separately, to a future date for a recurring review or leaves it
        unset to stop reviewing. ``is_due_for_review``/``due_for_review``
        compare ``reviewed_at`` against the *current* ``review_date`` rather
        than checking ``review_outcome`` alone, so a recurring review
        becomes due again once its next date arrives even though this
        method leaves the previous cycle's outcome text in place.
        """
        from datetime import datetime

        self.review_outcome = outcome_text
        self.reviewed_at = datetime.utcnow()
        self.reviewed_by_id = reviewed_by_id


VALID_LINK_TYPES = ['governs', 'constrains', 'enables']


class DecisionCapabilityLink(db.Model):
    """ARB-002: Many-to-many link between architecture decisions and capabilities."""
    __tablename__ = 'decision_capability_links'
    __table_args__ = (
        db.UniqueConstraint('decision_id', 'capability_id', name='uq_decision_capability'),
        {'extend_existing': True}
    )

    id = db.Column(db.Integer, primary_key=True)
    decision_id = db.Column(db.Integer, db.ForeignKey('architecture_decisions.id', ondelete='CASCADE'), nullable=False, index=True)
    # business_capability, not capabilities. Measured in production on 31 Aug
    # 2026: business_capability held 461 rows and capabilities held ZERO, so
    # this governance feature -- linking architecture decisions to the
    # capabilities they affect -- pointed at a store nothing populates. The
    # endpoint's existence guard was therefore correct and 404'd every real
    # capability a user has. Repointed with 0 rows in this table, so nothing
    # is migrated; see scripts/migrate_decision_capability_fk.sql.
    capability_id = db.Column(db.Integer, db.ForeignKey('business_capability.id', ondelete='CASCADE'), nullable=False, index=True)
    link_type = db.Column(db.String(20), nullable=False, default='governs')  # governs/constrains/enables
    is_primary = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    decision = db.relationship('ArchitectureDecision', foreign_keys=[decision_id], backref=db.backref('capability_links', lazy='dynamic', cascade='all, delete-orphan'))

    def to_dict(self):
        return {
            'id': self.id,
            'decision_id': self.decision_id,
            'capability_id': self.capability_id,
            'link_type': self.link_type,
            'is_primary': self.is_primary,
        }


# ── Phase H constants ────────────────────────────────────────────────────────

VALID_TRIGGER_TYPES = ['business_event', 'technology_eol', 'regulation', 'org_change', 'market', 'performance']
VALID_DISPOSITIONS = ['full_adm_cycle', 'incremental_update', 'exception', 'rejected', 'deferred']
VALID_CHANGE_REQUEST_STATUSES = ['open', 'assessing', 'disposition_set', 'closed']
VALID_IMPACT_LEVELS = ['critical', 'high', 'medium', 'low', 'none']
VALID_AFFECTED_ENTITY_TYPES = ['capability', 'application_component', 'business_service']


class ArchitectureChangeRequest(TenantMixin, db.Model):
    """ARB-004: Phase H change request — inbound signal that triggers change assessment."""
    __tablename__ = 'architecture_change_requests'
    __table_args__ = {'extend_existing': True}

    id = db.Column(db.Integer, primary_key=True)
    acr_reference = db.Column(db.String(50), unique=True, nullable=False)  # ACR-2026-001
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)

    # Trigger
    trigger_type = db.Column(db.String(50), nullable=False, default='business_event')
    trigger_description = db.Column(db.Text, nullable=True)

    # Workflow
    status = db.Column(db.String(30), nullable=False, default='open')
    disposition = db.Column(db.String(30), nullable=True)

    # Scope
    affected_domains = db.Column(db.JSON, nullable=True)  # ['application','technology','data','business']

    # FK to Phase H change management board (optional)
    board_id = db.Column(db.Integer, db.ForeignKey('kanban_boards.id', ondelete='SET NULL'), nullable=True)

    # Ownership
    raised_by_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    raised_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    closed_at = db.Column(db.DateTime, nullable=True)
    # R1-B85: the CTO scorecard escalates an open exception; nullable so an
    # un-escalated row reads as "-", never a fabricated date.
    escalated_at = db.Column(db.DateTime, nullable=True)

    raised_by = db.relationship('User', foreign_keys=[raised_by_id])
    impact_assessments = db.relationship('ChangeImpactAssessment', backref='change_request', lazy='dynamic', cascade='all, delete-orphan')
    change_notices = db.relationship('ArchitectureChangeNotice', backref='change_request', lazy='dynamic', cascade='all, delete-orphan')

    def to_dict(self):
        return {
            'id': self.id,
            'acr_reference': self.acr_reference,
            'title': self.title,
            'description': self.description,
            'trigger_type': self.trigger_type,
            'trigger_description': self.trigger_description,
            'status': self.status,
            'disposition': self.disposition,
            'affected_domains': self.affected_domains or [],
            'board_id': self.board_id,
            'raised_by_id': self.raised_by_id,
            'raised_at': self.raised_at.isoformat() if self.raised_at else None,
            'closed_at': self.closed_at.isoformat() if self.closed_at else None,
            'escalated_at': self.escalated_at.isoformat() if self.escalated_at else None,
        }

    @classmethod
    def next_acr_reference(cls):
        from datetime import datetime as dt
        year = dt.utcnow().year
        last = cls.query.filter(cls.acr_reference.like(f'ACR-{year}-%')).order_by(cls.id.desc()).first()
        if last:
            try:
                n = int(last.acr_reference.split('-')[-1]) + 1
            except (ValueError, IndexError):
                n = 1
        else:
            n = 1
        return f'ACR-{year}-{n:03d}'


class ChangeImpactAssessment(db.Model):
    """ARB-004: Impact assessment record linking a change request to affected entities."""
    __tablename__ = 'change_impact_assessments'
    __table_args__ = {'extend_existing': True}

    id = db.Column(db.Integer, primary_key=True)
    change_request_id = db.Column(db.Integer, db.ForeignKey('architecture_change_requests.id', ondelete='CASCADE'), nullable=False, index=True)

    # Polymorphic affected entity
    affected_entity_type = db.Column(db.String(30), nullable=True)  # capability/application_component/business_service
    affected_capability_id = db.Column(db.Integer, db.ForeignKey('capabilities.id', ondelete='SET NULL'), nullable=True)
    affected_decision_id = db.Column(db.Integer, db.ForeignKey('architecture_decisions.id', ondelete='SET NULL'), nullable=True)

    # Assessment
    impact_level = db.Column(db.String(20), nullable=False, default='medium')
    impact_description = db.Column(db.Text, nullable=True)
    recommended_action = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    assessed_by_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)

    affected_decision = db.relationship('ArchitectureDecision', foreign_keys=[affected_decision_id])

    def to_dict(self):
        return {
            'id': self.id,
            'change_request_id': self.change_request_id,
            'affected_entity_type': self.affected_entity_type,
            'affected_capability_id': self.affected_capability_id,
            'affected_decision_id': self.affected_decision_id,
            'impact_level': self.impact_level,
            'impact_description': self.impact_description,
            'recommended_action': self.recommended_action,
        }


class ArchitectureChangeNotice(db.Model):
    """ARB-004: Output record from a Phase H change request — what changed and what was decided."""
    __tablename__ = 'architecture_change_notices'
    __table_args__ = {'extend_existing': True}

    id = db.Column(db.Integer, primary_key=True)
    change_request_id = db.Column(db.Integer, db.ForeignKey('architecture_change_requests.id', ondelete='CASCADE'), nullable=False, index=True)
    acn_reference = db.Column(db.String(50), unique=True, nullable=False)  # ACN-2026-001
    scope_description = db.Column(db.Text, nullable=True)
    adm_phases_invoked = db.Column(db.JSON, nullable=True)  # ['B','C','E']

    issued_by_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    issued_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    issued_by = db.relationship('User', foreign_keys=[issued_by_id])

    def to_dict(self):
        return {
            'id': self.id,
            'change_request_id': self.change_request_id,
            'acn_reference': self.acn_reference,
            'scope_description': self.scope_description,
            'adm_phases_invoked': self.adm_phases_invoked or [],
            'issued_by_id': self.issued_by_id,
            'issued_at': self.issued_at.isoformat() if self.issued_at else None,
        }

    @classmethod
    def next_acn_reference(cls):
        from datetime import datetime as dt
        year = dt.utcnow().year
        last = cls.query.filter(cls.acn_reference.like(f'ACN-{year}-%')).order_by(cls.id.desc()).first()
        if last:
            try:
                n = int(last.acn_reference.split('-')[-1]) + 1
            except (ValueError, IndexError):
                n = 1
        else:
            n = 1
        return f'ACN-{year}-{n:03d}'
