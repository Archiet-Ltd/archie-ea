"""Testing & Validation routes for the Code Workbench.

All routes live under /solutions/<id>/codegen/test/* and are registered on
``codegen_bp`` (defined in codegen_routes.py).  This module is imported at the
bottom of codegen_routes.py so Flask picks up the decorated handlers.
"""
import logging

from flask import abort, jsonify, request
from flask_login import login_required

from app.extensions import db
from app.models.solution_models import Solution
from app.modules.codegen.routes.codegen_routes import codegen_bp
from app.modules.codegen.routes._helpers import _check_access
from app.utils.pagination import safe_int_arg

logger = logging.getLogger(__name__)


# ── Testing & Validation Routes (Phase 4) ──


@codegen_bp.route("/solutions/<int:solution_id>/codegen/test/generate-scenarios", methods=["POST"])
@login_required
def generate_test_scenarios_v2(solution_id):
    """Generate test scenarios from solution's model + rules."""
    from app.modules.codegen.services.scenario_generator import ScenarioGenerator
    from app.modules.codegen.models import SolutionRule

    solution = db.session.get(Solution, solution_id)
    if not solution or not _check_access(solution, write=True):
        abort(404)

    # Extract model entities from solution data
    model_entities = {}
    data_model = getattr(solution, "data_model", None)
    if data_model and isinstance(data_model, dict):
        for entity_name, entity_def in data_model.get("entities", {}).items():
            fields = []
            if isinstance(entity_def, dict):
                fields = list(entity_def.get("fields", {}).keys())
            elif isinstance(entity_def, list):
                fields = entity_def
            model_entities[entity_name] = fields

    # Extract active rules
    rules = []
    active_rules = SolutionRule.query.filter_by(solution_id=solution_id, is_active=True).all()
    for rule in active_rules:
        rules.append({"name": rule.name, "rule_definition": rule.rule_definition or {}})

    problem = ""
    problem_data = getattr(solution, "problem_clarification", None)
    if problem_data:
        if isinstance(problem_data, dict):
            problem = problem_data.get("problem_statement", "")
        else:
            problem = str(problem_data)

    gen = ScenarioGenerator()
    scenarios = gen.generate(
        problem_statement=problem,
        model_entities=model_entities,
        rules=rules,
    )

    return jsonify({"success": True, "scenarios": scenarios, "count": len(scenarios)})


@codegen_bp.route("/solutions/<int:solution_id>/codegen/test/record-result", methods=["POST"])
@login_required
def record_test_result(solution_id):
    """Record BA's pass/fail verdict on a test scenario."""
    from app.modules.codegen.services.scenario_tracker import ScenarioTracker

    solution = db.session.get(Solution, solution_id)
    if not solution or not _check_access(solution, write=True):
        abort(404)

    data = request.get_json(silent=True) or {}
    scenario_id = data.get("scenario_id")
    verdict = data.get("verdict")
    if not scenario_id or verdict not in ("pass", "fail", "partial"):
        return jsonify({"success": False, "error": "scenario_id and verdict (pass/fail/partial) required"}), 400

    tracker = ScenarioTracker()
    result = tracker.record(
        solution_id=solution_id,
        scenario_id=scenario_id,
        verdict=verdict,
        notes=data.get("notes"),
        rule_name=data.get("rule_name"),
    )

    return jsonify({"success": True, "result": result})


@codegen_bp.route("/solutions/<int:solution_id>/codegen/test/results", methods=["GET"])
@login_required
def get_test_results(solution_id):
    """Get all test results and summary for a solution."""
    from app.modules.codegen.services.scenario_tracker import ScenarioTracker

    solution = db.session.get(Solution, solution_id)
    if not solution or not _check_access(solution):
        abort(404)

    tracker = ScenarioTracker()
    results = tracker.get_results(solution_id)
    summary = tracker.get_summary(solution_id)
    failed_rules = tracker.get_failed_rules(solution_id)

    return jsonify({
        "success": True,
        "results": results,
        "summary": summary,
        "failed_rules": failed_rules,
    })


