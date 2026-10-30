"""
AgentRunner: ReAct loop for AI Chat tool use.

Flow per turn:
  1. Build system prompt (agent framing + live domain context)
  2. Call LLM with tool schemas
  3. If response has tool_calls → execute each → feed results back → repeat
  4. If response is text-only → return final response
  5. Cap at MAX_ITERATIONS to prevent runaway chains

Supports Anthropic (Claude) and OpenAI providers.
Tools marked 'approve' tier are queued for user confirmation — not executed.
"""

from app.modules.ai_chat.tools.executor import ToolCall
import json
import logging
from typing import Callable, Optional
from app.modules.ai_chat.services.ai_hallucination_detector import (
    Grounding,
    find_uncited_claims,
    organisation_names_in_text,
)
from app.modules.intelligence.services.answer_envelope import (
    as_whole_number,
    classify_estate_question,
)
from app.utils.tracing import traced

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 8

# Which tools write. Derived in tools/registry.py by reading each implementation
# for db.session.add/commit/delete, not from tool names.
try:
    from app.modules.ai_chat.tools.registry import mutating_tool_names as _mutating_tool_names
    _MUTATING_TOOLS = _mutating_tool_names()
except Exception:  # registry import failure must not take the turn with it
    _MUTATING_TOOLS = frozenset()

# Agent-mode system prompt prefix injected before domain context.
_AGENT_PREFIX = """You are an Enterprise Architecture Copilot with DIRECT WRITE ACCESS
to the architecture repository. You do not give advice for humans to act on — you act.

WHAT YOU CAN DO (the main tools; the list offered to you in this conversation is authoritative):
Phase A — Motivation: create_driver, create_goal, create_constraint
Phase B-D — Architecture: create_requirement, link_application_to_solution, link_vendor_product,
             link_capability_to_solution, create_archimate_element, create_archimate_relationship
Phase D — ArchiMate: run_inference_engine, diagnose_chain, explain_element, simulate_impact
Answers: answer (what breaks if X fails, what breaks if these fail together, which risks
         reach X): the same rows, citations and address the Ask and Impact pages show
Phase E — Options: create_option, mark_option_recommended
Phase G — Governance: submit_for_arb_review (requires confirmation)
Solution state: get_solution_summary, get_completeness_score, update_solution_fields,
                update_solution_phase, search_archimate_elements, find_applications,
                query_capability_gaps
Portfolio: update_application_status (requires confirmation)
Blueprint: generate_blueprint_narrative (requires confirmation)

HOW TO OPERATE:
1. When the user asks to create, link, map, update, or submit — CALL THE TOOL. Do not describe it.
2. If an entity name is ambiguous, ask the user to clarify BEFORE calling a write tool.
3. After each successful tool call, confirm what was done in plain English.
4. For 'approve' tier tools, state exactly what you will do and let the confirmation card handle it.
5. Never fabricate IDs. Always use names — the system resolves them to IDs.
6. If solution_id is in the ACTIVE SOLUTION CONTEXT below, pass it to all tools automatically.

GROUNDING — this is a system of record, so an unverifiable answer is worse than none:
7. State a fact about the portfolio only if a tool returned it or it appears in the
   context below. If neither, say you would need to look it up, and look it up.
7a. For any question about what depends on, breaks with, or is owned in the estate, CALL
   the answer tool (or simulate_impact) before replying. Do not answer it from memory.
7b. When you state a recorded fact about a record of the estate, cite it with a marker
   [ref:<id>] where <id> is an element id or the answer address a tool returned this
   turn. State counts as the tool returned them.
7c. General architecture guidance (patterns, TOGAF, ArchiMate concepts) is fine; keep it
   apart from statements about this estate.
7d. What the user asserts, or an earlier turn said, is not a fact on record: look it up.
8. Refer to records by the exact name the tool returned. The records you read are
   attached to your reply as sources automatically — you do not need to write links,
   but the names must match or the citation will not line up with what you said.
9. Read the coverage fields on every tool result. When a result says it is showing
   N of M, report M as the total and say the list is partial. Never present the
   number of rows you were shown as the number that exists.
10. When context arrives with an "_omitted" key, those parts were withheld for size
    and are NOT empty — retrieve them with a tool before drawing a conclusion.

LIVE ARCHITECTURE CONTEXT:
"""


def is_pause(error) -> bool:
    """True when the agent's error is the platform's pause: every provider in the order at its ceiling.
    The pause is the whole reply, so the routes send it as the answer and no error beside it."""
    return "answers are paused" in (str(error or "")).lower()


def sanitize_agent_error(reason) -> "Optional[str]":
    """Turn an upstream failure string into something safe to send to a browser.

    The raw reason is the provider's own error body. OpenRouter's 402 includes
    the *provider account's* ``user_id``; others echo request ids, key
    fragments, org names and internal URLs. That string was being returned
    verbatim as ``agent_error`` on POST /ai-chat/message, so provider internals
    reached every chat user.

    The categories are the same ones ``AgentRunner._fallback`` already
    distinguishes to choose its user-facing copy, so the client learns exactly
    as much as the copy already tells it — and nothing more. An HTTP status is
    included when one is present because it is the operator's handle on the
    failure and carries no account detail. The full reason stays in the server
    log.
    """
    import re as _re

    if not reason:
        return None
    text = str(reason)
    lowered = text.lower()

    status = ""
    m = _re.search(r"\b(4\d\d|5\d\d)\b", text)
    if m:
        status = f" (provider HTTP {m.group(1)})"

    if "answers are paused" in lowered:
        return "Answers are paused: every provider in the order is at its ceiling."
    if any(t in lowered for t in ("no api key", "not configured", "no provider", "no keys")):
        return "No LLM provider is configured."
    if any(
        t in lowered
        for t in (
            "quota", "insufficient", "rate limit", "429", "billing",
            "exceeded", "credit", "402",
        )
    ):
        return f"The LLM provider rejected the request: rate limit or quota{status}."
    if any(t in lowered for t in ("timeout", "timed out")):
        return f"The LLM provider did not respond in time{status}."
    if any(t in lowered for t in ("unauthorized", "401", "invalid api key", "forbidden", "403")):
        return f"The LLM provider rejected the credentials{status}."
    return f"The LLM request failed{status}."


# Words in a provider's refusal that mean "this one is spent, ask another".
_QUOTA_PHRASES = ("quota", "insufficient", "rate limit", "429", "billing", "exceeded")

_PROVIDER_BASE_URLS = {
    "openrouter": "https://openrouter.ai/api/v1",
    "nvidia": "https://integrate.api.nvidia.com/v1",
}
# Providers whose tool-calling the loop can drive.
_TOOL_PROVIDERS = ("anthropic", "openai", "openrouter", "nvidia")
_FREE_PROVIDERS = ("nvidia", "openrouter")
_PROVIDER_PRIORITY = ("openai", "anthropic", "gemini", "openrouter", "nvidia", "azure", "huggingface")


def _error_status(exc) -> Optional[int]:
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    return status if isinstance(status, int) else None


def is_retryable_llm_error(exc) -> bool:
    """True when another provider or model may answer what this one refused.

    Rate limits, quota, any server error, timeouts, dropped connections and a
    model or function the provider does not have. Anything else (a malformed
    request, a rejected key) would fail the same way elsewhere.
    """
    status = _error_status(exc)
    text = str(exc).lower()
    if "answers are paused" in text:
        return True  # this provider is at its ceiling; the next in the chain may still answer
    if status is not None:
        if status == 429 or status >= 500:
            return True
        if status == 404:
            return "not found" in text or "model" in text
        if status in (400, 401, 403, 422):
            return any(t in text for t in _QUOTA_PHRASES[:-1])
    names = {c.__name__.lower() for c in type(exc).__mro__}
    if any("timeout" in n or "connection" in n for n in names):
        return True
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    return any(t in text for t in _QUOTA_PHRASES) or "timed out" in text


def _base_url_of(provider: str) -> Optional[str]:
    """The address of an OpenAI-compatible third-party provider, honouring its configured override."""
    if provider not in _PROVIDER_BASE_URLS:
        return None
    from app.modules.ai_chat.services.llm_service_impl import LLMService

    return LLMService.base_url_for(provider) or _PROVIDER_BASE_URLS.get(provider)


def _models_of(setting_model: Optional[str]) -> list:
    """A provider's configured models: the first, then its comma-separated backups."""
    return [m.strip() for m in (setting_model or "").split(",") if m.strip()]


