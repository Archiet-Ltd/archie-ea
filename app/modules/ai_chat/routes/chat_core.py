"""
Core chat messaging, model selection, sessions, and configuration routes.

Routes: message, models, domains, personas, templates, context,
        history, history/clear, session/save, sessions, session/<id>.
"""

import logging

from flask import Response, current_app, g, jsonify, request, stream_with_context
from flask_login import current_user, login_required

from app.decorators import audit_log
from app.models.ai_service import AIPromptTemplate
from app.services.feature_flag_service import FeatureFlagService
from app.services.rate_limiter import rate_limit
from app.utils.validators import (
    sanitize_html,
    validate_chat_message,
    validate_enum,
    validate_integer,
    validate_string,
    validation_error_response,
)
from app.schemas.api_schemas import ChatMessageSchema, _load_and_validate
from . import unified_ai_chat_bp
from .chat_views import get_chat_service

logger = logging.getLogger(__name__)

# How long the SSE generator waits on an empty event_queue before emitting a
# keepalive. Module-level (not a literal in generate()) so a test can shrink
# it rather than actually sleeping for the production interval.
#
# Must stay comfortably BELOW the client's idle-read timeout
# (app/static/js/ai_chat/transport.js's STREAM_IDLE_TIMEOUT_MS, 30s): a turn
# with no tokens for a while (slow model, tool-heavy turn with silent gaps
# between tool calls) is legitimate, not a wedge, and used to look identical
# to one from the client's side because the ONLY thing standing between it
# and a false "AI service did not respond" was this same queue.get() at a
# 95s timeout - well inside the 30s window it was supposed to protect. At
# 15s, a client that has gone quiet for 30s has missed at least one
# keepalive it should have received, which is what actually distinguishes
# "slow" from "wedged" rather than merely how long since the turn started.
_STREAM_KEEPALIVE_INTERVAL_S = 15


def _bind_trusted_workbench(workspace_candidate, requested_solution_id=None):
    """Validate and persist a client workspace candidate as trusted server state."""
    from flask import session as flask_session
    from app import db
    from app.models.solution_architect_models import SolutionAnalysisSession
    from app.models.solution_models import Solution

    state = flask_session.get("_workbench_workflow_state") or {}
    candidate = workspace_candidate if workspace_candidate is not None else state.get("workspace_id")
    if candidate is None:
        return None, None
    valid, workspace_id, _ = validate_integer(candidate, min_val=1, field_name="workspace_id")
    if not valid:
        flask_session.pop("_workbench_workflow_state", None)
        return None, ({"success": False, "reason_codes": ["workspace_not_found"],
                       "missing_evidence": []}, 404)

    organization_id = getattr(g, "current_org_id", None)
    workspace = db.session.execute(
        db.select(SolutionAnalysisSession).where(
            SolutionAnalysisSession.id == workspace_id,
            SolutionAnalysisSession.organization_id == organization_id,
            SolutionAnalysisSession.created_by_id == current_user.id,
        )
    ).scalar_one_or_none()
    if workspace is None:
        flask_session.pop("_workbench_workflow_state", None)
        return None, ({"success": False, "reason_codes": ["workspace_not_found"],
                       "missing_evidence": []}, 404)

    solution_id = (workspace.custom_metadata or {}).get("solution_id")
    if not solution_id:
        flask_session.pop("_workbench_workflow_state", None)
        return None, ({"success": False, "reason_codes": ["workspace_solution_missing"],
                       "missing_evidence": []}, 422)
    solution = db.session.execute(
        db.select(Solution).where(
            Solution.id == solution_id,
            Solution.organization_id == organization_id,
        )
    ).scalar_one_or_none()
    if solution is None or (
        requested_solution_id is not None and requested_solution_id != solution_id
    ):
        flask_session.pop("_workbench_workflow_state", None)
        return None, ({"success": False, "reason_codes": ["workspace_solution_mismatch"],
                       "missing_evidence": []}, 422)

    state.update({"workspace_id": workspace.id, "solution_id": solution.id})
    flask_session["_workbench_workflow_state"] = state
    return workspace.id, None


def _get_configured_chat_models():
    """Return models that are actually selectable in AI chat."""
    from app.models.models import APISettings

    providers = APISettings.query.filter_by(enabled=True).all()
    models = []

    for provider in providers:
        provider_name = (provider.provider or "").strip().lower()
        if not provider_name:
            continue

        if provider_name != "huggingface" and not provider.has_key():
            continue

        raw_models = [
            m.strip() for m in (provider.default_model or "").split(",") if m and m.strip()
        ]
        if not raw_models:
            continue

        for order, model_id in enumerate(raw_models):
            model_info = {
                "provider": provider_name,
                "model": model_id,
                "display_name": f"{provider_name.title()} - {model_id}",
                "recommended_for": [],
                "fallback_order": order,
                "is_fallback": order > 0,
                "test_status": provider.test_status,
                "last_tested_at": provider.last_tested_at.isoformat()
                if provider.last_tested_at
                else None,
            }

            if provider_name == "openai" and "gpt-4o" in model_id:
                model_info["recommended_for"] = ["General conversation", "Complex analysis"]
            elif provider_name == "huggingface" and "flan-t5" in model_id:
                model_info["recommended_for"] = [
                    "Enterprise architecture",
                    "Instruction following",
                ]
            elif provider_name == "anthropic":
                model_info["recommended_for"] = ["Complex reasoning", "Analysis"]
            elif provider_name == "gemini":
                model_info["recommended_for"] = ["Multi-modal", "General tasks"]
            elif provider_name == "deepseek":
                model_info["recommended_for"] = ["Code analysis", "Technical tasks"]

            models.append(model_info)

    # Runtime provider resolution deliberately supports environment-backed
    # bootstrap configuration.  A tenant with no APISettings rows must see the
    # same provider that the message service will actually use; otherwise the
    # model picker reports "unavailable" while chat health is green.
    if not models:
        from app.modules.ai_chat.services.llm_service_impl import LLMService

        try:
            provider_name, model_id = LLMService._get_configured_provider()
        except ValueError:
            provider_name, model_id = None, None

        if provider_name and model_id:
            models.append({
                "provider": provider_name,
                "model": model_id,
                "display_name": f"{provider_name.title()} - {model_id}",
                "recommended_for": [],
                "fallback_order": 0,
                "is_fallback": False,
                "test_status": None,
                "last_tested_at": None,
            })

    return models


