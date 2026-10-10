"""Business Rules routes for the Code Workbench.

All routes live under /solutions/<id>/codegen/rules* and are registered on
``codegen_bp`` (defined in codegen_routes.py).  This module is imported at the
bottom of codegen_routes.py so Flask picks up the decorated handlers.
"""
import logging

from flask import jsonify, request
from flask_login import login_required

from app.extensions import db
from app.models.solution_models import Solution
from app.modules.codegen.models import CodegenGeneration
from app.modules.codegen.routes.codegen_routes import codegen_bp
from app.modules.codegen.routes._helpers import _check_access, _extract_model_fields

logger = logging.getLogger(__name__)


# ─── Business Rules: template + NL + CRUD routes ──────────────────────
# Design spec: docs/2026-03-30-archie-deploy-zero-dev-design.md §3


@codegen_bp.route("/solutions/<int:solution_id>/codegen/rules/templates", methods=["GET"])
@login_required
def list_rule_templates(solution_id):
    """List available rule templates with context-aware suggestions."""
    solution = Solution.query.get_or_404(solution_id)
    _check_access(solution)

    from app.modules.codegen.services.rule_template_engine import RuleTemplateEngine
    engine = RuleTemplateEngine()
    templates = engine.list_templates()

    # Get model fields for suggestions
    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
    suggestions = []
    if gen and gen.generated_files:
        model_fields = _extract_model_fields(gen.generated_files)
        suggestions = engine.suggest_templates(model_fields)

    return jsonify({"templates": templates, "suggestions": suggestions})


@codegen_bp.route("/solutions/<int:solution_id>/codegen/rules", methods=["POST"])
@login_required
def create_rule(solution_id):
    """Create a business rule from template or natural language."""
    solution = Solution.query.get_or_404(solution_id)
    _check_access(solution)

    body = request.get_json(silent=True) or {}
    source = body.get("source", "template")  # template or natural_language
    name = body.get("name", "")

    from app.modules.codegen.models import SolutionRule

    if source == "template":
        template_id = body.get("template_id")
        parameters = body.get("parameters", {})
        if not template_id:
            return jsonify({"error": "template_id is required"}), 400
        from app.modules.codegen.services.rule_template_engine import RuleTemplateEngine
        engine = RuleTemplateEngine()
        try:
            rule_def = engine.instantiate(template_id, parameters)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

    elif source == "natural_language":
        rule_text = body.get("rule_text", "")
        if not rule_text:
            return jsonify({"error": "rule_text is required"}), 400
        from app.modules.codegen.services.nl_rule_parser import NaturalLanguageRuleParser
        parser = NaturalLanguageRuleParser()
        gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
        model_entities = list(_extract_model_fields(gen.generated_files).keys()) if gen and gen.generated_files else []
        rule_def = parser.parse(rule_text, model_entities)
        if rule_def.get("clarification_needed"):
            return jsonify({"clarification_needed": True, "question": rule_def.get("question", ""), "confidence": rule_def.get("confidence", 0)}), 200
        if rule_def.get("error"):
            return jsonify({"error": rule_def["error"]}), 500
    else:
        return jsonify({"error": f"Unknown source: {source}"}), 400

    rule = SolutionRule(
        solution_id=solution_id,
        name=name or f"Rule from {source}",
        source=source,
        source_text=str(body),
        rule_definition=rule_def,
    )
    db.session.add(rule)
    db.session.commit()

    return jsonify({"id": rule.id, "name": rule.name, "rule_definition": rule.rule_definition}), 201


@codegen_bp.route("/solutions/<int:solution_id>/codegen/rules", methods=["GET"])
@login_required
def list_rules(solution_id):
    """List all business rules for a solution."""
    solution = Solution.query.get_or_404(solution_id)
    _check_access(solution)

    from app.modules.codegen.models import SolutionRule
    rules = SolutionRule.query.filter_by(solution_id=solution_id, is_active=True).all()
    return jsonify({"rules": [
        {"id": r.id, "name": r.name, "source": r.source, "rule_definition": r.rule_definition, "version": r.version, "is_active": r.is_active}
        for r in rules
    ]})


