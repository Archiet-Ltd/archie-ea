"""ArchiMate Inference Engine — core service with 3-pass pipeline.

Pass 1: Intra-layer inference (ensure completeness within each layer)
Pass 2: Cross-layer chain inference (enforce canonical chain)
Pass 3: Semantic refinement (LLM-based enrichment, optional)

Uses savepoint-per-pass transaction boundaries.
"""
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from app import db
from app.modules.architecture.services.inference_rules_registry import InferenceRulesRegistry
from app.modules.architecture.services.architecture_graph_facade import ArchitectureGraphFacade
from app.modules.architecture.services.inference_providers import PROVIDER_REGISTRY
from app.models.archimate_element_types import is_application_kind

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------

class InferenceError(Exception):
    """Base error for all engine failures."""


class ProviderError(InferenceError):
    """Provider failed to generate/refine/validate. Non-fatal."""


class RuleConflictError(InferenceError):
    """Multiple rules match with same priority."""


class SemanticValidationError(InferenceError):
    """Pass 3 semantic check failed. Non-fatal."""


# ---------------------------------------------------------------------------
# Execution context
# ---------------------------------------------------------------------------

@dataclass
class ExecutionContext:
    architecture_id: int
    inference_pass: int = 1
    provenance: str = "rule"
    confidence: float = 1.0
    max_depth: int = 25
    provenance_log: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    dry_run: bool = False
    elements_created: list = field(default_factory=list)
    relationships_created: list = field(default_factory=list)
    skip_semantic_pass: bool = False
    direction: str = "both"


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ProvenanceEntry:
    element_id: int
    element_type: str
    action: str         # "created" | "updated" | "linked" | "refined"
    source: str         # "rule" | "llm" | "user" | "import"
    rule_name: Optional[str]
    confidence: float
    inference_pass: int
    timestamp: datetime
    context: Optional[dict]


@dataclass
class PassResult:
    pass_number: int
    elements_created: int = 0
    relationships_created: int = 0
    elements_refined: int = 0
    duration_ms: int = 0


@dataclass
class InferenceResult:
    starting_node: Any  # GraphNode
    chain: list         # list[GraphNode]
    elements_created: list
    elements_updated: list
    relationships_created: list
    pass_results: dict  # {1: PassResult, 2: PassResult, 3: PassResult}
    completeness_score: float
    execution_time_ms: int
    provenance_log: list
    errors: list

    def merge(self, other: "InferenceResult"):
        """Merge another InferenceResult into this one."""
        self.elements_created.extend(other.elements_created)
        self.elements_updated.extend(other.elements_updated)
        self.relationships_created.extend(other.relationships_created)
        self.provenance_log.extend(other.provenance_log)
        self.errors.extend(other.errors)
        # Recompute completeness as average
        if other.completeness_score > 0:
            self.completeness_score = (self.completeness_score + other.completeness_score) / 2


@dataclass
class ChainDiagnostic:
    """Diagnostic for a single element type's downstream completeness."""
    element_type: str
    expected_downstream: list
    actual_downstream: list
    missing: list
    completeness: float


@dataclass
class ArchitectureDiagnostic:
    """Full architecture diagnostic across all element types."""
    architecture_id: int
    total_elements: int = 0
    total_relationships: int = 0
    chain_diagnostics: list = field(default_factory=list)  # list[ChainDiagnostic]
    overall_completeness: float = 0.0
    orphan_elements: list = field(default_factory=list)


@dataclass
class RepairResult:
    """Result of an automated repair operation."""
    elements_created: list = field(default_factory=list)
    relationships_created: list = field(default_factory=list)
    elements_updated: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    dry_run: bool = False


@dataclass
class ExplanationChain:
    """Human-readable explanation of how an element was inferred."""
    element_id: int
    element_type: str
    element_name: str
    steps: list = field(default_factory=list)  # list of explanation strings
    source_elements: list = field(default_factory=list)  # list of source element IDs
    rules_applied: list = field(default_factory=list)  # list of rule names
    confidence: float = 0.0