def _agent_auto_execute_allowed():
    """Whether the agent may execute a mutating tool without a confirmation.

    The session flag is a USER PREFERENCE. REQUIRE_AI_APPROVAL is an OPERATOR
    CONTROL. A preference must never defeat a control, and until 31 Aug 2026 it
    did: the agent loop read the session flag alone and never consulted the
    config at all, while config.py stated the flag gated "the LLM-agent
    mutating-tool queue" and that setting it false "restores the pre-Aug-2026
    direct-write behaviour for the LLM-agent paths".

    Both statements were false for the agent path. An operator who set
    REQUIRE_AI_APPROVAL=true believed AI-proposed writes went to a human queue;
    any authenticated user could POST /session/toggle-auto-execute once and
    every tier:"auto" mutating tool -- create_solution, create_archimate_element,
    create_driver/goal/constraint/requirement/risk, the link_* family -- then
    executed immediately with no approval row. Found 31 Aug 2026 by an AI
    architecture audit; the /ai-chat/data/* routes were correctly gated the
    whole time, which is why it was invisible.

    Fails CLOSED: an unreadable config is treated as "approval required".
    """
    if current_app.config.get("REQUIRE_AI_APPROVAL", True):
        return False
    from flask import session as flask_session

    return bool(flask_session.get("agent_auto_execute", False))


@unified_ai_chat_bp.route("/persona-context/<persona>", methods=["GET"])
@login_required
def get_persona_context(persona):
    """AI-1: inspect what an AI Architect persona sees (charter + live data).

    Lets architects audit the persona's evidence base, and lets E2E verify
    the live-context pipeline without an LLM call.
    """
    from app.modules.ai_chat.services.architect_persona_charters import (
        ARCHITECT_PERSONAS,
        CHARTERS,
        get_live_context,
    )

    if persona not in ARCHITECT_PERSONAS:
        return jsonify({
            "success": False,
            "error": f"Not an AI Architect persona. Available: {list(ARCHITECT_PERSONAS)}",
        }), 404
    return jsonify({
        "success": True,
        "persona": persona,
        "charter_chars": len(CHARTERS.get(persona, "")),
        "live_context": get_live_context(persona),
    })


@unified_ai_chat_bp.route("/models", methods=["GET"])
@login_required
def get_available_models():
    """
    Get available AI models
    ---
    tags:
      - AI Chat
    summary: List available LLM models
    description: Get all available LLM models configured and enabled in the system
    responses:
      200:
        description: Available models list
        schema:
          type: object
          properties:
            success:
              type: boolean
            models:
              type: array
              items:
                type: object
                properties:
                  provider:
                    type: string
                  model:
                    type: string
                  display_name:
                    type: string
                  recommended_for:
                    type: array
                    items:
                      type: string
            current_provider:
              type: string
            current_model:
              type: string
    """
    try:
        models = _get_configured_chat_models()

        return jsonify(
            {
                "success": True,
                "models": models,
                "current_provider": models[0]["provider"] if models else None,
                "current_model": models[0]["model"] if models else None,
            }
        )

    except Exception as e:
        current_app.logger.error(f"Error getting available models: {str(e)}")
        return (
            jsonify(
                {
                    "success": False,
                    "models": [],
                    "current_provider": None,
                    "current_model": None,
                    "error": "model_configuration_unavailable",
                    "message": "Could not load configured AI models.",
                }
            ),
            500,
        )


def _load_history(thread_id, user_id):
    """Prior turns of *thread_id*, oldest first, or [] when there are none.

    Ownership-checked: thread_id arrives from the client, so without the check a
    user could replay another user's conversation into their own prompt simply
    by passing someone else's id.

    Never raises. History is an enhancement to the turn, not a precondition for
    it - a failure here should cost the model its memory, not cost the user their
    answer.
    """
    if not thread_id or not user_id:
        return []
    try:
        from app.services.conversation_history import get_history_service, thread_owned_by

        if not thread_owned_by(thread_id, user_id):
            logger.warning("chat history requested for a thread the user does not own")
            return []
        messages = get_history_service().get_thread_messages(thread_id)
        return [
            {"role": m.role, "content": m.content}
            for m in messages
            if getattr(m, "role", None) in ("user", "assistant") and getattr(m, "content", None)
        ]
    except Exception:
        logger.warning("chat history unavailable; continuing without it", exc_info=True)
        return []


