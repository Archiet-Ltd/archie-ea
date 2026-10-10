"""Wizard AI Enhancement Routes — unified API for all 4 layers.

Wave 1: Quality Gate (Layer C) + Genome Perfector (Layer D)
Wave 2: Auto-Complete (Layer B) — adds POST /<id>/autocomplete/* routes
Wave 3: Copilot (Layer A) — adds POST /<id>/copilot/* routes

All routes require authentication and solution ownership.
All LLM calls go through LLMService with budget checking.
"""

import logging
from functools import wraps

from flask import Blueprint, request
from flask_login import current_user, login_required

from app.core.api.response import api_error, api_success
from app.models.solution_models import Solution

logger = logging.getLogger(__name__)

wizard_ai_bp = Blueprint("wizard_ai", __name__, url_prefix="/api/wizard")


def _require_solution_owner(f):
    """Guard: authenticated user must own the solution or be admin OF THE
    ACTIVE organisation.

    D-4 (admin-rbac-active-org continuation): the admin branch used to be
    ``current_user.is_admin()`` -- a global Permission.ADMINISTER flag,
    independent of which organisation is active in the session
    (``g.current_org_id``). Since every self-registered user is
    Administrator of their own organisation, a user who merely accepted a
    Viewer invitation into another organisation and switched their session
    into it could act on any solution there too, not just their own -- the
    exact bug admin_required/org_admin_required already fix elsewhere in
    this PR.
    """
    @wraps(f)
    def decorated(solution_id, *args, **kwargs):
        from flask import g

        from app.middleware.tenant_decorators import is_platform_admin
        from app.services.rbac_service import rbac_service

        solution = Solution.query.get_or_404(solution_id)
        active_org_id = getattr(g, "current_org_id", None)
        is_admin_here = is_platform_admin(current_user) or rbac_service.is_org_admin(
            current_user, active_org_id
        )
        if solution.created_by_id != current_user.id and not is_admin_here:
            return api_error("Access denied: you do not own this solution", 403)
        return f(solution_id, *args, **kwargs)
    return decorated


# ── Layer C: Quality Gate ────────────────────────────────────────

@wizard_ai_bp.route("/<int:solution_id>/quality/assess", methods=["POST"])
@login_required
@_require_solution_owner
def assess_quality(solution_id):
    """Score current step quality. Returns dimensions, issues, and pass/fail."""
    try:
        from app.modules.architecture_assistant.services.wizard_context_assembler import WizardContextAssembler
        from app.modules.architecture_assistant.services.wizard_quality_gate_service import WizardQualityGateService

        data = request.get_json() or {}
        step = data.get("step")
        step_data = data.get("step_data", {})

        if step is None or not isinstance(step, int) or step < 1 or step > 9:
            return api_error("Invalid step number", 400)

        assembler = WizardContextAssembler()
        context = assembler.assemble(solution_id)

        service = WizardQualityGateService()
        assessment = service.assess_step(solution_id, step, step_data, context)

        return api_success(data=service.to_dict(assessment))

    except Exception as e:
        logger.error("Quality assessment failed for solution %d: %s", solution_id, e, exc_info=True)
        return api_error("Quality assessment failed", 500)


@wizard_ai_bp.route("/<int:solution_id>/quality/can-advance", methods=["POST"])
@login_required
@_require_solution_owner
def can_advance(solution_id):
    """Check if user can advance past current step."""
    try:
        from app.modules.architecture_assistant.services.wizard_context_assembler import WizardContextAssembler
        from app.modules.architecture_assistant.services.wizard_quality_gate_service import WizardQualityGateService

        data = request.get_json() or {}
        step = data.get("step")
        step_data = data.get("step_data", {})

        if step is None or not isinstance(step, int) or step < 1 or step > 9:
            return api_error("Invalid step number", 400)

        assembler = WizardContextAssembler()
        context = assembler.assemble(solution_id)

        service = WizardQualityGateService()
        allowed, assessment = service.can_advance(solution_id, step, step_data, context)

        return api_success(data={
            "can_advance": allowed,
            "assessment": service.to_dict(assessment),
        })

    except Exception as e:
        logger.error("Can-advance check failed: %s", e, exc_info=True)
        return api_error("Can-advance check failed", 500)


@wizard_ai_bp.route("/<int:solution_id>/quality/skip", methods=["POST"])
@login_required
@_require_solution_owner
def record_quality_skip(solution_id):
    """Record that user skipped a soft-block quality gate."""
    try:
        from app.modules.architecture_assistant.services.wizard_quality_gate_service import (
            WizardQualityGateService,
            QualityAssessment,
        )

        data = request.get_json() or {}
        step = data.get("step")
        score = data.get("overall_score", 0)
        threshold = data.get("threshold", 70)

        if step is None:
            return api_error("Step required", 400)

        service = WizardQualityGateService()
        # Reconstruct minimal assessment for recording
        assessment = QualityAssessment(
            step=step,
            overall_score=score,
            threshold=threshold,
            passed=score >= threshold,
            hard_block=step in service.HARD_BLOCK_STEPS,
            dimensions=[],
            failing_items=[],
            auto_fixable_count=0,
            estimated_fix_time="",
        )
        service.record_skip(solution_id, step, assessment)

        return api_success(data={"recorded": True})

    except Exception as e:
        logger.error("Record skip failed: %s", e, exc_info=True)
        return api_error("Failed to record skip", 500)