@codegen_bp.route("/solutions/<int:solution_id>/codegen/rules/<int:rule_id>/compile", methods=["POST"])
@login_required
def compile_rule(solution_id, rule_id):
    """Compile a rule into implementation code."""
    solution = Solution.query.get_or_404(solution_id)
    _check_access(solution)

    from app.modules.codegen.models import SolutionRule
    rule = SolutionRule.query.get_or_404(rule_id)
    if rule.solution_id != solution_id:
        return jsonify({"error": "Rule does not belong to this solution"}), 403

    from app.modules.codegen.services.rule_code_generator import RuleCodeGenerator
    compiler = RuleCodeGenerator()
    try:
        artifacts = compiler.compile(rule.rule_definition)
        rule.implementation_artifacts = artifacts
        db.session.commit()
        return jsonify({"rule_id": rule.id, "artifacts": artifacts})
    except Exception as e:
        logger.exception("Rule compilation failed for rule %s", rule_id)
        return jsonify({"error": f"Compilation failed: {e}"}), 500


# ─── Business Rules: additional Phase 3a endpoints ──────────────────────


@codegen_bp.route("/solutions/<int:solution_id>/codegen/rules/templates/<template_id>", methods=["GET"])
@login_required
def get_rule_template(solution_id, template_id):
    """Return a single rule template by ID."""
    from app.modules.codegen.services.rule_template_engine import RuleTemplateEngine

    engine = RuleTemplateEngine()
    template = engine.get_template(template_id)
    if template is None:
        return jsonify({"success": False, "error": f"Template not found: {template_id}"}), 404
    return jsonify({"success": True, "template": template})


@codegen_bp.route("/solutions/<int:solution_id>/codegen/rules/suggest", methods=["GET"])
@login_required
def suggest_rule_templates(solution_id):
    """Suggest templates based on the solution's data model."""
    from app.modules.codegen.services.rule_template_engine import RuleTemplateEngine

    Solution.query.get_or_404(solution_id)
    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
    if not gen or not gen.uml_snapshot:
        return jsonify({"success": True, "suggestions": [], "reason": "No UML snapshot available"})

    uml = gen.uml_snapshot or {}
    data_model = {"classes": uml.get("class_diagram", {}).get("classes", [])}

    engine = RuleTemplateEngine()
    suggestions = engine.suggest_templates(data_model)
    return jsonify({"success": True, "suggestions": suggestions})


@codegen_bp.route("/solutions/<int:solution_id>/codegen/rules/<int:rule_id>", methods=["DELETE"])
@login_required
def delete_solution_rule(solution_id, rule_id):
    """Deactivate a business rule (soft delete)."""
    from app.modules.codegen.models import SolutionRule

    # A rule carries no organisation of its own; its solution does. Checking only
    # that the rule matches the URL's solution id let another organisation's pair
    # of ids through, so resolve the solution first.
    Solution.query.get_or_404(solution_id)
    rule = SolutionRule.query.get_or_404(rule_id)
    if rule.solution_id != solution_id:
        return jsonify({"success": False, "error": "Rule does not belong to this solution"}), 403
    rule.is_active = False
    db.session.commit()
    return jsonify({"success": True})


@codegen_bp.route("/solutions/<int:solution_id>/codegen/rules/compile", methods=["POST"])
@login_required
def compile_solution_rules(solution_id):
    """Compile all active rules for a solution into implementation artifacts."""
    from app.modules.codegen.models import SolutionRule
    from app.modules.codegen.services.rule_compiler import RuleCompiler

    Solution.query.get_or_404(solution_id)

    rules = SolutionRule.query.filter_by(solution_id=solution_id, is_active=True).all()
    if not rules:
        return jsonify({"success": True, "compiled": 0, "artifacts": {"files": {}, "side_effects": []}})

    compiler = RuleCompiler()
    all_files = {}
    all_side_effects = []
    errors = []

    for rule in rules:
        result = compiler.compile(rule.rule_definition)
        if result["success"]:
            all_files.update(result["artifacts"]["files"])
            all_side_effects.extend(result["artifacts"]["side_effects"])
            rule.implementation_artifacts = result["artifacts"]
        else:
            errors.append({"rule_id": rule.id, "name": rule.name, "errors": result["errors"]})

    db.session.commit()

    if errors:
        return jsonify({"success": False, "errors": errors, "compiled": len(rules) - len(errors)}), 400

    return jsonify({
        "success": True,
        "compiled": len(rules),
        "artifacts": {"files": all_files, "side_effects": all_side_effects},
    })