@unified_ai_chat_bp.route("/message", methods=["POST"])
@login_required
@rate_limit(30, "1h")  # LLM-003: 30 requests per hour for chat operations
@audit_log("send_chat_message")
def send_message():
    """
    Send chat message
    ---
    tags:
      - AI Chat
    summary: Send a message to the AI assistant
    description: Send a message to the AI chat system with optional domain context and persona
    parameters:
      - name: body
        in: body
        required: true
        schema:
          type: object
          required:
            - message
          properties:
            message:
              type: string
              description: User message content
            domain:
              type: string
              default: general
              description: Domain context (general, capabilities, applications, etc.)
            template_name:
              type: string
              default: General Inquiry
            element_id:
              type: integer
              description: Context element ID
            context_type:
              type: string
              description: Type of context element
            persona:
              type: string
              description: AI persona to use
            model:
              type: string
              description: Specific model to use
    responses:
      200:
        description: AI response
        schema:
          type: object
          properties:
            success:
              type: boolean
            response:
              type: string
            domain:
              type: string
            template:
              type: string
      400:
        description: Message content is required
      500:
        description: Server error
    """

    data = request.json
    if data and "template" in data and "template_name" not in data:
        data = dict(data)
        data["template_name"] = data.pop("template")

    # Validate JSON payload exists
    if data is None:
        return validation_error_response("Request body is required")

    # Capture the conversation this turn belongs to (None on the first message),
    # then REMOVE it from the payload — the request schema is strict
    # (unknown=RAISE), so leaving thread_id in would 400 the whole message.
    incoming_thread_id = data.get("thread_id")
    if isinstance(data, dict) and "thread_id" in data:
        data = dict(data)
        data.pop("thread_id", None)

    # T-031: marshmallow schema validation
    _schema = ChatMessageSchema()
    _validated, _err = _load_and_validate(_schema, data)
    if _err is not None:
        return _err
    data = _validated

    # Check if AI chat feature is enabled
    feature_guard = FeatureFlagService.require_ai_for_route(
        FeatureFlagService.FEATURE_CHAT, endpoint_name="send_message"
    )
    if feature_guard:
        return feature_guard

    # Validate and sanitize message content
    user_message = data.get("message")
    is_valid, validated_message, error = validate_chat_message(user_message)
    if not is_valid:
        return validation_error_response(error)
    user_message = validated_message

    # ENT-082: Check for slash commands before LLM processing
    from app.modules.ai_chat.services.command_parser_service import CommandParserService

    cmd_service = CommandParserService()
    if cmd_service.is_command(user_message):
        result = cmd_service.parse(user_message)
        if result["valid"]:
            domain_for_cmd = data.get("domain", "general")
            cmd_result = cmd_service.execute(
                result["command"], result["args"], current_user.id, domain_for_cmd
            )
            return jsonify({
                "success": True,
                "response": cmd_result["response"],
                "command": True,
                "domain": cmd_result.get("domain", domain_for_cmd),
            })
        else:
            return jsonify({
                "success": True,
                "response": result["error"],
                "command": True,
                "domain": data.get("domain", "general"),
            })

    # Validate domain field — must match MultiDomainChatService.domains keys
    domain = data.get("domain", "general")
    valid_domains = [
        "general",
        "architecture",
        "technology",
        "business_capability",
        "gap_analysis",
        "vendor_intelligence",
        "smart_search",
        "compliance",
    ]
    is_valid, validated_domain, error = validate_enum(
        domain, valid_domains, field_name="domain", case_insensitive=True
    )
    if not is_valid:
        domain = "general"  # Default to general if invalid
    else:
        domain = validated_domain

    # Validate template_name
    template_name = data.get("template_name", "General Inquiry")
    is_valid, validated_template, error = validate_string(
        template_name, max_length=100, field_name="template_name"
    )
    if is_valid and validated_template:
        template_name = sanitize_html(validated_template)

    # Validate context element ID (optional integer)
    context_element_id = data.get("element_id")
    if context_element_id is not None:
        is_valid, validated_id, error = validate_integer(
            context_element_id, min_val=1, field_name="element_id"
        )
        if not is_valid:
            return validation_error_response(error)
        context_element_id = validated_id

    # Validate EA workflow instance ID (AIC-001: grounds chat in a specific ADM phase context)
    workflow_instance_id = data.get("instance_id")
    if workflow_instance_id is not None:
        is_valid, validated_instance_id, error = validate_integer(
            workflow_instance_id, min_val=1, field_name="instance_id"
        )
        if not is_valid:
            workflow_instance_id = None
        else:
            workflow_instance_id = validated_instance_id

    # Validate context_type
    context_type = data.get("context_type")
    if context_type:
        is_valid, validated_ctx_type, error = validate_string(
            context_type, max_length=50, field_name="context_type"
        )
        if not is_valid:
            return validation_error_response(error)
        context_type = sanitize_html(validated_ctx_type)

    # Validate solution_id — grounds chat in a specific solution context
    solution_id = data.get("solution_id")
    if solution_id is not None:
        is_valid, validated_sol_id, error = validate_integer(
            solution_id, min_val=1, field_name="solution_id"
        )
        solution_id = validated_sol_id if is_valid else None

    trusted_workspace_id, workspace_error = _bind_trusted_workbench(
        data.get("workspace_id"), solution_id
    )
    if workspace_error:
        body, status = workspace_error
        return jsonify(body), status

    # Validate persona
    persona = data.get("persona")
    if persona:
        is_valid, validated_persona, error = validate_string(
            persona, max_length=50, field_name="persona"
        )
        if not is_valid:
            return validation_error_response(error)
        persona = sanitize_html(validated_persona)

    # Validate model selection
    requested_model = data.get("model")
    if requested_model:
        is_valid, validated_model, error = validate_string(
            requested_model, max_length=100, field_name="model"
        )
        if not is_valid:
            return validation_error_response(error)
        requested_model = sanitize_html(validated_model)
        available_models = {m["model"] for m in _get_configured_chat_models()}
        if requested_model not in available_models:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "invalid_model",
                        "message": f"Model '{requested_model}' is not available.",
                    }
                ),
                400,
            )

    try:
        # Get multi-domain chat service
        get_chat_service()

        # Prepare context data
        context_data = {}
        if context_element_id and context_type:
            context_data = {
                "element_id": context_element_id,
                "context_type": context_type,
                "domain": domain,
            }

        # Inject EA workflow instance context so chat is grounded in ADM phase (AIC-001)
        if workflow_instance_id:
            context_data["instance_id"] = workflow_instance_id

        # Ground chat in a specific solution when solution_id is provided
        if solution_id:
            context_data["solution_id"] = solution_id

        # Add document context from uploaded documents
        document_context = data.get("document_context", {})
        if document_context and isinstance(document_context, dict):
            context_data.update({
                key: value
                for key, value in document_context.items()
                if key not in {
                    "workspace_id",
                    "_trusted_workspace_id",
                    "arb_assertions",
                    "workflow_type",
                }
            })

        # ENT-085: Pass attached image data for vision/multimodal analysis
        # The chat client no longer sends these. AgentRunner — which is what
        # actually runs the turn — has no vision handling, so the base64 landed
        # in context_data and was dropped by every context loader. The user saw
        # a thumbnail and got a confident answer generated as if no diagram had
        # been attached. The UI was removed rather than left lying; this stays
        # as the reattachment point, and is inert while nothing sends it.
        image_data = data.get("image_data")
        if image_data and isinstance(image_data, str):
            context_data["image_data"] = image_data
            context_data["image_media_type"] = data.get("image_media_type", "image/png")

        # Run AgentRunner (ReAct loop with tool use).
        # Falls back to text-only mode automatically for unsupported providers.
        from app.modules.ai_chat.services.agent_runner import (
            AgentRunner,
            sanitize_agent_error,
        )

        if trusted_workspace_id is not None:
            context_data["workspace_id"] = trusted_workspace_id
            context_data["_trusted_workspace_id"] = trusted_workspace_id

        runner = AgentRunner(
            user_id=current_user.id,
            auto_execute=_agent_auto_execute_allowed(),
            # ARCH-020: prefer the real thread id when the client sent one;
            # otherwise fall back to the same per-user stable id
            # MultiDomainChatService uses (self._stable_session_id there), so
            # any approval this run queues is still traceable to *a* session
            # instead of chat_session_id=null.
            chat_session_id=incoming_thread_id or f"chat_user_{current_user.id}",
        )
        agent_result = runner.run(
            user_message=user_message,
            domain=domain,
            context=context_data,
            persona=persona,
            requested_model=requested_model,
            history=_load_history(incoming_thread_id, current_user.id),
        )

        # ENH-018: Populate junction tables if agent result includes design_output
        if solution_id and agent_result.get("metadata", {}).get("design_output"):
            try:
                from app.modules.architecture.routes.architecture_assistant_routes import (
                    populate_solution_junctions,
                )
                populate_solution_junctions(
                    solution_id, agent_result["metadata"]["design_output"]
                )
            except Exception as _junc_err:
                logger.debug("ENH-018: Junction populate skipped: %s", _junc_err)

        # Persist this turn (thread-backed history rail). Always on; never
        # breaks the reply if storage hiccups. thread_id rides back so the next
        # turn appends to the same conversation.
        thread_id = incoming_thread_id
        try:
            from app.services.conversation_history import persist_turn

            thread_id = persist_turn(
                current_user.id,
                incoming_thread_id,
                user_message,
                agent_result.get("response", ""),
                model=requested_model or (agent_result.get("metadata", {}) or {}).get("model"),
            )
        except Exception:
            logger.warning("send_message persist_turn failed", exc_info=True)

        # A failed LLM call is not a success. This used to return
        # "success": true alongside agent_error, so any client reading the
        # envelope — and any log or metric counting 2xx+success — recorded a
        # failure as a working answer. The HTTP status stays 200 and `response`
        # still carries the honest "couldn't be completed" copy, because the
        # user does have something to read; it is the envelope that was lying.
        agent_failure = agent_result.get("error")
        if agent_failure:
            logger.warning("send_message agent failure: %s", agent_failure)

        return jsonify(
            {
                "success": not agent_failure,
                "response": agent_result.get("response", ""),
                "domain": domain,
                "actions_taken": agent_result.get("actions_taken", []),
                "pending_approvals": agent_result.get("pending_approvals", []),
                "requires_approval": bool(agent_result.get("pending_approvals")),
                # The records the read tools actually returned this turn, so the
                # reader can check the answer against the rows rather than
                # trusting it.
                "sources": agent_result.get("sources", []),
                # AgentRunner._fallback() persists a friendly "couldn't be
                # completed" response AND keeps the raw failure reason on the
                # same dict; without this field the UI could not tell an
                # ordinary answer from one that only exists because the LLM
                # call failed.
                #
                # Sanitised: the raw reason is the provider's error body, which
                # for OpenRouter's 402 contains the provider account's user_id.
                # That was reaching the browser verbatim. The full reason is in
                # the server log line above.
                "agent_error": sanitize_agent_error(agent_failure),
                # Was hardcoded True on every response regardless of whether any
                # context was built - _build_system_prompt swallows a context
                # failure into an empty string, so the API asserted grounding
                # unconditionally. Report what actually happened.
                "context_used": bool(agent_result.get("sources")),
                "workspace_id": trusted_workspace_id,
                "thread_id": thread_id,
                "processing_metadata": {
                    "domain": domain,
                    "persona": persona,
                    "agent_mode": True,
                },
            }
        )

    except ValueError as e:
        current_app.logger.error(f"Validation error in chat message: {str(e)}")
        return jsonify({"error": "Invalid input", "error_type": "validation"}), 400
    except ConnectionError as e:
        current_app.logger.error(f"LLM connection error: {str(e)}")
        return (
            jsonify(
                {
                    "error": "Unable to connect to AI service. Please check your API configuration in Admin > API Settings.",
                    "error_type": "connection",
                }
            ),
            503,
        )
    except TimeoutError as e:
        current_app.logger.error(f"LLM timeout error: {str(e)}")
        return (
            jsonify(
                {
                    "error": "AI service request timed out. Please try again or use a shorter message.",
                    "error_type": "timeout",
                }
            ),
            504,
        )
    except Exception as e:
        error_msg = str(e)
        current_app.logger.error(
            f"Error processing chat message: {error_msg}", exc_info=True
        )

        # Provide more specific error messages based on error content
        if "api_key" in error_msg.lower() or "authentication" in error_msg.lower():
            return (
                jsonify(
                    {
                        "error": "AI service authentication failed. Please verify your API key in Admin > API Settings.",
                        "error_type": "auth",
                    }
                ),
                401,
            )
        elif "rate limit" in error_msg.lower() or "quota" in error_msg.lower():
            return (
                jsonify(
                    {
                        "error": "AI service rate limit exceeded. Please wait a moment and try again.",
                        "error_type": "rate_limit",
                    }
                ),
                429,
            )
        elif "model" in error_msg.lower() and (
            "not found" in error_msg.lower() or "invalid" in error_msg.lower()
        ):
            return (
                jsonify(
                    {
                        "error": "The selected AI model is not available. Please select a different model.",
                        "error_type": "model_error",
                    }
                ),
                400,
            )
        else:
            return (
                jsonify(
                    {
                        "error": "Failed to process message. Please try again.",
                        "error_type": "unknown",
                        "details": error_msg if current_app.debug else None,
                    }
                ),
                500,
            )