@wizard_ai_bp.route("/<int:solution_id>/quality/threshold/<int:step>", methods=["GET"])
@login_required
@_require_solution_owner
def get_threshold(solution_id, step):
    """Get quality threshold config for a step."""
    from app.modules.architecture_assistant.services.wizard_quality_gate_service import WizardQualityGateService

    service = WizardQualityGateService()
    return api_success(data={
        "step": step,
        "threshold": service.THRESHOLDS.get(step, 70),
        "hard_block": step in service.HARD_BLOCK_STEPS,
    })


# ── Layer D: Genome Perfector ────────────────────────────────────

@wizard_ai_bp.route("/<int:solution_id>/genome/perfect", methods=["POST"])
@login_required
@_require_solution_owner
def perfect_genome(solution_id):
    """Perfect genome before code generation. Single LLM call."""
    try:
        from app.modules.architecture_assistant.services.wizard_context_assembler import WizardContextAssembler
        from app.modules.codegen.services.genome_perfector_service import GenomePerfectorService
        from app.modules.codegen.models import CodegenGeneration

        # Get genome from DB or request body
        data = request.get_json() or {}
        genome = data.get("genome")

        if genome is None:
            gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
            if gen and gen.genome:
                genome = gen.genome
            else:
                return api_error("No genome found for this solution", 404)

        assembler = WizardContextAssembler()
        context = assembler.assemble(solution_id)

        service = GenomePerfectorService()
        result = service.perfect(solution_id, genome, context)

        return api_success(data=service.result_to_dict(result))

    except Exception as e:
        logger.error("Genome perfection failed: %s", e, exc_info=True)
        return api_error("Genome perfection failed", 500)


@wizard_ai_bp.route("/<int:solution_id>/genome/score", methods=["POST"])
@login_required
@_require_solution_owner
def score_genome(solution_id):
    """Score genome quality (deterministic, no LLM cost)."""
    try:
        from app.modules.codegen.services.genome_perfector_service import GenomePerfectorService
        from app.modules.codegen.models import CodegenGeneration

        data = request.get_json() or {}
        genome = data.get("genome")

        if genome is None:
            gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
            if gen and gen.genome:
                genome = gen.genome
            else:
                return api_error("No genome found", 404)

        service = GenomePerfectorService()
        score = service.score_genome(genome)

        return api_success(data=service.score_to_dict(score))

    except Exception as e:
        logger.error("Genome scoring failed: %s", e, exc_info=True)
        return api_error("Genome scoring failed", 500)


@wizard_ai_bp.route("/<int:solution_id>/genome/validate", methods=["POST"])
@login_required
@_require_solution_owner
def validate_genome(solution_id):
    """Validate genome schema (deterministic, no LLM cost)."""
    try:
        from app.modules.codegen.services.genome_perfector_service import GenomePerfectorService
        from app.modules.codegen.models import CodegenGeneration

        data = request.get_json() or {}
        genome = data.get("genome")

        if genome is None:
            gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
            if gen and gen.genome:
                genome = gen.genome
            else:
                return api_error("No genome found", 404)

        service = GenomePerfectorService()
        errors = service.validate_genome(genome)

        return api_success(data={
            "valid": len(errors) == 0,
            "errors": errors,
        })

    except Exception as e:
        logger.error("Genome validation failed: %s", e, exc_info=True)
        return api_error("Genome validation failed", 500)


# ── Layer B: Auto-Complete ───────────────────────────────────────

@wizard_ai_bp.route("/<int:solution_id>/autocomplete/step", methods=["POST"])
@login_required
@_require_solution_owner
def autocomplete_step(solution_id):
    """Complete all empty/weak fields in the current step."""
    try:
        from app.modules.architecture_assistant.services.wizard_context_assembler import WizardContextAssembler
        from app.modules.architecture_assistant.services.wizard_autocomplete_service import WizardAutoCompleteService

        data = request.get_json() or {}
        step = data.get("step")
        step_data = data.get("step_data", {})
        fields_to_complete = data.get("fields_to_complete")

        if step is None or not isinstance(step, int) or step < 1 or step > 9:
            return api_error("Invalid step number", 400)

        assembler = WizardContextAssembler()
        context = assembler.assemble(solution_id)

        service = WizardAutoCompleteService()
        result = service.complete_step(solution_id, step, step_data, context, fields_to_complete)

        return api_success(data=service.to_dict(result))

    except Exception as e:
        logger.error("Auto-complete step failed: %s", e, exc_info=True)
        return api_error("Auto-complete failed", 500)


