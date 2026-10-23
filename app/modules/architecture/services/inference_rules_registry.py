"""
InferenceRulesRegistry — centralized rules engine for ArchiMate element
inference and canonical chain enforcement.

Pure Python module: no DB, no LLM, no I/O.
"""

import re
from dataclasses import dataclass
from app.models.archimate_element_types import StandardTypeTable

# ---------------------------------------------------------------------------
# Element inference rules: regex pattern -> inferred element metadata
# ---------------------------------------------------------------------------

ELEMENT_INFERENCE_RULES = {
    "increase|reduce|improve|maximize|minimize": {
        "infer": "Outcome", "with_measurable_target": True, "layer": "motivation", "priority": 1,
    },
    "must|shall|cannot|will not": {
        "infer": "Constraint", "from": "Requirement", "layer": "motivation", "priority": 1,
    },
    "trust|speed|quality|integrity|transparency": {
        "infer": "Principle", "from": "Stakeholder", "layer": "motivation", "priority": 2,
    },
    "manage|deliver|analyse|govern|enable": {
        "infer": "Capability", "layer": "strategy", "priority": 1,
    },
    "plan|govern|design": {
        "infer": "BusinessProcess", "subtype": "supporting", "layer": "business", "priority": 2,
    },
    "deliver|operate|execute": {
        "infer": "BusinessProcess", "subtype": "value_stream", "layer": "business", "priority": 2,
    },
    "approve|validate|review": {
        "infer": "ApplicationService", "subtype": "workflow", "layer": "application", "priority": 1,
    },
    "maintain|update|record|store": {
        "infer": "ApplicationService", "subtype": "system_of_record", "layer": "application", "priority": 1,
    },
    "analyse|report|dashboard": {
        "infer": "ApplicationService", "subtype": "analytics", "layer": "application", "priority": 2,
    },
    "orchestrate|transform|integrate": {
        "infer": "ApplicationService", "subtype": "integration", "layer": "application", "priority": 2,
    },
    "monitor|alert|notify": {
        "infer": "ApplicationService", "subtype": "monitoring", "layer": "application", "priority": 3,
    },
    "api|rest|graphql": {
        "infer": "TechnologyService", "subtype": "api_gateway", "layer": "technology", "priority": 1,
    },
    "analytics|ml|ai": {
        "infer": "Node", "subtype": "compute_cluster", "layer": "technology", "priority": 2,
    },
    "workflow|orchestrat": {
        "infer": "TechnologyService", "subtype": "orchestration_engine", "layer": "technology", "priority": 2,
    },
    "transactional|crud|database": {
        "infer": "Node", "subtype": "database_server", "layer": "technology", "priority": 1,
    },
}

# ---------------------------------------------------------------------------
# Canonical chain: (parent_type, child_type, metadata)
# ---------------------------------------------------------------------------