@unified_ai_chat_bp.route("/feedback", methods=["POST"])
@login_required
def submit_message_feedback():
    """Record thumbs up/down feedback on an AI message."""
    data = request.json or {}
    rating = data.get("rating")  # 'up' or 'down'
    domain = data.get("domain", "general")
    persona = data.get("persona", "")
    message_text = data.get("message_text", "")[:500]  # cap length

    if rating not in ("up", "down"):
        return jsonify({"error": "rating must be 'up' or 'down'"}), 400

    from app import db

    try:
        from app.models.ai_chat_feedback import AIChatFeedback

        # organization_id is set by the tenant before_flush; do not pass it.
        db.session.add(AIChatFeedback(
            user_id=current_user.id,
            rating=rating,
            domain=domain,
            persona=persona,
            message_text=message_text,
        ))
        db.session.commit()
    except Exception:
        # This used to swallow every failure, log at INFO and return
        # success: True. organization_id was declared on no model and in no
        # migration, so the raw INSERT that referenced it raised
        # UndefinedColumn every time — the endpoint reported success for a
        # write that never happened, and without a rollback the aborted
        # transaction cascaded InFailedSqlTransaction into every later query
        # on the request.
        db.session.rollback()
        current_app.logger.exception("ai_chat_feedback insert failed")
        return jsonify({"error": "Feedback could not be saved"}), 500

    return jsonify({"success": True, "rating": rating})