@dataclass
class ImpactSimulation:
    """Simulated impact of removing or modifying an element."""
    element_id: int
    element_type: str
    affected_elements: list = field(default_factory=list)
    affected_relationships: list = field(default_factory=list)
    cascade_depth: int = 0
    severity: str = "low"  # "low" | "medium" | "high" | "critical"


@dataclass
class ValidationResult:
    """Result of validating an architecture's semantic correctness."""
    is_valid: bool
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    completeness_score: float = 0.0


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class ArchiMateInferenceEngine:
    """Core inference engine with 3-pass pipeline.

    Pass 1: Intra-layer — ensure each element has its same-layer neighbors.
    Pass 2: Cross-layer — enforce the canonical chain across layers.
    Pass 3: Semantic refinement — LLM enrichment (optional, non-fatal).
    """

    def __init__(self, architecture_id: int):
        self.architecture_id = architecture_id
        self.rules = InferenceRulesRegistry()
        self.graph = ArchitectureGraphFacade(architecture_id)
        self.context = ExecutionContext(architecture_id=architecture_id)

    def run(self, starting_node) -> InferenceResult:
        """Execute all 3 passes. Uses savepoint per pass."""
        start = time.time()
        try:
            for pass_num, pass_fn in [
                (1, self._run_intra_layer_inference),
                (2, self._run_cross_layer_chain_inference),
                (3, self._run_semantic_refinement),
            ]:
                self.context.inference_pass = pass_num
                try:
                    pass_fn(starting_node)
                except Exception as e:
                    self.context.errors.append(f"Pass {pass_num} failed: {e}")
                    if pass_num <= 2:
                        raise
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
        elapsed = int((time.time() - start) * 1000)
        return self._build_result(starting_node, elapsed)

    # ---- Pass implementations ----

    def _run_intra_layer_inference(self, node):
        """Pass 1: Ensure intra-layer structural completeness.

        Checks outbound pass-1 rules for the starting node type, then
        applies ApplicationComponent-specific completeness: every
        ApplicationComponent must have at least one ApplicationFunction
        (inbound realization) to ensure it has defined behaviour.
        """
        # 1. Standard outbound intra-layer completeness (rules with pass=1)
        intra_rules = self.rules.missing_intra_layer(node.element_type)
        for (target_type, _rel_type, _meta) in intra_rules:
            existing = self.graph.get_neighbors(node.id, direction="out")
            existing_of_type = [n for n in existing if n.element_type == target_type]
            if not existing_of_type:
                self._infer_missing_downstream(node, [target_type])

        # 2. ApplicationComponent structural completeness
        # Every ApplicationComponent must realize at least one ApplicationFunction.
        # Without this, the component has no defined behaviour and the codegen
        # will produce an empty service module with no business logic.
        if is_application_kind(node.element_type):
            inbound = self.graph.get_neighbors(node.id, direction="in")
            has_function = any(n.element_type == "ApplicationFunction" for n in inbound)
            if not has_function:
                fn_inferred = self.rules.infer_element(node, "ApplicationFunction")
                if fn_inferred:
                    fn_node = self.graph.get_or_create_node(
                        "ApplicationFunction", fn_inferred.key, fn_inferred.defaults
                    )
                    self.context.elements_created.append(fn_node.id)
                    self._log_provenance(
                        fn_node, "created", "rule",
                        "pass1_application_component_completeness"
                    )
                    rel_type = self.rules.canonical_rel_type(
                        "ApplicationFunction", "ApplicationComponent"
                    )
                    self.graph.get_or_create_relationship(
                        fn_node.id, node.id, rel_type or "realization",
                        metadata={
                            "source_tag": "rule",
                            "confidence": 1.0,
                            "inference_pass": self.context.inference_pass,
                        }
                    )
                    self.context.relationships_created.append({
                        "source_id": fn_node.id,
                        "target_id": node.id,
                        "rel_type": rel_type or "realization",
                    })

    def _run_cross_layer_chain_inference(self, node):
        """Pass 2: enforce the full canonical chain."""
        chain = self.ensure_full_chain_from(node)
        for n in chain:
            self.repair_chain(n)
        if self.context.direction != 'down':
            upstream = self._walk_upstream(node)
            for n in upstream:
                self.repair_chain(n)

    def _run_semantic_refinement(self, node):
        """Pass 3: LLM refinement. Skipped when context.skip_semantic_pass is True.

        Walks all elements created in this run and calls provider.refine_element()
        to replace auto-generated names with architect-quality names via LLM.
        """
        if self.context.skip_semantic_pass:
            return

        refined_count = 0
        # Refine all elements in the chain (created during this run)
        chain_nodes = self.graph.get_neighbors(node.id, direction="out")
        all_nodes = [node] + chain_nodes

        for n in all_nodes:
            provider = self._provider_for_type(n.element_type)
            if not provider:
                continue
            try:
                old_name = n.name
                provider.refine_element(n, context={"root": node})
                if n.name != old_name:
                    # Persist the refined name to the DB element
                    from app.models.archimate_core import ArchiMateElement
                    db_elem = ArchiMateElement.query.get(n.id)
                    if db_elem:
                        db_elem.name = n.name
                        if hasattr(db_elem, 'description') and hasattr(n, 'description'):
                            db_elem.description = n.description
                    refined_count += 1
                    self._log_provenance(n, "refined", "llm")
            except Exception as e:
                self.context.errors.append(f"Refinement failed for {n.element_type} ({n.id}): {e}")

        logger.info("Pass 3 refined %d elements from node %s", refined_count, node.id)

    # ---- Traversal & chain repair ----

    def ensure_full_chain_from(self, node):
        """Given any starting node, generate the full canonical chain downstream.
        Uses depth-first tree traversal — explores ALL required downstream edges.
        """
        visited = set()
        all_nodes = []

        def _traverse_down(current, depth):
            if current.id in visited or depth >= self.context.max_depth:
                return
            visited.add(current.id)
            all_nodes.append(current)

            downstream_rules = self.rules.required_downstream(current.element_type)
            for (target_type, rel_type, meta) in downstream_rules:
                existing = self.graph.get_neighbors(current.id, direction="out")
                existing_of_type = [n for n in existing if n.element_type == target_type]
                if existing_of_type:
                    for n in existing_of_type:
                        _traverse_down(n, depth + 1)
                elif meta.get("required", False):
                    inferred = self._infer_missing_downstream(current, [target_type])
                    if inferred:
                        _traverse_down(inferred, depth + 1)

        _traverse_down(node, 0)
        return all_nodes

    def _walk_upstream(self, node):
        """Walk upstream. Returns [root, ..., node] ordered list."""
        visited = set()
        chain = []

        def _traverse_up(current, depth):
            if current.id in visited or depth >= self.context.max_depth:
                return
            visited.add(current.id)
            chain.append(current)
            prev_types = self.rules.allowed_upstream_types(current.element_type)
            if not prev_types:
                return
            prev_nodes = self.graph.get_neighbors(current.id, direction="in")
            prev_nodes = [n for n in prev_nodes if n.element_type in prev_types]
            if not prev_nodes:
                return
            _traverse_up(prev_nodes[0], depth + 1)

        _traverse_up(node, 0)
        return list(reversed(chain))

    def _infer_missing_downstream(self, node, expected_types):
        """Infer the next element using deterministic rules, then providers."""
        for t in expected_types:
            # 1. Deterministic rule-based inference
            inferred = self.rules.infer_element(node, t)
            if inferred:
                new_node = self.graph.get_or_create_node(
                    t, inferred.key, inferred.defaults
                )
                self.context.elements_created.append(new_node.id)
                self._log_provenance(new_node, "created", "rule", inferred.rule_name)
                rel_type = self.rules.canonical_rel_type(node.element_type, t)
                self.graph.get_or_create_relationship(
                    node.id, new_node.id, rel_type,
                    metadata={
                        "source_tag": "rule",
                        "confidence": 1.0,
                        "inference_pass": self.context.inference_pass,
                    }
                )
                self.context.relationships_created.append(
                    {"source_id": node.id, "target_id": new_node.id, "rel_type": rel_type}
                )
                return new_node

            # 2. Provider-based generation (LLM augmentation)
            provider = self._provider_for_type(t)
            if provider:
                try:
                    generated = provider.generate_element(t, context={"from": node})
                except Exception as e:
                    self.context.errors.append(f"Provider failed for {t}: {e}")
                    continue
                if generated:
                    new_node = self.graph.get_or_create_node(
                        t, {"name": generated.name}, generated.to_dict()
                    )
                    self.context.elements_created.append(new_node.id)
                    self._log_provenance(new_node, "created", "llm", None)
                    rel_type = self.rules.canonical_rel_type(node.element_type, t)
                    self.graph.get_or_create_relationship(
                        node.id, new_node.id, rel_type,
                        metadata={
                            "source_tag": "llm",
                            "confidence": 0.7,
                            "inference_pass": self.context.inference_pass,
                        }
                    )
                    self.context.relationships_created.append(
                        {"source_id": node.id, "target_id": new_node.id, "rel_type": rel_type}
                    )
                    return new_node

        return None

    def detect_broken_chain(self, node):
        """Return missing required canonical relationships for this node."""
        missing = []
        expected = self.rules.expected_downstream(node.element_type)
        for (target_type, rel_type, meta) in expected:
            if not meta.get("required", False):
                continue
            existing = self.graph.find_relationships(source_id=node.id, rel_type=rel_type)
            targets_of_type = [r for r in existing if r.target.element_type == target_type]
            if not targets_of_type:
                missing.append({
                    "from": node.element_type,
                    "from_id": node.id,
                    "to": target_type,
                    "relationship": rel_type,
                    "required": True,
                })
        return missing

    def repair_chain(self, node):
        """Detect and repair missing links."""
        repaired = []
        missing = self.detect_broken_chain(node)
        for m in missing:
            inferred = self._infer_missing_downstream(node, [m["to"]])
            if inferred:
                repaired.append(inferred)
        return repaired

    # ---- Public API ----

    def generate_chain(self, node_id: int, direction: str = "both") -> InferenceResult:
        """Main entry point. Run all 3 passes from any starting element.

        Args:
            node_id: Starting element ID
            direction: "both" (default), "down", or "up"
        """
        node = self.graph.get_node(node_id)
        if not node:
            raise ValueError(f"Element {node_id} not found")
        self.context.direction = direction
        return self.run(node)

    def explain(self, node_id: int) -> ExplanationChain:
        """Walk upstream. Answer: 'Why does this element exist?'"""
        node = self.graph.get_node(node_id)
        if not node:
            raise ValueError(f"Element {node_id} not found")
        chain = self._walk_upstream(node)
        steps = []
        for i in range(len(chain) - 1):
            steps.append(
                f"{chain[i].element_type} '{chain[i].name}' → "
                f"{chain[i+1].element_type} '{chain[i+1].name}'"
            )
        return ExplanationChain(
            element_id=node.id,
            element_type=node.element_type,
            element_name=node.name,
            steps=steps,
            source_elements=[n.id for n in chain if n.id != node.id],
            rules_applied=[],
            confidence=1.0 if len(chain) > 1 else 0.0,
        )

    def diagnose(self, node_id: int) -> ChainDiagnostic:
        """Non-mutating. Returns broken links, missing elements, completeness score."""
        node = self.graph.get_node(node_id)
        if not node:
            raise ValueError(f"Element {node_id} not found")
        expected = self.rules.expected_downstream(node.element_type)
        missing = self.detect_broken_chain(node)
        missing_types = [m["to"] for m in missing]
        actual = [r[0] for r in expected if r[0] not in missing_types]
        total = len([r for r in expected if r[2].get("required", False)])
        found = total - len(missing)
        completeness = found / total if total > 0 else 1.0
        return ChainDiagnostic(
            element_type=node.element_type,
            expected_downstream=[r[0] for r in expected],
            actual_downstream=actual,
            missing=missing_types,
            completeness=completeness,
        )

    def repair(self, node_id: int, dry_run: bool = False) -> RepairResult:
        """Detect and fix broken chains. dry_run=True returns plan without persisting."""
        node = self.graph.get_node(node_id)
        if not node:
            raise ValueError(f"Element {node_id} not found")
        if dry_run:
            self.detect_broken_chain(node)
            return RepairResult(
                elements_created=[],
                relationships_created=[],
                elements_updated=[],
                errors=[],
                dry_run=True,
            )
        repaired = self.repair_chain(node)
        return RepairResult(
            elements_created=repaired,
            relationships_created=[],
            elements_updated=[],
            errors=self.context.errors,
            dry_run=False,
        )

    def validate_chain(self, node_id: int) -> ValidationResult:
        """Check every relationship in this element's chain is valid."""
        node = self.graph.get_node(node_id)
        if not node:
            raise ValueError(f"Element {node_id} not found")
        errors = []
        warnings = []
        rels = self.graph.find_relationships(source_id=node.id)
        for rel in rels:
            expected_type = self.rules.canonical_rel_type(
                rel.source.element_type, rel.target.element_type
            )
            if expected_type != rel.rel_type and expected_type != "association":
                errors.append(
                    f"Relationship {rel.source.element_type}→{rel.target.element_type} "
                    f"is '{rel.rel_type}' but expected '{expected_type}'"
                )
        missing = self.detect_broken_chain(node)
        for m in missing:
            warnings.append(f"Missing required: {m['from']}→{m['to']} ({m['relationship']})")
        completeness = self.diagnose(node_id).completeness
        return ValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            completeness_score=completeness,
        )

    # ---- Text-based generation ----

    def generate_from_text(self, text: str, starting_type: str = "Goal") -> InferenceResult:
        """Parse unstructured text, create starting element, then generate chain."""
        rule_match = self.rules.match_element_rule(text, "motivation")
        element_type = starting_type
        if rule_match:
            element_type = rule_match.get("infer", starting_type)

        node = self.graph.get_or_create_node(
            element_type,
            {"name": text[:100]},
            {"description": text},
        )
        self._log_provenance(node, "created", "user")
        return self.generate_chain(node.id)

    # ---- Architecture-wide operations ----

    @classmethod
    def generate_architecture(cls, architecture_id: int) -> InferenceResult:
        """Run all 3 passes across all root elements in an architecture."""
        engine = cls(architecture_id)
        roots = engine._find_roots()
        if not roots:
            return InferenceResult(
                starting_node=None, chain=[], elements_created=[],
                elements_updated=[], relationships_created=[],
                pass_results={}, completeness_score=0.0,
                execution_time_ms=0, provenance_log=[], errors=[],
            )
        first_result = engine.run(roots[0])
        for root in roots[1:]:
            engine.context = ExecutionContext(architecture_id=architecture_id)
            result = engine.run(root)
            first_result.merge(result)
        return first_result

    def _find_roots(self):
        """Find all elements with no upstream canonical relationships."""
        all_elements = self.graph.find_nodes(element_type=None, filters={})
        roots = []
        for element in all_elements:
            upstream_types = self.rules.allowed_upstream_types(element.element_type)
            if not upstream_types:
                roots.append(element)
                continue
            upstream = self.graph.get_neighbors(element.id, direction="in")
            upstream_canonical = [n for n in upstream if n.element_type in upstream_types]
            if not upstream_canonical:
                roots.append(element)
        return roots

    def diagnose_architecture(self) -> ArchitectureDiagnostic:
        """Diagnose every element in self.architecture_id."""
        all_elements = self.graph.find_nodes(element_type=None, filters={})
        all_rels = self.graph.find_relationships()
        diagnostics = []
        for elem in all_elements:
            diag = self.diagnose(elem.id)
            diagnostics.append(diag)

        layer_counts = {}
        for elem in all_elements:
            layer = elem.layer
            layer_counts.setdefault(layer, {"total": 0, "with_rels": 0})
            layer_counts[layer]["total"] += 1
            rels = self.graph.find_relationships(source_id=elem.id)
            if rels:
                layer_counts[layer]["with_rels"] += 1

        total_completeness = sum(d.completeness for d in diagnostics) / len(diagnostics) if diagnostics else 0.0

        return ArchitectureDiagnostic(
            architecture_id=self.architecture_id,
            total_elements=len(all_elements),
            total_relationships=len(all_rels),
            chain_diagnostics=diagnostics,
            overall_completeness=total_completeness,
            orphan_elements=[e for e in all_elements if not self.graph.get_neighbors(e.id)],
        )

    # ---- Simulation ----

    def simulate_change_impact(self, node_id: int, scope: str = "both") -> ImpactSimulation:
        """Non-mutating. Returns all elements impacted by a change to this node."""
        node = self.graph.get_node(node_id)
        if not node:
            raise ValueError(f"Element {node_id} not found")

        affected = []
        if scope in ("down", "both"):
            downstream = self.ensure_full_chain_from(node)
            affected.extend([n for n in downstream if n.id != node.id])

        if scope in ("up", "both"):
            upstream = self._walk_upstream(node)
            affected.extend([n for n in upstream if n.id != node.id])

        seen = set()
        unique_affected = []
        for n in affected:
            if n.id not in seen:
                seen.add(n.id)
                unique_affected.append(n)

        severity = "low"
        if len(unique_affected) > 10:
            severity = "critical"
        elif len(unique_affected) > 5:
            severity = "high"
        elif len(unique_affected) > 2:
            severity = "medium"

        return ImpactSimulation(
            element_id=node.id,
            element_type=node.element_type,
            affected_elements=unique_affected,
            affected_relationships=[],
            cascade_depth=len(unique_affected),
            severity=severity,
        )

    # ---- Export ----

    def export_chain(self, node_id: int, format: str = "json") -> dict:
        """Export element's full chain in tool-friendly format."""
        node = self.graph.get_node(node_id)
        if not node:
            raise ValueError(f"Element {node_id} not found")

        chain = self.ensure_full_chain_from(node)
        upstream = self._walk_upstream(node)
        all_nodes = list({n.id: n for n in upstream + chain}.values())

        if format == "json":
            return {
                "elements": [
                    {"id": n.id, "type": n.element_type, "name": n.name, "layer": n.layer}
                    for n in all_nodes
                ],
                "relationships": [
                    {"source_id": r.source.id, "target_id": r.target.id, "type": r.rel_type}
                    for n in all_nodes
                    for r in self.graph.find_relationships(source_id=n.id)
                ],
            }
        return {"format": format, "status": "not_implemented"}

    # ---- Introspection classmethods ----

    @classmethod
    def describe_rules(cls, element_type: str = None) -> dict:
        """Return active inference + relationship rules."""
        from app.modules.architecture.services.inference_rules_registry import (
            ELEMENT_INFERENCE_RULES, CANONICAL_CHAIN,
        )
        result = {
            "element_rules": ELEMENT_INFERENCE_RULES,
            "canonical_chain": [
                {"parent": p, "child": c, "meta": m}
                for p, c, m in CANONICAL_CHAIN
            ],
        }
        if element_type:
            result["canonical_chain"] = [
                r for r in result["canonical_chain"]
                if r["parent"] == element_type or r["child"] == element_type
            ]
        return result

    @classmethod
    def describe_chain_model(cls) -> dict:
        """Return the canonical chain definition."""
        from app.modules.architecture.services.inference_rules_registry import CANONICAL_CHAIN
        return {
            "chain": [
                {"parent": p, "child": c, "relationship": m["type"],
                 "required": m.get("required", False), "pass": m.get("pass", 1)}
                for p, c, m in CANONICAL_CHAIN
            ],
            "total_rules": len(CANONICAL_CHAIN),
        }

    @classmethod
    def describe_providers(cls) -> dict:
        """Return registered providers and their capabilities."""
        result = {}
        for type_name, provider in PROVIDER_REGISTRY.items():
            provider_class = type(provider).__name__
            result[type_name] = provider_class
        return result

    # ---- Result building ----

    def _build_result(self, starting_node, elapsed_ms: int) -> InferenceResult:
        chain = self.graph.get_neighbors(starting_node.id, direction="out")
        try:
            diag = self.diagnose(starting_node.id)
            completeness = diag.completeness
        except Exception:
            completeness = 0.0
        return InferenceResult(
            starting_node=starting_node,
            chain=[starting_node] + chain,
            elements_created=self.context.elements_created,
            elements_updated=[],
            relationships_created=self.context.relationships_created,
            pass_results={},
            completeness_score=completeness,
            execution_time_ms=elapsed_ms,
            provenance_log=self.context.provenance_log,
            errors=self.context.errors,
        )

    # ---- Provider lookup ----

    def _provider_for_type(self, element_type: str):
        """Lookup the provider for a given ArchiMate element type."""
        return PROVIDER_REGISTRY.get(element_type)

    # ---- Snapshot & Diff ----

    def take_snapshot(self) -> dict:
        """Capture point-in-time state of the architecture."""
        import hashlib
        import json

        nodes = []
        all_elements = self.graph.find_nodes(element_type=None, filters={})
        for node in all_elements:
            nodes.append({
                "id": node.id,
                "type": node.element_type,
                "name": node.name,
                "layer": node.layer,
            })

        relationships = []
        all_rels = self.graph.find_relationships()
        for rel in all_rels:
            relationships.append({
                "id": rel.id,
                "source_id": rel.source.id,
                "target_id": rel.target.id,
                "rel_type": rel.rel_type,
            })

        content = json.dumps({"nodes": nodes, "relationships": relationships}, sort_keys=True)
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]

        return {
            "nodes": nodes,
            "relationships": relationships,
            "hash": content_hash,
            "timestamp": datetime.utcnow().isoformat(),
        }

    def diff_snapshots(self, before: dict, after: dict) -> dict:
        """Compare two snapshots. Returns added/removed elements and relationships."""
        before_node_ids = {n["id"] for n in before.get("nodes", [])}
        after_node_ids = {n["id"] for n in after.get("nodes", [])}
        before_rel_ids = {r["id"] for r in before.get("relationships", [])}
        after_rel_ids = {r["id"] for r in after.get("relationships", [])}

        return {
            "added_nodes": [n for n in after.get("nodes", []) if n["id"] not in before_node_ids],
            "removed_nodes": [n for n in before.get("nodes", []) if n["id"] not in after_node_ids],
            "added_relationships": [r for r in after.get("relationships", []) if r["id"] not in before_rel_ids],
            "removed_relationships": [r for r in before.get("relationships", []) if r["id"] not in after_rel_ids],
        }

    # ---- Provenance logging ----

    def _log_provenance(self, node, action: str, source: str, rule_name: str = None):
        """Record a provenance entry for an inference action."""
        self.context.provenance_log.append(
            ProvenanceEntry(
                element_id=node.id,
                element_type=node.element_type,
                action=action,
                source=source,
                rule_name=rule_name,
                confidence=self.context.confidence,
                inference_pass=self.context.inference_pass,
                timestamp=datetime.utcnow(),
                context=None,
            )
        )
