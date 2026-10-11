"""CMP-046: ArchiMate 3.2 derived relationship computation (sect 5.5).

Implements the derivation rules that compute indirect (derived) relationships
through chains of explicit relationships.  The ArchiMate standard defines
that structural > dependency > other in precedence, and derived rels
"weaken" through the chain.

Derivation table (ArchiMate 3.2, Table 2):
    Composition + X     → X  (composition is transparent)
    Aggregation + X     → X  (aggregation is transparent)
    Realization + X     → X  (realization is transparent)
    Assignment + X      → X  (assignment is transparent)
    Serving + Serving   → Serving
    Serving + Access    → Access
    Access + Serving    → Access
    Flow + Flow         → Flow
    Triggering + Trig   → Triggering

All other combos → weakest: Association.

Max chain depth: 5 (prevent combinatorial explosion).
"""

import logging
from typing import Any, Dict, List, Set, Tuple

from app.models.constants import ArchiMateRelationshipType

logger = logging.getLogger(__name__)

# ArchiMate 3.2 relationship strength ordering (strongest → weakest)
STRENGTH_ORDER = [
    "Composition", "Aggregation", "Assignment", "Realization",
    "Serving", "Access", "Influence", "Triggering", "Flow",
    "Specialization", "Association",
]
STRENGTH_RANK = {r: i for i, r in enumerate(STRENGTH_ORDER)}

# "Transparent" structural relationships — they propagate without weakening
_TRANSPARENT = frozenset({"Composition", "Aggregation", "Realization", "Assignment"})

# Derivation table: (rel_A_type, rel_B_type) → derived_type
# If both are transparent they just propagate. Otherwise use this table.
_DERIVATION_TABLE = {
    ("Serving", "Serving"): "Serving",
    ("Serving", "Access"): "Access",
    ("Access", "Serving"): "Access",
    ("Flow", "Flow"): "Flow",
    ("Triggering", "Triggering"): "Triggering",
    ("Influence", "Influence"): "Influence",
}

MAX_DEPTH = 5


def _canonical_relationship_type(value: str) -> str:
    """Return the canonical PascalCase relationship type for derivation logic."""

    normalized = ArchiMateRelationshipType.normalize(value, pascal_case=True)
    if not normalized:
        return value
    return normalized


def _derive_type(type_a: str, type_b: str) -> str:
    """Compute the derived relationship type from chaining type_a → type_b."""
    type_a = _canonical_relationship_type(type_a)
    type_b = _canonical_relationship_type(type_b)
    # If either is transparent, result is the other
    if type_a in _TRANSPARENT:
        return type_b
    if type_b in _TRANSPARENT:
        return type_a
    # Check derivation table
    result = _DERIVATION_TABLE.get((type_a, type_b))
    if result:
        return result
    # Fallback: weakest = Association
    return "Association"


def _rule_id(type_a: str, type_b: str) -> str:
    """ADR-002-v2: a deterministic, stable identifier for the ``_derive_type``
    branch that combined ``type_a`` and ``type_b`` to produce a derived row.

    Same ``(type_a, type_b)`` always yields the same id; a different branch or
    different types yield a different one. Transparent propagation is one
    rule family (``transparent:...``), each ``_DERIVATION_TABLE`` row is its
    own rule (``table:...``), and the Association fallback is a third
    (``fallback:...``) — distinct per the ADR-002-v2 requirement that two
    genuinely different rules producing the same pair are two auditable rows,
    never one silently overwriting the other. Does not alter `_derive_type`
    itself; it mirrors the same branch order so the id always matches the
    branch that actually fired.
    """
    type_a = _canonical_relationship_type(type_a)
    type_b = _canonical_relationship_type(type_b)
    if type_a in _TRANSPARENT or type_b in _TRANSPARENT:
        branch = "transparent"
    elif (type_a, type_b) in _DERIVATION_TABLE:
        branch = "table"
    else:
        branch = "fallback"
    return f"{branch}:{type_a}:{type_b}"


