"""The one sentence a person sees when the assistant cannot answer.

Every customer-visible failure in the chat says this, so the wording cannot drift
between the streamed path, the plain path and the upload and matching routes.
Where to fix a provider is shown to an administrator as a link, never in this text.
"""

ASSISTANT_UNAVAILABLE = (
    "The assistant could not answer just now. "
    "Your question is saved; try again in a moment."
)

# The sentence a person reads when an AI control cannot run because no
# organisation has configured a provider for it yet -- a permanent,
# administrative gap, not the transient failure ASSISTANT_UNAVAILABLE
# describes above. Every route and widget gated on
# FeatureFlagService.is_ai_enabled() shows this, through ai_unavailable_notice
# / ai_unavailable_message, never its own wording, so a reader never sees a
# code name in quotes or the words "LLM provider".
FEATURE_INTROS = {
    "chat": "The assistant is not set up for your organisation yet.",
    "analysis": "AI analysis is not set up for your organisation yet.",
    "import": "AI import is not set up for your organisation yet.",
    "impact": "AI impact analysis is not set up for your organisation yet.",
    "deduplication": "AI duplicate detection is not set up for your organisation yet.",
    "suggestions": "AI suggestions are not set up for your organisation yet.",
    "entity_matching": "Entity matching is not set up for your organisation yet.",
}
_DEFAULT_FEATURE_INTRO = "This AI feature is not set up for your organisation yet."

ADMIN_AI_SETTINGS_PATH = "/admin/api-settings"
ADMIN_AI_SETTINGS_LINK_TEXT = "Settings, AI and models"


def ai_unavailable_notice(feature: str, is_admin: bool) -> dict:
    """The plain-language notice for one AI control with no provider configured.

    Returns the intro clause, the follow-on clause (which differs for an
    organisation administrator, who can act, versus anyone else, who asks
    one to), the combined plain sentence, and — for an administrator only —
    the link text and href to the page that fixes it.
    """
    intro = FEATURE_INTROS.get(feature, _DEFAULT_FEATURE_INTRO)
    if is_admin:
        description = (
            "An administrator can add an AI key under "
            + ADMIN_AI_SETTINGS_LINK_TEXT + "."
        )
        cta_text, cta_href = ADMIN_AI_SETTINGS_LINK_TEXT, ADMIN_AI_SETTINGS_PATH
    else:
        description = (
            "Ask your administrator to add an AI key under "
            + ADMIN_AI_SETTINGS_LINK_TEXT + "."
        )
        cta_text, cta_href = None, None
    return {
        "intro": intro,
        "description": description,
        "cta_text": cta_text,
        "cta_href": cta_href,
        "sentence": intro + " " + description,
    }


def ai_unavailable_message(feature: str, is_admin: bool) -> str:
    """The plain sentence alone, for a JSON ``message``/``error`` field or any
    caller that only wants text, never markup."""
    return ai_unavailable_notice(feature, is_admin)["sentence"]
