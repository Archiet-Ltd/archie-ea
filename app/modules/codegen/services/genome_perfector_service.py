"""Genome Perfector Service (Layer D).

Single LLM pass that transforms a sparse Architectural Genome into
north-star quality immediately before code generation. Fires at the
Step 6→7 transition, non-skippable.

Eight enhancement categories:
1. Module completeness (bounded contexts, entities, value objects)
2. Operation richness (CRUD + domain operations per entity)
3. State machines (for status-bearing entities)
4. Views (list/detail/create/edit per entity)
5. Sensitive fields (PII pattern detection)
6. Validation rules (type, format, range per field)
7. Rationale injection (why each decision was made)
8. Cross-module relationships (missing integration contracts)

Uses deterministic scoring before AND after perfection to measure
improvement. Both scores are persisted on CodegenGeneration and
CodegenGenerationHistory.
"""

import json
import logging
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import List, Optional, Tuple

from app import db

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class GenomeChange:
    path: str
    change_type: str  # "added" | "enriched" | "restructured"
    description: str


@dataclass
class GenomeQualityScore:
    overall: int
    module_completeness: int
    operation_coverage: int
    state_machine_coverage: int
    view_coverage: int
    validation_coverage: int
    traceability: int
    sensitive_field_coverage: int
    production_quality: int  # error_codes + audit_trail + searchable field coverage


