"""The one sentence a person sees when the assistant cannot answer.

Every customer-visible failure in the chat says this, so the wording cannot drift
between the streamed path, the plain path and the upload and matching routes.
Where to fix a provider is shown to an administrator as a link, never in this text.
"""

ASSISTANT_UNAVAILABLE = (
    "The assistant could not answer just now. "
    "Your question is saved; try again in a moment."
)

# Why a turn could not be answered, in the words a person reads. The server sends the code
# (``reason_code``) with a failed turn; the chat page is given this table once and shows the
# sentence for the code, so a reason has one wording on every path. Each sentence says what to do next.
FAILURE_REASONS = {
    "timeout": "The AI did not answer in time. Press Retry to ask again.",
    "provider_unreachable": (
        "The AI provider refused the request or could not be reached. "
        "Press Retry; if it keeps happening, tell your administrator."
    ),
    "signed_out": "You are signed out. Sign in again, then ask again.",
    "role_not_allowed": "Your role does not allow this. Ask your administrator for access.",
    "too_large": "The request was too large. Shorten your question, then press Retry.",
    "unknown": ASSISTANT_UNAVAILABLE,
}

# Reasons where asking again cannot help until the person or an administrator acts.
FAILURE_REASONS_WITHOUT_RETRY = ("role_not_allowed",)

# Sentences earlier turns saved into a conversation as the assistant's reply. A chat opened again
# recognises a saved reply that is one of these (or one of FAILURE_REASONS) as a failed turn.
SAVED_FAILURE_SENTENCES = (
    "The assistant is busy right now. Wait a minute, then press Retry.",
    "The assistant could not answer this time. Press Retry.",
    "The AI service is busy right now (its free usage limit was reached). "
    "Wait a minute, then press Retry.",
)


def reason_code_for_status(status) -> "str | None":
    """The reason code for an HTTP status the chat was answered with, or None when the status says nothing."""
    return {
        401: "signed_out", 403: "role_not_allowed", 413: "too_large",
        408: "timeout", 504: "timeout", 502: "provider_unreachable", 503: "provider_unreachable",
    }.get(status)


def reason_code_for_text(reason) -> "str | None":
    """The reason code a provider or transport failure text points at, or None when it names none."""
    text = str(reason or "").lower()
    if any(t in text for t in ("timeout", "timed out", "did not respond", "did not answer")):
        return "timeout"
    if any(t in text for t in ("too large", "context length", "context_length", "maximum context", "413")):
        return "too_large"
    if any(t in text for t in ("unauthorized", "401", "invalid api key", "forbidden", "403", "refused",
                               "connection", "unreachable", "502", "503", "bad gateway")):
        return "provider_unreachable"
    return None


def failure_sentence(code) -> str:
    """The sentence for a reason code; a code the table does not know reads as the general one."""
    return FAILURE_REASONS.get(code) or FAILURE_REASONS["unknown"]


def failure_table_for_page() -> dict:
    """What the chat page is given: every reason's sentence, which reasons offer no Retry, and the saved replies
    that mark a failed turn."""
    return {
        "reasons": dict(FAILURE_REASONS),
        "no_retry": list(FAILURE_REASONS_WITHOUT_RETRY),
        "saved": list(SAVED_FAILURE_SENTENCES) + list(FAILURE_REASONS.values()),
    }


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
