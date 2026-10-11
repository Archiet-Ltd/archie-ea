# app/modules/architecture_assistant/domain_promotion.py
"""Domain Promotion Service — promotes accepted proposals to ArchiMate elements."""

from app.services.archimate_backbone import sync_archimate_element
import json
import logging
import re
from datetime import datetime

from app import db

from app.modules.architecture_assistant.acm_properties_utils import (
    compliance_tags_from_acm,
    moscow_from_acm_properties,
    unwrap_acm_value,
)

# ── Quantitative NFR extraction ──────────────────────────────────────────────
# These regexes parse measurable targets from free-text descriptions so that
# the ArchiMateElement quality columns (performance_metrics, reliability_metrics,
# scalability_metrics, compliance_frameworks, security_posture) are populated for
# every promoted element that mentions them. Previously these columns were never
# set, so the UML enrichment LLM had no structured NFR data to work from.
_NFR_UPTIME = re.compile(r'(\d+(?:\.\d+)?)\s*%\s*(?:uptime|availability)', re.I)
_NFR_LATENCY = re.compile(r'(\d+(?:\.\d+)?)\s*ms\b', re.I)
_NFR_RPS = re.compile(r'(\d[\d,]*)\s*(?:req(?:uests?)?[/\s]s(?:ec(?:ond)?)?|rps|tps)\b', re.I)
_NFR_USERS = re.compile(r'(\d[\d,]*)\s*(?:concurrent\s+)?users?', re.I)
_NFR_COMPLIANCE = re.compile(
    r'\b(GDPR|HIPAA|SOC\s*2|ISO\s*27001|PCI[- ]DSS|FERPA|FedRAMP|CCPA|NIST)\b', re.I
)
_NFR_ENCRYPT = re.compile(r'\b(encrypt|TLS|SSL|AES|RSA|HTTPS)\b', re.I)
_NFR_AUTH = re.compile(r'\b(MFA|multi[- ]factor|2FA|OAuth|SAML|SSO)\b', re.I)


def _extract_nfr_metrics(text: str) -> dict:
    """Parse quantitative NFR targets from a free-text description.

    Returns a dict of {column_name: json_string} for each non-empty metric set.
    Only called during promotion — never touches confirmed/manually-set values.
    """
    if not text:
        return {}
    result = {}

    perf = {}
    m = _NFR_LATENCY.search(text)
    if m:
        perf["latency_ms"] = float(m.group(1))
    m = _NFR_RPS.search(text)
    if m:
        perf["throughput_rps"] = int(m.group(1).replace(",", ""))
    if perf:
        result["performance_metrics"] = json.dumps(perf)

    rel = {}
    m = _NFR_UPTIME.search(text)
    if m:
        rel["uptime_percent"] = float(m.group(1))
    if rel:
        result["reliability_metrics"] = json.dumps(rel)

    scale = {}
    m = _NFR_USERS.search(text)
    if m:
        scale["concurrent_users"] = int(m.group(1).replace(",", ""))
    if scale:
        result["scalability_metrics"] = json.dumps(scale)

    frameworks = sorted({f.upper().replace(" ", "") for f in _NFR_COMPLIANCE.findall(text)})
    if frameworks:
        result["compliance_frameworks"] = json.dumps(frameworks)

    sec = {}
    if _NFR_ENCRYPT.search(text):
        sec["encryption_required"] = True
    if _NFR_AUTH.search(text):
        sec["mfa_required"] = True
    if sec:
        result["security_posture"] = json.dumps(sec)

    return result

logger = logging.getLogger(__name__)

# Prefix-based mapping from ArchiMate element type to layer name.
# Ordered so longer/more specific prefixes (e.g. "System") come before shorter ones.
_TYPE_PREFIX_TO_LAYER = [
    ("Application", "application"),
    ("Business", "business"),
    ("Technology", "technology"),
    ("Node", "technology"),
    ("Device", "technology"),
    ("System", "technology"),
    ("Artifact", "technology"),
    ("Communication", "technology"),
    ("Path", "technology"),
    ("Capability", "strategy"),
    ("Resource", "strategy"),
    ("ValueStream", "strategy"),
    ("CourseOfAction", "strategy"),
    ("Stakeholder", "motivation"),
    ("Driver", "motivation"),
    ("Assessment", "motivation"),
    ("Goal", "motivation"),
    ("Outcome", "motivation"),
    ("Principle", "motivation"),
    ("Requirement", "motivation"),
    ("Constraint", "motivation"),
    ("Meaning", "motivation"),
    ("Value", "motivation"),
    ("WorkPackage", "implementation"),
    ("Deliverable", "implementation"),
    ("Implementation", "implementation"),
    ("Plateau", "implementation"),
    ("Gap", "implementation"),
]


