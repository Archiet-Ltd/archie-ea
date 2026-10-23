"""Journey Reasoning Orchestrator — the missing brain.

Chains: Problem -> Capabilities -> Landscape -> Gaps -> Options -> Recommendation
              -> Blueprint Population

This is the single service that turns a business problem description into a
pre-populated Solution Architecture Blueprint by reasoning over the enterprise
graph. Every piece it uses already exists; this service wires them together.

Spec: docs/2026-03-22-zero-to-hero-journey-spec-v2.md
State machine: draft_problem -> capability_discovery -> capability_confirmation
    -> landscape_mapping -> gap_analysis -> option_generation -> recommendation
    -> blueprint_population -> refinement -> ready_for_arb
"""

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from app import db
from app.models.business_capabilities import BusinessCapability
from app.models.solution_models import Solution, SolutionCapabilityMapping

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Journey state machine
# ---------------------------------------------------------------------------

class JourneyState(str, Enum):
    DRAFT_PROBLEM = "draft_problem"
    CAPABILITY_DISCOVERY = "capability_discovery"
    CAPABILITY_CONFIRMATION = "capability_confirmation"
    LANDSCAPE_MAPPING = "landscape_mapping"
    COST_ESTIMATION = "cost_estimation"
    GAP_ANALYSIS = "gap_analysis"
    OPTION_GENERATION = "option_generation"
    RECOMMENDATION = "recommendation"
    BLUEPRINT_POPULATION = "blueprint_population"
    REFINEMENT = "refinement"
    READY_FOR_ARB = "ready_for_arb"


ALLOWED_TRANSITIONS = {
    JourneyState.DRAFT_PROBLEM: [JourneyState.CAPABILITY_DISCOVERY],
    JourneyState.CAPABILITY_DISCOVERY: [JourneyState.CAPABILITY_CONFIRMATION, JourneyState.DRAFT_PROBLEM],
    JourneyState.CAPABILITY_CONFIRMATION: [JourneyState.LANDSCAPE_MAPPING, JourneyState.CAPABILITY_DISCOVERY],
    JourneyState.LANDSCAPE_MAPPING: [JourneyState.COST_ESTIMATION, JourneyState.GAP_ANALYSIS, JourneyState.CAPABILITY_CONFIRMATION],
    JourneyState.COST_ESTIMATION: [JourneyState.GAP_ANALYSIS, JourneyState.LANDSCAPE_MAPPING],
    JourneyState.GAP_ANALYSIS: [JourneyState.OPTION_GENERATION, JourneyState.LANDSCAPE_MAPPING],
    JourneyState.OPTION_GENERATION: [JourneyState.RECOMMENDATION, JourneyState.GAP_ANALYSIS],
    JourneyState.RECOMMENDATION: [JourneyState.BLUEPRINT_POPULATION, JourneyState.OPTION_GENERATION],
    JourneyState.BLUEPRINT_POPULATION: [JourneyState.REFINEMENT],
    JourneyState.REFINEMENT: [JourneyState.READY_FOR_ARB, JourneyState.BLUEPRINT_POPULATION],
    JourneyState.READY_FOR_ARB: [JourneyState.REFINEMENT],
}


# ---------------------------------------------------------------------------
# Data classes for pipeline results
# ---------------------------------------------------------------------------

@dataclass
class DiscoveredCapability:
    """A capability found in the enterprise catalog matching the problem."""
    capability_id: int
    name: str
    description: str
    level: int
    strategic_importance: str
    confidence: float
    rationale: str
    current_maturity: int = 0
    target_maturity: int = 0
    acm_domain: str = "APP"  # One of: UX, APP, DATA, SEC, DEV, AI, COM


@dataclass
class LandscapeApplication:
    """An application serving a discovered capability."""
    app_id: int
    app_name: str
    capability_id: int
    capability_name: str
    lifecycle_status: str
    evidence_level: str  # strong, weak, inferred
    support_level: str
    is_decommissioning: bool = False
    # Vendor product cost data (from vendor_products via app.vendor_product_id)
    vendor_product_id: Optional[int] = None
    vendor_product_name: str = ""
    vendor_name: str = ""
    annual_license_cost: Optional[float] = None
    implementation_cost: Optional[float] = None
    support_cost_pct: Optional[float] = None


@dataclass
class ArchitectureGap:
    """A structural gap detected in the architecture."""
    gap_id: str
    gap_type: str  # capability_not_served, missing_technology, decommission_dependency, integration_risk, security_pii_risk, architecture_inconsistency
    entity_type: str
    entity_id: int
    entity_name: str
    severity: str  # critical, high, medium, low
    rationale: str
    evidence: str
    recommended_mitigation: str


@dataclass
class SolutionOption:
    """A candidate solution option (buy/build/hybrid)."""
    option_id: str
    option_type: str  # buy, build, hybrid
    title: str
    description: str
    cost_estimate: str
    risk_score: str  # low, medium, high
    time_estimate: str
    dependencies: List[str] = field(default_factory=list)
    vendor_alignment: str = ""
    principle_alignment: str = ""


@dataclass
class JourneyAnalysis:
    """Complete output of the reasoning pipeline."""
    solution_id: int
    state: str
    problem_text: str
    capabilities: List[DiscoveredCapability] = field(default_factory=list)
    landscape: List[LandscapeApplication] = field(default_factory=list)
    gaps: List[ArchitectureGap] = field(default_factory=list)
    options: List[SolutionOption] = field(default_factory=list)
    recommendation: Optional[Dict[str, Any]] = None
    completeness: float = 0.0
    errors: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# The Orchestrator
# ---------------------------------------------------------------------------

