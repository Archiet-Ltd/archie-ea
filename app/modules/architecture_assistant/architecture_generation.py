"""Step 3 Architecture Generation — comprehensive multi-layer ArchiMate generation.

Generates ArchiMate 3.2 elements across all 7 layers (Motivation, Strategy, Business,
Application, Technology, Implementation, Physical) with per-capability expansion.

Two modes:
- generate_greenfield(): Per-capability LLM calls → merge → relationship pass
- generate_from_skeleton(): Fill placeholder elements in an inference skeleton
"""

import json
import logging
import re

from app import db
from app.services.application_kind import applications_query

logger = logging.getLogger(__name__)

# ── Per-capability element generation prompt ──────────────────────────
# Called once per accepted capability to produce a deep element cluster.
CAPABILITY_EXPANSION_PROMPT = """You are an expert enterprise architect using ArchiMate 3.2.
Generate a COMPREHENSIVE set of ArchiMate elements for ONE capability within this solution:

SOLUTION CONTEXT:
{problem_summary}

CAPABILITY TO EXPAND:
Name: {capability_name}
Description: {capability_description}

ALL ACCEPTED CAPABILITIES (for cross-references):
{all_capabilities}

EXISTING APPLICATIONS (reuse by exact name where they fit — mark source: "existing"):
{catalog_context}

VENDOR PRODUCTS AVAILABLE:
{vendor_context}

TECHNOLOGY STACKS IN USE:
{tech_stack_context}

Generate elements across ALL SEVEN ArchiMate layers for this capability:

MOTIVATION LAYER (6-10 elements — HARD CAP: max 10, aim for 6-8):
IMPORTANT: Do NOT repeat Stakeholder, Driver, or Goal elements that also appear in other
capabilities — generate each motivation element once across the full solution.
- Stakeholder: every person/team/org with interest (business owner, end users, ops, security, compliance)
- Driver: business and technical pressures (cost reduction, regulation, efficiency, risk)
- Goal: measurable outcomes tied to each driver (KPIs, SLAs)
- Requirement: EVERY non-functional requirement across these categories:
  * Performance (latency, throughput, concurrency targets)
  * Security (authentication, authorization, encryption, audit trail)
  * Availability (uptime SLA, DR/BC, failover, RTO/RPO)
  * Scalability (horizontal/vertical scaling, elasticity)
  * Data (retention, classification, sovereignty, PII handling)
  * Integration (protocols, formats, SLAs for external systems)
  * Compliance (regulatory: GDPR, SOX, ISO 27001, industry-specific)
  * Usability (accessibility WCAG, i18n, responsive design)
- Constraint: any limits (budget, timeline, legacy compatibility, regulatory)
- Principle: architectural principles this capability must follow

STRATEGY LAYER (3-6 elements):
- Capability: the business capability itself + any sub-capabilities (L1→L2 decomposition)
- CourseOfAction: strategic approach to deliver this capability (build vs buy, phased rollout)
- ValueStream: end-to-end value delivery this capability participates in. MUST be linked in relationships: one Goal REALIZES this ValueStream (the stream is the mechanism by which the goal becomes real); this ValueStream ASSOCIATED-WITH the Capability it depends on; this ValueStream REALIZES one Outcome (the measurable result of execution).
- Resource: key resources needed (people, budget, expertise)

BUSINESS LAYER (8-15 elements):
- BusinessProcess: every process this capability supports (including sub-processes)
- BusinessService: services exposed to users/other capabilities
- BusinessObject: business data entities (e.g., "Customer Record", "Invoice", "Contract")
- BusinessRole: roles that interact with these processes
- BusinessActor: specific organizational units or external parties
- BusinessEvent: triggers and business events
- Contract: SLAs or agreements with external parties

APPLICATION LAYER (8-15 elements):
- ApplicationComponent: software modules (main app + sub-components/microservices)
- ApplicationService: services exposed by each component
- ApplicationInterface: APIs (REST, GraphQL, messaging), UIs, integration points
- ApplicationFunction: internal behaviors (processing, calculation, transformation)
- DataObject: EVERY data entity (10+ minimum — entities, documents, messages, configs, audit logs, cache objects)
  Include data_classification (public/internal/confidential/restricted) and contains_pii (true/false) for each

TECHNOLOGY LAYER (6-12 elements):
- Node: servers, VMs, containers, Kubernetes clusters, load balancers
- SystemSoftware: OS, databases, message brokers, monitoring, security tools, middleware
- TechnologyService: cloud services (compute, storage, networking, managed services)
- CommunicationNetwork: networks, VPNs, CDNs, service mesh
- Artifact: deployment packages, configuration files, container images
- Device: end-user devices if applicable

IMPLEMENTATION LAYER (3-6 elements):
- WorkPackage: implementation phases/projects for this capability
- Plateau: architecture states (Baseline → Transition → Target)
- Gap: specific gaps between current and target state
- Deliverable: key outputs (documentation, deployed service, migrated data)

Rules:
- Each element MUST have: type, name, description, source ("existing" or "derived")
- Names must be SPECIFIC to this solution (e.g., "Fraud Detection API Gateway" not "API Gateway")
- BANNED GENERIC NAMES — never use: "API Gateway", "Frontend Application", "Backend Service",
  "Core Business Entity", "Integration Service", "Data Store", "Business Component". Every name
  must include the solution domain (e.g., "Stripe Payment API Gateway", not "API Gateway").
- For DataObjects: ALWAYS include data_classification and contains_pii
- Generate at LEAST 40 elements total across all layers
- Implementation layer is MANDATORY — always generate at least 1 WorkPackage + 1 Plateau
- Every element must be relevant to the capability being expanded

Return ONLY valid JSON:
{{
    "motivation": [{{"type": "Stakeholder|Driver|Assessment|Goal|Outcome|Requirement|Constraint|Principle|Value|Meaning", "name": "...", "description": "...", "source": "derived"}}],
    "strategy": [{{"type": "Capability|CourseOfAction|ValueStream|Resource", "name": "...", "description": "...", "source": "derived"}}],
    "business": [{{"type": "BusinessProcess|BusinessService|BusinessObject|BusinessRole|BusinessActor|BusinessFunction|BusinessCollaboration|BusinessInterface|BusinessInteraction|BusinessEvent|Contract|Representation|Product", "name": "...", "description": "...", "source": "derived"}}],
    "application": [{{"type": "ApplicationComponent|ApplicationService|ApplicationInterface|ApplicationFunction|ApplicationProcess|ApplicationInteraction|ApplicationEvent|ApplicationCollaboration|DataObject", "name": "...", "description": "...", "source": "derived", "data_classification": null, "contains_pii": null}}],
    "technology": [{{"type": "Node|SystemSoftware|TechnologyService|TechnologyFunction|TechnologyProcess|TechnologyInterface|TechnologyCollaboration|TechnologyInteraction|TechnologyEvent|CommunicationNetwork|Path|Artifact|Device", "name": "...", "description": "...", "source": "derived"}}],
    "implementation": [{{"type": "WorkPackage|Plateau|Gap|Deliverable|ImplementationEvent", "name": "...", "description": "...", "source": "derived"}}]
}}"""