@codegen_bp.route("/solutions/<int:solution_id>/codegen/test/run-acceptance", methods=["POST"])
@login_required
def run_acceptance_tests_v2(solution_id):
    """Run automated acceptance tests via Playwright."""
    from app.modules.codegen.services.acceptance_test_runner import AcceptanceTestRunner

    solution = db.session.get(Solution, solution_id)
    if not solution or not _check_access(solution, write=True):
        abort(404)

    data = request.get_json(silent=True) or {}
    scenarios = data.get("scenarios")

    runner = AcceptanceTestRunner()
    report = runner.run_all(solution_id=solution_id, scenarios=scenarios)

    return jsonify({"success": True, "report": report})


@codegen_bp.route("/solutions/<int:solution_id>/codegen/test/compare-process", methods=["POST"])
@login_required
def compare_process_v2(solution_id):
    """Compare old process vs new solution."""
    from app.modules.codegen.services.process_comparison_engine import ProcessComparisonEngine

    solution = db.session.get(Solution, solution_id)
    if not solution or not _check_access(solution, write=True):
        abort(404)

    engine = ProcessComparisonEngine()
    comparison = engine.compare(solution_id=solution_id)

    return jsonify({"success": True, "comparison": comparison})


@codegen_bp.route("/solutions/<int:solution_id>/codegen/test/auto-fix", methods=["POST"])
@login_required
def auto_fix_issue(solution_id):
    """Diagnose a test failure and optionally generate a fix."""
    from app.modules.codegen.services.auto_fixer import AutoFixer

    solution = db.session.get(Solution, solution_id)
    if not solution or not _check_access(solution, write=True):
        abort(404)

    data = request.get_json(silent=True) or {}
    failure_description = data.get("failure_description", "")
    if not failure_description:
        return jsonify({"success": False, "error": "failure_description required"}), 400

    fixer = AutoFixer()
    diagnosis = fixer.diagnose(failure_description, solution_id)

    result = {"diagnosis": diagnosis, "auto_fix_eligible": fixer.should_auto_fix(diagnosis.get("confidence", 0))}

    if data.get("generate_fix") and fixer.should_attempt_fix(diagnosis.get("confidence", 0)):
        fix = fixer.generate_fix(diagnosis, {})
        result["fix"] = fix

    return jsonify({"success": True, **result})


@codegen_bp.route("/solutions/<int:solution_id>/codegen/test/fix-loop", methods=["POST"])
@login_required
def run_fix_loop(solution_id):
    """Run the full diagnose -> fix -> verify loop for a test failure."""
    from app.modules.codegen.services.auto_fixer import AutoFixer

    solution = db.session.get(Solution, solution_id)
    if not solution or not _check_access(solution, write=True):
        abort(404)

    data = request.get_json(silent=True) or {}
    failure_description = data.get("failure_description", "")
    scenario_id = data.get("scenario_id")

    if not failure_description:
        return jsonify({"success": False, "error": "failure_description required"}), 400
    if not scenario_id:
        return jsonify({"success": False, "error": "scenario_id required"}), 400

    # Allow custom thresholds from request (for tuning)
    auto_threshold = data.get("auto_fix_threshold")
    attempt_threshold = data.get("attempt_fix_threshold")
    fixer = AutoFixer(
        auto_fix_threshold=auto_threshold,
        attempt_fix_threshold=attempt_threshold,
    )

    diagnosis = fixer.diagnose(failure_description, solution_id)
    result = fixer.fix_loop(
        diagnosis=diagnosis,
        solution_id=solution_id,
        scenario_id=scenario_id,
        generated_code={},
    )

    return jsonify({"success": True, **result})