def build_provider_chain(first_provider: str, first_model: str, pin_first: bool = False) -> list:
    """Ordered (provider, model, key, base_url) entries to try for one turn.

    Built from the enabled providers only, never adds a paid one. The order is
    the ``AI_PROVIDER_ORDER`` setting (comma-separated provider names) when set;
    otherwise the providers the organisation has its own key for come first
    (paid ones in priority order), then the free ones, NVIDIA before OpenRouter
    because the shared OpenRouter allowance runs out daily. An organisation
    without a key of its own starts on the free providers. ``pin_first`` keeps
    the caller's explicit choice at the front.
    A provider contributes each of its models in turn, each with every key.
    ``AI_PROVIDER_BASE_URL_<PROVIDER>`` points a provider at a gateway or mirror
    instead of its public address.
    """
    import os

    from app.models.models import APISettings
    from app.modules.ai_chat.services.llm_service_impl import LLMService
    from app.modules.ai_chat.services.model_defaults import default_model_for

    rows = {}
    try:
        for row in APISettings.query.filter_by(enabled=True).all():
            rows.setdefault(row.provider, row)
    except Exception:
        logger.warning("AgentRunner: could not read provider settings", exc_info=True)

    own = [p for p in _PROVIDER_PRIORITY if p in _TOOL_PROVIDERS and p in rows]
    # The platform's own keys count only for the providers in the configured order, each within its ceiling.
    platform = [
        p for p in LLMService.provider_order()
        if p in _TOOL_PROVIDERS and p not in rows and os.getenv(p.upper() + "_API_KEY") and LLMService.has_headroom(p)
    ]
    names = own + [p for p in platform if p not in own]
    has_paid = any(p in rows for p in ("anthropic", "openai", "azure", "gemini"))

    configured = (
        [p for p in LLMService.provider_order()]
        if (os.getenv("LLM_PROVIDER_ORDER") or os.getenv("AI_PROVIDER_ORDER")) else []
    )
    if configured:
        order = [p for p in configured if p in names] + [p for p in names if p not in configured]
    elif not has_paid:
        order = [p for p in _FREE_PROVIDERS if p in names] + [p for p in names if p not in _FREE_PROVIDERS]
    else:
        # The organisation's own paid key first, whatever the caller defaulted to;
        # the free providers only after it, and only if it fails.
        own = [p for p in names if p not in _FREE_PROVIDERS and p in rows]
        rest = [p for p in _FREE_PROVIDERS if p in names] + [p for p in names if p not in own and p not in _FREE_PROVIDERS]
        order = own + rest
    if pin_first:
        order = [first_provider] + [p for p in order if p != first_provider]
    if first_provider not in order:
        order.append(first_provider)

    chain = []
    for provider in order:
        row = rows.get(provider)
        models = _models_of(row.default_model if row else None)
        if provider == first_provider and first_model:
            models = [first_model] + [m for m in models if m != first_model]
        if not models:
            fallback = default_model_for(provider)
            models = [fallback] if fallback else []
        try:
            keys = LLMService._get_all_api_keys(provider)
        except Exception:
            keys = []
        for model in models:
            for key in keys:
                chain.append((provider, model, key, os.getenv("AI_PROVIDER_BASE_URL_" + provider.upper()) or _base_url_of(provider)))
    return chain


