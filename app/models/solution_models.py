"""
Solution Architecture Models for ArchiMate 3.2

Canonical module for Solution, SolutionPattern, and Contract models.
These are the core solution design entities used throughout the platform.

Solutions Architecture Elements:
- Solution: Complete end-to-end solution implementation
- SolutionPattern: Reusable solution templates and patterns
- Contract: Business contracts, SLAs, and vendor agreements
"""
# migration-exempt — new columns added via db.create_all() (migration freeze)

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import backref

from .. import db
from .mixins import OptimisticLockMixin, TenantMixin
from .mixins.core import parent_organization_default

# Use db.relationship instead of importing relationship


class Solution(TenantMixin, db.Model, OptimisticLockMixin):
    """
    ArchiMate-inspired Solution model.

    Represents a complete end-to-end solution that delivers business value
    through integrated applications, technologies, and processes.

    Examples:
    - "Customer 360 Platform" integrating CRM, analytics, and support systems
    - "Supply Chain Optimization Solution" with WMS, TMS, and analytics
    - "Digital Banking Platform" with core banking, mobile, and fraud detection
    """

    __tablename__ = "solutions"
    __table_args__ = {"extend_existing": True}

    id = Column(db.Integer, primary_key=True)
    name = Column(db.String(255), nullable=False, index=True)
    description = Column(db.Text)

    # ArchiMate linkage
    archimate_element_id = Column(db.Integer, db.ForeignKey("archimate_elements.id"))

    # Solution characteristics
    solution_type = Column(db.String(50))  # Platform, Product, Service, Integration
    business_domain = Column(db.String(100))  # Customer, Finance, Supply Chain, HR
    complexity_level = Column(db.String(20))  # Simple, Moderate, Complex, Enterprise

    # Value proposition
    business_value = Column(db.Text)  # Business benefits and ROI
    target_outcomes = Column(db.JSON)  # List of expected business outcomes
    success_metrics = Column(db.JSON)  # KPIs for measuring success

    # Solution scope
    scope_description = Column(db.Text)
    problem_clarification = db.Column(db.Text, nullable=True)  # JSON: questions + answers from Step 1
    journey_state = db.Column(db.JSON, nullable=True)  # Wizard navigation state (step, decisions, etc.)
    has_acm_domains = db.Column(db.Boolean, default=False)
    industry_overlay_code = db.Column(db.String(32), nullable=True)

    # Blueprint page fields (added 2026-03-21)
    section_narratives = db.Column(db.JSON, default=dict, nullable=True)
    ux_preferences = db.Column(db.JSON, nullable=True, default=None)
    section_scores = db.Column(db.JSON, default=dict, nullable=True)
    blueprint_version = db.Column(db.Integer, default=1)
    blueprint_updated_at = db.Column(db.DateTime, nullable=True)
    blueprint_updated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    # Lifecycle maturity score (0-100), synced by `flask solutions sync-maturity --apply`
    maturity_current = db.Column(db.Integer, nullable=True)
    maturity_target = db.Column(db.Integer, nullable=True)

    in_scope_applications = Column(db.JSON)  # List of included applications
    out_of_scope_applications = Column(db.JSON)  # Explicit exclusions

    # Financial information
    estimated_cost = Column(db.Numeric(15, 2))
    actual_cost = Column(db.Numeric(15, 2))
    roi_percentage = Column(db.Float)
    payback_period_months = Column(db.Integer)

    # Timeline and roadmap
    planned_start_date = Column(db.Date)
    planned_end_date = Column(db.Date)
    actual_start_date = Column(db.Date)
    actual_end_date = Column(db.Date)

    # Solution status
    status = Column(db.String(30), default="planned")  # planned, in_progress, deployed, deprecated
    deployment_status = Column(db.String(30))  # design, development, testing, production

    # Governance
    solution_owner = Column(db.String(255))
    business_sponsor = Column(db.String(255))
    technical_lead = Column(db.String(255))

    @property
    def architecture_lead(self):
        return self.technical_lead

    # ARB Governance Workflow
    governance_status = Column(
        db.String(30), default="draft"
    )  # draft, proposed, arb_review, approved, rejected
    arb_submission_date = Column(db.DateTime)
    arb_approval_date = Column(db.DateTime)
    arb_review_item_id = Column(
        db.Integer,
        db.ForeignKey(
            "arb_review_items.id", use_alter=True, name="fk_solutions_arb_review_item_id"
        ),
    )
    arb_rejection_reason = Column(db.Text)

    # Metadata
    created_at = Column(db.DateTime, default=datetime.utcnow)
    updated_at = Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by_id = Column(db.Integer, db.ForeignKey("users.id"))

    # === ADM Lifecycle (Wave 1 - Convergence) ===
    analysis_session_id = Column(Integer, ForeignKey("solution_analysis_sessions.id", use_alter=True), nullable=True)
    adm_phase = Column(String(20), default="A")  # A|B|C|D|E|F|G|H

    # Architecture Assistant wizard state
    current_step = db.Column(db.Integer, default=1, nullable=False, server_default="1")
    arb_snapshot = db.Column(db.JSON, nullable=True)  # ArchiMate element snapshot at ARB approval
    quality_baseline_data = db.Column(db.JSON, nullable=True)  # Operational capability baseline (Step 1B)

    adm_phase_a_completed_at = Column(DateTime, nullable=True)
    adm_phase_b_completed_at = Column(DateTime, nullable=True)
    adm_phase_c_completed_at = Column(DateTime, nullable=True)
    adm_phase_d_completed_at = Column(DateTime, nullable=True)
    adm_phase_e_completed_at = Column(DateTime, nullable=True)
    adm_phase_f_completed_at = Column(DateTime, nullable=True)
    adm_phase_g_completed_at = Column(DateTime, nullable=True)
    adm_phase_h_completed_at = Column(DateTime, nullable=True)
    target_completion_date = Column(Date, nullable=True)
    auto_complexity = Column(String(20), nullable=True)  # Low|Medium|High|Very High
    scope_in = Column(Text, nullable=True)
    scope_out = Column(Text, nullable=True)
    affected_systems = Column(Text, nullable=True)
    security_lead = Column(String(255), nullable=True)
    data_protection_officer = Column(String(255), nullable=True)

    # Architect scratchpad — staging area for AI suggestions before commit
    scratchpad_items = Column(JSON, default=list)

    # ADM cycle traceability (Phase H → new cycle)
    parent_solution_id = Column(
        Integer,
        ForeignKey("solutions.id", use_alter=True, name="fk_solutions_parent_solution_id"),
        nullable=True,
    )

    # migration-exempt — schema evolves via scripts/sync_schema.py per the
    # CLAUDE.md migration freeze (no Alembic on this platform).
    # Transformation Programme membership (PROG-001) — groups in-flight
    # solutions under a StrategicInitiative for programme-level governance
    # rollups (clean-core score, ARB pipeline, risk, wave timeline).
    initiative_id = Column(
        Integer,
        ForeignKey(
            "strategic_initiatives.id",
            ondelete="RESTRICT",
            use_alter=True,
            name="fk_solutions_initiative_id",
        ),
        nullable=True,
        index=True,
    )
    workstream_id = Column(
        Integer,
        ForeignKey("programme_workstreams.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    # Relationships
    archimate_element = db.relationship("ArchiMateElement", foreign_keys=[archimate_element_id])
    created_by = db.relationship("User", foreign_keys=[created_by_id], backref="created_solutions")
    blueprint_updated_by = db.relationship("User", foreign_keys=[blueprint_updated_by_id])
    analysis_session = db.relationship(
        "SolutionAnalysisSession",
        foreign_keys=[analysis_session_id],
        backref=backref("solution", uselist=False),
        lazy="joined"
    )
    # ADM cycle parent/child
    parent_solution = db.relationship(
        "Solution", remote_side="Solution.id",
        foreign_keys=[parent_solution_id],
        backref=db.backref("child_solutions", lazy="dynamic"),
    )
    # Transformation Programme (PROG-001)
    initiative = db.relationship(
        "StrategicInitiative",
        foreign_keys=[initiative_id],
        backref=db.backref("member_solutions", lazy="dynamic"),
    )
    workstream = db.relationship("ProgrammeWorkstream", foreign_keys=[workstream_id])

    # Avoid declaring inverse relationship to ARBReviewItem here to prevent circular mapper initialization.
    # ARBReviewItem already declares solution relationship; access via ARBReviewItem.solution or ARBReviewItem.solutions backref.
    arb_review_item_id = arb_review_item_id  # keep column present

    # Junction table relationships (lazy loaded)
    applications = db.relationship(
        "ApplicationComponent",
        secondary="solution_applications",
        backref=db.backref("solutions", lazy="dynamic"),
        lazy="dynamic",
    )

    vendor_products = db.relationship(
        "VendorProduct",
        secondary="solution_vendor_products",
        backref=db.backref("solutions", lazy="dynamic"),
        lazy="dynamic",
    )

    contracts = db.relationship(
        "SolutionContract",
        secondary="solution_contracts",
        backref=db.backref("solutions", lazy="dynamic"),
        lazy="dynamic",
    )

    @property
    def adm_phase_label(self):
        """Human-readable ADM phase name."""
        labels = {
            "A": "Architecture Vision",
            "B": "Business Architecture",
            "C": "Information Systems Architecture",
            "D": "Technology Architecture",
            "E": "Opportunities & Solutions",
            "F": "Migration Planning",
            "G": "Implementation Governance",
            "H": "Architecture Change Management",
        }
        return labels.get(self.adm_phase, "Unknown")

    @property
    def adm_phases_completed(self):
        """Return list of completed phase letters."""
        completed = []
        for letter in "ABCDEFGH":
            field = f"adm_phase_{letter.lower()}_completed_at"
            if getattr(self, field, None) is not None:
                completed.append(letter)
        return completed

    # Phase-gate requirements: ArchiMate artifact minimums per ADM phase
    PHASE_GATE_REQUIREMENTS = {
        "A": {
            "required": [("motivation", "drivers", 1), ("motivation", "goals", 1)],
            "advisory": [("motivation", "requirements", 1)],
        },
        "B": {
            "required": [("strategy", "business_capability", 1)],
            "advisory": [("business", "business_processes", 1)],
        },
        "C": {
            "required": [],
            "advisory": [("application", "application_components", 1)],
        },
        "D": {
            "required": [],
            "advisory": [("technology", "technology_nodes", 1)],
        },
        "E": {
            "required": [("strategy", "courses_of_action", 1)],
            "advisory": [],
        },
        "F": {
            "required": [("implementation", "work_packages", 1)],
            "advisory": [("implementation", "plateaus", 1)],
        },
        "G": {
            "required": [],
            "checks": ["arb_readiness"],
        },
        "H": {
            "required": [],
            "checks": ["has_metrics"],
        },
    }

    def validate_phase_gate(self, phase_letter):
        """
        Validate ArchiMate artifact minimums before allowing phase advancement.

        Returns dict: {valid: bool, errors: [...], warnings: [...]}
        """
        phase_letter = str(phase_letter or "A").upper()[:1]
        if phase_letter not in "ABCDEFGH":
            phase_letter = "A"
        gate = self.PHASE_GATE_REQUIREMENTS.get(phase_letter, {})
        errors = []
        warnings = []

        # Count artifacts per (layer_type, element_table)
        artifact_counts = {}
        junctions = SolutionArchiMateElement.query.filter_by(
            solution_id=self.id
        ).all()
        for j in junctions:
            key = (j.layer_type, j.element_table)
            artifact_counts[key] = artifact_counts.get(key, 0) + 1

        # Check required artifacts
        for layer, table, min_count in gate.get("required", []):
            actual = artifact_counts.get((layer, table), 0)
            if actual < min_count:
                errors.append(
                    f"Phase {phase_letter} requires at least {min_count} "
                    f"{table} ({layer} layer), found {actual}"
                )

        # Check advisory artifacts
        for layer, table, min_count in gate.get("advisory", []):
            actual = artifact_counts.get((layer, table), 0)
            if actual < min_count:
                warnings.append(
                    f"Phase {phase_letter} recommends at least {min_count} "
                    f"{table} ({layer} layer), found {actual}"
                )

        # Check special conditions
        for check_name in gate.get("checks", []):
            if check_name == "arb_readiness":
                readiness = self.arb_readiness
                if not readiness["can_submit"]:
                    failed = [c["label"] for c in readiness["checks"]
                              if c["required"] and not c["passed"]]
                    errors.append(
                        f"ARB readiness not met: {', '.join(failed)}"
                    )
            elif check_name == "has_metrics":
                try:
                    from app.models.solution_lifecycle_models import SolutionMetric
                    metric_count = SolutionMetric.query.filter_by(
                        solution_id=self.id
                    ).count()
                    if metric_count == 0:
                        warnings.append(
                            "Phase H recommends at least 1 success metric for value realization"
                        )
                except Exception:  # fabricated-ok: guarded skip on error; emits no fabricated value
                    pass
        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
        }

    def complete_adm_phase(self, phase_letter, force=False):
        """
        Mark an ADM phase as completed and advance to next.

        Validates phase-gate requirements first. Returns validation result.
        Set force=True to skip validation (backward compat).
        """
        from datetime import datetime as dt

        phase_letter = phase_letter.upper()
        validation = self.validate_phase_gate(phase_letter)

        if not force and not validation["valid"]:
            return validation

        field = f"adm_phase_{phase_letter.lower()}_completed_at"
        if hasattr(self, field):
            setattr(self, field, dt.utcnow())
        phases = "ABCDEFGH"
        idx = phases.index(phase_letter)
        if idx < len(phases) - 1:
            self.adm_phase = phases[idx + 1]

        validation["completed"] = True
        return validation

    @property
    def arb_readiness(self):
        """Compute ARB readiness checklist from page completeness."""
        checks = []

        checks.append({
            "label": "Problem statement defined",
            "passed": bool(self.description and len(self.description) > 20),
            "required": True,
        })
        checks.append({
            "label": "Solution owner assigned",
            "passed": bool(self.solution_owner),
            "required": True,
        })
        checks.append({
            "label": "Business sponsor assigned",
            "passed": bool(self.business_sponsor),
            "required": True,
        })
        checks.append({
            "label": "Technical lead assigned",
            "passed": bool(self.technical_lead),
            "required": True,
        })

        has_capabilities = False
        has_recommendations = False
        has_risks = False
        has_drivers_goals = False

        if self.analysis_session_id:
            try:
                from app.models.solution_architect_models import (
                    SolutionRecommendation,
                    SolutionDriver, SolutionGoal,
                )
                session_obj = self.analysis_session
                if session_obj and session_obj.problem_definition:
                    problem_id = session_obj.problem_definition.id
                    has_drivers_goals = (
                        SolutionDriver.query.filter_by(problem_id=problem_id).count() > 0
                        or SolutionGoal.query.filter_by(problem_id=problem_id).count() > 0
                    )
                has_recommendations = SolutionRecommendation.query.filter_by(
                    session_id=self.analysis_session_id
                ).count() > 0
            except Exception:  # fabricated-ok: on error the readiness flag stays False, rendering the check as not-passed; invents no value
                import logging
                logging.getLogger(__name__).debug("Could not check analysis data for readiness")

            try:
                from app.models.solution_models import SolutionCapabilityMapping
                session_obj = self.analysis_session
                if session_obj and session_obj.problem_definition:
                    problem_id = session_obj.problem_definition.id
                    has_capabilities = SolutionCapabilityMapping.query.filter_by(
                        problem_id=problem_id
                    ).count() > 0
                # Also check direct solution_id path
                if not has_capabilities:
                    has_capabilities = SolutionCapabilityMapping.query.filter_by(
                        solution_id=self.id
                    ).count() > 0
            except Exception:  # fabricated-ok: on error the readiness flag stays False, rendering the check as not-passed; invents no value
                import logging
                logging.getLogger(__name__).debug("Could not check capabilities for readiness")

        try:
            from app.services import risk_service
            canonical_risks, legacy_risks = risk_service.solution_risk_sources(self.id)
            has_risks = bool(canonical_risks or legacy_risks)
        except Exception:  # fabricated-ok: on error the readiness flag stays False, rendering the check as not-passed; invents no value
            import logging
            logging.getLogger(__name__).debug("Could not check risks for readiness")

        has_architecture = False
        try:
            has_architecture = self.archimate_elements.count() > 0
        except Exception:  # fabricated-ok: on error the readiness flag stays False, rendering the check as not-passed; invents no value
            import logging
            logging.getLogger(__name__).debug("Could not check architecture elements for readiness")

        checks.append({
            "label": "Business capabilities mapped",
            "passed": has_capabilities,
            "required": True,
        })
        checks.append({
            "label": "Solution option recommended",
            "passed": has_recommendations,
            "required": True,
        })
        checks.append({
            "label": "Business drivers or goals defined",
            "passed": has_drivers_goals,
            "required": True,
        })
        checks.append({
            "label": "Risks identified",
            "passed": has_risks,
            "required": True,
        })
        checks.append({
            "label": "Architecture scope defined",
            "passed": has_architecture,
            "required": True,
        })
        checks.append({
            "label": "Security lead assigned",
            "passed": bool(getattr(self, 'security_lead', None)),
            "required": False,
        })

        # CAP-018: Capability domain coverage check
        capability_domain_passed = False
        capability_domain_msg = None
        try:
            from app.models.business_capabilities import BusinessCapability
            from app.models.solution_models import SolutionCapabilityMapping as SCM
            # Gather all capability IDs from both solution_id and problem_id paths
            all_cap_ids_domain = set()
            direct = SCM.query.filter_by(
                solution_id=self.id
            ).all()
            for m in direct:
                all_cap_ids_domain.add(m.capability_id)
            if self.analysis_session_id:
                try:
                    session_obj = self.analysis_session
                    if session_obj and session_obj.problem_definition:
                        problem_maps = SCM.query.filter_by(
                            problem_id=session_obj.problem_definition.id
                        ).all()
                        for m in problem_maps:
                            all_cap_ids_domain.add(m.capability_id)
                except Exception:  # fabricated-ok: guarded skip on error; emits no fabricated value
                    pass

            if all_cap_ids_domain and self.business_domain:
                matching = BusinessCapability.query.filter(
                    BusinessCapability.id.in_(list(all_cap_ids_domain)),
                    BusinessCapability.business_domain == self.business_domain,
                ).count()
                capability_domain_passed = matching > 0
                if not capability_domain_passed:
                    capability_domain_msg = (
                        f"No mapped capabilities match the solution's "
                        f"business domain ({self.business_domain})."
                    )
            elif not all_cap_ids_domain:
                capability_domain_passed = False
                capability_domain_msg = (
                    "No capability mappings found for this solution."
                )
            elif not self.business_domain:
                capability_domain_passed = False
                capability_domain_msg = (
                    "Solution has no business domain set."
                )
        except Exception:  # fabricated-ok: on error the check is marked not-passed with an explicit 'could not evaluate' message; invents no value
            import logging
            logging.getLogger(__name__).debug(
                "Could not check capability domain coverage for readiness"
            )
            capability_domain_passed = False
            capability_domain_msg = "Could not evaluate capability domain coverage."

        checks.append({
            "label": "Capability domain coverage",
            "passed": capability_domain_passed,
            "required": True,
            "message": capability_domain_msg,
        })

        # CAP-018: No critical capability maturity gaps check
        no_critical_gaps_passed = True
        no_critical_gaps_msg = None
        try:
            from app.models.business_capabilities import BusinessCapability
            from app.models.solution_models import SolutionCapabilityMapping as SCM2
            # Gather all capability IDs from mappings (solution_id or problem_id)
            all_cap_ids = set()
            direct_mappings = SCM2.query.filter_by(
                solution_id=self.id
            ).all()
            for m in direct_mappings:
                all_cap_ids.add(m.capability_id)
            if self.analysis_session_id:
                try:
                    session_obj = self.analysis_session
                    if session_obj and session_obj.problem_definition:
                        problem_mappings = SCM2.query.filter_by(
                            problem_id=session_obj.problem_definition.id
                        ).all()
                        for m in problem_mappings:
                            all_cap_ids.add(m.capability_id)
                except Exception:  # fabricated-ok: guarded skip on error; emits no fabricated value
                    pass

            if all_cap_ids:
                critical_with_gap = BusinessCapability.query.filter(
                    BusinessCapability.id.in_(list(all_cap_ids)),
                    BusinessCapability.strategic_importance == "critical",
                    BusinessCapability.current_maturity_level.isnot(None),
                    BusinessCapability.target_maturity_level.isnot(None),
                ).all()
                gap_count = 0
                for cap in critical_with_gap:
                    gap = (cap.target_maturity_level or 0) - (cap.current_maturity_level or 0)
                    if gap > 2:
                        gap_count += 1
                if gap_count > 0:
                    no_critical_gaps_passed = False
                    no_critical_gaps_msg = (
                        f"{gap_count} critical capabilities have unresolved maturity gaps."
                    )
        except Exception:  # fabricated-ok: on error the check is marked not-passed with an explicit message; a pass is never shown unverified
            import logging
            logging.getLogger(__name__).debug(
                "Could not check critical capability gaps for readiness"
            )
            no_critical_gaps_passed = False
            no_critical_gaps_msg = "Could not evaluate critical capability gaps."

        checks.append({
            "label": "No critical capability gaps",
            "passed": no_critical_gaps_passed,
            "required": True,
            "message": no_critical_gaps_msg,
        })

        mandatory_checks = [c for c in checks if c["required"]]
        total = len(checks)
        passed = sum(1 for c in checks if c["passed"])
        max_score = len(mandatory_checks)
        score = sum(1 for c in mandatory_checks if c["passed"])
        is_ready = all(c["passed"] for c in mandatory_checks)
        percentage = round((score / max_score * 100) if max_score > 0 else 0.0, 1)

        return {
            "checks": checks,
            "total": total,
            "passed": passed,
            "can_submit": is_ready,
            "score": score,
            "max_score": max_score,
            "is_ready": is_ready,
            "percentage": percentage,
        }

    @property
    def architecture_completeness_score(self):
        """PLT-001: Compute architecture completeness across 14 junction tables.

        Each junction with >0 rows scores 1 point. Score = filled/14 * 100.
        Returns dict with score (int 0-100), filled (list), missing (list).
        """
        import logging
        logger = logging.getLogger(__name__)
        from app.models.solution_archimate_element import SolutionArchiMateElement as SAE
        from app.models.solution_architect_models import (
            SolutionConstraint,
            SolutionDriver,
            SolutionGoal,
            SolutionRecommendation,
            SolutionRequirement,
        )
        from app.models.solution_lifecycle_models import (
            SolutionMetric,
            SolutionPlateau,
            SolutionRisk,
            SolutionTCOItem,
        )
        from app.models.solution_sad_models import SolutionAPQCProcess

        junction_checks = [
            ("drivers", SolutionDriver, "problem_id"),
            ("goals", SolutionGoal, "problem_id"),
            ("constraints", SolutionConstraint, "problem_id"),
            ("requirements", SolutionRequirement, "problem_id"),
            ("applications", None, "solution_applications"),
            ("archimate_elements", SAE, "solution_id"),
            ("vendor_products", None, "solution_vendor_products"),
            ("recommendations", SolutionRecommendation, "session_id"),
            ("risks", SolutionRisk, "solution_id"),
            ("tco_items", SolutionTCOItem, "solution_id"),
            ("metrics", SolutionMetric, "solution_id"),
            ("plateaus", SolutionPlateau, "solution_id"),
            ("apqc_processes", SolutionAPQCProcess, "solution_id"),
            ("capability_mappings", SolutionCapabilityMapping, "solution_id"),
        ]

        filled = []
        missing = []

        # Get problem_id and session_id for analysis-linked junctions
        problem_id = None
        session_id = self.analysis_session_id
        if session_id:
            try:
                sess = self.analysis_session
                if sess and sess.problem_definition:
                    problem_id = sess.problem_definition.id
            except Exception:  # fabricated-ok: guarded skip on error; emits no fabricated value
                logger.debug("Could not resolve analysis session for completeness score")

        for name, model, fk_field in junction_checks:
            has_rows = False
            try:
                if name == "applications":
                    has_rows = self.applications.count() > 0
                elif name == "vendor_products":
                    has_rows = self.vendor_products.count() > 0
                elif fk_field == "problem_id":
                    if problem_id and model:
                        has_rows = model.query.filter_by(problem_id=problem_id).count() > 0
                elif fk_field == "session_id":
                    if session_id and model:
                        has_rows = model.query.filter_by(session_id=session_id).count() > 0
                elif model:
                    has_rows = model.query.filter_by(solution_id=self.id).count() > 0
            except Exception:  # fabricated-ok: guarded skip on error; emits no fabricated value
                logger.debug("Could not check junction %s for completeness", name)

            if has_rows:
                filled.append(name)
            else:
                missing.append(name)

        total = len(junction_checks)
        score = round(len(filled) / total * 100) if total > 0 else 0

        return {
            "score": score,
            "filled": filled,
            "missing": missing,
            "filled_count": len(filled),
            "total": total,
        }

    def __repr__(self):
        return f"<Solution {self.name} ({self.status})>"


class SolutionPattern(TenantMixin, db.Model):
    """
    ArchiMate-inspired Solution Pattern model.

    Represents reusable solution templates and architectural patterns
    that can be applied to solve common business problems.

    Examples:
    - "Microservices Pattern" for scalable application architecture
    - "Event-Driven Architecture Pattern" for real-time systems
    - "Data Lakehouse Pattern" for analytics platforms
    """

    __tablename__ = "solution_patterns"
    __table_args__ = {"extend_existing": True}

    id = Column(db.Integer, primary_key=True)
    name = Column(db.String(255), nullable=False, index=True)
    description = Column(db.Text)

    # ArchiMate linkage
    archimate_element_id = Column(db.Integer, db.ForeignKey("archimate_elements.id"))

    # Pattern characteristics
    pattern_category = Column(db.String(50))  # Architectural, Integration, Data, Security
    pattern_type = Column(db.String(50))  # Microservices, Event-Driven, Layered, Hexagonal
    complexity_level = Column(db.String(20))  # Simple, Moderate, Complex

    # Applicability
    business_domains = Column(db.JSON)  # List of applicable business domains
    use_cases = Column(db.JSON)  # Common use cases for this pattern
    anti_patterns = Column(db.JSON)  # Situations where this pattern should NOT be used

    # Technical specifications
    technology_stack = Column(db.JSON)  # Recommended technologies and frameworks
    integration_points = Column(db.JSON)  # Key integration requirements
    scalability_characteristics = Column(db.JSON)  # Scaling capabilities

    # Implementation guidance
    implementation_steps = Column(db.JSON)  # Step-by-step implementation guide
    best_practices = Column(db.JSON)  # Recommended practices
    common_pitfalls = Column(db.JSON)  # Common implementation mistakes to avoid

    # Quality attributes
    performance_characteristics = Column(db.JSON)  # Performance expectations
    security_considerations = Column(db.JSON)  # Security implications
    maintainability_score = Column(db.Integer)  # 1 - 10 maintainability rating

    # Governance
    pattern_owner = Column(db.String(255))
    approval_status = Column(db.String(30), default="draft")  # draft, approved, deprecated
    version = Column(db.String(20))

    # Usage tracking
    usage_count = Column(db.Integer, default=0)
    success_rate = Column(db.Float)  # Percentage of successful implementations
    last_used_date = Column(db.Date)

    # Metadata
    created_at = Column(db.DateTime, default=datetime.utcnow)
    updated_at = Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by_id = Column(db.Integer, db.ForeignKey("users.id"))

    # Relationships
    archimate_element = db.relationship("ArchiMateElement", foreign_keys=[archimate_element_id])
    created_by = db.relationship("User", backref="created_solution_patterns")

    def __repr__(self):
        return f"<SolutionPattern {self.name} ({self.pattern_category})>"


class SolutionContract(TenantMixin, db.Model):
    """
    Solution Contract model for business agreements, SLAs, and vendor contracts.

    Represents formal agreements that govern solution delivery,
    service levels, and vendor relationships.

    Examples:
    - "SaaS Agreement" for cloud software services
    - "SLA Agreement" for service level guarantees
    - "Vendor Contract" for product licensing and support
    """

    __tablename__ = "solution_contracts_model"

    id = Column(db.Integer, primary_key=True)
    name = Column(db.String(255), nullable=False, index=True)
    description = Column(db.Text)

    # ArchiMate linkage
    archimate_element_id = Column(db.Integer, db.ForeignKey("archimate_elements.id"))

    # Contract characteristics
    contract_type = Column(db.String(50))  # SaaS, License, Service, Support, Partnership
    contract_category = Column(db.String(50))  # Commercial, Legal, Technical, Service

    # Contract details
    vendor_organization = Column(db.String(255))  # Vendor or partner name
    contract_number = Column(db.String(100))  # Contract reference number
    contract_value = Column(db.Numeric(15, 2))  # Total contract value
    currency = Column(db.String(10))

    # Timeline
    start_date = Column(db.Date)
    end_date = Column(db.Date)
    renewal_terms = Column(db.Text)
    termination_notice_days = Column(db.Integer)

    # Service Level Agreement (SLA) terms
    sla_uptime_percentage = Column(db.Float)  # 99.9%, 99.99%, etc.
    sla_response_time_hours = Column(db.Integer)  # Response time SLA
    sla_resolution_time_hours = Column(db.Integer)  # Resolution time SLA
    penalty_clauses = Column(db.Text)  # Financial penalties for non-compliance

    # Technical specifications
    supported_services = Column(db.JSON)  # List of covered services
    service_hours = Column(db.String(100))  # 24x7, Business hours, etc.
    support_channels = Column(db.JSON)  # Phone, email, chat, etc.

    # Governance and compliance
    compliance_requirements = Column(db.JSON)  # GDPR, SOX, HIPAA, etc.
    audit_requirements = Column(db.Text)  # Audit and reporting requirements
    data_location_requirements = Column(db.String(255))  # Data residency requirements

    # Contract status
    status = Column(db.String(30), default="draft")  # draft, active, expired, terminated
    auto_renewal = Column(db.Boolean, default=False)

    # Financial terms
    billing_frequency = Column(db.String(30))  # Monthly, Quarterly, Annually
    payment_terms = Column(db.String(100))  # Net 30, Net 60, etc.
    price_adjustment_terms = Column(db.Text)  # Escalation clauses, etc.

    # Stakeholders
    contract_owner = Column(db.String(255))
    legal_approver = Column(db.String(255))
    technical_approver = Column(db.String(255))

    # Metadata
    created_at = Column(db.DateTime, default=datetime.utcnow)
    updated_at = Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by_id = Column(db.Integer, db.ForeignKey("users.id"))

    # Relationships
    archimate_element = db.relationship("ArchiMateElement", foreign_keys=[archimate_element_id])
    created_by = db.relationship("User", backref="created_solution_contracts")

    def __repr__(self):
        return f"<SolutionContract {self.name} ({self.status})>"


# ============================================================================
# JUNCTION TABLES
# ============================================================================

# Solution to ApplicationComponent junction
solution_applications = db.Table(
    "solution_applications",
    db.Column(
        "solution_id",
        db.Integer,
        db.ForeignKey("solutions.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column(
        "application_component_id",
        db.Integer,
        db.ForeignKey("application_components.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column("role", db.String(50)),  # 'primary', 'supporting', 'integrating'
    db.Column("integration_complexity", db.String(30)),  # 'simple', 'moderate', 'complex'
    db.Column("created_at", db.DateTime, default=datetime.utcnow),
)

# Solution to VendorProduct junction
solution_vendor_products = db.Table(
    "solution_vendor_products",
    db.Column(
        "solution_id",
        db.Integer,
        db.ForeignKey("solutions.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column(
        "vendor_product_id",
        db.Integer,
        db.ForeignKey("vendor_products.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    # The solution's organisation, filled at insert from the solution (the relationship inserts only
    # the two keys); the fence's migration backfills it and switches row-level security on.
    db.Column(
        "organization_id",
        db.Integer,
        db.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        default=parent_organization_default("solutions", "solution_id"),
    ),
    db.Column("implementation_type", db.String(50)),  # 'licensed', 'customized', 'integrated'
    db.Column("license_count", db.Integer),
    db.Column("created_at", db.DateTime, default=datetime.utcnow),
)

# Solution to ValueStream junction
solution_value_streams = db.Table(
    "solution_value_streams",
    db.Column(
        "solution_id",
        db.Integer,
        db.ForeignKey("solutions.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column(
        "value_stream_id",
        db.Integer,
        db.ForeignKey("value_streams.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column("contribution_level", db.String(30)),  # 'enabling', 'supporting', 'transforming'
    db.Column("created_at", db.DateTime, default=datetime.utcnow),
)

# Solution to WorkPackage junction
solution_work_packages = db.Table(
    "solution_work_packages",
    db.Column(
        "solution_id",
        db.Integer,
        db.ForeignKey("solutions.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column(
        "work_package_id",
        db.Integer,
        db.ForeignKey("work_packages.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column("created_at", db.DateTime, default=datetime.utcnow),
)

# Solution to Deliverable junction
solution_deliverables = db.Table(
    "solution_deliverables",
    db.Column(
        "solution_id",
        db.Integer,
        db.ForeignKey("solutions.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column(
        "deliverable_id",
        db.Integer,
        db.ForeignKey("deliverables.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column("created_at", db.DateTime, default=datetime.utcnow),
)

# SolutionPattern to Solution junction
solution_pattern_applications = db.Table(
    "solution_pattern_applications",
    db.Column(
        "solution_pattern_id",
        db.Integer,
        db.ForeignKey("solution_patterns.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column(
        "solution_id",
        db.Integer,
        db.ForeignKey("solutions.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column("adherence_level", db.String(30)),  # 'full', 'partial', 'inspired'
    db.Column("customizations", db.Text),  # How the pattern was customized
    db.Column("created_at", db.DateTime, default=datetime.utcnow),
)

# Solution to Contract junction
solution_contracts = db.Table(
    "solution_contracts",
    db.Column(
        "solution_id",
        db.Integer,
        db.ForeignKey("solutions.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column(
        "contract_id",
        db.Integer,
        db.ForeignKey("solution_contracts_model.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column("relationship_type", db.String(50)),  # 'governing', 'supporting', 'related'
    db.Column("created_at", db.DateTime, default=datetime.utcnow),
)

# Contract to VendorProduct junction
contract_vendor_products = db.Table(
    "contract_vendor_products",
    db.Column(
        "contract_id",
        db.Integer,
        db.ForeignKey("solution_contracts_model.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column(
        "vendor_product_id",
        db.Integer,
        db.ForeignKey("vendor_products.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column("license_terms", db.Text),
    db.Column("support_level", db.String(50)),
    db.Column("created_at", db.DateTime, default=datetime.utcnow),
)


# ============================================================================
# SOLUTION CAPABILITY AND ARCHIMATE MAPPINGS
# ============================================================================


class SolutionCapabilityMapping(TenantMixin, db.Model):
    """
    Junction table for mapping problem definitions to business capabilities.
    DB column is problem_id (FK to solution_problem_definitions).
    """

    __tablename__ = "solution_capability_mappings"
    __tenant_parent__ = (('solution_id', 'solutions'), ('problem_id', 'solution_problem_definitions'),)

    id = Column(db.Integer, primary_key=True)
    problem_id = Column(
        db.Integer, db.ForeignKey("solution_problem_definitions.id", ondelete="CASCADE"), index=True
    )
    # Direct solution link — used when no analysis session exists (capability-based planning path)
    solution_id = Column(
        db.Integer, db.ForeignKey("solutions.id", ondelete="CASCADE"), nullable=True, index=True
    )
    capability_id = Column(
        db.Integer,
        db.ForeignKey("business_capability.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Mapping metadata
    support_level = Column(db.String(20))  # DB uses USER-DEFINED enum
    priority = Column(db.Integer, default=0)
    coverage_percentage = Column(db.Float)
    maturity_current = Column(db.Integer)
    maturity_target = Column(db.Integer)
    notes = Column(db.Text)
    rationale = Column(db.Text)

    # Timestamps
    created_at = Column(db.DateTime, default=datetime.utcnow)
    created_by_id = Column(db.Integer, db.ForeignKey("users.id"))

    # Relationships
    capability = db.relationship(
        "BusinessCapability", backref=db.backref("solution_mappings", lazy="dynamic")
    )
    created_by = db.relationship("User")

    def __repr__(self):
        return (
            f"<SolutionCapabilityMapping problem={self.problem_id} capability={self.capability_id}>"
        )


class SolutionArchiMateElement(TenantMixin, db.Model):
    """
    Junction table for mapping solutions to ArchiMate elements across all 6 layers.
    Uses polymorphic lookup to reference elements from different layer tables.
    """

    __tablename__ = "solution_archimate_elements"
    __tenant_parent__ = (('solution_id', 'solutions'),)

    id = Column(db.Integer, primary_key=True)
    solution_id = Column(
        db.Integer, db.ForeignKey("solutions.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # ArchiMate layer type (nullable for direct FK rows created via solution_archimate_element.py)
    layer_type = Column(
        db.String(30), nullable=True, index=True
    )  # 'motivation', 'strategy', 'business', 'application', 'technology', 'implementation'

    # Polymorphic element reference
    element_id = Column(db.Integer, nullable=False)  # FK to respective layer table
    element_table = Column(
        db.String(100), nullable=True
    )  # Table name for polymorphic lookup (e.g., 'stakeholders', 'business_actors')
    element_name = Column(db.String(255))  # Cached name for display

    # ArchiMate relationship metadata
    relationship_type = Column(
        db.String(50)
    )  # ArchiMate relationship type (e.g., 'realizes', 'serves', 'composes')

    # Additional metadata
    notes = Column(db.Text)
    is_new_element = Column(db.Boolean, default=False)  # Was this created inline?
    # e.g. 'primary', 'supporting', 'impacted', 'ai_derived'
    element_role = Column(db.String(64), nullable=False, default="primary")
    # Structured spec data: fields, api_contract, business_rules, integrations, deployment
    spec_data = Column(db.JSON, nullable=True, default=None)

    # Timestamps
    created_at = Column(db.DateTime, default=datetime.utcnow)
    updated_at = Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by_id = Column(db.Integer, db.ForeignKey("users.id"))

    # Relationships
    solution = db.relationship(
        "Solution",
        backref=db.backref("archimate_elements", lazy="dynamic", cascade="all, delete-orphan"),
    )
    created_by = db.relationship("User")

    __table_args__ = (
        db.UniqueConstraint(
            "solution_id",
            "layer_type",
            "element_table",
            "element_id",
            name="uq_solution_archimate_element",
        ),
        db.Index("ix_solution_archimate_layer", "solution_id", "layer_type"),
    )

    # ArchiMate layer colors for visual notation
    LAYER_COLORS = {
        "motivation": "#B3A2C7",  # Purple
        "strategy": "#F5D742",  # Yellow/Gold
        "business": "#FFFFB5",  # Light Yellow
        "application": "#B5E3FF",  # Light Blue
        "technology": "#C9E6B5",  # Light Green
        "implementation": "#FFB5B5",  # Light Pink
    }

    # ArchiMate layer element tables mapping
    LAYER_TABLES = {
        "motivation": [
            "stakeholders",
            "drivers",
            "assessments",
            "goals",
            "outcomes",
            "constraints",
            "values",
            "meanings",
            "principles",
            "requirements",
        ],
        "strategy": ["resources", "courses_of_action", "value_streams", "capabilities"],
        "business": [
            "business_actors",
            "business_roles",
            "business_processes",
            "business_services",
            "business_collaborations",
            "business_interfaces",
            "business_events",
            "business_functions",
            "business_interactions",
            "contracts",
            "products",
            "representations",
        ],
        "application": [
            "application_components",
            "application_services",
            "application_interfaces",
            "application_collaborations",
            "application_events",
            "application_functions",
            "application_processes",
            "application_interactions",
            "data_objects",
        ],
        "technology": [
            "technology_nodes",
            "technology_devices",
            "technology_system_software",
            "technology_collaborations",
            "technology_interfaces",
            "technology_paths",
            "technology_communication_networks",
            "technology_functions",
            "technology_processes",
            "technology_interactions",
            "technology_events",
            "technology_services",
            "artifacts",
            "materials",
            "distribution_networks",
            "facilities",
            "equipment",
        ],
        "implementation": [
            "work_packages",
            "deliverables",
            "plateaus",
            "gaps",
            "implementation_events",
        ],
    }

    @classmethod
    def get_layer_color(cls, layer_type: str) -> str:
        """Get the ArchiMate color for a layer type."""
        return cls.LAYER_COLORS.get(layer_type, "#CCCCCC")

    def __repr__(self):
        return f"<SolutionArchiMateElement solution={self.solution_id} layer={self.layer_type} table={self.element_table}>"


class SolutionComment(TenantMixin, db.Model):
    """
    Section-level comment threads for solution design collaboration.

    Enables stakeholders to annotate specific sections (Phase A, B-D, E, etc.)
    of a solution design with threaded comments.
    """

    __tablename__ = "solution_comments"
    __tenant_parent__ = (('solution_id', 'solutions'),)

    id = Column(Integer, primary_key=True)
    solution_id = Column(Integer, ForeignKey("solutions.id", ondelete="CASCADE"), nullable=False, index=True)
    section_name = Column(String(50), nullable=False, index=True)
    author_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    author_name = Column(String(255), nullable=False)
    content = Column(Text, nullable=False)
    parent_comment_id = Column(Integer, ForeignKey("solution_comments.id"), nullable=True)  # migration-exempt
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    replies = db.relationship("SolutionComment", backref=db.backref("parent", remote_side="SolutionComment.id"), lazy="dynamic")

    VALID_SECTIONS = [
        "phase_a", "phase_bcd", "phase_e", "risks",
        "phase_h", "tco", "phase_f", "archimate",
        "phase_g", "capabilities", "record",
    ]

    def to_dict(self):
        return {
            "id": self.id,
            "solution_id": self.solution_id,
            "section_name": self.section_name,
            "author_id": self.author_id,
            "author_name": self.author_name,
            "content": self.content,
            "parent_comment_id": self.parent_comment_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self):
        return f"<SolutionComment id={self.id} solution={self.solution_id} section={self.section_name}>"


class SolutionFitGapEntry(TenantMixin, db.Model):
    """
    ERP Fit-Gap Analysis entry for SAP/Microsoft programme governance.

    Replaces Excel fit-gap sheets with a governed, versioned, requirements-traced
    table linked to the Solution Architecture Blueprint.
    """

    __tablename__ = "solution_fit_gap_entries"
    __tenant_parent__ = (('solution_id', 'solutions'),)

    # SAP ACTIVATE fit-gap taxonomy
    FIT_TYPES = ["standard", "configuration", "enhancement", "extension", "custom", "out_of_scope"]
    STATUSES = ["draft", "reviewed", "approved"]

    id = Column(Integer, primary_key=True)
    solution_id = Column(Integer, ForeignKey("solutions.id", ondelete="CASCADE"), nullable=False, index=True)
    business_process = Column(String(255), nullable=False)
    erp_module = Column(String(100))
    fit_type = Column(String(50))   # standard|configuration|enhancement|extension|custom|out_of_scope|isv_solution|customization|custom_development
    justification = Column(Text)
    status = Column(String(20), default="draft")   # draft|reviewed|approved
    capability_id = Column(Integer, ForeignKey("capabilities.id", ondelete="SET NULL"), nullable=True)
    requirement_id = Column(Integer, ForeignKey("solution_requirements.id", ondelete="SET NULL"), nullable=True)
    option_id = Column(Integer, ForeignKey("solution_recommendations.id", ondelete="SET NULL"), nullable=True)
    estimated_effort_days = Column(Integer, nullable=True)
    sort_order = Column(Integer, default=0)
    provenance = Column(db.JSON, nullable=True)   # {created_by, created_at, source}
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "solution_id": self.solution_id,
            "business_process": self.business_process,
            "erp_module": self.erp_module,
            "fit_type": self.fit_type,
            "justification": self.justification,
            "status": self.status,
            "capability_id": self.capability_id,
            "requirement_id": self.requirement_id,
            "option_id": self.option_id,
            "estimated_effort_days": self.estimated_effort_days,
            "sort_order": self.sort_order,
            "provenance": self.provenance or {},
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self):
        return f"<SolutionFitGapEntry id={self.id} solution={self.solution_id} fit_type={self.fit_type}>"