@codegen_bp.route("/solutions/<int:solution_id>/codegen/test/history", methods=["GET"])
@login_required
def get_test_run_history(solution_id):
    """Get test run history for a solution."""
    from app.modules.codegen.services.test_scheduler import TestScheduler

    solution = db.session.get(Solution, solution_id)
    if not solution or not _check_access(solution):
        abort(404)

    limit = safe_int_arg('limit', 20, minimum=1, maximum=500)
    scheduler = TestScheduler()
    history = scheduler.get_run_history(solution_id, limit=limit)

    return jsonify({"success": True, "history": history})


@codegen_bp.route("/solutions/<int:solution_id>/codegen/test/version-trend", methods=["GET"])
@login_required
def get_test_version_trend(solution_id):
    """Get test results grouped by version for trend analysis."""
    from app.modules.codegen.services.test_scheduler import TestScheduler

    solution = db.session.get(Solution, solution_id)
    if not solution or not _check_access(solution):
        abort(404)

    scheduler = TestScheduler()
    trend = scheduler.get_version_trend(solution_id)

    return jsonify({"success": True, "trend": trend})


@codegen_bp.route("/solutions/<int:solution_id>/codegen/test/trend", methods=["GET"])
@login_required
def get_test_trend(solution_id):
    """Get daily pass/fail trend for scenario results."""
    from app.modules.codegen.services.scenario_tracker import ScenarioTracker

    solution = db.session.get(Solution, solution_id)
    if not solution or not _check_access(solution):
        abort(404)

    tracker = ScenarioTracker()
    trend = tracker.get_trend(solution_id)
    correlation = tracker.get_failure_correlation(solution_id)

    return jsonify({"success": True, "trend": trend, "failure_correlation": correlation})


@codegen_bp.route("/solutions/<int:solution_id>/codegen/test/schedule", methods=["POST"])
@login_required
def schedule_test_run(solution_id):
    """Schedule a test run (on-demand or post-deploy trigger)."""
    from app.modules.codegen.services.test_scheduler import TestScheduler

    solution = db.session.get(Solution, solution_id)
    if not solution or not _check_access(solution, write=True):
        abort(404)

    data = request.get_json(silent=True) or {}
    trigger_type = data.get("trigger", "on_demand")
    version = data.get("version")

    scheduler = TestScheduler()
    try:
        result = scheduler.trigger(solution_id=solution_id, trigger_type=trigger_type, version=version)
        return jsonify({"success": True, **result})
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400


@codegen_bp.route("/solutions/<int:solution_id>/codegen/test/generate-edge-cases", methods=["POST"])
@login_required
def generate_edge_case_scenarios(solution_id):
    """Generate LLM-based edge case scenarios supplementing deterministic ones."""
    from app.modules.codegen.services.scenario_generator import ScenarioGenerator
    from app.modules.codegen.models import SolutionRule

    solution = db.session.get(Solution, solution_id)
    if not solution or not _check_access(solution, write=True):
        abort(404)

    data = request.get_json(silent=True) or {}
    existing_scenarios = data.get("existing_scenarios", [])

    model_entities = {}
    data_model = getattr(solution, "data_model", None)
    if data_model and isinstance(data_model, dict):
        for entity_name, entity_def in data_model.get("entities", {}).items():
            fields = []
            if isinstance(entity_def, dict):
                fields = list(entity_def.get("fields", {}).keys())
            elif isinstance(entity_def, list):
                fields = entity_def
            model_entities[entity_name] = fields

    rules = []
    active_rules = SolutionRule.query.filter_by(solution_id=solution_id, is_active=True).all()
    for rule in active_rules:
        rules.append({"name": rule.name, "rule_definition": rule.rule_definition or {}})

    problem = ""
    problem_data = getattr(solution, "problem_clarification", None)
    if problem_data:
        if isinstance(problem_data, dict):
            problem = problem_data.get("problem_statement", "")
        else:
            problem = str(problem_data)

    gen = ScenarioGenerator()
    edge_cases = gen.generate_edge_cases(
        problem_statement=problem,
        model_entities=model_entities,
        rules=rules,
        existing_scenarios=existing_scenarios,
    )

    return jsonify({"success": True, "edge_cases": edge_cases, "count": len(edge_cases)})