class AgentRunner:
    """
    Orchestrates the LLM tool-use loop for a single user turn.

    Parameters
    ----------
    user_id : int
        The current user's DB ID (used for ownership/audit on writes).
    yield_event : callable, optional
        SSE event callback: yield_event({"type": "...", "data": ...}).
        If None, events are discarded (non-streaming mode).
    auto_execute : bool, default False
        The user's write-approval preference (session flag `agent_auto_execute`,
        toggled via POST /ai-chat/session/toggle-auto-execute). Read from
        flask.session by the CALLER, not here: the streaming chat route runs
        this class from a background thread with only an app context re-
        established (see chat_core.py's run_agent()), so flask.session is not
        reliably available at run() time. Default False means a fresh session,
        or a caller that forgets to pass it, queues writes rather than firing
        them - the safe default for a write-approval gate.
    """

    def __init__(
        self,
        user_id: int,
        yield_event: Optional[Callable] = None,
        auto_execute: bool = False,
        chat_session_id: Optional[str] = None,
    ):
        self.user_id = user_id
        self._emit = yield_event or (lambda _e: None)
        self.auto_execute = bool(auto_execute)
        # ARCH-020: the conversation this run belongs to. Threaded onto every
        # AIChatCRUDApproval this run queues (_queue_approval) so an approval
        # is never orphaned from the chat that raised it. A unique id per call
        # to run() identifies the specific turn within that session.
        self.chat_session_id = chat_session_id
        self._turn_id: Optional[str] = None
        # What this turn may state: tool results, the context handed to the
        # model, the conversation so far and the user's own words. The citation
        # gate in _finalize refuses an answer that states a fact about the
        # estate that none of it supports.
        self._grounding = Grounding()
        self._context_text = ""
        self._estate_question = None
        self._nudged = False
        self._gate_cache = None
        self._context_struct = None
        self._question_names = set()
        self._gate_mode_now = self._gate_mode()
        # Tokens of the model call in flight, held back until the gate has
        # judged the text so a refused answer is never partly shown.
        self._held_tokens: Optional[list] = None
        self._withheld_claims: list = []   # sentences refused (enforce mode)
        self._would_withhold: list = []    # sentences logged only (log mode)

    GATE_MODES = ("log", "enforce", "off")

    @staticmethod
    def _gate_mode() -> str:
        """ASSISTANT_CITATION_GATE_MODE: "log" (default: the answer is shown
        unchanged and an answer the gate would refuse is logged), "enforce"
        (the answer is replaced by "Not recorded") or "off". One place."""
        import os

        value = None
        try:
            from flask import current_app, has_app_context

            if has_app_context():
                value = current_app.config.get("ASSISTANT_CITATION_GATE_MODE")
        except Exception:  # pragma: no cover - no application at all
            value = None
        if value is None:
            value = os.environ.get("ASSISTANT_CITATION_GATE_MODE", "log")
        value = str(value or "log").strip().lower()
        return value if value in AgentRunner.GATE_MODES else "log"

    def _emit_token(self, text: str) -> None:
        """Stream a token, or hold it while the citation gate has not yet seen
        the text it belongs to (see ``_release_held_tokens``)."""
        if self._held_tokens is not None:
            self._held_tokens.append(text)
        else:
            self._emit({"type": "token", "text": text})

    def _release_held_tokens(self, release: bool) -> None:
        held, self._held_tokens = self._held_tokens, None
        if release:
            for text in held or []:
                self._emit({"type": "token", "text": text})

    @staticmethod
    def _inject_trusted_tool_context(
        tool_name: str, arguments: dict, trusted_workspace_id: Optional[int]
    ) -> dict:
        """Replace model-provided governance identity with server context."""
        trusted = dict(arguments or {})
        if tool_name == "submit_for_arb_review":
            trusted.pop("workspace_id", None)
            trusted.pop("workflow_type", None)
            trusted.pop("phase", None)
            trusted.pop("cost_source", None)
            trusted.pop("human_reviewed", None)
            trusted.pop("direct_route_evidence", None)
            trusted.pop("assertions", None)
            if trusted_workspace_id is not None:
                trusted["workspace_id"] = trusted_workspace_id
        return trusted

    # ------------------------------------------------------------------ #
    # Public entry point                                                   #
    # ------------------------------------------------------------------ #

    # How much prior conversation to replay. Turns, not messages: one turn is a
    # user message plus its assistant reply.
    MAX_HISTORY_TURNS = 10
    MAX_HISTORY_CHARS = 24000

    # Budget for the serialised domain context block.
    MAX_CONTEXT_CHARS = 6000

    # ------------------------------------------------------------------ #
    # Citations
    # ------------------------------------------------------------------ #
    # Which read tool yields which kind of record. Derived from the tool's own
    # results rather than asked of the model: a citation the model writes is a
    # claim, and claims are the thing being verified. These are ground truth -
    # the rows the database actually returned this turn.
    #
    # Widened by reading each of the 37 tools' return shapes, not by matching
    # names. Only tools whose result carries records with a real id and name
    # belong here; the rest return scores, narratives or write receipts, so
    # mapping them would be inert at best and, if the entity type were guessed
    # wrong, a fabricated citation - the exact failure this mechanism prevents.
    #
    # technical_capability has no detail route, so _source_url returns None for
    # it and the UI shows the name unlinked. The id still travels, so the record
    # stays findable.
    _TOOL_ENTITY = {
        "find_applications": "application",
        "find_applications_by_capability": "application",
        "query_capability_gaps": "capability",
        "search_capabilities_by_problem": "capability",
        "search_archimate_elements": "archimate_element",
        "find_technical_capabilities": "technical_capability",
        "get_solution_summary": "solution",
    }
    MAX_SOURCES = 25

    @staticmethod
    def _source_url(entity_type: str, row: dict):
        """Best-effort link to the record, or None.

        None is a normal outcome, not a failure: url_for needs the endpoint to be
        registered, and blueprints here register non-fatally (CLAUDE.md), so a
        degraded feature must not take the citation with it. The UI shows the
        name unlinked in that case - still verifiable, since the id travels with
        it.
        """
        from flask import url_for

        try:
            if entity_type == "application":
                return url_for("unified_applications.application_detail", id=row["id"])
            if entity_type == "capability":
                return url_for("enterprise_crud.get_capability", capability_id=row["id"])
            if entity_type == "vendor":
                return url_for("unified_vendors.vendor_detail", vendor_id=row["id"])
            if entity_type == "solution":
                return url_for("solution_design.view_solution", solution_id=row["id"])
            if entity_type == "archimate_element":
                # This route is keyed by layer and type as well as id, both of
                # which search_archimate_elements already returns.
                if row.get("layer") and row.get("type"):
                    return url_for(
                        "archimate_crud.detail_element",
                        layer=str(row["layer"]).lower(),
                        element_type=str(row["type"]),
                        element_id=row["id"],
                    )
        except Exception:
            # Unregistered endpoint, or no request/app context to build against.
            return None
        return None

    # Tools that return the canonical answer envelope.
    _ANSWER_TOOLS = frozenset({"answer", "simulate_impact"})

    def _collect_answer_sources(self, result: dict, sources: list) -> None:
        """The stored answer first (its page opens the same rows the Ask page
        shows), then the elements its rows name, each linked to its record."""
        body = result.get("result") if isinstance(result, dict) else None
        if not isinstance(result, dict) or not result.get("success") or not isinstance(body, dict):
            return
        seen = {(s["type"], s["id"]) for s in sources}
        address = body.get("address")
        if address and ("answer", address) not in seen and len(sources) < self.MAX_SOURCES:
            sources.append({
                "type": "answer",
                "id": address,
                "name": f"Answer: {str(body.get('question_kind', 'impact')).replace('_', ' ')}",
                "url": body.get("page_url"),
            })
        elements = body.get("elements") or {}
        shown_ids = {r.get("element_id") for r in body.get("rows") or [] if isinstance(r, dict)}
        shown_ids |= set(body.get("params", {}).get("element_ids") or [])
        if body.get("params", {}).get("element_id") is not None:
            shown_ids.add(body["params"]["element_id"])
        for key, element in elements.items():
            if len(sources) >= self.MAX_SOURCES:
                return
            if not isinstance(element, dict) or element.get("id") is None or not element.get("name"):
                continue
            if shown_ids and element["id"] not in shown_ids:
                continue
            if ("archimate_element", element["id"]) in seen:
                continue
            seen.add(("archimate_element", element["id"]))
            sources.append({
                "type": "archimate_element",
                "id": element["id"],
                "name": element["name"],
                "url": self._source_url("archimate_element", element),
            })

    def _collect_sources(self, tool_name: str, result: dict, sources: list) -> None:
        """Record the entities a read tool actually returned, for citation.

        Without this an answer is unfalsifiable: the model says "Salesforce is
        end-of-life in 2027" and the reader has no id, no link and no way to tell
        a real row from a fluent invention. For a system of record that is worse
        than no answer.

        Deduplicated on (type, id) because the same record often comes back from
        several tools in one turn, and capped so a broad query cannot bury the
        answer under its own footnotes.
        """
        if tool_name in self._ANSWER_TOOLS:
            self._collect_answer_sources(result, sources)
            return
        entity_type = self._TOOL_ENTITY.get(tool_name)
        if not entity_type or not isinstance(result, dict) or not result.get("success"):
            return
        rows = result.get("result")
        # A single-record tool returns {"result": {...}} rather than a list.
        # get_solution_summary is exactly that, so requiring a list meant an
        # answer built on a real solution cited nothing and read as ungrounded —
        # while _source_url had carried an unreachable `solution` branch all along.
        if isinstance(rows, dict):
            rows = [rows]
        if not isinstance(rows, list):
            return

        seen = {(s["type"], s["id"]) for s in sources}
        for row in rows:
            if len(sources) >= self.MAX_SOURCES:
                return
            if not isinstance(row, dict) or row.get("id") is None or not row.get("name"):
                continue
            key = (entity_type, row["id"])
            if key in seen:
                continue
            seen.add(key)
            sources.append({
                "type": entity_type,
                "id": row["id"],
                "name": row["name"],
                "url": self._source_url(entity_type, row),
            })

    @classmethod
    def _serialise_context(cls, raw_ctx: dict) -> str:
        """Serialise domain context, dropping WHOLE keys when over budget.

        This was `json.dumps(raw_ctx, default=str)[:6000]` - a raw slice of a
        JSON string. Two problems, both silent. It cut mid-token, so the model
        received malformed JSON ending part-way through a field value and had to
        guess where the data stopped. And nothing told it anything had been
        removed, so a context truncated from 40 elements to 22 looked exactly
        like a portfolio that contains 22.

        Dropping whole top-level keys keeps the JSON parseable and names what
        went missing, so the model can say "I wasn't given that" instead of
        answering from the fragment it happened to receive.
        """
        if not raw_ctx:
            return ""

        blob = json.dumps(raw_ctx, default=str)
        if len(blob) <= cls.MAX_CONTEXT_CHARS:
            return blob

        # Largest keys first - dropping one big list usually beats dropping
        # several small scalars that carry the totals.
        kept = dict(raw_ctx)
        dropped = []
        by_size = sorted(
            raw_ctx.keys(),
            key=lambda k: len(json.dumps(raw_ctx[k], default=str)),
            reverse=True,
        )
        for key in by_size:
            if len(json.dumps(kept, default=str)) <= cls.MAX_CONTEXT_CHARS:
                break
            if len(kept) == 1:
                break  # never drop the last key - an empty object says nothing
            kept.pop(key, None)
            dropped.append(key)

        if dropped:
            kept["_omitted"] = {
                "keys": sorted(dropped),
                "reason": (
                    "Dropped to fit the context budget. These were NOT empty - "
                    "use the search tools to retrieve them rather than assuming "
                    "they contain nothing."
                ),
            }

        blob = json.dumps(kept, default=str)
        # Belt and braces: if a single retained key still blows the budget, cut
        # it, but say so in the text rather than leaving broken JSON.
        if len(blob) > cls.MAX_CONTEXT_CHARS:
            return (
                blob[: cls.MAX_CONTEXT_CHARS]
                + '\n/* CONTEXT TRUNCATED MID-VALUE - treat the final entry as '
                'incomplete and verify anything from it with a tool call. */'
            )
        return blob

    @staticmethod
    def _prepare_history(history: Optional[list]) -> list:
        """Normalise stored turns into a valid, bounded message list.

        Three things have to hold or the provider rejects the request outright:
        the sequence must alternate user/assistant, it must begin with a user
        message, and it must not end with one (this turn's message goes there).
        Stored history can violate all three - a turn whose assistant reply
        failed to persist leaves two user messages adjacent - so this rebuilds
        pairs rather than trusting the rows.
        """
        if not history:
            return []

        pairs = []
        pending_user = None
        for entry in history:
            role = (entry or {}).get("role")
            content = (entry or {}).get("content")
            if not content or role not in ("user", "assistant"):
                continue
            if role == "user":
                pending_user = str(content)
            elif pending_user is not None:
                pairs.append((pending_user, str(content)))
                pending_user = None
            # An assistant message with no preceding user message is dropped:
            # replaying it would break alternation.

        pairs = pairs[-AgentRunner.MAX_HISTORY_TURNS:]

        # Drop whole turns from the oldest end until under the character budget.
        while pairs and sum(len(u) + len(a) for u, a in pairs) > AgentRunner.MAX_HISTORY_CHARS:
            pairs.pop(0)

        messages = []
        for user_text, assistant_text in pairs:
            messages.append({"role": "user", "content": user_text})
            messages.append({"role": "assistant", "content": assistant_text})
        return messages

    @traced("agent-run", "assistant")
    def run(
        self,
        user_message: str,
        domain: str = "general",
        context: Optional[dict] = None,
        persona: Optional[str] = None,
        requested_model: Optional[str] = None,
        stream_mode: bool = False,
        history: Optional[list] = None,
    ) -> dict:
        """
        Execute the full ReAct loop for one user message.

        `history` is the prior turns of this conversation as
        [{"role": "user"|"assistant", "content": str}, ...], oldest first.
        Without it every turn was an independent single-message call - the model
        had no idea what "it" referred to in a follow-up - while the UI presented
        a persistent thread rail. Callers pass None for a genuinely new
        conversation.

        Returns
        -------
        dict with keys:
          response         : str   — final LLM text to show the user
          actions_taken    : list  — successfully executed tool calls
          pending_approvals: list  — 'approve' tier tools queued for confirmation
          error            : str   — set only on fatal failure
        """
        from app.modules.ai_chat.services.llm_service_impl import LLMService
        from app.modules.ai_chat.tools.executor import ToolCall, ToolExecutor
        from app.modules.ai_chat.tools.registry import TOOL_SCHEMAS, TOOL_SCHEMA_BY_NAME

        # ARCH-020: a fresh id per turn, independent of chat_session_id (the
        # conversation). Any approval queued during this call to run() carries
        # this id so it can be traced back to the specific run that raised it.
        import uuid as _uuid
        self._turn_id = str(_uuid.uuid4())

        self._context_struct = None
        # Build system prompt with live domain context
        system_prompt = self._build_system_prompt(domain, context, persona, user_message=user_message)

        # Only this turn's tool results and the structured context the system
        # injected ground a claim about the estate. The words of the user and
        # earlier assistant turns do not: a fact someone asserted is not a fact
        # on record.
        self._grounding = Grounding()
        self._grounding.add_context(self._context_struct)
        self._grounding.add_context(self._context_text)
        self._gate_mode_now = self._gate_mode()
        self._estate_question = (
            None if self._gate_mode_now == "off"
            else self._classify_estate(user_message, context, history)
        )
        self._question_names = set()
        self._withheld_claims = []
        self._would_withhold = []
        if self._gate_mode_now != "off":
            try:
                from app.middleware.tenant_context import current_org_id

                self._question_names = organisation_names_in_text(user_message, current_org_id())
            except Exception:
                logger.warning("AgentRunner: question name lookup failed", exc_info=True)
        self._nudged = False
        self._gate_cache = None

        # Get provider, model, and first available API key
        try:
            provider, model = LLMService._get_configured_provider()
            if requested_model:
                # Resolve the PROVIDER along with the model. Overriding `model`
                # alone sent whatever the user picked to whatever provider the
                # resolver happened to land on - choose an Anthropic model while
                # it resolved OpenAI and a Claude model id went to OpenAI, which
                # fails with an opaque provider error.
                #
                # MultiDomainChatService._resolve_requested_model already does
                # this correctly by matching the id against each enabled
                # provider's configured models; reuse it rather than writing a
                # second, divergent copy.
                resolved = None
                try:
                    from app.modules.ai_chat.services.multi_domain_chat_service import (
                        MultiDomainChatService,
                    )

                    resolved = MultiDomainChatService()._resolve_requested_model(requested_model)
                except Exception:
                    logger.warning(
                        "AgentRunner: could not resolve requested model %r to a provider",
                        requested_model,
                        exc_info=True,
                    )

                if resolved:
                    provider, model = resolved
                else:
                    # Not configured for any enabled provider. Keep the resolved
                    # provider's own default rather than sending it a model id it
                    # does not serve.
                    logger.warning(
                        "AgentRunner: requested model %r is not configured for any "
                        "enabled provider; using %s/%s instead",
                        requested_model, provider, model,
                    )
            api_keys = LLMService._get_all_api_keys(provider)
            if not api_keys:
                return self._finalize(
                    self._fallback("No API keys configured for provider: " + provider),
                    user_message=user_message, domain=domain, context=context,
                    persona=persona,
                )
            api_key = api_keys[0]
            chain = []
            first_base_url = None
            if provider in _TOOL_PROVIDERS:
                chain = build_provider_chain(provider, model, pin_first=bool(requested_model))
                if chain:
                    provider, model, api_key, first_base_url = chain[0]
        except Exception as e:
            logger.warning("AgentRunner: provider selection failed: %s", e)
            return self._finalize(
                self._fallback(str(e)),
                user_message=user_message, domain=domain, context=context,
                persona=persona,
            )

        # Verify provider supports tool use
        # openrouter and nvidia use OpenAI-compatible API with tool calling
        if provider not in _TOOL_PROVIDERS:
            logger.info(
                "AgentRunner: provider '%s' does not support tool use — falling back to text mode",
                provider,
            )
            return self._text_only_fallback(
                user_message, system_prompt, provider, model, api_key, LLMService,
                user_message=user_message, domain=domain, context=context, persona=persona,
            )

        # Resolve base_url for OpenAI-compatible third-party providers
        base_url = (first_base_url if chain else None) or _base_url_of(provider)

        # Build tool schemas for the provider
        tool_schemas = self._build_tool_schemas(provider, TOOL_SCHEMAS)
        offered_tools = {
            s["name"] for s in TOOL_SCHEMAS if "chat" in s.get("surfaces", ["chat"])
        }

        # Initialise message history with the prior turns of this conversation.
        #
        # Bounded deliberately: the whole transcript would grow without limit and
        # the tool-call blocks appended during this turn already share the same
        # budget. Kept as whole turns so the sequence stays strictly alternating,
        # which the Anthropic API requires, and trimmed from the OLDEST end so
        # the most recent exchange - the one a follow-up refers to - always
        # survives.
        messages = self._prepare_history(history)
        messages.append({"role": "user", "content": user_message})
        trusted_workspace_id = (context or {}).get("_trusted_workspace_id")
        executor = ToolExecutor(self.user_id, persona=persona)
        actions_taken = []
        required_actions = []
        pending_approvals = []
        # Records the read tools actually returned this turn, for citation.
        sources = []

        chain_pos = 0
        for iteration in range(MAX_ITERATIONS):
            # Call LLM
            self._held_tokens = []
            llm_resp = None
            while llm_resp is None:
                try:
                    llm_resp = self._call_llm(
                        provider, model, api_key, system_prompt, messages, tool_schemas,
                        stream=stream_mode,
                        base_url=base_url,
                    )
                except Exception as e:
                    self._release_held_tokens(False)
                    if is_retryable_llm_error(e):
                        logger.warning(
                            "AgentRunner: %s/%s refused (%s)", provider, model,
                            sanitize_agent_error(e),
                        )
                        if chain_pos + 1 < len(chain):
                            chain_pos += 1
                            provider, model, api_key, base_url = chain[chain_pos]
                            tool_schemas = self._build_tool_schemas(provider, TOOL_SCHEMAS)
                            self._held_tokens = []
                            continue
                        return self._finalize(
                            self._fallback(f"LLM call failed: {e}", busy=True),
                            user_message=user_message, domain=domain, context=context,
                            persona=persona, model_used=model,
                        )
                    logger.warning(
                        "AgentRunner: %s/%s failed (%s)", provider, model,
                        sanitize_agent_error(e),
                    )
                    logger.debug("AgentRunner LLM call failed (iteration %d)", iteration, exc_info=True)
                    return self._finalize(
                        self._fallback(f"LLM call failed: {e}"),
                        user_message=user_message, domain=domain, context=context,
                        persona=persona, model_used=model,
                    )

            # Text-only response — we're done
            if not llm_resp.get("tool_calls"):
                # The final text is shown only if the citation gate passes it.
                final_text = llm_resp.get("text") or ""
                if (
                    self._gate_mode_now == "enforce"
                    and self._estate_question
                    and self._grounding.tool_results == 0
                    and not self._nudged
                    and iteration < MAX_ITERATIONS - 1
                ):
                    # An estate question must be answered from a stored answer.
                    # One nudge to look it up, then the gate decides.
                    self._nudged = True
                    self._release_held_tokens(False)
                    messages.append({"role": "assistant", "content": final_text or "(no answer)"})
                    messages.append({
                        "role": "user",
                        "content": (
                            "Before replying, call the answer tool for this question "
                            "and reply only from the rows it returns, citing each "
                            "record with [ref:<id>]."
                        ),
                    })
                    continue
                if self._gate_mode_now == "enforce":
                    gate_passes = not self._find_uncited(final_text)
                else:
                    # Monitor mode: the answer is shown unchanged; the gate
                    # is evaluated (and logged) once, in _apply_citation_gate.
                    gate_passes = True
                # An answer that read the genome is shown only after its
                # citation post-check, drawn by the server (see _finalize).
                self._release_held_tokens(gate_passes and not self._grounding.genome_slices)
                return self._finalize(
                    {
                        "response": llm_resp.get("text", ""),
                        "actions_taken": actions_taken,
                        "required_actions": required_actions,
                        "pending_approvals": pending_approvals,
                        "sources": sources,
                    },
                    user_message=user_message,
                    domain=domain,
                    context=context,
                    persona=persona,
                    model_used=model,
                    provider_used=provider,
                )

            # Text before a tool call is a preface, not an answer: it is never
            # streamed, never kept in the history of the turn and never stored.
            self._release_held_tokens(False)

            # Process each tool call
            tool_results = []
            for tc_raw in llm_resp["tool_calls"]:
                tc = ToolCall(
                    id=tc_raw["id"],
                    name=tc_raw["name"],
                    arguments=self._inject_trusted_tool_context(
                        tc_raw["name"], tc_raw["arguments"], trusted_workspace_id
                    ),
                )
                schema = TOOL_SCHEMA_BY_NAME.get(tc.name, {})

                if tc.name not in offered_tools:
                    # Tool selection is the server's: only a tool offered on the
                    # chat surface can run, whatever text (a pasted document, a
                    # retrieved excerpt, an earlier tool result) names another.
                    logger.warning(
                        "AgentRunner: refusing tool %r, not offered on the chat surface",
                        tc.name,
                    )
                    result = {
                        "success": False,
                        "error": f"Tool '{tc.name}' is not available here.",
                        "code": "TOOL_NOT_OFFERED",
                    }
                elif self._should_queue(schema, self.auto_execute):
                    # Queue for user approval. Either the tool is always-approve
                    # (destructive/significant regardless of the gate), or it
                    # mutates and this session has auto-execute off - the
                    # write-approval gate described in toggle_auto_execute.
                    approval_id = self._queue_approval(tc, persona=persona)
                    pending_approvals.append({
                        "approval_id": approval_id,
                        "tool": tc.name,
                        "arguments": tc.arguments,
                        "summary": self._approval_summary(tc),
                    })
                    result = {
                        "success": False,
                        "pending_approval": True,
                        "approval_id": approval_id,
                        "message": (
                            f"Action queued for confirmation (approval #{approval_id}). "
                            "The user must approve before this executes."
                        ),
                    }
                    self._emit({"type": "approval_queued", "tool": tc.name, "approval_id": approval_id})
                else:
                    # Auto-execute
                    self._emit({"type": "tool_start", "tool": tc.name, "args": tc.arguments})
                    result = executor.execute(tc)
                    self._collect_sources(tc.name, result, sources)
                    self._grounding.add_tool_result(
                        result, mutates=tc.name in _MUTATING_TOOLS
                    )
                    self._emit({"type": "tool_result", "tool": tc.name, "result": result})

                    if result.get("success"):
                        actions_taken.append({
                            "tool": tc.name,
                            "arguments": tc.arguments,
                            "result": result.get("result"),
                            "message": result.get("message"),
                            # Marked here so the client does not carry a second
                            # copy of the read/write split. The registry flag is
                            # the single source of truth for receipts, the
                            # next-artifact suggestion and approval tiering.
                            "mutates": tc.name in _MUTATING_TOOLS,
                        })
                    elif result.get("recovery"):
                        required_actions.append(result["recovery"])

                tool_results.append((tc_raw, result))

            # Feed results back into messages for next iteration
            messages = self._append_tool_results(provider, messages, llm_resp, tool_results)

        # Hit iteration cap
        logger.warning("AgentRunner hit MAX_ITERATIONS=%d for user_id=%s", MAX_ITERATIONS, self.user_id)
        return self._finalize(
            {
                "response": (
                    "I've completed the available steps. Here's what was done:\n"
                    + "\n".join(f"- {a['message']}" for a in actions_taken)
                    if actions_taken
                    else "I reached the action limit without completing all steps. Please try again with a simpler request."
                ),
                "actions_taken": actions_taken,
                "required_actions": required_actions,
                "pending_approvals": pending_approvals,
                "sources": sources,
                # Text the system built from tool receipts, not model prose.
                "system_generated": True,
            },
            user_message=user_message,
            domain=domain,
            context=context,
            persona=persona,
            model_used=model,
            provider_used=provider,
        )

    # ------------------------------------------------------------------ #
    # System prompt construction                                           #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _short_token(value, default: str) -> str:
        """A short single-line label from client input (a phase letter)."""
        text = "".join(ch for ch in str(value or "") if ch.isalnum() or ch in " -_.")[:32]
        return text.strip() or default

    def _build_system_prompt(self, domain: str, context: Optional[dict], persona: Optional[str], user_message: str = "") -> str:
        """Build agent system prompt: agent prefix + solution context + live domain context."""
        # Inject solution context if present (blueprint panel provides this)
        solution_block = ""
        solution_id = as_whole_number((context or {}).get("solution_id"))
        if isinstance(solution_id, bool) or not isinstance(solution_id, int):
            solution_id = None
        if solution_id is not None:
            # The id is coerced to a whole number and the name, which the client
            # supplies, is fenced: neither can carry an instruction into the
            # system role or change which solution the tools default to.
            from app.modules.ai_chat.services.architect_persona_charters import fence_untrusted

            solution_name = str(context.get("solution_name") or "Unknown")[:200]
            solution_block = (
                f"\nACTIVE SOLUTION CONTEXT:\n"
                f"  Solution ID: {solution_id}\n"
                f"  ADM Phase: {self._short_token(context.get('current_phase'), 'A')}\n"
                f"All create/link tools default to solution_id={solution_id} "
                f"unless the user specifies otherwise.\n"
                + fence_untrusted("SOLUTION NAME", solution_name)
            )

        # Portfolio context — compact snapshot of related solutions
        portfolio_block = ""
        if solution_id is not None:
            try:
                from app.modules.ai_chat.services.architect_persona_charters import fence_untrusted
                from app.modules.ai_chat.services.portfolio_context import PortfolioContextBuilder
                portfolio_block = fence_untrusted(
                    "PORTFOLIO CONTEXT",
                    PortfolioContextBuilder().build(
                        solution_id=solution_id,
                        user_id=self.user_id,
                        question=user_message,
                    ),
                )
            except Exception as _pb_err:
                logger.debug("AgentRunner: portfolio context failed: %s", _pb_err)

        ctx_block = ""
        try:
            from app.modules.ai_chat.services.multi_domain_chat_service import MultiDomainChatService
            svc = MultiDomainChatService()
            ctx_result = svc.get_domain_context(domain, context or {})
            if ctx_result.get("success"):
                raw_ctx = ctx_result.get("context", {})
                # Organisation-owned text (names, descriptions, uploaded
                # documents) is data, not instruction: it is fenced.
                from app.modules.ai_chat.services.architect_persona_charters import fence_untrusted

                # The structured rows the model is handed ground its answer.
                serialised = self._serialise_context(raw_ctx)
                # Only what the model actually saw (whole keys are dropped
                # when over budget) grounds anything.
                try:
                    seen = json.loads(serialised) if serialised else None
                except ValueError:
                    seen = None
                self._context_struct = seen if isinstance(seen, dict) else None
                ctx_block = fence_untrusted("LIVE ARCHITECTURE DATA", serialised)
        except Exception as e:
            logger.debug("AgentRunner: context build failed: %s", e)

        # The governed charter: the persona's mission and scope, the six HARD
        # RULES (evidence, no fabrication, propose-don't-dispose, cite your
        # source) and a live-data block queried now.
        #
        # This used to be the one-line note below and nothing else, so selecting
        # "AI Data Architect" over "CIO" changed the prompt by a job title. The
        # charters were reachable only through
        # MultiDomainChatService._get_persona_system_prompt <- process_message,
        # which this path never calls — so the assistant was not governed by the
        # rules CLAUDE.md names as its governance layer, and the rule forbidding
        # invented application names and counts was never in context.
        persona_note = ""
        if persona:
            try:
                from app.modules.ai_chat.services.architect_persona_charters import (
                    build_architect_prompt,
                )

                # Returns None for the six ungoverned personas, which then fall
                # through to the label below rather than losing their persona.
                charter = build_architect_prompt(persona)
                if charter:
                    persona_note = "\n" + charter + "\n"
            except Exception:
                # A charter that cannot be built must not cost the turn. The
                # label is a worse prompt, not a broken one.
                logger.warning(
                    "AgentRunner: charter unavailable for persona %s", persona, exc_info=True
                )
            if not persona_note:
                persona_note = f"\nYou are operating as: {persona.replace('_', ' ').title()}.\n"

        # The charter goes last, after the context block. _serialise_context
        # drops whole keys when it is over budget, so anything appended after it
        # is safe from that trimming — and the HARD RULES are the last thing that
        # should be sacrificed to make room.
        # What the turn may state: the fenced blocks the model was handed.
        # The client-supplied solution name is not grounding; the live-data and
        # portfolio blocks the system built are.
        self._context_text = portfolio_block
        return _AGENT_PREFIX + solution_block + portfolio_block + ctx_block + persona_note

    # ------------------------------------------------------------------ #
    # Provider-specific tool schema conversion                             #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _build_tool_schemas(provider: str, schemas: list) -> list:
        # The one place the chat model's tool list is built. A tool that is not
        # offered on the chat surface (the connector's route-backed tools, which
        # need a request context the streaming path lacks) is left out; an entry
        # with no "surfaces" key is a chat tool, as before.
        schemas = [s for s in schemas if "chat" in s.get("surfaces", ["chat"])]
        if provider == "anthropic":
            return [
                {
                    "name": s["name"],
                    "description": s["description"],
                    "input_schema": s["parameters"],
                }
                for s in schemas
            ]
        else:  # openai
            return [
                {
                    "type": "function",
                    "function": {
                        "name": s["name"],
                        "description": s["description"],
                        "parameters": s["parameters"],
                    },
                }
                for s in schemas
            ]

    # ------------------------------------------------------------------ #
    # LLM call (tool-enabled)                                             #
    # ------------------------------------------------------------------ #

    def _call_llm(
        self,
        provider: str,
        model: str,
        api_key: str,
        system_prompt: str,
        messages: list,
        tools: list,
        stream: bool = False,
        base_url: str = None,
    ) -> dict:
        """
        Call the LLM with tool schemas.  Returns normalised dict:
          {"text": str|None, "tool_calls": list, "raw": raw_response}

        Everything that leaves for the provider (instructions, history, tool
        results) is scrubbed of email addresses, phone numbers and credentials
        first.
        """
        from app.modules.ai_chat.services.llm_service_impl import (
            scrub_chat_messages,
            scrub_for_transmission,
        )

        system_prompt = scrub_for_transmission(system_prompt)
        messages = scrub_chat_messages(messages)
        platform_funded = self._platform_funded(provider)
        if platform_funded:
            from app.modules.ai_chat.services.llm_service_impl import AnswersPaused, LLMService

            allowed, reason = LLMService.platform_call_allowed(provider)
            if not allowed:
                raise AnswersPaused(reason)
        if provider == "anthropic":
            if stream:
                result = self._call_anthropic_streaming(model, api_key, system_prompt, messages, tools)
            else:
                result = self._call_anthropic(model, api_key, system_prompt, messages, tools)
        else:
            if stream:
                result = self._call_openai_streaming(model, api_key, system_prompt, messages, tools, base_url=base_url)
            else:
                result = self._call_openai(model, api_key, system_prompt, messages, tools, base_url=base_url)
        if platform_funded:
            self._record_platform_spend(provider, model, result.get("raw") if isinstance(result, dict) else None)
        return result

    @staticmethod
    def _platform_funded(provider: str) -> bool:
        """True when the key in use is the platform's (from the environment), not the organisation's row."""
        try:
            from app.modules.ai_chat.services.llm_service_impl import LLMService

            return LLMService._keys_are_platform(provider)
        except Exception:  # noqa: BLE001 - without a settings table in reach the key can only be the platform's
            return True

    @staticmethod
    def _record_platform_spend(provider: str, model: str, raw) -> None:
        """Add the call to this month's ledger from the usage the provider reported; never breaks the turn."""
        try:
            from app.modules.ai_chat.services.llm_service_impl import LLMService

            usage = getattr(raw, "usage", None)
            if usage is None and isinstance(raw, dict):
                usage = raw.get("usage")

            def _count(*names):
                for name in names:
                    value = getattr(usage, name, None) if not isinstance(usage, dict) else usage.get(name)
                    if value:
                        return int(value)
                return 0

            LLMService.record_spend(
                provider, model, _count("prompt_tokens", "input_tokens"), _count("completion_tokens", "output_tokens"),
            )
        except Exception:  # noqa: BLE001 - the answer was given; the ledger failure is logged by the service
            logger.warning("AgentRunner: spend was not recorded for %s", provider, exc_info=True)

    def _call_anthropic(self, model, api_key, system_prompt, messages, tools) -> dict:
        import anthropic
        from app.modules.ai_chat.services.llm_service_impl import LLMService

        LLMService._guard_provider_call("anthropic", model, prompt=system_prompt)
        client = anthropic.Anthropic(api_key=api_key, timeout=90.0)
        max_tokens = 8192 if "sonnet" in model or "opus" in model else 4096

        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=[
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=messages,
            tools=tools,
            tool_choice={"type": "auto"},
        )

        text = None
        tool_calls = []
        for block in response.content:
            if block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "arguments": block.input,
                })
            elif block.type == "text":
                text = block.text

        return {"text": text, "tool_calls": tool_calls, "raw": response}

    def _call_anthropic_streaming(self, model, api_key, system_prompt, messages, tools) -> dict:
        import anthropic
        from app.modules.ai_chat.services.llm_service_impl import LLMService

        LLMService._guard_provider_call("anthropic", model, prompt=system_prompt)
        client = anthropic.Anthropic(api_key=api_key, timeout=90.0)
        max_tokens = 8192 if "sonnet" in model or "opus" in model else 4096

        with client.messages.stream(
            model=model,
            max_tokens=max_tokens,
            system=[
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=messages,
            tools=tools,
            tool_choice={"type": "auto"},
        ) as stream:
            for text in stream.text_stream:
                self._emit_token(text)
            final = stream.get_final_message()

        text = None
        tool_calls = []
        for block in final.content:
            if block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "arguments": block.input,
                })
            elif block.type == "text":
                text = block.text

        return {"text": text, "tool_calls": tool_calls, "raw": final}

    @staticmethod
    def _provider_extra_body(provider_name: str) -> Optional[dict]:
        """OpenRouter's data-policy setting: only endpoints that neither train on nor log prompts."""
        if provider_name == "openrouter":
            from app.modules.ai_chat.services.llm_service_impl import OPENROUTER_PROVIDER_POLICY

            return {"provider": dict(OPENROUTER_PROVIDER_POLICY)}
        return None

    def _call_openai(self, model, api_key, system_prompt, messages, tools, base_url=None) -> dict:
        from openai import OpenAI
        from app.modules.ai_chat.services.llm_service_impl import LLMService

        provider_name = "openai"
        if base_url and "openrouter.ai" in base_url:
            provider_name = "openrouter"
        elif base_url and "integrate.api.nvidia.com" in base_url:
            provider_name = "nvidia"
        LLMService._guard_provider_call(provider_name, model, prompt=system_prompt)
        client = OpenAI(api_key=api_key, base_url=base_url, timeout=90.0)
        full_messages = [{"role": "system", "content": system_prompt}] + messages

        # Newer OpenAI models (o1, o3, gpt-5.x) require max_completion_tokens; all other models
        # accept it too (it supersedes the deprecated max_tokens parameter).
        _token_limit = 8192 if ("gpt-4" in model or "gpt-5" in model) else 4096
        response = client.chat.completions.create(
            model=model,
            messages=full_messages,
            tools=tools,
            tool_choice="auto",
            temperature=0.0,
            max_completion_tokens=_token_limit,
            extra_body=self._provider_extra_body(provider_name),
        )

        msg = response.choices[0].message
        tool_calls = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                tool_calls.append({
                    "id": tc.id,
                    "name": tc.function.name,
                    "arguments": json.loads(tc.function.arguments),
                })

        return {"text": msg.content, "tool_calls": tool_calls, "raw": response}

    def _call_openai_streaming(self, model, api_key, system_prompt, messages, tools, base_url=None) -> dict:
        from openai import OpenAI
        from app.modules.ai_chat.services.llm_service_impl import LLMService

        provider_name = "openai"
        if base_url and "openrouter.ai" in base_url:
            provider_name = "openrouter"
        elif base_url and "integrate.api.nvidia.com" in base_url:
            provider_name = "nvidia"
        LLMService._guard_provider_call(provider_name, model, prompt=system_prompt)
        client = OpenAI(api_key=api_key, base_url=base_url, timeout=90.0)
        full_messages = [{"role": "system", "content": system_prompt}] + messages

        text_acc = ""
        tool_calls_acc: dict = {}

        # Newer OpenAI models (o1, o3, gpt-5.x) require max_completion_tokens.
        _token_limit = 8192 if ("gpt-4" in model or "gpt-5" in model) else 4096
        with client.chat.completions.create(
            model=model,
            messages=full_messages,
            tools=tools,
            tool_choice="auto",
            temperature=0.0,
            max_completion_tokens=_token_limit,
            stream=True,
            extra_body=self._provider_extra_body(provider_name),
        ) as stream:
            for chunk in stream:
                delta = chunk.choices[0].delta
                if delta.content:
                    text_acc += delta.content
                    self._emit_token(delta.content)
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in tool_calls_acc:
                            tool_calls_acc[idx] = {"id": "", "name": "", "arguments": ""}
                        if tc.id:
                            tool_calls_acc[idx]["id"] = tc.id
                        if tc.function and tc.function.name:
                            tool_calls_acc[idx]["name"] += tc.function.name
                        if tc.function and tc.function.arguments:
                            tool_calls_acc[idx]["arguments"] += tc.function.arguments

        tool_calls = []
        for idx in sorted(tool_calls_acc.keys()):
            tc = tool_calls_acc[idx]
            try:
                arguments = json.loads(tc["arguments"]) if tc["arguments"] else {}
            except json.JSONDecodeError:
                arguments = {}
            tool_calls.append({"id": tc["id"], "name": tc["name"], "arguments": arguments})

        return {"text": text_acc or None, "tool_calls": tool_calls, "raw": None}

    # ------------------------------------------------------------------ #
    # Message history management                                          #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _fenced_result(tc_raw: dict, result: dict) -> str:
        """A tool result as the model sees it: inside the one untrusted-content
        fence. Names, descriptions and uploaded text in a result are data; an
        instruction in one is reported, not followed, and cannot select a tool."""
        from app.modules.ai_chat.services.architect_persona_charters import fence_untrusted

        label = "".join(
            ch for ch in str(tc_raw.get("name", "tool")) if ch.isalnum() or ch == "_"
        )[:60]
        return fence_untrusted(f"TOOL RESULT {label}", json.dumps(result, default=str))

    def _append_tool_results(
        self, provider: str, messages: list, llm_resp: dict, tool_results: list
    ) -> list:
        """Append assistant tool-use blocks and tool results into message history."""
        messages = list(messages)

        if provider == "anthropic":
            # Append the full raw content blocks from the assistant
            # Text blocks that accompany a tool call are dropped: only the
            # tool_use blocks are carried forward.
            blocks = [
                b for b in llm_resp["raw"].content
                if (b.get("type") if isinstance(b, dict) else getattr(b, "type", None)) != "text"
            ]
            messages.append({
                "role": "assistant",
                "content": blocks,
            })
            # Append tool results as a user message
            messages.append({
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tc_raw["id"],
                        "content": self._fenced_result(tc_raw, result),
                    }
                    for tc_raw, result in tool_results
                ],
            })
        else:  # openai
            # raw is None in streaming mode — reconstruct assistant message from accumulated fields
            # The text that accompanies a tool call is not carried forward.
            assistant_content = None
            messages.append({
                "role": "assistant",
                "content": assistant_content,
                "tool_calls": [
                    {
                        "id": tc_raw["id"],
                        "type": "function",
                        "function": {
                            "name": tc_raw["name"],
                            "arguments": json.dumps(tc_raw["arguments"]),
                        },
                    }
                    for tc_raw, _ in tool_results
                ],
            })
            for tc_raw, result in tool_results:
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc_raw["id"],
                    "content": self._fenced_result(tc_raw, result),
                })

        return messages

    # ------------------------------------------------------------------ #
    # Approval queue                                                       #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _should_queue(schema: dict, auto_execute: bool) -> bool:
        """True if a tool call must be queued for confirmation, not executed now.

        Three independent reasons, any one is sufficient:
          - tier == "approve": always queued. These are destructive/significant
            regardless of the write-approval gate.
          - mutates is True: the write-approval gate. A tool that writes must
            always queue — the auto_execute switch only controls whether reads
            run unconfirmed, not whether writes do.
          - risk_class in {"write", "external_action"}: a tool that writes
            directly or triggers external side effects must always queue,
            even if auto_execute is on. This is the backup guard for the 17
            tier="auto" mutating tools.

        Read and propose tools (risk_class "read" or "propose", mutates False)
        are never queued by this rule — gating reads would put every search
        behind a confirmation prompt, which is the failure mode
        toggle_auto_execute's docstring warned against.

        Pure and schema-driven so it can be exhaustively unit-tested without a
        DB, an LLM, or a Flask request/session.

        Fails CLOSED on an unclassified tool. TOOL_SCHEMA_BY_NAME.get(name, {})
        in the run loop below hands this {} for any tool name the registry does
        not recognise, and `mutates` is absent from every real schema only in
        that case (every registered tool declares it explicitly — see
        tests/test_tool_mutates_flag.py). Treating "we don't know" the same as
        "doesn't mutate" would let an unregistered or future tool execute
        unqueued the moment it forgot to declare the flag; treating it as
        "mutates" instead means the only failure mode is an unnecessary
        confirmation prompt, never a silent write.
        """
        if schema.get("tier") == "approve":
            return True
        mutates = schema.get("mutates")
        if mutates is None:
            return True
        # Always queue if the tool writes or triggers side effects,
        # regardless of auto_execute. The auto_execute flag only affects
        # whether reads bypass confirmation.
        if bool(mutates):
            return True
        if schema.get("risk_class") in ("write", "external_action"):
            return True
        return False

    def _queue_approval(self, tc: "ToolCall", persona: Optional[str] = None) -> int:
        """Write a pending AIChatCRUDApproval record and return its ID."""
        from app.modules.ai_chat.services.ai_chat_approval_service import (
            _load_acting_user,
            create_approval_record,
        )

        # Consolidation: routed through the one writer of ai_chat_crud_approvals
        # instead of constructing the row directly, which left organization_id
        # NULL here (invisible to every organisation-scoped query and to
        # escalate_overdue_approvals). _load_acting_user, not User.query.get():
        # the agent runner loops over tenants inside one session, and get()
        # returning an identity-map hit from a different organisation's turn
        # would bypass the tenant filter entirely.
        actor = _load_acting_user(self.user_id)
        approval = create_approval_record(
            organization_id=actor.organization_id if actor else None,
            operation_type="tool_use",
            entity_type=tc.name,
            original_command=tc.name,
            operation_payload=tc.arguments,
            summary=self._approval_summary(tc),
            user_id=self.user_id,
            chat_session_id=self.chat_session_id,
            agent_turn_id=self._turn_id,
            expiry_minutes=24 * 60,  # unchanged: this caller's own 24h window
            persona=persona,
        )
        return approval.id

    # Human-readable business-term label for a tool name, used by the generic
    # fallback in _approval_summary below (ARCH-023). Kept separate from the
    # per-tool templates so a new tool without a bespoke template still reads
    # as English instead of a Python identifier.
    _TOOL_LABELS = {
        "create_capability": "create a capability",
        "create_application": "create an application",
        "create_vendor": "create a vendor",
        "create_solution": "create a solution",
        "update_application_status": "change an application's status",
        "submit_for_arb_review": "submit a solution for ARB review",
        "generate_blueprint_narrative": "generate blueprint narrative text",
        "delete_capability": "delete a capability",
        "delete_application": "delete an application",
    }

    @classmethod
    def _approval_summary(cls, tc: "ToolCall") -> str:
        """A business-readable summary — never Python/JSON literal syntax (ARCH-023).

        Every operation type gets either a bespoke sentence template (below) or
        a generic-but-still-English fallback built from a human label plus a
        plain "key: value" listing of the arguments — never an f-string dump of
        the raw dict/args, which previously rendered verbatim as e.g.
        "Execute create_solution with args: {'name': 'HxGN EAM', ...}". The raw
        payload is still available to the caller via operation_payload for a
        "view technical detail" disclosure in the UI; it is simply never the
        primary summary text.
        """
        summaries = {
            "update_application_status": (
                "Change application '{application_name}' status to '{new_status}'. Reason: {rationale}"
            ),
            "submit_for_arb_review": (
                "Submit solution '{solution_name}' for ARB review."
            ),
            "generate_blueprint_narrative": (
                "Generate AI narrative for section '{section_id}' of solution {solution_id}. "
                "This will overwrite any existing text in that section."
            ),
        }
        template = summaries.get(tc.name)
        if template:
            try:
                return template.format(**tc.arguments)
            except KeyError:
                pass  # fall through to the generic builder below

        label = cls._TOOL_LABELS.get(tc.name, tc.name.replace("_", " "))
        if not tc.arguments:
            return f"Ask the assistant to {label}."
        details = "; ".join(
            f"{str(k).replace('_', ' ')}: {v}" for k, v in tc.arguments.items()
        )
        return f"Ask the assistant to {label} — {details}."

    # ------------------------------------------------------------------ #
    # Fallbacks                                                           #
    # ------------------------------------------------------------------ #

    def _fallback(self, reason: str, busy: bool = False) -> dict:
        reason_l = (reason or "").lower()
        if "answers are paused" in reason_l:
            from app.modules.ai_chat.services.llm_service_impl import PAUSED_MESSAGE

            msg = PAUSED_MESSAGE
        elif busy:
            msg = (
                "The assistant is busy right now. Wait a minute, then press Retry."
            )
        elif any(t in reason_l for t in ("no api key", "not configured", "no provider", "no keys")):
            msg = (
                "AI features aren't configured yet — add an API key under "
                "Admin → API Settings to enable the assistant."
            )
        elif any(t in reason_l for t in _QUOTA_PHRASES):
            msg = (
                "The AI service is busy right now (its free usage limit was reached). "
                "Wait a minute, then press Retry."
            )
        else:
            msg = (
                "The AI request couldn't be completed. See the error detail below "
                "or check Admin → API Settings."
            )
        return {
            "response": msg,
            "actions_taken": [],
            "pending_approvals": [],
            "error": reason,
        }

    def _record_run(
        self,
        persona: Optional[str],
        domain: str,
        user_message: str,
        context: Optional[dict],
        actions_taken: list,
        pending_approvals: list,
        sources: list,
        outcome: str,
        success: bool,
        model_used: Optional[str] = None,
    ) -> Optional[str]:
        """Write one AgentRunRecord for this agent invocation.
        
        Returns None on success, or an error string if the record could not be
        written. The caller (``_finalize``) surfaces this in the result dict so
        the caller can act on it instead of silently losing the record.
        """
        try:
            from flask import g, has_request_context

            from app.models.agent_charter import AgentCharter
            from app.models.agent_run_record import AgentRunRecord

            org_id = None
            if has_request_context():
                org_id = getattr(g, "current_org_id", None)
            if org_id is None:
                from sqlalchemy import text

                from app import db as _ra_db
                row = _ra_db.session.execute(
                    text("SELECT organization_id FROM users WHERE id = :uid"),
                    {"uid": self.user_id},
                ).fetchone()
                org_id = int(row[0]) if row and row[0] else None
            if org_id is None:
                return

            charter_version = None
            if persona:
                charter = AgentCharter.current_for(persona, org_id)
                if charter:
                    charter_version = charter.version

            record = AgentRunRecord(
                organization_id=org_id,
                user_id=self.user_id,
                persona=persona,
                charter_version=charter_version,
                inputs={
                    "user_message": user_message,
                    "domain": domain,
                    "context": context,
                    **(
                        {"citation_gate_withheld": self._withheld_claims}
                        if self._withheld_claims
                        else {}
                    ),
                    **(
                        {"citation_gate_would_withhold": self._would_withhold}
                        if self._would_withhold
                        else {}
                    ),
                },
                tools_called=[
                    {
                        "tool": a["tool"],
                        "arguments": a.get("arguments"),
                        "result": a.get("result"),
                        "mutates": a.get("mutates"),
                    }
                    for a in actions_taken
                ] if actions_taken else None,
                records_read=[
                    {"type": s["type"], "id": s["id"], "name": s["name"]}
                    for s in (sources or [])
                ] if sources else None,
                proposals=[
                    {
                        "approval_id": p["approval_id"],
                        "tool": p["tool"],
                        "summary": p.get("summary"),
                    }
                    for p in pending_approvals
                ] if pending_approvals else None,
                outcome=outcome,
                success=success,
                domain=domain,
                model_used=model_used,
            )
            from app import db as _rr_db
            _rr_db.session.add(record)
            _rr_db.session.commit()
            return None
        except Exception:
            logger.warning("AgentRunner: failed to write run record", exc_info=True)
            return "Run record could not be written — the run completed but was not recorded"

    @staticmethod
    def _classify_estate(user_message: str, context=None, history=None) -> Optional[str]:
        """The answer kind when this message is an estate question, else None:
        its wording must match a cue AND the message itself must name an
        element or application of the organisation (the gate's fenced
        lookup). Context and earlier turns never make a question an estate
        question; a follow-up gets no nudge, and an answer that names records
        without markers is caught by the name rule anyway."""
        kind = classify_estate_question(user_message)
        if not kind:
            return None
        try:
            from app.middleware.tenant_context import current_org_id

            if organisation_names_in_text(user_message, current_org_id()):
                return kind
        except Exception:
            logger.warning("AgentRunner: estate question lookup failed", exc_info=True)
        return None

    def _find_uncited(self, text: str) -> list:
        """Run the citation gate on *text* for this turn, once per distinct
        text. A failure of the gate or of the organisation-name lookup fails
        CLOSED for an estate answer (an estate question, or a turn in which a
        tool ran) and open only for a turn that never touched the estate."""
        cached = self._gate_cache
        if cached is not None and cached[0] == text:
            return cached[1]
        try:
            from app.middleware.tenant_context import current_org_id

            org_names = organisation_names_in_text(text, current_org_id())
            uncited = find_uncited_claims(
                text,
                self._grounding,
                org_names=org_names,
                estate_question=bool(self._estate_question),
                question_names=self._question_names,
            )
        except Exception:
            logger.warning("AgentRunner: citation gate failed", exc_info=True)
            if self._gate_mode_now == "enforce" and (
                self._estate_question or self._grounding.tool_results > 0
            ):
                uncited = [{"claim": "", "unsupported": ["the citation check could not run"]}]
            else:
                uncited = []
        self._gate_cache = (text, uncited)
        return uncited

    CITATION_REFUSAL = (
        "Not recorded: I can't give this answer, because it states facts about "
        "your estate that none of the records I read this turn support. "
        "Ask me to look the specific item up, for example what breaks if a named "
        "application fails, and I will answer from the recorded rows."
    )

    def _apply_citation_gate(self, result: dict) -> None:
        """Check an answer's statements about the estate against this turn's
        tool results and context, according to ASSISTANT_CITATION_GATE_MODE.

        "log" (default): the answer is shown unchanged; an answer the gate
        would have refused is logged once (record names and reasons, not the
        text) and its sentences are kept on the run record under
        ``citation_gate_would_withhold``. "enforce": such an answer is replaced
        by an honest "not recorded" and its sentences are kept under
        ``citation_gate_withheld``. "off": nothing runs. The result carries
        counts only (``citation_gate``); sentences never reach the browser.
        """
        mode = self._gate_mode_now
        text = result.get("response")
        if mode == "off" or result.get("error") or result.get("system_generated") \
                or not isinstance(text, str) or not text.strip():
            return
        try:
            uncited = self._find_uncited(text)
        except Exception:
            if mode == "enforce":
                raise
            logger.warning("AgentRunner: citation gate failed (monitor mode)", exc_info=True)
            return
        enforce = mode == "enforce"
        # Counts only: the result is spread into the stream's done event, and
        # the claims withheld must not reach the browser. They go to the run
        # record (see _record_run) and the log.
        result["citation_gate"] = {
            "checked": True,
            "mode": mode,
            "refused": bool(uncited) and enforce,
            "would_refuse": bool(uncited),
            "uncited_count": len(uncited),
            "sources": len(result.get("sources") or []),
        }
        if not uncited:
            return
        if enforce:
            self._withheld_claims = uncited
        else:
            self._would_withhold = uncited
        if enforce:
            logger.warning(
                "AgentRunner: citation gate refused an answer (%d uncited claim(s))",
                len(uncited),
            )
            result["response"] = self.CITATION_REFUSAL
            return
        self._log_would_refuse(uncited)

    def _apply_genome_post_check(self, result: dict, provider: Optional[str]) -> None:
        """When this turn read a genome slice: remove every sentence that cites an
        id outside the turn's ledger, and attach the server-drawn answer (links,
        AI notice, provider, as-of line, partial note) as ``genome_answer``."""
        text = result.get("response")
        if not self._grounding.genome_slices or result.get("error")                 or result.get("system_generated") or not isinstance(text, str) or not text.strip():
            return
        from app.modules.ai_chat.services import genome_answer

        built = genome_answer.build(text, self._grounding, provider)
        result["response"] = built["text"]
        result["genome_answer"] = {
            "html": built["html"],
            "removed": built["removed"],
            "slices": built["slices"],
        }
        if built["removed"]:
            logger.warning(
                "genome_post_check removed %d citation(s) outside the turn ledger", built["removed"],
            )

    def _log_would_refuse(self, uncited: list) -> None:
        """One structured WARNING per answer the gate would have refused. The
        unverified record names are logged; the answer text is not."""
        try:
            from app.middleware.tenant_context import current_org_id

            organization_id = current_org_id()
        except Exception:
            organization_id = None
        reasons = []
        for claim in uncited[:20]:
            reasons.extend(str(item)[:120] for item in claim.get("unsupported", [])[:5])
        event = {
            "event": "citation_gate_would_refuse",
            "mode": "log",
            "organization_id": organization_id,
            "conversation_id": self.chat_session_id,
            "question_class": self._estate_question,
            "unverified_sentences": len(uncited),
            "reasons": reasons,
        }
        logger.warning(
            "citation_gate_would_refuse %s", json.dumps(event, default=str),
            extra={"gate_event": event},
        )

    def _finalize(
        self,
        result: dict,
        user_message: str,
        domain: str,
        context: Optional[dict],
        persona: Optional[str],
        model_used: Optional[str] = None,
        provider_used: Optional[str] = None,
    ) -> dict:
        """Apply the citation gate, record the run and return the result dict.

        ``answered_by`` names the provider and model that produced the reply,
        read from the entry that answered (``model_used``), never the first one
        tried. A refused or failed turn carries none, so no model is named."""
        self._apply_genome_post_check(result, provider_used)
        self._apply_citation_gate(result)
        if (result.get("citation_gate") or {}).get("refused"):
            result.pop("genome_answer", None)
        if model_used and not result.get("error"):
            result["answered_by"] = {"provider": provider_used, "model": model_used}
        record_error = self._record_run(
            persona=persona,
            domain=domain,
            user_message=user_message,
            context=context,
            actions_taken=result.get("actions_taken", []),
            pending_approvals=result.get("pending_approvals", []),
            sources=result.get("sources", []),
            outcome=result.get("response", result.get("error", "")),
            success=not result.get("error"),
            model_used=model_used,
        )
        if record_error:
            result["run_record_error"] = record_error
        return result

    def _text_only_fallback(
        self, message: str, system_prompt: str, provider: str, model: str, api_key: str, LLMService,
        user_message: str = "", domain: str = "general", context: Optional[dict] = None,
        persona: Optional[str] = None,
    ) -> dict:
        """Run a plain text call (no tools) for unsupported providers."""
        try:
            prompt = system_prompt + "\n\nUser: " + message
            text, _ = LLMService._call_llm_with_failover(
                prompt=prompt, model=model, provider=provider
            )
            return self._finalize(
                {
                    "response": text,
                    "actions_taken": [],
                    "pending_approvals": [],
                },
                user_message=user_message or message,
                domain=domain,
                context=context,
                persona=persona,
                model_used=model,
            )
        except Exception as e:
            return self._finalize(
                self._fallback(str(e)),
                user_message=user_message or message,
                domain=domain,
                context=context,
                persona=persona,
            )
