"""The one place that says what a capability's type is.

A capability is strategic, operational or supporting. The judgment lives in
``unified_capabilities.capability_type`` and nowhere else; every view that groups or
colours by it reads that column, and every writer goes through here.

Where nobody has made the judgment yet, a capability gets a starting value from its
category and its level (``default_capability_type``). The same rule is written once, as
data below, and is used both by Python and, via ``default_type_sql``, by the projection and
the backfill, so the two cannot drift apart.
"""
from __future__ import annotations

from typing import Optional

STRATEGIC = "strategic"
OPERATIONAL = "operational"
SUPPORTING = "supporting"

CAPABILITY_TYPES = (STRATEGIC, OPERATIONAL, SUPPORTING)

CAPABILITY_TYPE_LABELS = {
    STRATEGIC: "Strategic",
    OPERATIONAL: "Operational",
    SUPPORTING: "Supporting",
}

# Plain-language meaning, shown as help text wherever a person sets the type.
CAPABILITY_TYPE_HELP = {
    STRATEGIC: "Differentiates the company. Invest, build and measure it.",
    OPERATIONAL: "Runs the company day to day. Keep it reliable and standardised.",
    SUPPORTING: "Every company needs it. Buy it and commoditise it.",
}

# Starting rule, used only when no one has set a type:
#   1. a recognised category decides (core and differentiating capabilities are strategic);
#   2. otherwise the level decides: top-level capabilities are strategic, second-level
#      operational, anything deeper supporting.
# Whether a capability has linked applications is deliberately not part of the rule: a
# capability nobody has mapped yet says nothing about how much it matters.
CATEGORY_DEFAULTS = {
    "core": STRATEGIC,
    "differentiating": STRATEGIC,
    "strategic": STRATEGIC,
    "operations": OPERATIONAL,
    "operational": OPERATIONAL,
    "supporting": SUPPORTING,
    "support": SUPPORTING,
    "enabling": SUPPORTING,
}
LEVEL_DEFAULTS = {1: STRATEGIC, 2: OPERATIONAL}
DEEPER_LEVEL_DEFAULT = SUPPORTING


def normalise_capability_type(value) -> Optional[str]:
    """Return the canonical type for ``value``, or None when it is not one."""
    text = str(value or "").strip().lower()
    return text if text in CAPABILITY_TYPES else None


def default_capability_type(category=None, level=None) -> str:
    by_category = CATEGORY_DEFAULTS.get(str(category or "").strip().lower())
    if by_category:
        return by_category
    try:
        return LEVEL_DEFAULTS.get(int(level), DEEPER_LEVEL_DEFAULT)
    except (TypeError, ValueError):
        return DEEPER_LEVEL_DEFAULT


def default_type_sql(category_column: str, level_column: str) -> str:
    """The starting rule as a SQL CASE over two columns (trusted identifiers only)."""
    whens = "".join(
        f"WHEN '{key}' THEN '{value}' "
        for key, value in CATEGORY_DEFAULTS.items()
    )
    level_whens = "".join(
        f"WHEN {key} THEN '{value}' " for key, value in LEVEL_DEFAULTS.items()
    )
    return (
        f"COALESCE(CASE lower(trim({category_column})) {whens}END, "
        f"CASE {level_column} {level_whens}ELSE '{DEEPER_LEVEL_DEFAULT}' END)"
    )


BACKFILL_SQL = (
    "UPDATE unified_capabilities SET capability_type = "
    + default_type_sql("category", "level")
    + " WHERE capability_type IS NULL OR trim(capability_type) = ''"
)


def backfill_capability_types(connection) -> int:
    """Give every untyped capability its starting type. Never changes a typed one."""
    from sqlalchemy import text

    return connection.execute(text(BACKFILL_SQL)).rowcount


def set_capability_type(capability, value) -> str:
    """Set the type on a UnifiedCapability; raises ValueError for anything else."""
    chosen = normalise_capability_type(value)
    if chosen is None:
        raise ValueError("Choose strategic, operational or supporting.")
    capability.capability_type = chosen
    return chosen
