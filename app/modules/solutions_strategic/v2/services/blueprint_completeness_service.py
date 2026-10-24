"""Blueprint Completeness Service — per-section scoring engine.

Scores each of the 14 blueprint sections based on:
  - Element coverage (40%): required ArchiMate element types present with descriptions
  - Relationship coverage (35%): downstream chain completeness via InferenceEngine
  - Traceability (25%): chain validation errors via InferenceEngine

For spec-enabled sections (application_cooperation, data_information, deployment_view),
two additional dimensions are scored:
  - Spec completeness (20%): all specs confirmed (fields, contracts, rules, deployment)
  - Spec quality (20%): validated, versioned, hashed, described

Gracefully degrades when no architecture model or elements are linked.
"""

import logging
from datetime import datetime

from app import db
from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship, ArchitectureModel
from app.models.archimate_viewpoint import ArchiMateViewpoint
from app.models.solution_archimate_element import SolutionArchiMateElement
from app.models.solution_models import Solution

logger = logging.getLogger(__name__)

# Weights for composite score (standard 3-dimension sections)
WEIGHT_ELEMENT = 0.40
WEIGHT_RELATIONSHIP = 0.35
WEIGHT_TRACEABILITY = 0.25

# Weights for spec-enabled sections (5-dimension scoring)
WEIGHT_ELEMENT_SPEC = 0.25
WEIGHT_RELATIONSHIP_SPEC = 0.20
WEIGHT_TRACEABILITY_SPEC = 0.15
WEIGHT_SPEC_COMPLETENESS = 0.20
WEIGHT_SPEC_QUALITY = 0.20

# Sections that use 5-dimension scoring
SPEC_ENABLED_SECTIONS = {"application_cooperation", "data_information", "deployment_view"}

# Minimum word count for narrative-only sections to score 100%
NARRATIVE_WORD_THRESHOLD = 100

# Human-readable section titles for next-actions output
SECTION_TITLES = {
    "executive_summary": "Executive Summary",
    "vision_motivation": "Vision & Motivation",
    "value_stream_map": "Value Stream Map",
    "business_process_view": "Business Process View",
    "application_cooperation": "Application Co-operation",
    "data_information": "Data & Information",
    "deployment_view": "Deployment View",
    "network_communication": "Network & Communication",
    "gap_analysis": "Gap Analysis",
    "transition_roadmap": "Transition Roadmap",
    "work_packages": "Work Packages",
    "security_viewpoint": "Security Viewpoint",
    "nfr_satisfaction": "NFR Satisfaction",
    "requirements_traceability": "Requirements Traceability",
    "architecture_decisions": "Architecture Decisions",
    "erp_fit_gap": "ERP Fit-Gap Analysis",
    "integration_architecture": "Integration Architecture",
}


# ──────────────────────────────────────────────────────────────────────────
# Canonical blueprint section catalogue (S-02 / S-03)
#
# This list IS the blueprint document: the template renders one section per
# entry, in this order, and every completeness figure on the page divides by
# `len(BLUEPRINT_SECTIONS)`. Before this list existed, four surfaces each
# carried their own hardcoded copy of the section ids and their own literal
# denominator of 14 — the governance ring, the "N of 14 sections" caption,
# the gap-advisor card and the header strip — so one page load could show
# 25% / 6-of-14 / 13-of-14-need-work / 8-not-started simultaneously, and
# "Architecture Decisions" (section 15, backed and editable) was counted by
# none of them. Add a section here and every surface picks it up; do not
# re-list section ids in a template.
#
# `erp_fit_gap` and `integration_architecture` are scored by the service but
# are NOT part of the blueprint document (no rendered section), so they are
# deliberately absent — a section the reader cannot see must not move a
# number the reader can.
#
# `kind` says how blueprint.html renders it: "narrative" and "matrix" and
# "decisions" are bespoke blocks; "viewpoint" entries are the generic
# diagram+table loop.
# ──────────────────────────────────────────────────────────────────────────
BLUEPRINT_SECTIONS = [
    {"id": "executive_summary", "number": 1, "title": "Executive Summary", "short": "Exec Summary", "viewpoint": None, "kind": "narrative"},
    {"id": "vision_motivation", "number": 2, "title": "Vision & Motivation", "short": "Motivation", "viewpoint": "Motivation Viewpoint", "kind": "viewpoint"},
    {"id": "value_stream_map", "number": 3, "title": "Value Stream Map", "short": "Value Stream", "viewpoint": "Value Stream Viewpoint", "kind": "viewpoint"},
    {"id": "business_process_view", "number": 4, "title": "Business Process View", "short": "Business", "viewpoint": "Business Process Cooperation Viewpoint", "kind": "viewpoint"},
    {"id": "application_cooperation", "number": 5, "title": "Application Cooperation", "short": "Application", "viewpoint": "Application Cooperation Viewpoint", "kind": "viewpoint"},
    {"id": "data_information", "number": 6, "title": "Data & Information", "short": "Data", "viewpoint": "Information Structure Viewpoint", "kind": "viewpoint"},
    {"id": "deployment_view", "number": 7, "title": "Deployment & Migration", "short": "Deployment", "viewpoint": "Deployment Viewpoint", "kind": "viewpoint"},
    {"id": "network_communication", "number": 8, "title": "Network & Communication", "short": "Network", "viewpoint": "Technology Viewpoint", "kind": "viewpoint"},
    {"id": "gap_analysis", "number": 9, "title": "Gap Analysis", "short": "Gaps", "viewpoint": "Gap Analysis", "kind": "viewpoint"},
    {"id": "transition_roadmap", "number": 10, "title": "Transition Roadmap", "short": "Roadmap", "viewpoint": "Migration Viewpoint", "kind": "viewpoint"},
    {"id": "work_packages", "number": 11, "title": "Work Packages", "short": "Work Pkg", "viewpoint": "Implementation & Migration Viewpoint", "kind": "viewpoint"},
    {"id": "security_viewpoint", "number": 12, "title": "Security Viewpoint", "short": "Security", "viewpoint": "Security Viewpoint", "kind": "viewpoint"},
    {"id": "nfr_satisfaction", "number": 13, "title": "NFR Satisfaction", "short": "NFRs", "viewpoint": "Requirements Realization Viewpoint", "kind": "viewpoint"},
    {"id": "requirements_traceability", "number": 14, "title": "Requirements Traceability", "short": "Trace", "viewpoint": "Requirements Realization Viewpoint", "kind": "matrix"},
    {"id": "architecture_decisions", "number": 15, "title": "Architecture Decisions", "short": "Decisions", "viewpoint": None, "kind": "decisions"},
]