def _archimate_type_to_layer(archimate_type: str) -> str:
    """Map an ArchiMate element type string to its canonical layer name."""
    for prefix, layer in _TYPE_PREFIX_TO_LAYER:
        if archimate_type.startswith(prefix):
            return layer
    return "other"


class DomainPromotionService:
    """Promotes accepted SolutionBlueprintProposals to real ArchiMateElements."""

    def promote_domain(self, solution_id, domain_code):
        """Promote all accepted proposals in a domain to ArchiMate elements.

        Checks property coverage threshold for differentiating/important domains
        before allowing promotion. Returns error dict if blocked.
        """
        from app.models.solution_blueprint_proposal import SolutionBlueprintProposal
        from app.models.solution_domain_spec import SolutionDomainSpec

        # Property coverage is tracked but NOT enforced at domain confirmation.
        # Enforcement moves to ARB submission (Phase G) where properties are expected
        # to be filled. Blocking mid-journey on empty properties prevents completion.
        spec = SolutionDomainSpec.query.filter_by(
            solution_id=solution_id, domain_code=domain_code
        ).first()

        proposals = SolutionBlueprintProposal.query.filter_by(
            solution_id=solution_id,
            acm_domain=domain_code,
            status="accepted",
        ).all()

        from app.modules.architecture_assistant.journey_graph import JourneyGraph
        graph = JourneyGraph.resume_for_solution(solution_id)

        promoted = 0
        for p in proposals:
            if p.promoted_element_id:
                continue

            node = graph.facade.get_or_create_node(
                element_type=p.archimate_type,
                key={"name": p.name},
                defaults={"description": p.description or ""},
            )
            db.session.flush()  # E6: ensure IDs are assigned

            # Populate NFR quality columns from quantitative metrics in description.
            # Only sets columns that are currently empty — never overwrites manual input.
            if p.description:
                nfr = _extract_nfr_metrics(p.description)
                for col, val in nfr.items():
                    if hasattr(node.model, col) and not getattr(node.model, col):
                        setattr(node.model, col, val)

            # Store ACM metadata on the element if columns exist
            if hasattr(node.model, "acm_domain"):
                node.model.acm_domain = domain_code
            if hasattr(node.model, "is_baseline"):
                node.model.is_baseline = p.is_baseline
            if hasattr(node.model, "acm_source"):
                node.model.acm_source = p.source
            if hasattr(node.model, "overlay_code"):
                node.model.overlay_code = p.overlay_code
            # Persist wizard ACM fields on the catalog element so UML/codegen can read them
            if hasattr(node.model, "acm_properties") and p.acm_properties:
                from app.modules.architecture_assistant.property_service import PropertyService
                raw = {}
                for k, v in p.acm_properties.items():
                    if isinstance(v, dict) and "value" in v:
                        raw[k] = v["value"]
                    else:
                        raw[k] = v
                PropertyService().merge_element_properties(node.model, raw, source="promotion")

            p.promoted_element_id = node.id
            p.status = "promoted"
            promoted += 1

        # Create default relationships from template metadata
        rels_created = self._create_default_relationships(
            solution_id, domain_code, proposals, graph
        )

        # ── Create SolutionArchiMateElement junctions ──────────────────────────
        # Promoted elements exist in archimate_elements but are invisible to the
        # codegen pipeline (UMLEnrichmentService, completeness scoring) until they
        # have a junction record in solution_archimate_elements.
        junctions_created = self._create_solution_junctions(solution_id, proposals)

        # ── Structural cross-layer inference (Pass 2 only, no LLM) ────────────
        # For each promoted element, repair any missing downstream chain links
        # (e.g. BusinessProcess → ApplicationService) and junction the derived
        # elements to this solution with element_role="ai_derived".
        derived_count = self._run_structural_inference(solution_id, graph, proposals)

        # ── Bridge motivation-layer proposals to SolutionRequirement rows ───────
        # _get_solution_business_rules() Sources 3-5 require a SolutionProblemDefinition
        # which document-sourced solutions may never have. SolutionRequirement has a
        # direct solution_id FK (nullable problem_id) — this bridge ensures every
        # Constraint/Goal/Driver/Requirement proposal creates a row that Source 7
        # (added to _get_solution_business_rules) can read directly.
        from app.models.solution_models import Solution as _Sol
        _sol_obj = _Sol.query.get(solution_id)
        _org_id = _sol_obj.organization_id if _sol_obj else None
        reqs_created = self._create_solution_requirements(solution_id, _org_id, proposals)

        # Update domain spec
        spec = SolutionDomainSpec.query.filter_by(
            solution_id=solution_id, domain_code=domain_code
        ).first()
        if spec:
            spec.status = "confirmed"
            spec.confirmed_at = datetime.utcnow()

        db.session.commit()

        logger.info(
            "Promoted %d elements in domain %s for solution %d "
            "(%d junctions, %d ai_derived, %d requirements)",
            promoted, domain_code, solution_id, junctions_created, derived_count, reqs_created,
        )

        return {
            "domain_code": domain_code,
            "promoted": promoted,
            "relationships_created": rels_created,
            "junctions_created": junctions_created,
            "derived_elements": derived_count,
            "requirements_created": reqs_created,
            "status": "confirmed",
        }

    def _create_solution_requirements(self, solution_id, org_id, proposals):
        """Create SolutionRequirement rows for motivation-layer promoted proposals.

        SolutionConstraint/SolutionGoal/SolutionDriver all require a
        SolutionProblemDefinition (problem_id NOT NULL). SolutionRequirement
        allows nullable problem_id with a direct solution_id FK — making it
        the correct bridge for document-sourced solutions that skip the wizard
        analysis session. Once created, _get_solution_business_rules() Source 7
        reads these rows directly without needing pd_id.

        Idempotent: skips if a row with source="proposal:<id>" already exists.
        """
        from app.models.solution_architect_models import SolutionRequirement, RequirementType

        _TYPE_TO_REQ = {
            "Constraint": RequirementType.CONSTRAINT,
            "Goal": RequirementType.QUALITY,
            "Driver": RequirementType.QUALITY,
            "Requirement": RequirementType.FUNCTIONAL,
        }
        _TYPE_MANDATORY = {"Constraint"}

        created = 0
        for p in proposals:
            if p.archimate_type not in _TYPE_TO_REQ:
                continue
            source_tag = f"proposal:{p.id}"
            if SolutionRequirement.query.filter_by(
                solution_id=solution_id, source=source_tag
            ).first():
                continue
            # Pull enrichment fields from acm_properties (plain or {value, source})
            acm_props = p.acm_properties or {}
            req = SolutionRequirement(
                solution_id=solution_id,
                organization_id=org_id,
                name=p.name,
                description=p.description or p.name,
                requirement_type=_TYPE_TO_REQ[p.archimate_type],
                source=source_tag,
                is_mandatory=(p.archimate_type in _TYPE_MANDATORY),
                status="open",
                acceptance_criteria=unwrap_acm_value(acm_props.get("acceptance_criteria")),
                moscow_priority=moscow_from_acm_properties(acm_props),
                stakeholder_name=(
                    unwrap_acm_value(acm_props.get("stakeholder_name"))
                    or unwrap_acm_value(acm_props.get("stakeholder"))
                ),
                compliance_tags=compliance_tags_from_acm(acm_props),
                verification_method=unwrap_acm_value(acm_props.get("verification_method")),
            )
            db.session.add(req)
            sync_archimate_element(req)
            created += 1

        if created:
            db.session.flush()
        return created

    def _create_solution_junctions(self, solution_id, proposals):
        """Create SolutionArchiMateElement junctions for promoted proposals.

        Idempotent: skips proposals that already have a junction record.
        """
        from app.models.solution_archimate_element import SolutionArchiMateElement

        created = 0
        for p in proposals:
            if not p.promoted_element_id:
                continue
            existing = SolutionArchiMateElement.query.filter_by(
                solution_id=solution_id,
                element_id=p.promoted_element_id,
            ).first()
            if existing:
                continue
            junction = SolutionArchiMateElement(
                solution_id=solution_id,
                element_id=p.promoted_element_id,
                layer_type=_archimate_type_to_layer(p.archimate_type or ""),
                element_table="archimate_elements",
                element_name=p.name,
                element_role="primary",
            )
            db.session.add(junction)
            created += 1

        if created:
            db.session.flush()
        return created

    def _run_structural_inference(self, solution_id, graph, proposals):
        """Run Pass 2 cross-layer inference on promoted elements.

        For each promoted element, calls repair() to detect and fill missing
        downstream chain links. Any newly created elements are junctioned to
        this solution with element_role="ai_derived" so the codegen pipeline
        can see them.

        repair() does not trigger the semantic (LLM) pass — that is Pass 3 and
        is only invoked via generate_chain(). This is intentionally fast and
        deterministic.
        """
        from app.modules.architecture.services.inference_engine_service import (
            ArchiMateInferenceEngine,
        )
        from app.models.solution_archimate_element import SolutionArchiMateElement

        derived_count = 0
        try:
            engine = ArchiMateInferenceEngine(graph.architecture_id)

            for p in proposals:
                if not p.promoted_element_id:
                    continue
                try:
                    result = engine.repair(p.promoted_element_id)
                    for node in (result.elements_created or []):
                        existing = SolutionArchiMateElement.query.filter_by(
                            solution_id=solution_id,
                            element_id=node.id,
                        ).first()
                        if existing:
                            continue
                        junction = SolutionArchiMateElement(
                            solution_id=solution_id,
                            element_id=node.id,
                            layer_type=_archimate_type_to_layer(node.element_type or ""),
                            element_table="archimate_elements",
                            element_name=node.name,
                            element_role="ai_derived",
                        )
                        db.session.add(junction)
                        derived_count += 1
                except Exception as e:
                    logger.debug(
                        "Inference repair skipped for element %d: %s",
                        p.promoted_element_id, e,
                    )

            if derived_count:
                db.session.flush()
        except Exception as e:
            logger.warning(
                "Structural inference failed for solution %d: %s",
                solution_id, e,
            )

        return derived_count

    def _create_default_relationships(self, solution_id, domain_code, proposals, graph):
        """Create relationships between promoted elements using template metadata.

        Looks up the original AcmDomainTemplate for each proposal to get the
        default_rel_target type, then finds a matching promoted element.
        """
        from app.models.acm_domain_template import AcmDomainTemplate
        from app.models.solution_blueprint_proposal import SolutionBlueprintProposal

        # Build template lookup: (domain, name) → template
        templates = AcmDomainTemplate.query.filter(
            AcmDomainTemplate.default_rel_type.isnot(None),
            AcmDomainTemplate.default_rel_target.isnot(None),
        ).all()
        tmpl_lookup = {}
        for t in templates:
            tmpl_lookup[(t.domain_code, t.name)] = t

        # Build type lookup: archimate_type → promoted proposals (across all domains)
        all_promoted = SolutionBlueprintProposal.query.filter_by(
            solution_id=solution_id,
            status="promoted",
        ).all()
        type_to_elements = {}
        for p in all_promoted:
            if p.promoted_element_id:
                type_to_elements.setdefault(p.archimate_type, []).append(p)

        created = 0
        for p in proposals:
            if not p.promoted_element_id or not p.default_rel_type:
                continue

            # Look up the target type from the original template
            tmpl = tmpl_lookup.get((p.acm_domain, p.name))
            target_type = tmpl.default_rel_target if tmpl else None
            if not target_type:
                continue

            # Prefer targets in the same domain, then any domain
            candidates = type_to_elements.get(target_type, [])
            same_domain = [c for c in candidates if c.acm_domain == domain_code]
            target = same_domain[0] if same_domain else (candidates[0] if candidates else None)

            if target and target.promoted_element_id:
                try:
                    graph.facade.get_or_create_relationship(
                        source_id=p.promoted_element_id,
                        target_id=target.promoted_element_id,
                        rel_type=p.default_rel_type,
                        metadata={
                            "source_tag": "acm_template",
                            "confidence": 0.95,
                            "inference_pass": 0,
                            "rule_name": "acm_baseline_default",
                        },
                    )
                    created += 1
                except Exception as e:
                    logger.debug(
                        "Relationship creation failed for %s → %s: %s",
                        p.name, target.name, e,
                    )

        if created:
            db.session.flush()

        logger.info(
            "Created %d default relationships in domain %s for solution %d",
            created, domain_code, solution_id,
        )
        return created
