"""Enterprise Context Assembler for AI-powered architecture generation.

Gathers relevant enterprise portfolio data (applications, ArchiMate elements,
vendors, capabilities, principles, prior solutions) and formats it as
structured context for LLM prompts.  This replaces the thin
"TOGAF Phase {phase}" context string with a rich enterprise snapshot so that
generated architectures reference real entities instead of fabricated ones.

Reuses three existing services — PortfolioSearchService, ArchitectureRAGService,
and direct DB queries — without introducing new data-access patterns.
"""

import logging
import re
from dataclasses import dataclass, field


from typing import Any, Dict, List, Optional

from app import db
from app.models.constants import ArchiMateRelationshipType
from app.utils.tenant_sql import org_scope

logger = logging.getLogger(__name__)


def _rollback_quietly() -> None:
    """Clear a failed statement so later queries in the same block can run.

    PostgreSQL aborts the whole transaction on the first error, and every
    subsequent statement then fails with InFailedSqlTransaction. A handler that
    catches per-query without rolling back therefore turns one broken query into
    an empty answer for all of them.
    """
    try:
        db.session.rollback()
    except Exception:  # noqa: BLE001
        logger.debug("rollback after a failed context query also failed")


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class EnterpriseContext:
    """Structured enterprise context for LLM prompt enrichment."""

    description: str = ""
    phase: str = "C"
    business_domain: str = ""

    # Portfolio data (populated by assemble_context)
    applications: List[Dict[str, Any]] = field(default_factory=list)
    archimate_elements: List[Dict[str, Any]] = field(default_factory=list)
    vendors: List[Dict[str, Any]] = field(default_factory=list)
    capabilities: List[Dict[str, Any]] = field(default_factory=list)
    principles: List[Dict[str, Any]] = field(default_factory=list)
    prior_solutions: List[Dict[str, Any]] = field(default_factory=list)
    solution_entities: Dict[str, Any] = field(default_factory=dict)

    # Stats for context preview
    @property
    def stats(self) -> Dict[str, int]:
        return {
            "applications": len(self.applications),
            "archimate_elements": len(self.archimate_elements),
            "vendors": len(self.vendors),
            "capabilities": len(self.capabilities),
            "principles": len(self.principles),
            "prior_solutions": len(self.prior_solutions),
        }


@dataclass
class ProcessedElement:
    """An element from LLM generation, categorised after post-processing."""

    name: str
    type: str
    layer: str
    category: str  # "existing", "new", "possible_duplicate"
    valid: bool = True

    # Linking data
    existing_id: Optional[int] = None
    duplicate_match: Optional[Dict[str, Any]] = None
    duplicate_score: float = 0.0


@dataclass
class ProcessedResult:
    """Post-processed generation result with categorised elements."""

    elements: List[Dict[str, Any]] = field(default_factory=list)
    relationships: List[Dict[str, Any]] = field(default_factory=list)
    gaps: List[str] = field(default_factory=list)
    llm_used: bool = False
    context_stats: Dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Phase-specific element types (for prompt guidance)
# ---------------------------------------------------------------------------

_PHASE_DESCRIPTIONS = {
    "A": "Architecture Vision — Stakeholders, Drivers, Goals, Principles, Constraints",
    "B": "Business Architecture — Processes, Services, Roles, Actors, Objects",
    "C": "Information Systems — Applications, Services, Interfaces, Data Objects",
    "D": "Technology Architecture — Nodes, Devices, System Software, Networks",
}

_PHASE_ELEMENT_TYPES = {
    "A": ["Stakeholder", "Driver", "Goal", "Principle", "Constraint", "Assessment"],
    "B": ["BusinessProcess", "BusinessService", "BusinessRole", "BusinessActor",
           "BusinessObject", "BusinessFunction"],
    "C": ["ApplicationComponent", "ApplicationService", "ApplicationInterface",
           "DataObject", "ApplicationFunction"],
    "D": ["Node", "SystemSoftware", "Device", "CommunicationNetwork",
           "TechnologyService", "Artifact"],
}


# ---------------------------------------------------------------------------
# Main service
# ---------------------------------------------------------------------------