BLUEPRINT_SECTION_IDS = [s["id"] for s in BLUEPRINT_SECTIONS]

# A section counts as complete at the same threshold ARB readiness uses, so
# "complete" means the same thing everywhere on the page.
SECTION_COMPLETE_THRESHOLD = 80

# Guidance shown by the gap advisor for a section below the threshold.
SECTION_GAP_HINTS = {
    "executive_summary": "Write the executive summary — aim for 100+ words describing the problem, solution approach, and business value.",
    "vision_motivation": "Add Business Drivers, Goals, and Architecture Principles to anchor the design rationale.",
    "value_stream_map": "Build the strategy chain: Goal → ValueStream (realization) → Capability (association) → Outcome (realization). Stages link via triggering. Without this chain the blueprint has no traceable path from business goal to delivered outcome.",
    "business_process_view": "Document Business Processes, Business Functions, and the actors who perform them.",
    "application_cooperation": "Link Application Components and define integration interfaces and data exchange patterns.",
    "data_information": "Add Data Objects and Data Components; show how information flows and is governed.",
    "deployment_view": "Define Deployment Nodes, System Software, and the infrastructure that runs this solution.",
    "network_communication": "Map Network Components, Communication Paths, and connectivity between deployment nodes.",
    "gap_analysis": "Document current-state vs. target-state gaps — show what is missing and the impact of each gap.",
    "transition_roadmap": "Add Transition States and Plateaus that show how the architecture evolves from now to target.",
    "work_packages": "Define Work Packages with owners, timelines, and cross-package dependencies.",
    "security_viewpoint": "Document security controls, policies, trust boundaries, and threat mitigations.",
    "nfr_satisfaction": "List Non-Functional Requirements (performance, reliability, scalability) and how the design satisfies each.",
    "requirements_traceability": "Link Requirement elements to architecture elements to demonstrate end-to-end traceability.",
    "architecture_decisions": "Record the Architecture Decision Records — the option chosen, the options rejected, and why.",
}


def compute_blueprint_completeness(scores):
    """Derive every completeness figure the blueprint page shows, once.

    Weighting model — chosen explicitly, not emergent:
      * All sections carry EQUAL weight. There is no per-section multiplier;
        a missing Executive Summary costs exactly as much as a missing
        Deployment view.
      * `percent` is the arithmetic mean of the per-section `overall` scores,
        so a partially-filled section earns FRACTIONAL credit (a page of
        50%-filled sections reads 50%, not 0%). It is not the ratio of
        complete sections — that ratio is `sections_complete / total_sections`
        and is reported separately rather than conflated with the dial.
      * A section is `complete` at >= 80 (SECTION_COMPLETE_THRESHOLD, the ARB
        gate), `not_started` at exactly 0, and `partial` in between. The three
        buckets are exhaustive and disjoint, so they always sum to
        `total_sections`, and `sections_needing_work` == partial + not_started.
      * A missing score is 0, never omitted — a section with no data is
        incomplete, not excluded from the denominator.

    Args:
        scores: dict of section_id -> score dict (as produced by score_all).

    Returns:
        dict with total_sections, sections_complete, sections_partial,
        sections_not_started, sections_needing_work, percent, and `sections`
        (per-section rows: id, number, title, short, percent, state, hint).
    """
    scores = scores or {}
    rows = []
    complete = partial = not_started = 0
    total_pct = 0

    for defn in BLUEPRINT_SECTIONS:
        raw = scores.get(defn["id"]) or {}
        pct = raw.get("overall", 0) if isinstance(raw, dict) else 0
        try:
            pct = int(pct or 0)
        except (TypeError, ValueError):
            pct = 0
        total_pct += pct

        if pct >= SECTION_COMPLETE_THRESHOLD:
            state = "complete"
            complete += 1
        elif pct <= 0:
            state = "not_started"
            not_started += 1
        else:
            state = "partial"
            partial += 1

        rows.append({
            "id": defn["id"],
            "number": defn["number"],
            "title": defn["title"],
            "short": defn["short"],
            "viewpoint": defn["viewpoint"],
            "kind": defn["kind"],
            "percent": pct,
            "state": state,
            "hint": SECTION_GAP_HINTS.get(defn["id"], ""),
        })

    total = len(BLUEPRINT_SECTIONS)
    return {
        "total_sections": total,
        "sections_complete": complete,
        "sections_partial": partial,
        "sections_not_started": not_started,
        "sections_needing_work": partial + not_started,
        "percent": int(round(total_pct / total)) if total else 0,
        "threshold": SECTION_COMPLETE_THRESHOLD,
        "sections": rows,
    }


