"""Pure prompt assembly for the programme-wizard AI prefill (R1-09).

Moved out of ``ProgrammeSetupService.ai_prefill_programme``
(app/modules/solutions_strategic/v2/services/programme_setup_service.py),
where the caller's free-text description was interpolated directly into the
prompt with no fencing -- outside every AI gate's scan root, since that
module is not under ``app/modules/ai_chat/``. This module has no DB access
and does not import ``ToolExecutor``: it only builds text, and it lives
where ``scripts/check_ai_untrusted_content.py`` (scan root ``app/modules/
ai_chat/``) can see it.

The instruction text and the output-key contract are trusted constants
written by us. The description is untrusted (an enterprise architect's own
free text, which could contain a planted instruction) and is appended only
through ``fence_untrusted``, after ``governed_evidence_rules()`` -- the same
shape ADR 0014 decisions 4 and 5 give retrieved content elsewhere, applied
here by analogy (llm-spec section 12).
"""

from __future__ import annotations

from app.modules.ai_chat.services.architect_persona_charters import (
    fence_untrusted,
    governed_evidence_rules,
)

# The label fence_untrusted wraps the description in ("=== BEGIN <label> ===").
PREFILL_DESCRIPTION_LABEL = "Programme Description"

_INSTRUCTION = """You are helping populate a transformation-programme intake form from a
free-text description written by an enterprise architect.

Extract ONLY what the description below actually supports. If something is not
stated or cannot be confidently inferred, respond with null for that field --
never guess or invent a plausible-sounding value.

Return ONLY a JSON object with exactly these keys:
{{
  "name": string or null (a short programme name, not the whole description),
  "objective": string or null (one or two sentences on the business objective),
  "workstream_type": one of {workstream_types} or null,
  "business_units": array of strings or null (business units in scope),
  "outcome_statement": string or null (the outcome the programme commits to),
  "direction": one of {directions} or null (does the metric increase/decrease/stay the same),
  "metric_name": string or null (the metric that proves the outcome),
  "unit": string or null (unit the metric is measured in),
  "baseline_value": number or null (only if a current/starting value is explicitly stated),
  "target_value": number or null (only if a target value is explicitly stated),
  "target_date": string or null (YYYY-MM-DD, only if a date is explicitly stated or unambiguously computable)
}}"""


def build_prefill_prompt(description: str, *, workstream_types, directions) -> str:
    """Build the full system prompt: trusted instruction and output contract,
    the shared evidence rules, then the caller's description fenced as
    untrusted content. Returns a single string ready to send as the prompt.
    """
    system_prompt = _INSTRUCTION.format(
        workstream_types=list(workstream_types), directions=list(directions)
    )
    system_prompt = system_prompt + governed_evidence_rules()
    description_context = description
    system_prompt = system_prompt + fence_untrusted(PREFILL_DESCRIPTION_LABEL, description_context)
    return system_prompt


__all__ = ["PREFILL_DESCRIPTION_LABEL", "build_prefill_prompt"]