CANONICAL_CHAIN = [
    ("Stakeholder", "Driver", {"type": "association", "cardinality": "1:N", "required": True, "pass": 1}),
    ("Driver", "Assessment", {"type": "association", "cardinality": "1:N", "required": True, "pass": 1}),
    ("Assessment", "Goal", {"type": "influence", "cardinality": "1:N", "required": True, "pass": 1}),
    ("Goal", "Outcome", {"type": "realization", "cardinality": "1:N", "required": True, "pass": 1, "archimate_source": "child"}),
    ("Goal", "Principle", {"type": "realization", "cardinality": "1:N", "required": False, "pass": 1, "archimate_source": "child"}),
    ("Outcome", "Requirement", {"type": "realization", "cardinality": "1:N", "required": True, "pass": 1, "archimate_source": "child"}),
    ("Principle", "Requirement", {"type": "influence", "cardinality": "N:M", "required": False, "pass": 1}),
    ("Requirement", "Constraint", {"type": "specialization", "cardinality": "1:N", "required": False, "pass": 1}),
    ("Goal", "CourseOfAction", {"type": "realization", "cardinality": "1:N", "required": False, "pass": 2, "archimate_source": "child"}),
    ("CourseOfAction", "Capability", {"type": "realization", "cardinality": "1:N", "required": False, "pass": 2, "archimate_source": "child"}),
    ("Goal", "Capability", {"type": "realization", "cardinality": "1:N", "required": False, "pass": 2, "archimate_source": "child"}),
    ("Outcome", "Capability", {"type": "realization", "cardinality": "1:N", "required": False, "pass": 2, "archimate_source": "child"}),
    ("Requirement", "Capability", {"type": "realization", "cardinality": "N:M", "required": False, "pass": 2, "archimate_source": "child"}),
    ("Principle", "Capability", {"type": "influence", "cardinality": "N:M", "required": False, "pass": 2}),
    ("Capability", "Capability", {"type": "composition", "cardinality": "1:N", "required": False, "pass": 1}),
    ("Capability", "BusinessProcess", {"type": "realization", "cardinality": "1:N", "required": False, "pass": 2, "archimate_source": "child"}),
    ("Capability", "BusinessFunction", {"type": "realization", "cardinality": "1:N", "required": False, "pass": 2, "archimate_source": "child"}),
    ("Capability", "BusinessRole", {"type": "association", "cardinality": "1:N", "required": False, "pass": 2}),
    ("Capability", "ValueStreamStage", {"type": "serving", "cardinality": "1:N", "required": False, "pass": 2, "archimate_source": "child"}),
    ("BusinessRole", "BusinessProcess", {"type": "assignment", "cardinality": "N:M", "required": False, "pass": 1}),
    ("BusinessProcess", "BusinessEvent", {"type": "triggering", "cardinality": "1:N", "required": False, "pass": 1}),
    ("BusinessService", "BusinessProcess", {"type": "realization", "cardinality": "1:N", "required": False, "pass": 1, "archimate_source": "child"}),
    ("BusinessProcess", "ApplicationService", {"type": "serving", "cardinality": "1:N", "required": True, "pass": 2, "archimate_source": "child"}),
    ("ApplicationService", "ApplicationComponent", {"type": "realization", "cardinality": "1:N", "required": True, "pass": 2, "archimate_source": "child"}),
    ("ApplicationFunction", "ApplicationComponent", {"type": "realization", "cardinality": "1:N", "required": False, "pass": 1, "archimate_source": "child"}),
    ("BusinessProcess", "BusinessObject", {"type": "access", "cardinality": "N:M", "required": False, "pass": 1}),
    ("ApplicationComponent", "DataObject", {"type": "access", "cardinality": "1:N", "required": False, "pass": 2}),
    ("BusinessRole", "ApplicationService", {"type": "serving", "cardinality": "N:M", "required": False, "pass": 2, "archimate_source": "child"}),
    ("ApplicationComponent", "TechnologyService", {"type": "serving", "cardinality": "1:N", "required": True, "pass": 2, "archimate_source": "child"}),
    ("TechnologyService", "TechnologyFunction", {"type": "realization", "cardinality": "1:N", "required": False, "pass": 2, "archimate_source": "child"}),
    ("TechnologyFunction", "TechnologyComponent", {"type": "composition", "cardinality": "1:N", "required": False, "pass": 2}),
    ("TechnologyComponent", "Node", {"type": "assignment", "cardinality": "N:1", "required": True, "pass": 2}),
    ("Node", "Artifact", {"type": "assignment", "cardinality": "1:N", "required": False, "pass": 2}),
    ("Node", "CommunicationNetwork", {"type": "association", "cardinality": "N:M", "required": False, "pass": 2}),
    ("Assessment", "Gap", {"type": "association", "cardinality": "1:N", "required": False, "pass": 2}),
    ("Gap", "WorkPackage", {"type": "realization", "cardinality": "1:N", "required": True, "pass": 2, "archimate_source": "child"}),
    ("WorkPackage", "Deliverable", {"type": "realization", "cardinality": "1:N", "required": True, "pass": 2, "archimate_source": "child"}),
    ("WorkPackage", "Plateau", {"type": "association", "cardinality": "N:M", "required": False, "pass": 2}),
    ("Plateau", "Gap", {"type": "association", "cardinality": "1:N", "required": False, "pass": 2}),
]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class InferredElement:
    """Result of a rule-based element inference."""
    element_type: str
    key: dict          # e.g. {"name": "..."}
    defaults: dict     # e.g. {"description": "...", "subtype": "..."}
    rule_name: str


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class InferenceRulesRegistry:
    def __init__(self):
        self._compiled_rules = [
            (re.compile(r"\b(?:" + pattern + r")\b", re.IGNORECASE), rule)
            for pattern, rule in ELEMENT_INFERENCE_RULES.items()
        ]

    def match_element_rule(self, text: str, source_layer: str) -> dict | None:
        """Match text against ELEMENT_INFERENCE_RULES, filtered by layer context.
        Returns the winning rule dict or None.
        """
        matches = []
        valid_layers = self._valid_target_layers(source_layer)
        for compiled, rule in self._compiled_rules:
            if compiled.search(text) and rule["layer"] in valid_layers:
                matches.append(rule)
        if not matches:
            return None
        return self.resolve_conflict(matches, source_layer)

    def resolve_conflict(self, matches: list[dict], source_layer: str) -> dict:
        """Deterministic conflict resolution: layer -> priority -> specificity."""
        return sorted(matches, key=lambda m: m["priority"])[0]

    def allowed_downstream_types(self, element_type: str) -> list[str]:
        """Types that can appear downstream of element_type in the canonical chain."""
        return list({child for parent, child, _ in CANONICAL_CHAIN if parent == element_type})

    def allowed_upstream_types(self, element_type: str) -> list[str]:
        """Types that can appear upstream of element_type in the canonical chain."""
        return list({parent for parent, child, _ in CANONICAL_CHAIN if child == element_type})

    def canonical_rel_type(self, parent_type: str, child_type: str) -> str:
        """Lookup the relationship type between parent and child in CANONICAL_CHAIN."""
        for parent, child, meta in CANONICAL_CHAIN:
            if parent == parent_type and child == child_type:
                return meta["type"]
        return "association"  # fallback

    def required_downstream(self, element_type: str) -> list[tuple[str, str, dict]]:
        """Return only required=True downstream entries as (target_type, rel_type, meta)."""
        return [
            (child, meta["type"], meta)
            for parent, child, meta in CANONICAL_CHAIN
            if parent == element_type and meta.get("required", False)
        ]

    def expected_downstream(self, element_type: str) -> list[tuple[str, str, dict]]:
        """Return all downstream entries (required + optional)."""
        return [
            (child, meta["type"], meta)
            for parent, child, meta in CANONICAL_CHAIN
            if parent == element_type
        ]

    def missing_intra_layer(self, element_type: str) -> list[tuple[str, str, dict]]:
        """Return pass-1 downstream entries (intra-layer only)."""
        return [
            (child, meta["type"], meta)
            for parent, child, meta in CANONICAL_CHAIN
            if parent == element_type and meta["pass"] == 1
        ]

    def infer_element(self, node, target_type: str) -> InferredElement | None:
        """Apply element inference rules to generate a spec for a new element.
        `node` must have .name and .element_type attributes.
        """
        if not hasattr(node, "name") or not node.name:
            return None

        # Extract the root name — strip compound prefixes like "BusinessProcess for Capability for X"
        root_name = self._extract_root_name(node.name)
        result = self.match_element_rule(root_name, self._layer_for_type(node.element_type))

        # Build a clean, short name (max 95 chars to stay under varchar(100))
        # Do NOT include the type as a prefix — names must be descriptive, not "Outcome: X"
        name = root_name[:95] if len(root_name) > 95 else root_name

        if result and result["infer"] == target_type:
            return InferredElement(
                element_type=target_type,
                key={"name": name},
                defaults={
                    "description": "Inferred from %s: %s" % (node.element_type, root_name),
                    "subtype": result.get("subtype"),
                },
                rule_name="%s_to_%s" % (node.element_type, target_type),
            )
        # Fallback: generate a generic element if no rule matched
        return InferredElement(
            element_type=target_type,
            key={"name": name},
            defaults={"description": "Inferred from %s: %s" % (node.element_type, root_name)},
            rule_name="generic_%s_to_%s" % (node.element_type, target_type),
        )

    @staticmethod
    def _extract_root_name(name: str) -> str:
        """Strip compound inference prefixes to get the original root name.
        E.g. 'BusinessProcess for Capability for Reduce IT cost' -> 'Reduce IT cost'
        """
        # Known element type prefixes that appear in inferred names
        prefixes = [
            "Outcome: ", "Capability: ", "BusinessProcess: ", "BusinessFunction: ",
            "ApplicationService: ", "ApplicationComponent: ", "DataObject: ",
            "TechnologyService: ", "TechnologyComponent: ", "Node: ",
            "WorkPackage: ", "Deliverable: ", "Gap: ", "Plateau: ",
            "BusinessRole: ", "BusinessObject: ", "Artifact: ",
            # Legacy "X for Y" format
            "Outcome for ", "Capability for ", "BusinessProcess for ",
            "BusinessFunction for ", "ApplicationService for ",
            "ApplicationComponent for ", "DataObject for ",
            "TechnologyService for ", "TechnologyComponent for ",
            "Node for ", "WorkPackage for ", "Deliverable for ",
            "Gap for ", "Plateau for ", "BusinessRole for ",
            "BusinessObject for ", "Artifact for ",
        ]
        result = name
        changed = True
        while changed:
            changed = False
            for prefix in prefixes:
                if result.startswith(prefix):
                    result = result[len(prefix):]
                    changed = True
                    break
        return result.strip() or name

    def _valid_target_layers(self, source_layer: str) -> list[str]:
        """Layer progression: each layer can target itself or the next."""
        return {
            "motivation": ["motivation", "strategy"],
            "strategy": ["strategy", "business"],
            "business": ["business", "application"],
            "application": ["application", "technology"],
            "technology": ["technology", "implementation"],
            "implementation": ["implementation"],
        }.get(source_layer, [source_layer])

    def _layer_for_type(self, element_type: str) -> str:
        """Map element type to its ArchiMate layer."""
        layer_map = StandardTypeTable({
            "Stakeholder": "motivation", "Driver": "motivation", "Assessment": "motivation",
            "Goal": "motivation", "Outcome": "motivation", "Principle": "motivation",
            "Requirement": "motivation", "Constraint": "motivation",
            "CourseOfAction": "strategy", "Capability": "strategy",
            "ValueStreamStage": "strategy", "Resource": "strategy",
            "BusinessProcess": "business", "BusinessFunction": "business",
            "BusinessService": "business", "BusinessEvent": "business",
            "BusinessRole": "business", "BusinessObject": "business",
            "ApplicationService": "application", "ApplicationComponent": "application",
            "ApplicationFunction": "application", "DataObject": "application",
            "TechnologyService": "technology", "TechnologyFunction": "technology",
            "TechnologyComponent": "technology", "Node": "technology",
            "Artifact": "technology", "CommunicationNetwork": "technology",
            "Gap": "implementation", "WorkPackage": "implementation",
            "Deliverable": "implementation", "Plateau": "implementation",
        })
        return layer_map.get(element_type, "business")
