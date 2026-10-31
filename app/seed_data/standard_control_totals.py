"""How many controls a published standard has, for the catalogues the product holds only part of.

Only a count is kept here, never control text: the count lets a page say how much of a standard is loaded.
"""

from typing import Optional

# Framework code in the catalogue -> the number of controls the published standard defines.
PUBLISHED_CONTROLS = {
    "ISO - 27001": 93,  # ISO/IEC 27001:2022 Annex A
}


def controls_loaded_text(code: Optional[str], loaded: int) -> Optional[str]:
    """"3 of 93 controls loaded" when the catalogue holds part of the standard, None when it is whole or unknown."""
    total = PUBLISHED_CONTROLS.get(code or "")
    if total is None or loaded >= total:
        return None
    return "%d of %d controls loaded" % (loaded, total)
