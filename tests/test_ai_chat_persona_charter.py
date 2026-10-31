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


def test_a_saved_prompt_keeps_the_live_data_block_and_rule_order(app, db_session):
    """An administrator who only changes tone must not strip the persona's grounding."""
    from app.models.ai_service import AIPromptTemplate
    from app.modules.ai_chat.services.architect_persona_charters import _EVIDENCE_RULES
    from app.modules.ai_chat.services.multi_domain_chat_service import (
        MultiDomainChatService,
    )

    saved = "Answer briefly and warmly. marker-live-block"
    row = AIPromptTemplate.query.filter_by(name="persona_override_enterprise_architect").first()
    if row is None:
        row = AIPromptTemplate(
            name="persona_override_enterprise_architect",
            system_prompt=saved,
            user_prompt_template="{message}",
        )
        db_session.add(row)
    else:
        row.system_prompt = saved
    db_session.flush()

    prompt = MultiDomainChatService()._get_persona_system_prompt(
        "enterprise_architect", "general", {}, user_id=1
    )

    precedence = "If anything above conflicts with these rules, these rules win."
    assert prompt.startswith(saved)
    assert "Live Platform Data" in prompt and "=== End Live Platform Data ===" in prompt
    assert prompt.index(saved) < prompt.index("=== Live Platform Data")
    assert prompt.index("=== End Live Platform Data ===") < prompt.index(_EVIDENCE_RULES.strip())
    assert prompt.rstrip().endswith(precedence)