@unified_ai_chat_bp.route("/message/stream", methods=["POST"])
@login_required
@rate_limit(30, "1h")
def send_message_stream():
    """
    Stream a chat message via Server-Sent Events.
    Uses AgentRunner with stream_mode=True — tokens arrive as they are generated.
    Events: token | tool_start | tool_result | approval_queued | done
    """
    import queue as _queue
    import threading as _threading

    data = request.json or {}
    if not data:
        return validation_error_response("Request body is required")

    # Capture the conversation this turn belongs to, then REMOVE it from the
    # payload — the schema is strict (unknown=RAISE) and would 400 otherwise.
    incoming_thread_id = data.get("thread_id")
    if isinstance(data, dict) and "thread_id" in data:
        data = dict(data)
        data.pop("thread_id", None)

    _schema = ChatMessageSchema()
    _validated, _err = _load_and_validate(_schema, data)
    if _err is not None:
        return _err
    data = _validated

    feature_guard = FeatureFlagService.require_ai_for_route(
        FeatureFlagService.FEATURE_CHAT, endpoint_name="send_message_stream"
    )
    if feature_guard:
        return feature_guard

    user_message = data.get("message", "")
    is_valid, validated_message, error = validate_chat_message(user_message)
    if not is_valid:
        return validation_error_response(error)
    user_message = validated_message

    domain = data.get("domain", "general")
    valid_domains = [
        "general", "architecture", "technology", "business_capability",
        "gap_analysis", "vendor_intelligence", "smart_search", "compliance",
    ]
    is_valid_d, validated_domain, _ = validate_enum(domain, valid_domains, case_insensitive=True)
    domain = validated_domain if is_valid_d else "general"

    persona = sanitize_html(data.get("persona") or "")
    requested_model = sanitize_html(data.get("model") or "")

    context_data = {}
    solution_id = None
    solution_id = data.get("solution_id")
    if solution_id:
        is_valid_s, validated_sol, _ = validate_integer(solution_id, min_val=1, field_name="solution_id")
        if is_valid_s:
            solution_id = validated_sol
            context_data["solution_id"] = solution_id
    trusted_workspace_id, workspace_error = _bind_trusted_workbench(
        data.get("workspace_id"), solution_id
    )
    if workspace_error:
        body, status = workspace_error
        return jsonify(body), status
    event_queue = _queue.Queue()

    def emit(e):
        event_queue.put(e)

    app = current_app._get_current_object()
    user_id_for_thread = current_user.id if current_user.is_authenticated else None

    # Capture the tenant NOW, while we are still on the request thread.
    #
    # app/middleware/tenant_context.py sets g.current_org_id in a before_request
    # handler, and app/middleware/tenant_isolation.py returns without adding any
    # filter when it is absent. The worker below runs in a daemon thread with only
    # an app context - stream_with_context preserves the request context for the
    # SSE *generator*, not for this thread - so without this every TenantMixin
    # SELECT executed while building the prompt and running the agent's tools ran
    # UNFILTERED ACROSS EVERY ORGANISATION. That included APISettings, so provider
    # selection could pick up another tenant's LLM API key and send this
    # organisation's prompts under, and billed to, that account.
    #
    # This is the default path: the UI tries /message/stream first and only falls
    # back to /message on failure.
    from app.middleware.tenant_context import current_org_id as _current_org_id

    org_id_for_thread = _current_org_id()
    # Same reason as the tenant capture above: the write-approval preference
    # lives in flask.session, which the background thread below does not have
    # (only an app context is re-established there, not a request context).
    # Read it here, on the request thread, and pass the plain bool down.

    auto_execute_for_thread = _agent_auto_execute_allowed()
    if trusted_workspace_id is not None:
        context_data["workspace_id"] = trusted_workspace_id
        context_data["_trusted_workspace_id"] = trusted_workspace_id
    # Load history here too, on the request thread, so the worker starts with the
    # conversation already in hand rather than paying a DB round-trip inside the
    # stream.
    history_for_thread = _load_history(incoming_thread_id, user_id_for_thread)
    if org_id_for_thread is None:
        # Reproduce the request's own tenant context exactly rather than guessing
        # an org - but say so, because an authenticated chat turn should have one.
        logger.warning(
            "chat stream: no tenant context on the request; the agent thread will "
            "run with the same unscoped context this user would see in-request"
        )

    # The assistant run on the worker thread keeps this request's trace id, so
    # its queries and model calls are followed from the same id.
    from app.utils.tracing import current_trace_id, trace_scope

    trace_id_for_thread = current_trace_id()

    def run_agent():
        try:
            with app.app_context(), trace_scope(
                "agent-run", "assistant", trace_id=trace_id_for_thread
            ):
                # Re-establish the tenant before anything queries. Must precede
                # AgentRunner construction: context assembly reads on import-time
                # paths inside run().
                g.current_org_id = org_id_for_thread

                from app.modules.ai_chat.services.agent_runner import (
                    AgentRunner,
                    sanitize_agent_error,
                )
                runner = AgentRunner(
                    user_id=user_id_for_thread,
                    yield_event=emit,
                    auto_execute=auto_execute_for_thread,
                    # ARCH-020: same reasoning as the non-streaming send_message
                    # path above — real thread id when available, else the
                    # per-user stable fallback, so approvals from this run are
                    # never orphaned with chat_session_id=null.
                    chat_session_id=incoming_thread_id or f"chat_user_{user_id_for_thread}",
                )
                result = runner.run(
                    user_message=user_message,
                    domain=domain,
                    context=context_data,
                    persona=persona or None,
                    requested_model=requested_model or None,
                    stream_mode=True,
                    history=history_for_thread,
                )
                # Persist this turn (thread-backed history rail). Always on;
                # never breaks the stream. thread_id rides back on the done event.
                try:
                    from app.services.conversation_history import persist_turn

                    result["thread_id"] = persist_turn(
                        user_id_for_thread,
                        incoming_thread_id,
                        user_message,
                        result.get("response", ""),
                        model=requested_model or (result.get("metadata", {}) or {}).get("model"),
                    )
                except Exception:
                    logger.warning("stream persist_turn failed", exc_info=True)
                # Same leak as the non-streaming path: result["error"] is the
                # provider's raw error body (OpenRouter's 402 carries the
                # provider account's user_id). Log it, send a category.
                if result.get("error"):
                    logger.warning("stream agent failure: %s", result["error"])
                    result["error"] = sanitize_agent_error(result["error"])
                event_queue.put({"type": "done", **result})
        except Exception as exc:
            logger.exception("send_message_stream: agent error")
            from app.modules.ai_chat.services.agent_runner import (
                sanitize_agent_error as _sanitize,
            )
            event_queue.put({
                "type": "done",
                "response": "",
                "error": _sanitize(str(exc)),
                "actions_taken": [],
                "pending_approvals": [],
            })

    _threading.Thread(target=run_agent, daemon=True).start()

    def generate():
        import json as _json
        # A real `data:` event, not a `:`-comment: the client's idle timer
        # resets on any byte it reads regardless of what it parses to, but an
        # actual event is what makes this independently testable and what
        # the plan asks for ("periodic server keepalive events"), rather
        # than relying on an implementation detail of the client's timer.
        yield f"data: {_json.dumps({'type': 'keepalive'})}\n\n"
        while True:
            try:
                event = event_queue.get(timeout=_STREAM_KEEPALIVE_INTERVAL_S)
                yield f"data: {_json.dumps(event, default=str)}\n\n"
                if event.get("type") == "done":
                    break
            except _queue.Empty:
                yield f"data: {_json.dumps({'type': 'keepalive'})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def _generate_follow_up_questions_simple(message: str, domain: str, persona: str) -> list:
    """Generate simple follow-up question suggestions (used by streaming endpoint)."""
    domain_qs = {
        "architecture": [
            "What ArchiMate relationships should I define?",
            "How does this align with TOGAF ADM?",
            "Which elements have the highest technical debt?",
        ],
        "technology": [
            "Which applications are at end-of-life?",
            "What integration patterns are recommended?",
            "Which applications lack a business owner?",
        ],
        "business_capability": [
            "Which capabilities have the lowest maturity?",
            "How do these map to strategic objectives?",
            "Which APQC processes are unmapped?",
        ],
        "gap_analysis": [
            "What is the remediation priority?",
            "Which gaps have highest business impact?",
            "Are there vendor solutions to close these gaps?",
        ],
        "vendor_intelligence": [
            "What is the 3-year TCO comparison?",
            "Which vendors expire in 90 days?",
            "Which vendor has the best capability fit?",
        ],
        "general": [
            "Can you give an executive summary?",
            "What are the top 3 risks to address?",
            "What should be the next step in my review?",
        ],
    }
    persona_qs = {
        "cio": ["What investment is required?", "What is the compliance risk?"],
        "enterprise_architect": [
            "How does this fit the Target Architecture?",
            "Which TOGAF deliverables should I update?",
        ],
        "business_analyst": [
            "How does this affect requirements?",
            "Which stakeholders need to be informed?",
        ],
    }
    candidates = persona_qs.get(persona, []) + domain_qs.get(domain, domain_qs["general"])
    return candidates[:3]


