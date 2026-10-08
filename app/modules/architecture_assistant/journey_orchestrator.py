"""Master orchestrator for the journey wizard.

Coordinates the per-step pipeline:
Step 1: ClarificationService → enriched brief → motivation element generation
Step 2: CapabilityDerivationService → three-catalog matching + inference cascade
Step 3: JourneyGraph.create_inference_skeleton → ArchitectureGenerationService → detail filling
Step 4: VariantGeneratorService → decision points, diff-based options, risk persistence
Step 5: MigrationPlannerService → gap analysis, phased work packages, RoadmapTask persistence
Step 6: ValidationEngineService → compliance, COBIT/ITIL, traceability, ARB submission
"""

import logging

from app import db

logger = logging.getLogger(__name__)


class JourneyOrchestrator:
    """Coordinates the journey wizard pipeline."""

    def __init__(self, solution_id: int):
        self.solution_id = solution_id
        self._graph = None

    @property
    def graph(self):
        """Lazy-load JourneyGraph."""
        if self._graph is None:
            from app.modules.architecture_assistant.journey_graph import JourneyGraph
            self._graph = JourneyGraph.resume_for_solution(self.solution_id)
        return self._graph

    # ── Session Persistence ─────────────────────────────────────────

    def save_state(self, state_data: dict) -> dict:
        """Save wizard navigation state to Solution.journey_state.

        Merges incoming state with existing, preserving server-managed keys
        (those starting with '_') that the client never sends.
        """
        from app.models.solution_models import Solution
        from sqlalchemy.orm.attributes import flag_modified
        solution = Solution.query.get(self.solution_id)
        if not solution:
            return {"error": "Solution not found"}
        # Preserve server-managed keys (e.g. _arch_gen, _arch_critique) that
        # the client never includes in its save-state payload.
        existing = solution.journey_state if isinstance(solution.journey_state, dict) else {}
        merged = {k: v for k, v in existing.items() if k.startswith("_")}
        merged.update(state_data)
        solution.journey_state = merged
        flag_modified(solution, "journey_state")
        db.session.commit()
        return {"saved": True}

    def load_state(self) -> dict:
        """Load wizard state + architecture elements from DB."""
        from app.models.solution_models import Solution
        solution = Solution.query.get(self.solution_id)
        if not solution:
            return {"journey_state": None, "architecture": None, "proposals": []}

        # Load architecture elements if they exist
        architecture = None
        try:
            by_layer = self.graph.get_elements_by_layer()
            architecture = {
                layer: [{
                    "id": n.id,
                    "name": n.name,
                    "type": n.element_type,
                    "description": getattr(n.model, "description", "") or "",
                    "source": getattr(n.model, "acm_source", None) or "derived",
                    "acm_domain": getattr(n.model, "acm_domain", None),
                } for n in nodes]
                for layer, nodes in by_layer.items()
            }
        except Exception as e:
            logger.debug("No architecture to load: %s", e)

        # Load relationships between loaded elements
        relationships = []
        if architecture:
            try:
                from app.models.archimate_core import ArchiMateRelationship, ArchitectureModel
                from app.models.architecture_inference_relationship import ArchitectureInferenceRelationship
                name_by_id = {el["id"]: el["name"] for els in architecture.values() for el in els}
                element_ids = list(name_by_id.keys())
                if element_ids:
                    # Load ArchiMate relationships (from LLM generation)
                    rels = ArchiMateRelationship.query.filter(
                        ArchiMateRelationship.source_id.in_(element_ids),
                        ArchiMateRelationship.target_id.in_(element_ids),
                    ).all()
                    for r in rels:
                        src_name = name_by_id.get(r.source_id)
                        tgt_name = name_by_id.get(r.target_id)
                        if src_name and tgt_name:
                            relationships.append({
                                "source_id": r.source_id,
                                "source_name": src_name,
                                "target_id": r.target_id,
                                "target_name": tgt_name,
                                "type": r.type,
                            })
                    # Also load inference relationships (from domain promotion + repair)
                    arch_model = ArchitectureModel.query.filter_by(solution_id=self.solution_id).first()
                    if arch_model:
                        existing_pairs = {(r["source_id"], r["target_id"]) for r in relationships}
                        inf_rels = ArchitectureInferenceRelationship.query.filter_by(
                            architecture_id=arch_model.id,
                        ).all()
                        for r in inf_rels:
                            if (r.source_id, r.target_id) not in existing_pairs:
                                src_name = name_by_id.get(r.source_id)
                                tgt_name = name_by_id.get(r.target_id)
                                if src_name and tgt_name:
                                    relationships.append({
                                        "source_id": r.source_id,
                                        "source_name": src_name,
                                        "target_id": r.target_id,
                                        "target_name": tgt_name,
                                        "type": r.rel_type,
                                    })
            except Exception as e:
                logger.debug("Could not load relationships: %s", e)

        # Load proposals
        proposals = []
        try:
            from app.modules.architecture_assistant.document_ingestion import DocumentIngestionService
            proposals = DocumentIngestionService().list_proposals(self.solution_id)
        except Exception as e:
            logger.debug("Could not load proposals: %s", e)

        return {
            "journey_state": solution.journey_state,
            "enriched_brief": solution.problem_clarification or solution.description or "",
            "architecture": architecture,
            "relationships": relationships,
            "proposals": proposals,
        }

    # ── Document Ingestion ────────────────────────────────────────────

    def ingest_document(self, file_storage) -> dict:
        """Step 1 alt: Extract architecture elements from an uploaded document."""
        from app.modules.architecture_assistant.document_ingestion import DocumentIngestionService
        svc = DocumentIngestionService()
        return svc.extract_from_file(self.solution_id, file_storage)

    def ingest_text(self, text, source_name="pasted text") -> dict:
        """Step 1 alt: Extract from pasted text."""
        from app.modules.architecture_assistant.document_ingestion import DocumentIngestionService
        svc = DocumentIngestionService()
        return svc.extract_from_text(self.solution_id, text, source_doc_name=source_name)

    def list_proposals(self, status=None) -> list:
        """List blueprint proposals for this solution."""
        from app.modules.architecture_assistant.document_ingestion import DocumentIngestionService
        return DocumentIngestionService().list_proposals(self.solution_id, status)

    def accept_proposal(self, proposal_id) -> dict:
        """Accept a single proposal — create element + run inference chain."""
        from app.modules.architecture_assistant.document_ingestion import DocumentIngestionService
        return DocumentIngestionService().accept_proposal(proposal_id, self.solution_id)

    def reject_proposal(self, proposal_id) -> dict:
        """Reject a proposal."""
        from app.modules.architecture_assistant.document_ingestion import DocumentIngestionService
        return DocumentIngestionService().reject_proposal(proposal_id, self.solution_id)

    def batch_accept_proposals(self, proposal_ids) -> dict:
        """Batch accept multiple proposals."""
        from app.modules.architecture_assistant.document_ingestion import DocumentIngestionService
        return DocumentIngestionService().batch_accept(proposal_ids, self.solution_id)

    # ── Step 1: Clarification ────────────────────────────────────────

    def generate_clarifying_questions(self, problem_statement: str) -> dict:
        """Step 1A: Generate clarifying questions from the problem brief."""
        from app.modules.architecture_assistant.clarification_service import ClarificationService
        svc = ClarificationService()
        questions = svc.generate_questions(problem_statement)
        return {"questions": questions}

    def merge_clarification_answers(self, original_brief: str, answers: list) -> dict:
        """Step 1B: Merge answers into enriched brief."""
        from app.modules.architecture_assistant.clarification_service import ClarificationService
        svc = ClarificationService()
        enriched = svc.merge_answers(original_brief, answers)
        return {"enriched_brief": enriched}

    # ── Step 2: Capability Derivation ────────────────────────────────

    def derive_capabilities(self, problem_description: str, motivation_elements: list = None) -> dict:
        """Step 2: Derive business capabilities + technical + application + compliance.
        
        Capability derivation now reads directly from the canonical business-capability
        store and the compliance requirement table, without the intermediate
        CapabilityDerivationService which was a duplicate implementation.
        """
        from app.models.business_capabilities import BusinessCapability
        from app.modules.solutions_strategic.v2.routes.solution_ai_routes import _match_against_catalog

        caps = BusinessCapability.query.order_by(BusinessCapability.name).all()
        if not caps:
            return {"capabilities": [], "gap_summary": ""}

        # Map capabilities into the shape the downstream JourneyOrchestrator expects
        suggestions = [{"name": c.name, "id": c.id, "description": getattr(c, "description", "")} for c in caps]

        try:
            from app.modules.ai_chat.services.solution_ai_service import SolutionAIService
            ai_service = SolutionAIService()
            ai_result = ai_service.suggest_capabilities(
                solution_description=problem_description,
                existing_capabilities=[{"id": c.id, "name": c.name} for c in caps],
                motivation_elements=motivation_elements,
            )
            if ai_result.get("success") and ai_result.get("capabilities"):
                matched = _match_against_catalog(
                    suggestions=ai_result["capabilities"],
                    catalog_caps=caps,
                    problem_brief=problem_description,
                )
                result = matched if isinstance(matched, dict) else {"capabilities": matched}
                return result
        except Exception:
            pass
        return {"capabilities": suggestions, "gap_summary": ""}

    def get_capability_details(self, capability_id: int,
                               capability_name: str,
                               business_domain: str = "") -> dict:
        """Get technical caps, coverage gaps, compliance, and APQC links for one capability.

        Reads from the canonical stores directly instead of through the removed
        CapabilityDerivationService.
        """
        from app.models.technical_capability import TechnicalCapability
        from app.models.application_layer import ApplicationComponent
        from app.models.business_capabilities import ApplicationCapabilityCoverage
        from app.modules.ai_chat.services.solution_ai_service import _token_overlap

        # Technical capabilities — ACM domain match
        tech_caps = []
        try:
            all_tech = TechnicalCapability.query.order_by(TechnicalCapability.level_number).all()
            for tc in all_tech:
                overlap = _token_overlap(capability_name, getattr(tc, "name", ""))
                if overlap >= 0.3:
                    tech_caps.append({
                        "id": tc.id,
                        "name": tc.name,
                        "acm_domain": getattr(tc, "acm_domain", ""),
                        "level": getattr(tc, "level", None),
                        "description": getattr(tc, "description", ""),
                        "match_score": round(overlap, 2),
                        "match_type": "exact" if overlap >= 0.7 else "partial",
                        "source": "catalog",
                    })
            tech_caps.sort(key=lambda x: x["match_score"], reverse=True)
            tech_caps = tech_caps[:10]
        except Exception:
            pass

        # Coverage gaps
        coverage = []
        try:
            rows = ApplicationCapabilityCoverage.query.filter_by(
                capability_id=capability_id
            ).order_by(ApplicationCapabilityCoverage.coverage_percentage.desc()).all()
            app_ids = [c.application_component_id for c in rows]
            apps = ApplicationComponent.query.filter(
                ApplicationComponent.id.in_(app_ids)
            ).all() if app_ids else []
            apps_by_id = {a.id: a for a in apps}
            coverage = [{
                "application_id": c.application_component_id,
                "application_name": (
                    apps_by_id[c.application_component_id].name
                    if c.application_component_id in apps_by_id
                    else f"App {c.application_component_id}"
                ),
                "coverage_percentage": c.coverage_percentage,
                "support_level": getattr(c, "support_level", None),
                "is_strategic": getattr(c, "is_strategic", False),
                "confidence_score": getattr(c, "confidence_score", None),
            } for c in rows]
        except Exception:
            pass

        # Compliance requirements
        compliance = []
        try:
            from app.modules.architecture_assistant.services.compliance_service import (
                query_compliance_requirements,
            )
            compliance = query_compliance_requirements(capability_id)
        except Exception:
            pass

        # APQC links
        apqc_processes = []
        try:
            from app.models.business_capabilities import BusinessCapability
            all_caps = BusinessCapability.query.filter(
                BusinessCapability.code.isnot(None)
            ).all()
            for cap in all_caps:
                score = _token_overlap(capability_name, cap.name)
                if score >= 0.5:
                    apqc_processes.append({
                        "id": cap.id,
                        "name": cap.name,
                        "code": getattr(cap, "code", ""),
                        "match_score": round(score, 2),
                    })
            apqc_processes.sort(key=lambda x: x["match_score"], reverse=True)
            apqc_processes = apqc_processes[:5]
        except Exception:
            pass

        return {
            "technical_capabilities": tech_caps,
            "coverage": coverage,
            "compliance": compliance,
            "apqc_processes": apqc_processes,
        }

    # ── Step 3: Architecture Generation ──────────────────────────────

    def generate_architecture(self, accepted_capabilities: list, problem_summary: str, compliance_constraints: list = None) -> dict:
        """Step 3: Generate comprehensive 7-layer ArchiMate architecture and persist to DB.

        Uses per-capability LLM expansion to produce elements across Motivation,
        Strategy, Business, Application, Technology, and Implementation layers.
        Persists each element to: ArchiMateElement (global catalog) and
        SolutionBlueprintProposal with status='pending' (for Steps 4/5/6 review).
        SolutionArchiMateElement junctions are created ONLY when a proposal is
        accepted (single: accept_proposal, bulk: batch_accept → confirm_domain).
        Also persists AIR relationships.

        Args:
            accepted_capabilities: List of dicts with name, description, match_type
            problem_summary: Enriched problem description
            compliance_constraints: From Step 2 compliance injection

        Returns:
            dict with elements_by_layer, generation result, and basic validation
        """
        from app.modules.architecture_assistant.architecture_generation import ArchitectureGenerationService
        gen_svc = ArchitectureGenerationService()

        # ── Fix 1: Create a run record and engine_run_id before any DB work ──
        import uuid
        from app.models.architecture_generation_run import ArchitectureGenerationRun
        engine_run_id = str(uuid.uuid4())
        gen_run = ArchitectureGenerationRun(
            run_id=engine_run_id,
            solution_id=self.solution_id,
            status="running",
        )
        db.session.add(gen_run)
        try:
            db.session.flush()  # get gen_run.id; rolled back with outer try if needed
        except Exception:
            db.session.rollback()
            engine_run_id = str(uuid.uuid4())  # degrade gracefully — no run record

        # ── Fix 4: Pre-run cleanup — remove unreviewed journey proposals from prior runs ──
        # Post-ORCH-001: generation writes proposals (not SAE rows), so cleanup
        # deletes stale pending journey proposals so re-runs produce a fresh proposal set.
        try:
            from app.models.solution_blueprint_proposal import SolutionBlueprintProposal as _SBP_cleanup
            _stale_proposals = _SBP_cleanup.query.filter(
                _SBP_cleanup.solution_id == self.solution_id,
                _SBP_cleanup.status == "pending",
                _SBP_cleanup.source.in_(["journey", "journey_gap_fill"]),
            ).all()
            _cleaned = len(_stale_proposals)
            for _sp in _stale_proposals:
                db.session.delete(_sp)
            if _cleaned:
                db.session.flush()
                logger.info("Pre-run cleanup removed %d pending journey proposals", _cleaned)
        except Exception as _cleanup_err:
            logger.warning("Pre-run cleanup failed (non-fatal): %s", _cleanup_err)
            db.session.rollback()
        # ── End Fix 1+4 ──────────────────────────────────────────────────

        # Load already-persisted elements for this solution so generate_greenfield can:
        # (a) pre-seed its dedup set — preventing re-creation of document-extracted elements
        # (b) inject them into the LLM prompt as context — preventing hollow echoes
        # Post-ORCH-001: proposals are the primary source of truth (SAE rows only exist for
        # accepted/legacy solutions). Query proposals first; fall back to SAE for legacy.
        _existing_elements = []
        try:
            from app.models.solution_blueprint_proposal import SolutionBlueprintProposal as _SBP_pre
            _proposal_rows = _SBP_pre.query.filter(
                _SBP_pre.solution_id == self.solution_id,
                _SBP_pre.status.notin_(["rejected"]),
            ).with_entities(_SBP_pre.name, _SBP_pre.archimate_type).all()
            _existing_elements = [
                {"name": r.name, "type": r.archimate_type, "layer": ""}
                for r in _proposal_rows if r.name and r.archimate_type
            ]
            if not _existing_elements:
                # Legacy fallback: solutions with SAE rows but no proposals
                from app.models.archimate_core import ArchiMateElement as _AE_pre
                from app.models.solution_archimate_element import SolutionArchiMateElement as _SAE_pre
                _existing_rows = (
                    db.session.query(_AE_pre.name, _AE_pre.type, _AE_pre.layer)
                    .join(_SAE_pre, _SAE_pre.element_id == _AE_pre.id)
                    .filter(_SAE_pre.solution_id == self.solution_id)
                    .all()
                )
                _existing_elements = [
                    {"name": r.name, "type": r.type, "layer": r.layer or "application"}
                    for r in _existing_rows
                ]
            if _existing_elements:
                logger.info(
                    "Pre-seeding generation with %d existing elements for solution %d",
                    len(_existing_elements), self.solution_id,
                )
        except Exception as _pre_err:
            logger.warning("Could not load existing elements for dedup pre-seed: %s", _pre_err)

        # Per-capability expansion across all 7 layers + relationship pass
        gen_result = gen_svc.generate_greenfield(
            capabilities=accepted_capabilities,
            problem_summary=problem_summary,
            compliance_constraints=compliance_constraints,
            existing_elements=_existing_elements,
        )

        # ── Fix 2: Validate LLM output before any DB write ──────────────
        VALID_ARCHIMATE_TYPES = {
            "Stakeholder", "Driver", "Goal", "Requirement", "Assessment", "Principle",
            "Constraint", "Outcome", "Value", "Meaning",
            "Capability", "CourseOfAction", "ValueStream", "Resource",
            "BusinessProcess", "BusinessService", "BusinessObject", "BusinessRole",
            "BusinessActor", "BusinessFunction", "BusinessEvent", "BusinessInterface",
            "BusinessCollaboration", "BusinessInteraction", "Contract", "Product",
            "ApplicationComponent", "ApplicationService", "ApplicationFunction",
            "ApplicationInterface", "ApplicationEvent", "ApplicationProcess", "DataObject",
            "Node", "Device", "SystemSoftware", "TechnologyService", "TechnologyFunction",
            "TechnologyInterface", "Artifact", "CommunicationNetwork", "Path",
            "WorkPackage", "Deliverable", "Plateau", "Gap", "ImplementationEvent",
            "Equipment", "Facility", "DistributionNetwork", "Material",
        }
        _pre_layers = gen_result.get("elements_by_layer", {})
        _pre_total = sum(len(v) for v in _pre_layers.values())
        _validation_errors = []
        if _pre_total == 0:
            _validation_errors.append("LLM returned zero elements across all layers")
        for _layer, _els in _pre_layers.items():
            for _el in _els:
                if not _el.get("name", "").strip():
                    _validation_errors.append(f"Element in {_layer} has empty name")
                if _el.get("type") not in VALID_ARCHIMATE_TYPES:
                    _validation_errors.append(
                        f"Invalid ArchiMate type '{_el.get('type')}' in {_layer}"
                    )
        if _validation_errors:
            logger.error("Architecture generation validation failed: %s", _validation_errors)
            return {
                "elements_by_layer": {},
                "relationships": [],
                "validation": {
                    "issues": [
                        {"element_name": "Generation", "missing_type": e, "severity": "required"}
                        for e in _validation_errors
                    ],
                    "overall": 0,
                },
                "error": f"LLM output failed validation: {_validation_errors[0]}",
            }
        # ── End Fix 2 ────────────────────────────────────────────────────

        elements_by_layer = gen_result.get("elements_by_layer", {})
        raw_relationships = gen_result.get("relationships", [])
        total = sum(len(v) for v in elements_by_layer.values())

        # ── Domain coherence pre-filter ─────────────────────────────────
        # Reject elements whose names share zero meaningful tokens with the
        # problem_summary.  This prevents LLM domain drift (e.g. IoT/industrial
        # elements appearing in an email-order-processing solution).
        # Only applied when problem_summary is non-empty; structural elements
        # (WorkPackage, Plateau, Gap, Deliverable) are exempt.
        import re as _re
        _DC_STOP = frozenset({
            'a','an','the','and','or','of','to','in','for','with','by','at','on',
            'is','are','be','as','this','that','its','it','new','system','service',
            'data','platform','solution','management','process','processing',
            'capability','component','interface','application','api','layer',
            'architecture','enterprise','digital','business','technology',
            'implement','design','integrate','integration','support','enable',
        })
        _DC_EXEMPT_TYPES = {
            'WorkPackage','Plateau','Gap','Deliverable','ImplementationEvent',
        }
        def _dc_tokens(text):
            return {w for w in _re.split(r'[^a-z]+', (text or '').lower())
                    if len(w) > 3 and w not in _DC_STOP}

        _domain_tokens = _dc_tokens(problem_summary)
        _domain_filter_active = len(_domain_tokens) >= 3
        _domain_rejected = 0

        if _domain_filter_active:
            filtered_by_layer = {}
            for _layer, _els in elements_by_layer.items():
                kept = []
                for _el in _els:
                    _el_type = _el.get("type", "")
                    if _el_type in _DC_EXEMPT_TYPES:
                        kept.append(_el)
                        continue
                    _el_tokens = _dc_tokens(_el.get("name", "") + " " + _el.get("description", ""))
                    if _el_tokens & _domain_tokens:
                        kept.append(_el)
                    else:
                        _domain_rejected += 1
                        logger.debug("Domain filter rejected: %s (%s) — no overlap with problem summary",
                                     _el.get("name"), _el_type)
                filtered_by_layer[_layer] = kept
            elements_by_layer = filtered_by_layer
            if _domain_rejected:
                logger.info("Domain coherence filter removed %d off-domain elements", _domain_rejected)
        # ── End domain coherence pre-filter ────────────────────────────

        # ── Persist elements to DB ──────────────────────────────────────
        persisted_by_layer = {}
        name_to_element_id = {}  # For relationship resolution

        # ACM domain mapping: ArchiMate type -> default ACM domain
        TYPE_TO_ACM_DOMAIN = {
            # Motivation layer
            "Stakeholder": "UX", "Driver": "UX", "Goal": "UX", "Requirement": "SEC",
            "Assessment": "UX", "Principle": "SEC", "Constraint": "SEC",
            "Outcome": "UX", "Value": "UX", "Meaning": "UX",
            # Strategy layer
            "Capability": "APP", "CourseOfAction": "APP", "ValueStream": "APP",
            "Resource": "APP",
            # Business layer
            "BusinessProcess": "APP", "BusinessService": "APP", "BusinessObject": "DATA",
            "BusinessRole": "UX", "BusinessActor": "UX", "BusinessFunction": "APP",
            "BusinessEvent": "APP", "BusinessInterface": "UX", "BusinessCollaboration": "APP",
            "BusinessInteraction": "APP", "Contract": "SEC", "Product": "APP",
            # Application layer
            "ApplicationComponent": "APP", "ApplicationService": "APP",
            "ApplicationFunction": "APP", "ApplicationInterface": "COM",
            "ApplicationEvent": "APP", "ApplicationProcess": "APP",
            "DataObject": "DATA",
            # Technology layer
            "Node": "DEV", "Device": "DEV", "SystemSoftware": "DEV",
            "TechnologyService": "DEV", "TechnologyFunction": "DEV",
            "TechnologyInterface": "COM", "Artifact": "DEV",
            "CommunicationNetwork": "COM", "Path": "COM",
            # Implementation layer
            "WorkPackage": "DEV", "Deliverable": "DEV", "Plateau": "APP",
            "Gap": "APP", "ImplementationEvent": "DEV",
            # Physical layer
            "Equipment": "DEV", "Facility": "DEV", "DistributionNetwork": "COM",
            "Material": "DEV",
        }

        arch_model_id = None
        try:
            from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship, ArchitectureModel
            from app.models.solution_blueprint_proposal import SolutionBlueprintProposal

            # Get or create an ArchitectureModel scoped to this solution
            arch_model = ArchitectureModel.query.filter_by(solution_id=self.solution_id).first()
            if not arch_model:
                arch_model = ArchitectureModel(
                    name=f"Journey Architecture (Solution {self.solution_id})",
                    version="1.0",
                    solution_id=self.solution_id,
                )
                db.session.add(arch_model)
                db.session.flush()
            arch_model_id = arch_model.id

            # Resolve organization_id from solution (required by archimate_elements NOT NULL constraint)
            from app.models.solution_models import Solution
            _solution_obj = Solution.query.get(self.solution_id)
            _org_id = _solution_obj.organization_id if _solution_obj else None

            # ── Zero-touch quality classification ─────────────────────
            from app.modules.architecture_assistant.element_quality_classifier import classify_element
            _auto_accepted = 0
            _pending_review = 0
            _auto_rejected = 0
            _rejected_reasons: list = []
            # coverage_matrix[capability][layer] = element_count
            _coverage: dict = {}

            # Canonical ArchiMate 3.2 type→layer map.  LLMs occasionally place an element
            # in the wrong layer key (e.g. SystemSoftware under "business").  Override the
            # LLM-supplied layer with the canonical one so the DB is always correct.
            _CANONICAL_LAYER = {
                'Stakeholder':'motivation','Driver':'motivation','Assessment':'motivation',
                'Goal':'motivation','Outcome':'motivation','Principle':'motivation',
                'Requirement':'motivation','Constraint':'motivation','Meaning':'motivation','Value':'motivation',
                'Resource':'strategy','Capability':'strategy','ValueStream':'strategy','CourseOfAction':'strategy',
                'BusinessActor':'business','BusinessRole':'business','BusinessProcess':'business',
                'BusinessFunction':'business','BusinessService':'business','BusinessObject':'business',
                'BusinessInterface':'business','BusinessCollaboration':'business',
                'BusinessInteraction':'business','BusinessEvent':'business',
                'Contract':'business','Representation':'business','Product':'business',
                'ApplicationComponent':'application','ApplicationCollaboration':'application',
                'ApplicationInterface':'application','ApplicationFunction':'application',
                'ApplicationProcess':'application','ApplicationInteraction':'application',
                'ApplicationEvent':'application','ApplicationService':'application','DataObject':'application',
                'Node':'technology','Device':'technology','SystemSoftware':'technology',
                'TechnologyCollaboration':'technology','TechnologyInterface':'technology','Path':'technology',
                'CommunicationNetwork':'technology','TechnologyFunction':'technology',
                'TechnologyProcess':'technology','TechnologyInteraction':'technology',
                'TechnologyEvent':'technology','TechnologyService':'technology','Artifact':'technology',
                'Equipment':'physical','Facility':'physical','DistributionNetwork':'physical','Material':'physical',
                'WorkPackage':'implementation','Deliverable':'implementation',
                'ImplementationEvent':'implementation','Plateau':'implementation','Gap':'implementation',
            }

            for layer, elements in elements_by_layer.items():
                persisted_by_layer[layer] = []
                for el_data in elements:
                    try:
                        el_type = el_data.get("type", "Unknown")
                        el_name = el_data.get("name", "Unnamed")
                        el_desc = el_data.get("description", "")
                        el_source = el_data.get("source", "derived")
                        acm_domain = TYPE_TO_ACM_DOMAIN.get(el_type, "APP")
                        # Correct layer if LLM placed element in wrong bucket
                        layer = _CANONICAL_LAYER.get(el_type, layer)

                        # ── Quality gate (zero-touch pipeline) ────────────
                        # existing/pattern elements bypass the classifier (always accept)
                        if el_source not in ("existing", "pattern"):
                            _qr = classify_element(el_data)
                            if _qr["verdict"] == "reject":
                                _auto_rejected += 1
                                _rejected_reasons.append({
                                    "name": el_name, "type": el_type,
                                    "reasons": _qr["reasons"], "score": _qr["score"],
                                })
                                continue
                            _is_auto_accept = (_qr["verdict"] == "accept")
                        else:
                            _is_auto_accept = True

                        # 1. Check if ArchiMateElement with same name+type exists
                        existing = ArchiMateElement.query.filter_by(
                            name=el_name, type=el_type
                        ).first()

                        if existing:
                            element = existing
                            el_source = "existing"
                        else:
                            # Create new ArchiMateElement in the global catalog
                            element = ArchiMateElement(
                                name=el_name,
                                type=el_type,
                                layer=layer,
                                description=el_desc,
                                scope="application",
                                acm_domain=acm_domain,
                                acm_source="journey",
                                architecture_id=arch_model.id,
                                organization_id=_org_id,
                            )
                            db.session.add(element)
                            db.session.flush()  # Get element.id

                        name_to_element_id[el_name.lower().strip()] = element.id

                        # Track quality metrics for response summary.
                        # SAE junction is NOT created here — it is created only when
                        # the user accepts a proposal (single: accept_proposal, bulk:
                        # batch_accept_proposals → confirm_domain → DomainPromotionService).
                        if _is_auto_accept:
                            _auto_accepted += 1
                        else:
                            _pending_review += 1

                        # Track coverage for gap analysis
                        _cap_src = el_data.get("capability_source") or ""
                        if _cap_src:
                            _coverage.setdefault(_cap_src, {})
                            _coverage[_cap_src][layer] = _coverage[_cap_src].get(layer, 0) + 1

                        # 3. Create SolutionBlueprintProposal (for Steps 4/5/6)
                        existing_proposal = SolutionBlueprintProposal.query.filter_by(
                            solution_id=self.solution_id,
                            name=el_name,
                            archimate_type=el_type,
                        ).first()
                        if not existing_proposal:
                            # Seed properties using PropertyService sensible defaults for this ArchiMate type,
                            # then override with any LLM-derived values below.
                            from app.modules.architecture_assistant.property_service import PropertyService
                            acm_props = PropertyService().get_default_properties(el_type)
                            # Override build_or_buy based on element origin
                            acm_props["build_or_buy"] = {
                                "value": "existing" if el_source == "existing" else acm_props.get("build_or_buy", {}).get("value", "build"),
                                "source": "derived" if el_source == "existing" else "default",
                            }
                            # Propagate LLM-generated data classification and PII flags
                            if el_data.get("data_classification"):
                                acm_props["data_classification"] = {
                                    "value": el_data["data_classification"], "source": "llm"
                                }
                            if el_data.get("contains_pii") is not None:
                                acm_props["contains_pii"] = {
                                    "value": el_data["contains_pii"], "source": "llm"
                                }
                            if el_data.get("capability_source"):
                                acm_props["capability_source"] = {
                                    "value": el_data["capability_source"], "source": "derived"
                                }
                            from app.services.solution_blueprint_service import (
                                create_solution_blueprint_proposal,
                            )
                            proposal = create_solution_blueprint_proposal(
                                solution_id=self.solution_id,
                                archimate_type=el_type,
                                name=el_name,
                                description=el_desc,
                                source="journey",
                                status="pending",
                                acm_domain=acm_domain,
                                is_baseline=False,
                                promoted_element_id=element.id,
                                acm_properties=acm_props,
                                organization_id=_org_id,
                            )

                        # Flush after each element so partial persistence works
                        db.session.flush()

                        # Build persisted element dict (with DB id) for the response
                        persisted_by_layer[layer].append({
                            "id": element.id,
                            "type": el_type,
                            "name": el_name,
                            "description": el_desc,
                            "source": el_source,
                            "acm_domain": acm_domain,
                            "data_classification": el_data.get("data_classification"),
                            "contains_pii": el_data.get("contains_pii"),
                        })
                    except Exception as el_err:
                        logger.error(
                            "Failed to persist element %s (%s) for solution %d: %s",
                            el_data.get("name"), el_data.get("type"), self.solution_id, el_err,
                        )
                        db.session.rollback()
                        # Re-establish arch_model reference after rollback
                        arch_model = ArchitectureModel.query.filter_by(solution_id=self.solution_id).first()
                        continue

            # ── Zero-touch gap-fill pass ──────────────────────────────
            # Identify (capability, layer) pairs with zero elements and request
            # a targeted LLM top-up.  Only runs when capabilities were tracked.
            REQUIRED_CAP_LAYERS = ("motivation", "strategy", "business", "application", "technology")
            if _coverage:
                _gaps = []
                for _cap_name, _layer_counts in _coverage.items():
                    for _req_layer in REQUIRED_CAP_LAYERS:
                        if not _layer_counts.get(_req_layer):
                            _gaps.append({"capability": _cap_name, "layer": _req_layer})

                if _gaps:
                    logger.info("Gap-fill: %d (capability, layer) pairs missing — running targeted LLM", len(_gaps))
                    try:
                        from app.modules.architecture_assistant.architecture_generation import ArchitectureGenerationService
                        _gen_svc = ArchitectureGenerationService()
                        # Build flat existing element list for dedup seeding
                        _existing_flat = []
                        for _l, _els in persisted_by_layer.items():
                            _existing_flat.extend(_els)
                        _problem_summary = problem_summary
                        _gap_result = _gen_svc.generate_gap_fill(
                            gaps=_gaps,
                            problem_summary=_problem_summary,
                            existing_elements=_existing_flat,
                        )
                        _gap_els_by_layer = _gap_result.get("elements_by_layer", {})

                        for _gl, _gel_list in _gap_els_by_layer.items():
                            for _gel in _gel_list:
                                try:
                                    _gel_name = _gel.get("name", "Unnamed")
                                    _gel_type = _gel.get("type", "Unknown")
                                    _qr2 = classify_element(_gel)
                                    if _qr2["verdict"] == "reject":
                                        _auto_rejected += 1
                                        continue
                                    _acm_dom2 = TYPE_TO_ACM_DOMAIN.get(_gel_type, "APP")
                                    _existing2 = ArchiMateElement.query.filter_by(name=_gel_name, type=_gel_type).first()
                                    if _existing2:
                                        _el2 = _existing2
                                    else:
                                        _el2 = ArchiMateElement(
                                            name=_gel_name, type=_gel_type, layer=_gl,
                                            description=_gel.get("description", ""),
                                            scope="application", acm_domain=_acm_dom2,
                                            acm_source="journey_gap_fill",
                                            architecture_id=arch_model.id,
                                            organization_id=_org_id,
                                        )
                                        db.session.add(_el2)
                                        db.session.flush()
                                    name_to_element_id[_gel_name.lower().strip()] = _el2.id
                                    # Gap-fill elements also go through proposals — not directly into SAE.
                                    _existing_proposal2 = SolutionBlueprintProposal.query.filter_by(
                                        solution_id=self.solution_id, name=_gel_name,
                                        archimate_type=_gel_type,
                                    ).first()
                                    if not _existing_proposal2:
                                        from app.modules.architecture_assistant.property_service import PropertyService
                                        from app.services.solution_blueprint_service import (
                                            create_solution_blueprint_proposal,
                                        )
                                        _acm_props2 = PropertyService().get_default_properties(_gel_type)
                                        _proposal2 = create_solution_blueprint_proposal(
                                            solution_id=self.solution_id,
                                            archimate_type=_gel_type,
                                            name=_gel_name,
                                            description=_gel.get("description", ""),
                                            source="journey_gap_fill",
                                            status="pending",
                                            acm_domain=_acm_dom2,
                                            is_baseline=False,
                                            promoted_element_id=_el2.id,
                                            acm_properties=_acm_props2,
                                            organization_id=_org_id,
                                        )
                                        _auto_accepted += 1
                                        persisted_by_layer.setdefault(_gl, []).append({
                                            "id": _el2.id, "type": _gel_type, "name": _gel_name,
                                            "description": _gel.get("description", ""),
                                            "source": "derived", "acm_domain": _acm_dom2,
                                        })
                                    db.session.flush()
                                except Exception as _ge_err:
                                    logger.warning("Gap-fill element persist failed: %s", _ge_err)
                                    db.session.rollback()
                    except Exception as _gf_err:
                        logger.warning("Gap-fill pass failed (non-fatal): %s", _gf_err)

            _auto_quality = {
                "auto_accepted": _auto_accepted,
                "pending_review": _pending_review,
                "auto_rejected": _auto_rejected,
                "rejected_reasons": _rejected_reasons[:50],  # cap for response size
                "coverage_matrix": _coverage,
            }

            # ── Fix 3: Cap + in-memory dedup before DB loop ──────────────
            # Priority: canonical chain types first, then all others
            CANONICAL_REL_TYPES = {
                "realization", "serving", "association", "aggregation",
                "composition", "assignment", "triggering", "flow",
            }
            canonical_rels = [r for r in raw_relationships if r.get("type") in CANONICAL_REL_TYPES]
            other_rels = [r for r in raw_relationships if r.get("type") not in CANONICAL_REL_TYPES]
            capped_relationships = (canonical_rels + other_rels)[:300]

            seen_rel_keys: set = set()
            deduplicated_relationships = []
            for _r in capped_relationships:
                _rk = (
                    _r.get("source_name", "").lower().strip(),
                    _r.get("target_name", "").lower().strip(),
                    _r.get("type", ""),
                )
                if _rk not in seen_rel_keys:
                    seen_rel_keys.add(_rk)
                    deduplicated_relationships.append(_r)
            # ── End Fix 3 ────────────────────────────────────────────────

            # ── Persist relationships ───────────────────────────────────
            persisted_rels = 0
            for rel_data in deduplicated_relationships:
                source_name = rel_data.get("source_name", "")
                target_name = rel_data.get("target_name", "")
                rel_type = rel_data.get("type", "association")
                rel_desc = rel_data.get("description", "")

                source_id = name_to_element_id.get(source_name.lower().strip())
                target_id = name_to_element_id.get(target_name.lower().strip())

                if source_id and target_id:
                    existing_rel = ArchiMateRelationship.query.filter_by(
                        source_id=source_id,
                        target_id=target_id,
                        type=rel_type,
                    ).first()
                    if not existing_rel:
                        rel = ArchiMateRelationship(
                            source_id=source_id,
                            target_id=target_id,
                            type=rel_type,
                            description=rel_desc,
                            architecture_id=arch_model.id,
                            connection_spec={"engine_run_id": engine_run_id},
                            organization_id=_org_id,
                        )
                        db.session.add(rel)
                        persisted_rels += 1

            # Finalize the run record before committing
            try:
                gen_run.status = "completed"
                gen_run.completed_at = __import__("datetime").datetime.utcnow()
                gen_run.element_count = total
                gen_run.relationship_count = persisted_rels
            except Exception as exc:
                logger.debug("suppressed error in JourneyOrchestrator.generate_architecture (app/modules/architecture_assistant/journey_orchestrator.py): %s", exc)  # gen_run may not exist if flush failed earlier

            db.session.commit()
            logger.info(
                "Persisted %d elements + %d relationships for solution %d (run=%s)",
                total, persisted_rels, self.solution_id, engine_run_id,
            )

            # ── Cross-layer relationship wiring pass ──────────────────────
            # For every pair of adjacent ArchiMate layers that have elements,
            # create ArchitectureInferenceRelationship rows so the Sankey and
            # accuracy endpoint have real links instead of falling back to
            # synthesis mode.  Round-robin pairs ensure every element has at
            # least one upstream or downstream connection.
            # Canonical cross-layer rel types (ArchiMate 3.2 §5):
            #   motivation → strategy  : realization
            #   strategy   → business  : realization
            #   business   → application: serving
            #   application→ technology : serving
            #   technology → implementation: serving
            _LAYER_ORDER = [
                "motivation", "strategy", "business",
                "application", "technology", "implementation",
            ]
            _XREL_TYPE = {
                ("motivation", "strategy"): "realization",
                ("strategy", "business"): "realization",
                ("business", "application"): "serving",
                ("application", "technology"): "serving",
                ("technology", "implementation"): "serving",
            }
            try:
                from app.models.architecture_inference_relationship import ArchitectureInferenceRelationship
                _wired = 0
                for _li, _src_layer in enumerate(_LAYER_ORDER[:-1]):
                    _tgt_layer = _LAYER_ORDER[_li + 1]
                    _src_els = persisted_by_layer.get(_src_layer, [])
                    _tgt_els = persisted_by_layer.get(_tgt_layer, [])
                    if not _src_els or not _tgt_els:
                        continue
                    _rel_type = _XREL_TYPE[(_src_layer, _tgt_layer)]
                    _n_tgt = len(_tgt_els)
                    for _idx, _src_el in enumerate(_src_els):
                        _tgt_el = _tgt_els[_idx % _n_tgt]
                        # Skip if already wired in either direction
                        _already = ArchitectureInferenceRelationship.query.filter_by(
                            source_id=_src_el["id"],
                            target_id=_tgt_el["id"],
                        ).first()
                        if _already:
                            continue
                        _air = ArchitectureInferenceRelationship(
                            architecture_id=arch_model.id if arch_model else None,
                            source_type="ArchiMateElement",
                            source_id=_src_el["id"],
                            target_type="ArchiMateElement",
                            target_id=_tgt_el["id"],
                            rel_type=_rel_type,
                            source_tag="auto_wired",
                            confidence=0.5,
                            inference_pass=2,
                            rule_name="cross_layer_wiring",
                        )
                        db.session.add(_air)
                        _wired += 1
                if _wired:
                    db.session.commit()
                    logger.info("Cross-layer wiring: %d AIR rows for solution %d", _wired, self.solution_id)
            except Exception as _wire_err:
                logger.warning("Cross-layer wiring pass failed (non-fatal): %s", _wire_err)
                try:
                    db.session.rollback()
                except Exception as exc:
                    logger.debug("suppressed error in JourneyOrchestrator.generate_architecture (app/modules/architecture_assistant/journey_orchestrator.py): %s", exc)
            # ── End cross-layer wiring ────────────────────────────────────

        except Exception as e:
            db.session.rollback()
            logger.error("Failed to persist architecture elements: %s", e, exc_info=True)
            try:
                gen_run.status = "failed"
                gen_run.error_message = str(e)
                gen_run.completed_at = __import__("datetime").datetime.utcnow()
                db.session.commit()
            except Exception as exc:
                logger.debug("suppressed error in JourneyOrchestrator.generate_architecture (app/modules/architecture_assistant/journey_orchestrator.py): %s", exc)
            # Still return the generated data even if persistence fails
            persisted_by_layer = elements_by_layer

        # ── Vendor product ArchiMate injection ────────────────────────
        # For elements marked source:"existing" that match vendor products,
        # inject the vendor's full ArchiMate representation
        vendor_injection_count = 0
        try:
            from app.models.vendor.vendor_organization import VendorProduct
            from app.modules.vendors.services.vendor_product_archimate_generator import VendorProductArchiMateGenerator

            for layer_els in persisted_by_layer.values():
                for el in layer_els:
                    if el.get("source") != "existing":
                        continue
                    # Match by name against vendor products
                    product = VendorProduct.query.filter(
                        VendorProduct.name.ilike(f"%{el['name']}%")
                    ).first()
                    if product:
                        try:
                            generator = VendorProductArchiMateGenerator(product.id)
                            vendor_elements = generator.generate_all()
                            vendor_injection_count += len(vendor_elements) if vendor_elements else 0
                            logger.info("Injected %d vendor elements for product '%s'",
                                        len(vendor_elements) if vendor_elements else 0, product.name)
                        except Exception as vg_err:
                            logger.debug("Vendor generation for '%s' failed: %s", product.name, vg_err)
        except Exception as v_err:
            logger.debug("Vendor product injection skipped: %s", v_err)

        if vendor_injection_count > 0:
            logger.info("Total vendor ArchiMate elements injected: %d", vendor_injection_count)

        # Use persisted data (with DB ids) for the response
        result_by_layer = persisted_by_layer if persisted_by_layer else elements_by_layer

        # Validation: count by layer, flag missing/thin layers
        issues = []
        required_layers = ("motivation", "strategy", "business", "application", "technology")
        recommended_layers = ("implementation",)
        all_check_layers = required_layers + recommended_layers

        # Minimum element thresholds per layer for a real architecture
        MIN_ELEMENTS = {
            "motivation": 6, "strategy": 3, "business": 8,
            "application": 8, "technology": 6, "implementation": 3,
        }

        for layer in all_check_layers:
            count = len(result_by_layer.get(layer, []))
            min_expected = MIN_ELEMENTS.get(layer, 1)
            severity = "required" if layer in required_layers else "recommended"
            if count == 0:
                issues.append({
                    "element_name": layer.title() + " Layer",
                    "missing_type": "any element",
                    "severity": severity,
                })
            elif count < min_expected:
                issues.append({
                    "element_name": layer.title() + " Layer",
                    "missing_type": f"thin ({count}/{min_expected} minimum)",
                    "severity": "warning",
                })

        validation = {
            "issues": issues,
            "completeness": {
                layer: {
                    "percentage": min(100, round(len(result_by_layer.get(layer, [])) / max(MIN_ELEMENTS.get(layer, 1), 1) * 100)),
                    "complete": len(result_by_layer.get(layer, [])),
                    "total": max(MIN_ELEMENTS.get(layer, 1), len(result_by_layer.get(layer, []))),
                }
                for layer in all_check_layers
            },
            "overall": round(100 * (1 - len([i for i in issues if i["severity"] == "required"]) / len(required_layers))),
        }

        logger.info("Architecture generated: %d elements, %d%% complete", total, validation["overall"])

        # ── Semantic quality check: type→layer consistency ─────────────
        # Detect elements whose ArchiMate type belongs to a different layer than the
        # layer bucket they were placed in (e.g. ApplicationComponent in motivation).
        # This is distinct from the VALID_ARCHIMATE_TYPES check above (which only checks
        # that the type string is a known ArchiMate type, not that it's in the right layer).
        _TYPE_TO_CANONICAL_LAYER = {
            "Stakeholder": "motivation", "Driver": "motivation", "Assessment": "motivation",
            "Goal": "motivation", "Outcome": "motivation", "Principle": "motivation",
            "Requirement": "motivation", "Constraint": "motivation",
            "Value": "motivation", "Meaning": "motivation",
            "Capability": "strategy", "CourseOfAction": "strategy",
            "ValueStream": "strategy", "Resource": "strategy",
            "BusinessActor": "business", "BusinessRole": "business",
            "BusinessCollaboration": "business", "BusinessInterface": "business",
            "BusinessProcess": "business", "BusinessFunction": "business",
            "BusinessInteraction": "business", "BusinessEvent": "business",
            "BusinessService": "business", "BusinessObject": "business",
            "Contract": "business", "Representation": "business", "Product": "business",
            "ApplicationComponent": "application", "ApplicationCollaboration": "application",
            "ApplicationInterface": "application", "ApplicationFunction": "application",
            "ApplicationInteraction": "application", "ApplicationProcess": "application",
            "ApplicationEvent": "application", "ApplicationService": "application",
            "DataObject": "application",
            "Node": "technology", "Device": "technology", "SystemSoftware": "technology",
            "TechnologyCollaboration": "technology", "TechnologyInterface": "technology",
            "Path": "technology", "CommunicationNetwork": "technology",
            "TechnologyFunction": "technology", "TechnologyProcess": "technology",
            "TechnologyInteraction": "technology", "TechnologyEvent": "technology",
            "TechnologyService": "technology", "Artifact": "technology",
            "Equipment": "physical", "Facility": "physical",
            "DistributionNetwork": "physical", "Material": "physical",
            "WorkPackage": "implementation", "Deliverable": "implementation",
            "ImplementationEvent": "implementation", "Plateau": "implementation",
            "Gap": "implementation",
        }
        type_layer_mismatches = []
        for _layer, _els in result_by_layer.items():
            for _el in _els:
                canonical = _TYPE_TO_CANONICAL_LAYER.get(_el.get("type"))
                if canonical and canonical != _layer:
                    type_layer_mismatches.append({
                        "name": _el.get("name", "?"),
                        "type": _el.get("type"),
                        "placed_in": _layer,
                        "should_be": canonical,
                    })
        if type_layer_mismatches:
            logger.warning(
                "Semantic quality: %d type→layer mismatches found (e.g. %s in '%s' layer)",
                len(type_layer_mismatches),
                type_layer_mismatches[0]["name"],
                type_layer_mismatches[0]["placed_in"],
            )

        # ── Canonical chain coverage ────────────────────────────────────
        # Check what fraction of the required canonical chain links are realised
        # by the relationships that were saved for this solution.
        _chain_coverage = {"covered": 0, "required": 0, "applicable": 0, "pct": 0, "missing_pairs": []}
        try:
            from app.modules.architecture.services.inference_rules_registry import CANONICAL_CHAIN
            from app.models.archimate_core import ArchiMateRelationship
            from app.models.architecture_inference_relationship import ArchitectureInferenceRelationship

            # Build type/name/id lookup from persisted response elements.
            _name_to_type: dict[str, str] = {}
            _id_to_type: dict[int, str] = {}
            _type_to_ids: dict[str, list[int]] = {}
            for _layer, _els in result_by_layer.items():
                for _el in _els:
                    if _el.get("name") and _el.get("type"):
                        _name_to_type[_el["name"]] = _el["type"]
                    if _el.get("id") and _el.get("type"):
                        _eid = int(_el["id"])
                        _etype = _el["type"]
                        _id_to_type[_eid] = _etype
                        _type_to_ids.setdefault(_etype, []).append(_eid)

            # Build a set of (source_type, target_type) pairs from:
            # - raw generated relationships (name-resolved)
            # - persisted ArchiMateRelationship rows
            # - persisted ArchitectureInferenceRelationship rows
            _rel_pairs: set[tuple[str, str]] = set()
            for _rel in raw_relationships:
                src_t = _name_to_type.get(_rel.get("source_name", ""))
                tgt_t = _name_to_type.get(_rel.get("target_name", ""))
                if src_t and tgt_t:
                    _rel_pairs.add((src_t, tgt_t))

            if arch_model_id:
                for _r in ArchiMateRelationship.query.filter_by(architecture_id=arch_model_id).all():
                    _st = _id_to_type.get(_r.source_id)
                    _tt = _id_to_type.get(_r.target_id)
                    if _st and _tt:
                        _rel_pairs.add((_st, _tt))
                for _r in ArchitectureInferenceRelationship.query.filter_by(architecture_id=arch_model_id).all():
                    _st = _id_to_type.get(_r.source_id)
                    _tt = _id_to_type.get(_r.target_id)
                    if _st and _tt:
                        _rel_pairs.add((_st, _tt))

            required_links = [(p, c, m) for p, c, m in CANONICAL_CHAIN if m.get("required")]
            # Only score required links that are applicable to this architecture instance.
            applicable_links = [
                (p, c, m)
                for p, c, m in required_links
                if _type_to_ids.get(p) and _type_to_ids.get(c)
            ]

            # Deterministic chain repair: if both required endpoint types exist but no pair
            # is present, auto-wire one AIR edge so downstream steps have a usable chain.
            _autowired_pairs = 0
            if arch_model_id:
                for p, c, m in applicable_links:
                    if (p, c) in _rel_pairs:
                        continue
                    _src_id = _type_to_ids[p][0]
                    _tgt_id = _type_to_ids[c][0]
                    _already = ArchitectureInferenceRelationship.query.filter_by(
                        architecture_id=arch_model_id,
                        source_id=_src_id,
                        target_id=_tgt_id,
                    ).first()
                    if _already:
                        _rel_pairs.add((p, c))
                        continue
                    _air_rel = ArchitectureInferenceRelationship(
                        architecture_id=arch_model_id,
                        source_type="ArchiMateElement",
                        source_id=_src_id,
                        target_type="ArchiMateElement",
                        target_id=_tgt_id,
                        rel_type=m.get("type", "association"),
                        source_tag="auto_wired_required_chain",
                        confidence=0.6,
                        inference_pass=2,
                        rule_name="required_chain_coverage",
                    )
                    db.session.add(_air_rel)
                    _rel_pairs.add((p, c))
                    _autowired_pairs += 1
                if _autowired_pairs:
                    db.session.commit()
                    logger.info("Canonical chain auto-wired %d missing required pair(s)", _autowired_pairs)

            covered = sum(1 for p, c, _ in applicable_links if (p, c) in _rel_pairs)
            _chain_coverage = {
                "covered": covered,
                "required": len(required_links),
                "applicable": len(applicable_links),
                "pct": round(100 * covered / len(applicable_links)) if applicable_links else 100,
                "missing_pairs": [
                    {"from": p, "to": c}
                    for p, c, _ in applicable_links
                    if (p, c) not in _rel_pairs
                ],
            }
            logger.info(
                "Canonical chain coverage: %d/%d applicable required links (%d%%, total required=%d)",
                covered, len(applicable_links), _chain_coverage["pct"], len(required_links),
            )
        except Exception as _cc_err:
            logger.debug("Chain coverage calculation failed (non-fatal): %s", _cc_err)

        # ── SEM-001: Orphan detection ────────────────────────────────────
        # Elements with zero relationships in raw_relationships (LLM-generated).
        # These are structural islands — no semantic traceability.
        _names_in_rels: set[str] = set()
        for _r in raw_relationships:
            _sn = (_r.get("source_name") or "").lower().strip()
            _tn = (_r.get("target_name") or "").lower().strip()
            if _sn:
                _names_in_rels.add(_sn)
            if _tn:
                _names_in_rels.add(_tn)

        orphan_elements: list[dict] = []
        for _layer, _els in result_by_layer.items():
            for _el in _els:
                _el_name_lower = (_el.get("name") or "").lower().strip()
                if _el_name_lower and _el_name_lower not in _names_in_rels:
                    orphan_elements.append({
                        "name": _el.get("name"),
                        "type": _el.get("type"),
                        "layer": _layer,
                    })

        # ── SEM-001: Minimum population rules (flag, not reject) ─────────
        # Architectures missing key structural element types are incomplete.
        _thin_layers: list[dict] = []

        _motivation_els = result_by_layer.get("motivation", [])
        if not any(e.get("type") in ("Goal", "Requirement") for e in _motivation_els):
            _thin_layers.append({
                "layer": "motivation",
                "issue": "no Goal or Requirement elements",
                "severity": "high",
            })

        _business_els = result_by_layer.get("business", [])
        if not any(e.get("type") == "BusinessProcess" for e in _business_els):
            _thin_layers.append({
                "layer": "business",
                "issue": "no BusinessProcess elements",
                "severity": "high",
            })

        _app_els = result_by_layer.get("application", [])
        if not any(e.get("type") == "ApplicationComponent" for e in _app_els):
            _thin_layers.append({
                "layer": "application",
                "issue": "no ApplicationComponent elements",
                "severity": "high",
            })

        _tech_els = result_by_layer.get("technology", [])
        if not any(e.get("type") in ("TechnologyService", "Node") for e in _tech_els):
            _thin_layers.append({
                "layer": "technology",
                "issue": "no TechnologyService or Node elements",
                "severity": "high",
            })

        # ── SEM-001: Cross-layer connectivity check ──────────────────────
        # For each adjacent layer pair, check if at least one relationship
        # crosses that boundary (using source/target names resolved to layers).
        _name_to_layer: dict[str, str] = {}
        for _layer, _els in result_by_layer.items():
            for _el in _els:
                _n = (_el.get("name") or "").lower().strip()
                if _n:
                    _name_to_layer[_n] = _layer

        _connected_pairs: set[tuple[str, str]] = set()
        for _r in raw_relationships:
            _sn = (_r.get("source_name") or "").lower().strip()
            _tn = (_r.get("target_name") or "").lower().strip()
            _sl = _name_to_layer.get(_sn)
            _tl = _name_to_layer.get(_tn)
            if _sl and _tl and _sl != _tl:
                _connected_pairs.add((_sl, _tl))
                _connected_pairs.add((_tl, _sl))  # bidirectional

        _ADJACENT_PAIRS = [
            ("motivation", "strategy"),
            ("strategy", "business"),
            ("business", "application"),
            ("application", "technology"),
            ("technology", "implementation"),
        ]
        disconnected_layer_pairs: list[dict] = []
        for _src_l, _tgt_l in _ADJACENT_PAIRS:
            _has_src = bool(result_by_layer.get(_src_l))
            _has_tgt = bool(result_by_layer.get(_tgt_l))
            if _has_src and _has_tgt:
                if (_src_l, _tgt_l) not in _connected_pairs and (_tgt_l, _src_l) not in _connected_pairs:
                    disconnected_layer_pairs.append({
                        "from": _src_l,
                        "to": _tgt_l,
                        "severity": "high",
                    })

        # ── SEM-001: Duplicate (name, type) detection ────────────────────
        # Generation loop artifacts: same element generated multiple times
        # (e.g., from repeated capability expansion).
        _seen_name_type: dict[str, int] = {}
        duplicate_elements: list[dict] = []
        for _layer, _els in result_by_layer.items():
            for _el in _els:
                _key = ((_el.get("name") or "").lower().strip(), _el.get("type", ""))
                _seen_name_type[_key] = _seen_name_type.get(_key, 0) + 1
        for (_dup_name, _dup_type), _cnt in _seen_name_type.items():
            if _cnt > 1:
                duplicate_elements.append({
                    "name": _dup_name,
                    "type": _dup_type,
                    "count": _cnt,
                })

        if orphan_elements:
            logger.warning(
                "SEM-001: %d orphan elements (no relationships) for solution %d",
                len(orphan_elements), self.solution_id,
            )
        if _thin_layers:
            logger.warning(
                "SEM-001: %d thin layer(s) missing required element types: %s",
                len(_thin_layers),
                [f["layer"] for f in _thin_layers],
            )
        if disconnected_layer_pairs:
            logger.warning(
                "SEM-001: %d disconnected adjacent layer pair(s): %s",
                len(disconnected_layer_pairs),
                [(p["from"], p["to"]) for p in disconnected_layer_pairs],
            )
        if duplicate_elements:
            logger.warning(
                "SEM-001: %d duplicate (name, type) pairs found",
                len(duplicate_elements),
            )

        semantic_quality = {
            "type_layer_mismatches": type_layer_mismatches,
            "mismatch_count": len(type_layer_mismatches),
            "chain_coverage": _chain_coverage,
            "orphan_elements": orphan_elements[:50],  # cap for response size
            "orphan_count": len(orphan_elements),
            "thin_layers": _thin_layers,
            "disconnected_layer_pairs": disconnected_layer_pairs,
            "duplicate_elements": duplicate_elements[:20],
            "duplicate_count": len(duplicate_elements),
        }

        # ── Post-generation: run inference engine to fill chain gaps ──
        inference_stats = {"elements_created": 0, "relationships_created": 0}
        try:
            from app.models.archimate_core import ArchitectureModel
            arch_model = ArchitectureModel.query.filter_by(solution_id=self.solution_id).first()
            if arch_model:
                from app.modules.architecture.services.inference_engine_service import ArchiMateInferenceEngine
                inf_result = ArchiMateInferenceEngine.generate_architecture(arch_model.id)
                inference_stats["elements_created"] = len(inf_result.elements_created)
                inference_stats["relationships_created"] = len(inf_result.relationships_created)
                if inference_stats["elements_created"] > 0 or inference_stats["relationships_created"] > 0:
                    logger.info(
                        "Inference engine filled %d elements + %d relationships for solution %d",
                        inference_stats["elements_created"],
                        inference_stats["relationships_created"],
                        self.solution_id,
                    )
        except Exception as inf_err:
            logger.warning("Inference engine pass failed (non-fatal): %s", inf_err)

        return {
            "generation": gen_result,
            "validation": validation,
            "elements_by_layer": result_by_layer,
            "relationships": raw_relationships,
            "inference": inference_stats,
            "semantic_quality": semantic_quality,
            "auto_quality": locals().get("_auto_quality", {
                "auto_accepted": 0, "pending_review": 0, "auto_rejected": 0,
                "rejected_reasons": [], "coverage_matrix": {},
            }),
        }

    # ── Step 3B: Phase-Level Generation ─────────────────────────────

    def generate_phase(self, phase, dry_run=True) -> dict:
        """Generate missing elements for a specific TOGAF phase."""
        from app.modules.architecture_assistant.phase_generator import PhaseGeneratorService
        svc = PhaseGeneratorService()
        return svc.generate_phase(self.solution_id, phase, dry_run)

    def get_phase_status(self) -> dict:
        """Get completeness status for all TOGAF phases."""
        from app.modules.architecture_assistant.phase_generator import PhaseGeneratorService
        svc = PhaseGeneratorService()
        return svc.get_phase_status(self.solution_id)

    # ── Inline Element Editing ────────────────────────────────────────

    def update_element(self, element_id, updates) -> dict:
        """Update an ArchiMateElement's fields.

        Supports: name, description, type, layer, acm_properties (merged patch).
        """
        from app.models.archimate_core import ArchiMateElement
        from app.modules.architecture_assistant.property_service import PropertyService, PropertyValidationError

        element = ArchiMateElement.query.get(element_id)
        if not element:
            return {"error": f"Element {element_id} not found"}

        governed_values = None
        if "acm_properties" in updates and isinstance(updates["acm_properties"], dict):
            # The organisation's governed property definitions are checked
            # before anything on the element changes: a refused value stores
            # nothing, not even the other fields in the same patch.
            from app.modules.architecture_assistant.property_service import GovernedPropertyService

            governed_values, property_errors = GovernedPropertyService().validate_updates(
                updates.get("type") or element.type, updates["acm_properties"],
                organization_id=element.organization_id,
            )
            if property_errors:
                return {"error": " ".join(property_errors), "property_errors": property_errors}

        old_name = element.name
        old_type = element.type
        if "name" in updates and updates["name"]:
            element.name = updates["name"]
        if "description" in updates:
            element.description = updates["description"]
        if "type" in updates and updates["type"]:
            element.type = updates["type"]
        if "layer" in updates and updates["layer"]:
            element.layer = updates["layer"]
        if governed_values is not None:
            try:
                PropertyService().merge_element_properties(element, governed_values, source="user")
            except PropertyValidationError as exc:
                return {"error": str(exc), "status_code": 400}

        db.session.commit()

        # Log element_type corrections for prompt-hardening analysis (Layer 4).
        if old_type and element.type and old_type != element.type:
            try:
                import json
                import os
                from datetime import datetime, timezone
                from flask_login import current_user
                _log_path = os.path.join(
                    os.path.dirname(__file__), "..", "..", "..", "docs", "arch_correction_log.jsonl"
                )
                _log_path = os.path.normpath(_log_path)
                _entry = {
                    "solution_id": self.solution_id,
                    "element_id": element_id,
                    "element_name": element.name,
                    "original_type": old_type,
                    "corrected_type": element.type,
                    "layer": element.layer,
                    "corrected_at": datetime.now(timezone.utc).isoformat(),
                    "user_id": current_user.id if current_user.is_authenticated else None,
                }
                with open(_log_path, "a", encoding="utf-8") as _f:
                    _f.write(json.dumps(_entry) + "\n")
            except Exception as _log_err:
                logger.debug("Correction log write skipped: %s", _log_err)

        # Best-effort cascade rename in RoadmapTask descriptions
        if old_name != element.name:
            try:
                from app.models.roadmap import RoadmapTask
                tasks = RoadmapTask.query.filter(
                    RoadmapTask.description.contains(old_name)
                ).all()
                for task in tasks:
                    task.description = task.description.replace(old_name, element.name)
                if tasks:
                    db.session.commit()
            except Exception as e:
                logger.debug("Cascade rename skipped: %s", e)

        return {
            "id": element.id,
            "name": element.name,
            "description": element.description,
            "type": element.type,
            "layer": element.layer,
        }

    def delete_element(self, element_id) -> dict:
        """Delete an ArchiMateElement and its relationships from the journey architecture."""
        from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
        element = ArchiMateElement.query.get(element_id)
        if not element:
            return {"error": f"Element {element_id} not found"}

        name = element.name
        # Remove relationships first
        ArchiMateRelationship.query.filter(
            (ArchiMateRelationship.source_id == element_id) |
            (ArchiMateRelationship.target_id == element_id)
        ).delete(synchronize_session=False)
        db.session.delete(element)
        db.session.commit()
        return {"deleted": True, "id": element_id, "name": name}

    def create_element(self, data: dict) -> dict:
        """Create a new ArchiMateElement in this journey's architecture."""
        from app.models.archimate_core import ArchiMateElement, ArchitectureModel

        arch_model = ArchitectureModel.query.filter_by(solution_id=self.solution_id).first()
        if not arch_model:
            return {"error": "No architecture model for this solution"}

        element = ArchiMateElement(
            name=data["name"],
            type=data["type"],
            layer=data["layer"],
            description=data.get("description", ""),
            architecture_id=arch_model.id,
            acm_source="journey",
            acm_properties=data.get("acm_properties", {}),
        )
        db.session.add(element)
        db.session.commit()
        return {
            "id": element.id,
            "name": element.name,
            "type": element.type,
            "layer": element.layer,
        }

    def create_relationship(self, source_id: int, target_id: int, rel_type: str) -> dict:
        """Create an ArchiMate relationship between two elements in the journey architecture."""
        from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship, ArchitectureModel

        arch_model = ArchitectureModel.query.filter_by(solution_id=self.solution_id).first()
        if not arch_model:
            return {"error": "No architecture model for this solution"}

        # Verify both elements belong to this architecture
        src = ArchiMateElement.query.filter_by(id=source_id, architecture_id=arch_model.id).first()
        tgt = ArchiMateElement.query.filter_by(id=target_id, architecture_id=arch_model.id).first()
        if not src:
            return {"error": f"Source element {source_id} not found in this architecture"}
        if not tgt:
            return {"error": f"Target element {target_id} not found in this architecture"}

        rel = ArchiMateRelationship(
            source_id=source_id,
            target_id=target_id,
            type=rel_type,
            architecture_id=arch_model.id,
        )
        db.session.add(rel)
        db.session.commit()
        return {"id": rel.id, "source_id": source_id, "target_id": target_id, "type": rel_type}

    def regenerate_layer(self, layer: str, problem_summary: str, capabilities: list) -> dict:
        """Regenerate a single thin layer without re-running the full pipeline."""
        # Traceability id stamped onto every element this call derives. It was
        # referenced in spec_data below but never created in this method — only in
        # the full-pipeline path — so regenerating a layer raised NameError. Mirrors
        # the id generation used there.
        import uuid

        engine_run_id = str(uuid.uuid4())
        from app.modules.architecture_assistant.architecture_generation import (
            ALL_LAYERS,
            ArchitectureGenerationService,
        )
        from app.models.archimate_core import ArchiMateElement, ArchitectureModel
        from app.models.solution_archimate_element import SolutionArchiMateElement
        from app.models.solution_blueprint_proposal import SolutionBlueprintProposal

        if layer not in ALL_LAYERS:
            return {"error": f"Invalid layer: {layer}. Valid: {', '.join(ALL_LAYERS)}"}

        gen_svc = ArchitectureGenerationService()
        gen_result = gen_svc.generate_greenfield(
            capabilities=capabilities,
            problem_summary=problem_summary,
        )

        layer_elements = gen_result.get("elements_by_layer", {}).get(layer, [])
        if not layer_elements:
            return {"new_elements": 0, "elements": [], "message": f"LLM produced no {layer} elements"}

        TYPE_TO_ACM = {
            "Capability": "APP", "CourseOfAction": "APP", "ValueStream": "APP", "Resource": "APP",
            "WorkPackage": "DEV", "Deliverable": "DEV", "Plateau": "APP", "Gap": "APP",
            "Stakeholder": "UX", "Driver": "UX", "Goal": "UX", "Requirement": "SEC",
            "BusinessProcess": "APP", "BusinessService": "APP", "BusinessObject": "DATA",
            "ApplicationComponent": "APP", "ApplicationService": "APP", "DataObject": "DATA",
            "Node": "DEV", "SystemSoftware": "DEV", "TechnologyService": "DEV",
        }

        arch_model = ArchitectureModel.query.filter_by(solution_id=self.solution_id).first()
        if not arch_model:
            return {"error": "No architecture model — run full generation first"}

        persisted = []
        try:
            for el_data in layer_elements:
                el_name = el_data.get("name", "Unnamed")
                el_type = el_data.get("type", "Unknown")
                acm_domain = TYPE_TO_ACM.get(el_type, "APP")

                existing = ArchiMateElement.query.filter_by(name=el_name, type=el_type).first()
                if not existing:
                    element = ArchiMateElement(
                        name=el_name, type=el_type, layer=layer,
                        description=el_data.get("description", ""),
                        scope="application", acm_domain=acm_domain,
                        acm_source="journey_regen", architecture_id=arch_model.id,
                    )
                    db.session.add(element)
                    db.session.flush()
                else:
                    element = existing

                if not SolutionArchiMateElement.query.filter_by(
                    solution_id=self.solution_id, element_id=element.id
                ).first():
                    db.session.add(SolutionArchiMateElement(
                        solution_id=self.solution_id, element_id=element.id,
                        layer_type=layer, element_table="archimate_elements",
                        element_name=el_name, element_role="ai_derived",
                        is_new_element=(existing is None),
                        spec_data={"fields_status": "pending", "is_ai_derived": True, "engine_run_id": engine_run_id},
                    ))

                if not SolutionBlueprintProposal.query.filter_by(
                    solution_id=self.solution_id, name=el_name, archimate_type=el_type
                ).first():
                    db.session.add(SolutionBlueprintProposal(
                        solution_id=self.solution_id, archimate_type=el_type,
                        name=el_name, description=el_data.get("description", ""),
                        source="journey", status="promoted", acm_domain=acm_domain,
                        is_baseline=False, promoted_element_id=element.id,
                        acm_properties={
                            "build_or_buy": {"value": "TBD", "source": "default"},
                            "deployment_model": {"value": "TBD", "source": "default"},
                        },
                    ))

                persisted.append({"id": element.id, "name": el_name, "type": el_type})

            db.session.commit()
            logger.info("Regenerated %d %s-layer elements for solution %d",
                        len(persisted), layer, self.solution_id)
        except Exception as e:
            db.session.rollback()
            logger.error("Layer regeneration failed: %s", e)
            return {"error": str(e)}

        return {"new_elements": len(persisted), "layer": layer, "elements": persisted}

    # ── Graph-Based Traceability ──────────────────────────────────────

    def get_traceability_chains(self) -> list:
        """Get provenance-tracked traceability chains from the graph."""
        return self.graph.get_traceability_chains()

    # ── Step 4: Architecture Variants ─────────────────────────────────

    def generate_variants(self, architecture_elements, capabilities, problem_summary) -> dict:
        """Step 4: Identify decision points and generate architecture variants."""
        from app.modules.architecture_assistant.variant_generator import VariantGeneratorService
        svc = VariantGeneratorService()
        return svc.generate_variants(architecture_elements, capabilities, problem_summary, self.solution_id)

    def select_variant(self, decision_point_id, option_id, decision_points) -> dict:
        """Step 4: Apply a selected variant and persist risks."""
        from app.modules.architecture_assistant.variant_generator import VariantGeneratorService
        svc = VariantGeneratorService()
        return svc.select_variant(self.solution_id, decision_point_id, option_id, decision_points)

    # ── Step 5: Migration Planning ────────────────────────────────────

    def generate_migration_plan(self, architecture_elements, problem_summary, constraints=None) -> dict:
        """Step 5: Generate phased migration plan with work packages."""
        from app.modules.architecture_assistant.migration_planner import MigrationPlannerService
        svc = MigrationPlannerService()
        return svc.generate_plan(architecture_elements, problem_summary, self.solution_id, constraints)

    # ── Step 6: Full Validation ───────────────────────────────────────

    def validate(self) -> dict:
        """Step 6: Basic validation pass (legacy)."""
        return self.graph.validate_completeness()

    def get_full_validation(self, architecture_elements, capabilities, migration_plan=None) -> dict:
        """Step 6: Run comprehensive validation with compliance, governance, traceability."""
        from app.modules.architecture_assistant.validation_engine import ValidationEngineService
        svc = ValidationEngineService()
        return svc.full_validate(architecture_elements, capabilities, self.solution_id, migration_plan)

    def submit_to_arb(self, _validation_result=None) -> dict:
        """Fail closed; submission requires authenticated evidence attestations."""
        return {
            "success": False,
            "error": "legacy_submission_disabled",
            "reason_codes": ["canonical_submission_required"],
            "solution_id": self.solution_id,
        }

    def rebuild_relationships(self, problem_summary: str = "") -> dict:
        """Re-run Pass 2 relationship generation for existing elements without regenerating them.

        Loads all persisted elements for this solution, calls the LLM relationship
        prompt, and persists new relationships (skipping duplicates).
        Returns counts of new and total relationships.
        """
        from app.models.archimate_core import ArchiMateRelationship, ArchitectureModel
        from app.models.solution_archimate_element import SolutionArchiMateElement
        from app.models.archimate_core import ArchiMateElement
        from app.modules.architecture_assistant.architecture_generation import ArchitectureGenerationService, RELATIONSHIP_PROMPT
        from app.modules.ai_chat.services.llm_service import LLMService

        # Load architecture model for this solution
        arch_model = ArchitectureModel.query.filter_by(solution_id=self.solution_id).first()
        if not arch_model:
            return {"error": "No architecture model found — generate architecture first"}

        # Load all elements linked to this solution
        junctions = SolutionArchiMateElement.query.filter_by(solution_id=self.solution_id).all()
        if not junctions:
            return {"error": "No architecture elements found for this solution"}

        # Build name→id map (case-insensitive) and elements-by-layer for the prompt
        LAYER_MAP = {
            "Stakeholder": "motivation", "Driver": "motivation", "Assessment": "motivation",
            "Goal": "motivation", "Outcome": "motivation", "Principle": "motivation",
            "Requirement": "motivation", "Constraint": "motivation",
            "Meaning": "motivation", "Value": "motivation",
            "Capability": "strategy", "CourseOfAction": "strategy",
            "ValueStream": "strategy", "Resource": "strategy",
            "BusinessActor": "business", "BusinessRole": "business",
            "BusinessCollaboration": "business", "BusinessInterface": "business",
            "BusinessProcess": "business", "BusinessFunction": "business",
            "BusinessInteraction": "business", "BusinessEvent": "business",
            "BusinessService": "business", "BusinessObject": "business",
            "Representation": "business", "Product": "business", "Contract": "business",
            "ApplicationComponent": "application", "ApplicationCollaboration": "application",
            "ApplicationInterface": "application", "ApplicationFunction": "application",
            "ApplicationInteraction": "application", "ApplicationProcess": "application",
            "ApplicationEvent": "application", "ApplicationService": "application",
            "DataObject": "application",
            "Node": "technology", "Device": "technology", "SystemSoftware": "technology",
            "TechnologyCollaboration": "technology", "TechnologyInterface": "technology",
            "Path": "technology", "CommunicationNetwork": "technology",
            "TechnologyFunction": "technology", "TechnologyProcess": "technology",
            "TechnologyInteraction": "technology", "TechnologyEvent": "technology",
            "TechnologyService": "technology", "Artifact": "technology",
            "Equipment": "physical", "Facility": "physical",
            "DistributionNetwork": "physical", "Material": "physical",
            "WorkPackage": "implementation", "Deliverable": "implementation",
            "ImplementationEvent": "implementation",
            "Plateau": "implementation", "Gap": "implementation",
        }

        name_to_id = {}
        elements_by_layer = {item: [] for item in ["motivation", "strategy", "business", "application", "technology", "implementation"]}

        for j in junctions:
            el = ArchiMateElement.query.get(j.element_id)
            if not el:
                continue
            el_type = getattr(el, 'element_type', None) or getattr(el, 'type', None) or ''
            layer = LAYER_MAP.get(el_type, "application")
            name_to_id[el.name.lower().strip()] = el.id
            elements_by_layer[layer].append({"name": el.name, "type": el_type, "description": el.description or ""})

        total_elements = sum(len(v) for v in elements_by_layer.values())
        if total_elements == 0:
            return {"error": "No elements resolved from junctions"}

        # Use problem_summary from solution if not provided
        if not problem_summary:
            from app.models.solution_models import Solution
            sol = Solution.query.get(self.solution_id)
            problem_summary = (sol.problem_clarification or sol.description or "") if sol else ""

        # Build elements summary string (same format as Pass 2)
        gen_svc = ArchitectureGenerationService()
        elements_summary = gen_svc._build_elements_summary(elements_by_layer)
        target_rels = max(total_elements * 2, 30)

        rel_prompt = RELATIONSHIP_PROMPT.format(
            problem_summary=problem_summary,
            elements_summary=elements_summary,
            target_rel_count=target_rels,
        )

        provider, model = LLMService._get_configured_provider()
        raw_text, _ = LLMService._call_llm(prompt=rel_prompt, model=model, provider=provider)
        if not raw_text:
            return {"error": "LLM returned empty response"}

        # Parse the relationship response. _parse_json_response targets layer-keyed
        # responses; relationship responses use {"relationships": [...]}.
        # Try direct JSON parse first, then strip markdown fences + retry.
        import json as _json
        import re as _re
        relationships_raw = []
        _text = raw_text.strip()
        _text = _re.sub(r'^```(?:json)?\s*', '', _text)
        _text = _re.sub(r'\s*```\s*$', '', _text)
        _start = _text.find('{')
        _end = _text.rfind('}') + 1
        if _start >= 0 and _end > _start:
            try:
                _parsed = _json.loads(_text[_start:_end])
                relationships_raw = _parsed.get("relationships", [])
            except _json.JSONDecodeError:
                # Try extracting the relationships array directly
                _m = _re.search(r'"relationships"\s*:\s*\[', _text)
                if _m:
                    try:
                        relationships_raw = _json.loads('[' + _text[_m.end():].split(']')[0] + ']')
                    except Exception as exc:
                        logger.debug("suppressed error in JourneyOrchestrator.rebuild_relationships (app/modules/architecture_assistant/journey_orchestrator.py): %s", exc)

        if not relationships_raw:
            logger.warning("rebuild_relationships: LLM response produced 0 relationships — raw: %s", raw_text[:300])
            return {"new_relationships": 0, "total_relationships": 0, "warning": "LLM returned no parseable relationships"}

        # Persist new relationships
        new_count = 0
        for rel_data in relationships_raw:
            src = rel_data.get("source_name", "").lower().strip()
            tgt = rel_data.get("target_name", "").lower().strip()
            rel_type = rel_data.get("type", "association")
            source_id = name_to_id.get(src)
            target_id = name_to_id.get(tgt)
            if not source_id or not target_id or source_id == target_id:
                continue
            exists = ArchiMateRelationship.query.filter_by(
                source_id=source_id, target_id=target_id, type=rel_type
            ).first()
            if not exists:
                db.session.add(ArchiMateRelationship(
                    source_id=source_id,
                    target_id=target_id,
                    type=rel_type,
                    description=rel_data.get("description", ""),
                    architecture_id=arch_model.id,
                ))
                new_count += 1

        db.session.commit()
        total_rels = ArchiMateRelationship.query.filter(
            ArchiMateRelationship.source_id.in_(list(name_to_id.values()))
        ).count()
        logger.info("rebuild_relationships: %d new, %d total for solution %d", new_count, total_rels, self.solution_id)
        return {"new_relationships": new_count, "total_relationships": total_rels}

    # ── ACM Domain-Driven Architecture ────────────────────────────────

    def get_promoted_elements(self):
        """Return all promoted/accepted proposals for confirmed domains, grouped by ArchiMate layer."""
        from app.models.solution_blueprint_proposal import SolutionBlueprintProposal

        LAYER_MAP = {
            # Motivation
            "Requirement": "motivation", "Constraint": "motivation", "Principle": "motivation",
            "Goal": "motivation", "Driver": "motivation", "Assessment": "motivation",
            "Stakeholder": "motivation", "Value": "motivation", "Meaning": "motivation",
            "Outcome": "motivation",
            # Strategy
            "Capability": "strategy", "CourseOfAction": "strategy",
            "ValueStream": "strategy", "Resource": "strategy",
            # Business
            "BusinessRole": "business", "BusinessActor": "business", "BusinessProcess": "business",
            "BusinessFunction": "business", "BusinessService": "business", "BusinessObject": "business",
            "BusinessEvent": "business", "BusinessInterface": "business", "BusinessCollaboration": "business",
            "BusinessInteraction": "business", "Contract": "business", "Product": "business",
            "Representation": "business",
            # Application
            "ApplicationComponent": "application", "ApplicationService": "application",
            "ApplicationFunction": "application", "ApplicationInterface": "application",
            "ApplicationEvent": "application", "ApplicationProcess": "application",
            "ApplicationCollaboration": "application", "ApplicationInteraction": "application",
            "DataObject": "application",
            # Technology
            "Node": "technology", "Device": "technology", "SystemSoftware": "technology",
            "TechnologyService": "technology", "TechnologyFunction": "technology",
            "TechnologyInterface": "technology", "TechnologyProcess": "technology",
            "TechnologyEvent": "technology", "TechnologyCollaboration": "technology",
            "TechnologyInteraction": "technology", "Artifact": "technology",
            "CommunicationNetwork": "technology", "Path": "technology",
            # Implementation
            "WorkPackage": "implementation", "Deliverable": "implementation",
            "Plateau": "implementation", "Gap": "implementation",
            "ImplementationEvent": "implementation",
            # Physical
            "Equipment": "physical", "Facility": "physical",
            "DistributionNetwork": "physical", "Material": "physical",
        }

        proposals = SolutionBlueprintProposal.query.filter_by(
            solution_id=self.solution_id,
        ).filter(
            SolutionBlueprintProposal.status.in_(["promoted", "accepted"])
        ).order_by(SolutionBlueprintProposal.acm_domain, SolutionBlueprintProposal.id).all()

        by_layer = {
            "motivation": [], "strategy": [], "business": [],
            "application": [], "technology": [],
            "implementation": [], "physical": [],
        }
        for p in proposals:
            layer = LAYER_MAP.get(p.archimate_type, "application")
            if layer not in by_layer:
                by_layer[layer] = []
            by_layer[layer].append({
                "id": p.promoted_element_id or p.id,
                "proposal_id": p.id,
                "name": p.name,
                "type": p.archimate_type,
                "description": p.description or "",
                "source": p.source or "derived",
                "acm_domain": p.acm_domain,
                "is_baseline": p.is_baseline,
                "acm_properties": p.acm_properties or {},
            })

        total = sum(len(v) for v in by_layer.values())

        # Load relationships between promoted elements
        relationships = []
        try:
            from app.models.architecture_inference_relationship import ArchitectureInferenceRelationship
            from app.models.archimate_core import ArchitectureModel, ArchiMateRelationship
            arch_model = ArchitectureModel.query.filter_by(solution_id=self.solution_id).first()
            if arch_model:
                # Build id→name lookup from promoted elements
                id_to_name = {}
                promoted_ids = set()
                for layer_els in by_layer.values():
                    for el in layer_els:
                        if el.get("id"):
                            promoted_ids.add(el["id"])
                            id_to_name[el["id"]] = el["name"]

                # Load inference relationships (created by domain promotion + repair pass)
                rels = ArchitectureInferenceRelationship.query.filter_by(
                    architecture_id=arch_model.id,
                ).all()
                for r in rels:
                    src_name = id_to_name.get(r.source_id)
                    tgt_name = id_to_name.get(r.target_id)
                    if src_name and tgt_name:
                        relationships.append({
                            "source_id": r.source_id,
                            "source_name": src_name,
                            "target_id": r.target_id,
                            "target_name": tgt_name,
                            "type": r.rel_type,
                        })

                # Also load any ArchiMateRelationship rows (from LLM-driven generation)
                archimate_rels = ArchiMateRelationship.query.filter(
                    ArchiMateRelationship.source_id.in_(list(promoted_ids)),
                    ArchiMateRelationship.target_id.in_(list(promoted_ids)),
                ).all()
                existing_pairs = {(r["source_id"], r["target_id"]) for r in relationships}
                for r in archimate_rels:
                    if (r.source_id, r.target_id) not in existing_pairs:
                        src_name = id_to_name.get(r.source_id)
                        tgt_name = id_to_name.get(r.target_id)
                        if src_name and tgt_name:
                            relationships.append({
                                "source_id": r.source_id,
                                "source_name": src_name,
                                "target_id": r.target_id,
                                "target_name": tgt_name,
                                "type": r.type,
                            })
        except Exception as e:
            logger.debug("Could not load relationships: %s", e)

        return {
            "elements_by_layer": by_layer,
            "relationships": relationships,
            "total": total,
            "validation": {"overall": 100, "issues": []},
        }

    def populate_domains(self, enriched_brief, industry_overlay=None):
        """Populate all 7 ACM domains with baselines + LLM suggestions."""
        from app.modules.architecture_assistant.acm_domain_service import AcmDomainService
        svc = AcmDomainService()
        return svc.populate_domains(self.solution_id, enriched_brief, industry_overlay)

    def load_domains(self):
        """Reload domain data from DB (for session restore)."""
        from app.modules.architecture_assistant.acm_domain_service import AcmDomainService
        svc = AcmDomainService()
        return svc.load_domains(self.solution_id)

    def confirm_domain(self, domain_code):
        """Confirm a domain — promotes accepted proposals to ArchiMate elements."""
        from app.modules.architecture_assistant.domain_promotion import DomainPromotionService
        svc = DomainPromotionService()
        return svc.promote_domain(self.solution_id, domain_code)

    def update_domain_status(self, domain_code, status=None, justification=None, tier=None):
        """Update domain status, tier, or N/A justification."""
        from app.models.solution_domain_spec import SolutionDomainSpec
        spec = SolutionDomainSpec.query.filter_by(
            solution_id=self.solution_id, domain_code=domain_code
        ).first()
        if not spec:
            return {"error": "Domain spec not found"}
        if status:
            spec.status = status
        if justification is not None:
            spec.status_justification = justification
        if tier:
            spec.relevance_tier = tier
        if status == "confirmed":
            from datetime import datetime
            spec.confirmed_at = datetime.utcnow()
        db.session.commit()
        return spec.to_dict()

    def check_cross_domain(self, domain, archimate_type, element_name):
        """Evaluate cross-domain dependencies for an element."""
        from app.modules.architecture_assistant.cross_domain_engine import CrossDomainEngine
        engine = CrossDomainEngine()
        return {"dependencies": engine.evaluate(domain, archimate_type, element_name)}

    def get_domain_completeness(self):
        """Get completeness scores and blockers."""
        from app.modules.architecture_assistant.domain_completeness import DomainCompletenessService
        svc = DomainCompletenessService()
        return svc.score(self.solution_id)

    # ── Element Properties ────────────────────────────────────────

    def get_property_templates(self, archimate_type, domain=None, tier=None):
        """Get property templates for an element type."""
        from app.modules.architecture_assistant.property_service import PropertyService
        return PropertyService().get_templates_for_type(archimate_type, tier or "standard", domain)

    def generate_decision_rationale(self) -> dict:
        """Generate build/buy decision rationale for this solution's proposals.

        For each SolutionBlueprintProposal that still lacks a decision_rationale,
        ask the LLM for a build-vs-buy recommendation and a short justification
        grounded in the proposal's name/description, then persist the rationale
        and resolve the build_or_buy value (out of "TBD"). Commits per proposal so
        partial progress survives an interruption. Called by the fire-and-forget
        generate_decision_rationale_worker (decisions/generate route); previously
        this method did not exist, so the worker always failed silently and
        decisions/status never reached "complete".
        """
        import json as _json
        import re as _re

        from app import db
        from app.models.solution_blueprint_proposal import SolutionBlueprintProposal
        from app.services.llm_service import LLMService

        proposals = (
            SolutionBlueprintProposal.query.filter(
                SolutionBlueprintProposal.solution_id == self.solution_id,
                SolutionBlueprintProposal.decision_rationale.is_(None),
            )
            .limit(200)
            .all()
        )

        processed = 0
        for prop in proposals:
            props = dict(prop.acm_properties or {})
            bob = props.get("build_or_buy") or {}
            current = bob.get("value") if isinstance(bob, dict) else bob

            prompt = (
                "You are an enterprise architect making a build-vs-buy decision for a "
                "single solution component. Respond ONLY with JSON of the form "
                '{"decision": "build" | "buy" | "existing", "rationale": "2-3 sentence justification"}.\n\n'
                f"Component: {prop.name}\n"
                f"Description: {prop.description or '(none provided)'}\n"
                f"Tentative decision so far: {current or 'TBD'}\n"
            )
            try:
                text = LLMService.generate_from_prompt(prompt) or ""
                match = _re.search(r"\{.*\}", text, _re.DOTALL)
                data = _json.loads(match.group()) if match else {}
            except Exception:  # noqa: BLE001 - LLM/parse failure falls back below
                data = {}

            decision = (data.get("decision") or "").strip().lower()
            if decision not in ("build", "buy", "existing"):
                decision = current if current and current != "TBD" else "build"
            rationale = (data.get("rationale") or "").strip() or (
                f"Recommended to {decision} this component based on its scope and the solution context."
            )

            props["build_or_buy"] = {"value": decision, "source": "llm_decision"}
            prop.acm_properties = props  # reassign so SQLAlchemy detects the JSON change
            prop.decision_rationale = rationale
            db.session.add(prop)
            try:
                db.session.commit()
                processed += 1
            except Exception:  # noqa: BLE001
                db.session.rollback()

        return {"processed": processed, "total_candidates": len(proposals)}

    # ── Data-Driven Steps 4-6 (ACM flow) ────────────────────────

    def get_decision_points(self):
        """Step 4: Derive decision points from element properties.

        Groups promoted proposals by their build_or_buy and deployment_model
        properties to surface architecture decisions the architect already made.
        """
        from app.models.solution_blueprint_proposal import SolutionBlueprintProposal

        proposals = SolutionBlueprintProposal.query.filter_by(
            solution_id=self.solution_id,
        ).filter(
            SolutionBlueprintProposal.status.in_(["promoted", "accepted"])
        ).all()

        decisions = {}
        for p in proposals:
            props = p.acm_properties or {}
            # Extract property values (handle both {value, source} and plain values)
            def _val(key):
                v = props.get(key)
                if isinstance(v, dict):
                    return v.get("value", "")
                return v or ""

            build_buy = _val("build_or_buy")
            deploy = _val("deployment_model")
            avail = _val("availability_target")
            effort = _val("estimated_effort")
            cost = _val("estimated_cost_annual")
            vendor = _val("vendor_product")
            stack = _val("technology_stack")

            elem_info = {
                "id": p.promoted_element_id or p.id,
                "name": p.name,
                "type": p.archimate_type,
                "acm_domain": p.acm_domain,
                "description": p.description or "",
            }

            if build_buy and build_buy != "TBD":
                key = "build_or_buy"
                decisions.setdefault(key, {"name": "Build vs Buy", "type": key, "elements": []})
                decisions[key]["elements"].append({
                    **elem_info,
                    "value": build_buy,
                    "detail": vendor if build_buy in ("buy", "SaaS") else stack,
                    "effort": effort,
                    "cost": cost,
                })

            if deploy and deploy != "TBD":
                key = "deployment_model"
                decisions.setdefault(key, {"name": "Deployment Model", "type": key, "elements": []})
                decisions[key]["elements"].append({
                    **elem_info,
                    "value": deploy,
                    "detail": _val("hosting_target"),
                    "availability": avail,
                })

            if avail and avail != "TBD":
                key = "availability"
                decisions.setdefault(key, {"name": "Availability & SLA", "type": key, "elements": []})
                decisions[key]["elements"].append({
                    **elem_info,
                    "value": avail,
                    "detail": _val("performance_sla"),
                })

        return {"decision_points": list(decisions.values()), "total_elements": len(proposals)}

    def get_roadmap_data(self):
        """Step 5: Derive phased roadmap from element properties.

        Groups elements into phases based on dependencies and effort estimates.
        Phase order: Technology → Application → Business (infrastructure first).
        """
        from app.models.solution_blueprint_proposal import SolutionBlueprintProposal

        # Phase assignment: maps ArchiMate types to roadmap phases
        # Phase 0: Strategy alignment (why we're building)
        # Phase 1: Infrastructure foundation
        # Phase 2: Application & data layer
        # Phase 3: Business process integration
        # Phase 4: Implementation execution (work packages, plateaus)
        LAYER_ORDER = {
            # Strategy layer → Phase 0
            "Capability": 0, "CourseOfAction": 0, "ValueStream": 0, "Resource": 0,
            # Technology layer → Phase 1
            "Node": 1, "SystemSoftware": 1, "Device": 1,
            "TechnologyService": 1, "CommunicationNetwork": 1, "Artifact": 1,
            "Path": 1,
            # Application layer → Phase 2
            "DataObject": 2, "ApplicationComponent": 2, "ApplicationService": 2,
            "ApplicationInterface": 2, "ApplicationFunction": 2,
            "ApplicationProcess": 2, "ApplicationEvent": 2,
            # Business layer → Phase 3
            "BusinessProcess": 3, "BusinessService": 3, "BusinessRole": 3,
            "BusinessActor": 3, "BusinessObject": 3, "BusinessFunction": 3,
            "BusinessEvent": 3, "Contract": 3,
            # Implementation layer → Phase 4
            "WorkPackage": 4, "Deliverable": 4, "Plateau": 4, "Gap": 4,
            "ImplementationEvent": 4,
            # Physical layer → Phase 1 (infrastructure)
            "Equipment": 1, "Facility": 1, "DistributionNetwork": 1,
        }

        proposals = SolutionBlueprintProposal.query.filter_by(
            solution_id=self.solution_id,
        ).filter(
            SolutionBlueprintProposal.status.in_(["promoted", "accepted"])
        ).all()

        # Skip motivation-layer elements (requirements, stakeholders) — they inform but aren't buildable
        buildable = [p for p in proposals if LAYER_ORDER.get(p.archimate_type, -1) >= 0]

        phases = {
            0: {"id": "phase0", "name": "Strategy Alignment", "phase_order": 0, "elements": [], "total_effort": ""},
            1: {"id": "phase1", "name": "Foundation (Infrastructure)", "phase_order": 1, "elements": [], "total_effort": ""},
            2: {"id": "phase2", "name": "Core (Applications & Data)", "phase_order": 2, "elements": [], "total_effort": ""},
            3: {"id": "phase3", "name": "Integration (Business Processes)", "phase_order": 3, "elements": [], "total_effort": ""},
            4: {"id": "phase4", "name": "Execution (Work Packages & Transitions)", "phase_order": 4, "elements": [], "total_effort": ""},
        }

        for p in buildable:
            props = p.acm_properties or {}
            def _val(key):
                v = props.get(key)
                if isinstance(v, dict):
                    return v.get("value", "")
                return v or ""

            phase_num = LAYER_ORDER.get(p.archimate_type, 2)
            phases[phase_num]["elements"].append({
                "id": p.promoted_element_id or p.id,
                "proposal_id": p.id,
                "name": p.name,
                "type": p.archimate_type,
                "acm_domain": p.acm_domain,
                "build_or_buy": _val("build_or_buy"),
                "estimated_effort": _val("estimated_effort"),
                "implementation_status": _val("implementation_status"),
                "dependencies": _val("dependencies"),
                "team_owner": _val("team_owner"),
                "deployment_model": _val("deployment_model"),
            })

        result_phases = [ph for ph in phases.values() if ph["elements"]]
        return {
            "phases": result_phases,
            "total_elements": len(buildable),
            "total_phases": len(result_phases),
        }

    def get_arb_package(self):
        """Step 6: Build complete ARB submission package.

        Aggregates: elements by domain, property coverage, waivers,
        NFR summary, domain specs, and completeness scores.
        """
        from app.models.solution_blueprint_proposal import SolutionBlueprintProposal
        from app.models.solution_domain_spec import SolutionDomainSpec
        from app.models.solution_models import Solution

        solution = Solution.query.get(self.solution_id)
        if not solution:
            return {"error": "Solution not found"}

        specs = SolutionDomainSpec.query.filter_by(solution_id=self.solution_id).all()
        proposals = SolutionBlueprintProposal.query.filter_by(
            solution_id=self.solution_id,
        ).filter(
            SolutionBlueprintProposal.status.in_(["promoted", "accepted"])
        ).all()

        # Group by domain
        domains_summary = {}
        for spec in specs:
            code = spec.domain_code
            domain_props = [p for p in proposals if p.acm_domain == code]
            waived = [p for p in domain_props if p.waived]
            nfrs = [p for p in domain_props if p.source == "nfr"]

            # Count filled properties
            total_props = 0
            filled_props = 0
            for p in domain_props:
                props = p.acm_properties or {}
                for key, val in props.items():
                    total_props += 1
                    v = val.get("value") if isinstance(val, dict) else val
                    if v not in (None, "", "TBD"):
                        filled_props += 1

            domains_summary[code] = {
                "domain_code": code,
                "status": spec.status,
                "relevance_tier": spec.relevance_tier,
                "justification": spec.status_justification,
                "element_count": len(domain_props),
                "waived_count": len(waived),
                "waivers": [{"name": w.name, "reason": w.waiver_reason or ""} for w in waived],
                "nfr_count": len(nfrs),
                "nfrs": [{"name": n.name, "description": n.description or ""} for n in nfrs],
                "property_coverage": round(filled_props / max(total_props, 1) * 100),
            }

        total_elements = len(proposals)
        confirmed = sum(1 for s in specs if s.status == "confirmed")
        na_with_just = sum(1 for s in specs if s.status == "not_applicable" and s.status_justification)
        domain_coverage = confirmed + na_with_just

        # Layer coverage check: count elements per ArchiMate layer
        layer_counts = {}
        for p in proposals:
            layer = self._type_to_layer(p.archimate_type)
            layer_counts[layer] = layer_counts.get(layer, 0) + 1

        required_layers = ("motivation", "strategy", "business", "application", "technology")
        layers_covered = sum(1 for item in required_layers if layer_counts.get(item, 0) >= 1)
        layers_with_depth = sum(1 for item in required_layers if layer_counts.get(item, 0) >= 3)

        return {
            "solution": {
                "id": solution.id,
                "name": solution.name,
                "governance_status": solution.governance_status,
                "description": solution.description or "",
            },
            "domains": domains_summary,
            "summary": {
                "total_elements": total_elements,
                "domain_coverage": f"{domain_coverage}/7",
                "confirmed_domains": confirmed,
                "total_waivers": sum(d["waived_count"] for d in domains_summary.values()),
                "total_nfrs": sum(d["nfr_count"] for d in domains_summary.values()),
                "layer_coverage": f"{layers_covered}/{len(required_layers)}",
                "layer_counts": layer_counts,
            },
            "ready_for_arb": (
                domain_coverage >= 6
                and total_elements > 0
                and layers_covered >= 4  # at least 4 of 5 required layers populated
                and layers_with_depth >= 3  # at least 3 layers with 3+ elements
            ),
        }

    @staticmethod
    def _type_to_layer(archimate_type):
        """Map an ArchiMate element type to its layer name."""
        _map = {
            "Stakeholder": "motivation", "Driver": "motivation", "Assessment": "motivation",
            "Goal": "motivation", "Outcome": "motivation", "Principle": "motivation",
            "Requirement": "motivation", "Constraint": "motivation",
            "Meaning": "motivation", "Value": "motivation",
            "Capability": "strategy", "CourseOfAction": "strategy",
            "ValueStream": "strategy", "Resource": "strategy",
            "BusinessActor": "business", "BusinessRole": "business",
            "BusinessCollaboration": "business", "BusinessInterface": "business",
            "BusinessProcess": "business", "BusinessFunction": "business",
            "BusinessInteraction": "business", "BusinessEvent": "business",
            "BusinessService": "business", "BusinessObject": "business",
            "Representation": "business", "Product": "business", "Contract": "business",
            "ApplicationComponent": "application", "ApplicationCollaboration": "application",
            "ApplicationInterface": "application", "ApplicationFunction": "application",
            "ApplicationInteraction": "application", "ApplicationProcess": "application",
            "ApplicationEvent": "application", "ApplicationService": "application",
            "DataObject": "application",
            "Node": "technology", "Device": "technology", "SystemSoftware": "technology",
            "TechnologyCollaboration": "technology", "TechnologyInterface": "technology",
            "Path": "technology", "CommunicationNetwork": "technology",
            "TechnologyFunction": "technology", "TechnologyProcess": "technology",
            "TechnologyInteraction": "technology", "TechnologyEvent": "technology",
            "TechnologyService": "technology", "Artifact": "technology",
            "Equipment": "physical", "Facility": "physical",
            "DistributionNetwork": "physical", "Material": "physical",
            "WorkPackage": "implementation", "Deliverable": "implementation",
            "ImplementationEvent": "implementation",
            "Plateau": "implementation", "Gap": "implementation",
        }
        return _map.get(archimate_type, "application")

    def unconfirm_domain(self, domain_code):
        """Revert a confirmed domain to pending, removing promoted elements."""
        from app.models.solution_blueprint_proposal import SolutionBlueprintProposal
        from app.models.solution_domain_spec import SolutionDomainSpec
        from app.models.archimate_core import ArchiMateElement

        spec = SolutionDomainSpec.query.filter_by(
            solution_id=self.solution_id, domain_code=domain_code
        ).first()
        if not spec:
            return {"error": "Domain spec not found"}
        if spec.status != "confirmed":
            return {"error": "Domain is not confirmed"}

        # Revert promoted proposals and delete promoted elements
        proposals = SolutionBlueprintProposal.query.filter_by(
            solution_id=self.solution_id,
            acm_domain=domain_code,
            status="promoted",
        ).all()

        reverted = 0
        for p in proposals:
            if p.promoted_element_id:
                el = ArchiMateElement.query.get(p.promoted_element_id)
                if el:
                    db.session.delete(el)
                p.promoted_element_id = None
            p.status = "accepted"
            reverted += 1

        spec.status = "pending"
        spec.confirmed_at = None
        db.session.commit()

        return {"domain_code": domain_code, "reverted": reverted, "status": "pending"}

    def backfill_domain_elements(self):
        """One-time backfill: add missing baseline templates + create relationships.

        For solutions that were populated before new templates were added:
        1. Finds baseline templates that don't have proposals yet
        2. Creates + auto-accepts + promotes them
        3. Creates default relationships for ALL promoted elements
        """
        from app.models.acm_domain_template import AcmDomainTemplate
        from app.models.solution_blueprint_proposal import SolutionBlueprintProposal
        from app.models.solution_domain_spec import SolutionDomainSpec
        from app.modules.architecture_assistant.journey_graph import JourneyGraph

        # Only backfill solutions with ACM domains
        from app.models.solution_models import Solution
        solution = Solution.query.get(self.solution_id)
        if not solution or not getattr(solution, 'has_acm_domains', False):
            return {"error": "Solution does not use ACM domains"}

        specs = {s.domain_code: s for s in SolutionDomainSpec.query.filter_by(
            solution_id=self.solution_id
        ).all()}

        # Get all existing proposal names for this solution
        existing_names = set()
        for p in SolutionBlueprintProposal.query.filter_by(solution_id=self.solution_id).all():
            existing_names.add((p.acm_domain, p.name))

        # Find baseline templates that don't have proposals yet
        templates = AcmDomainTemplate.query.filter_by(is_baseline=True).all()
        graph = JourneyGraph.resume_for_solution(self.solution_id)

        new_proposals = 0
        new_promoted = 0
        for tmpl in templates:
            if (tmpl.domain_code, tmpl.name) in existing_names:
                continue
            if tmpl.domain_code not in specs:
                continue

            # Pre-fill basic properties so coverage doesn't regress
            default_props = self._get_default_properties(tmpl.archimate_type)

            # Create proposal as accepted
            proposal = SolutionBlueprintProposal(
                solution_id=self.solution_id,
                archimate_type=tmpl.archimate_type,
                name=tmpl.name,
                description=tmpl.description or "",
                source="baseline",
                acm_domain=tmpl.domain_code,
                is_baseline=True,
                confidence=1.0,
                status="accepted",
                default_rel_type=tmpl.default_rel_type,
                acm_properties=default_props,
            )
            db.session.add(proposal)
            db.session.flush()
            new_proposals += 1

            # If domain is confirmed, also promote immediately
            spec = specs.get(tmpl.domain_code)
            if spec and spec.status == "confirmed":
                node = graph.facade.get_or_create_node(
                    element_type=tmpl.archimate_type,
                    key={"name": tmpl.name},
                    defaults={"description": tmpl.description or ""},
                )
                db.session.flush()
                if hasattr(node.model, "acm_domain"):
                    node.model.acm_domain = tmpl.domain_code
                if hasattr(node.model, "is_baseline"):
                    node.model.is_baseline = True
                proposal.promoted_element_id = node.id
                proposal.status = "promoted"
                new_promoted += 1

        db.session.commit()

        # Now create relationships for ALL promoted elements
        from app.modules.architecture_assistant.domain_promotion import DomainPromotionService
        svc = DomainPromotionService()
        total_rels = 0
        for domain_code in specs:
            proposals = SolutionBlueprintProposal.query.filter_by(
                solution_id=self.solution_id,
                acm_domain=domain_code,
                status="promoted",
            ).all()
            rels = svc._create_default_relationships(
                self.solution_id, domain_code, proposals, graph
            )
            total_rels += rels

        db.session.commit()

        logger.info(
            "Backfill for solution %d: %d new proposals, %d promoted, %d relationships",
            self.solution_id, new_proposals, new_promoted, total_rels,
        )
        return {
            "new_proposals": new_proposals,
            "new_promoted": new_promoted,
            "relationships_created": total_rels,
        }

    @staticmethod
    def _get_default_properties(archimate_type):
        """Return sensible default property values for backfilled baseline elements."""
        # NOTE: "TBD" is treated as unfilled by the completeness scorer.
        # Use real values so backfilled elements don't tank coverage.
        DEFAULTS = {
            "ApplicationComponent": {
                "deployment_model": {"value": "cloud-native", "source": "default"},
                "build_or_buy": {"value": "build", "source": "default"},
                "availability_target": {"value": "99.9%", "source": "default"},
            },
            "ApplicationService": {
                "deployment_model": {"value": "cloud-native", "source": "default"},
                "build_or_buy": {"value": "build", "source": "default"},
                "availability_target": {"value": "99.9%", "source": "default"},
                "api_style": {"value": "REST", "source": "default"},
            },
            "ApplicationFunction": {
                "deployment_model": {"value": "cloud-native", "source": "default"},
                "build_or_buy": {"value": "build", "source": "default"},
            },
            "DataObject": {
                "data_classification": {"value": "internal", "source": "default"},
                "contains_pii": {"value": False, "source": "default"},
                "retention_period": {"value": "Per policy", "source": "default"},
            },
            "Node": {
                "network_zone": {"value": "private", "source": "default"},
                "dr_strategy": {"value": "active-passive", "source": "default"},
                "managed_service": {"value": True, "source": "default"},
            },
            "SystemSoftware": {
                "network_zone": {"value": "private", "source": "default"},
                "managed_service": {"value": True, "source": "default"},
                "license_model": {"value": "open-source", "source": "default"},
            },
            "CommunicationNetwork": {
                "network_zone": {"value": "private", "source": "default"},
            },
            "BusinessProcess": {
                "automation_level": {"value": "semi-automated", "source": "default"},
            },
            "BusinessService": {},
            "BusinessRole": {},
            "BusinessObject": {
                "data_classification": {"value": "internal", "source": "default"},
            },
            "ApplicationInterface": {
                "interface_type": {"value": "REST-API", "source": "default"},
                "authentication": {"value": "OAuth2", "source": "default"},
            },
        }
        return DEFAULTS.get(archimate_type, {})

    def update_proposal_properties(self, proposal_id, properties):
        """Update properties on a proposal. Sets source to 'user'."""
        from app.models.solution_blueprint_proposal import SolutionBlueprintProposal
        from app.modules.architecture_assistant.property_service import PropertyService, PropertyValidationError
        proposal = SolutionBlueprintProposal.query.get(proposal_id)
        if not proposal:
            return {"error": "Proposal not found"}
        from app.modules.architecture_assistant.property_service import GovernedPropertyService

        properties, property_errors = GovernedPropertyService().validate_updates(
            proposal.archimate_type, properties,
        )
        if property_errors:
            return {"error": " ".join(property_errors), "property_errors": property_errors}
        svc = PropertyService()
        try:
            proposal.acm_properties = svc.merge_properties(
                proposal.acm_properties or {},
                properties,
                archimate_type=proposal.archimate_type,
                source="user",
            )
        except PropertyValidationError as exc:
            return {"error": str(exc), "status_code": 400}
        db.session.commit()
        return {"id": proposal.id, "acm_properties": proposal.acm_properties}

    def generate_domain_properties(self, domain_code, problem_summary=""):
        """LLM-populate properties for accepted/proposed proposals in a domain.

        Chunks proposals into batches of 10 to stay within LLM output token limits.
        Only overwrites properties at their generic default or empty — never user edits.

        Returns: {"updated": N, "skipped": N}
        """
        import json
        from app.models.solution_blueprint_proposal import SolutionBlueprintProposal
        from app.models.acm_property_template import AcmPropertyTemplate
        from app.modules.ai_chat.services.llm_service import LLMService

        proposals = SolutionBlueprintProposal.query.filter(
            SolutionBlueprintProposal.solution_id == self.solution_id,
            SolutionBlueprintProposal.acm_domain == domain_code,
            SolutionBlueprintProposal.status.in_(["proposed", "accepted", "promoted"]),
        ).all()

        if not proposals:
            return {"updated": 0, "skipped": 0}

        # Group templates by archimate_type (one DB query)
        types_needed = list({p.archimate_type for p in proposals})
        templates_by_type = {}
        from app.modules.architecture_assistant.property_service import template_query

        all_templates = template_query().filter(
            AcmPropertyTemplate.archimate_type.in_(types_needed)
        ).all()
        for t in all_templates:
            templates_by_type.setdefault(t.archimate_type, []).append(t)

        # Filter to proposals that actually have templates
        eligible = [p for p in proposals if templates_by_type.get(p.archimate_type)]
        if not eligible:
            return {"updated": 0, "skipped": len(proposals)}

        def _build_element_block(p):
            tmpls = templates_by_type.get(p.archimate_type, [])
            fields = []
            for t in tmpls:
                opts = ""
                if t.enum_options:
                    opts = " (options: " + "/".join(str(o) for o in t.enum_options[:6]) + ")"
                help_hint = (" — " + t.help_text[:60]) if t.help_text else ""
                fields.append(f"  {t.property_key}{opts}{help_hint}")
            return (
                f"Element: {p.name}\nType: {p.archimate_type}\n"
                f"Description: {p.description or 'No description'}\n"
                f"Properties to fill:\n" + "\n".join(fields)
            )

        def _call_batch(batch):
            elements_block = [_build_element_block(p) for p in batch]
            prompt = (
                "You are a senior enterprise architect filling in technical properties "
                "for ArchiMate 3.2 elements in a solution blueprint.\n\n"
                f"Solution context:\n{problem_summary or 'Not provided'}\n\n"
                "For each element below, suggest specific values based on the solution context. "
                "Use the provided options where listed. Use 'TBD' only when there is genuinely "
                "no basis to suggest a value. Do not invent data that contradicts the context.\n\n"
                "For each property, provide a confidence score (0.0-1.0) and a brief rationale.\n\n"
                + "\n---\n".join(elements_block)
                + "\n\nReturn ONLY valid JSON with element names as keys:\n"
                '{"Element Name": {"property_key": {"value": "...", "confidence": 0.85, '
                '"rationale": "reason for this value"}, ...}, ...}'
            )
            provider, model = LLMService._get_configured_provider()
            raw, _ = LLMService._call_llm(prompt=prompt, model=model, provider=provider)
            if not raw:
                return {}
            clean = raw.strip()
            if clean.startswith("```"):
                clean = clean.split("```")[1]
                if clean.startswith("json"):
                    clean = clean[4:]
            return json.loads(clean.strip())

        # Process in batches of 10 to avoid LLM output truncation
        BATCH_SIZE = 10
        all_suggestions = {}
        errors = []
        for i in range(0, len(eligible), BATCH_SIZE):
            batch = eligible[i:i + BATCH_SIZE]
            try:
                batch_result = _call_batch(batch)
                all_suggestions.update(batch_result)
            except Exception as e:
                logger.error(
                    "generate_domain_properties batch %d-%d failed: %s",
                    i, i + len(batch), e
                )
                errors.append(str(e))

        if not all_suggestions and errors:
            return {"updated": 0, "skipped": len(proposals), "error": errors[0]}

        updated = 0
        for p in proposals:
            suggested = all_suggestions.get(p.name) or all_suggestions.get(p.name.strip())
            if not suggested:
                continue
            props = dict(p.acm_properties or {})
            changed = False
            for key, raw_val in suggested.items():
                # Accept both new format {"value":..,"confidence":..,"rationale":..}
                # and legacy flat format "some_value"
                if isinstance(raw_val, dict) and "value" in raw_val:
                    val = raw_val["value"]
                    confidence = raw_val.get("confidence")
                    rationale = raw_val.get("rationale", "")
                else:
                    val = raw_val
                    confidence = None
                    rationale = ""
                if not val or val == "TBD":
                    continue
                existing = props.get(key)
                # Never overwrite user edits; only overwrite "default"/empty
                if isinstance(existing, dict):
                    if existing.get("source") == "user":
                        continue
                elif existing not in (None, "", "TBD"):
                    continue
                entry = {"value": val, "source": "llm"}
                if confidence is not None:
                    entry["confidence"] = confidence
                    entry["rationale"] = rationale
                props[key] = entry
                changed = True
            if changed:
                p.acm_properties = props
                updated += 1

        if updated:
            db.session.commit()

        result = {"updated": updated, "skipped": len(proposals) - updated}
        if errors:
            result["partial_errors"] = errors
        return result

    def apply_default_properties_to_domain(self, domain_code):
        """Fill empty ACM property slots from templates + safe heuristics (no LLM).

        Does not set compliance_reference (requires catalog / human). May set a
        draft acceptance_criteria for Requirement elements from name/description.
        Never overwrites user-sourced properties.
        """
        from app.models.solution_blueprint_proposal import SolutionBlueprintProposal
        from app.modules.architecture_assistant.property_service import PropertyService

        proposals = SolutionBlueprintProposal.query.filter(
            SolutionBlueprintProposal.solution_id == self.solution_id,
            SolutionBlueprintProposal.acm_domain == domain_code,
            SolutionBlueprintProposal.status.in_(["proposed", "accepted", "promoted"]),
        ).all()

        if not proposals:
            return {"updated": 0, "skipped": 0}

        svc = PropertyService()
        updated = 0

        def _draft_acceptance(name, description):
            d = (description or "").strip()
            if len(d) > 120:
                return "%s: %s" % (name, d[:220])
            if d:
                return "%s: %s" % (name, d)
            return "Deliverable: %s — add measurable acceptance criteria before ARB." % name

        for p in proposals:
            props = svc.merge_template_defaults_only(
                p.acm_properties or {}, p.archimate_type or "Requirement", tier="standard"
            )
            changed = props != (p.acm_properties or {})

            if (p.archimate_type or "") == "Requirement":
                if svc.acm_slot_is_fillable(props.get("acceptance_criteria")):
                    props["acceptance_criteria"] = {
                        "value": _draft_acceptance(p.name or "Requirement", p.description),
                        "source": "suggested",
                    }
                    changed = True

            if changed:
                p.acm_properties = props
                updated += 1

        if updated:
            db.session.commit()

        return {"updated": updated, "skipped": len(proposals) - updated}

    def auto_fill_roadmap_properties(self, problem_summary: str = "") -> dict:
        """LLM-populate roadmap properties (build_or_buy, estimated_effort,
        implementation_status, deployment_model) for all promoted/accepted buildable elements.

        Only fills empty/default slots — never overwrites user edits.
        Returns: {"updated": N, "skipped": N}
        """
        import json
        from app.models.solution_blueprint_proposal import SolutionBlueprintProposal
        from app.modules.ai_chat.services.llm_service import LLMService

        # ArchiMate types that appear in the implementation roadmap (excludes Motivation layer)
        BUILDABLE_TYPES = {
            "Node", "SystemSoftware", "Device", "TechnologyService", "CommunicationNetwork",
            "Artifact", "Path", "DataObject", "ApplicationComponent", "ApplicationService",
            "ApplicationInterface", "ApplicationFunction", "ApplicationProcess", "ApplicationEvent",
            "BusinessProcess", "BusinessService", "BusinessRole", "BusinessActor", "BusinessObject",
            "BusinessFunction", "BusinessEvent", "Contract",
            "WorkPackage", "Deliverable", "Plateau", "Gap", "ImplementationEvent",
            "Equipment", "Facility", "DistributionNetwork",
        }

        proposals = SolutionBlueprintProposal.query.filter(
            SolutionBlueprintProposal.solution_id == self.solution_id,
            SolutionBlueprintProposal.status.in_(["promoted", "accepted"]),
        ).all()

        buildable = [p for p in proposals if p.archimate_type in BUILDABLE_TYPES]
        if not buildable:
            return {"updated": 0, "skipped": len(proposals)}

        # Values MUST match the template's <select> option values exactly
        ROADMAP_SCHEMA = {
            "build_or_buy": "Build/Buy/Hybrid/TBD — how will this be delivered?",
            "estimated_effort": "XS (1-2w)/S (2-4w)/M (1-3m)/L (3-6m)/XL (6m+) — implementation effort",
            "implementation_status": "not_started/in_progress/complete/blocked — current status",
        }

        def _needs_fill(props, key):
            v = props.get(key)
            if v is None or v == "":
                return True
            if isinstance(v, dict):
                src = v.get("source", "")
                val = v.get("value", "")
                return not val or src not in ("user", "llm")
            return False

        def _build_prompt(batch):
            lines = []
            for p in batch:
                props = p.acm_properties or {}
                missing = [k for k in ROADMAP_SCHEMA if _needs_fill(props, k)]
                if not missing:
                    continue
                fields = "\n".join(
                    f"  {k}: {ROADMAP_SCHEMA[k]}" for k in missing
                )
                lines.append(
                    f"Element: {p.name}\nType: {p.archimate_type}\n"
                    f"Description: {p.description or 'No description'}\n"
                    f"Fill these properties:\n{fields}"
                )
            if not lines:
                return None
            return (
                "You are a senior enterprise architect filling in implementation roadmap properties "
                "for ArchiMate 3.2 elements.\n\n"
                f"Solution context:\n{problem_summary or 'Enterprise architecture modernisation platform'}\n\n"
                "For each element, fill ONLY the listed properties with realistic values. "
                "Use the options provided. Return ONLY valid JSON:\n"
                '{"Element Name": {"build_or_buy": "Build", "estimated_effort": "M (1-3m)", '
                '"implementation_status": "not_started"}, ...}\n\n'
                + "\n---\n".join(lines)
            )

        BATCH_SIZE = 15
        all_suggestions = {}
        errors = []
        for i in range(0, len(buildable), BATCH_SIZE):
            batch = buildable[i:i + BATCH_SIZE]
            prompt = _build_prompt(batch)
            if not prompt:
                continue
            try:
                provider, model = LLMService._get_configured_provider()
                raw, _ = LLMService._call_llm(prompt=prompt, model=model, provider=provider)
                if not raw:
                    continue
                clean = raw.strip()
                if clean.startswith("```"):
                    clean = clean.split("```")[1]
                    if clean.startswith("json"):
                        clean = clean[4:]
                batch_result = json.loads(clean.strip())
                all_suggestions.update(batch_result)
            except Exception as e:
                logger.error("auto_fill_roadmap_properties batch %d failed: %s", i, e)
                errors.append(str(e))

        if not all_suggestions and errors:
            raise RuntimeError(errors[0])

        updated = 0
        for p in buildable:
            suggested = all_suggestions.get(p.name) or all_suggestions.get(p.name.strip())
            if not suggested:
                continue
            props = dict(p.acm_properties or {})
            changed = False
            for key in ROADMAP_SCHEMA:
                val = suggested.get(key)
                if not val or val == "Unknown":
                    continue
                if not _needs_fill(props, key):
                    continue
                props[key] = {"value": str(val), "source": "llm"}
                changed = True
            if changed:
                p.acm_properties = props
                updated += 1

        if updated:
            db.session.commit()

        return {"updated": updated, "skipped": len(buildable) - updated}