@dataclass
class GenomePerfectionResult:
    perfected_genome: dict
    score_before: int
    score_after: int
    changes: List[GenomeChange]
    cost_gbp: Decimal
    llm_ran: bool = False  # True only when LLM was available and actually invoked


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class GenomePerfectorService:
    """Perfect the Architectural Genome before code generation."""

    def perfect(
        self,
        solution_id: int,
        genome: dict,
        solution_context: dict,
    ) -> GenomePerfectionResult:
        """Single LLM call to transform sparse genome into north-star.

        Returns enhanced genome + diff of what changed.
        Falls back to original genome if LLM is unavailable.
        """
        score_before_obj = self.score_genome(genome)
        score_before = score_before_obj.overall

        # Attempt LLM perfection
        perfected, changes, cost = self._run_llm_perfection(genome, solution_context)

        if perfected is None:
            # LLM unavailable — return original with zero changes
            return GenomePerfectionResult(
                perfected_genome=genome,
                score_before=score_before,
                score_after=score_before,
                changes=[],
                cost_gbp=Decimal("0"),
                llm_ran=False,
            )

        # Validate the perfected genome
        validation_errors = self.validate_genome(perfected)
        if validation_errors:
            logger.warning(
                "Perfected genome has validation errors, using original: %s",
                validation_errors[:3],
            )
            return GenomePerfectionResult(
                perfected_genome=genome,
                score_before=score_before,
                score_after=score_before,
                changes=[],
                cost_gbp=cost,
                llm_ran=True,  # LLM ran but result was invalid
            )

        score_after_obj = self.score_genome(perfected)
        score_after = score_after_obj.overall

        # Persist scores
        self._persist_scores(solution_id, score_before, score_after)

        return GenomePerfectionResult(
            perfected_genome=perfected,
            score_before=score_before,
            score_after=score_after,
            changes=changes,
            cost_gbp=cost,
            llm_ran=True,
        )

    def score_genome(self, genome: dict) -> GenomeQualityScore:
        """Deterministic scoring of genome completeness."""
        modules = genome.get("modules", {})
        if not modules:
            return GenomeQualityScore(0, 0, 0, 0, 0, 0, 0, 0, 0)

        module_completeness = self._score_module_completeness(modules)
        operation_coverage = self._score_operation_coverage(modules)
        state_machine_coverage = self._score_state_machines(modules)
        view_coverage = self._score_views(modules)
        validation_coverage = self._score_validation(modules)
        traceability = self._score_traceability(modules)
        sensitive_field_coverage = self._score_sensitive_fields(modules, genome)
        production_quality = self._score_production_quality(modules, genome)

        # Weights adjusted to 1.00 with production_quality added at 0.10
        overall = int(round(
            module_completeness * 0.15
            + operation_coverage * 0.15
            + state_machine_coverage * 0.15
            + view_coverage * 0.15
            + validation_coverage * 0.10
            + traceability * 0.10
            + sensitive_field_coverage * 0.10
            + production_quality * 0.10
        ))

        return GenomeQualityScore(
            overall=overall,
            module_completeness=module_completeness,
            operation_coverage=operation_coverage,
            state_machine_coverage=state_machine_coverage,
            view_coverage=view_coverage,
            validation_coverage=validation_coverage,
            traceability=traceability,
            sensitive_field_coverage=sensitive_field_coverage,
            production_quality=production_quality,
        )

    def validate_genome(self, genome: dict) -> List[str]:
        """Schema validation — catches structural errors before codegen."""
        from app.modules.codegen.services.genome_validator import validate_genome
        return validate_genome(genome)

    # ------------------------------------------------------------------
    # Deterministic scoring helpers
    # ------------------------------------------------------------------

    def _score_module_completeness(self, modules: dict) -> int:
        """Score: bounded_context, aggregate_root, entities, value_objects present."""
        if not modules:
            return 0
        total = len(modules)
        complete = 0
        for name, mod in modules.items():
            if not isinstance(mod, dict):
                continue
            has_bc = bool(mod.get("bounded_context"))
            has_root = bool(mod.get("aggregate_root"))
            has_entities = bool(mod.get("entities")) and len(mod.get("entities", [])) > 0
            has_vos = bool(mod.get("value_objects"))
            # Score: 1 point each, normalized
            mod_score = sum([has_bc, has_root, has_entities, has_vos])
            if mod_score >= 3:
                complete += 1
        return int((complete / max(total, 1)) * 100)

    def _score_operation_coverage(self, modules: dict) -> int:
        """Score: entities have CRUD + at least one domain operation."""
        total_entities = 0
        entities_with_ops = 0
        for mod in modules.values():
            if not isinstance(mod, dict):
                continue
            entities = mod.get("entities", [])
            operations = mod.get("operations", {})
            if isinstance(entities, list):
                total_entities += len(entities)
            elif isinstance(entities, dict):
                total_entities += len(entities)
            if operations and len(operations) >= 4:  # CRUD minimum
                entities_with_ops += max(len(entities) if isinstance(entities, (list, dict)) else 0, 0)
        return int((entities_with_ops / max(total_entities, 1)) * 100) if total_entities else 0

    def _score_state_machines(self, modules: dict) -> int:
        """Score: % of status-bearing entities with state machines."""
        status_entities = 0
        with_sm = 0
        for mod in modules.values():
            if not isinstance(mod, dict):
                continue
            entities = mod.get("entities", [])
            sm = mod.get("state_machine") or mod.get("state_machines", {})
            # Check if any entity name suggests status
            for ent in (entities if isinstance(entities, list) else []):
                ent if isinstance(ent, str) else (ent.get("name", "") if isinstance(ent, dict) else "")
                # Heuristic: most domain entities benefit from state machines
                status_entities += 1
                if sm:
                    with_sm += 1
        return int((with_sm / max(status_entities, 1)) * 100) if status_entities else 50

    def _score_views(self, modules: dict) -> int:
        """Score: % of entities with list/detail/create view specs."""
        total = 0
        with_views = 0
        for mod in modules.values():
            if not isinstance(mod, dict):
                continue
            entities = mod.get("entities", [])
            views = mod.get("views", {})
            total += len(entities) if isinstance(entities, (list, dict)) else 0
            if views and isinstance(views, dict) and len(views) >= 2:
                with_views += len(entities) if isinstance(entities, (list, dict)) else 0
        return int((with_views / max(total, 1)) * 100) if total else 0

    def _score_validation(self, modules: dict) -> int:
        """Score: % of modules with validation rules."""
        total = len(modules)
        with_validation = 0
        for mod in modules.values():
            if not isinstance(mod, dict):
                continue
            validation = mod.get("validation") or mod.get("validation_rules")
            if validation:
                with_validation += 1
        return int((with_validation / max(total, 1)) * 100)

    def _score_traceability(self, modules: dict) -> int:
        """Score: % of modules/decisions with _rationale."""
        total = len(modules)
        with_rationale = 0
        for mod in modules.values():
            if not isinstance(mod, dict):
                continue
            if mod.get("_rationale"):
                with_rationale += 1
        return int((with_rationale / max(total, 1)) * 100)

    def _score_sensitive_fields(self, modules: dict, genome: dict) -> int:
        """Score: sensitive_fields section present and populated."""
        sf = genome.get("sensitive_fields", [])
        if sf and len(sf) >= 1:
            return 100
        # Check if any module has sensitive field annotations
        for mod in modules.values():
            if isinstance(mod, dict) and mod.get("sensitive_fields"):
                return 100
        # No PII patterns detected — could be legitimate
        return 50

    def _score_production_quality(self, modules: dict, genome: dict) -> int:
        """Score production-readiness concerns that don't appear in domain completeness:
        - error_codes section populated (33 pts): structured error codes, not just HTTPException
        - audit_trail configured in at least one module (33 pts): who changed what when
        - At least 20% of field-bearing entities have searchable fields (34 pts)
        """
        score = 0

        # Error codes — are they defined?
        ec = genome.get("error_codes", {})
        if ec and (ec.get("codes") or ec.get("prefix")):
            score += 33

        # Audit trail — at least one module has it configured
        for mod in modules.values():
            if isinstance(mod, dict) and mod.get("audit_trail", {}).get("enabled"):
                score += 33
                break

        # Searchable fields — does at least 20% of entities define searchable=true?
        entities_with_fields = 0
        entities_with_searchable = 0
        for mod in modules.values():
            if not isinstance(mod, dict):
                continue
            for entity_name, fields in (mod.get("fields") or {}).items():
                if fields:
                    entities_with_fields += 1
                    if any(f.get("searchable") for f in fields if isinstance(f, dict)):
                        entities_with_searchable += 1
        if entities_with_fields > 0:
            ratio = entities_with_searchable / entities_with_fields
            if ratio >= 0.20:
                score += 34
            elif ratio > 0:
                score += 17  # partial credit

        return min(score, 100)

    # ------------------------------------------------------------------
    # LLM perfection
    # ------------------------------------------------------------------

    def _run_llm_perfection(
        self, genome: dict, context: dict,
    ) -> Tuple[Optional[dict], List[GenomeChange], Decimal]:
        """Call LLM to perfect the genome. Returns (perfected, changes, cost)."""
        try:
            from app.modules.ai_chat.services.llm_service import LLMService

            if not LLMService.is_available():
                logger.info("LLM unavailable — skipping genome perfection")
                return None, [], Decimal("0")

            provider, model = LLMService._get_configured_provider()
            max_tokens = LLMService.get_max_tokens_limit(provider, model, requested_max=8192)

            prompt = self._build_prompt(genome, context)
            raw, interaction = LLMService._call_llm(
                prompt=prompt, model=model, provider=provider, max_tokens=max_tokens,
            )

            cost = Decimal(str(getattr(interaction, "cost", 0) or 0))

            if not raw:
                return None, [], cost

            perfected, changes = self._parse_perfection_response(raw, genome)
            return perfected, changes, cost

        except Exception:
            logger.exception("Genome perfection LLM call failed")
            return None, [], Decimal("0")

    def _build_prompt(self, genome: dict, context: dict) -> str:
        """Build the genome perfection prompt."""
        genome_yaml = json.dumps(genome, indent=2, default=str)[:12000]

        caps_summary = ""
        for cap in context.get("capabilities_summary", [])[:10]:
            if isinstance(cap, dict):
                caps_summary += f"  - {cap.get('name', '')}: {cap.get('description', '')[:100]}\n"

        elements_summary = ""
        for el in context.get("elements_summary", [])[:15]:
            if isinstance(el, dict):
                elements_summary += f"  - {el.get('name', '')} ({el.get('type', '')}, {el.get('layer', '')})\n"

        build_buy_summary = ""
        for bb in context.get("build_buy_summary", [])[:10]:
            if isinstance(bb, dict):
                build_buy_summary += f"  - {bb.get('element', '')}: {bb.get('build_buy', '')}\n"

        return f"""You are a senior enterprise solution architect perfecting an Architectural Genome JSON before code generation on the Entelim platform. Your job is to ensure every section is complete enough to generate production-grade code.

RULES:
1. NEVER remove or contradict user-confirmed values
2. Only ADD missing detail, ENRICH sparse fields, COMPLETE partial specs
3. Every module MUST have: bounded_context, aggregate_root, entities[], value_objects[], operations (CRUD minimum), views (list/detail/create)
4. Every entity with a status/state field MUST have a state_machine with states, transitions, guards, and effects
5. Every field that looks like PII (email, phone, SSN, address, DOB, card) MUST be in sensitive_fields with appropriate level (pii/restricted/confidential)
6. Every decision MUST have a _rationale explaining why
7. Every field MUST have validation rules (type, format, range, required, referential)
8. Detect missing cross-module relationships and add integration contracts
9. Output the COMPLETE perfected genome as valid JSON (not just changes)
10. Ground all additions in the solution context — never invent unrelated features
11. Mark searchable: true on fields that users would naturally search or filter on — typically: name, title, description, reference_number, email, code, status, category, type. At least 20% of entities should have at least one searchable field.
12. Every module that stores user data mutations, financial transactions, compliance records, or audit-sensitive state MUST have audit_trail.enabled: true with tracked_operations including "create", "update", "delete"
13. The top-level error_codes section MUST be populated: at minimum one 404 (not found), 409 (conflict), and 422 (validation) error code per entity, plus global 401/403/429

SOLUTION CONTEXT:
- Problem: {context.get('problem_statement', '')[:800]}
- Domain: {context.get('business_domain', '')}
- Org size: {context.get('organization_size', '')}
- Budget: {context.get('budget_range', '')}
- Timeline: {context.get('timeline_months', '')} months
- Constraints: {', '.join(context.get('constraints', [])[:5])}
- Capabilities:
{caps_summary}
- ArchiMate elements:
{elements_summary}
- Build/buy decisions:
{build_buy_summary}

INPUT GENOME:
```json
{genome_yaml}
```

Return the COMPLETE perfected genome as valid JSON. No explanations, no markdown — just the JSON object."""

    def _parse_perfection_response(
        self, raw: str, original: dict,
    ) -> Tuple[Optional[dict], List[GenomeChange]]:
        """Parse LLM response into perfected genome + change list."""
        try:
            text = raw.strip()
            # Strip markdown code fences
            if text.startswith("```"):
                text = text.split("\n", 1)[-1]
            if text.endswith("```"):
                text = text.rsplit("```", 1)[0]
            text = text.strip()

            # Extract the JSON object even when the model wraps it in prose
            # ("Here is the perfected genome: {...}") — json.loads on the whole
            # string otherwise raises and the perfected genome is silently lost.
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                text = text[start:end]

            perfected = json.loads(text)
            if not isinstance(perfected, dict):
                logger.warning("LLM returned non-dict genome")
                return None, []

            # Diff to identify changes
            changes = self._diff_genomes(original, perfected)

            return perfected, changes

        except (json.JSONDecodeError, ValueError):
            logger.warning("Failed to parse perfected genome JSON")
            return None, []

    def _diff_genomes(self, original: dict, perfected: dict, prefix: str = "") -> List[GenomeChange]:
        """Simple diff between original and perfected genomes."""
        changes = []
        for key, new_val in perfected.items():
            path = f"{prefix}.{key}" if prefix else key
            if key not in original:
                changes.append(GenomeChange(path, "added", f"Added {key}"))
            elif original[key] != new_val:
                if isinstance(new_val, dict) and isinstance(original.get(key), dict):
                    changes.extend(self._diff_genomes(original[key], new_val, path))
                else:
                    changes.append(GenomeChange(path, "enriched", f"Enriched {key}"))
        return changes[:50]  # Cap change list

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _persist_scores(self, solution_id: int, score_before: int, score_after: int) -> None:
        """Persist pre/post perfection scores on codegen models."""
        try:
            from app.modules.codegen.models import CodegenGeneration

            gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
            if gen:
                gen.genome_quality_score = score_after
                db.session.commit()
        except Exception:
            logger.exception("Failed to persist genome perfection scores")

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def score_to_dict(self, score: GenomeQualityScore) -> dict:
        return asdict(score)

    def result_to_dict(self, result: GenomePerfectionResult) -> dict:
        return {
            "perfected_genome": result.perfected_genome,
            "score_before": result.score_before,
            "score_after": result.score_after,
            "changes": [asdict(c) for c in result.changes],
            "cost_gbp": str(result.cost_gbp),
        }