# ── Combined multi-capability expansion prompt (replaces N per-cap calls) ────
# Single LLM call for ALL capabilities — avoids N×20s serial timeout.
COMBINED_EXPANSION_PROMPT = """You are an expert enterprise architect using ArchiMate 3.2.
Generate a COMPREHENSIVE set of ArchiMate elements for ALL {cap_count} capabilities in this solution.

SOLUTION CONTEXT:
{problem_summary}

ENTITY SEEDS — use these EXACT names as DataObject names (do not rename, genericise, or replace them):
{entity_seeds}

AUTH MODEL — use these roles for BusinessRole elements and ApplicationInterface access controls:
{auth_model}

BUSINESS RULES — reflect these in BusinessProcess, Constraint, and Requirement elements:
{business_rules}

CAPABILITIES TO EXPAND ({cap_count} total):
{capabilities_detail}

EXISTING APPLICATIONS (reuse by exact name — mark source: "existing"):
{catalog_context}

VENDOR PRODUCTS AVAILABLE:
{vendor_context}

TECHNOLOGY STACKS IN USE:
{tech_stack_context}

COMPLIANCE CONSTRAINTS:
{compliance_context}

{existing_elements_block}

For EACH capability, generate elements across ALL SIX ArchiMate layers.
Tag every element with "capability_source" set to the EXACT capability name from the list above.

Per capability, generate at LEAST 14 elements total:

APPLICATION LAYER (3-4 per capability) — GENERATE FIRST:
- ApplicationComponent, ApplicationService, ApplicationInterface, ApplicationFunction
- DataObject: include data_classification (public/internal/confidential/restricted) and contains_pii (true/false)

TECHNOLOGY LAYER (2-3 per capability) — use SPECIFIC infrastructure names, NOT mirrors of ApplicationComponents:
TYPE RATIO RULE: For every 3 technology elements, aim for 1 Node + 1 SystemSoftware + 1 TechnologyService.
If generating 2 elements: 1 Node + 1 SystemSoftware (TechnologyService is optional).
TechnologyService MUST NOT exceed 40% of your technology elements.
- Node (MINIMUM 1 per capability): actual compute nodes — e.g., "GCP Cloud Run Instance", "AWS ECS Fargate Cluster", "On-Premise Application Server". NOT "Node: Email Service". NOT just a mirror of an ApplicationComponent name.
- SystemSoftware (MINIMUM 1 per capability): actual software platforms — e.g., "PostgreSQL 15 Database", "RabbitMQ Message Broker", "nginx Reverse Proxy". NOT "SystemSoftware: AI Order Processing Engine".
- TechnologyService: ONLY for cloud-managed services — e.g., "GCP Cloud Storage Bucket", "Azure Service Bus". NEVER name a TechnologyService the same as an ApplicationComponent.
- CommunicationNetwork, Artifact: as needed

BUSINESS LAYER (2-3 per capability):
- BusinessProcess, BusinessService, BusinessObject, BusinessRole, BusinessActor

MOTIVATION LAYER (EXACTLY 5 per capability — HARD CAP, never exceed 5):
CROSS-CAPABILITY DEDUP RULE: If the same Requirement, Goal, or Stakeholder applies to multiple
capabilities, generate it ONCE under the most relevant capability only. Do NOT repeat it.
- Requirement (MINIMUM 2, MANDATORY — generate before all other motivation elements):
  Name each as a specific verifiable condition, NOT an echo of a Goal name.
  Cover at least 2 of these categories per capability:
  * Performance: "<component> must process <metric> within <threshold>" (e.g., "Email parsing latency < 500ms p95")
  * Security/Compliance: "<data or system> must comply with <standard>" (e.g., "Personal data must comply with GDPR Article 17")
  * Availability: "<service> uptime must be >= <SLA>" (e.g., "Order processing service uptime >= 99.9%")
  * Data: "<data type> must be classified as <level> with PII handling per <policy>"
  * Integration: "<system A> must integrate with <system B> via <protocol> within <latency>"
  WRONG: "Requirement: Reduce manual order processing workload" (that is the Goal, not a Requirement)
  RIGHT: "Requirement: Order extraction accuracy >= 98% with < 2% false-positive rate"
- Goal (1): measurable outcome this capability delivers
- Driver (1): business or technical pressure driving this capability
- Constraint (1): hard limit (budget, timeline, technology, regulatory)
IMPORTANT: The 5-element cap is STRICT. Do not add extra Stakeholder, Assessment, Value, or Meaning
elements on top — they push motivation past the cap and cause layer imbalance.

STRATEGY LAYER (1-2 per capability):
- Capability, CourseOfAction, ValueStream, Resource

Rules:
- Names must be SPECIFIC to this solution (e.g., "Fraud Detection API Gateway" not "API Gateway")
- BANNED GENERIC NAMES — these provide zero information and must never appear:
  "API Gateway", "Frontend Application", "Backend Service", "Core Business Entity",
  "Integration Service", "Data Store", "Business Component", "Application Service",
  "Technology Service", "Business Process". Every name must include the solution domain.
- Every element MUST have: type, name, description, source ("existing" or "derived"), capability_source
- Use "existing" source and exact name only for apps listed in EXISTING APPLICATIONS above
- Avoid duplicate element names across capabilities (merge shared infrastructure elements)
- IMPLEMENTATION LAYER IS MANDATORY: Always generate at least 1 WorkPackage and 1 Plateau
  for the solution, even if only one capability is present. Do NOT omit this layer.
- Outcome and Assessment elements MUST describe specific measurable results, NOT echo other element names
  WRONG: {{"type": "Outcome", "name": "Outcome: Reduce manual order processing workload"}}
  RIGHT: {{"type": "Outcome", "name": "20% reduction in order processing headcount within 6 months"}}

Return ONLY valid JSON (no markdown fences) — application array MUST be populated:
{{
    "application": [{{"type": "ApplicationComponent|ApplicationService|ApplicationInterface|ApplicationFunction|ApplicationProcess|ApplicationInteraction|ApplicationEvent|ApplicationCollaboration|DataObject", "name": "...", "description": "...", "source": "derived", "data_classification": null, "contains_pii": null, "capability_source": "exact cap name"}}],
    "technology": [{{"type": "Node|SystemSoftware|TechnologyService|TechnologyFunction|TechnologyProcess|TechnologyInterface|TechnologyCollaboration|TechnologyInteraction|TechnologyEvent|CommunicationNetwork|Path|Artifact|Device", "name": "...", "description": "...", "source": "derived", "capability_source": "exact cap name"}}],
    "business": [{{"type": "BusinessProcess|BusinessService|BusinessObject|BusinessRole|BusinessActor|BusinessFunction|BusinessCollaboration|BusinessInterface|BusinessInteraction|BusinessEvent|Contract|Representation|Product", "name": "...", "description": "...", "source": "derived", "capability_source": "exact cap name"}}],
    "motivation": [{{"type": "Stakeholder|Driver|Assessment|Goal|Outcome|Requirement|Constraint|Principle|Value|Meaning", "name": "...", "description": "...", "source": "derived", "capability_source": "exact cap name"}}],
    "strategy": [{{"type": "Capability|CourseOfAction|ValueStream|Resource", "name": "...", "description": "...", "source": "derived", "capability_source": "exact cap name"}}],
    "implementation": [{{"type": "WorkPackage|Plateau|Gap|Deliverable|ImplementationEvent", "name": "...", "description": "...", "source": "derived", "capability_source": "exact cap name"}}]
}}"""

# ── Cross-capability relationship generation prompt ───────────────────
RELATIONSHIP_PROMPT = """You are an enterprise architect. Given these ArchiMate elements,
generate COMPREHENSIVE relationships following ArchiMate 3.2 metamodel rules.

SOLUTION CONTEXT:
{problem_summary}

ALL ELEMENTS (by layer):
{elements_summary}

Generate relationships using these ArchiMate 3.2 types:
- Realization: ApplicationService realizes BusinessProcess; CourseOfAction realizes Capability; Goal realizes ValueStream; ValueStream realizes Outcome
- Serving: ApplicationComponent serves BusinessService; TechnologyService serves ApplicationComponent; Capability serves ValueStream
- Composition: ApplicationComponent composed-of DataObject; Node composed-of SystemSoftware
- Aggregation: BusinessFunction aggregates BusinessProcess
- Assignment: Node assigned-to ApplicationComponent; BusinessRole assigned-to BusinessProcess
- Association: Requirement associated-with Goal; Stakeholder associated-with Driver; ValueStream associated-with Capability
- Influence: Driver influences Goal; Assessment influences Requirement
- Flow: ApplicationInterface flow-to ApplicationInterface (data exchange)
- Access: ApplicationService accesses DataObject (read/write)
- Triggering: BusinessEvent triggers BusinessProcess; ValueStream stage triggers next ValueStream stage

MANDATORY CHAINS — every generated architecture MUST include these:
1. Strategy chain: Goal -[realization]-> ValueStream -[association]-> Capability -[realization]-> BusinessProcess
2. Outcome chain: ValueStream -[realization]-> Outcome (the measurable result of executing the stream)
3. Vertical chain: Goal -[realization]-> ValueStream; Capability -[realization]-> BusinessProcess -[realization]-> ApplicationService -[serving]-> ApplicationComponent -[realization]-> Node
4. Technical chain: ApplicationComponent -[realization]-> Node -[composition]-> SystemSoftware (each app has infrastructure)
5. Coverage chain: ApplicationComponent -[serving]-> BusinessService -[realization]-> BusinessProcess (app layer serves business)

RULES:
- Generate at LEAST 3 relationships per element (aim for {target_rel_count} total)
- EVERY element must connect to at least one other element — no islands
- Cross-layer relationships are critical: connect Motivation→Strategy→Business→Application→Technology
- The full canonical chain: Goal→ValueStream→Capability→BusinessProcess→ApplicationService→ApplicationComponent→Node
- ValueStream elements that have no Goal realization and no Outcome realization are incomplete — add them
- Include intra-layer relationships too (e.g., BusinessProcess→BusinessProcess for sub-processes, ValueStream stage→stage triggering)

Return ONLY valid JSON:
{{
    "relationships": [
        {{"source_name": "exact element name", "target_name": "exact element name", "type": "realization|serving|composition|aggregation|assignment|association|influence|flow|access|triggering", "description": "why this relationship exists"}}
    ]
}}"""

DETAIL_FILLING_PROMPT = """You are architecting a solution for this problem:

{problem_summary}

ARCHITECTURE SKELETON (fill in names and descriptions for each placeholder):

{skeleton_description}

EXISTING CATALOG (reuse where possible):
{catalog_context}

VENDOR PRODUCTS AVAILABLE:
{vendor_context}

EXISTING INTERFACES:
{interface_context}

ORGANIZATION TECHNOLOGY STACKS:
{tech_stack_context}

COMPLIANCE CONSTRAINTS:
{compliance_context}

For each placeholder element:
1. Check if an existing catalog item matches — if so, use its exact name and mark "existing": true
2. Check if a vendor product could serve this — mention it in the description
3. If no match, generate a new element specific to this solution and mark "existing": false
4. For DataObjects: include data_classification (public/internal/confidential/restricted) and contains_pii (true/false)

Return ONLY valid JSON:
{{
    "elements": [
        {{"placeholder_name": "[Inferred BusinessProcess for X]", "name": "Actual Process Name", "description": "What it does", "existing": false, "vendor_product": null, "data_classification": null, "contains_pii": null}}
    ]
}}"""