class EnterpriseContextAssembler:
    """Assembles enterprise portfolio context for AI architecture generation.

    Queries existing portfolio data using PortfolioSearchService and
    ArchitectureRAGService, then formats it for LLM prompt injection.
    """

    def __init__(self):
        from app.services.portfolio_search_service import PortfolioSearchService
        self._search = PortfolioSearchService()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def assemble_context(
        self,
        description: str,
        phase: str = "C",
        business_domain: Optional[str] = None,
        solution_id: Optional[int] = None,
    ) -> EnterpriseContext:
        """Gather enterprise context relevant to the generation request.

        Parameters
        ----------
        description : str
            Natural-language architecture description from the user.
        phase : str
            TOGAF ADM phase (A/B/C/D).
        business_domain : str, optional
            Business domain filter to narrow results.
        solution_id : int, optional
            If provided, include entities already linked to this solution.

        Returns
        -------
        EnterpriseContext
            Structured context ready for prompt formatting.
        """
        ctx = EnterpriseContext(
            description=description,
            phase=phase,
            business_domain=business_domain or "",
        )

        terms = self.extract_terms(description)
        # Build individual search queries from terms and their component words
        search_queries = list(terms)
        for term in terms:
            words = term.split()
            if len(words) > 1:
                search_queries.extend(w for w in words if len(w) > 2)
        if not search_queries:
            search_queries = [description[:100]]

        # 1. Find relevant applications (with enriched metadata)
        ctx.applications = self._find_multi(
            search_queries, self._find_applications, business_domain
        )

        # 2. Find existing ArchiMate elements (so LLM references, not recreates)
        ctx.archimate_elements = self._find_multi(
            search_queries, self._find_archimate_elements, phase
        )

        # 3. Find relevant vendor products
        ctx.vendors = self._find_multi(search_queries, self._find_vendors)

        # 4. Find relevant capabilities
        ctx.capabilities = self._find_multi(
            search_queries, self._find_capabilities, business_domain
        )

        # 5. Get architecture principles (from RAG service)
        ctx.principles = self._get_principles(business_domain)

        # 6. Get prior solutions in same domain
        ctx.prior_solutions = self._get_prior_solutions(
            business_domain, search_queries[0]
        )

        # 7. If solution_id provided, get its linked entities
        if solution_id:
            ctx.solution_entities = self._get_solution_entities(solution_id)

        return ctx

    def format_for_prompt(self, ctx: EnterpriseContext) -> str:
        """Format enterprise context as a string for LLM prompt injection.

        Stays under ~4000 tokens by limiting each section to top-N results
        and truncating descriptions.

        Parameters
        ----------
        ctx : EnterpriseContext
            The assembled context.

        Returns
        -------
        str
            Formatted context string for inclusion in the LLM prompt.
        """
        parts = []

        phase_desc = _PHASE_DESCRIPTIONS.get(ctx.phase, f"Phase {ctx.phase}")
        parts.append(f"## TOGAF ADM Phase: {phase_desc}")
        parts.append(f"## Relevant ArchiMate element types for this phase: "
                      f"{', '.join(_PHASE_ELEMENT_TYPES.get(ctx.phase, []))}")

        if ctx.business_domain:
            parts.append(f"## Business Domain: {ctx.business_domain}")

        # Applications
        if ctx.applications:
            parts.append("\n## Existing Applications in This Domain "
                          f"({len(ctx.applications)} found)")
            parts.append("IMPORTANT: Reference these by name. Do NOT invent "
                          "alternatives for applications that already exist.")
            for app in ctx.applications[:15]:
                meta = app.get("metadata", {})
                line = f"- {app['name']}"
                extras = []
                if meta.get("vendor"):
                    extras.append(f"Vendor: {meta['vendor']}")
                if meta.get("technology_stack"):
                    extras.append(f"Stack: {meta['technology_stack']}")
                if meta.get("criticality"):
                    extras.append(f"Criticality: {meta['criticality']}")
                if meta.get("lifecycle_stage"):
                    extras.append(f"Lifecycle: {meta['lifecycle_stage']}")
                if meta.get("business_domain"):
                    extras.append(f"Domain: {meta['business_domain']}")
                if extras:
                    line += " | " + " | ".join(extras)
                parts.append(line)

        # ArchiMate elements
        if ctx.archimate_elements:
            parts.append("\n## Existing ArchiMate Elements "
                          f"({len(ctx.archimate_elements)} found)")
            parts.append("USE these element IDs when they match your design. "
                          "Only create NEW elements for things not listed here.")
            for elem in ctx.archimate_elements[:20]:
                meta = elem.get("metadata", {})
                el_type = meta.get("element_type", "")
                layer = meta.get("layer", "")
                parts.append(
                    f"- [ID:{elem['id']}] {elem['name']} "
                    f"({el_type}, {layer})"
                )

        # Vendors
        if ctx.vendors:
            parts.append(f"\n## Available Vendor Products ({len(ctx.vendors)} found)")
            for v in ctx.vendors[:10]:
                meta = v.get("metadata", {})
                line = f"- {v['name']}"
                if meta.get("vendor_type"):
                    line += f" ({meta['vendor_type']})"
                if meta.get("gartner_quadrant"):
                    line += f" | Quadrant: {meta['gartner_quadrant']}"
                if meta.get("risk_score"):
                    line += f" | Risk: {meta['risk_score']}"
                parts.append(line)

        # Capabilities
        if ctx.capabilities:
            parts.append(f"\n## Business Capabilities ({len(ctx.capabilities)} found)")
            for cap in ctx.capabilities[:10]:
                meta = cap.get("metadata", {})
                line = f"- {cap['name']}"
                if meta.get("current_maturity") and meta.get("target_maturity"):
                    line += (f" | Maturity: {meta['current_maturity']}/5 "
                             f"→ {meta['target_maturity']}/5")
                if meta.get("strategic_importance"):
                    line += f" | Strategic: {meta['strategic_importance']}"
                parts.append(line)

        # Principles
        if ctx.principles:
            parts.append(f"\n## Architecture Principles ({len(ctx.principles)} found)")
            for p in ctx.principles[:10]:
                desc = (p.get("description") or "")[:150]
                parts.append(f"- **{p['name']}**: {desc}")

        # Prior solutions
        if ctx.prior_solutions:
            parts.append(f"\n## Prior Solutions in This Domain ({len(ctx.prior_solutions)} found)")
            for sol in ctx.prior_solutions[:5]:
                line = f"- {sol['name']} ({sol.get('status', 'unknown')})"
                if sol.get("description"):
                    line += f" — {sol['description'][:100]}"
                parts.append(line)

        # Solution-linked entities
        if ctx.solution_entities:
            parts.append("\n## Already Linked to This Solution")
            for entity_type, entities in ctx.solution_entities.items():
                if entities:
                    names = ", ".join(e["name"] for e in entities[:10])
                    parts.append(f"- {entity_type}: {names}")

        text = "\n".join(parts)

        # Hard limit: ~16000 chars ≈ ~4000 tokens
        max_chars = 16000
        if len(text) > max_chars:
            text = text[:max_chars] + "\n[...context truncated for token limit]"

        return text

    def build_generation_prompt(self, ctx: EnterpriseContext) -> str:
        """Build the full LLM prompt including enterprise context.

        Parameters
        ----------
        ctx : EnterpriseContext
            The assembled context from assemble_context().

        Returns
        -------
        str
            Complete prompt string for the LLM.
        """
        context_text = self.format_for_prompt(ctx)

        prompt = f"""You are a hands-on solution architect at a Fortune 500 company producing
a WORKING ArchiMate 3.2 diagram that will be reviewed by the Architecture Review Board.

## Architect's Request
{ctx.description}

## REAL Enterprise Portfolio (THIS IS YOUR COMPANY'S DATA — USE IT)
{context_text}

## MANDATORY Rules — violations will be rejected by the ARB

### 1. USE EXISTING ASSETS FIRST (most important rule)
- BEFORE creating any element, CHECK if an equivalent exists in the portfolio above.
- If an application, capability, or technology exists above, reference it by ID with
  "existing_id". Do NOT create a "new" element that duplicates an existing one.
- At least 40% of your elements MUST reference existing portfolio items.
- If you cannot find ANY matching existing items, state why in the rationale.

### 2. ARCHITECTURE, NOT THEORY
- Do NOT generate generic motivation elements like "Business Growth" or "Cost Efficiency".
- Every element must be SPECIFIC to the request: name real systems, real teams, real
  processes from this company's portfolio.
- Stakeholders should be ROLES (e.g., "CRM Product Owner", "Data Platform Team") not
  generic titles like "CIO" unless the request specifically mentions the CIO.
- Goals and drivers must be MEASURABLE and SPECIFIC to the request, not textbook items.

### 3. CONNECTED GRAPH (every element must participate in at least one relationship)
- Generate at least 1.5x as many relationships as elements.
- A diagram where elements float without connections is useless — show HOW things connect.
- Cross-layer relationships are REQUIRED: show how business processes USE applications,
  how applications are DEPLOYED on technology, how goals are REALIZED by capabilities.

### 4. GAP ANALYSIS (only if requested)
- For each new element that doesn't exist in the portfolio, explain the gap clearly.
- Gaps should be actionable: "No API gateway exists for CRM integration" not "Integration needed".

### 5. Valid ArchiMate 3.2 relationship types ONLY
composition, aggregation, assignment, realization, serving, access, influence,
triggering, flow, specialization, association.

## Output Format (JSON only, no markdown, no preamble)
{{
  "elements": [
    {{
      "name": "string (use EXACT name from portfolio for existing items)",
      "type": "ArchiMate3.2Type",
      "layer": "business|application|technology|motivation|strategy|implementation",
      "existing_id": null_or_integer,
      "is_new": true_or_false,
      "gap_reason": "string or null (required if is_new=true)"
    }}
  ],
  "relationships": [
    {{
      "source_name": "string (must match an element name exactly)",
      "target_name": "string (must match an element name exactly)",
      "type": "one of the valid types above",
      "description": "WHY this relationship exists (1 sentence)"
    }}
  ],
  "gaps": [
    "Actionable gap description — what the company needs to build/acquire/change"
  ],
  "rationale": "Explain your key design decisions referencing the company's actual portfolio"
}}"""
        return prompt

    def get_context_preview(
        self,
        description: str,
        phase: str = "C",
        business_domain: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Quick context preview for the frontend (counts only, fast).

        Used by the generation dialog to show "Found: 12 apps, 8 elements..."
        before the user clicks Generate.

        The counts MUST reflect the context the AI actually reasons over, so the
        preview runs the same assembly path as generation (assemble_context) and
        reports the size of each resulting entity list. A previous version ran a
        separate, tighter search (limit=5) here, which reported far fewer entities
        than generation truly used and made the modal look as though the AI only
        saw a handful of the tenant's records.
        """
        terms = self.extract_terms(description)

        counts = {
            "application": 0,
            "archimate_element": 0,
            "vendor": 0,
            "capability": 0,
        }
        try:
            ctx = self.assemble_context(
                description=description,
                phase=phase,
                business_domain=business_domain,
            )
            counts = {
                "application": len(ctx.applications),
                "archimate_element": len(ctx.archimate_elements),
                "vendor": len(ctx.vendors),
                "capability": len(ctx.capabilities),
            }
        except Exception as exc:
            logger.debug("Context preview assembly failed: %s", exc)

        return {
            "query_terms": terms,
            "counts": counts,
            "business_domain": business_domain,
            "phase": phase,
        }

    # ------------------------------------------------------------------
    # Post-processing
    # ------------------------------------------------------------------

    def post_process(
        self,
        llm_response: Dict[str, Any],
        ctx: EnterpriseContext,
    ) -> ProcessedResult:
        """Post-process LLM generation output.

        - Validates relationships against ArchiMate meta-model
        - Links elements to existing portfolio entities by ID
        - Fuzzy-deduplicates new elements against existing catalog
        - Extracts gaps

        Parameters
        ----------
        llm_response : dict
            Raw LLM output with elements, relationships, gaps.
        ctx : EnterpriseContext
            The enterprise context used for generation.

        Returns
        -------
        ProcessedResult
        """
        result = ProcessedResult(context_stats=ctx.stats)

        raw_elements = llm_response.get("elements") or []
        raw_relationships = llm_response.get("relationships") or []
        raw_gaps = llm_response.get("gaps") or []

        # Build lookup of existing elements from context
        existing_by_id = {
            e["id"]: e for e in ctx.archimate_elements
        }
        existing_by_name = {
            e["name"].lower(): e for e in ctx.archimate_elements
        }

        # Process elements
        for elem in raw_elements:
            name = (elem.get("name") or "").strip()
            el_type = (elem.get("type") or "").strip()
            layer = (elem.get("layer") or "").strip().lower()
            existing_id = elem.get("existing_id")
            is_new = elem.get("is_new", True)

            if not name or not el_type:
                continue

            processed = {
                "name": name,
                "type": el_type,
                "layer": layer,
                "valid": self._is_valid_type(el_type),
                "gap_reason": elem.get("gap_reason"),
            }

            # Category 1: LLM explicitly referenced an existing element
            if existing_id and existing_id in existing_by_id:
                processed["category"] = "existing"
                processed["existing_id"] = existing_id
                processed["duplicate"] = False
            elif not is_new and name.lower() in existing_by_name:
                match = existing_by_name[name.lower()]
                processed["category"] = "existing"
                processed["existing_id"] = match["id"]
                processed["duplicate"] = False
            else:
                # Category 2 or 3: check for fuzzy duplicates
                dup = self._find_duplicate(name, el_type)
                if dup:
                    processed["category"] = "possible_duplicate"
                    processed["duplicate_match"] = dup
                    processed["duplicate_score"] = dup.get("score", 0)
                    processed["duplicate"] = True
                else:
                    processed["category"] = "new"
                    processed["duplicate"] = False

            result.elements.append(processed)

        # Process relationships — normalise LLM verb forms (realizes→realization, etc.)
        for rel in raw_relationships:
            source = (rel.get("source_name") or "").strip()
            target = (rel.get("target_name") or "").strip()
            raw_rel_type = (rel.get("type") or "association").lower()

            if not source or not target:
                continue

            # Normalise aliases: realizes→realization, serves→serving, etc.
            rel_type = self._normalize_rel_type_alias(raw_rel_type)
            is_valid = self._is_valid_relationship_type(rel_type)

            processed_rel = {
                "source_name": source,
                "target_name": target,
                "type": rel_type,
                "description": rel.get("description", ""),
                "valid": is_valid,
                "warning": "",
            }

            if not is_valid:
                processed_rel["warning"] = (
                    f"'{raw_rel_type}' is not a valid ArchiMate 3.2 relationship type"
                )

            result.relationships.append(processed_rel)

        # Gaps
        result.gaps = [str(g) for g in raw_gaps if g]

        return result

    # ------------------------------------------------------------------
    # Term extraction
    # ------------------------------------------------------------------

    @staticmethod
    def extract_terms(description: str) -> List[str]:
        """Extract key search terms from a natural-language description.

        Removes common stop words and returns meaningful noun phrases
        for portfolio search queries.
        """
        if not description:
            return []

        stop_words = {
            "a", "an", "the", "and", "or", "but", "in", "on", "at", "to",
            "for", "of", "with", "by", "from", "is", "are", "was", "were",
            "be", "been", "being", "have", "has", "had", "do", "does", "did",
            "will", "would", "could", "should", "may", "might", "shall",
            "can", "need", "must", "that", "this", "these", "those", "it",
            "its", "they", "them", "their", "we", "our", "i", "my", "you",
            "your", "not", "no", "all", "each", "every", "both", "such",
            "into", "through", "about", "between", "after", "before",
            "during", "without", "within", "also", "very", "just", "only",
            "new", "via", "using", "used", "use", "create", "design",
            "build", "develop", "implement", "deploy", "migrate", "replace",
            "architecture", "diagram", "model", "generate",
        }

        # Split on punctuation
        fragments = re.split(r"[,;:.\-\(\)\[\]\"\'\/\\&\n]+", description)
        terms = []
        for frag in fragments:
            words = frag.strip().split()
            cleaned = [w for w in words if w.lower() not in stop_words and len(w) > 1]
            if cleaned:
                term = " ".join(cleaned).strip()
                if 2 <= len(term) <= 60:
                    terms.append(term)

        return terms[:10]

    # ------------------------------------------------------------------
    # Private: portfolio queries
    # ------------------------------------------------------------------

    def _find_multi(
        self,
        queries: List[str],
        finder_fn,
        extra_arg=None,
    ) -> List[Dict[str, Any]]:
        """Run a finder function across multiple search queries, deduplicating by ID."""
        seen_ids: set = set()
        merged: List[Dict[str, Any]] = []
        for q in queries:
            try:
                hits = finder_fn(q, extra_arg) if extra_arg is not None else finder_fn(q)
            except Exception:
                continue
            for hit in hits:
                rid = hit.get("id")
                if rid and rid not in seen_ids:
                    seen_ids.add(rid)
                    merged.append(hit)
        return merged

    def _find_applications(
        self, query: str, domain: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Find relevant applications with enriched metadata."""
        results = self._search.search(
            query, entity_type="application", limit=15, threshold=0.3
        )

        # Enrich with additional fields not in the basic search
        enriched = []
        for r in results:
            app_data = dict(r)
            try:
                _org_clause, _org_params = org_scope()
                row = db.session.execute(
                    db.text(
                        "SELECT vendor_name, technology_stack, criticality, "
                        "lifecycle_status, business_domain, integration_methods, "
                        "deployment_model "
                        "FROM application_components WHERE id = :id" + _org_clause
                    ),
                    {"id": r["id"], **_org_params},
                ).fetchone()
                if row:
                    app_data["metadata"] = {
                        "vendor": row[0] or "",
                        "technology_stack": row[1] or "",
                        "criticality": row[2] or "",
                        "lifecycle_status": row[3] or "",
                        "business_domain": row[4] or "",
                        "integration_methods": row[5] or "",
                        "deployment_model": row[6] or "",
                    }
            except Exception:
                logger.debug("Failed to enrich app %s", r.get("id"))
            enriched.append(app_data)

        # If domain filter provided, boost domain-matching apps
        if domain and enriched:
            domain_lower = domain.lower()
            for app in enriched:
                app_domain = (
                    app.get("metadata", {}).get("business_domain") or ""
                ).lower()
                if domain_lower in app_domain:
                    app["score"] = min(1.0, app.get("score", 0.5) + 0.2)
            enriched.sort(key=lambda x: x.get("score", 0), reverse=True)

        return enriched[:15]

    def _find_archimate_elements(
        self, query: str, phase: str
    ) -> List[Dict[str, Any]]:
        """Find existing ArchiMate elements, prioritising phase-relevant types.

        Uses direct ILIKE queries against archimate_elements.name and
        archimate_elements.description to ensure broad matching.  Falls back
        to PortfolioSearchService only when the direct query returns nothing.
        """
        results: List[Dict[str, Any]] = []
        seen_ids: set = set()

        # --- Direct ILIKE search (primary path) ---
        try:
            from app.models.archimate_core import ArchiMateElement

            # Split query into individual words and batch into single OR query
            words = [w.strip() for w in query.split() if len(w.strip()) > 2]
            if not words:
                words = [query.strip()]

            # model-safety-ok: single batched query, not N+1
            or_conditions = []
            for word in words:
                pattern = f"%{word}%"
                or_conditions.append(ArchiMateElement.name.ilike(pattern))
                or_conditions.append(ArchiMateElement.description.ilike(pattern))

            rows = (
                ArchiMateElement.query.filter(db.or_(*or_conditions))
                .limit(60)
                .all()
            ) if or_conditions else []

            for r in rows:
                    if r.id not in seen_ids:
                        seen_ids.add(r.id)
                        score = self._search._compute_score(
                            query, r.name, getattr(r, "description", None)
                        )
                        results.append({
                            "id": r.id,
                            "type": "archimate_element",
                            "name": r.name,
                            "description": (getattr(r, "description", None) or "")[:200],
                            "score": round(max(score, 0.3), 3),
                            "metadata": {
                                "element_type": getattr(r, "type", None),
                                "layer": getattr(r, "layer", None),
                            },
                        })
        except Exception:
            logger.warning("Direct ArchiMate search failed, falling back to PortfolioSearchService")

        # --- Fallback: PortfolioSearchService ---
        if not results:
            try:
                results = self._search.search(
                    query, entity_type="archimate_element", limit=30, threshold=0.2
                )
            except Exception:
                logger.warning("PortfolioSearchService archimate search also failed")

        # Boost elements whose type matches the requested phase
        phase_types = set(_PHASE_ELEMENT_TYPES.get(phase, []))
        for elem in results:
            el_type = elem.get("metadata", {}).get("element_type", "")
            if el_type in phase_types:
                elem["score"] = min(1.0, elem.get("score", 0.5) + 0.15)

        results.sort(key=lambda x: x.get("score", 0), reverse=True)
        return results[:20]

    def _find_vendors(self, query: str) -> List[Dict[str, Any]]:
        """Find relevant vendors with risk/quadrant data.

        Uses direct ILIKE queries against vendor_organizations for broad
        matching, with PortfolioSearchService as fallback.
        """
        results: List[Dict[str, Any]] = []
        seen_ids: set = set()

        # --- Direct ILIKE search (primary path) ---
        try:
            from app.models.vendor.vendor_organization import VendorOrganization

            words = [w.strip() for w in query.split() if len(w.strip()) > 2]
            if not words:
                words = [query.strip()]

            # model-safety-ok: single batched query, not N+1
            or_conditions = []
            for word in words:
                pattern = f"%{word}%"
                or_conditions.append(VendorOrganization.name.ilike(pattern))
                or_conditions.append(VendorOrganization.display_name.ilike(pattern))

            rows = (
                VendorOrganization.query.filter(db.or_(*or_conditions))
                .limit(30)
                .all()
            ) if or_conditions else []

            for r in rows:
                    if r.id not in seen_ids:
                        seen_ids.add(r.id)
                        score = self._search._compute_score(
                            query, r.name, getattr(r, "display_name", None)
                        )
                        results.append({
                            "id": r.id,
                            "type": "vendor",
                            "name": r.name,
                            "description": (getattr(r, "display_name", None) or "")[:200],
                            "score": round(max(score, 0.3), 3),
                            "metadata": {
                                "vendor_type": getattr(r, "vendor_type", None),
                            },
                        })
        except Exception:
            logger.warning("Direct vendor search failed, falling back to PortfolioSearchService")

        # --- Fallback: PortfolioSearchService ---
        if not results:
            try:
                results = self._search.search(
                    query, entity_type="vendor", limit=10, threshold=0.3
                )
            except Exception:
                logger.warning("PortfolioSearchService vendor search also failed")

        # Enrich with vendor-specific fields
        enriched = []
        for r in results:
            v_data = dict(r)
            try:
                row = db.session.execute(  # tenant-filtered: scoped via parent FK (id from search results)
                    db.text(
                        # market_position is a vendor_products column and was
                        # never on vendor_organizations, so this SELECT raised
                        # UndefinedColumn and the bare except below dropped the
                        # Gartner position and vendor type along with it -- the
                        # model got no vendor positioning at all.
                        "SELECT gartner_magic_quadrant_position, "
                        "forrester_wave_position, vendor_type "
                        "FROM vendor_organizations WHERE id = :id"
                    ),
                    {"id": r["id"]},
                ).fetchone()
                if row:
                    v_data["metadata"] = {
                        **v_data.get("metadata", {}),
                        "gartner_quadrant": row[0] or "",
                        # Named for what it actually is. Reporting a Forrester
                        # Wave placement to the model as "market_position" would
                        # be a label describing a different field than the one
                        # read, which is the fabrication rule's core case.
                        "forrester_wave": row[1] or "",
                        "vendor_type": row[2] or "",
                    }
            except Exception:
                logger.warning("Failed to enrich vendor %s", r.get("id"))
            enriched.append(v_data)

        return enriched[:10]

    def _find_capabilities(
        self, query: str, domain: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Find relevant business capabilities with maturity data.

        Uses direct ILIKE queries against business_capability for broad
        matching, with PortfolioSearchService as fallback.
        """
        results: List[Dict[str, Any]] = []
        seen_ids: set = set()

        # --- Direct ILIKE search (primary path) ---
        try:
            from app.models.business_capabilities import BusinessCapability

            words = [w.strip() for w in query.split() if len(w.strip()) > 2]
            if not words:
                words = [query.strip()]

            # model-safety-ok: single batched query, not N+1
            or_conditions = []
            for word in words:
                pattern = f"%{word}%"
                or_conditions.append(BusinessCapability.name.ilike(pattern))
                or_conditions.append(BusinessCapability.description.ilike(pattern))

            rows = (
                BusinessCapability.query.filter(db.or_(*or_conditions))
                .limit(30)
                .all()
            ) if or_conditions else []

            for r in rows:
                    if r.id not in seen_ids:
                        seen_ids.add(r.id)
                        score = self._search._compute_score(
                            query, r.name, getattr(r, "description", None)
                        )
                        results.append({
                            "id": r.id,
                            "type": "capability",
                            "name": r.name,
                            "description": (getattr(r, "description", None) or "")[:200],
                            "score": round(max(score, 0.3), 3),
                            "metadata": {
                                "level": getattr(r, "level", None),
                                "category": getattr(r, "category", None),
                            },
                        })
        except Exception:
            logger.warning("Direct capability search failed, falling back to PortfolioSearchService")

        # --- Fallback: PortfolioSearchService ---
        if not results:
            try:
                results = self._search.search(
                    query, entity_type="capability", limit=10, threshold=0.3
                )
            except Exception:
                logger.warning("PortfolioSearchService capability search also failed")

        # Enrich with maturity data
        enriched = []
        for r in results:
            cap_data = dict(r)
            try:
                _org_clause, _org_params = org_scope()
                row = db.session.execute(
                    db.text(
                        "SELECT current_maturity_level, target_maturity_level, "
                        "strategic_importance, business_value, business_domain "
                        "FROM business_capability WHERE id = :id" + _org_clause
                    ),
                    {"id": r["id"], **_org_params},
                ).fetchone()
                if row:
                    cap_data["metadata"] = {
                        **cap_data.get("metadata", {}),
                        "current_maturity": row[0],
                        "target_maturity": row[1],
                        "strategic_importance": row[2] or "",
                        "business_value": row[3],
                        "business_domain": row[4] or "",
                    }
            except Exception:
                logger.debug("Failed to enrich capability %s", r.get("id"))
            enriched.append(cap_data)

        return enriched[:10]

    def _get_principles(self, domain: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get architecture principles from the RAG service."""
        try:
            from app.services.architecture_rag_service import ArchitectureRAGService
            rag = ArchitectureRAGService()
            ctx = rag.get_context_for_solution(business_domain=domain)
            return ctx.get("principles", [])
        except Exception:
            logger.debug("RAG service unavailable for principles")
            return []

    def _get_prior_solutions(
        self, domain: Optional[str], query: str
    ) -> List[Dict[str, Any]]:
        """Get prior approved solutions in the same domain."""
        try:
            q = (
                "SELECT s.name, s.governance_status, s.business_domain, "
                "s.description, s.complexity_level, s.solution_type "
                "FROM solutions s "
                "WHERE s.governance_status IN ('approved', 'arb_approved', 'deployed') "
            )
            params: Dict[str, Any] = {}
            _org_clause, _org_params = org_scope(prefix="s.")
            q += _org_clause + " "
            params.update(_org_params)
            if domain:
                q += "AND s.business_domain = :domain "
                params["domain"] = domain
            q += "ORDER BY s.updated_at DESC NULLS LAST LIMIT 5"

            rows = db.session.execute(db.text(q), params).fetchall()  # tenant-filtered: scoped via parent FK (domain filter on solutions)
            return [
                {
                    "name": r[0],
                    "status": r[1] or "",
                    "domain": r[2] or "",
                    "description": (r[3] or "")[:150],
                    "complexity_level": r[4] or "",
                    "solution_type": r[5] or "",
                }
                for r in rows
            ]
        except Exception:
            logger.warning("Failed to query prior solutions")
            return []

    def _get_solution_entities(self, solution_id: int) -> Dict[str, List[Dict]]:
        """Get entities already linked to a specific solution."""
        entities: Dict[str, List[Dict]] = {}

        # Linked applications.
        #
        # The join column is application_component_id; `sa.application_id` does
        # not exist on solution_applications and never has. Every call raised
        # UndefinedColumn here, which aborted the transaction and made the two
        # queries below fail too with InFailedSqlTransaction — so all three
        # handlers returned [] and the assembler reported "no entities linked to
        # this solution" to the LLM for every solution in the system.
        try:
            _org_clause, _org_params = org_scope(prefix="ac.")
            rows = db.session.execute(
                db.text(
                    "SELECT ac.id, ac.name FROM application_components ac "
                    "JOIN solution_applications sa "
                    "ON sa.application_component_id = ac.id "
                    "WHERE sa.solution_id = :sid AND ac.deleted_at IS NULL" + _org_clause
                ),
                {"sid": solution_id, **_org_params},
            ).fetchall()
            entities["applications"] = [{"id": r[0], "name": r[1]} for r in rows]
        except Exception:
            logger.warning(
                "Solution %s: linked-application lookup failed", solution_id,
                exc_info=True,
            )
            entities["applications"] = []
            # A failed statement poisons the transaction; without this the next
            # two blocks cannot run at all.
            _rollback_quietly()

        # Linked ArchiMate elements
        #
        # solution_archimate_elements carries no organization_id, so the join
        # alone proves nothing: a solution_id from another tenant would hand its
        # elements straight into the LLM prompt. archimate_elements does carry
        # one — scope on it.
        try:
            _org_clause, _org_params = org_scope(prefix="ae.")
            rows = db.session.execute(
                db.text(
                    "SELECT ae.id, ae.name, ae.type, ae.layer "
                    "FROM archimate_elements ae "
                    "JOIN solution_archimate_elements sae ON sae.element_id = ae.id "
                    "WHERE sae.solution_id = :sid" + _org_clause
                ),
                {"sid": solution_id, **_org_params},
            ).fetchall()
            entities["archimate_elements"] = [
                {"id": r[0], "name": r[1], "type": r[2], "layer": r[3]}
                for r in rows
            ]
        except Exception:
            logger.warning(
                "Solution %s: linked-element lookup failed", solution_id,
                exc_info=True,
            )
            entities["archimate_elements"] = []
            _rollback_quietly()

        # Linked vendor products
        try:
            rows = db.session.execute(  # tenancy-ok: vendor_product_details has no organization_id column and no tenant-scoped parent in this join; solution_vendor_products is keyed by solution_id only
                db.text(
                    "SELECT vp.id, vp.product_name FROM vendor_product_details vp "
                    "JOIN solution_vendor_products svp ON svp.vendor_product_id = vp.id "
                    "WHERE svp.solution_id = :sid"
                ),
                {"sid": solution_id},
            ).fetchall()
            entities["vendor_products"] = [{"id": r[0], "name": r[1]} for r in rows]
        except Exception:
            logger.warning(
                "Solution %s: linked-vendor-product lookup failed", solution_id,
                exc_info=True,
            )
            entities["vendor_products"] = []
            _rollback_quietly()

        return entities

    # ------------------------------------------------------------------
    # Private: validation helpers
    # ------------------------------------------------------------------

    _ALL_ARCHIMATE_TYPES = {
        "BusinessActor", "BusinessRole", "BusinessProcess", "BusinessFunction",
        "BusinessService", "BusinessObject", "BusinessEvent", "Contract", "Product",
        "ApplicationComponent", "ApplicationService", "ApplicationFunction",
        "ApplicationInterface", "ApplicationProcess", "DataObject",
        "Node", "Device", "SystemSoftware", "TechnologyService", "Artifact",
        "CommunicationNetwork", "Path", "TechnologyFunction", "TechnologyProcess",
        "TechnologyInterface", "TechnologyEvent",
        "Stakeholder", "Driver", "Goal", "Requirement", "Constraint", "Principle",
        "Assessment", "Value", "Meaning", "Outcome",
        "Capability", "Resource", "CourseOfAction", "ValueStream",
        "WorkPackage", "Deliverable", "Plateau", "Gap",
    }

    _VALID_RELATIONSHIP_TYPES = {
        "composition", "aggregation", "assignment", "realization",
        "serving", "access", "influence", "triggering", "flow",
        "specialization", "association",
    }

    @classmethod
    def _is_valid_type(cls, el_type: str) -> bool:
        return el_type in cls._ALL_ARCHIMATE_TYPES

    @classmethod
    def _normalize_rel_type_alias(cls, raw: str) -> str:
        """Normalise LLM verb forms to canonical ArchiMate noun forms."""
        return ArchiMateRelationshipType.normalize(raw) or raw

    @classmethod
    def _is_valid_relationship_type(cls, rel_type: str) -> bool:
        return rel_type.lower() in cls._VALID_RELATIONSHIP_TYPES

    def _find_duplicate(
        self, name: str, el_type: str
    ) -> Optional[Dict[str, Any]]:
        """Check if an element name closely matches an existing one."""
        try:
            results = self._search.search(
                name, entity_type="archimate_element", limit=3, threshold=0.7
            )
            for r in results:
                if r.get("score", 0) >= 0.85:
                    return {
                        "id": r["id"],
                        "name": r["name"],
                        "type": r.get("metadata", {}).get("element_type", ""),
                        "score": r["score"],
                    }
        except Exception as exc:
            logger.debug("Duplicate check failed for '%s': %s", name, exc)
        return None
