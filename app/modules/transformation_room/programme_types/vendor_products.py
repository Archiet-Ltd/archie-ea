"""Known vendor product names, for the template validator's rule V6/rule 8
(sdd.md 4.4 item 8): a type marked ``vendor_specific: false`` must not name a
specific vendor's product.

Exported as a module constant so the R3 validator (llm-spec section 12, rule
V6) imports this list rather than keeping a private copy -- one accessor for
"what counts as a vendor product name" (ADR 0008 rule 3).

Starting set: the vendor keys ``StrategicInitiative.vendor_key`` already
names in its own column comment (``app/models/strategic.py``) plus the
product names the vendor-template seed command curates
(``app/commands/seed_vendor_archimate_templates.py``). Grow this list by
adding a name, not by inventing a scoring rule -- the check is a literal,
case-insensitive substring match.
"""

from __future__ import annotations

VENDOR_PRODUCT_NAMES: tuple[str, ...] = (
    "SAP",
    "S/4HANA",
    "S4HANA",
    "Salesforce",
    "Microsoft Dynamics",
    "Microsoft Power Platform",
)

__all__ = ["VENDOR_PRODUCT_NAMES"]
