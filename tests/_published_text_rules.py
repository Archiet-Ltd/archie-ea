"""What no recording text, caption, transcript or share manifest may contain: the one place these rules are written.

Words a person reading a shared recording should never meet are the ones that say how the product was built
rather than what it does, the internal record codes of the build, and the placeholder-looking names of
demonstration people (a first name followed by a job word or a joined-up job title, instead of a surname)."""

from __future__ import annotations

import re

BUILD_PROCESS_WORDS = re.compile(r"claude|codex|kilo|aider|orchestrator|agent|R1-B|TB-\d|PB-\d", re.I)

# a first name then a surname that is really a job or a team: "Riley Logistics" or "Drew DataAnalyst"
PLACEHOLDER_PERSON_NAME = re.compile(
    r"\b[A-Z][a-z]+ (?:[A-Z][a-z]+[A-Z]\w*|[A-Z]{2,}[a-z]\w*|Calibrator|Logistics|Inventory|Compliance|Procurement|"
    r"Production|Quality|Support|Admin|Analyst)\b")

DATES = re.compile(r"\bQ[1-4]\s*(?:20)?\d\d\b|\bwave\s*\d\b|\b20(?:2[6-9]|3\d)\b|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d", re.I)
