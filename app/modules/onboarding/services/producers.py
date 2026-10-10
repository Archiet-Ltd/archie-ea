"""Proposal producers for the Review screen (onboarding-redesign-v3 §6).

A producer is a plain function: the organisation's onboarding profile in,
candidate proposals out. No producer writes anything -- the Review screen
and ``proposals.decide()`` are the only path to the organisation's profile.

Today there is one producer, ``from_answers``, built entirely from what the
founder already told us on "Bring your company" (Screen 2), for the cases
where one answer implies something beyond itself -- for example, the stage
selected there implies which controls are typically expected, the same
comparison stage_gaps.py already makes for Screen 4's gap list. The
website-reading producer described in the onboarding redesign (industry,
size, tech stack, customer segment read from the company's own site) is a
separate, not-yet-landed piece of work: the fetcher exists on
feat/company-context-fetcher, but the engine that turns a fetched page into
a proposal does not, so ``from_answers`` never guesses at a field Screen 2
does not actually ask about -- it says so honestly instead (an empty list),
rather than inventing a value.
"""
from __future__ import annotations

from . import stage_gaps

SOURCE = "answers"
SOURCE_LABEL = "From your answers"

_STAGE_LABELS = {
    "pre_revenue": "pre-revenue",
    "early_revenue": "early revenue",
    "growing": "growing",
    "established": "established",
}

# Stage does not determine a funding round, but it is a genuine, honestly
# hedged signal ("usually", "confidence: likely") -- never a certainty.
_FUNDING_STAGE_BY_COMPANY_STAGE = {
    "pre_revenue": ("Pre-seed or bootstrapped", "founders and early believers, before paying customers"),
    "early_revenue": ("Seed", "proving the model with first paying customers"),
    "growing": ("Series A or B", "repeatable sales and a growing team"),
    "established": ("Later-stage, or no longer raising", "multiple teams and formal processes"),
}


def from_answers(org_profile: dict) -> list[dict]:
    """Candidates implied by what was already answered on Screen 2.

    Returns a list of ``{field, value, source, source_label, confidence,
    reason}`` dicts, newest signal first. Produces nothing when the signal
    it needs (the stage) was never answered -- an honest absence, not a
    guess.
    """
    stage = org_profile.get("stage")
    if stage not in _STAGE_LABELS:
        return []

    stage_label = _STAGE_LABELS[stage]
    candidates: list[dict] = []

    funding = _FUNDING_STAGE_BY_COMPANY_STAGE.get(stage)
    if funding:
        value, why = funding
        candidates.append({
            "field": "funding_stage",
            "value": value,
            "source": SOURCE,
            "source_label": SOURCE_LABEL,
            "confidence": "likely",
            "reason": f"Companies at the {stage_label} stage are usually {why}",
        })

    if org_profile.get("region_europe_or_eu_customers"):
        candidates.append({
            "field": "region",
            "value": "Serves customers in Europe / the EU",
            "source": SOURCE,
            "source_label": SOURCE_LABEL,
            "confidence": "certain",
            "reason": "You told us this in Bring your company",
        })

    controls = stage_gaps.applicable_controls(
        stage,
        region_europe_or_eu_customers=bool(org_profile.get("region_europe_or_eu_customers")),
        handles_card_data_directly=bool(org_profile.get("handles_card_data_directly")),
    )
    for control in controls:
        label = stage_gaps.label_for(control["key"])
        condition = control.get("condition")
        if condition == "region_europe_or_eu_customers":
            reason = "You told us you serve customers in the EU"
        elif condition == "handles_card_data_directly":
            reason = "You told us you handle card data directly"
        else:
            reason = f"Typical for companies at the {stage_label} stage"
        candidates.append({
            "field": "compliance_hints",
            "value": label,
            "source": SOURCE,
            "source_label": SOURCE_LABEL,
            "confidence": "likely",
            "reason": reason,
        })

    return candidates
