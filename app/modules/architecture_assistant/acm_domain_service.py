"""ACM Domain Service — builds the 7-domain structure from baselines + LLM + overlays."""

import json
import logging
import re

from app import db

logger = logging.getLogger(__name__)

ACM_DOMAINS = ["UX", "APP", "DATA", "SEC", "DEV", "AI", "COM"]

ACM_DOMAIN_NAMES = {
    "UX": "User Experience",
    "APP": "Application Services",
    "DATA": "Data & Storage",
    "SEC": "Security & Identity",
    "DEV": "DevOps & Platform",
    "AI": "AI & Analytics",
    "COM": "Communication & Integration",
}

PROPERTY_FILL_PROMPT = """You are an enterprise architect filling in architecture element properties.

BUSINESS PROBLEM:
{enriched_brief}

ELEMENTS THAT NEED PROPERTIES:
[
{element_list}
]

For each element, suggest property values based on the business problem context.
Use the element's ArchiMate type to determine which properties apply:

- ApplicationComponent/ApplicationService: deployment_model (cloud-native/cloud-hosted/on-prem/hybrid/SaaS), build_or_buy (build/buy/extend-existing/SaaS/open-source), availability_target (99.99%/99.95%/99.9%/99%/best-effort), technology_stack, estimated_effort, estimated_cost_annual
- DataObject: data_classification (public/internal/confidential/restricted), contains_pii (true/false), retention_period, estimated_volume_initial
- Node/SystemSoftware: network_zone (public/DMZ/private/restricted), managed_service (true/false), dr_strategy (active-active/active-passive/pilot-light/backup-restore)
- ApplicationInterface: interface_type (REST-API/GraphQL/webhook/event-stream/file-transfer/UI), authentication (OAuth2/API-key/mTLS/SAML)
- BusinessProcess: automation_level (manual/semi-automated/fully-automated/AI-assisted)
- Requirement/Constraint/Principle: priority (must-have/should-have/could-have/wont-have)

Return ONLY valid JSON with element IDs as keys:
{{
    "elements": {{
        "123": {{"deployment_model": "cloud-native", "build_or_buy": "build", "availability_target": "99.95%"}},
        "456": {{"data_classification": "confidential", "contains_pii": true, "retention_period": "7 years"}}
    }}
}}

Only include properties that are relevant for each element type. Use specific values based on the business problem, not generic defaults."""

DOMAIN_GENERATION_PROMPT = """You are an enterprise architect using ArchiMate 3.2.

Given this business problem, suggest solution-specific ArchiMate elements for each of the 7 ACM domains.
For each element, attempt to match against the existing catalog first. Only create novel elements when no match exists.

BUSINESS PROBLEM:
{enriched_brief}

EXISTING APPLICATIONS (reuse where possible):
{catalog_context}

THE 7 ACM DOMAINS:
- UX (User Experience): UI, personas, accessibility, frontend
- APP (Application Services): APIs, business logic, services, workflows
- DATA (Data & Storage): Databases, schemas, retention, classification
- SEC (Security & Identity): Auth, encryption, compliance, audit
- DEV (DevOps & Platform): CI/CD, monitoring, DR, infrastructure
- AI (AI & Analytics): Reporting, ML, data quality, KPIs
- COM (Communication): Integration patterns, messaging, events, notifications

For each domain, also suggest a relevance tier:
- "differentiating" — core value of the solution
- "important" — significant beyond baseline
- "standard" — baseline is sufficient

For each element, also suggest values for key properties where applicable:
- For DataObjects: data_classification, contains_pii, estimated_volume, retention_period
- For ApplicationComponents: deployment_model, build_or_buy, technology_stack, availability_target
- For ApplicationInterfaces: interface_type, authentication
- For Nodes: network_zone, managed_service
- For Requirements: priority
- For BusinessProcesses: automation_level

Omit the "properties" key entirely if no properties are applicable for that element type.

Return ONLY valid JSON:
{{
    "domains": {{
        "UX": {{
            "suggested_tier": "standard",
            "tier_reason": "Why this tier",
            "elements": [
                {{
                    "type": "ApplicationComponent",
                    "name": "ML Fraud Scoring Engine",
                    "description": "...",
                    "match_type": "novel",
                    "existing_id": null,
                    "layer": "application",
                    "properties": {{
                        "deployment_model": "cloud-native",
                        "build_or_buy": "build",
                        "availability_target": "99.95%",
                        "technology_stack": "Python/TensorFlow Serving"
                    }}
                }}
            ]
        }}
    }}
}}"""