class BlueprintCompletenessService:
    """Scores blueprint sections for a given solution."""

    SECTION_DEFINITIONS = {
        "executive_summary": {
            "viewpoint": None,
            "scoring": "narrative_only",
        },
        "vision_motivation": {
            "viewpoint": "Motivation",
            "required_types": ["Stakeholder", "Driver", "Goal", "Requirement"],
        },
        "value_stream_map": {
            "viewpoint": "Strategy",
            "required_types": ["ValueStream", "Capability", "Outcome"],
            "chain_scoring": "value_stream_capability_outcome",
        },
        "business_process_view": {
            "viewpoint": "Business Process",
            "required_types": ["BusinessProcess", "BusinessActor", "BusinessService"],
        },
        "application_cooperation": {
            "viewpoint": "Application Co-operation",
            "required_types": ["ApplicationComponent", "ApplicationService", "ApplicationInterface"],
        },
        "data_information": {
            "viewpoint": "Information Structure",
            "required_types": ["DataObject", "ApplicationComponent"],
        },
        "deployment_view": {
            "viewpoint": "Deployment",
            "required_types": ["Node", "Device", "SystemSoftware", "ApplicationComponent"],
        },
        "network_communication": {
            "viewpoint": "Infrastructure Usage",
            "required_types": ["CommunicationNetwork", "Path", "Node"],
        },
        "gap_analysis": {
            "viewpoint": "Implementation and Migration",
            "required_types": ["Gap", "Plateau"],
        },
        "transition_roadmap": {
            "viewpoint": "Implementation and Migration",
            "required_types": ["Plateau", "WorkPackage"],
        },
        "work_packages": {
            "viewpoint": "Implementation and Migration",
            "required_types": ["WorkPackage", "Deliverable", "ImplementationEvent"],
        },
        "security_viewpoint": {
            "viewpoint": "Security",
            "required_types": ["Constraint", "Requirement"],
        },
        "nfr_satisfaction": {
            "viewpoint": "Requirements Realization",
            "required_types": ["Requirement"],
        },
        "requirements_traceability": {
            "viewpoint": "Requirements Realization",
            "scoring": "traceability_matrix",
            "required_types": ["Requirement", "WorkPackage"],
        },
        "architecture_decisions": {
            "viewpoint": None,
            "scoring": "narrative_only",
        },
        "erp_fit_gap": {
            "viewpoint": "Implementation and Migration",
            "scoring": "fit_gap_completeness",
            "required_fit_gap_fields": [
                "business_process", "erp_module", "fit_type", "justification", "status"
            ],
        },
        "integration_architecture": {
            "viewpoint": None,
            "scoring": "integration_architecture_completeness",
        },
    }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def score_section(self, solution_id, section_id):
        """Score a single blueprint section.

        Returns dict with keys: element, relationship, traceability, overall
        (each 0-100 integer).
        """
        defn = self.SECTION_DEFINITIONS.get(section_id)
        if not defn:
            logger.warning("Unknown section_id: %s", section_id)
            return {"element": 0, "relationship": 0, "traceability": 0, "overall": 0}

        # Narrative-only sections (e.g. executive_summary)
        if defn.get("scoring") == "narrative_only":
            return self._score_narrative_section(solution_id, section_id)

        if defn.get("scoring") == "fit_gap_completeness":
            return self._score_fit_gap_section(solution_id)

        if defn.get("scoring") == "integration_architecture_completeness":
            return self._score_integration_architecture_section(solution_id)

        # Architecture-backed sections
        elements = self._get_section_elements(solution_id, section_id)
        required_types = defn.get("required_types", [])

        elem_score = round(self._score_element_coverage(elements, required_types))

        # Chain-aware relationship scoring for value_stream_map
        if defn.get("chain_scoring") == "value_stream_capability_outcome":
            rel_score = round(self._score_value_stream_chain(solution_id, elements))
        else:
            rel_score = round(self._score_relationship_coverage(solution_id, elements))
        trace_score = round(self._score_traceability(solution_id, elements))

        if section_id in SPEC_ENABLED_SECTIONS:
            spec_completeness = self._score_spec_completeness(solution_id, section_id)
            spec_quality = self._score_spec_quality(solution_id, section_id)
            overall = round(
                elem_score * WEIGHT_ELEMENT_SPEC
                + rel_score * WEIGHT_RELATIONSHIP_SPEC
                + trace_score * WEIGHT_TRACEABILITY_SPEC
                + spec_completeness * WEIGHT_SPEC_COMPLETENESS
                + spec_quality * WEIGHT_SPEC_QUALITY
            )
            return {
                "element": elem_score,
                "relationship": rel_score,
                "traceability": trace_score,
                "spec_completeness": spec_completeness,
                "spec_quality": spec_quality,
                "overall": overall,
            }

        overall = round(
            elem_score * WEIGHT_ELEMENT
            + rel_score * WEIGHT_RELATIONSHIP
            + trace_score * WEIGHT_TRACEABILITY
        )

        # CAP-027: capability linkage bonus for value_stream_map.
        # An unlinked Capability element scores from element_coverage alone.
        # Properly linked capabilities (SolutionCapabilityMapping rows exist)
        # earn +15 pts — reflecting that the architect has traced business alignment.
        if section_id == "value_stream_map":
            try:
                from app.models.solution_models import SolutionCapabilityMapping
                linked = SolutionCapabilityMapping.query.filter_by(
                    solution_id=solution_id
                ).limit(1).count()
                if linked:
                    overall = min(100, overall + 15)
            except Exception as exc:
                logger.debug("suppressed error in BlueprintCompletenessService.score_section (app/modules/solutions_strategic/v2/services/blueprint_completeness_service.py): %s", exc)

        return {
            "element": elem_score,
            "relationship": rel_score,
            "traceability": trace_score,
            "overall": overall,
        }

    def score_all(self, solution_id):
        """Score all 14 sections and persist to Solution.section_scores.

        Returns dict mapping section_id -> score dict.
        """
        all_scores = {}
        for section_id in self.SECTION_DEFINITIONS:
            all_scores[section_id] = self.score_section(solution_id, section_id)

        # Persist scores on the solution row
        try:
            sol = Solution.query.get(solution_id)
            if sol:
                sol.section_scores = all_scores
                sol.blueprint_updated_at = datetime.utcnow()
                db.session.commit()
        except Exception:
            logger.exception("Failed to persist section_scores for solution %s", solution_id)
            db.session.rollback()

        return all_scores

    def get_next_actions(self, solution_id, precomputed_scores=None):
        """Return top 3 lowest-scoring sections with actionable descriptions.

        Args:
            solution_id: The solution to inspect.
            precomputed_scores: Optional dict of section_id -> score dict.
                If provided, avoids a redundant score_all() call.
        """
        all_scores = precomputed_scores if precomputed_scores else self.score_all(solution_id)

        # Filter to only known, applicable sections
        known_scores = {
            k: v for k, v in all_scores.items()
            if k in self.SECTION_DEFINITIONS
            and not (isinstance(v, dict) and v.get("not_applicable"))
        }
        ranked = sorted(known_scores.items(), key=lambda kv: kv[1].get("overall", 0) if isinstance(kv[1], dict) else 0)
        actions = []
        for section_id, scores in ranked[:3]:
            defn = self.SECTION_DEFINITIONS[section_id]
            title = SECTION_TITLES.get(section_id, section_id)

            # Build actionable description based on weakest dimension
            desc = self._build_action_description(section_id, scores, defn)
            actions.append({
                "section_id": section_id,
                "title": title,
                "description": desc,
                "scores": scores,
            })
        return actions

    def check_arb_ready(self, solution_id, precomputed_scores=None):
        """Check if solution is ARB-ready.

        Requires every section >= 80% overall AND spec confirmation for
        spec-enabled sections.

        Args:
            solution_id: The solution to inspect.
            precomputed_scores: Optional dict of section_id -> score dict.
                If provided, avoids a redundant score_all() call.

        Returns:
            dict with keys: ready (bool), reasons (list of str).
        """
        all_scores = precomputed_scores if precomputed_scores else self.score_all(solution_id)

        is_ready = True
        reasons = []

        for section_id, scores in all_scores.items():
            if section_id not in self.SECTION_DEFINITIONS:
                continue
            if isinstance(scores, dict) and scores.get("not_applicable"):
                continue
            if (scores.get("overall", 0) if isinstance(scores, dict) else 0) < 80:
                title = SECTION_TITLES.get(section_id, section_id)
                reasons.append(f"{title}: overall score {scores['overall']}% (needs 80%)")
                is_ready = False

        # Check spec confirmation for spec-enabled sections
        for section_id in SPEC_ENABLED_SECTIONS:
            scores = all_scores.get(section_id, {})
            if scores.get("spec_completeness", 0) < 100:
                title = SECTION_TITLES.get(section_id, section_id)
                reasons.append(f"{title}: not all specs confirmed")
                is_ready = False

        return {"ready": is_ready, "reasons": reasons}

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _score_fit_gap_section(self, solution_id: int) -> dict:
        """Score the ERP Fit-Gap section.

        Formula: (complete_entries × 15) + (cap_links × 5) + (effort_set × 2), capped at 100.
        A 'complete' entry has all 5 required fields non-empty.
        """
        try:
            from app.models.solution_models import SolutionFitGapEntry
            entries = SolutionFitGapEntry.query.filter_by(solution_id=solution_id).all()
            if not entries:
                return {"element": 0, "relationship": 0, "traceability": 0, "overall": 0, "element_count": 0, "not_applicable": True}
            required_fields = ["business_process", "erp_module", "fit_type", "justification", "status"]
            complete = sum(
                1 for e in entries
                if all(getattr(e, f, None) for f in required_fields)
            )
            cap_links = sum(1 for e in entries if e.capability_id)
            effort_set = sum(1 for e in entries if e.estimated_effort_days is not None)
            score = min(100, (complete * 15) + (cap_links * 5) + (effort_set * 2))
            return {
                "element": score,
                "relationship": min(100, cap_links * 20) if cap_links else 0,
                "traceability": min(100, (complete * 100 // len(entries)) if entries else 0),
                "overall": score,
                "element_count": len(entries),
            }
        except Exception:
            raise  # honest failure: never report a fabricated 0% completeness score on error

    def _score_integration_architecture_section(self, solution_id: int) -> dict:
        """Score the Integration Architecture section.

        Formula:
          coverage (40%): % of flows that have a pattern linked
          compliance (40%): % of pattern-linked flows using approved patterns
          contract_completeness (20%): % of flows whose source_app has an IntegrationContract
        A blocked pattern caps composite at 0.
        """
        try:
            from app.models.solution_sad_models import SolutionIntegrationFlow
            from app.models.integration_contract import IntegrationContract

            flows = SolutionIntegrationFlow.query.filter_by(solution_id=solution_id).all()
            total_flows = len(flows)

            if total_flows == 0:
                return {
                    "score": 0,
                    "score_dimensions": {
                        "coverage": 0,
                        "compliance": 100,
                        "contract_completeness": 0,
                    },
                    "composite_score": 0,
                    "has_blocked_patterns": False,
                    "total_flows": 0,
                    "flows_with_pattern": 0,
                    "approved_flows": 0,
                    "blocked_flows": 0,
                    "element": 0,
                    "relationship": 0,
                    "traceability": 0,
                    "overall": 0,
                    "not_applicable": True,
                }

            flows_with_pattern = 0
            approved_flows = 0
            blocked_flows = 0
            has_blocked = False

            for flow in flows:
                pattern_id = flow.pattern_id
                governance_status = flow.governance_status or 'undocumented'

                if pattern_id:
                    flows_with_pattern += 1
                    if governance_status == 'approved':
                        approved_flows += 1
                    elif governance_status == 'blocked':
                        blocked_flows += 1
                        has_blocked = True

            source_app_ids = [f.source_app_id for f in flows if f.source_app_id]
            contracts_count = IntegrationContract.query.filter(
                IntegrationContract.application_id.in_(source_app_ids)
            ).count() if source_app_ids else 0
            flows_with_contract = min(contracts_count, total_flows)

            coverage = (flows_with_pattern / total_flows * 100) if total_flows else 0
            compliance = (approved_flows / flows_with_pattern * 100) if flows_with_pattern else 100
            contract_completeness = (flows_with_contract / total_flows * 100) if total_flows else 0

            composite = (coverage * 0.4) + (compliance * 0.4) + (contract_completeness * 0.2)
            if has_blocked:
                composite = 0

            score = round(composite)
            return {
                "score": score,
                "score_dimensions": {
                    "coverage": round(coverage),
                    "compliance": round(compliance),
                    "contract_completeness": round(contract_completeness),
                },
                "composite_score": score,
                "has_blocked_patterns": has_blocked,
                "total_flows": total_flows,
                "flows_with_pattern": flows_with_pattern,
                "approved_flows": approved_flows,
                "blocked_flows": blocked_flows,
                "element": score,
                "relationship": round(compliance),
                "traceability": round(contract_completeness),
                "overall": score,
            }
        except Exception:
            logger.exception(
                "Failed to score integration_architecture section for solution %s", solution_id
            )
            return {
                "score": 0,
                "score_dimensions": {"coverage": 0, "compliance": 0, "contract_completeness": 0},
                "composite_score": 0,
                "has_blocked_patterns": False,
                "total_flows": 0,
                "flows_with_pattern": 0,
                "approved_flows": 0,
                "blocked_flows": 0,
                "element": 0,
                "relationship": 0,
                "traceability": 0,
                "overall": 0,
            }

    def _score_narrative_section(self, solution_id, section_id):
        """Score a narrative-only section by word count."""
        sol = Solution.query.get(solution_id)
        if not sol or not sol.section_narratives:
            return {"element": 0, "relationship": 0, "traceability": 0, "overall": 0}

        text = sol.section_narratives.get(section_id, "")
        word_count = len(text.split()) if text else 0
        pct = min(100, round(word_count / NARRATIVE_WORD_THRESHOLD * 100))
        # For narrative sections all sub-scores equal the narrative pct
        return {"element": pct, "relationship": pct, "traceability": pct, "overall": pct}

    def _get_architecture_id(self, solution_id):
        """Bridge from solution_id to architecture_id.

        Strategy:
        1. ArchitectureModel with solution_id FK
        2. Fallback: first element linked via SolutionArchiMateElement junction
        """
        arch = ArchitectureModel.query.filter_by(solution_id=solution_id).first()
        if arch:
            return arch.id

        # Fallback: grab architecture_id from linked elements
        junction = (
            db.session.query(SolutionArchiMateElement.element_id)
            .filter_by(solution_id=solution_id)
            .first()
        )
        if junction:
            elem = ArchiMateElement.query.get(junction.element_id)
            if elem and elem.architecture_id:
                return elem.architecture_id

        return None

    def _get_section_elements(self, solution_id, section_id):
        """Get ArchiMate elements linked to solution, filtered by viewpoint."""
        defn = self.SECTION_DEFINITIONS.get(section_id, {})
        viewpoint_name = defn.get("viewpoint")

        # Get all elements linked to this solution
        element_ids = (
            db.session.query(SolutionArchiMateElement.element_id)
            .filter_by(solution_id=solution_id)
            .all()
        )
        if not element_ids:
            return []

        ids = [eid for (eid,) in element_ids]
        elements = ArchiMateElement.query.filter(ArchiMateElement.id.in_(ids)).all()

        if not viewpoint_name:
            return elements

        # Filter through viewpoint
        viewpoint = ArchiMateViewpoint.query.filter_by(name=viewpoint_name).first()
        if viewpoint:
            return viewpoint.filter_elements(elements)

        # No viewpoint row found — fall back to required_types filter
        required_types = defn.get("required_types", [])
        if required_types:
            return [e for e in elements if e.type in required_types]
        return elements

    def _score_value_stream_chain(self, solution_id, elements):
        """Score the Goal → ValueStream → Capability → Outcome chain.

        The canonical ArchiMate 3.x Strategy chain is:
          Goal  ←─ realizes ─  ValueStream  ─ uses(assoc) ─  Capability
                                   │                              │
                                   └──── stages (ordered) ────────┘
          ValueStream  ─ realization ─  Outcome

        Scoring breakdown (100 pts total):
          35 pts  Goal → ValueStream realization exists
          35 pts  ValueStream → Capability association/serving exists
          30 pts  ValueStream → Outcome realization exists
        """
        if not elements:
            return 0.0

        element_ids = {e.id for e in elements}
        type_index = {}
        for e in elements:
            type_index.setdefault(e.type, []).append(e.id)

        has_vs = bool(type_index.get("ValueStream"))
        has_cap = bool(type_index.get("Capability"))
        has_outcome = bool(type_index.get("Outcome"))

        if not has_vs:
            return 0.0

        # Not guarded: a query failure used to score the chain 0.0, which is
        # exactly the score a solution gets when its Goal → ValueStream →
        # Capability → Outcome chain is genuinely unlinked. The caller
        # (`_score_section`) feeds this straight into a completeness
        # percentage, so the user would act on a number that was never
        # measured. Let it reach the route's error handling instead.
        rels = ArchiMateRelationship.query.filter(
            db.or_(
                ArchiMateRelationship.source_id.in_(element_ids),
                ArchiMateRelationship.target_id.in_(element_ids),
            )
        ).all()

        vs_ids = set(type_index.get("ValueStream", []))
        cap_ids = set(type_index.get("Capability", []))
        outcome_ids = set(type_index.get("Outcome", []))

        # Collect element IDs in the parent architecture model for cross-section elements
        # (Goal lives in vision_motivation section, not value_stream_map)
        goal_ids = set()
        try:
            from app.models.archimate_core import ArchiMateElement
            arch_id = self._get_architecture_id(solution_id)
            if arch_id:
                goals = ArchiMateElement.query.filter_by(
                    architecture_id=arch_id, type="Goal"
                ).all()
                goal_ids = {g.id for g in goals}
        except Exception as exc:
            logger.debug("suppressed error in BlueprintCompletenessService._score_value_stream_chain (app/modules/solutions_strategic/v2/services/blueprint_completeness_service.py): %s", exc)

        score = 0.0

        # 35 pts: Goal → ValueStream realization (Goal realizes ValueStream, or VS realizes Goal)
        goal_vs_linked = any(
            ((r.type or "").lower() == "realization" and r.source_id in vs_ids and r.target_id in goal_ids)
            or ((r.type or "").lower() == "realization" and r.source_id in goal_ids and r.target_id in vs_ids)
            for r in rels
        )
        if goal_vs_linked:
            score += 35.0
        elif has_vs and goal_ids:
            # Partial credit if ValueStream exists but Goal link is missing
            score += 10.0

        # 35 pts: ValueStream → Capability (association or serving in either direction)
        vs_cap_linked = has_cap and any(
            ((r.type or "").lower() in ("association", "serving", "realization"))
            and (
                (r.source_id in vs_ids and r.target_id in cap_ids)
                or (r.source_id in cap_ids and r.target_id in vs_ids)
            )
            for r in rels
        )
        if vs_cap_linked:
            score += 35.0
        elif has_cap:
            score += 10.0  # Capability present but not linked to ValueStream

        # 30 pts: ValueStream → Outcome realization
        vs_outcome_linked = has_outcome and any(
            (r.type or "").lower() == "realization"
            and (
                (r.source_id in vs_ids and r.target_id in outcome_ids)
                or (r.source_id in outcome_ids and r.target_id in vs_ids)
            )
            for r in rels
        )
        if vs_outcome_linked:
            score += 30.0
        elif has_outcome:
            score += 10.0  # Outcome present but not linked to ValueStream

        return min(score, 100.0)

    def _score_element_coverage(self, elements, required_types):
        """Percentage of required types present with non-empty description."""
        if not required_types:
            return 100.0 if elements else 0.0

        types_present = set()
        for elem in elements:
            if elem.type in required_types and elem.description:
                types_present.add(elem.type)

        return (len(types_present) / len(required_types)) * 100

    def _score_relationship_coverage(self, solution_id, elements):
        """Score relationship completeness.

        Uses simple connectivity: proportion of solution elements that participate
        in at least one ArchiMate relationship (to any element). This is a good
        proxy for whether the architect has modelled relationships, without the
        false-strict penalty of enterprise-wide canonical chain expectations.
        """
        if not elements:
            return 0.0
        return self._score_relationships_simple(elements)

    def _score_relationships_solution_scoped(self, elements):
        """Proportion of elements with at least one intra-solution relationship.

        An element scores as 'connected' only when BOTH the source and target
        of a relationship are present in this solution's element set.

        Queries both ArchiMateRelationship (ARM) and ArchitectureInferenceRelationship (AIR)
        so that wizard-generated relationships (written to AIR by domain_promotion.py) are
        counted. ARM-only scoring returned 0% for all wizard solutions (sol 5723: AIR=24, ARM=0).
        """
        if not elements:
            return 0.0

        solution_element_ids = set(e.id for e in elements)

        if len(solution_element_ids) > 500:
            logger.warning(
                "Large element set (%d) in _score_relationships_solution_scoped "
                "— IN clause may be slow; consider EXISTS subquery if this becomes a hotspot",
                len(solution_element_ids),
            )

        connected = set()

        def _apply_strict(rels):
            """Count only relationships where both ends are in this solution."""
            for r in rels:
                if r.source_id in solution_element_ids and r.target_id in solution_element_ids:
                    connected.add(r.source_id)
                    connected.add(r.target_id)

        _apply_strict(ArchiMateRelationship.query.filter(
            db.or_(
                ArchiMateRelationship.source_id.in_(solution_element_ids),
                ArchiMateRelationship.target_id.in_(solution_element_ids),
            )
        ).all())

        try:
            from app.models.architecture_inference_relationship import (
                ArchitectureInferenceRelationship as AIR,
            )
            _apply_strict(AIR.query.filter(
                db.or_(
                    AIR.source_id.in_(solution_element_ids),
                    AIR.target_id.in_(solution_element_ids),
                )
            ).all())
        except Exception as _air_err:
            logger.warning(
                "AIR scoring failed in _score_relationships_solution_scoped "
                "(%d elements) — ARM-only fallback: %s",
                len(elements), _air_err,
            )

        return (len(connected) / len(elements)) * 100

    def _score_relationships_simple(self, elements):
        """Proportion of elements that participate in any relationship (cross-solution fallback).

        Looser semantics than _score_relationships_solution_scoped: an element counts as
        connected if it appears on either end of any relationship, even cross-solution.

        Queries both ARM and AIR so wizard-generated relationships are included.
        """
        if not elements:
            return 0.0

        element_ids = set(e.id for e in elements)

        if len(element_ids) > 500:
            logger.warning(
                "Large element set (%d) in _score_relationships_simple "
                "— IN clause may be slow; consider EXISTS subquery if this becomes a hotspot",
                len(element_ids),
            )

        connected = set()

        def _apply_loose(rels):
            for r in rels:
                if r.source_id in element_ids:
                    connected.add(r.source_id)
                if r.target_id in element_ids:
                    connected.add(r.target_id)

        _apply_loose(ArchiMateRelationship.query.filter(
            db.or_(
                ArchiMateRelationship.source_id.in_(element_ids),
                ArchiMateRelationship.target_id.in_(element_ids),
            )
        ).all())

        try:
            from app.models.architecture_inference_relationship import (
                ArchitectureInferenceRelationship as AIR,
            )
            _apply_loose(AIR.query.filter(
                db.or_(
                    AIR.source_id.in_(element_ids),
                    AIR.target_id.in_(element_ids),
                )
            ).all())
        except Exception as _air_err:
            logger.warning(
                "AIR scoring failed in _score_relationships_simple "
                "(%d elements) — ARM-only fallback: %s",
                len(elements), _air_err,
            )

        return (len(connected) / len(elements)) * 100

    def _score_traceability(self, solution_id, elements):
        """Score traceability using InferenceEngine.validate_chain().

        Score = % of elements with 0 validation errors.
        Falls back to 0 if no architecture_id.
        """
        if not elements:
            return 0.0

        architecture_id = self._get_architecture_id(solution_id)
        if not architecture_id:
            return 0.0

        try:
            from app.modules.architecture.services.inference_engine_service import (
                ArchiMateInferenceEngine,
            )
            engine = ArchiMateInferenceEngine(architecture_id)
        except Exception:
            logger.warning(
                "Failed to initialise InferenceEngine for traceability scoring "
                "(architecture %s)",
                architecture_id,
            )
            return 0.0

        valid_count = 0
        total_checked = 0
        for elem in elements:
            try:
                result = engine.validate_chain(elem.id)
                total_checked += 1
                if len(result.errors) == 0:
                    valid_count += 1
            except Exception:
                logger.warning(
                    "InferenceEngine.validate_chain() failed for element %s, skipping",
                    elem.id,
                )
                continue

        if total_checked == 0:
            return 0.0
        return (valid_count / total_checked) * 100

    def _score_spec_completeness(self, solution_id, section_id):
        """Score spec completeness: all specs confirmed (fields, contracts, rules, deployment).

        Returns 0-100 integer.
        """
        junctions = SolutionArchiMateElement.query.filter_by(solution_id=solution_id).all()
        if not junctions:
            return 0

        section_def = self.SECTION_DEFINITIONS.get(section_id, {})
        required_types = section_def.get("required_types", [])

        relevant = []
        for j in junctions:
            elem = ArchiMateElement.query.get(j.element_id)
            if elem and elem.type in required_types:
                relevant.append(j)

        if not relevant:
            return 0

        confirmed_count = 0
        total_checks = 0

        for j in relevant:
            sd = j.spec_data or {}

            if section_id == "application_cooperation":
                # Check fields + api_contract + business_rules
                total_checks += 3
                if sd.get("fields_status") == "confirmed":
                    confirmed_count += 1
                if sd.get("contract_status") == "confirmed":
                    confirmed_count += 1
                rules = sd.get("business_rules", [])
                if rules and all(r.get("status") == "confirmed" for r in rules):
                    confirmed_count += 1
                elif not rules:
                    total_checks -= 1  # No rules to confirm

            elif section_id == "data_information":
                # Check integration contracts
                integrations = sd.get("integrations", {})
                if integrations:
                    total_checks += len(integrations)
                    for key, contract in integrations.items():
                        if contract.get("status") == "confirmed":
                            confirmed_count += 1
                else:
                    total_checks += 1  # At least one expected

            elif section_id == "deployment_view":
                # Check deployment spec
                total_checks += 1
                if sd.get("deployment_status") == "confirmed":
                    confirmed_count += 1

        if total_checks == 0:
            return 0
        return round(confirmed_count / total_checks * 100)

    def _score_spec_quality(self, solution_id, section_id):
        """Score spec quality: validated, consistent, versioned, hashed.

        Returns 0-100 integer.
        """
        try:
            from app.modules.solutions_strategic.v2.services.spec_validators import (
                validate_fields_schema,
                validate_integration_contract,
                validate_deployment_spec,
            )
        except ImportError:
            logger.warning("spec_validators not available, spec_quality scored as 0")
            return 0

        junctions = SolutionArchiMateElement.query.filter_by(solution_id=solution_id).all()
        if not junctions:
            return 0

        quality_points = 0
        quality_total = 0

        for j in junctions:
            sd = j.spec_data or {}

            if section_id == "application_cooperation":
                fields = sd.get("fields", [])
                if fields:
                    quality_total += 4  # validation, versioned, hashed, descriptions
                    errors = validate_fields_schema(fields)
                    if not errors:
                        quality_points += 1
                    if sd.get("fields_version", 0) > 0:
                        quality_points += 1
                    if sd.get("fields_hash"):
                        quality_points += 1
                    described = sum(1 for f in fields if f.get("description"))
                    if described >= len(fields) * 0.8:
                        quality_points += 1

            elif section_id == "data_information":
                for key, contract in sd.get("integrations", {}).items():
                    quality_total += 3  # validation, versioned, hashed
                    errors = validate_integration_contract(contract)
                    if not errors:
                        quality_points += 1
                    if contract.get("version", 0) > 0:
                        quality_points += 1
                    if contract.get("contract_hash"):
                        quality_points += 1

            elif section_id == "deployment_view":
                deploy = sd.get("deployment", {})
                if deploy:
                    quality_total += 3  # validation, versioned, hashed
                    errors = validate_deployment_spec(deploy)
                    if not errors:
                        quality_points += 1
                    if sd.get("deployment_version", 0) > 0:
                        quality_points += 1
                    if sd.get("deployment_hash"):
                        quality_points += 1

        if quality_total == 0:
            return 0
        return round(quality_points / quality_total * 100)

    def _build_action_description(self, section_id, scores, defn):
        """Build a human-readable action description for a low-scoring section."""
        if defn.get("scoring") == "narrative_only":
            if scores.get("overall", 0) < 100:
                return (
                    f"Write at least {NARRATIVE_WORD_THRESHOLD} words for "
                    f"the {SECTION_TITLES.get(section_id, section_id)} narrative."
                )
            return "Section is complete."

        candidates = [
            ("element", scores.get("element", 0)),
            ("relationship", scores.get("relationship", 0)),
            ("traceability", scores.get("traceability", 0)),
        ]
        if section_id in SPEC_ENABLED_SECTIONS:
            candidates.append(("spec_completeness", scores.get("spec_completeness", 0)))
            candidates.append(("spec_quality", scores.get("spec_quality", 0)))

        weakest = min(candidates, key=lambda x: x[1])
        required = defn.get("required_types", [])

        if weakest[0] == "element":
            if section_id == "value_stream_map":
                return (
                    "Link this solution to the business capabilities it serves. "
                    "Add a Capability ArchiMate element and connect it to the enterprise "
                    "catalog — without this, ARB reviewers cannot assess alignment with "
                    "the enterprise capability model."
                )
            missing_hint = ", ".join(required[:3])
            return (
                f"Add ArchiMate elements of types: {missing_hint}. "
                f"Ensure each has a description."
            )
        elif weakest[0] == "relationship":
            return (
                "Connect existing elements with relationships. "
                "Use the ArchiMate composer to add missing links."
            )
        elif weakest[0] == "spec_completeness":
            return (
                "Confirm component specifications. Open each element's spec panel "
                "and confirm fields, contracts, and business rules."
            )
        elif weakest[0] == "spec_quality":
            return (
                "Improve spec quality by ensuring all specs pass validation, "
                "have version history, and include descriptions for all fields."
            )
        else:
            return (
                "Improve traceability by ensuring all elements have valid "
                "downstream chains with correct relationship types."
            )
