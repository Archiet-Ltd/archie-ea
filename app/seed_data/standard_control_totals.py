"""How many controls a published standard has, for the catalogues the product holds only part of.

Only a count is kept here, never control text: the count lets a page say how much of a standard is loaded.
"""

from typing import Optional

# Framework code in the catalogue -> the number of controls the published standard defines.
PUBLISHED_CONTROLS = {
    "ISO - 27001": 93,  # ISO/IEC 27001:2022 Annex A: 37 organizational, 8 people, 14 physical, 34 technological
    "SOC-2": 61,  # AICPA 2017 trust services criteria: 33 common, 3 availability, 2 confidentiality, 5 processing integrity, 18 privacy
    "GDPR": 40,  # Regulation (EU) 2016/679 articles 5-22, 24-39 and 44-49: the articles that place a duty on a controller or processor, not the regulation's published article count
    "NIST-CSF": 134,  # NIST CSF 2.0: 6 functions, 22 categories, 106 subcategories
    "ITIL-4": 34,  # ITIL 4 management practices
    "COBIT-2019": 40,  # COBIT 2019 governance and management objectives
    "TOGAF": 10,  # ADM: preliminary phase, phases A to H and requirements management
    "ARCHIMATE": 7,  # ArchiMate 3.2 layers: strategy, business, application, technology, physical, motivation, implementation and migration
    "SAFE": 6,  # SAFe portfolio Kanban states
}


# Catalogues that hold a chosen part of a standard by design, and what that part is.
SCOPE_NOTES = {
    "GDPR": "Lists the articles that place a duty on a controller or processor, not every article of the regulation.",
}


def scope_note(code: Optional[str]) -> Optional[str]:
    """What a catalogue that is deliberately a part of its standard covers, or None."""
    return SCOPE_NOTES.get(code or "")


def controls_loaded_text(code: Optional[str], loaded: int) -> Optional[str]:
    """"3 of 93 controls loaded" when the catalogue holds part of the standard, None when it is whole or unknown."""
    total = PUBLISHED_CONTROLS.get(code or "")
    if total is None or loaded >= total:
        return None
    return "%d of %d controls loaded" % (loaded, total)
