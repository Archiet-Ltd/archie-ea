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

WHAT YOU CAN DO (27 tools available):
Phase A — Motivation: create_driver, create_goal, create_constraint
Phase B-D — Architecture: create_requirement, link_application_to_solution, link_vendor_product,
             link_capability_to_solution, create_archimate_element, create_archimate_relationship
Phase D — ArchiMate: run_inference_engine, diagnose_chain, explain_element, simulate_impact
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

        # Build system prompt with live domain context
        system_prompt = self._build_system_prompt(domain, context, persona, user_message=user_message)

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
                return self._fallback("No API keys configured for provider: " + provider)
            api_key = api_keys[0]
        except Exception as e:
            logger.warning("AgentRunner: provider selection failed: %s", e)
            return self._fallback(str(e))

        # Verify provider supports tool use
        # openrouter and deepseek use OpenAI-compatible API with tool calling
        OPENAI_COMPAT_PROVIDERS = {"openrouter", "deepseek"}
        if provider not in ("anthropic", "openai") and provider not in OPENAI_COMPAT_PROVIDERS:
            logger.info(
                "AgentRunner: provider '%s' does not support tool use — falling back to text mode",
                provider,
            )
            return self._text_only_fallback(
                user_message, system_prompt, provider, model, api_key, LLMService
            )

        # Resolve base_url for OpenAI-compatible third-party providers
        _PROVIDER_BASE_URLS = {
            "openrouter": "https://openrouter.ai/api/v1",
            "deepseek": "https://api.deepseek.com/v1",
        }
        base_url = _PROVIDER_BASE_URLS.get(provider)

        # Build tool schemas for the provider
        tool_schemas = self._build_tool_schemas(provider, TOOL_SCHEMAS)

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
        executor = ToolExecutor(self.user_id)
        actions_taken = []
        required_actions = []
        pending_approvals = []
        # Records the read tools actually returned this turn, for citation.
        sources = []

        for iteration in range(MAX_ITERATIONS):
            # Call LLM
            try:
                llm_resp = self._call_llm(
                    provider, model, api_key, system_prompt, messages, tool_schemas,
                    stream=stream_mode,
                    base_url=base_url,
                )
            except ValueError as e:
                err_msg = str(e)
                if "Budget limit exceeded" in err_msg or "budget" in err_msg.lower():
                    logger.warning("AgentRunner: budget exhausted for user_id=%s", self.user_id)
                    return {
                        "response": (
                            "The monthly AI usage budget has been reached. "
                            "Please contact your administrator to increase the budget, "
                            "or try again next month."
                        ),
                        "actions_taken": actions_taken,
                        "required_actions": required_actions,
                        "pending_approvals": pending_approvals,
                        "sources": sources,
                        "error": err_msg,
                    }
                return self._fallback(f"LLM call failed: {e}")
            except Exception as e:
                logger.exception("AgentRunner LLM call failed (iteration %d)", iteration)
                return self._fallback(f"LLM call failed: {e}")

            # Text-only response — we're done
            if not llm_resp.get("tool_calls"):
                return {
                    "response": llm_resp.get("text", ""),
                    "actions_taken": actions_taken,
                    "required_actions": required_actions,
                    "pending_approvals": pending_approvals,
                    "sources": sources,
                }

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

                if self._should_queue(schema, self.auto_execute):
                    # Queue for user approval. Either the tool is always-approve
                    # (destructive/significant regardless of the gate), or it
                    # mutates and this session has auto-execute off - the
                    # write-approval gate described in toggle_auto_execute.
                    approval_id = self._queue_approval(tc)
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
        return {
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
        }

    # ------------------------------------------------------------------ #
    # System prompt construction                                           #
    # ------------------------------------------------------------------ #

    def _build_system_prompt(self, domain: str, context: Optional[dict], persona: Optional[str], user_message: str = "") -> str:
        """Build agent system prompt: agent prefix + solution context + live domain context."""
        # Inject solution context if present (blueprint panel provides this)
        solution_block = ""
        if context and context.get("solution_id"):
            solution_block = (
                f"\nACTIVE SOLUTION CONTEXT:\n"
                f"  Solution ID: {context['solution_id']}\n"
                f"  Name: {context.get('solution_name', 'Unknown')}\n"
                f"  ADM Phase: {context.get('current_phase', 'A')}\n"
                f"All create/link tools default to solution_id={context['solution_id']} "
                f"unless the user specifies otherwise.\n"
            )

        # Portfolio context — compact snapshot of related solutions
        portfolio_block = ""
        if context and context.get("solution_id"):
            try:
                from app.modules.ai_chat.services.portfolio_context import PortfolioContextBuilder
                portfolio_block = "\n" + PortfolioContextBuilder().build(
                    solution_id=context["solution_id"],
                    user_id=self.user_id,
                    question=user_message,
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
                ctx_block = self._serialise_context(raw_ctx)
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
        return _AGENT_PREFIX + solution_block + portfolio_block + ctx_block + persona_note

    # ------------------------------------------------------------------ #
    # Provider-specific tool schema conversion                             #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _build_tool_schemas(provider: str, schemas: list) -> list:
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
        Call the LLM with tool schemas through the model service's guards.

        Routes through LLMService._call_llm_with_tools so every model call in
        the loop passes the same budget pre-flight, prompt scrub and usage
        metering as every other model call.
        """
        from app.modules.ai_chat.services.llm_service_impl import LLMService
        from flask import g

        org_id = getattr(g, "current_org_id", None)
        return LLMService._call_llm_with_tools(
            provider=provider,
            model=model,
            api_key=api_key,
            system_prompt=system_prompt,
            messages=messages,
            tools=tools,
            stream=stream,
            base_url=base_url,
            user_id=self.user_id,
            org_id=org_id,
            emit=self._emit if stream else None,
        )

    # ------------------------------------------------------------------ #
    # Message history management                                          #
    # ------------------------------------------------------------------ #

    def _append_tool_results(
        self, provider: str, messages: list, llm_resp: dict, tool_results: list
    ) -> list:
        """Append assistant tool-use blocks and tool results into message history."""
        messages = list(messages)

        if provider == "anthropic":
            # Append the full raw content blocks from the assistant
            messages.append({
                "role": "assistant",
                "content": llm_resp["raw"].content,
            })
            # Append tool results as a user message
            messages.append({
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tc_raw["id"],
                        "content": json.dumps(result, default=str),
                    }
                    for tc_raw, result in tool_results
                ],
            })
        else:  # openai
            # raw is None in streaming mode — reconstruct assistant message from accumulated fields
            if llm_resp.get("raw") is not None:
                raw_msg = llm_resp["raw"].choices[0].message
                assistant_content = raw_msg.content
            else:
                assistant_content = llm_resp.get("text")
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
                    "content": json.dumps(result, default=str),
                })

        return messages

    # ------------------------------------------------------------------ #
    # Approval queue                                                       #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _should_queue(schema: dict, auto_execute: bool) -> bool:
        """True if a tool call must be queued for confirmation, not executed now.

        Every write tool queues an approval row regardless of the auto-execute
        setting.  Two independent reasons, either one is sufficient:
          - tier == "approve": always queued. These are destructive/significant
            regardless of the write-approval gate.
          - mutates is True: always queued.  No write tool runs without an
            approval row until the founder decides otherwise.

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
        return bool(mutates)

    def _queue_approval(self, tc: "ToolCall") -> int:
        """Write a pending AIChatCRUDApproval record and return its ID."""
        from datetime import datetime, timedelta
        from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus
        from app import db

        record = AIChatCRUDApproval(
            user_id=self.user_id,
            operation_type="tool_use",
            entity_type=tc.name,
            original_command=tc.name,
            operation_payload=json.dumps(tc.arguments),
            summary=self._approval_summary(tc),
            status=ApprovalStatus.PENDING,
            expires_at=datetime.utcnow() + timedelta(hours=24),
            chat_session_id=self.chat_session_id,
            agent_turn_id=self._turn_id,
        )
        db.session.add(record)
        db.session.commit()
        return record.id

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

    def _fallback(self, reason: str) -> dict:
        reason_l = (reason or "").lower()
        if any(t in reason_l for t in ("no api key", "not configured", "no provider", "no keys")):
            msg = (
                "AI features aren't configured yet — add an API key under "
                "Admin → API Settings to enable the assistant."
            )
        elif any(
            t in reason_l
            for t in ("quota", "insufficient", "rate limit", "429", "billing", "exceeded")
        ):
            msg = (
                "The AI provider rejected the request (rate limit or quota exceeded). "
                "Check the provider's plan/billing, or switch providers in "
                "Admin → API Settings."
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

    def _text_only_fallback(
        self, message: str, system_prompt: str, provider: str, model: str, api_key: str, LLMService
    ) -> dict:
        """Run a plain text call (no tools) for unsupported providers."""
        try:
            prompt = system_prompt + "\n\nUser: " + message
            text, _ = LLMService._call_llm_with_failover(
                prompt=prompt, model=model, provider=provider
            )
            return {
                "response": text,
                "actions_taken": [],
                "pending_approvals": [],
            }
        except Exception as e:
            return self._fallback(str(e))