ALL_LAYERS = ("motivation", "strategy", "business", "application", "technology", "implementation")

# ── Implementation layer generation prompt (Pass 1C) ─────────────────────
# Runs whenever implementation layer has < 3 elements after Pass 1/1B.
# Uses constraint + application context derived from the already-merged elements.
IMPLEMENTATION_GENERATION_PROMPT = """You are an enterprise architect generating ArchiMate 3.2 Implementation layer elements.

SOLUTION CONTEXT:
{problem_summary}

CONSTRAINTS IDENTIFIED (use these to derive phases and timelines):
{constraint_elements}

CURRENT STATE (applications/systems being replaced or migrated):
{current_state_apps}

TARGET STATE COMPONENTS (what will be deployed):
{target_state_components}

CAPABILITIES BEING IMPLEMENTED:
{capabilities_text}

Generate a concrete phased migration plan as ArchiMate Implementation elements:

PLATEAU elements (2-3 required) — architecture states:
- "Baseline Plateau": current state — describe what exists today (legacy systems, manual processes)
- "Transition Plateau": intermediate MVP state — first deliverable, partial automation
- "Target Plateau": final desired state after full implementation

WORKPACKAGE elements (3-5 required) — implementation activities:
- Name each as an active verb + outcome: e.g., "Deploy AI Order Processing Engine", "Migrate Payroll Data to Cloud SaaS"
- Each must map to a specific target component and capability
- Include a realistic sequence (infrastructure first, application second, cutover last)

GAP elements (2-3 required) — delta between Baseline and Target:
- Name each as a specific missing capability: e.g., "No automated email order extraction", "COBOL payroll not cloud-compatible"
- These are the architectural gaps the solution closes

DELIVERABLE elements (1-2) — key outputs marking phase completion

Rules:
- Names must be SPECIFIC to this solution context
- Each element needs a concrete description explaining its scope
- Do NOT generate generic elements like "Phase 1" or "Migration Step"

Return ONLY valid JSON:
{{
    "implementation": [
        {{"type": "WorkPackage|Plateau|Gap|Deliverable|ImplementationEvent", "name": "...", "description": "...", "source": "derived"}}
    ]
}}"""


def _filter_hollow_derived(merged: dict) -> dict:
    """Remove elements whose names are mechanical prefix+copy of another element's name.

    Two detection modes:
    1. Prefix pattern: "Outcome: Reduce manual workload" mirrors a Goal named "Reduce manual workload"
    2. Cross-type echo: Capability/BusinessProcess/TechnologyService named identically to a
       semantically parent element (Goal→Capability, Capability→BusinessProcess,
       AppComponent→TechnologyService). These carry zero additive architectural information.
    """
    HOLLOW_PREFIXES = (
        "outcome: ", "assessment: ", "businessprocess: ",
        "applicationservice: ", "technologyservice: ", "capability: ",
        "applicationcomponent: ", "datasource: ",
    )

    # Build a lookup of all existing names (lowercased) before filtering
    all_existing_names = {
        el["name"].lower().strip()
        for layer in ALL_LAYERS
        for el in merged.get(layer, [])
    }

    # Cross-type echo sets: names that each (layer, type) pair must NOT duplicate verbatim.
    # Rule: a derived element that adds nothing beyond echoing a parent-layer name is hollow.
    goal_names = {
        el["name"].lower().strip()
        for el in merged.get("motivation", [])
        if el.get("type") == "Goal"
    }
    driver_names = {
        el["name"].lower().strip()
        for el in merged.get("motivation", [])
        if el.get("type") == "Driver"
    }
    capability_names = {
        el["name"].lower().strip()
        for el in merged.get("strategy", [])
        if el.get("type") == "Capability"
    }
    appcomp_names = {
        el["name"].lower().strip()
        for el in merged.get("application", [])
        if el.get("type") == "ApplicationComponent"
    }

    # Maps (layer, ArchiMate type) → set of parent names this element must NOT echo
    CROSS_TYPE_ECHO_RULES = {
        ("motivation", "Outcome"): goal_names,
        ("motivation", "Assessment"): driver_names | goal_names,
        ("strategy", "Capability"): goal_names,
        ("business", "BusinessProcess"): capability_names,
        ("technology", "TechnologyService"): appcomp_names,
        ("technology", "TechnologyFunction"): appcomp_names,
    }

    filtered_counts = {}
    for layer in ALL_LAYERS:
        original = merged.get(layer, [])
        kept = []
        removed = 0
        for el in original:
            name_lower = el.get("name", "").lower().strip()
            el_type = el.get("type", "")
            is_hollow = False

            # Mode 1: prefix pattern check
            for prefix in HOLLOW_PREFIXES:
                if name_lower.startswith(prefix):
                    stripped = name_lower[len(prefix):]
                    if stripped in all_existing_names:
                        is_hollow = True
                        break

            # Mode 2: cross-type same-name echo check
            if not is_hollow:
                echo_set = CROSS_TYPE_ECHO_RULES.get((layer, el_type))
                if echo_set and name_lower in echo_set:
                    is_hollow = True

            if is_hollow:
                removed += 1
            else:
                kept.append(el)
        if removed:
            filtered_counts[layer] = removed
        merged[layer] = kept

    if filtered_counts:
        logger.info(
            "Hollow-derived filter removed %d elements: %s",
            sum(filtered_counts.values()),
            filtered_counts,
        )
    return merged



def _build_caps_detail(caps: list) -> str:
    """Build enriched capabilities detail string for LLM prompts.

    Includes technical sub-capabilities (ACM patterns, technologies) and application
    coverage gap context so the LLM generates architecture appropriate to the gap.
    """
    lines = []
    for i, c in enumerate(caps):
        cap_name = c.get("name", "?")
        cap_desc = c.get("description", "")
        entry_lines = [f"{i + 1}. {cap_name}: {cap_desc}"]

        # Technical sub-capabilities from ACM catalog
        for tc in (c.get("technical_capabilities") or [])[:3]:
            flag = ""
            if tc.get("is_differentiating"):
                flag = " [DIFFERENTIATING — generate deep, specific elements]"
            elif tc.get("is_foundational"):
                flag = " [FOUNDATIONAL — shared infrastructure element]"
            entry_lines.append(f"   Technical: {tc['name']} ({tc.get('acm_domain', '')}){flag}")
            patterns = tc.get("technology_patterns") or []
            if patterns:
                entry_lines.append(f"   Patterns: {', '.join(str(p) for p in patterns[:4])}")
            techs = tc.get("common_technologies") or []
            if techs:
                entry_lines.append(f"   Technologies: {', '.join(str(t) for t in techs[:6])}")
            maturity = tc.get("industry_maturity", "")
            if maturity:
                entry_lines.append(f"   Maturity: {maturity}")

        # Application coverage gaps
        gaps = c.get("coverage_gaps") or []
        if gaps:
            gap_lines = []
            for g in gaps[:4]:
                pct = g.get("coverage_percentage") or 0
                app = g.get("application_name", "Unknown App")
                status = g.get("gap_status", "")
                priority = g.get("replacement_priority", "")
                approach = g.get("replacement_approach", "")
                debt = g.get("technical_debt_score")
                gline = f"   Existing coverage: {app} covers {pct}%"
                if status:
                    gline += f" [gap: {status}]"
                if priority and priority.lower() not in ("", "none"):
                    gline += f" [replace priority: {priority}]"
                if approach and approach.lower() not in ("", "retain"):
                    gline += f" [approach: {approach}]"
                if debt is not None and debt > 60:
                    gline += f" [tech debt: {debt}/100]"
                gap_lines.append(gline)
            if gaps[0].get("coverage_percentage", 100) < 50:
                entry_lines.append("   ⚠ SIGNIFICANT GAP: generate NEW application components to fill this capability gap")
            entry_lines.extend(gap_lines)
        else:
            entry_lines.append("   Coverage: No existing applications mapped — generate full greenfield architecture")

        lines.append("\n".join(entry_lines))

    return "\n\n".join(lines)


def _parse_enriched_brief_seeds(problem_summary: str) -> tuple:
    """Extract entity seeds, auth model, and business rules from a structured enriched brief.

    The clarification merge step produces ## Core Entities, ## Access Model, and
    ## Business Rules sections when user answers specify domain entities, user roles,
    and system constraints. All three are injected into architecture generation so
    the LLM uses exact names and honours domain rules rather than inferring generics.

    Returns (entity_seeds_str, auth_model_str, business_rules_str) — any may be empty.
    """
    import re
    entity_seeds = ""
    auth_model = ""
    business_rules = ""
    try:
        entity_match = re.search(
            r'##\s*Core Entities\s*\n((?:[^\n#][^\n]*\n?)+?)(?=\n##|\Z)',
            problem_summary, re.IGNORECASE | re.DOTALL,
        )
        if entity_match:
            entity_seeds = entity_match.group(1).strip()

        auth_match = re.search(
            r'##\s*Access Model\s*\n((?:[^\n#][^\n]*\n?)+?)(?=\n##|\Z)',
            problem_summary, re.IGNORECASE | re.DOTALL,
        )
        if auth_match:
            auth_model = auth_match.group(1).strip()

        rules_match = re.search(
            r'##\s*Business Rules\s*\n((?:[^\n#][^\n]*\n?)+?)(?=\n##|\Z)',
            problem_summary, re.IGNORECASE | re.DOTALL,
        )
        if rules_match:
            business_rules = rules_match.group(1).strip()
    except Exception as exc:
        logger.debug("suppressed error in _parse_enriched_brief_seeds (app/modules/architecture_assistant/architecture_generation.py): %s", exc)
    return entity_seeds, auth_model, business_rules


