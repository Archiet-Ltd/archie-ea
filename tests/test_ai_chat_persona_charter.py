"""The governed persona charters must reach the model on the live path.

`architect_persona_charters.py` holds each architect's charter, the six HARD
RULES (evidence, no fabrication, propose-don't-dispose, cite your source...) and
a per-persona live-data block. CLAUDE.md names this as the AI assistant's
governance layer.

It was reachable only through `MultiDomainChatService._get_persona_system_prompt`
← `process_message`. The live path is `POST /ai-chat/message` →
`AgentRunner.run()`, which never calls it. `AgentRunner`'s entire use of the
persona was one line:

    persona_note = f"\\nYou are operating as: {persona.replace('_',' ').title()}.\\n"

Eight words. Selecting "AI Data Architect" instead of "CIO" changed the prompt by
a job title and nothing else — the assistant was not governed by its charter, and
the HARD RULES that forbid fabricating application names and counts were never in
context.
"""

import pytest

from app.modules.ai_chat.services.architect_persona_charters import ARCHITECT_PERSONAS


@pytest.mark.parametrize("persona", ARCHITECT_PERSONAS)
def test_a_governed_persona_gets_its_charter(app, persona):
    """Each of the five governed architects must carry its charter."""
    from app.modules.ai_chat.services.agent_runner import AgentRunner

    with app.app_context():
        prompt = AgentRunner(user_id=1)._build_system_prompt(
            domain="general", context=None, persona=persona
        )

    assert "HARD RULES (non-negotiable)" in prompt, (
        f"{persona} did not receive the HARD RULES. The assistant is not governed "
        f"by its charter — the rules forbidding invented application names, counts "
        f"and dates are not in the prompt."
    )
    assert "Live Platform Data" in prompt, (
        f"{persona} received a charter with no live-data block, so the rule "
        f"'every number MUST come from the Live Platform Data section' points at "
        f"nothing."
    )


def test_the_charter_is_persona_specific(app):
    """Two different architects must not receive the same prompt.

    The defect this guards is subtle: wiring in *a* charter for every persona
    would satisfy the test above while making the picker decorative again.
    """
    from app.modules.ai_chat.services.agent_runner import AgentRunner

    with app.app_context():
        runner = AgentRunner(user_id=1)
        ea = runner._build_system_prompt(domain="general", context=None,
                                         persona="enterprise_architect")
        da = runner._build_system_prompt(domain="general", context=None,
                                         persona="data_architect")

    assert ea != da, "every architect received an identical prompt"
    assert "landscape steward" in ea, "the enterprise architect's own charter is missing"
    assert "landscape steward" not in da, (
        "the data architect received the enterprise architect's charter"
    )


def test_an_ungoverned_persona_still_gets_its_label(app):
    """11 personas are selectable; only 5 are governed. The rest must not break.

    `build_architect_prompt` returns None for a non-architect, so the fallback
    has to survive that or selecting "CIO" would produce a prompt with no
    persona information at all.
    """
    from app.modules.ai_chat.services.agent_runner import AgentRunner

    with app.app_context():
        prompt = AgentRunner(user_id=1)._build_system_prompt(
            domain="general", context=None, persona="product_analyst"
        )

    assert "Product" in prompt or "product" in prompt, "an ungoverned persona lost its label entirely"


def test_no_persona_is_unchanged_from_the_old_eight_word_note(app):
    """Guards the regression directly: EVERY persona must get more than a title.

    This test used to prove charter injection by comparing a governed persona
    against an ungoverned one. That control group no longer exists: P-01 found
    six of the twelve advertised personas had no charter at all and fell through
    to a generic fallback whose scaffold was byte-identical across personas —
    which is exactly why three maximally different personas returned the same
    signature sentence verbatim. All twelve are now governed, so a
    governed-vs-ungoverned length comparison compares two governed prompts and
    fails for the right reason.

    The property that actually matters is asserted instead: every persona's
    prompt must carry its OWN charter text, and two different personas must not
    produce the same prompt.
    """
    from app.modules.ai_chat.services.agent_runner import AgentRunner
    from app.modules.ai_chat.services.architect_persona_charters import ARCHITECT_PERSONAS

    with app.app_context():
        runner = AgentRunner(user_id=1)
        prompts = {
            name: runner._build_system_prompt(domain="general", context=None, persona=name)
            for name in ARCHITECT_PERSONAS
        }

    for name, prompt in prompts.items():
        assert len(prompt) > 1000, f"{name}'s prompt is too short to carry a charter"

    # Distinctness is the real regression guard: P-01's defect was several
    # personas sharing one template with only the role name substituted.
    distinct = {p for p in prompts.values()}
    assert len(distinct) == len(prompts), (
        "two or more personas produced an identical system prompt — this is the "
        "P-01 defect, where persona selection changed the label and not the expertise"
    )


def test_every_page_the_charters_recommend_resolves(app):
    """Every internal path the charters tell the assistant to recommend must be a real route.

    The persona charters, and the shared evidence rules appended to every
    prompt, name Entelim pages the assistant may send users to (for example
    the audit log and the import page). Each extracted path must resolve in
    the Flask url_map so the assistant can never cite a 404.
    """
    import re

    from app.modules.ai_chat.services.architect_persona_charters import (
        CHARTERS,
        _EVIDENCE_RULES,
    )

    cue = re.compile(r"(?:e\.g\.|see|point to|action on|browse at)[^.;]*", re.IGNORECASE)
    path = re.compile(r"/[a-z0-9][\w-]*(?:/[\w-]+)*")
    recommended = []
    for source in list(CHARTERS.values()) + [_EVIDENCE_RULES]:
        for match in cue.finditer(source):
            recommended.extend(path.findall(match.group(0)))

    rules = {rule.rule.rstrip("/") for rule in app.url_map.iter_rules()}
    for page in sorted(set(recommended)):
        assert page.rstrip("/") in rules, (
            f"{page!r} is recommended by a persona charter but is not a "
            f"registered route — the assistant would send users to a 404"
        )