class JourneyReasoningOrchestrator:
    """Chains the full zero-to-hero pipeline.

    Each method corresponds to a state transition. The orchestrator persists
    state after every step so the journey is resumable.
    """

    # ACM domain keyword sets for capability→domain inference.
    # Applied in _infer_acm_domain() to tag every discovered capability.
    _ACM_DOMAIN_KEYWORDS: Dict[str, List[str]] = {
        "UX":  ["user experience", "frontend", "ui ", "ux", "interface", "portal",
                "web app", "mobile", "accessibility", "responsive", "dashboard",
                "design system", "widget", "form", "screen", "page", "browser"],
        "APP": ["api", "service", "microservice", "backend", "business logic",
                "integration", "middleware", "workflow", "orchestrat", "bpm",
                "application server", "rest", "graphql", "soap", "endpoint"],
        "DATA": ["database", " data ", "storage", "cache", "data lake", "warehouse",
                 "etl", "pipeline", "repository", "persistence", "sql", "nosql",
                 "blob", "object store", "backup", "archiv"],
        "SEC":  ["security", "auth", "identity", "access control", "permission",
                 "compliance", "encrypt", "sso", "oauth", "saml", "rbac", "iam",
                 "certificate", "secret", "vault", "audit log", "gdpr", "pii"],
        "DEV":  ["devops", "ci/cd", "pipeline", "infrastructure", "deployment",
                 "kubernetes", "docker", "container", "terraform", "cloud",
                 "monitoring", "observab", "logging", "alerting", "platform",
                 "helm", "ansible", "build", "release", "environment"],
        "AI":   ["ai", "machine learning", "ml ", "analytics", "intelligence",
                 "prediction", "recommendation", "nlp", "model", "training",
                 "inference", "llm", "data science", "bi ", "reporting", "insight"],
        "COM":  ["messaging", "notification", "email", "chat", "real-time",
                 "websocket", "event", "communication", "collaboration", "sms",
                 "push notification", "pubsub", "queue", "broker", "signalr"],
    }

    _ACM_DOMAIN_NAMES: Dict[str, str] = {
        "UX":  "User Experience",
        "APP": "Application Services",
        "DATA": "Data & Storage",
        "SEC":  "Security & Identity",
        "DEV":  "DevOps & Platform",
        "AI":   "AI & Analytics",
        "COM":  "Communication",
    }

    def __init__(self, solution_id: int):
        self.solution_id = solution_id
        self._solution = None

    @property
    def solution(self) -> Solution:
        if self._solution is None:
            self._solution = Solution.query.get(self.solution_id)
        return self._solution

    def _get_journey_data(self) -> dict:
        """Load persisted journey data from solution.journey_state."""
        return self.solution.journey_state or {}

    def _save_journey_data(self, data: dict):
        """Persist journey data to solution.journey_state."""
        import copy
        from sqlalchemy.orm.attributes import flag_modified
        # Force-refresh from DB to avoid StaleDataError when multiple saves
        # happen in a single method (e.g. _transition save + end-of-method save)
        from sqlalchemy.orm.exc import StaleDataError

        # Keys the wizard's own progress save writes. When another request saved
        # the row between our read and write, those keys belong to the other
        # writer: take the stored values for them and keep this step's results.
        wizard_keys = {
            "schema_version", "current_step", "completed_steps", "enriched_brief",
            "problem_statement", "clarify_phase", "domains_populated",
            "structured_intake", "accepted_elements", "updated_at",
        }
        for attempt in range(3):
            self._solution = None
            db.session.expire_all()
            sol = self.solution  # re-fetches from DB
            to_write = copy.deepcopy(data)
            if attempt:
                stored = sol.journey_state if isinstance(sol.journey_state, dict) else {}
                for key in wizard_keys:
                    if key in stored:
                        to_write[key] = copy.deepcopy(stored[key])
            sol.journey_state = to_write
            flag_modified(sol, "journey_state")
            try:
                db.session.commit()
                break
            except StaleDataError:
                db.session.rollback()
                if attempt == 2:
                    raise
        self._solution = None  # force reload for next access

    def _get_state(self) -> JourneyState:
        data = self._get_journey_data()
        raw = data.get("state", JourneyState.DRAFT_PROBLEM.value)
        try:
            return JourneyState(raw)
        except ValueError:
            return JourneyState.DRAFT_PROBLEM

    def _transition(self, new_state: JourneyState):
        current = self._get_state()
        all_states = list(JourneyState)
        current_idx = all_states.index(current) if current in all_states else 0
        new_idx = all_states.index(new_state) if new_state in all_states else 0

        now_iso = datetime.utcnow().isoformat()

        # Already at or past target — no-op
        if current_idx >= new_idx:
            if current != new_state:
                logger.info(
                    "Allowing backward transition %s -> %s for journey resume (solution %d)",
                    current.value, new_state.value, self.solution_id,
                )
            data = self._get_journey_data()
            data["state"] = new_state.value
            data["last_transition"] = now_iso
            if current != new_state:
                history = data.setdefault("transition_history", [])
                history.append({"from": current.value, "to": new_state.value, "at": now_iso})
            self._save_journey_data(data)
            return

        # Forward transition: auto-advance through intermediate states
        # instead of rejecting skips (the pipeline calls steps in sequence
        # but race conditions or retries can cause state to lag behind)
        for i in range(current_idx + 1, new_idx + 1):
            intermediate = all_states[i]
            allowed = ALLOWED_TRANSITIONS.get(
                all_states[i - 1] if i > 0 else current, []
            )
            if intermediate not in allowed:
                logger.warning(
                    "Auto-advancing through %s -> %s (solution %d, target %s)",
                    all_states[i - 1].value, intermediate.value,
                    self.solution_id, new_state.value,
                )

        data = self._get_journey_data()
        history = data.setdefault("transition_history", [])
        history.append({"from": current.value, "to": new_state.value, "at": now_iso})
        data["state"] = new_state.value
        data["last_transition"] = now_iso
        self._save_journey_data(data)
        logger.info(
            "Journey state: %s -> %s (solution %d)",
            current.value, new_state.value, self.solution_id,
        )

    # =========================================================================
    # STAGE 2: CONNECT
    # =========================================================================
    # ACM DOMAIN COVERAGE
    # =========================================================================

    def _infer_acm_domain(self, name: str, description: str = "") -> str:
        """Infer the most likely ACM domain for a capability by keyword scoring.

        Scores the capability name + description against each domain's keyword set.
        Returns the highest-scoring domain code, defaulting to APP when ambiguous.
        This is intentionally lightweight — the goal is coverage signal, not taxonomy.
        """
        text = (name + " " + (description or "")).lower()
        best_domain = "APP"
        best_score = 0
        for domain, keywords in self._ACM_DOMAIN_KEYWORDS.items():
            score = sum(1 for kw in keywords if kw in text)
            if score > best_score:
                best_score = score
                best_domain = domain
        return best_domain

    def compute_acm_coverage(self, confirmed_capabilities: list) -> dict:
        """Compute which of the 7 ACM domains are covered by confirmed capabilities.

        Returns a dict suitable for the frontend coverage panel and the ARB gate check.
        confirmed_capabilities is a list of dicts with 'name', 'description', 'acm_domain'.
        """
        coverage: Dict[str, list] = {code: [] for code in self._ACM_DOMAIN_KEYWORDS}
        for cap in confirmed_capabilities:
            domain = cap.get("acm_domain") or self._infer_acm_domain(
                cap.get("name", ""), cap.get("description", "")
            )
            if domain in coverage:
                coverage[domain].append(cap.get("name", ""))

        covered = [d for d, caps in coverage.items() if caps]
        uncovered = [d for d, caps in coverage.items() if not caps]

        return {
            "by_domain": {
                code: {
                    "name": self._ACM_DOMAIN_NAMES[code],
                    "capabilities": caps,
                    "covered": bool(caps),
                }
                for code, caps in coverage.items()
            },
            "covered_count": len(covered),
            "uncovered_domains": uncovered,
            "coverage_pct": round(len(covered) / 7 * 100),
        }

    def is_ready_for_arb_reasoning(self) -> tuple:
        """Check if the reasoning pipeline has completed enough to submit to ARB.

        Returns (ready: bool, reasons: list[str]).
        Reasons are human-readable, shown to the architect in the 422 response.
        """
        data = self._get_journey_data()
        reasons = []

        if not data.get("confirmed_capabilities"):
            reasons.append("No capabilities confirmed — complete Step 2 first")
        if not data.get("landscape"):
            reasons.append("Application landscape not mapped — Step 3 incomplete")
        if not data.get("gaps") and not data.get("options"):
            reasons.append("Gap analysis not run — Step 4 incomplete")
        if not data.get("recommendation"):
            reasons.append("No architecture recommendation selected — Step 5 incomplete")

        # ACM coverage gate: require >= 4 of 7 domains covered
        acm = self.compute_acm_coverage(data.get("confirmed_capabilities", []))
        covered = acm.get("covered_count", 0)
        if covered < 4:
            missing = ", ".join(acm.get("uncovered_domains", []))
            reasons.append(
                f"Only {covered}/7 ACM domains covered (minimum 4 required). "
                f"Missing: {missing}. Add capabilities or pass override_acm_warning=true."
            )

        return len(reasons) == 0, reasons

    # =========================================================================

    def discover_capabilities(self, problem_text: str) -> List[DiscoveredCapability]:
        """CONNECT-1: Discover capabilities from problem text.

        1. Extract keywords via LLM
        2. Fuzzy + semantic search across BusinessCapability catalog
        3. Return ranked matches with rationale
        4. Confidence threshold >= 0.55
        """
        self._transition(JourneyState.CAPABILITY_DISCOVERY)

        # Save problem text
        data = self._get_journey_data()
        data["problem_text"] = problem_text
        self.solution.description = problem_text
        self._save_journey_data(data)

        # Extract keywords via LLM
        keywords = self._extract_keywords(problem_text)
        logger.info("Extracted %d keywords from problem text", len(keywords))

        # Search catalog
        all_capabilities = BusinessCapability.query.filter(
            BusinessCapability.is_deprecated.is_(False)
        ).all()

        scored = []
        for cap in all_capabilities:
            score, rationale = self._score_capability(cap, keywords, problem_text)
            if score >= 0.55:
                scored.append(DiscoveredCapability(
                    capability_id=cap.id,
                    name=cap.name,
                    description=cap.description or "",
                    level=cap.level or 1,
                    strategic_importance=cap.strategic_importance or "medium",
                    confidence=round(score, 2),
                    rationale=rationale,
                    current_maturity=cap.current_maturity_level or 0,
                    target_maturity=cap.target_maturity_level or 0,
                    acm_domain=self._infer_acm_domain(cap.name, cap.description or ""),
                ))

        # Sort by confidence descending
        scored.sort(key=lambda c: c.confidence, reverse=True)

        # Persist results
        data = self._get_journey_data()
        data["discovered_capabilities"] = [asdict(c) for c in scored]
        self._save_journey_data(data)

        return scored

    def confirm_capabilities(
        self, confirmed_ids: List[int], rejected_ids: List[int] = None
    ) -> List[DiscoveredCapability]:
        """CONNECT-1 completion: User confirms/rejects discovered capabilities.

        Creates SolutionCapabilityMapping junctions for confirmed capabilities.
        """
        self._transition(JourneyState.CAPABILITY_CONFIRMATION)
        rejected_ids = rejected_ids or []

        data = self._get_journey_data()
        discovered = data.get("discovered_capabilities", [])

        confirmed = [c for c in discovered if c["capability_id"] in confirmed_ids]

        # Fallback: if discovered_capabilities was empty (e.g. frontend used
        # derive-capabilities LLM fallback) but confirmed_ids are real catalog
        # IDs, look them up directly from the DB.
        if not confirmed and confirmed_ids:
            for cap_id in confirmed_ids:
                if cap_id is None:
                    continue
                cap = BusinessCapability.query.get(cap_id)
                if cap:
                    confirmed.append({
                        "capability_id": cap.id,
                        "name": cap.name,
                        "description": cap.description or "",
                        "level": cap.level or 1,
                        "strategic_importance": getattr(cap, "strategic_importance", "medium") or "medium",
                        "confidence": 0.7,
                        "rationale": "Confirmed by user",
                        "current_maturity": 0,
                        "target_maturity": 0,
                    })

        # Create junction records
        for cap_data in confirmed:
            existing = SolutionCapabilityMapping.query.filter_by(
                solution_id=self.solution_id,
                capability_id=cap_data["capability_id"],
            ).first()
            if not existing:
                mapping = SolutionCapabilityMapping(
                    solution_id=self.solution_id,
                    capability_id=cap_data["capability_id"],
                    support_level="required",
                    rationale=cap_data.get("rationale", ""),
                    # Seed coverage from confidence score — updated to real % after landscape mapping
                    coverage_percentage=round(cap_data.get("confidence", 0.7) * 100, 1),
                    maturity_current=cap_data.get("current_maturity", 0),
                    maturity_target=cap_data.get("target_maturity", 0),
                )
                db.session.add(mapping)

        db.session.commit()

        # Persist confirmed list + ACM coverage snapshot
        data["confirmed_capabilities"] = confirmed
        data["rejected_capability_ids"] = rejected_ids
        data["acm_coverage"] = self.compute_acm_coverage(confirmed)
        self._save_journey_data(data)

        return [DiscoveredCapability(**c) for c in confirmed]

    def map_landscape(self) -> List[LandscapeApplication]:
        """CONNECT-2: Map the application landscape for confirmed capabilities.

        For each confirmed capability:
        1. Query application_capability_mapping for serving apps
        2. Identify lifecycle status
        3. Tag evidence level (strong/weak/inferred)
        4. Auto-link relevant applications to the solution
        """
        self._transition(JourneyState.LANDSCAPE_MAPPING)

        data = self._get_journey_data()
        confirmed = data.get("confirmed_capabilities", [])
        confirmed_ids = []
        for c in confirmed:
            cid = c.get("capability_id") if isinstance(c, dict) else None
            if cid is not None:
                confirmed_ids.append(cid)
            else:
                logger.warning(
                    "Skipping confirmed capability entry with no capability_id (solution %d): %s",
                    self.solution_id, c,
                )

        if not confirmed_ids:
            logger.warning(
                "map_landscape called with no confirmed capabilities for solution %d",
                self.solution_id,
            )
            data["landscape"] = []
            data["landscape_message"] = "No capabilities confirmed yet. Go back and confirm capabilities first."
            self._save_journey_data(data)
            return []

        # Query application-capability mappings
        # Column names: business_capability_id, application_component_id
        try:
            from app.models.application_capability import ApplicationCapabilityMapping
            from app.models.application_portfolio import ApplicationComponent
        except ImportError as e:
            logger.error(
                "Failed to import required models for landscape mapping (solution %d): %s",
                self.solution_id, e, exc_info=True,
            )
            raise ValueError(f"Landscape mapping unavailable — missing model: {e}")

        try:
            mappings = (
                # tenant-scoping-ok: FK id already org-scoped (application/capability resolved via a TenantMixin model or the current request's own app/solution).
                ApplicationCapabilityMapping.query
                .filter(ApplicationCapabilityMapping.business_capability_id.in_(confirmed_ids))
                .all()
            )
        except Exception as e:
            logger.error(
                "Database query failed for ApplicationCapabilityMapping (solution %d, capability_ids=%s): %s",
                self.solution_id, confirmed_ids, e, exc_info=True,
            )
            raise ValueError("Landscape mapping could not read the application-capability mappings. Try again in a moment.")

        # Fallback: no mappings exist — allow pipeline to continue
        if not mappings:
            logger.info(
                "No ApplicationCapabilityMapping rows found for capabilities %s (solution %d). "
                "Proceeding with empty landscape.",
                confirmed_ids, self.solution_id,
            )
            data["landscape"] = []
            data["landscape_message"] = (
                "No applications mapped to these capabilities yet. "
                "You can proceed to manually add architecture elements, or map applications to "
                "capabilities in the portfolio and re-run this step."
            )
            self._save_journey_data(data)
            return []

        landscape = []
        seen_apps = set()

        for mapping in mappings:
            try:
                app = ApplicationComponent.query.get(mapping.application_component_id)
                if not app:
                    logger.debug(
                        "ApplicationComponent %d not found for mapping (capability %d)",
                        mapping.application_component_id, mapping.business_capability_id,
                    )
                    continue

                cap = BusinessCapability.query.get(mapping.business_capability_id)
                cap_name = cap.name if cap else f"Capability {mapping.business_capability_id}"

                lifecycle = getattr(app, "lifecycle_status", None) or "unknown"
                is_decom = "decommission" in lifecycle.lower() or lifecycle.startswith("5.")

                # Determine evidence level
                coverage = mapping.coverage_percentage or 0
                if coverage >= 70:
                    evidence = "strong"
                elif coverage >= 30:
                    evidence = "weak"
                else:
                    evidence = "inferred"

                # Fetch vendor product cost data if available
                vp_id = getattr(app, "vendor_product_id", None)
                vp_name = ""
                v_name = ""
                annual_cost = None
                impl_cost = None
                supp_pct = None
                if vp_id:
                    try:
                        from sqlalchemy import text as sa_text
                        vp_row = db.session.execute(  # tenant-filtered: scoped via parent FK (vendor_product_id)
                            sa_text(
                                "SELECT vp.name, vp.base_license_cost_annual, "
                                "vp.implementation_cost_estimate, vp.support_cost_percentage, "
                                "vo.name as vendor_name "
                                "FROM vendor_products vp "
                                "LEFT JOIN vendor_organizations vo ON vp.vendor_organization_id = vo.id "
                                "WHERE vp.id = :vpid"
                            ),
                            {"vpid": vp_id},
                        ).fetchone()
                        if vp_row:
                            vp_name = vp_row[0] or ""
                            annual_cost = float(vp_row[1]) if vp_row[1] else None
                            impl_cost = float(vp_row[2]) if vp_row[2] else None
                            supp_pct = float(vp_row[3]) if vp_row[3] else None
                            v_name = vp_row[4] or ""
                    except Exception as e:
                        logger.error(
                            "Vendor product lookup failed for app %d (vp_id=%s, solution %d): %s",
                            app.id, vp_id, self.solution_id, e, exc_info=True,
                        )
                        # Cost fields remain None — response will show N/A

                entry = LandscapeApplication(
                    app_id=app.id,
                    app_name=app.name or f"App {app.id}",
                    capability_id=mapping.business_capability_id,
                    capability_name=cap_name,
                    lifecycle_status=lifecycle,
                    evidence_level=evidence,
                    support_level=mapping.support_level or "unknown",
                    is_decommissioning=is_decom,
                    vendor_product_id=vp_id,
                    vendor_product_name=vp_name,
                    vendor_name=v_name,
                    annual_license_cost=annual_cost,
                    implementation_cost=impl_cost,
                    support_cost_pct=supp_pct,
                )
                # Deduplicate: one entry per app (first capability wins)
                if app.id not in seen_apps:
                    landscape.append(entry)
                    self._link_application(app.id)
                    seen_apps.add(app.id)

            except Exception as e:
                logger.error(
                    "Failed to process mapping (app_component_id=%d, capability_id=%d) for solution %d: %s",
                    mapping.application_component_id, mapping.business_capability_id,
                    self.solution_id, e, exc_info=True,
                )
                # Continue processing remaining mappings
                continue

        # Update coverage_percentage on SolutionCapabilityMapping from real landscape evidence.
        # Count serving apps per capability — more apps = higher coverage confidence.
        cap_app_counts: dict = {}
        for a in landscape:
            cid = a.capability_id
            cap_app_counts[cid] = cap_app_counts.get(cid, 0) + 1

        for cid, app_count in cap_app_counts.items():
            try:
                mapping = SolutionCapabilityMapping.query.filter_by(
                    solution_id=self.solution_id, capability_id=cid
                ).first()
                if mapping:
                    # Evidence-based coverage: 1 app=40%, 2=65%, 3+=85%
                    real_coverage = min(40.0 + (app_count - 1) * 22.5, 85.0)
                    mapping.coverage_percentage = round(real_coverage, 1)
                    db.session.add(mapping)
            except Exception as _e:
                logger.warning("coverage_percentage update failed for cap %d: %s", cid, _e)
        try:
            db.session.commit()
        except Exception as _e:
            logger.warning("coverage_percentage batch commit failed: %s", _e)
            db.session.rollback()

        # Persist
        data["landscape"] = [asdict(a) for a in landscape]
        if not landscape:
            data["landscape_message"] = (
                "Application-capability mappings exist but no valid applications were found. "
                "Check that referenced applications still exist in the portfolio."
            )
        self._save_journey_data(data)

        return landscape

    def run_inference(self) -> dict:
        """CONNECT-3: Run inference engine on solution's element set.

        Returns inference result summary. Max 20 elements, 50 relationships.
        """
        try:
            from app.modules.architecture.services.inference_engine_service import (
                InferenceEngine,
                ExecutionContext,
            )
        except ImportError:
            logger.warning("Inference engine not available")
            return {"available": False, "error": "Inference engine not imported"}

        try:
            engine = InferenceEngine()
            ctx = ExecutionContext(
                architecture_id=self.solution_id,
                dry_run=False,
                skip_semantic_pass=True,  # skip LLM pass for speed
            )

            # Get solution's linked elements as starting points
            from app.models.solution_models import SolutionArchiMateElement
            linked = SolutionArchiMateElement.query.filter_by(
                solution_id=self.solution_id
            ).all()

            if not linked:
                return {"available": True, "elements_created": 0, "relationships_created": 0,
                        "message": "No elements linked to solution yet"}

            total_elements = 0
            total_relationships = 0

            try:
                result = engine.repair(ctx)
                total_elements = len(result.elements_created) if hasattr(result, 'elements_created') else 0
                total_relationships = len(result.relationships_created) if hasattr(result, 'relationships_created') else 0
            except Exception as e:
                logger.warning("Inference repair failed for solution %d: %s", self.solution_id, e)

            # Enforce limits from spec
            summary = {
                "available": True,
                "elements_created": min(total_elements, 20),
                "relationships_created": min(total_relationships, 50),
            }

            data = self._get_journey_data()
            data["inference_result"] = summary
            self._save_journey_data(data)

            return summary

        except Exception as e:
            logger.error("Inference engine error: %s", e)
            return {"available": False, "error": str(e)}

    # =========================================================================
    # STAGE 2.5: COST ESTIMATION
    # =========================================================================

    def estimate_costs(self) -> Dict[str, Any]:
        """COST stage: Estimate costs from vendor products in the landscape.

        For each application in the landscape that has a vendor_product_id:
        1. Look up the vendor product's annual license cost
        2. Calculate: annual_license + (implementation / 5yr amortization) + (annual * support_pct/100)
        3. Aggregate by capability
        4. Produce total estimated annual and 5-year TCO

        For apps WITHOUT vendor product data: flag as "cost unknown" (not fabricated).
        """
        self._transition(JourneyState.COST_ESTIMATION)

        data = self._get_journey_data()
        landscape = data.get("landscape", [])
        data.get("confirmed_capabilities", [])

        cost_by_capability = {}
        total_annual = 0.0
        total_implementation = 0.0
        costed_apps = 0
        uncosted_apps = 0

        for app_entry in landscape:
            cap_name = app_entry.get("capability_name", "Unknown")
            annual = app_entry.get("annual_license_cost")
            impl = app_entry.get("implementation_cost")
            supp_pct = app_entry.get("support_cost_pct") or 0

            if annual is not None:
                support_annual = annual * (supp_pct / 100) if supp_pct else 0
                effective_annual = annual + support_annual
                total_annual += effective_annual
                total_implementation += (impl or 0)
                costed_apps += 1

                if cap_name not in cost_by_capability:
                    cost_by_capability[cap_name] = {
                        "annual_total": 0,
                        "implementation_total": 0,
                        "products": [],
                    }
                cost_by_capability[cap_name]["annual_total"] += effective_annual
                cost_by_capability[cap_name]["implementation_total"] += (impl or 0)
                cost_by_capability[cap_name]["products"].append({
                    "product": app_entry.get("vendor_product_name", ""),
                    "vendor": app_entry.get("vendor_name", ""),
                    "annual_license": annual,
                    "support_annual": round(support_annual, 2),
                    "implementation": impl,
                })
            else:
                uncosted_apps += 1

        # 5-year TCO: implementation + (annual * 5)
        tco_5yr = total_implementation + (total_annual * 5)

        cost_summary = {
            "total_annual_operating": round(total_annual, 2),
            "total_implementation": round(total_implementation, 2),
            "tco_5_year": round(tco_5yr, 2),
            "costed_apps": costed_apps,
            "uncosted_apps": uncosted_apps,
            "cost_coverage_pct": round(
                (costed_apps / (costed_apps + uncosted_apps) * 100)
                if (costed_apps + uncosted_apps) > 0 else 0, 1
            ),
            "by_capability": cost_by_capability,
            "currency": "USD",
        }

        data["cost_summary"] = cost_summary
        self._save_journey_data(data)

        return cost_summary

    # =========================================================================
    # STAGE 3: REASON
    # =========================================================================

    def detect_gaps(self) -> List[ArchitectureGap]:
        """REASON-2: Detect structural gaps in the architecture.

        Gap types: capability_not_served, missing_technology,
        decommission_dependency, integration_risk, security_pii_risk,
        architecture_inconsistency
        """
        self._transition(JourneyState.GAP_ANALYSIS)

        data = self._get_journey_data()
        confirmed = data.get("confirmed_capabilities", [])
        landscape = data.get("landscape", [])

        gaps = []
        gap_counter = 0

        # Build lookup: capability_id -> serving apps
        cap_apps = {}
        for app_entry in landscape:
            cid = app_entry["capability_id"]
            if cid not in cap_apps:
                cap_apps[cid] = []
            cap_apps[cid].append(app_entry)

        for cap in confirmed:
            cid = cap["capability_id"]
            serving = cap_apps.get(cid, [])

            # Gap: capability not served by any application
            if not serving:
                gap_counter += 1
                gaps.append(ArchitectureGap(
                    gap_id=f"GAP-{gap_counter:03d}",
                    gap_type="capability_not_served",
                    entity_type="capability",
                    entity_id=cid,
                    entity_name=cap["name"],
                    severity="high",
                    rationale=f"Capability '{cap['name']}' has no serving application in the enterprise portfolio",
                    evidence=f"0 applications found for capability_id={cid}",
                    recommended_mitigation=f"Identify or procure an application to serve '{cap['name']}'",
                ))

            # Gap: capability served by decommissioning app
            decom_apps = [a for a in serving if a.get("is_decommissioning")]
            if decom_apps:
                for da in decom_apps:
                    gap_counter += 1
                    gaps.append(ArchitectureGap(
                        gap_id=f"GAP-{gap_counter:03d}",
                        gap_type="decommission_dependency",
                        entity_type="application",
                        entity_id=da["app_id"],
                        entity_name=da["app_name"],
                        severity="critical",
                        rationale=(
                            f"'{da['app_name']}' serves capability '{cap['name']}' "
                            f"but has lifecycle status '{da['lifecycle_status']}' (decommissioning)"
                        ),
                        evidence=f"lifecycle_status='{da['lifecycle_status']}' on app_id={da['app_id']}",  # raw-html-ok: internal evidence string field, never rendered as HTML/browser output
                        recommended_mitigation=f"Plan migration from '{da['app_name']}' before decommission date",
                    ))

            # Gap: capability served only by weak/inferred evidence
            strong = [a for a in serving if a.get("evidence_level") == "strong"]
            if serving and not strong:
                gap_counter += 1
                gaps.append(ArchitectureGap(
                    gap_id=f"GAP-{gap_counter:03d}",
                    gap_type="architecture_inconsistency",
                    entity_type="capability",
                    entity_id=cid,
                    entity_name=cap["name"],
                    severity="medium",
                    rationale=f"Capability '{cap['name']}' has only weak/inferred application support",
                    evidence=f"{len(serving)} apps with evidence levels: {[a['evidence_level'] for a in serving]}",
                    recommended_mitigation=f"Validate application coverage for '{cap['name']}' with owners",
                ))

        # Gap: inference engine found missing ArchiMate elements/relationships
        inference = data.get("inference_result", {})
        elements_created = inference.get("elements_created", 0)
        relationships_created = inference.get("relationships_created", 0)
        if inference.get("available") and (elements_created > 0 or relationships_created > 0):
            gap_counter += 1
            gaps.append(ArchitectureGap(
                gap_id=f"GAP-{gap_counter:03d}",
                gap_type="architecture_inconsistency",
                entity_type="solution",
                entity_id=self.solution_id,
                entity_name=f"Solution {self.solution_id}",
                severity="medium",
                rationale=(
                    "ArchiMate inference engine detected incomplete architecture chains "
                    f"and inferred {elements_created} missing elements and "
                    f"{relationships_created} missing relationships"
                ),
                evidence=(
                    f"inference_result: elements_created={elements_created}, "
                    f"relationships_created={relationships_created}"
                ),
                recommended_mitigation=(
                    "Review the ArchiMate element set for this solution. "
                    "Navigate to Architecture > Elements to validate inferred connections."
                ),
            ))

        # Persist
        data["gaps"] = [asdict(g) for g in gaps]
        self._save_journey_data(data)

        return gaps

    def generate_options(self, user_constraints: Dict[str, Any] = None) -> List[SolutionOption]:
        """REASON-3: Generate solution options (buy/build/hybrid).

        Uses LLM to generate options grounded in the gap analysis and landscape.
        """
        self._transition(JourneyState.OPTION_GENERATION)

        data = self._get_journey_data()
        gaps = data.get("gaps", [])
        landscape = data.get("landscape", [])
        problem = data.get("problem_text", "")
        user_constraints = user_constraints or {}

        # Build context for LLM
        cost_summary = data.get("cost_summary", {})
        context = {
            "problem": problem,
            "gaps_count": len(gaps),
            "critical_gaps": [g for g in gaps if g.get("severity") == "critical"],
            "high_gaps": [g for g in gaps if g.get("severity") == "high"],
            "landscape_apps": len(set(a["app_id"] for a in landscape)),
            "decommissioning_apps": [a for a in landscape if a.get("is_decommissioning")],
            "constraints": user_constraints,
            "cost_summary": cost_summary,
        }

        options = self._generate_options_from_context(context)

        data["options"] = [asdict(o) for o in options]
        data["user_constraints"] = user_constraints
        self._save_journey_data(data)

        return options

    def select_recommendation(self, selected_option_id: str) -> Dict[str, Any]:
        """REASON-4: User selects preferred option.

        Records the selection and transitions to blueprint population.
        """
        self._transition(JourneyState.RECOMMENDATION)

        data = self._get_journey_data()
        options = data.get("options", [])

        selected = None
        for opt in options:
            if opt.get("option_id") == selected_option_id:
                selected = opt
                break

        if not selected:
            raise ValueError(f"Option '{selected_option_id}' not found")

        recommendation = {
            "selected_option": selected,
            "selected_at": datetime.utcnow().isoformat(),
            "rationale": f"User selected option: {selected.get('title', selected_option_id)}",
        }

        data["recommendation"] = recommendation
        self._save_journey_data(data)

        return recommendation

    # =========================================================================
    # STAGE 4: PRODUCE
    # =========================================================================

    def populate_blueprint(self) -> Dict[str, Any]:
        """PRODUCE-1: Auto-populate the blueprint from reasoning output.

        Auto-populated sections (per spec):
        - Executive Summary, Problem Statement, Capability Model,
          Application Cooperation, Gap Analysis, Options Analysis,
          Recommendation, Transition Roadmap

        Manual sections (left blank):
        - Budget, Timeline, Stakeholders, NFRs, Security, Data Classification
        """
        self._transition(JourneyState.BLUEPRINT_POPULATION)

        data = self._get_journey_data()
        problem = data.get("problem_text", "")
        capabilities = data.get("confirmed_capabilities", [])
        landscape = data.get("landscape", [])
        gaps = data.get("gaps", [])
        recommendation = data.get("recommendation", {})
        recommendation.get("selected_option", {})

        # Build section narratives from graph data
        narratives = {}

        narratives["executive_summary"] = self._generate_section_narrative(
            "executive_summary", data
        )
        narratives["problem_statement"] = problem
        narratives["capability_model"] = self._generate_section_narrative(
            "capability_model", data
        )
        narratives["application_cooperation"] = self._generate_section_narrative(
            "application_cooperation", data
        )
        narratives["gap_analysis"] = self._generate_section_narrative(
            "gap_analysis", data
        )
        narratives["options_analysis"] = self._generate_section_narrative(
            "options_analysis", data
        )
        narratives["recommendation"] = self._generate_section_narrative(
            "recommendation", data
        )
        narratives["transition_roadmap"] = self._generate_section_narrative(
            "transition_roadmap", data
        )

        # Calculate completeness score per spec:
        # 0.25 * capability_coverage + 0.25 * landscape_coverage
        # + 0.25 * gap_resolution + 0.25 * narrative_completeness
        cap_coverage = min(len(capabilities) / 5, 1.0) if capabilities else 0
        land_coverage = min(len(landscape) / 3, 1.0) if landscape else 0
        # gap_resolution: 0.0 = no gaps identified, 0.25 = gaps identified,
        # 0.5 = options generated for gaps, 0.75 = recommendation selected
        options = data.get("options", [])
        if recommendation:
            gap_resolution = 0.75
        elif options:
            gap_resolution = 0.5
        elif gaps:
            gap_resolution = 0.25
        else:
            gap_resolution = 0.0
        narrative_filled = sum(1 for v in narratives.values() if v and len(str(v)) > 50)
        narrative_completeness = narrative_filled / 8  # 8 auto sections

        completeness = round(
            0.25 * cap_coverage
            + 0.25 * land_coverage
            + 0.25 * gap_resolution
            + 0.25 * narrative_completeness,
            2,
        )

        # Calculate per-section scores (format: {section_id: {"overall": 0-100}})
        # Must match BlueprintCompletenessService.score_all() format
        section_scores = {}
        for section, text in narratives.items():
            if text and len(str(text)) > 50:
                section_scores[section] = {"overall": 80}
            elif text and len(str(text)) > 10:
                section_scores[section] = {"overall": 40}
            else:
                section_scores[section] = {"overall": 0}

        # Persist to solution
        solution = self.solution
        solution.section_narratives = narratives
        solution.section_scores = section_scores
        solution.blueprint_version = (solution.blueprint_version or 0) + 1
        solution.blueprint_updated_at = datetime.utcnow()

        # Create risks from gaps
        self._create_risks_from_gaps(gaps)

        # Create SolutionArchiMateElement junctions from landscape applications
        # This enables the Code Workbench to generate code from the blueprint
        self._create_archimate_elements_from_landscape(landscape)

        db.session.commit()

        data["completeness"] = completeness
        data["blueprint_populated"] = True
        self._save_journey_data(data)

        return {
            "completeness": completeness,
            "sections_populated": len([v for v in narratives.values() if v]),
            "gaps_as_risks": len(gaps),
            "section_scores": section_scores,
        }

    def get_analysis(self) -> JourneyAnalysis:
        """Return the full analysis state for the current solution."""
        data = self._get_journey_data()

        return JourneyAnalysis(
            solution_id=self.solution_id,
            state=data.get("state", JourneyState.DRAFT_PROBLEM.value),
            problem_text=data.get("problem_text", ""),
            capabilities=[DiscoveredCapability(**c) for c in data.get("confirmed_capabilities", [])],
            landscape=[LandscapeApplication(**a) for a in data.get("landscape", [])],
            gaps=[ArchitectureGap(**g) for g in data.get("gaps", [])],
            options=[SolutionOption(**o) for o in data.get("options", [])],
            recommendation=data.get("recommendation"),
            completeness=data.get("completeness", 0.0),
        )

    # =========================================================================
    # FULL PIPELINE (convenience method)
    # =========================================================================

    def run_full_pipeline(
        self, problem_text: str, confirmed_capability_ids: List[int],
        selected_option_id: str, user_constraints: Dict[str, Any] = None,
    ) -> JourneyAnalysis:
        """Run the entire pipeline in one call (for API/testing).

        In the UI, each step is called individually with user confirmation.
        This method chains them for programmatic use.
        """
        self.discover_capabilities(problem_text)
        self.confirm_capabilities(confirmed_capability_ids)
        self.map_landscape()
        self.estimate_costs()
        self.run_inference()
        self.detect_gaps()
        self.generate_options(user_constraints)
        self.select_recommendation(selected_option_id)
        self.populate_blueprint()
        return self.get_analysis()

    # =========================================================================
    # PRIVATE HELPERS
    # =========================================================================

    def _extract_keywords(self, problem_text: str) -> List[str]:
        """Extract capability-relevant keywords from problem text via LLM."""
        try:
            from app.services.llm_service import LLMService
            prompt = f"""Extract business capability keywords from this problem description.
Return ONLY a JSON array of keyword strings. No explanations.

Problem: "{problem_text}"

Example output: ["customer management", "billing", "data integration", "reporting"]
"""
            text = LLMService.generate_from_prompt(prompt)
            # Parse JSON array from response
            import re
            match = re.search(r'\[.*?\]', text, re.DOTALL)
            if match:
                return json.loads(match.group())
            return []
        except Exception as e:
            logger.warning("LLM keyword extraction failed, using simple tokenization: %s", e)
            # Fallback: simple tokenization
            stop_words = {
                "the", "a", "an", "is", "are", "we", "our", "need", "to", "with",
                "and", "or", "for", "in", "on", "of", "that", "this", "it", "by", "from",
                "system", "systems", "current", "new", "legacy", "target", "state", "will",
                "have", "has", "been", "into", "across", "using", "also", "while",
                "reduce", "enable", "ensure", "provide", "support", "must", "should",
                "include", "all", "each", "per", "than", "more", "less", "over", "under",
            }
            words = problem_text.lower().split()
            return [w for w in words if len(w) > 3 and w not in stop_words]

    def _score_capability(
        self, capability: BusinessCapability, keywords: List[str], problem_text: str
    ) -> tuple:
        """Score a capability against keywords and problem text.

        Returns (score, rationale) tuple. Score 0.0-1.0.
        """
        name_lower = (capability.name or "").lower()
        desc_lower = (capability.description or "").lower()
        domain_lower = (capability.business_domain or "").lower()
        category_lower = (capability.category or "").lower()
        problem_lower = problem_text.lower()

        score = 0.0
        matches = []

        for kw in keywords:
            kw_lower = kw.lower()
            if len(kw_lower) < 3:
                continue
            if kw_lower in name_lower:
                score += 0.25
                matches.append(f"'{kw}' in name")
            if kw_lower in desc_lower:
                score += 0.15
                matches.append(f"'{kw}' in description")
            if kw_lower in domain_lower:
                score += 0.2
                matches.append(f"'{kw}' in domain")
            if kw_lower in category_lower:
                score += 0.15
                matches.append(f"'{kw}' in category")

        # Also check if any word from the domain/category appears in problem text
        if domain_lower:
            domain_words = [w for w in domain_lower.split() if len(w) > 3]
            domain_hits = sum(1 for w in domain_words if w in problem_lower)
            if domain_hits > 0:
                score += 0.15 * min(domain_hits, 3)
                matches.append(f"{domain_hits} domain words in problem")

        # Boost for strategic importance
        if capability.strategic_importance == "critical":
            score += 0.1
        elif capability.strategic_importance == "high":
            score += 0.05

        # Cap at 1.0
        score = min(score, 1.0)

        rationale = "; ".join(matches) if matches else "weak match"
        return score, rationale

    def _link_application(self, app_id: int):
        """Link an application to the solution via junction table."""
        try:
            from sqlalchemy import text
            existing = db.session.execute(  # tenant-filtered: scoped via parent FK (solution_id)
                text("SELECT 1 FROM solution_applications WHERE solution_id = :sid AND application_component_id = :aid"),  # tenant-filtered
                {"sid": self.solution_id, "aid": app_id}
            ).first()
            if not existing:
                db.session.execute(  # tenant-filtered: scoped via parent FK (solution_id)
                    text("INSERT INTO solution_applications (solution_id, application_component_id, role, created_at) VALUES (:sid, :aid, 'supporting', NOW())"),  # tenant-filtered
                    {"sid": self.solution_id, "aid": app_id}
                )
        except Exception as e:
            logger.warning("Failed to link app %d to solution %d: %s", app_id, self.solution_id, e)

    def _generate_options_from_context(self, context: Dict) -> List[SolutionOption]:
        """Generate buy/build/hybrid options using LLM grounded in context."""
        try:
            from app.services.llm_service import LLMService

            cost_ctx = context.get("cost_summary", {})
            cost_line = ""
            if cost_ctx.get("tco_5_year"):
                cost_line = (
                    f"\nKNOWN COST DATA (from vendor product catalog):\n"
                    f"- Existing landscape annual operating cost: ${cost_ctx.get('total_annual_operating', 0):,.0f}\n"
                    f"- Existing landscape 5-year TCO: ${cost_ctx.get('tco_5_year', 0):,.0f}\n"
                    f"- Cost data coverage: {cost_ctx.get('cost_coverage_pct', 0)}% of landscape apps\n"
                    "Use these figures as anchors when estimating option costs. "
                    "Express cost_estimate as a dollar range string, e.g. '$500k-$800k' or '$1.2M-$2M'.\n"
                )
            else:
                cost_line = (
                    "\nNo vendor cost data available. "
                    "Express cost_estimate as a dollar range string based on typical industry benchmarks "
                    "for the option type and scope, e.g. '$200k-$400k'.\n"
                )

            prompt = f"""Generate 3 solution options (Buy, Build, Hybrid) for this architecture problem.

PROBLEM: {context.get('problem', 'No problem defined')}

CURRENT LANDSCAPE:
- {context.get('landscape_apps', 0)} applications serve the required capabilities
- {len(context.get('decommissioning_apps', []))} applications are being decommissioned

GAPS:
- {len(context.get('critical_gaps', []))} critical gaps
- {len(context.get('high_gaps', []))} high gaps

CONSTRAINTS: {json.dumps(context.get('constraints', {}))}
{cost_line}
Return ONLY valid JSON with this structure:
{{
  "options": [
    {{
      "option_type": "buy",
      "title": "...",
      "description": "...",
      "cost_estimate": "$Xk-$Yk",
      "risk_score": "low/medium/high",
      "time_estimate": "3-6 months",
      "dependencies": ["..."],
      "vendor_alignment": "...",
      "principle_alignment": "..."
    }}
  ]
}}
"""
            text = LLMService.generate_from_prompt(prompt) or "{}"

            import re
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                parsed = json.loads(match.group())
                raw_options = parsed.get("options", [])
                return [
                    SolutionOption(
                        option_id=f"OPT-{i+1:03d}",
                        option_type=o.get("option_type", "hybrid"),
                        title=o.get("title", f"Option {i+1}"),
                        description=o.get("description", ""),
                        cost_estimate=o.get("cost_estimate", "medium"),
                        risk_score=o.get("risk_score", "medium"),
                        time_estimate=o.get("time_estimate", "6-12 months"),
                        dependencies=o.get("dependencies", []),
                        vendor_alignment=o.get("vendor_alignment", ""),
                        principle_alignment=o.get("principle_alignment", ""),
                    )
                    for i, o in enumerate(raw_options[:3])
                ]
        except Exception as e:
            logger.error("LLM option generation failed: %s", e)

        # Fallback: generate template options without LLM
        return [
            SolutionOption(
                option_id="OPT-001", option_type="buy",
                title="Buy: Commercial off-the-shelf solution",
                description="Procure an existing product from a vendor that addresses the identified gaps.",
                cost_estimate="medium", risk_score="low", time_estimate="3-6 months",
            ),
            SolutionOption(
                option_id="OPT-002", option_type="build",
                title="Build: Custom development",
                description="Build a bespoke solution tailored to exact requirements.",
                cost_estimate="high", risk_score="high", time_estimate="12-18 months",
            ),
            SolutionOption(
                option_id="OPT-003", option_type="hybrid",
                title="Hybrid: Extend existing platform with custom integration",
                description="Extend existing strategic applications with custom integrations to fill gaps.",
                cost_estimate="medium", risk_score="medium", time_estimate="6-12 months",
            ),
        ]

    def _generate_section_narrative(self, section: str, data: dict) -> str:
        """Generate a blueprint section narrative from graph data via LLM.

        The LLM reads the graph context and produces text. It does NOT invent.
        """
        capabilities = data.get("confirmed_capabilities", [])
        landscape = data.get("landscape", [])
        gaps = data.get("gaps", [])
        cost_summary = data.get("cost_summary", {})
        problem = data.get("problem_text", "")
        recommendation = data.get("recommendation", {})
        selected = recommendation.get("selected_option", {})

        # Build section-specific context
        section_contexts = {
            "executive_summary": (
                f"Problem: {problem}\n"
                f"Capabilities required: {len(capabilities)}\n"
                f"Applications in landscape: {len(landscape)}\n"
                f"Gaps identified: {len(gaps)}\n"
                f"Recommendation: {selected.get('title', 'pending')}\n"
                f"Estimated annual operating cost: ${cost_summary.get('total_annual_operating', 0):,.0f}\n"
                f"5-year TCO: ${cost_summary.get('tco_5_year', 0):,.0f}\n"
                f"Cost data coverage: {cost_summary.get('cost_coverage_pct', 0)}%"
            ),
            "capability_model": "\n".join(
                f"- {c['name']} (Level {c.get('level', '?')}, importance: {c.get('strategic_importance', '?')}, confidence: {c.get('confidence', '?')})"
                for c in capabilities
            ),
            "application_cooperation": "\n".join(
                f"- {a['app_name']} serves {a['capability_name']} (lifecycle: {a['lifecycle_status']}, evidence: {a['evidence_level']})"
                for a in landscape
            ),
            "gap_analysis": "\n".join(
                f"- [{g['severity'].upper()}] {g['rationale']}"
                for g in gaps
            ),
            "options_analysis": "\n".join(
                f"- {o.get('title', 'Option')}: cost={o.get('cost_estimate', '?')}, risk={o.get('risk_score', '?')}, time={o.get('time_estimate', '?')}"
                for o in data.get("options", [])
            ),
            "recommendation": (
                f"Selected: {selected.get('title', 'pending')}\n"
                f"Type: {selected.get('option_type', '?')}\n"
                f"Description: {selected.get('description', '')}"
            ),
            "transition_roadmap": (
                f"Selected approach: {selected.get('title', 'pending')}\n"
                f"Timeline: {selected.get('time_estimate', 'TBD')}\n"
                f"Critical gaps to address: {len([g for g in gaps if g.get('severity') == 'critical'])}\n"
                f"High gaps to address: {len([g for g in gaps if g.get('severity') == 'high'])}"
            ),
        }

        context_text = section_contexts.get(section, "")
        if not context_text:
            return ""

        try:
            from app.services.llm_service import LLMService

            prompt = f"""Write the '{section.replace('_', ' ').title()}' section of a Solution Architecture Document.

USE ONLY THE FOLLOWING DATA. Do not invent any facts, entities, or numbers.

{context_text}

Write 2-4 paragraphs. Be specific. Reference the actual entities listed above.
Do not use placeholder text or generic language.
"""
            return LLMService.generate_from_prompt(prompt) or context_text
        except Exception as e:
            logger.warning("LLM narrative generation failed for %s: %s", section, e)
            # Fallback: return raw context as narrative
            return context_text

    def _create_archimate_elements_from_landscape(self, landscape: List[dict]):
        """Create SolutionArchiMateElement junctions from landscape applications.

        This bridges the reasoning pipeline output to the Code Workbench input.
        The codegen system reads from SolutionArchiMateElement to generate code.
        """
        try:
            from app.models.solution_models import SolutionArchiMateElement
            from app.models.application_portfolio import ApplicationComponent

            for app_entry in landscape:
                app_id = app_entry.get("app_id")
                if not app_id:
                    continue

                # Check if app has an archimate_element_id
                app = ApplicationComponent.query.get(app_id)
                if not app:
                    continue

                archimate_id = getattr(app, "archimate_element_id", None)
                if not archimate_id:
                    # Try to find or create an ArchiMate element for this app
                    from app.models.archimate_core import ArchiMateElement
                    existing = ArchiMateElement.query.filter_by(
                        name=app.name, element_type="ApplicationComponent"
                    ).first()
                    if existing:
                        archimate_id = existing.id
                    else:
                        # Create a new ArchiMate element for this application
                        new_elem = ArchiMateElement(
                            name=app.name or f"App {app_id}",
                            element_type="ApplicationComponent",
                            layer="application",
                            description=f"Application component derived from enterprise portfolio (app_id={app_id})",
                        )
                        db.session.add(new_elem)
                        db.session.flush()
                        archimate_id = new_elem.id

                # Create junction if not exists
                existing_junction = SolutionArchiMateElement.query.filter_by(
                    solution_id=self.solution_id,
                    element_id=archimate_id,
                ).first()
                if not existing_junction:
                    junction = SolutionArchiMateElement(
                        solution_id=self.solution_id,
                        element_id=archimate_id,
                        layer_type="application",
                        element_table="application_components",
                        element_name=app.name or f"App {app_id}",
                    )
                    db.session.add(junction)

        except Exception as e:
            logger.warning("Failed to create ArchiMate elements from landscape: %s", e)

        # Also link DataObject elements related to the confirmed capabilities
        # The Code Workbench needs DataObjects for class diagram generation
        try:
            from sqlalchemy import text as sa_text

            confirmed = self._get_journey_data().get("confirmed_capabilities", [])
            if confirmed:
                # Build keyword list from capability names
                keywords = set()
                for cap in confirmed:
                    for word in (cap.get("name", "") or "").lower().split():
                        if len(word) > 3 and word not in {"manage", "with", "from", "support", "service"}:
                            keywords.add(word)

                if keywords:
                    # Find DataObjects matching any keyword
                    conditions = " OR ".join([f"ae.name ILIKE :kw{i}" for i in range(len(keywords))])
                    params = {f"kw{i}": f"%{kw}%" for i, kw in enumerate(keywords)}
                    params["sid"] = self.solution_id

                    data_objects = db.session.execute(  # tenant-filtered: scoped via parent FK (solution_id)
                        sa_text(
                            f"SELECT ae.id, ae.name FROM archimate_elements ae "
                            f"WHERE ae.type = 'DataObject' AND ({conditions}) "
                            f"AND ae.id NOT IN (SELECT element_id FROM solution_archimate_elements WHERE solution_id = :sid)"
                        ),
                        params,
                    ).fetchall()

                    for do_id, do_name in data_objects[:15]:  # limit to 15 DataObjects
                        junction = SolutionArchiMateElement(
                            solution_id=self.solution_id,
                            element_id=do_id,
                            layer_type="application",
                            element_table="archimate_elements",
                            element_name=do_name,
                        )
                        db.session.add(junction)
                        logger.info("Linked DataObject %d (%s) to solution %d", do_id, do_name, self.solution_id)

        except Exception as e:
            logger.warning("Failed to link DataObject elements: %s", e)

    def _create_risks_from_gaps(self, gaps: List[dict]):
        """Create risk records from detected gaps."""
        try:
            from app.models.solution_sad_models import RiskSnapshot
            for gap in gaps:
                # Map severity to probability/impact
                severity_map = {
                    "critical": (5, 5),
                    "high": (4, 4),
                    "medium": (3, 3),
                    "low": (2, 2),
                }
                prob, impact = severity_map.get(gap.get("severity", "medium"), (3, 3))

                risk = RiskSnapshot(
                    solution_id=self.solution_id,
                    name=gap.get("entity_name", "Unknown gap"),
                    category=gap.get("gap_type", "architecture_gap"),
                    description=gap.get("rationale", ""),
                    probability=prob,
                    impact=impact,
                    mitigation_strategy=gap.get("recommended_mitigation", ""),
                    status="identified",
                )
                db.session.add(risk)
        except Exception as e:
            logger.warning("Failed to create risks from gaps: %s", e)