def describe_rule(rule_id):
    """The rule named by a stored ``rule_id``, in words, or ``None``.

    Reads the same table ``_derive_type`` applies, so the description can only
    say what the rule actually does. An id this table did not produce (another
    engine's, or a malformed one) is not guessed at: ``None`` means the rule
    that produced the connection is not recorded in a form this engine knows.
    """
    if not isinstance(rule_id, str):
        return None
    parts = rule_id.split(":")
    if len(parts) != 3:
        return None
    branch, type_a, type_b = parts
    type_a = _canonical_relationship_type(type_a)
    type_b = _canonical_relationship_type(type_b)
    if type_a not in STRENGTH_RANK or type_b not in STRENGTH_RANK:
        return None
    if _rule_id(type_a, type_b) != rule_id:
        return None
    result = _derive_type(type_a, type_b)

    def kind(type_name):
        word = type_name.lower()
        return f"{'an' if word[0] in 'aeiou' else 'a'} {word} link"

    words = f"{kind(type_a).capitalize()} followed by {kind(type_b)} gives {kind(result)}"
    if branch == "transparent":
        return f"{words}: composition, aggregation, realization and assignment pass a connection through unchanged."
    if branch == "table":
        return f"{words}, by the ArchiMate derivation table."
    return f"{words}: no stronger rule applies, so only the weakest kind of link follows."


class ArchiMateDerivationService:
    """Compute derived relationships from a set of elements and relationships."""

    def compute_derived(
        self,
        elements: List[Dict[str, Any]],
        relationships: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Compute all derived (indirect) relationships not already explicit.

        Args:
            elements: List of {id, name, type, layer}
            relationships: List of {id, source_id, target_id, type}

        Returns:
            List of derived relationships:
            [{source_id, target_id, type, chain, depth, relationship_chain, rule_id}]

            ``chain`` is unchanged from the pre-ADR-002-v2 shape: the node path
            (element ids) walked to reach the derived pair. ``depth`` is
            unchanged: ``len(chain) - 1``. Two fields are additive:
            ``relationship_chain`` is the ordered ``archimate_relationships.id``
            sequence traversed — its length always equals ``depth`` — and
            ``rule_id`` identifies which ``_derive_type`` branch produced
            ``type`` (see ``_rule_id``).
        """
        element_ids = {e["id"] for e in elements}

        # Build adjacency: source_id → [(target_id, rel_type, rel_id)]
        adj: Dict[int, List[Tuple[int, str, int]]] = {}
        for rel in relationships:
            src = rel["source_id"]
            tgt = rel["target_id"]
            if src not in element_ids or tgt not in element_ids:
                continue
            adj.setdefault(src, []).append((tgt, _canonical_relationship_type(rel["type"]), rel["id"]))

        # Existing explicit pairs (source, target) to avoid duplicating
        explicit_pairs: Set[Tuple[int, int]] = set()
        for rel in relationships:
            explicit_pairs.add((rel["source_id"], rel["target_id"]))

        derived: List[Dict[str, Any]] = []
        seen_pairs: Set[Tuple[int, int]] = set()

        # BFS from each element
        for start_id in element_ids:
            # Queue: (current_node, accumulated_type, depth, chain_path,
            # relationship_chain). ADR-002-v2: relationship_chain is additive —
            # it carries the ordered archimate_relationships.id sequence
            # alongside the existing node path (chain_path), using the rel_id
            # already present in the adjacency tuple (previously discarded).
            # It does not change which nodes are visited, in what order, or
            # when the walk stops: the cycle guard below still keys only on
            # chain_path, and the depth guard is untouched.
            queue: List[Tuple[int, str, int, List[int], List[int]]] = []
            for next_id, rel_type, rel_id in adj.get(start_id, []):
                queue.append((next_id, rel_type, 1, [start_id, next_id], [rel_id]))

            while queue:
                current, acc_type, depth, path, rel_chain = queue.pop(0)
                if depth >= MAX_DEPTH:
                    continue

                for next_id, rel_type, rel_id in adj.get(current, []):
                    if next_id in path:
                        continue  # No cycles

                    new_type = _derive_type(acc_type, rel_type)
                    new_path = path + [next_id]
                    new_rel_chain = rel_chain + [rel_id]
                    pair = (start_id, next_id)

                    # Only add if not explicit and not already derived
                    if pair not in explicit_pairs and pair not in seen_pairs and start_id != next_id:
                        seen_pairs.add(pair)
                        derived.append({
                            "source_id": start_id,
                            "target_id": next_id,
                            "type": new_type,
                            "chain": new_path,
                            "depth": len(new_path) - 1,
                            # ADR-002-v2 additions.
                            "relationship_chain": new_rel_chain,
                            "rule_id": _rule_id(acc_type, rel_type),
                        })

                    queue.append((next_id, new_type, depth + 1, new_path, new_rel_chain))

        logger.info(
            "Derived relationship computation: %d explicit → %d derived",
            len(relationships), len(derived),
        )
        return derived