@unified_ai_chat_bp.route("/token-usage", methods=["GET"])
@login_required
def get_token_usage():
    """
    Get current token usage for the active chat session.
    ---
    tags:
      - AI Chat
    summary: Get token usage statistics
    description: >
        Returns estimated token count for the current user's chat history
        and the context-window limit for the active LLM provider.
    security:
      - cookieAuth: []
    responses:
      200:
        description: Token usage statistics
        schema:
          type: object
          properties:
            success:
              type: boolean
            token_usage:
              type: object
              properties:
                total_tokens:
                  type: integer
                limit:
                  type: integer
                percentage:
                  type: number
                warning:
                  type: string
            provider:
              type: string
      500:
        description: Server error
    """
    try:
        from app.modules.ai_chat.services.context_window_service import (
            ContextWindowService,
        )
        from app.services.llm_service import LLMService

        chat_service = get_chat_service()
        history = chat_service.get_chat_history(
            current_user.id if current_user.is_authenticated else None
        )

        try:
            provider_name, _ = LLMService._get_configured_provider()
        except ValueError:
            # No LLM provider configured is a settled configuration state, not a failure.
            # Counts are None (unknowable without a provider tokenizer), never a 0 that
            # would read as a measured zero.
            # error-signalling-ok: configuration-state verdict, not a failure; the caller reads configured=false and renders the warning
            return jsonify({
                "success": True,
                "token_usage": {
                    "total_tokens": None,
                    "limit": None,
                    "percentage": None,
                    "warning": "No enabled LLM provider is configured.",
                },
                "provider": None,
                "message_count": len(history),
                "configured": False,
            })

        ctx_svc = ContextWindowService()
        usage = ctx_svc.get_usage_info(history, provider_name)

        return jsonify({
            "success": True,
            "token_usage": usage,
            "provider": provider_name,
            "message_count": len(history),
            "configured": True,
        })
    except Exception as e:
        current_app.logger.error(f"Error getting token usage: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "error": "Failed to compute token usage",
        }), 500


@unified_ai_chat_bp.route("/domains", methods=["GET"])
@login_required
def get_available_domains():
    """Get available chat domains."""
    try:
        chat_service = get_chat_service()
        domains = chat_service.get_available_domains()
        return jsonify({"success": True, "domains": domains})
    except Exception:
        return jsonify(
            {"error": "Failed to get domains", "details": "See server logs for details"}
        ), 500


@unified_ai_chat_bp.route("/personas", methods=["GET"])
@login_required
def get_available_personas():
    """
    Get available personas
    ---
    tags:
      - AI Chat
    summary: List available AI personas
    description: Get all available chat personas for different interaction styles
    security:
      - cookieAuth: []
    responses:
      200:
        description: Available personas
        schema:
          type: object
          properties:
            success:
              type: boolean
            personas:
              type: array
              items:
                type: object
      401:
        description: Unauthorized
      500:
        description: Server error
    """
    try:
        chat_service = get_chat_service()
        personas = chat_service.get_available_personas()
        return jsonify({"success": True, "personas": personas})
    except Exception:
        return jsonify(
            {
                "error": "Failed to get personas",
                "details": "See server logs for details",
            }
        ), 500