def _build_caps_detail(caps: list) -> str:
    """Build enriched capabilities detail string for LLM prompts.

    Includes technical sub-capabilities (ACM patterns, technologies) and application
    coverage gap context so the LLM generates architecture appropriate to the gap.
    """
    lines = []
    for i, c in enumerate(caps):
        cap_name = c.get("name", "?")
        cap_desc = c.get("description", "")
        entry_lines = [f"{i + 1}. {cap_name}: {cap_desc}"]

        # Technical sub-capabilities from ACM catalog
        for tc in (c.get("technical_capabilities") or [])[:3]:
            flag = ""
            if tc.get("is_differentiating"):
                flag = " [DIFFERENTIATING — generate deep, specific elements]"
            elif tc.get("is_foundational"):
                flag = " [FOUNDATIONAL — shared infrastructure element]"
            entry_lines.append(f"   Technical: {tc['name']} ({tc.get('acm_domain', '')}){flag}")
            patterns = tc.get("technology_patterns") or []
            if patterns:
                entry_lines.append(f"   Patterns: {', '.join(str(p) for p in patterns[:4])}")
            techs = tc.get("common_technologies") or []
            if techs:
                entry_lines.append(f"   Technologies: {', '.join(str(t) for t in techs[:6])}")
            maturity = tc.get("industry_maturity", "")
            if maturity:
                entry_lines.append(f"   Maturity: {maturity}")

        # Application coverage gaps
        gaps = c.get("coverage_gaps") or []
        if gaps:
            gap_lines = []
            for g in gaps[:4]:
                pct = g.get("coverage_percentage") or 0
                app = g.get("application_name", "Unknown App")
                status = g.get("gap_status", "")
                priority = g.get("replacement_priority", "")
                approach = g.get("replacement_approach", "")
                debt = g.get("technical_debt_score")
                gline = f"   Existing coverage: {app} covers {pct}%"
                if status:
                    gline += f" [gap: {status}]"
                if priority and priority.lower() not in ("", "none"):
                    gline += f" [replace priority: {priority}]"
                if approach and approach.lower() not in ("", "retain"):
                    gline += f" [approach: {approach}]"
                if debt is not None and debt > 60:
                    gline += f" [tech debt: {debt}/100]"
                gap_lines.append(gline)
            if gaps[0].get("coverage_percentage", 100) < 50:
                entry_lines.append("   ⚠ SIGNIFICANT GAP: generate NEW application components to fill this capability gap")
            entry_lines.extend(gap_lines)
        else:
            entry_lines.append("   Coverage: No existing applications mapped — generate full greenfield architecture")

        lines.append("\n".join(entry_lines))

    return "\n\n".join(lines)