@wizard_ai_bp.route("/<int:solution_id>/autocomplete/transition", methods=["POST"])
@login_required
@_require_solution_owner
def autocomplete_transition(solution_id):
    """Complete fields needed by the next step based on current step output."""
    try:
        from app.modules.architecture_assistant.services.wizard_context_assembler import WizardContextAssembler
        from app.modules.architecture_assistant.services.wizard_autocomplete_service import WizardAutoCompleteService

        data = request.get_json() or {}
        from_step = data.get("from_step")
        to_step = data.get("to_step")
        step_data = data.get("step_data", {})

        if not from_step or not to_step:
            return api_error("from_step and to_step required", 400)

        assembler = WizardContextAssembler()
        context = assembler.assemble(solution_id)

        service = WizardAutoCompleteService()
        result = service.complete_transition(solution_id, from_step, to_step, step_data, context)

        return api_success(data=service.to_dict(result))

    except Exception as e:
        logger.error("Auto-complete transition failed: %s", e, exc_info=True)
        return api_error("Auto-complete transition failed", 500)


@wizard_ai_bp.route("/<int:solution_id>/autocomplete/apply", methods=["POST"])
@login_required
@_require_solution_owner
def apply_completions(solution_id):
    """Apply user-accepted completions to journey_state."""
    try:
        from app.modules.architecture_assistant.services.wizard_autocomplete_service import WizardAutoCompleteService

        data = request.get_json() or {}
        accepted_fields = data.get("accepted_fields", {})

        if not accepted_fields:
            return api_error("No accepted fields provided", 400)

        service = WizardAutoCompleteService()
        result = service.apply_completions(solution_id, accepted_fields)

        return api_success(data=result)

    except Exception as e:
        logger.error("Apply completions failed: %s", e, exc_info=True)
        return api_error("Apply completions failed", 500)


# ── Layer A: Copilot ─────────────────────────────────────────────

@wizard_ai_bp.route("/<int:solution_id>/copilot/review-field", methods=["POST"])
@login_required
@_require_solution_owner
def copilot_review_field(solution_id):
    """Single field review. Called on 2s idle debounce."""
    try:
        from app.modules.architecture_assistant.services.wizard_context_assembler import WizardContextAssembler
        from app.modules.architecture_assistant.services.wizard_copilot_service import WizardCopilotService

        data = request.get_json() or {}
        step = data.get("step")
        field_name = data.get("field_name", "")
        field_value = data.get("field_value", "")

        if step is None or not field_name:
            return api_error("step and field_name required", 400)

        assembler = WizardContextAssembler()
        context = assembler.assemble(solution_id)

        service = WizardCopilotService()
        suggestion = service.review_field(solution_id, step, field_name, field_value, context)

        if suggestion is None:
            return api_success(data={"suggestion": None})

        return api_success(data={"suggestion": service.to_dict(suggestion)})

    except Exception as e:
        logger.error("Copilot field review failed: %s", e, exc_info=True)
        return api_error("Copilot review failed", 500)


@wizard_ai_bp.route("/<int:solution_id>/copilot/review-step", methods=["POST"])
@login_required
@_require_solution_owner
def copilot_review_step(solution_id):
    """Batch review all fields in current step ('Enhance All')."""
    try:
        from app.modules.architecture_assistant.services.wizard_context_assembler import WizardContextAssembler
        from app.modules.architecture_assistant.services.wizard_copilot_service import WizardCopilotService

        data = request.get_json() or {}
        step = data.get("step")
        step_data = data.get("step_data", {})

        if step is None or not isinstance(step, int):
            return api_error("Valid step number required", 400)

        assembler = WizardContextAssembler()
        context = assembler.assemble(solution_id)

        service = WizardCopilotService()
        suggestions = service.review_step(solution_id, step, step_data, context)

        return api_success(data={
            "suggestions": service.to_dict_list(suggestions),
            "count": len(suggestions),
        })

    except Exception as e:
        logger.error("Copilot step review failed: %s", e, exc_info=True)
        return api_error("Copilot step review failed", 500)


@wizard_ai_bp.route("/<int:solution_id>/copilot/accept", methods=["POST"])
@login_required
@_require_solution_owner
def copilot_accept_suggestion(solution_id):
    """Track suggestion acceptance for analytics."""
    try:
        from app.modules.architecture_assistant.services.wizard_copilot_service import WizardCopilotService

        data = request.get_json() or {}
        suggestion_id = data.get("suggestion_id", "")
        field_name = data.get("field_name", "")
        new_value = data.get("new_value", "")

        service = WizardCopilotService()
        result = service.accept_suggestion(solution_id, suggestion_id, field_name, new_value)

        return api_success(data=result)

    except Exception as e:
        logger.error("Accept suggestion failed: %s", e, exc_info=True)
        return api_error("Accept suggestion failed", 500)