@unified_ai_chat_bp.route("/templates", methods=["GET"])
@login_required
def get_prompt_templates():
    """
    Get prompt templates
    ---
    tags:
      - AI Chat
    summary: List available prompt templates
    description: Get all available AI prompt templates for different domains
    security:
      - cookieAuth: []
    responses:
      200:
        description: Available templates
        schema:
          type: object
          properties:
            success:
              type: boolean
            templates:
              type: array
              items:
                type: object
                properties:
                  id:
                    type: integer
                  name:
                    type: string
                  description:
                    type: string
                  domain:
                    type: string
                  template_text:
                    type: string
      401:
        description: Unauthorized
      500:
        description: Server error
    """
    try:
        templates = AIPromptTemplate.query.all()
        template_list = []
        for template in templates:
            template_list.append(
                {
                    "id": template.id,
                    "name": template.name,
                    "description": template.description,
                    "domain": template.category,
                    "template_text": template.user_prompt_template,
                }
            )
        # ENT-056: include slash commands so the UI can surface them
        slash_commands = [
            {
                "command": "/submit-arb",
                "description": "Submit solution to ARB for review. Usage: /submit-arb <solution_id>",
            }
        ]
        return jsonify({"success": True, "templates": template_list, "slash_commands": slash_commands})
    except Exception:
        return jsonify(
            {
                "error": "Failed to get templates",
                "details": "See server logs for details",
            }
        ), 500


# ============================================================================
# DATA INTERACTION ROUTES (AI-DRIVEN DATA MODIFICATIONS)
# ============================================================================




# ============================================================================
# SESSION MANAGEMENT ROUTES
# ============================================================================


@unified_ai_chat_bp.route("/history", methods=["GET"])
@login_required
def get_chat_history():
    """Get user's chat history."""
    try:
        chat_service = get_chat_service()
        history = chat_service.get_chat_history(current_user.id)

        return jsonify({"success": True, "history": history})

    except Exception:
        return jsonify(
            {
                "error": "Failed to get chat history",
                "details": "See server logs for details",
            }
        ), 500


@unified_ai_chat_bp.route("/history/clear", methods=["POST"])
@login_required
@audit_log("clear_chat_history")
def clear_chat_history():
    """Clear user's chat history."""
    try:
        chat_service = get_chat_service()
        result = chat_service.clear_chat_history(current_user.id)

        return jsonify(
            {
                "success": result.get("success", False),
                "message": result.get("message", "History cleared"),
            }
        )

    except Exception:
        return jsonify(
            {
                "error": "Failed to clear history",
                "details": "See server logs for details",
            }
        ), 500


@unified_ai_chat_bp.route("/session/save", methods=["POST"])
@login_required
@audit_log("save_chat_session")
def save_chat_session():
    """
    Save current chat session.

    Expected JSON payload:
    {
        "session_name": "Customer Analysis Session",
        "messages": [...],
        "context": {}
    }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data received"}), 400

        chat_service = get_chat_service()
        result = chat_service.save_session(current_user.id, data)

        return jsonify(
            {
                "success": result.get("success", False),
                "session_id": result.get("session_id"),
                "message": result.get("message", "Session saved"),
            }
        )

    except Exception:
        return jsonify(
            {
                "error": "Failed to save session",
                "details": "See server logs for details",
            }
        ), 500


@unified_ai_chat_bp.route("/sessions", methods=["GET"])
@login_required
def get_saved_sessions():
    """Get user's saved chat sessions."""
    try:
        chat_service = get_chat_service()
        sessions = chat_service.list_sessions(current_user.id)

        return jsonify({"success": True, "sessions": sessions})

    except Exception:
        return jsonify(
            {
                "error": "Failed to get sessions",
                "details": "See server logs for details",
            }
        ), 500


@unified_ai_chat_bp.route("/session/<session_id>", methods=["GET"])
@login_required
def load_chat_session(session_id):
    """Load a saved chat session."""
    try:
        chat_service = get_chat_service()
        result = chat_service.load_session(session_id)

        if result.get("success"):
            return jsonify(result)
        else:
            return jsonify({"error": result.get("error", "Session not found")}), 404

    except Exception:
        return jsonify(
            {
                "error": "Failed to load session",
                "details": "See server logs for details",
            }
        ), 500


# ============================================================================
# CONVERSATION THREADS — the ChatGPT-style history rail (always on)
# Backed by the conversation_history thread/message model. Every chat turn is
# persisted (see persist_turn in send_message / send_message_stream), so history
# survives reloads and restarts. Ownership-checked to prevent cross-user reads.
# ============================================================================


def _conv_dict(obj):
    """Normalise a thread/message to a dict whether cached as object or dict."""
    return obj.to_dict() if hasattr(obj, "to_dict") else obj


@unified_ai_chat_bp.route("/threads", methods=["GET"])
@login_required
def list_conversation_threads():
    """List the current user's conversations, newest first, for the rail."""
    try:
        from app.services.conversation_history import get_history_service

        threads = get_history_service().get_user_threads(current_user.id, limit=100)
        return jsonify({"success": True, "threads": [_conv_dict(t) for t in threads]})
    except Exception as e:
        current_app.logger.exception("list_conversation_threads failed: %s", e)
        return jsonify({"success": False, "error": "Could not load conversations"}), 500


@unified_ai_chat_bp.route("/threads/<thread_id>", methods=["GET"])
@login_required
def get_conversation_thread(thread_id):
    """Load one conversation's messages (ownership-checked)."""
    from app.services.conversation_history import get_history_service, thread_owned_by

    if not thread_owned_by(thread_id, current_user.id):
        return jsonify({"success": False, "error": "Conversation not found"}), 404
    messages = get_history_service().get_thread_messages(thread_id)
    return jsonify(
        {
            "success": True,
            "thread_id": thread_id,
            "messages": [_conv_dict(m) for m in messages],
        }
    )


@unified_ai_chat_bp.route("/threads/<thread_id>", methods=["DELETE"])
@login_required
def delete_conversation_thread(thread_id):
    """Delete one conversation (ownership-checked)."""
    from app.services.conversation_history import get_history_service, thread_owned_by

    if not thread_owned_by(thread_id, current_user.id):
        return jsonify({"success": False, "error": "Conversation not found"}), 404
    try:
        get_history_service().delete_thread(thread_id)
        return jsonify({"success": True})
    except Exception as e:
        current_app.logger.warning("delete_conversation_thread failed: %s", e)
        return jsonify({"success": False, "error": "Could not delete conversation"}), 500


# ============================================================================
# AIC-312: WORKSPACE ENDPOINTS
# ============================================================================


@unified_ai_chat_bp.route("/workspace/<int:workspace_id>", methods=["GET"])
@login_required
def get_workspace(workspace_id):
    """Load a workbench workspace by ID."""
    from app.modules.ai_chat.services.workbench_kernel import WorkbenchKernel

    kernel = WorkbenchKernel(user_id=current_user.id)
    workspace = kernel.resume(workspace_id)

    if not workspace:
        return jsonify({"success": False, "error": "Workspace not found"}), 404

    return jsonify({"success": True, "workspace": workspace})