class ArchitectureGenerationService:
    """Generates comprehensive ArchiMate architectures via per-capability LLM expansion."""

    def generate_greenfield(self, capabilities: list, problem_summary: str,
                            compliance_constraints: list = None,
                            existing_elements: list = None) -> dict:
        """Generate a comprehensive 7-layer architecture by expanding each capability.

        Pipeline:
        0. Pattern matching — seed with reference architecture if problem matches
        1. Semantic search — get relevant apps/vendors per capability (not flat list)
        2. Per-capability LLM expansion across all 7 layers
        3. Merge all clusters, deduplicating by (name, type)
        4. Cross-capability relationship generation
        5. Relationship validation against ArchiMate metamodel

        Args:
            capabilities: List of dicts with name, description
            problem_summary: The enriched problem description
            compliance_constraints: Compliance requirements from Step 2
            existing_elements: Already-persisted elements for this solution (from document
                ingestion or prior generation). Pre-seeds the dedup set so the LLM cannot
                re-create them, and injects them into the prompt as context.

        Returns:
            dict with elements_by_layer, relationships, patterns_applied, errors
        """
        from app.modules.ai_chat.services.llm_service import LLMService

        all_caps_text = "\n".join(
            f"- {c.get('name', '?')}: {c.get('description', '')}"
            for c in capabilities
        )
        tech_ctx = self._build_enriched_tech_context(capabilities)
        compliance_ctx = "\n".join(
            f"- {c.get('framework', '')}: {c.get('name', '')}"
            for c in (compliance_constraints or [])
        ) or "None specified"

        import threading as _threading
        merged = {layer: [] for layer in ALL_LAYERS}
        seen_keys = set()

        # Pre-seed dedup with already-persisted elements so the LLM cannot re-create them.
        # Any generated element with the same (name, type) as an existing element will be
        # silently discarded by _merge_element — no duplicate proposal, no hollow echo.
        existing_elements_block = ""
        if existing_elements:
            for ex in existing_elements:
                ex_name = ex.get("name", "").lower().strip()
                ex_type = ex.get("type", "")
                if ex_name and ex_type:
                    seen_keys.add((ex_name, ex_type))
            # Build prompt block capped at 60 elements to stay within token budget
            block_lines = [
                "ALREADY EXISTING ELEMENTS — these are already in the architecture.",
                "DO NOT re-create them. Only generate elements that are ADDITIVE to this list:",
            ]
            for ex in existing_elements[:60]:
                block_lines.append(f"- {ex.get('type', 'Unknown')}: {ex.get('name', '')}")
            existing_elements_block = "\n".join(block_lines)

        _merge_lock = _threading.Lock()
        errors = []
        patterns_applied = []

        def _merge_element(layer, el_data, source_tag="derived"):
            el_name = el_data.get("name", "Unnamed")
            el_type = el_data.get("type", "Unknown")
            dedup_key = (el_name.lower().strip(), el_type)
            with _merge_lock:
                if dedup_key in seen_keys:
                    return False
                seen_keys.add(dedup_key)
                merged[layer].append({
                    "type": el_type,
                    "name": el_name,
                    "description": el_data.get("description", ""),
                    "source": el_data.get("source", source_tag),
                    "data_classification": el_data.get("data_classification"),
                    "contains_pii": el_data.get("contains_pii"),
                    "capability_source": el_data.get("capability_source", ""),
                })
                return True

        # ── Pass 0: Pattern seeding ───────────────────────────────────
        pattern_relationships = []
        try:
            from app.modules.architecture.services.archimate_pattern_library import ArchiMatePatternLibrary

            library = ArchiMatePatternLibrary()
            # Create a lightweight proxy with the attributes detect_pattern expects
            class _TextProxy:
                def __init__(self, text):
                    self.name = "Solution"
                    self.description = text
                    self.technology_stack = ""
                    self.application_functions_text = text

            matched = library.detect_pattern(_TextProxy(problem_summary))
            if matched and matched.get("confidence", 0) >= 0.6:
                pattern_def = matched.get("pattern", {})
                pattern_id = matched.get("pattern_id", "")
                pattern_name = matched.get("pattern_name", "")
                # Use first 30 chars of problem as app name substitute
                app_label = problem_summary.split(".")[0][:30].strip()
                logger.info("Pattern matched: %s (%.0f%% confidence)", pattern_name, matched["confidence"] * 100)

                seed_count = 0
                for el in pattern_def.get("elements", []):
                    layer = self._element_type_to_layer(el.get("type", ""))
                    if layer and _merge_element(layer, {
                        "type": el["type"],
                        "name": el.get("name", "").replace("{app}", app_label),
                        "description": el.get("description", ""),
                        "source": "pattern",
                        "capability_source": f"pattern:{pattern_name}",
                    }):
                        seed_count += 1

                for rel in pattern_def.get("relationships", []):
                    pattern_relationships.append({
                        "source_name": rel.get("source", "").replace("{app}", app_label),
                        "target_name": rel.get("target", "").replace("{app}", app_label),
                        "type": rel.get("type", "association"),
                        "description": f"From pattern: {pattern_name}",
                    })

                patterns_applied.append({"pattern_id": pattern_id, "name": pattern_name,
                                         "confidence": matched["confidence"], "elements_seeded": seed_count})
                logger.info("Pattern seeded %d elements + %d relationships", seed_count, len(pattern_relationships))
        except Exception as e:
            logger.debug("Pattern matching skipped: %s", e)

        # ── Pass 1: Combined multi-capability expansion (1 LLM call) ──────
        # Replaces N serial per-capability calls to avoid Gunicorn timeout.
        # Falls back to batched calls (4 per batch) if the combined call fails.
        catalog_ctx = self._get_catalog_context()
        vendor_ctx = self._get_vendor_context()

        # Parse entity seeds, auth model, and business rules from the structured enriched brief
        entity_seeds, auth_model, business_rules = _parse_enriched_brief_seeds(problem_summary)

        # Augment problem_summary with business rules for per-capability prompts
        # (CAPABILITY_EXPANSION_PROMPT doesn't have a dedicated {business_rules} slot,
        # but including rules in the problem context ensures the LLM honours them)
        _rules_suffix = (
            f"\n\nBUSINESS RULES (must be reflected in BusinessProcess and ApplicationService elements):\n{business_rules}"
            if business_rules else ""
        )
        problem_summary_with_rules = problem_summary + _rules_suffix

        def _run_combined_call(caps_subset: list) -> dict | None:
            caps_detail = _build_caps_detail(caps_subset)
            prompt = COMBINED_EXPANSION_PROMPT.format(
                cap_count=len(caps_subset),
                problem_summary=problem_summary,
                entity_seeds=entity_seeds or "(none — infer from capability names and problem context)",
                auth_model=auth_model or "(none — default to single authenticated user role)",
                business_rules=business_rules or "(none specified)",
                capabilities_detail=caps_detail,
                catalog_context=catalog_ctx,
                vendor_context=vendor_ctx,
                tech_stack_context=tech_ctx,
                compliance_context=compliance_ctx,
                existing_elements_block=existing_elements_block or "(none — this is a fresh generation)",
            )
            try:
                provider, model = LLMService._get_configured_provider()
                raw_text, _ = LLMService._call_llm(
                    prompt=prompt, model=model, provider=provider, max_tokens=8192
                )
                return self._parse_json_response(raw_text) if raw_text else None
            except Exception as e:
                logger.error("Combined expansion call failed: %s", e)
                return None

        def _ingest_parsed(parsed: dict, cap_count: int) -> int:
            total = 0
            for layer in ALL_LAYERS:
                for el in parsed.get(layer, []):
                    if _merge_element(layer, el):
                        total += 1
            return total

        # ── Pass 1 strategy ──────────────────────────────────────────────
        # >4 caps always exceeds 8192 max_tokens in a single combined call.
        # Skip the doomed all-caps attempt; batch into groups of 3 and run
        # all batches IN PARALLEL so latency ≈ 1 batch (~2 min) not N×2 min.
        # For ≤4 caps try the combined call first — it fits and is fastest.
        from concurrent.futures import ThreadPoolExecutor, as_completed as _as_completed

        def _run_batch(batch):
            """Run one batch: combined call first, per-cap last resort. Thread-safe."""
            parsed = _run_combined_call(batch)
            if parsed:
                return parsed, [c.get("name") for c in batch], []
            # Per-cap last resort (batch combined call also failed)
            per_cap_results = {}
            cap_errors = []
            for cap in batch:
                cap_name = cap.get("name", "Unknown Capability")
                cap_desc = cap.get("description", "")
                cap_catalog = self._get_semantic_catalog_context(cap_name, cap_desc)
                cap_vendor = self._get_semantic_vendor_context(cap_name, cap_desc)
                prompt = CAPABILITY_EXPANSION_PROMPT.format(
                    problem_summary=problem_summary_with_rules,
                    capability_name=cap_name,
                    capability_description=cap_desc,
                    all_capabilities=all_caps_text,
                    catalog_context=cap_catalog,
                    vendor_context=cap_vendor,
                    tech_stack_context=tech_ctx,
                    compliance_context=compliance_ctx,
                )
                try:
                    provider, model = LLMService._get_configured_provider()
                    raw_text, _ = LLMService._call_llm(
                        prompt=prompt, model=model, provider=provider, max_tokens=8192
                    )
                    if raw_text:
                        per_parsed = self._parse_json_response(raw_text)
                        if per_parsed:
                            for layer in ALL_LAYERS:
                                for el in per_parsed.get(layer, []):
                                    el["capability_source"] = cap_name
                            per_cap_results[cap_name] = per_parsed
                except Exception as e:
                    logger.error("Per-cap fallback failed for '%s': %s", cap_name, e)
                    cap_errors.append(f"Expansion error for {cap_name}: {str(e)}")
            merged_batch: dict = {layer: [] for layer in ALL_LAYERS}
            for per in per_cap_results.values():
                for layer in ALL_LAYERS:
                    merged_batch[layer].extend(per.get(layer, []))
            return (merged_batch if any(merged_batch.values()) else None,
                    list(per_cap_results.keys()), cap_errors)

        batch_size = 3

        if len(capabilities) <= 4:
            logger.info("Pass 1: single combined call for %d capabilities", len(capabilities))
            combined_parsed = _run_combined_call(capabilities)
            if combined_parsed:
                _ingest_parsed(combined_parsed, len(capabilities))
                logger.info("Combined expansion: %d elements", sum(len(merged[item]) for item in ALL_LAYERS))
            else:
                logger.warning("Combined call failed — parallel batch fallback")
                errors.append("Combined expansion failed; used parallel batch fallback")
                batches = [capabilities[i:i + batch_size] for i in range(0, len(capabilities), batch_size)]
                with ThreadPoolExecutor(max_workers=len(batches)) as pool:
                    futs = {pool.submit(_run_batch, b): b for b in batches}
                    for fut in _as_completed(futs):
                        batch_result, cap_names, batch_errs = fut.result()
                        if batch_result:
                            _ingest_parsed(batch_result, len(futs[fut]))
                        errors.extend(batch_errs or [])
        else:
            # >4 caps: skip all-caps combined call (always truncates at 8192 tokens)
            batches = [capabilities[i:i + batch_size] for i in range(0, len(capabilities), batch_size)]
            logger.info(
                "Pass 1: parallel batch expansion — %d caps → %d concurrent batches of ≤%d",
                len(capabilities), len(batches), batch_size,
            )
            with ThreadPoolExecutor(max_workers=len(batches)) as pool:
                futs = {pool.submit(_run_batch, b): b for b in batches}
                for fut in _as_completed(futs):
                    batch_result, cap_names, batch_errs = fut.result()
                    if batch_result:
                        _ingest_parsed(batch_result, len(futs[fut]))
                        logger.info("Batch %s: ingested", cap_names)
                    else:
                        logger.warning("Batch %s: no elements produced", cap_names)
                    errors.extend(batch_errs or [])

        # ── Pass 1B: Fill missing critical layers via per-capability calls ──
        # The combined call and batch fallback both truncate at 8192 tokens — which
        # layers end up empty depends on ordering in the LLM response.  Cover all 5
        # non-trivial layers so Pass 1B fires regardless of which layers were cut.
        _required_layers = ("motivation", "strategy", "business", "application", "technology", "implementation")
        _empty_required = [item for item in _required_layers if not merged.get(item)]
        if _empty_required:
            logger.warning(
                "Pass 1B: layers %s empty after batched expansion — running per-capability fill",
                _empty_required,
            )
            # Use up to 3 capabilities to populate missing layers (avoid token budget exhaustion)
            fill_caps = capabilities[:3]
            for cap in fill_caps:
                cap_name = cap.get("name", "Unknown Capability")
                cap_desc = cap.get("description", "")
                cap_catalog = self._get_semantic_catalog_context(cap_name, cap_desc)
                cap_vendor = self._get_semantic_vendor_context(cap_name, cap_desc)
                prompt = CAPABILITY_EXPANSION_PROMPT.format(
                    problem_summary=problem_summary_with_rules,
                    capability_name=cap_name,
                    capability_description=cap_desc,
                    all_capabilities=all_caps_text,
                    catalog_context=cap_catalog,
                    vendor_context=cap_vendor,
                    tech_stack_context=tech_ctx,
                    compliance_context=compliance_ctx,
                )
                try:
                    provider, model = LLMService._get_configured_provider()
                    raw_text, _ = LLMService._call_llm(
                        prompt=prompt, model=model, provider=provider, max_tokens=8192
                    )
                    if raw_text:
                        per_parsed = self._parse_json_response(raw_text)
                        if per_parsed:
                            for layer in _empty_required:
                                for el in per_parsed.get(layer, []):
                                    el["capability_source"] = cap_name
                                    _merge_element(layer, el)
                    logger.info("Pass 1B fill for '%s': done", cap_name)
                except Exception as e:
                    logger.error("Pass 1B fill failed for '%s': %s", cap_name, e)

        # ── Pass 1C: Dedicated Implementation layer generation ───────────
        # The combined pass reliably under-fills Implementation (lowest LLM priority,
        # first truncated at 8192 tokens). Run a targeted call whenever < 2 elements.
        if len(merged.get("implementation", [])) < 2:
            try:
                _constraint_els = [
                    el["name"] + ": " + el.get("description", "")
                    for el in merged.get("motivation", [])
                    if el.get("type") == "Constraint"
                ][:10]
                _current_apps = self._get_catalog_context()[:800]
                _target_comps = [
                    el["name"]
                    for el in merged.get("application", [])
                    if el.get("type") == "ApplicationComponent"
                ][:15]
                _caps_text = "\n".join(
                    f"- {c.get('name', '')}: {c.get('description', '')}"
                    for c in capabilities
                )
                impl_prompt = IMPLEMENTATION_GENERATION_PROMPT.format(
                    problem_summary=problem_summary,
                    constraint_elements="\n".join(_constraint_els) or "None captured",
                    current_state_apps=_current_apps or "Not specified",
                    target_state_components="\n".join(_target_comps) or "Not specified",
                    capabilities_text=_caps_text,
                )
                provider, model = LLMService._get_configured_provider()
                raw_text, _ = LLMService._call_llm(
                    prompt=impl_prompt, model=model, provider=provider, max_tokens=4096
                )
                if raw_text:
                    impl_parsed = self._parse_json_response(raw_text)
                    if impl_parsed:
                        added = 0
                        for el in impl_parsed.get("implementation", []):
                            if _merge_element("implementation", el):
                                added += 1
                        logger.info("Pass 1C: %d implementation elements generated", added)
            except Exception as e:
                logger.error("Pass 1C implementation generation failed: %s", e)

        # ── Post-Pass 1: Filter hollow derived elements ───────────────
        # Remove mechanical prefix+copy patterns (e.g. "Outcome: Reduce manual workload",
        # "TechnologyService: Email Service") that mirror existing element names verbatim.
        merged = _filter_hollow_derived(merged)

        total_elements = sum(len(v) for v in merged.values())
        logger.info("Pass 1 complete: %d total elements across %d capabilities", total_elements, len(capabilities))

        # ── Pass 2: Cross-capability relationship generation ──────────
        relationships = list(pattern_relationships)  # start with pattern rels
        if total_elements > 0:
            try:
                # Cap elements_summary to 120 elements (keep most important layers first)
                # to avoid input token overflow on large solutions
                _summary_merged = {}
                _summary_cap = 120
                _summary_count = 0
                for _sl in ["strategy", "business", "application", "technology",
                            "motivation", "implementation"]:
                    _sl_els = merged.get(_sl, [])
                    remaining = _summary_cap - _summary_count
                    if remaining <= 0:
                        break
                    _summary_merged[_sl] = _sl_els[:remaining]
                    _summary_count += len(_summary_merged[_sl])
                elements_summary = self._build_elements_summary(_summary_merged)

                # Cap target_rels to avoid response token overflow.
                # Each relationship JSON object ≈ 50-80 tokens; 8192 output tokens ≈ 100-160 rels max.
                # Use 60 as a safe cap leaving room for JSON framing.
                target_rels = min(max(20, total_elements // 2), 60)

                rel_prompt = RELATIONSHIP_PROMPT.format(
                    problem_summary=problem_summary,
                    elements_summary=elements_summary,
                    target_rel_count=target_rels,
                )

                provider, model = LLMService._get_configured_provider()
                # Use higher max_tokens for relationship generation — responses are larger arrays.
                # Anthropic supports 32K, OpenAI/Azure 16K, DeepSeek/Gemini 8K.
                _rel_max_tokens = LLMService.get_max_tokens_limit(provider, model, requested_max=16000)
                raw_text, _ = LLMService._call_llm(
                    prompt=rel_prompt, model=model, provider=provider, max_tokens=_rel_max_tokens
                )

                if raw_text:
                    parsed = self._parse_json_response(raw_text)
                    if parsed:
                        for rel in parsed.get("relationships", []):
                            relationships.append({
                                "source_name": rel.get("source_name", ""),
                                "target_name": rel.get("target_name", ""),
                                "type": rel.get("type", "association"),
                                "description": rel.get("description", ""),
                            })

                logger.info("Pass 2 complete: %d relationships generated", len(relationships))

            except Exception as e:
                logger.error("Relationship generation failed: %s", e)
                errors.append(f"Relationship generation error: {str(e)}")

        # ── Pass 3: Validate relationships against ArchiMate metamodel ─
        validated_rels, rejected_count = self._validate_relationships(relationships, merged)
        if rejected_count > 0:
            logger.info("Pass 3: %d/%d relationships rejected by metamodel validation",
                        rejected_count, len(relationships))

        return {
            "elements_by_layer": merged,
            "relationships": validated_rels,
            "patterns_applied": patterns_applied,
            "errors": errors,
        }

    def generate_gap_fill(self, gaps: list, problem_summary: str,
                          existing_elements: list = None) -> dict:
        """Fill missing (capability, layer) pairs via a single focused LLM call.

        Called after the main generation + classification pass to plug coverage gaps.
        Only generates elements additive to what already exists — pre-seeds dedup
        so nothing already in the architecture is re-created.

        Args:
            gaps: list of dicts with keys: capability (str), layer (str)
            problem_summary: enriched problem description
            existing_elements: all elements already persisted for this solution

        Returns:
            dict with elements_by_layer (same shape as generate_greenfield return value)
        """
        from app.modules.ai_chat.services.llm_service import LLMService

        if not gaps:
            return {"elements_by_layer": {layer: [] for layer in ALL_LAYERS}, "errors": []}

        # Build what's missing
        gap_lines = "\n".join(
            f"- Capability '{g['capability']}' is missing elements in the {g['layer']} layer"
            for g in gaps
        )

        # Build existing elements block (same pre-seeding as greenfield)
        seen_keys: set = set()
        existing_block_lines = [
            "ALREADY EXISTING ELEMENTS — do NOT re-create these:",
        ]
        for ex in (existing_elements or []):
            ex_name = ex.get("name", "").lower().strip()
            ex_type = ex.get("type", "")
            if ex_name and ex_type:
                seen_keys.add((ex_name, ex_type))
                existing_block_lines.append(f"- {ex_type}: {ex.get('name', '')}")
        existing_block = "\n".join(existing_block_lines[:62])  # cap at token budget

        prompt = (
            "You are an expert enterprise architect using ArchiMate 3.2.\n"
            "The architecture generation pipeline has coverage gaps that need filling.\n\n"
            f"SOLUTION CONTEXT:\n{problem_summary}\n\n"
            f"COVERAGE GAPS TO FILL:\n{gap_lines}\n\n"
            f"{existing_block}\n\n"
            "For each gap listed above, generate 3-6 specific ArchiMate elements in that layer "
            "for that capability. Names must be SPECIFIC to the solution (e.g., "
            "'Fraud Detection Audit Log' not 'Audit Log').\n\n"
            "Return ONLY valid JSON with this structure:\n"
            "{{\n"
            '  "motivation": [{{"type": "...", "name": "...", "description": "...", "source": "derived", "capability_source": "..."}}],\n'
            '  "strategy":   [...],\n'
            '  "business":   [...],\n'
            '  "application":[...],\n'
            '  "technology": [...],\n'
            '  "implementation": [...]\n'
            "}}"
        )

        result: dict = {layer: [] for layer in ALL_LAYERS}
        errors: list = []

        try:
            provider, model = LLMService._get_configured_provider()
            raw_text, _ = LLMService._call_llm(
                prompt=prompt, model=model, provider=provider, max_tokens=4096
            )
            if not raw_text:
                errors.append("gap_fill: LLM returned empty response")
                return {"elements_by_layer": result, "errors": errors}

            parsed = self._parse_json_response(raw_text)
            if not parsed:
                errors.append("gap_fill: could not parse LLM JSON response")
                return {"elements_by_layer": result, "errors": errors}

            for layer in ALL_LAYERS:
                for el in parsed.get(layer, []):
                    el_name = el.get("name", "").lower().strip()
                    el_type = el.get("type", "")
                    dedup_key = (el_name, el_type)
                    if dedup_key in seen_keys:
                        continue
                    seen_keys.add(dedup_key)
                    result[layer].append({
                        "type": el_type,
                        "name": el.get("name", "Unnamed"),
                        "description": el.get("description", ""),
                        "source": "derived",
                        "data_classification": el.get("data_classification"),
                        "contains_pii": el.get("contains_pii"),
                        "capability_source": el.get("capability_source", gaps[0]["capability"] if gaps else ""),
                    })

        except Exception as e:
            logger.error("generate_gap_fill failed: %s", e)
            errors.append(f"gap_fill error: {str(e)}")

        total = sum(len(v) for v in result.values())
        logger.info("generate_gap_fill: %d elements generated for %d gaps", total, len(gaps))
        return {"elements_by_layer": result, "errors": errors}

    def generate_from_skeleton(self, journey_graph, problem_summary: str, compliance_constraints: list = None):
        """Fill all placeholder elements in the skeleton with LLM-generated details.

        Args:
            journey_graph: JourneyGraph instance with skeleton already created
            problem_summary: The enriched problem description
            compliance_constraints: List of compliance requirement dicts

        Returns:
            dict with {elements_updated, errors}
        """
        facade = journey_graph.facade
        all_nodes = facade.find_nodes(element_type=None, filters={})

        # Find placeholder nodes (those with [Inferred ...] names)
        placeholders = [n for n in all_nodes if n.name.startswith("[Inferred")]

        if not placeholders:
            return {"elements_updated": 0, "errors": []}

        # Build context for LLM
        skeleton_desc = "\n".join(
            f"- {n.element_type} ({n.layer or 'unknown'}): {n.name}"
            for n in placeholders
        )
        catalog_ctx = self._get_catalog_context()
        vendor_ctx = self._get_vendor_context()
        interface_ctx = self._get_interface_context()
        tech_ctx = self._get_tech_stack_context()
        compliance_ctx = "\n".join(
            f"- {c.get('framework', '')}: {c.get('name', '')}"
            for c in (compliance_constraints or [])
        ) or "None specified"

        prompt = DETAIL_FILLING_PROMPT.format(
            problem_summary=problem_summary,
            skeleton_description=skeleton_desc,
            catalog_context=catalog_ctx,
            vendor_context=vendor_ctx,
            interface_context=interface_ctx,
            tech_stack_context=tech_ctx,
            compliance_context=compliance_ctx,
        )

        try:
            from app.modules.ai_chat.services.llm_service import LLMService
            provider, model = LLMService._get_configured_provider()
            raw_text, _ = LLMService._call_llm(prompt=prompt, model=model, provider=provider)

            if not raw_text:
                return {"elements_updated": 0, "errors": ["LLM returned empty response"]}

            parsed = self._parse_json_response(raw_text)
            if not parsed:
                return {"elements_updated": 0, "errors": ["Failed to parse LLM response"]}

            elements = parsed.get("elements", [])

            # Build lookup: placeholder_name -> new details
            detail_map = {}
            for el in elements:
                pname = el.get("placeholder_name", "")
                if pname:
                    detail_map[pname] = el

            # Update placeholder nodes
            updated = 0
            from app.models.archimate_core import ArchiMateElement
            for node in placeholders:
                details = detail_map.get(node.name)
                if not details:
                    continue

                element = ArchiMateElement.query.get(node.id)
                if element:
                    element.name = details.get("name", node.name)
                    element.description = details.get("description", "")
                    updated += 1

            db.session.commit()
            return {"elements_updated": updated, "errors": []}

        except Exception as e:
            logger.error("Architecture generation failed: %s", e)
            return {"elements_updated": 0, "errors": [str(e)]}

    # ── Helpers ────────────────────────────────────────────────────────

    def _parse_json_response(self, raw_text: str) -> dict | None:
        """Extract and parse JSON from an LLM response.

        Attempts three escalating repair strategies before giving up:
        1. Direct parse
        2. Fix common missing-comma patterns (} { and "value" "key")
        3. Extract individual layers via regex (handles truncated responses)
        """
        text = raw_text.strip()
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```\s*$', '', text)
        json_start = text.find("{")
        json_end = text.rfind("}") + 1

        if json_start < 0 or json_end <= json_start:
            return None

        candidate = text[json_start:json_end]

        # Strategy 1: direct parse
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as e:
            logger.error("JSON parse error: %s", e)

        # Strategy 2: fix common LLM JSON errors (missing commas)
        try:
            repaired = candidate
            # "value" "key": → "value", "key":  (missing comma between properties)
            repaired = re.sub(r'("(?:[^"\\]|\\.)*")\s*("(?:[^"\\]|\\.)*"\s*:)', r'\1, \2', repaired)
            # } { → }, {  (missing comma between array objects)
            repaired = re.sub(r'\}\s*\{', '}, {', repaired)
            result = json.loads(repaired)
            logger.info("JSON repaired successfully with missing-comma fix")
            return result
        except json.JSONDecodeError:
            pass

        # Strategy 3: extract individual layers using bracket-depth tracking
        # (handles truncation and `]` chars inside string values)
        layer_result = {}
        all_layers = ["motivation", "strategy", "business", "application",
                      "technology", "implementation", "physical"]
        for layer in all_layers:
            key_pattern = rf'"{layer}"\s*:\s*\['
            km = re.search(key_pattern, candidate)
            if not km:
                continue
            start = km.end() - 1  # position of the opening `[`
            depth = 0
            in_string = False
            escape_next = False
            end = -1
            for i in range(start, len(candidate)):
                ch = candidate[i]
                if escape_next:
                    escape_next = False
                    continue
                if ch == "\\" and in_string:
                    escape_next = True
                    continue
                if ch == '"':
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if ch == "[":
                    depth += 1
                elif ch == "]":
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
            if end > start:
                try:
                    layer_result[layer] = json.loads(candidate[start:end])
                except json.JSONDecodeError:
                    pass

        # Strategy 4: if layer array is truncated, extract complete objects up to truncation point
        if not layer_result:
            for layer in all_layers:
                key_pattern = rf'"{layer}"\s*:\s*\['
                km = re.search(key_pattern, candidate)
                if not km:
                    continue
                arr_start = km.end() - 1
                # Try to extract as many complete objects as possible
                objects = []
                pos = arr_start + 1
                while pos < len(candidate):
                    # Skip whitespace and commas
                    while pos < len(candidate) and candidate[pos] in ' \t\n\r,':
                        pos += 1
                    if pos >= len(candidate) or candidate[pos] == ']':
                        break
                    if candidate[pos] != '{':
                        break
                    # Track object boundaries
                    obj_start = pos
                    depth = 0
                    in_str = False
                    esc = False
                    obj_end = -1
                    for i in range(pos, len(candidate)):
                        c = candidate[i]
                        if esc:
                            esc = False
                            continue
                        if c == '\\' and in_str:
                            esc = True
                            continue
                        if c == '"':
                            in_str = not in_str
                            continue
                        if in_str:
                            continue
                        if c == '{':
                            depth += 1
                        elif c == '}':
                            depth -= 1
                            if depth == 0:
                                obj_end = i + 1
                                break
                    if obj_end < 0:
                        break  # incomplete object, stop
                    try:
                        obj = json.loads(candidate[obj_start:obj_end])
                        objects.append(obj)
                        pos = obj_end
                    except json.JSONDecodeError:
                        break
                if objects:
                    layer_result[layer] = objects

        if layer_result:
            total_els = sum(len(v) for v in layer_result.values())
            logger.info("JSON partially recovered: %d layer(s), %d elements via bracket-depth fallback",
                        len(layer_result), total_els)
            return layer_result

        # Strategy 5: extract "relationships" array from truncated relationship-prompt responses.
        # Handles the case where the LLM outputs {"relationships": [...]} but JSON is truncated
        # mid-array (strategies 1-4 only handle layer-keyed responses).
        rel_key_match = re.search(r'"relationships"\s*:\s*\[', candidate)
        if rel_key_match:
            arr_start = rel_key_match.end() - 1
            rel_objects: list = []
            pos = arr_start + 1
            while pos < len(candidate):
                while pos < len(candidate) and candidate[pos] in ' \t\n\r,':
                    pos += 1
                if pos >= len(candidate) or candidate[pos] == ']':
                    break
                if candidate[pos] != '{':
                    break
                obj_start = pos
                depth = 0
                in_str = False
                esc = False
                obj_end = -1
                for i in range(pos, len(candidate)):
                    c = candidate[i]
                    if esc:
                        esc = False
                        continue
                    if c == '\\' and in_str:
                        esc = True
                        continue
                    if c == '"':
                        in_str = not in_str
                        continue
                    if in_str:
                        continue
                    if c == '{':
                        depth += 1
                    elif c == '}':
                        depth -= 1
                        if depth == 0:
                            obj_end = i + 1
                            break
                if obj_end < 0:
                    break
                try:
                    obj = json.loads(candidate[obj_start:obj_end])
                    rel_objects.append(obj)
                    pos = obj_end
                except json.JSONDecodeError:
                    break
            if rel_objects:
                logger.info(
                    "JSON partially recovered: %d relationships via bracket-depth fallback (Strategy 5)",
                    len(rel_objects),
                )
                return {"relationships": rel_objects}

        logger.error("JSON parse failed — all repair strategies exhausted")
        return None

    def _build_elements_summary(self, elements_by_layer: dict) -> str:
        """Build a compact text summary of all elements for the relationship prompt."""
        lines = []
        for layer in ALL_LAYERS:
            elements = elements_by_layer.get(layer, [])
            if elements:
                lines.append(f"\n{layer.upper()} LAYER ({len(elements)} elements):")
                for el in elements:
                    lines.append(f"  - [{el['type']}] {el['name']}: {el.get('description', '')[:80]}")
        return "\n".join(lines)

    def get_vendor_products(self, capability_ids: list = None):
        """Query vendor products relevant to the solution's capabilities."""
        try:
            from app.models.vendor.vendor_organization import VendorProduct
            products = VendorProduct.query.limit(20).all()
            return [{
                "id": p.id,
                "name": p.name,
                "vendor_name": getattr(p, "vendor_name", ""),
                "deployment_type": getattr(p, "deployment_type", ""),
                "hosting_model": getattr(p, "hosting_model", ""),
            } for p in products]
        except Exception as e:
            logger.debug("Vendor product query failed: %s", e)
            return []

    def get_existing_interfaces(self, app_ids: list = None):
        """Query existing application interfaces."""
        try:
            from app.models.application_layer import ApplicationInterface
            interfaces = ApplicationInterface.query.limit(20).all()
            return [{
                "id": i.id,
                "application_name": getattr(i, "application_name", ""),
                "interface_type": getattr(i, "interface_type", ""),
                "protocol": getattr(i, "protocol", ""),
                "data_format": getattr(i, "data_format", ""),
            } for i in interfaces]
        except Exception as e:
            logger.debug("Interface query failed: %s", e)
            return []

    def get_technology_stacks(self):
        """Aggregate existing technology stacks from applications."""
        try:
            from app.models.application_portfolio import ApplicationComponent
            apps = ApplicationComponent.query.filter(
                ApplicationComponent.technology_stack.isnot(None)
            ).limit(50).all()
            stacks = {}
            for app in apps:
                ts = app.technology_stack
                if isinstance(ts, str):
                    try:
                        ts = json.loads(ts)
                    except (json.JSONDecodeError, TypeError):
                        continue
                if isinstance(ts, list):
                    for tech in ts:
                        stacks[tech] = stacks.get(tech, 0) + 1
            return sorted(stacks.items(), key=lambda x: x[1], reverse=True)[:20]
        except Exception as e:
            logger.debug("Tech stack query failed: %s", e)
            return []

    def _get_catalog_context(self) -> str:
        """Return flat application catalog with integer IDs for combined prompts."""
        try:
            apps = applications_query().limit(40).all()
            if not apps:
                return "No applications in catalog"
            return "\n".join("- [id:%d] %s" % (a.id, a.name) for a in apps)
        except Exception:
            return "No applications in catalog"

    def _get_vendor_context(self) -> str:
        """Return flat vendor product list for combined prompts."""
        products = self.get_vendor_products()
        if not products:
            return "No vendor products in catalog"
        return "\n".join(
            "- %s (%s)" % (p["name"], p.get("vendor_name", "Unknown vendor"))
            for p in products
        )

    def _get_semantic_catalog_context(self, capability_name: str, capability_desc: str) -> str:
        """Use semantic search to find the most relevant applications for a capability."""
        try:
            from app.services.semantic_search_service import get_semantic_search_service
            query = f"{capability_name}: {capability_desc}"
            # Was SemanticSearchService.semantic_search(...) - called on the CLASS,
            # so self was never bound and query landed in the self slot.
            results = get_semantic_search_service().semantic_search(
                query=query, domain="applications", top_k=15,
            )
            if results:
                lines = []
                for r in results:
                    name = r.get("name") or r.get("title", "Unknown")
                    desc = (r.get("description") or "")[:100]
                    score = r.get("similarity_score", r.get("score", 0))
                    lines.append(f"- {name} (relevance: {score:.0%}): {desc}")
                return "\n".join(lines)
        except Exception as e:
            logger.debug("Semantic catalog search failed, falling back to flat list: %s", e)

        # Fallback: flat list
        try:
            apps = applications_query().limit(30).all()
            return "\n".join(f"- {a.name}" for a in apps) or "No applications in catalog"
        except Exception:
            return "No applications in catalog"

    def _get_semantic_vendor_context(self, capability_name: str, capability_desc: str) -> str:
        """Use semantic search to find the most relevant vendor products for a capability."""
        try:
            from app.services.semantic_search_service import get_semantic_search_service
            query = f"{capability_name}: {capability_desc}"
            results = get_semantic_search_service().semantic_search(
                query=query, domain="vendors", top_k=10,
            )
            if results:
                lines = []
                for r in results:
                    name = r.get("name") or r.get("title", "Unknown")
                    vendor = r.get("vendor_name", "")
                    desc = (r.get("description") or "")[:80]
                    lines.append(f"- {name} ({vendor}): {desc}")
                return "\n".join(lines)
        except Exception as e:
            logger.debug("Semantic vendor search failed, falling back to flat list: %s", e)

        # Fallback: flat list
        products = self.get_vendor_products()
        if not products:
            return "No vendor products in catalog"
        return "\n".join(f"- {p['name']} ({p.get('vendor_name', 'Unknown vendor')})" for p in products)

    def _get_interface_context(self):
        interfaces = self.get_existing_interfaces()
        if not interfaces:
            return "No interfaces cataloged"
        return "\n".join(
            f"- {i.get('application_name', '?')}: {i.get('interface_type', '?')} ({i.get('protocol', '?')})"
            for i in interfaces
        )

    def _build_enriched_tech_context(self, capabilities: list) -> str:
        """Build tech_stack_context enriched with per-capability ACM technology patterns.

        Combines the org-wide technology stack baseline with capability-specific
        technology patterns and common_technologies from TechnicalCapability records.
        """
        baseline = self._get_tech_stack_context()

        cap_tech_lines = []
        for c in capabilities:
            cap_name = c.get("name", "")
            tech_caps = c.get("technical_capabilities") or []
            all_techs = []
            for tc in tech_caps:
                all_techs.extend(tc.get("common_technologies") or [])
                all_techs.extend(tc.get("technology_patterns") or [])
            if all_techs:
                unique = list(dict.fromkeys(str(t) for t in all_techs))[:8]
                cap_tech_lines.append(f"- {cap_name}: {', '.join(unique)}")

        if not cap_tech_lines:
            return baseline

        return (
            baseline
            + "\n\nCAPABILITY-SPECIFIC TECHNOLOGY PATTERNS (use these technologies when generating elements for each capability):\n"
            + "\n".join(cap_tech_lines)
        )

    def _get_tech_stack_context(self):
        stacks = self.get_technology_stacks()
        if not stacks:
            return "No technology stack data"
        return "\n".join(f"- {tech}: used by {count} apps" for tech, count in stacks)

    def _validate_relationships(self, relationships: list, elements_by_layer: dict) -> tuple:
        """Validate relationships against ArchiMate 3.2 metamodel. Returns (valid_rels, rejected_count)."""
        try:
            from app.modules.architecture.services.archimate_validator import ArchiMateValidator
            validator = ArchiMateValidator()
        except Exception as e:
            logger.debug("Validator not available, skipping relationship validation: %s", e)
            return relationships, 0

        # Build name→type lookup
        name_to_type = {}
        for layer_els in elements_by_layer.values():
            for el in layer_els:
                name_to_type[el["name"]] = el["type"]

        validated = []
        rejected = 0
        for rel in relationships:
            source_type = name_to_type.get(rel.get("source_name"))
            target_type = name_to_type.get(rel.get("target_name"))
            rel_type = rel.get("type", "association")

            if not source_type or not target_type:
                # Element not found — keep the relationship (may reference external elements)
                validated.append(rel)
                continue

            try:
                allowed = validator.get_allowed_relationship_types(source_type, target_type)
                # Normalize case for comparison
                rel_type_lower = rel_type.lower()
                allowed_lower = [a.lower() for a in allowed]

                if rel_type_lower in allowed_lower:
                    # Relationship type is explicitly valid — keep as-is
                    validated.append(rel)
                elif allowed:
                    # Type is invalid but alternatives exist — correct to first valid option.
                    # Do NOT fall back to "association" just because it is universally allowed;
                    # that would hide real type errors from the architect.
                    corrected = allowed[0]
                    rel = dict(rel)  # don't mutate original
                    rel["type"] = corrected
                    rel["description"] = (
                        (rel.get("description") or "")
                        + f" [type corrected: {rel_type}→{corrected}]"
                    ).strip()
                    validated.append(rel)
                    logger.debug(
                        "Corrected relationship type: %s -[%s→%s]-> %s",
                        source_type, rel_type, corrected, target_type,
                    )
                else:
                    rejected += 1
                    logger.debug(
                        "Rejected relationship: %s -[%s]-> %s (no valid type exists)",
                        source_type, rel_type, target_type,
                    )
            except Exception:
                # Validator error — keep the relationship unchanged
                validated.append(rel)

        return validated, rejected

    @staticmethod
    def _element_type_to_layer(element_type: str) -> str | None:
        """Map ArchiMate element type to layer key."""
        _map = {
            # Motivation
            "Stakeholder": "motivation", "Driver": "motivation", "Assessment": "motivation",
            "Goal": "motivation", "Outcome": "motivation", "Principle": "motivation",
            "Requirement": "motivation", "Constraint": "motivation",
            "Value": "motivation", "Meaning": "motivation",
            # Strategy
            "Capability": "strategy", "CourseOfAction": "strategy",
            "ValueStream": "strategy", "Resource": "strategy",
            # Business
            "BusinessActor": "business", "BusinessRole": "business",
            "BusinessCollaboration": "business", "BusinessInterface": "business",
            "BusinessProcess": "business", "BusinessFunction": "business",
            "BusinessInteraction": "business", "BusinessEvent": "business",
            "BusinessService": "business", "BusinessObject": "business",
            "Contract": "business", "Representation": "business", "Product": "business",
            # Application
            "ApplicationComponent": "application", "ApplicationCollaboration": "application",
            "ApplicationInterface": "application", "ApplicationFunction": "application",
            "ApplicationInteraction": "application", "ApplicationProcess": "application",
            "ApplicationEvent": "application", "ApplicationService": "application",
            "DataObject": "application",
            # Technology
            "Node": "technology", "Device": "technology", "SystemSoftware": "technology",
            "TechnologyCollaboration": "technology", "TechnologyInterface": "technology",
            "Path": "technology", "CommunicationNetwork": "technology",
            "TechnologyFunction": "technology", "TechnologyProcess": "technology",
            "TechnologyInteraction": "technology", "TechnologyEvent": "technology",
            "TechnologyService": "technology", "Artifact": "technology",
            # Physical
            "Equipment": "physical", "Facility": "physical",
            "DistributionNetwork": "physical", "Material": "physical",
            # Implementation & Migration
            "WorkPackage": "implementation", "Deliverable": "implementation",
            "ImplementationEvent": "implementation", "Plateau": "implementation",
            "Gap": "implementation",
        }
        return _map.get(element_type)
