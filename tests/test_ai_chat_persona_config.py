"""A persona's default scope must point at the context built for it.

`domain` is the only request parameter that reliably changes what the model is
shown — it selects one of nine context loaders. `persona` selects the charter.
They are orthogonal, which is why the redesign keeps scope visible rather than
deriving it and hiding it.

That only works if the defaults are right. They were not.
"""

import pytest

from app.modules.ai_chat.services.multi_domain_chat_service import (
    MultiDomainChatService,
    PERSONA_CONFIGS,
)


ROLE_DEFAULT_PERSONAS = {
    "solution_architect": "solutions_architect",
    "enterprise_architect": "enterprise_architect",
    "business_architect": "business_architect",
    "arb_member": "arb_member",
    "portfolio_manager": "portfolio_manager",
    "cto": "cio",
    "procurement": "procurement",
    "application_manager": "application_manager",
    "platform_admin": "platform_admin",
    # Promoted from charter-only on 31 Aug 2026; each gets its own charter
    # rather than falling back to the enterprise_architect generalist,
    # which is the whole argument for promoting them.
    "security_architect": "security_architect",
    "data_architect": "data_architect",
    # R1-B36 (TB-0146), 2026-10-05: promoted from unassignable to assignable.
    # None gets a dedicated charter in this PR -- mapped to the closest
    # existing persona's voice; a real charter per persona is follow-up work.
    "finance": "procurement",
    "compliance": "security_architect",
    "risk": "enterprise_architect",
    # NOT platform_admin: _platform_admin_context's last_import() is not
    # organisation-scoped (see 6 Oct 2026 review fix).
    "operations": "enterprise_architect",
    "non_technical_owner": "application_manager",
}


def test_every_supported_role_has_a_selectable_governed_chat_default():
    """The persisted enterprise role must resolve to a real picker persona.

    Before persona unification, four supported roles had charters but no picker
    configuration, while every page silently started as Enterprise Architect.
    """
    from app.models.user import VALID_ROLES
    from app.modules.ai_chat.services.architect_persona_charters import (
        ARCHITECT_PERSONAS,
        PERSONA_ALIASES,
        ROLE_DEFAULT_PERSONAS as actual_defaults,
    )

    assert actual_defaults == ROLE_DEFAULT_PERSONAS
    assert set(actual_defaults) == set(VALID_ROLES)
    assert set(actual_defaults.values()).issubset(PERSONA_CONFIGS)
    assert {
        PERSONA_ALIASES.get(persona, persona)
        for persona in actual_defaults.values()
    }.issubset(ARCHITECT_PERSONAS)


def test_persona_categories_cover_every_selectable_persona():
    """A configured role cannot be hidden by a partial category response."""
    categories = MultiDomainChatService.get_available_personas(None)["categories"]
    categorized = {
        persona
        for personas in categories.values()
        for persona in personas
    }
    assert categorized == set(PERSONA_CONFIGS)


def test_the_data_architect_defaults_to_the_data_architecture_context():
    """It defaulted to `architecture`, so it never loaded its own loader.

    `_load_data_architecture_context` exists and was unreachable for the persona
    named after it: selecting "AI Data Architect" loaded the generic architecture
    context instead.
    """
    assert PERSONA_CONFIGS["data_architect"]["default_domain"] == "data_architecture"


@pytest.mark.parametrize("persona,cfg", sorted(PERSONA_CONFIGS.items()))
def test_every_persona_defaults_to_a_domain_that_exists(persona, cfg, app):
    """A default naming a domain with no loader silently falls through to general."""
    default = cfg.get("default_domain")
    assert default, f"{persona} declares no default_domain"

    with app.app_context():
        svc = MultiDomainChatService()
        result = svc.get_domain_context(default, {})
    assert result.get("success"), (
        f"{persona} defaults to domain '{default}', which did not load: "
        f"{result.get('error')}"
    )


def test_the_capability_architect_is_reachable():
    """It has a config entry AND dedicated prompts, and must be categorized.

    capability_architect_prompts.py is imported for it at
    multi_domain_chat_service.py:3337, so omitting it from the categories
    would make a working persona impossible to choose in the dynamic picker.
    """
    assert "capability_architect" in PERSONA_CONFIGS
    categories = MultiDomainChatService.get_available_personas(None)["categories"]
    assert "capability_architect" in categories["architects"], (
        "capability_architect is configured, has its own prompt module, and "
        "cannot be chosen"
    )


def test_the_compliance_and_data_architecture_scopes_are_offered():
    """Both exist server-side with a loader and appeared in no UI.

    `compliance` is the ARB's question — "verify this against our architecture
    principles" — and was the most valuable domain nobody could select.
    """
    from pathlib import Path

    tpl = (Path(__file__).resolve().parents[1]
           / "app/templates/ai_chat/index.html").read_text(encoding="utf-8")
    for domain in ("compliance", "data_architecture"):
        assert 'value="%s"' % domain in tpl, (
            f"the {domain} scope has a server-side loader and no way to reach it"
        )


def test_the_dead_template_selector_is_gone():
    """template_name is validated, sanitised, then discarded.

    chat_core.py bounds it to 100 chars and runs sanitize_html over it; nothing
    reads it afterwards and AgentRunner.run() has no such parameter. The
    dropdown, and the AIPromptTemplate query that filled it on every page load,
    affected no answer ever produced.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    tpl = (root / "app/templates/ai_chat/index.html").read_text(encoding="utf-8")
    assert 'id="template-selector"' not in tpl, "the inert template selector is still rendered"

    # Check executable lines only. A comment explaining why the query was removed
    # legitimately names it, and a substring match over the whole file would
    # flag that prose — the same trap the design-tokens gate falls into.
    views = (root / "app/modules/ai_chat/routes/chat_views.py").read_text(encoding="utf-8")
    code = [
        line for line in views.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    offenders = [line.strip() for line in code if "AIPromptTemplate" in line]
    assert not offenders, (
        "the page still queries prompt templates for a control that no longer "
        "exists: %s" % offenders
    )