@unified_ai_chat_bp.route("/workspace/<int:workspace_id>/versions", methods=["GET"])
@login_required
def get_workspace_versions(workspace_id):
    """List version history for a workspace."""
    from app.modules.ai_chat.services.workbench_kernel import WorkbenchKernel

    kernel = WorkbenchKernel(user_id=current_user.id)
    workspace = kernel.load_workspace(workspace_id)
    if not workspace:
        return jsonify({"success": False, "error": "Workspace not found"}), 404

    versions = kernel.list_versions(workspace_id)
    return jsonify({"success": True, "versions": versions})


# ============================================================================
# ANALYTICS AND MONITORING
# ============================================================================




# ============================================================================
# DOMAIN CONTEXT
# ============================================================================


@unified_ai_chat_bp.route("/context/<domain>")
@login_required
def get_domain_context(domain):
    """Get context data for specific domain."""
    try:
        chat_service = get_chat_service()
        context_data = chat_service.get_domain_context(domain)
        # P-08: get_domain_context() returns {"success": False, ...} for an
        # unrecognized domain instead of raising — jsonify()-ing it verbatim
        # produced HTTP 200 for a failure, so a caller checking response.ok
        # (the documented CLAUDE.md fetch trap) never sees the error. Mirror
        # the success flag onto the HTTP status.
        status = 200 if context_data.get("success", True) else 404
        return jsonify(context_data), status
    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


# ============================================================================
# NATURAL LANGUAGE QUERY ROUTES
# ============================================================================


# ============================================================================
# AGENT TOOL APPROVAL ROUTES
# ============================================================================


@unified_ai_chat_bp.route("/tools/approve/<int:approval_id>", methods=["POST"])
@login_required
def approve_tool_action(approval_id: int):
    """
    Execute a previously queued 'approve' tier tool action.

    The AI agent queues destructive/significant operations (e.g. update_application_status,
    submit_for_arb_review) instead of executing them immediately.  The frontend shows
    the user a confirmation dialog; clicking Confirm calls this endpoint.
    """
    # ARCH-022: this used to hand-roll status/approved_at updates directly
    # (bypassing AIChatApprovalService entirely) and never set approved_by_id
    # -- the exact defect: an approval left PENDING, executed, with
    # approved_by_id: null, because the code path that actually flipped the
    # row's status never wrote who did it. approve_and_execute's operation_type
    # == "tool_use" branch already dispatches through ToolExecutor with the
    # same ToolCall shape this route built by hand, sets approved_by_id, and
    # writes the audit trail — delegate to it instead of duplicating (and
    # subtly diverging from) that logic.
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

    result = AIChatApprovalService(current_user.id).approve_and_execute(approval_id, current_user.id)
    status_code = {
        "NOT_FOUND": 404,
        "FORBIDDEN": 403,
        "APPROVAL_DENIED": 403,
        "CONFLICT": 409,
    }.get(result.get("code"), 200 if result.get("success") else 400)
    return jsonify({
        "success": result.get("success", False),
        "message": result.get("message", ""),
        "result": result.get("result"),
        "error": result.get("error"),
    }), status_code


@unified_ai_chat_bp.route("/tools/reject/<int:approval_id>", methods=["POST"])
@login_required
def reject_tool_action(approval_id: int):
    """Cancel a queued 'approve' tier tool action."""
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

    result = AIChatApprovalService(current_user.id).reject_approval(approval_id)
    status_code = {
        "NOT_FOUND": 404,
        "FORBIDDEN": 403,
        "APPROVAL_DENIED": 403,
        "CONFLICT": 409,
    }.get(result.get("code"), 200 if result.get("success") else 400)
    return jsonify({
        "success": result.get("success", False),
        "message": result.get("message", "Action cancelled." if result.get("success") else result.get("error")),
    }), status_code


@unified_ai_chat_bp.route("/session/auto-execute", methods=["GET"])
@login_required
def get_auto_execute():
    """Read (never flip) the session's current auto-execute preference.

    M2 fix: blueprint_chat.js's `autoExecute` Alpine field used to have no way
    to learn the server's real state on page load — it just assumed a
    hardcoded default, so a session left ON from a previous page visit showed
    as OFF in a freshly loaded panel (or vice versa) until the user clicked
    the toggle once to force a resync. init() below calls this on mount to
    seed the UI from ground truth. Deliberately a separate GET, not a change
    to the POST toggle route's contract — the toggle intentionally has no
    'give me the state without changing it' mode, and overloading it with a
    query param would be a bigger change than adding four lines here.
    """
    from flask import session as flask_session

    return jsonify({"success": True, "auto_execute": flask_session.get("agent_auto_execute", False)})


@unified_ai_chat_bp.route("/session/toggle-auto-execute", methods=["POST"])
@login_required
def toggle_auto_execute():
    """Toggle the session's auto-execute preference and return the new state.

    This is a PREFERENCE, and it cannot defeat the operator control: when
    REQUIRE_AI_APPROVAL is true, _agent_auto_execute_allowed() returns False
    whatever this flag says, and every mutating tool is queued. Until 31 Aug
    2026 the agent path read this flag alone, so any authenticated user could
    turn off a governance control the operator had set.

    ENFORCED. Each of the 37 tools in tools/registry.py carries an explicit
    `mutates` flag, read alongside `tier` (registry.py's own docstring). Both
    chat routes read this session flag on the request thread and pass it into
    AgentRunner(auto_execute=...) — see send_message() and the run_agent()
    worker in send_message_stream() below, which captures it before spawning
    the background thread because flask.session is not available there.
    AgentRunner._should_queue(schema, auto_execute) then queues a call for
    confirmation when EITHER is true:
      - tier == "approve" (unconditional — unaffected by this flag), or
      - mutates is True and auto_execute is False.
    Read-only tools (mutates False) are never queued, regardless of this flag —
    gating them would put every search behind an approval prompt, which is what
    made an earlier "OFF means everything confirms" version of this docstring
    false: `tier` alone could not distinguish a write from a read.

    Default is OFF (session has no `agent_auto_execute` key -> False), so a
    fresh session queues every mutating tool call until the user explicitly
    turns auto-execute on. Session-scoped, not persisted: it resets when the
    session does. blueprint_chat.js:43 calls this endpoint from the blueprint
    chat panel's toggle.
    """
    from flask import session as flask_session
    current = flask_session.get("agent_auto_execute", False)
    flask_session["agent_auto_execute"] = not current
    return jsonify({"success": True, "auto_execute": not current})