class AcmDomainService:
    """Builds the 7-domain ACM structure for a solution."""

    def get_baselines(self, industry_overlay=None):
        """Get baseline elements for all 7 domains, optionally with industry overlay."""
        from app.models.acm_domain_template import AcmDomainTemplate

        result = {d: [] for d in ACM_DOMAINS}

        baselines = (
            AcmDomainTemplate.query.filter_by(is_baseline=True, industry_overlay=None)
            .order_by(AcmDomainTemplate.sort_order)
            .all()
        )
        for t in baselines:
            result[t.domain_code].append({**t.to_dict(), "source": "baseline"})

        if industry_overlay:
            overlays = (
                AcmDomainTemplate.query.filter_by(industry_overlay=industry_overlay)
                .order_by(AcmDomainTemplate.sort_order)
                .all()
            )
            for t in overlays:
                result[t.domain_code].append(
                    {
                        **t.to_dict(),
                        "source": "industry_overlay",
                        "overlay_code": industry_overlay,
                    }
                )
        return result

    def get_nfr_templates(self, domain_code, tier="standard"):
        """Get NFR templates. Standard=core only, important/differentiating=all."""
        from app.models.acm_domain_template import AcmDomainTemplate

        query = AcmDomainTemplate.query.filter_by(domain_code=domain_code, is_nfr=True)
        if tier == "standard":
            query = query.filter_by(is_core_nfr=True)
        return [t.to_dict() for t in query.order_by(AcmDomainTemplate.sort_order).all()]

    def initialize_domains(self, solution_id, industry_overlay=None):
        """Create 7 SolutionDomainSpec rows. Returns list of dicts."""
        from app.models.solution_domain_spec import SolutionDomainSpec

        specs = []
        for code in ACM_DOMAINS:
            existing = SolutionDomainSpec.query.filter_by(
                solution_id=solution_id, domain_code=code
            ).first()
            if existing:
                specs.append(existing.to_dict())
                continue
            spec = SolutionDomainSpec(
                solution_id=solution_id,
                domain_code=code,
                relevance_tier="standard",
                status="pending",
            )
            db.session.add(spec)
            specs.append(spec.to_dict())
        db.session.commit()
        return specs

    def populate_domains(self, solution_id, enriched_brief, industry_overlay=None):
        """Full population: baselines + overlay + LLM suggestions + tier recommendations."""
        baselines = self.get_baselines(industry_overlay)
        self.initialize_domains(solution_id, industry_overlay)

        # Create baseline proposals with pre-filled properties
        from app.models.solution_blueprint_proposal import SolutionBlueprintProposal
        from app.services.solution_blueprint_service import create_solution_blueprint_proposal
        from app.modules.architecture_assistant.property_service import PropertyService
        prop_svc = PropertyService()

        for domain_code, elements in baselines.items():
            for el in elements:
                existing = SolutionBlueprintProposal.query.filter_by(
                    solution_id=solution_id,
                    name=el["name"],
                    acm_domain=domain_code,
                    is_baseline=True,
                ).first()
                if existing:
                    continue
                # Pre-fill properties from template defaults
                default_props = prop_svc.get_default_properties(
                    el["archimate_type"], tier="standard"
                )
                create_solution_blueprint_proposal(
                    solution_id=solution_id,
                    archimate_type=el["archimate_type"],
                    name=el["name"],
                    description=el.get("description", ""),
                    source=el.get("source", "baseline"),
                    acm_domain=domain_code,
                    is_baseline=el.get("is_baseline", False),
                    overlay_code=el.get("overlay_code"),
                    confidence=1.0,
                    status="proposed",
                    default_rel_type=el.get("default_rel_type"),
                    acm_properties=default_props if default_props else None,
                )
        db.session.commit()

        # Create NFR proposals from NFR templates
        from app.models.acm_domain_template import AcmDomainTemplate

        nfr_templates = AcmDomainTemplate.query.filter_by(is_nfr=True).order_by(
            AcmDomainTemplate.domain_code, AcmDomainTemplate.sort_order
        ).all()
        for nfr in nfr_templates:
            existing = SolutionBlueprintProposal.query.filter_by(
                solution_id=solution_id,
                name=nfr.name,
                acm_domain=nfr.domain_code,
                source="nfr",
            ).first()
            if existing:
                continue
            # Pre-fill NFR properties (priority, compliance_reference, etc.)
            nfr_props = prop_svc.get_default_properties(
                nfr.archimate_type, tier="standard"
            )
            # NFRs are Requirements — set priority to should-have by default
            if "priority" not in nfr_props:
                nfr_props["priority"] = {"value": "should-have", "source": "default"}
            create_solution_blueprint_proposal(
                solution_id=solution_id,
                archimate_type=nfr.archimate_type,
                name=nfr.name,
                description=nfr.description or "",
                source="nfr",
                acm_domain=nfr.domain_code,
                is_baseline=False,
                confidence=1.0,
                status="proposed",
                default_rel_type=nfr.default_rel_type,
                acm_properties=nfr_props if nfr_props else None,
            )
        db.session.commit()

        # LLM call for solution-specific elements
        llm_result = self._generate_solution_elements(enriched_brief)
        tier_suggestions = {}

        if llm_result and "domains" in llm_result:
            for domain_code, domain_data in llm_result["domains"].items():
                if domain_code not in ACM_DOMAINS:
                    continue
                tier_suggestions[domain_code] = {
                    "tier": domain_data.get("suggested_tier", "standard"),
                    "reason": domain_data.get("tier_reason", ""),
                }
                for el in domain_data.get("elements", []):
                    # Store LLM-suggested properties with source tracking
                    llm_properties = el.get("properties", {}) or {}
                    acm_props = {}
                    for key, value in llm_properties.items():
                        if value is not None:
                            acm_props[key] = {"value": value, "source": "llm"}

                    # Guard: existing_id must be an integer (LLM may return a name string)
                    raw_existing_id = el.get("existing_id")
                    existing_element_id = raw_existing_id if isinstance(raw_existing_id, int) else None
                    create_solution_blueprint_proposal(
                        solution_id=solution_id,
                        archimate_type=el.get("type", "Unknown"),
                        name=el.get("name", "Unnamed"),
                        description=el.get("description", ""),
                        source="llm",
                        acm_domain=domain_code,
                        is_baseline=False,
                        match_type=el.get("match_type", "novel"),
                        existing_element_id=existing_element_id,
                        confidence=0.8,
                        status="proposed",
                        acm_properties=acm_props if acm_props else None,
                    )
            db.session.commit()

        # LLM pass: fill properties on ALL elements (baseline + LLM)
        self._llm_fill_element_properties(solution_id, enriched_brief)

        # Update domain specs with tier suggestions
        from app.models.solution_domain_spec import SolutionDomainSpec

        for code, suggestion in tier_suggestions.items():
            spec = SolutionDomainSpec.query.filter_by(
                solution_id=solution_id, domain_code=code
            ).first()
            if spec and spec.status == "pending":
                spec.relevance_tier = suggestion["tier"]
        db.session.commit()

        return self._build_domain_response(solution_id, tier_suggestions)

    def _generate_solution_elements(self, enriched_brief):
        """Call LLM for solution-specific elements across all 7 domains."""
        try:
            from app.modules.ai_chat.services.llm_service import LLMService

            provider, model = LLMService._get_configured_provider()
            prompt = DOMAIN_GENERATION_PROMPT.format(
                enriched_brief=enriched_brief,
                catalog_context=self._get_catalog_context(),
            )
            # Domain generation returns a large JSON object; without a generous
            # max_tokens the response truncated mid-string ("Unterminated string"
            # / "Expecting ',' delimiter" at ~23k chars) and domain generation
            # silently failed.
            max_tokens = LLMService.get_max_tokens_limit(provider, model, requested_max=8192)
            raw_text, _ = LLMService._call_llm(
                prompt=prompt, model=model, provider=provider, max_tokens=max_tokens
            )
            if not raw_text:
                return None
            text = raw_text.strip()
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```\s*$", "", text)
            json_start = text.find("{")
            json_end = text.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                return json.loads(text[json_start:json_end])
            return None
        except Exception as e:
            logger.error("LLM domain generation failed: %s", e)
            return None

    def load_domains(self, solution_id):
        """Reload domain data from DB (for session restore when domainsPopulated=true)."""
        return self._build_domain_response(solution_id, tier_suggestions={})

    def _llm_fill_element_properties(self, solution_id, enriched_brief):
        """Second LLM pass: fill properties on ALL elements that have empty/default properties.

        Takes the problem context + all elements and asks the LLM to suggest
        context-appropriate property values for each element.
        """
        from app.models.solution_blueprint_proposal import SolutionBlueprintProposal

        proposals = SolutionBlueprintProposal.query.filter_by(
            solution_id=solution_id,
        ).filter(
            SolutionBlueprintProposal.status.in_(["proposed", "accepted"])
        ).all()

        # Build element list for the prompt — only include elements that need properties
        elements_needing_props = []
        for p in proposals:
            props = p.acm_properties or {}
            # Skip elements that already have LLM-filled properties
            has_llm_props = any(
                isinstance(v, dict) and v.get("source") == "llm"
                for v in props.values()
            )
            if has_llm_props:
                continue
            elements_needing_props.append(p)

        if not elements_needing_props:
            return

        # Build a compact element list for the prompt
        element_lines = []
        for p in elements_needing_props[:50]:  # Cap at 50 to keep prompt manageable
            element_lines.append(
                '  {"id": %d, "domain": "%s", "type": "%s", "name": "%s"}'
                % (p.id, p.acm_domain, p.archimate_type, p.name)
            )

        prompt = PROPERTY_FILL_PROMPT.format(
            enriched_brief=enriched_brief,
            element_list=",\n".join(element_lines),
        )

        try:
            from app.modules.ai_chat.services.llm_service import LLMService
            provider, model = LLMService._get_configured_provider()
            max_tokens = LLMService.get_max_tokens_limit(provider, model, requested_max=8192)
            raw_text, _ = LLMService._call_llm(
                prompt=prompt, model=model, provider=provider, max_tokens=max_tokens
            )
            if not raw_text:
                return

            text = raw_text.strip()
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```\s*$", "", text)
            json_start = text.find("{")
            json_end = text.rfind("}") + 1
            if json_start < 0 or json_end <= json_start:
                return
            result = json.loads(text[json_start:json_end])

            # Apply LLM-suggested properties to proposals
            props_by_id = {int(k): v for k, v in result.get("elements", {}).items()}
            updated = 0
            for p in elements_needing_props:
                llm_props = props_by_id.get(p.id, {})
                if not llm_props:
                    continue
                existing = p.acm_properties or {}
                for key, value in llm_props.items():
                    if value is not None and value != "" and value != "TBD":
                        # Don't overwrite user-set values
                        current = existing.get(key)
                        if isinstance(current, dict) and current.get("source") == "user":
                            continue
                        existing[key] = {"value": value, "source": "llm"}
                p.acm_properties = existing
                updated += 1

            if updated:
                db.session.commit()
                logger.info("LLM filled properties on %d elements for solution %d", updated, solution_id)

        except Exception as e:
            logger.error("LLM property fill failed (non-blocking): %s", e)

    def _get_catalog_context(self):
        try:
            from app.models.application_portfolio import ApplicationComponent

            apps = ApplicationComponent.query.limit(30).all()
            if not apps:
                return "No applications in catalog"
            # Include IDs so LLM can reference them as integers in existing_id
            return "\n".join("- [id:%d] %s" % (a.id, a.name) for a in apps)
        except Exception:
            return "No applications in catalog"

    def _build_domain_response(self, solution_id, tier_suggestions):
        from app.models.solution_blueprint_proposal import SolutionBlueprintProposal
        from app.models.solution_domain_spec import SolutionDomainSpec

        domains = {}
        for code in ACM_DOMAINS:
            spec = SolutionDomainSpec.query.filter_by(
                solution_id=solution_id, domain_code=code
            ).first()
            proposals = (
                SolutionBlueprintProposal.query.filter_by(
                    solution_id=solution_id, acm_domain=code
                )
                .order_by(SolutionBlueprintProposal.confidence.desc())
                .all()
            )
            suggestion = tier_suggestions.get(code, {})
            domains[code] = {
                "code": code,
                "name": ACM_DOMAIN_NAMES.get(code, code),
                "spec": spec.to_dict() if spec else None,
                "suggested_tier": suggestion.get("tier", "standard"),
                "tier_reason": suggestion.get("reason", ""),
                "elements": [
                    {
                        "id": p.id,
                        "archimate_type": p.archimate_type,
                        "name": p.name,
                        "description": p.description,
                        "source": p.source,
                        "is_baseline": p.is_baseline,
                        "overlay_code": p.overlay_code,
                        "match_type": p.match_type,
                        "confidence": p.confidence,
                        "status": p.status,
                        "waived": p.waived,
                        "waiver_reason": p.waiver_reason,
                        "cross_domain_rule_id": p.cross_domain_rule_id,
                        "acm_properties": p.acm_properties or {},
                    }
                    for p in proposals
                ],
                "nfr_templates": self.get_nfr_templates(
                    code, spec.relevance_tier if spec else "standard"
                ),
            }
        return {"domains": domains}
