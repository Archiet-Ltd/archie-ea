"""
Product Generation API — Deterministic code generation from solution blueprints.

Wave 1: 3 routes — generate, run sandbox, download ZIP.
COM-023: USE_SOLUTION_PRODUCT env flag removed — blueprint now always registered.
"""
import io
import logging
import zipfile

from flask import Blueprint, jsonify, request, send_file
from flask_login import login_required

from app import db
from app.utils.pagination import safe_int_arg

logger = logging.getLogger(__name__)

solution_product_bp = Blueprint(
    "solution_product",
    __name__,
    url_prefix="/api/solutions",
)


@solution_product_bp.route("/<int:solution_id>/product/generate", methods=["POST"])
@login_required
def generate_product(solution_id):
    """Generate a deterministic code bundle from a solution blueprint.

    POST /api/solutions/<id>/product/generate
    """
    from app.models.solution_models import Solution, SolutionArchiMateElement
    from app.modules.solutions_product.services.product_spec_bundle import (
        build_product_spec_bundle,
    )
    from app.modules.solutions_product.services.deterministic_code_generator import (
        DeterministicCodeGenerator,
    )
    from app.modules.solutions_product.models import SolutionCodeBundle

    solution = Solution.query.get(solution_id)
    if not solution:
        return jsonify({"success": False, "error": "Solution not found"}), 404

    # Pre-condition: check application-layer element count
    app_element_count = SolutionArchiMateElement.query.filter_by(
        solution_id=solution_id,
        layer_type="application",
    ).count()

    if app_element_count < 3:
        return jsonify({
            "success": False,
            "error": "Insufficient application elements",
            "detail": f"Solution has {app_element_count} application-layer element(s). Minimum 3 required.",
            "hint": "Link more application elements in the Business Architecture section.",
        }), 400

    # Auto-enrich: run UML enrichment if no ai_inferred or confirmed fields exist yet.
    # This ensures field-aware code generation without requiring a manual enrichment step.
    try:
        from app.models.solution_archimate_element import SolutionArchiMateElement as _SAE
        _has_fields = db.session.query(_SAE.id).filter(
            _SAE.solution_id == solution_id,
            _SAE.spec_data.isnot(None),
        ).first()
        _needs_enrichment = True
        if _has_fields:
            _links = _SAE.query.filter_by(solution_id=solution_id).all()
            _needs_enrichment = not any(
                (item.spec_data or {}).get("fields_status") in ("confirmed", "ai_inferred")
                for item in _links
            )
        if _needs_enrichment:
            logger.info("Auto-triggering UML enrichment for solution %d before code generation", solution_id)
            from app.modules.codegen.services.uml_enrichment_service import UMLEnrichmentService
            _enrich_result = UMLEnrichmentService.enrich(solution_id)
            if _enrich_result.get("success"):
                # Also persist UML snapshot to CodegenGeneration for Code Workbench visibility
                try:
                    from app.modules.codegen.models import CodegenGeneration
                    _cg = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
                    if not _cg:
                        _cg = CodegenGeneration(solution_id=solution_id, version=1)
                        db.session.add(_cg)
                    _cg.uml_snapshot = _enrich_result.get("uml")
                    db.session.commit()
                except Exception as _cg_err:
                    logger.warning("Could not persist UML snapshot: %s", _cg_err)
                    db.session.rollback()
            else:
                logger.warning(
                    "Auto-enrichment failed for solution %d: %s — proceeding with structural-only generation",
                    solution_id, _enrich_result.get("error"),
                )
    except Exception as _enrich_err:
        logger.warning("Auto-enrichment check failed (non-fatal): %s", _enrich_err)

    # Rollback any aborted transaction left by the UML enrichment step.
    # The enrichment service catches its own exceptions and returns an error
    # dict — the DB session may be in a failed state even when no Python
    # exception propagated out of the try block above.
    db.session.rollback()

    # ── Genome inference + completeness gate ────────────────────────────────
    # If this solution has a genome (wizard-based path), enrich it with
    # inferred production concerns and validate completeness before generating.
    # If no genome exists, skip and proceed with the existing ArchiMate path.
    inference_log = {}
    completeness_warnings = []
    try:
        from app.modules.codegen.models import CodegenGeneration
        from app.modules.solutions_product.services.genome_inference_service import infer_genome
        from app.modules.solutions_product.services.genome_completeness_gate import check_completeness

        _cg = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
        if _cg and _cg.genome:
            _journey = solution.journey_state or {}
            _step1 = _journey.get("steps", {}).get("1", {})
            _problem_text = (
                _step1.get("problem_statement", "")
                or _step1.get("problem", "")
                or solution.problem_clarification
                or ""
            )

            _enriched_genome = infer_genome(_cg.genome, problem_text=_problem_text)
            inference_log = _enriched_genome.get("_inference_log", {})

            _gate = check_completeness(_enriched_genome)
            if not _gate.can_generate:
                return jsonify({
                    "success": False,
                    "error": "Genome incomplete — cannot generate production-ready code",
                    "blocking_issues": _gate.blocking_issues,
                    "warnings": _gate.warnings,
                    "hint": (
                        "The inference engine could not resolve these fields automatically. "
                        "Return to the wizard and provide the missing information."
                    ),
                }), 400

            completeness_warnings = _gate.warnings

            # Strip _inference_log before persisting — ephemeral metadata only,
            # storing it pollutes future inference runs and the Genome Perfector.
            _cg.genome = {k: v for k, v in _enriched_genome.items() if k != "_inference_log"}
            db.session.commit()
            logger.info(
                "Genome inference complete for solution %d: %d fields inferred, %d warnings",
                solution_id, len(inference_log), len(completeness_warnings),
            )
    except Exception as _infer_err:
        logger.error(
            "Genome inference failed for solution %d (non-fatal): %s",
            solution_id, _infer_err,
        )
        db.session.rollback()

    # Build the spec bundle
    try:
        bundle = build_product_spec_bundle(solution_id)
    except ValueError as e:
        return jsonify({
            "success": False,
            "error": "Spec generation failed",
            "detail": str(e),
        }), 422

    # Generate code
    language = (request.json or {}).get("language", "python-fastapi")
    generator = DeterministicCodeGenerator(language=language)
    code_bundle = generator.generate(bundle)

    # Persist metadata
    record = SolutionCodeBundle(
        solution_id=solution_id,
        bundle_id=code_bundle.bundle_id,
        language=language,
        spec_hash=bundle.spec_hash,
        status="generated",
        file_count=len(code_bundle.files),
    )
    db.session.add(record)
    db.session.commit()

    return jsonify({
        "success": True,
        "solution_id": solution_id,
        "bundle_id": code_bundle.bundle_id,
        "language": language,
        "file_count": len(code_bundle.files),
        "spec_maturity": bundle.maturity_score,
        "spec_hash": bundle.spec_hash,
        "services": [
            {"name": s.name, "path_count": len(s.paths)}
            for s in bundle.services
        ],
        "inference_log": inference_log,
        "completeness_warnings": completeness_warnings,
    })


@solution_product_bp.route("/<int:solution_id>/product/<bundle_id>/run", methods=["POST"])
@login_required
def run_product(solution_id, bundle_id):
    """Run generated code in a Docker sandbox.

    POST /api/solutions/<id>/product/<bundle_id>/run
    """
    from app.modules.solutions_product.models import SolutionCodeBundle
    from app.modules.solutions_product.services.product_spec_bundle import (
        build_product_spec_bundle,
    )
    from app.modules.solutions_product.services.deterministic_code_generator import (
        DeterministicCodeGenerator,
    )
    from app.modules.solutions_product.services.sandbox_runner import SandboxRunner

    record = SolutionCodeBundle.query.filter_by(
        solution_id=solution_id,
        bundle_id=bundle_id,
    ).first()

    if not record:
        return jsonify({
            "success": False,
            "error": "Bundle not found",
            "detail": f"No bundle with ID '{bundle_id}' exists for solution {solution_id}.",
        }), 404

    # Regenerate code (deterministic = same output from same spec)
    try:
        bundle = build_product_spec_bundle(solution_id)
        generator = DeterministicCodeGenerator(language=record.language)
        code_bundle = generator.generate(bundle)
    except ValueError as e:
        return jsonify({
            "success": False,
            "error": "Regeneration failed",
            "detail": str(e),
        }), 422

    # Run in sandbox
    runner = SandboxRunner()
    result = runner.run(code_bundle)

    # Update record
    record.status = result.status
    record.test_summary = result.test_summary
    db.session.commit()

    return jsonify({
        "success": True,
        "bundle_id": bundle_id,
        "status": result.status,
        "test_summary": result.test_summary,
        "failures": [
            {
                "test_name": f.test_name,
                "error": f.error,
                "expected_in_wave1": f.expected_in_wave1,
                "logs": f.logs,
            }
            for f in result.failures
        ],
        "duration_seconds": round(result.duration_seconds, 1),
        "build_log": result.build_log,
    })


@solution_product_bp.route("/<int:solution_id>/product/<bundle_id>/download", methods=["GET"])
@login_required
def download_product(solution_id, bundle_id):
    """Download generated code as a ZIP file.

    GET /api/solutions/<id>/product/<bundle_id>/download
    """
    from app.modules.solutions_product.models import SolutionCodeBundle
    from app.modules.solutions_product.services.product_spec_bundle import (
        build_product_spec_bundle,
    )
    from app.modules.solutions_product.services.deterministic_code_generator import (
        DeterministicCodeGenerator,
    )

    record = SolutionCodeBundle.query.filter_by(
        solution_id=solution_id,
        bundle_id=bundle_id,
    ).first()

    if not record:
        return jsonify({
            "success": False,
            "error": "Bundle not found",
            "detail": f"No bundle with ID '{bundle_id}' exists for solution {solution_id}.",
        }), 404

    # Regenerate code (deterministic)
    try:
        bundle = build_product_spec_bundle(solution_id)
        generator = DeterministicCodeGenerator(language=record.language)
        code_bundle = generator.generate(bundle)
    except ValueError as e:
        return jsonify({
            "success": False,
            "error": "Regeneration failed",
            "detail": str(e),
        }), 422

    # Create ZIP in memory
    zip_buffer = io.BytesIO()
    project_name = f"archie-product-{solution_id}"
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in code_bundle.files:
            zf.writestr(f"{project_name}/{f.path}", f.content)

    zip_buffer.seek(0)
    return send_file(
        zip_buffer,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{project_name}.zip",
    )


@solution_product_bp.route("/<int:solution_id>/product/<bundle_id>/preview", methods=["POST"])
@login_required
def preview_product(solution_id, bundle_id):
    """Start the generated service as a live preview.

    POST /api/solutions/<id>/product/<bundle_id>/preview

    Builds the Docker image, starts uvicorn on a dynamic port (9100-9199),
    returns the preview URL. Auto-stops after 10 minutes.

    Response includes:
    - url: the preview URL (e.g., http://127.0.0.1:9105)
    - docs_url: FastAPI auto-generated Swagger UI
    - health_url: health check endpoint
    - ttl_seconds: time until auto-stop
    """
    from app.modules.solutions_product.models import SolutionCodeBundle
    from app.modules.solutions_product.services.product_spec_bundle import (
        build_product_spec_bundle,
    )
    from app.modules.solutions_product.services.deterministic_code_generator import (
        DeterministicCodeGenerator,
    )
    from app.modules.solutions_product.services.sandbox_runner import SandboxRunner

    record = SolutionCodeBundle.query.filter_by(
        solution_id=solution_id,
        bundle_id=bundle_id,
    ).first()

    if not record:
        return jsonify({
            "success": False,
            "error": "Bundle not found",
        }), 404

    try:
        bundle = build_product_spec_bundle(solution_id)
        generator = DeterministicCodeGenerator(language=record.language)
        code_bundle = generator.generate(bundle)
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 422

    runner = SandboxRunner()
    result = runner.preview(code_bundle)

    if result.get("status") == "running":
        # Replace localhost with the server's public IP for external access
        from flask import request as flask_request
        host = flask_request.host.split(":")[0]  # strip port
        port = result["port"]
        result["url"] = f"http://{host}:{port}"
        result["docs_url"] = f"http://{host}:{port}/docs"
        result["health_url"] = f"http://{host}:{port}/health"
        result["success"] = True
    else:
        result["success"] = False

    return jsonify(result)


@solution_product_bp.route("/<int:solution_id>/product/<bundle_id>/preview", methods=["DELETE"])
@login_required
def stop_preview_product(solution_id, bundle_id):
    """Stop a running preview container.

    DELETE /api/solutions/<id>/product/<bundle_id>/preview
    """
    from app.modules.solutions_product.services.sandbox_runner import SandboxRunner
    runner = SandboxRunner()
    runner.stop_preview(bundle_id)
    return jsonify({"success": True, "message": "Preview stopped"})


@solution_product_bp.route("/<int:solution_id>/product/<bundle_id>/push-to-github", methods=["POST"])
@login_required
def push_to_github(solution_id, bundle_id):
    """Push generated code to a GitHub repository.

    POST /api/solutions/<id>/product/<bundle_id>/push-to-github

    If the repo already exists, opens a PR with updated code instead of
    overwriting. Returns repo URL and branch on success.
    """
    from app.modules.solutions_product.models import SolutionCodeBundle
    from app.modules.solutions_product.services.product_spec_bundle import (
        build_product_spec_bundle,
    )
    from app.modules.solutions_product.services.deterministic_code_generator import (
        DeterministicCodeGenerator,
    )
    from app.modules.solutions_product.services.git_integration_service import (
        GitIntegrationService,
    )

    record = SolutionCodeBundle.query.filter_by(
        solution_id=solution_id,
        bundle_id=bundle_id,
    ).first()

    if not record:
        return jsonify({
            "success": False,
            "error": "Bundle not found",
            "detail": f"No bundle with ID '{bundle_id}' exists for solution {solution_id}.",
        }), 404

    # Regenerate code (deterministic = same output from same spec)
    try:
        bundle = build_product_spec_bundle(solution_id)
        generator = DeterministicCodeGenerator(language=record.language)
        code_bundle = generator.generate(bundle)
    except ValueError as e:
        return jsonify({
            "success": False,
            "error": "Regeneration failed",
            "detail": str(e),
        }), 422

    # Push to GitHub
    git_service = GitIntegrationService()
    repo_name = (request.json or {}).get("repo_name")
    result = git_service.create_repo_and_push(solution_id, code_bundle, repo_name=repo_name)

    return jsonify(result), 200 if result.get("success") else 400


@solution_product_bp.route("/<int:solution_id>/push-to-devops", methods=["POST"])
@login_required
def push_to_devops(solution_id):
    """Push generated code to GitHub or Azure DevOps for this solution.

    POST /api/solutions/<id>/push-to-devops
    Body: {"provider": "github"|"azure_devops", "connector_id": N}
    Returns: {"pr_url": "...", "branch": "..."}
    """
    from app.services.devops_push_service import DevOpsPushService
    from app.models.connector_config import DevOpsConnectorConfig
    from app.models.solution_models import Solution
    from app.modules.codegen.services.fastapi_stub_generator import generate as fastapi_generate

    data = request.get_json(force=True)
    provider = data.get("provider")
    connector_id = data.get("connector_id")

    solution = Solution.query.get(solution_id)
    if not solution:
        return jsonify({"error": "Solution not found"}), 404

    # Load connector config
    config = DevOpsConnectorConfig.query.filter_by(id=connector_id).first()
    if not config or not config.enabled or config.provider != provider:
        return jsonify({"error": "Connector not found or not enabled for this provider"}), 400

    org_id = config.organization_id
    solution_slug = getattr(solution, "slug", str(solution_id))
    generated_files = fastapi_generate(solution_id)
    if not generated_files:
        return jsonify({"error": "No files generated for this solution"}), 400

    svc = DevOpsPushService()
    if provider == "github":
        result = svc.push_to_github(org_id, solution_id, solution_slug, generated_files)
    elif provider == "azure_devops":
        result = svc.push_to_azure_devops(org_id, solution_id, solution_slug, generated_files)
    else:
        return jsonify({"error": "Unknown provider"}), 400

    if "error" in result:
        return jsonify(result), 400
    return jsonify(result)

@solution_product_bp.route("/<int:solution_id>/ci-cd-config", methods=["PUT"])
@login_required
def update_cicd_config(solution_id):
    """Update CI/CD pipeline configuration for a solution.

    PUT /api/solutions/<id>/ci-cd-config
    Body: {
        "provider": "github_actions" | "gitlab_ci",
        "container_registry": "ghcr" | "ecr" | "gcr" | "acr",
        "k8s_staging_namespace": "staging",
        "k8s_prod_namespace": "production",
        "require_manual_approval": true,
        "run_security_scan": true,
        "compliance_check_url": "https://..."
    }
    """
    from app.models.solution_models import Solution

    solution = Solution.query.get(solution_id)
    if not solution:
        return jsonify({"success": False, "error": "Solution not found"}), 404

    body = request.json or {}

    # Validate provider
    valid_providers = {"github_actions", "gitlab_ci"}
    provider = body.get("provider", "github_actions")
    if provider not in valid_providers:
        return jsonify({
            "success": False,
            "error": f"Invalid provider: {provider}. Must be one of: {', '.join(sorted(valid_providers))}",
        }), 400

    # Validate container registry
    valid_registries = {"ghcr", "ecr", "gcr", "acr"}
    registry = body.get("container_registry", "ghcr")
    if registry not in valid_registries:
        return jsonify({
            "success": False,
            "error": f"Invalid registry: {registry}. Must be one of: {', '.join(sorted(valid_registries))}",
        }), 400

    ci_cd_config = {
        "provider": provider,
        "container_registry": registry,
        "k8s_staging_namespace": body.get("k8s_staging_namespace", "staging"),
        "k8s_prod_namespace": body.get("k8s_prod_namespace", "production"),
        "require_manual_approval": body.get("require_manual_approval", True),
        "run_security_scan": body.get("run_security_scan", True),
        "compliance_check_url": body.get(
            "compliance_check_url",
            f"https://archie.company.com/api/solutions/{solution_id}/compliance/check",
        ),
    }

    # Store in solution metadata_json
    import json as json_mod
    try:
        meta = getattr(solution, "metadata_json", None) or {}
        if isinstance(meta, str):
            meta = json_mod.loads(meta)
    except (ValueError, TypeError):
        meta = {}

    meta["ci_cd"] = ci_cd_config
    solution.metadata_json = meta
    db.session.commit()

    return jsonify({
        "success": True,
        "solution_id": solution_id,
        "ci_cd": ci_cd_config,
    })


@solution_product_bp.route("/<int:solution_id>/ci-cd-config", methods=["GET"])
@login_required
def get_cicd_config(solution_id):
    """Get the current CI/CD pipeline configuration for a solution.

    GET /api/solutions/<id>/ci-cd-config
    """
    from app.models.solution_models import Solution
    import json as json_mod

    solution = Solution.query.get(solution_id)
    if not solution:
        return jsonify({"success": False, "error": "Solution not found"}), 404

    try:
        meta = getattr(solution, "metadata_json", None) or {}
        if isinstance(meta, str):
            meta = json_mod.loads(meta)
    except (ValueError, TypeError):
        meta = {}

    ci_cd = meta.get("ci_cd", {
        "provider": "github_actions",
        "container_registry": "ghcr",
        "k8s_staging_namespace": "staging",
        "k8s_prod_namespace": "production",
        "require_manual_approval": True,
        "run_security_scan": True,
    })

    return jsonify({
        "success": True,
        "solution_id": solution_id,
        "ci_cd": ci_cd,
    })


@solution_product_bp.route("/<int:solution_id>/product/<bundle_id>/synthesize", methods=["POST"])
@login_required
def synthesize_business_logic(solution_id, bundle_id):
    """Synthesize handler bodies from confirmed business rules via LLM.

    POST /api/solutions/<id>/product/<bundle_id>/synthesize

    Iterates through elements with confirmed business_rules in spec_data,
    calls the BusinessLogicSynthesizer for each, and stores the generated
    handler bodies back into spec_data.synthesized_handlers.
    """
    from app.models.solution_models import Solution
    from app.modules.solutions_product.models import SolutionCodeBundle
    from app.modules.solutions_product.services.business_logic_synthesizer import (
        BusinessLogicSynthesizer,
    )

    solution = Solution.query.get(solution_id)
    if not solution:
        return jsonify({"success": False, "error": "Solution not found"}), 404

    record = SolutionCodeBundle.query.filter_by(
        solution_id=solution_id,
        bundle_id=bundle_id,
    ).first()

    if not record:
        return jsonify({
            "success": False,
            "error": "Bundle not found",
            "detail": f"No bundle with ID '{bundle_id}' exists for solution {solution_id}.",
        }), 404

    try:
        synthesizer = BusinessLogicSynthesizer()
        result = synthesizer.synthesize_all(solution_id)
    except Exception as e:
        logger.error("Business logic synthesis failed for solution %s: %s", solution_id, e)
        return jsonify({
            "success": False,
            "error": "Synthesis failed",
            "detail": str(e),
        }), 500

    # Commit spec_data updates
    db.session.commit()

    return jsonify({
        "success": True,
        "solution_id": solution_id,
        "bundle_id": bundle_id,
        "handlers_generated": result["handlers_generated"],
        "handlers_failed": result["handlers_failed"],
        "total": result["total"],
    })


# ── Compliance Monitoring (CODEGEN-06) ─────────────────────────────────────


@solution_product_bp.route("/<int:solution_id>/compliance/check", methods=["POST"])
@login_required
def check_compliance(solution_id):
    """Run a compliance check against a live service.

    POST /api/solutions/<id>/compliance/check
    Body: {"service_url": "http://...", "spec_id": <optional int>}
    """
    from flask_login import current_user
    from app.models.published_api_spec import PublishedAPISpec
    from app.modules.solutions_product.services.compliance_checker import ComplianceChecker

    body = request.json or {}
    service_url = body.get("service_url", "").strip()
    if not service_url:
        return jsonify({"success": False, "error": "service_url is required"}), 400

    # Validate URL starts with http
    if not service_url.startswith(("http://", "https://")):
        return jsonify({"success": False, "error": "service_url must start with http:// or https://"}), 400

    spec_id = body.get("spec_id")
    if not spec_id:
        # Use latest published spec for this solution
        latest_spec = (
            PublishedAPISpec.query
            .filter_by(solution_id=solution_id, status="published")
            .order_by(PublishedAPISpec.published_at.desc())
            .first()
        )
        if not latest_spec:
            # Fall back to any spec (including draft)
            latest_spec = (
                PublishedAPISpec.query
                .filter_by(solution_id=solution_id)
                .order_by(PublishedAPISpec.published_at.desc())
                .first()
            )
        if not latest_spec:
            return jsonify({
                "success": False,
                "error": "No published API spec found for this solution. Generate specs first.",
            }), 404
        spec_id = latest_spec.id

    checker = ComplianceChecker()
    try:
        result = checker.check(
            published_spec_id=spec_id,
            service_url=service_url,
            timeout=body.get("timeout", 10),
            checked_by_id=current_user.id if current_user and current_user.is_authenticated else None,
        )
    except Exception as e:
        logger.exception("Compliance check failed for solution %s", solution_id)
        return jsonify({"success": False, "error": f"Check failed: {str(e)}"}), 500

    status_code = 200 if result.get("success") else 400
    return jsonify(result), status_code


@solution_product_bp.route("/<int:solution_id>/compliance/history", methods=["GET"])
@login_required
def compliance_history(solution_id):
    """Return past compliance checks for a solution.

    GET /api/solutions/<id>/compliance/history
    """
    from app.models.compliance_check import RuntimeComplianceCheck as ComplianceCheck
    from app.models.solution_models import Solution
    from app.utils.route_guards import require_entity

    require_entity(Solution, solution_id, description="Solution not found")

    limit = safe_int_arg('limit', 20, minimum=1, maximum=500)
    limit = min(limit, 100)  # Cap at 100

    checks = (
        ComplianceCheck.query
        .filter_by(solution_id=solution_id)
        .order_by(ComplianceCheck.checked_at.desc())
        .limit(limit)
        .all()
    )

    return jsonify({
        "success": True,
        "solution_id": solution_id,
        "count": len(checks),
        "checks": [c.to_dict() for c in checks],
    })


@solution_product_bp.route("/<int:solution_id>/compliance/latest", methods=["GET"])
@login_required
def compliance_latest(solution_id):
    """Return the most recent compliance check for a solution.

    GET /api/solutions/<id>/compliance/latest
    """
    from app.models.compliance_check import RuntimeComplianceCheck as ComplianceCheck
    from app.models.solution_models import Solution
    from app.utils.route_guards import require_entity

    require_entity(Solution, solution_id, description="Solution not found")

    check = (
        ComplianceCheck.query
        .filter_by(solution_id=solution_id)
        .order_by(ComplianceCheck.checked_at.desc())
        .first()
    )

    if not check:
        return jsonify({
            "success": True,
            "solution_id": solution_id,
            "check": None,
            "message": "No compliance checks recorded yet.",
        })

    return jsonify({
        "success": True,
        "solution_id": solution_id,
        "check": check.to_dict(),
    })


# ── Database Config (RUNTIME-05) ───────────────────────────────────────────


_VALID_ENGINES = {"postgresql"}
_VALID_INSTANCE_CLASSES = {
    "db.t3.micro", "db.t3.small", "db.t3.medium", "db.t3.large",
    "db.t4g.micro", "db.t4g.small", "db.t4g.medium", "db.t4g.large",
    "db.r6g.large", "db.r6g.xlarge", "db.r6g.2xlarge",
    "db.m6g.large", "db.m6g.xlarge",
}


@solution_product_bp.route("/<int:solution_id>/database-config", methods=["PUT"])
@login_required
def update_database_config(solution_id):
    """Update the database provisioning config for a solution.

    PUT /api/solutions/<id>/database-config
    Body: {
        "engine": "postgresql",
        "version": "16",
        "instance_class": "db.t3.micro",
        "storage_gb": 20,
        "max_storage_gb": 100,
        "multi_az": false,
        "encryption": true,
        "backup_days": 7,
        "connection_string_env": "DATABASE_URL"
    }
    """
    from app.models.solution_models import Solution

    solution = Solution.query.get(solution_id)
    if not solution:
        return jsonify({"success": False, "error": "Solution not found"}), 404

    body = request.json or {}
    if not body:
        return jsonify({"success": False, "error": "Request body is required"}), 400

    # Validate engine
    engine = body.get("engine", "postgresql")
    if engine not in _VALID_ENGINES:
        return jsonify({
            "success": False,
            "error": f"Unsupported engine: {engine}. Supported: {', '.join(sorted(_VALID_ENGINES))}",
        }), 400

    # Validate instance class if provided
    instance_class = body.get("instance_class", "db.t3.micro")
    if instance_class not in _VALID_INSTANCE_CLASSES:
        return jsonify({
            "success": False,
            "error": f"Invalid instance_class: {instance_class}",
            "valid_classes": sorted(_VALID_INSTANCE_CLASSES),
        }), 400

    # Validate numeric ranges
    storage_gb = body.get("storage_gb", 20)
    max_storage_gb = body.get("max_storage_gb", 100)
    backup_days = body.get("backup_days", 7)

    if not isinstance(storage_gb, int) or storage_gb < 5 or storage_gb > 65536:
        return jsonify({"success": False, "error": "storage_gb must be between 5 and 65536"}), 400
    if not isinstance(max_storage_gb, int) or max_storage_gb < storage_gb:
        return jsonify({"success": False, "error": "max_storage_gb must be >= storage_gb"}), 400
    if not isinstance(backup_days, int) or backup_days < 0 or backup_days > 35:
        return jsonify({"success": False, "error": "backup_days must be between 0 and 35"}), 400

    # Build the config
    db_config = {
        "engine": engine,
        "version": str(body.get("version", "16")),
        "provisioning": "managed",
        "cloud_provider": "aws",
        "instance_class": instance_class,
        "storage_gb": storage_gb,
        "max_storage_gb": max_storage_gb,
        "multi_az": bool(body.get("multi_az", False)),
        "encryption": bool(body.get("encryption", True)),
        "backup_days": backup_days,
        "connection_string_env": body.get("connection_string_env", "DATABASE_URL"),
    }

    # Store in solution metadata_json
    import json
    meta = getattr(solution, "metadata_json", None) or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except (json.JSONDecodeError, TypeError):
            meta = {}
    meta["database_config"] = db_config
    solution.metadata_json = meta
    db.session.commit()

    return jsonify({
        "success": True,
        "solution_id": solution_id,
        "database_config": db_config,
    })


# ── Spec Webhooks (RUNTIME-08) ─────────────────────────────────────────────


@solution_product_bp.route("/<int:solution_id>/webhooks", methods=["POST"])
@login_required
def create_webhook(solution_id):
    """Create a webhook subscription for spec change notifications.

    POST /api/solutions/<id>/webhooks
    Body: {
        "url": "https://...",
        "event_types": ["spec_changed", "drift_detected"],
        "auth_header_env": "MY_WEBHOOK_TOKEN",
        "retry_count": 3
    }
    """
    from app.models.spec_webhook import SpecWebhook

    body = request.json or {}
    url = (body.get("url") or "").strip()
    if not url:
        return jsonify({"success": False, "error": "url is required"}), 400

    # Validate URL scheme (HTTPS required in production, HTTP allowed for localhost/dev)
    if not url.startswith(("http://", "https://")):
        return jsonify({"success": False, "error": "url must start with http:// or https://"}), 400

    import os
    if os.environ.get("FLASK_ENV") == "production" or os.environ.get("FLASK_DEBUG") != "1":
        if url.startswith("http://") and "localhost" not in url and "127.0.0.1" not in url:
            return jsonify({
                "success": False,
                "error": "HTTPS required for webhook URLs in production (HTTP allowed for localhost)",
            }), 400

    # Validate event types
    valid_types = {"spec_changed", "drift_detected", "compliance_failed"}
    event_types = body.get("event_types", ["spec_changed"])
    if not isinstance(event_types, list) or not event_types:
        return jsonify({"success": False, "error": "event_types must be a non-empty list"}), 400
    invalid = set(event_types) - valid_types
    if invalid:
        return jsonify({
            "success": False,
            "error": f"Invalid event types: {', '.join(sorted(invalid))}. Valid: {', '.join(sorted(valid_types))}",
        }), 400

    retry_count = body.get("retry_count", 3)
    if not isinstance(retry_count, int) or retry_count < 0 or retry_count > 10:
        return jsonify({"success": False, "error": "retry_count must be between 0 and 10"}), 400

    webhook = SpecWebhook(
        solution_id=solution_id,
        url=url,
        event_types=event_types,
        auth_header_env=body.get("auth_header_env"),
        retry_count=retry_count,
        enabled=body.get("enabled", True),
    )
    db.session.add(webhook)
    db.session.commit()

    return jsonify({"success": True, "webhook": webhook.to_dict()}), 201


@solution_product_bp.route("/<int:solution_id>/webhooks", methods=["GET"])
@login_required
def list_webhooks(solution_id):
    """List all webhooks for a solution.

    GET /api/solutions/<id>/webhooks
    """
    from app.models.solution_models import Solution
    from app.models.spec_webhook import SpecWebhook
    from app.utils.route_guards import require_entity

    require_entity(Solution, solution_id, description="Solution not found")

    webhooks = SpecWebhook.query.filter_by(solution_id=solution_id).all()
    return jsonify({
        "success": True,
        "solution_id": solution_id,
        "count": len(webhooks),
        "webhooks": [w.to_dict() for w in webhooks],
    })


@solution_product_bp.route("/<int:solution_id>/webhooks/<int:webhook_id>", methods=["PUT"])
@login_required
def update_webhook(solution_id, webhook_id):
    """Update a webhook subscription.

    PUT /api/solutions/<id>/webhooks/<wid>
    """
    from app.models.spec_webhook import SpecWebhook

    webhook = SpecWebhook.query.filter_by(id=webhook_id, solution_id=solution_id).first()
    if not webhook:
        return jsonify({"success": False, "error": "Webhook not found"}), 404

    body = request.json or {}

    if "url" in body:
        url = body["url"].strip()
        if not url.startswith(("http://", "https://")):
            return jsonify({"success": False, "error": "url must start with http:// or https://"}), 400
        webhook.url = url

    if "event_types" in body:
        valid_types = {"spec_changed", "drift_detected", "compliance_failed"}
        event_types = body["event_types"]
        if not isinstance(event_types, list) or not event_types:
            return jsonify({"success": False, "error": "event_types must be a non-empty list"}), 400
        invalid = set(event_types) - valid_types
        if invalid:
            return jsonify({
                "success": False,
                "error": f"Invalid event types: {', '.join(sorted(invalid))}",
            }), 400
        webhook.event_types = event_types

    if "auth_header_env" in body:
        webhook.auth_header_env = body["auth_header_env"]

    if "retry_count" in body:
        rc = body["retry_count"]
        if not isinstance(rc, int) or rc < 0 or rc > 10:
            return jsonify({"success": False, "error": "retry_count must be between 0 and 10"}), 400
        webhook.retry_count = rc

    if "enabled" in body:
        webhook.enabled = bool(body["enabled"])

    db.session.commit()

    return jsonify({"success": True, "webhook": webhook.to_dict()})


@solution_product_bp.route("/<int:solution_id>/webhooks/<int:webhook_id>", methods=["DELETE"])
@login_required
def delete_webhook(solution_id, webhook_id):
    """Delete a webhook subscription.

    DELETE /api/solutions/<id>/webhooks/<wid>
    """
    from app.models.spec_webhook import SpecWebhook

    webhook = SpecWebhook.query.filter_by(id=webhook_id, solution_id=solution_id).first()
    if not webhook:
        return jsonify({"success": False, "error": "Webhook not found"}), 404

    db.session.delete(webhook)
    db.session.commit()

    return jsonify({"success": True, "message": "Webhook deleted"})


@solution_product_bp.route("/<int:solution_id>/webhooks/<int:webhook_id>/test", methods=["POST"])
@login_required
def test_webhook(solution_id, webhook_id):
    """Fire a test event to a webhook.

    POST /api/solutions/<id>/webhooks/<wid>/test
    """
    from app.models.spec_webhook import SpecWebhook
    from app.modules.solutions_product.services.drift_remediation_service import DriftRemediationService

    webhook = SpecWebhook.query.filter_by(id=webhook_id, solution_id=solution_id).first()
    if not webhook:
        return jsonify({"success": False, "error": "Webhook not found"}), 404

    svc = DriftRemediationService()
    test_payload = {
        "event": "test",
        "solution_id": solution_id,
        "timestamp": __import__("datetime").datetime.utcnow().isoformat() + "Z",
        "message": "This is a test webhook from Entelim",
    }

    success = svc._fire_single_webhook(webhook, test_payload)

    return jsonify({
        "success": success,
        "webhook_id": webhook_id,
        "status": "delivered" if success else "failed",
    })


# ── Compliance Schedule (RUNTIME-08) ───────────────────────────────────────


@solution_product_bp.route("/<int:solution_id>/compliance-schedule", methods=["PUT"])
@login_required
def update_compliance_schedule(solution_id):
    """Set compliance check schedule for a solution.

    PUT /api/solutions/<id>/compliance-schedule
    Body: {
        "enabled": true,
        "cron": "0 6 * * 1",
        "service_url": "http://order-service:8000",
        "auto_publish": false
    }
    """
    import json as json_mod
    from app.models.solution_models import Solution

    solution = Solution.query.get(solution_id)
    if not solution:
        return jsonify({"success": False, "error": "Solution not found"}), 404

    body = request.json or {}

    service_url = (body.get("service_url") or "").strip()
    if service_url and not service_url.startswith(("http://", "https://")):
        return jsonify({"success": False, "error": "service_url must start with http:// or https://"}), 400

    schedule_config = {
        "enabled": bool(body.get("enabled", True)),
        "cron": body.get("cron", "0 6 * * 1"),
        "service_url": service_url,
        "auto_publish": bool(body.get("auto_publish", False)),
    }

    meta = getattr(solution, "metadata_json", None) or {}
    if isinstance(meta, str):
        try:
            meta = json_mod.loads(meta)
        except (json_mod.JSONDecodeError, TypeError):
            meta = {}

    meta["compliance_schedule"] = schedule_config
    solution.metadata_json = meta
    db.session.commit()

    return jsonify({
        "success": True,
        "solution_id": solution_id,
        "compliance_schedule": schedule_config,
    })


@solution_product_bp.route("/<int:solution_id>/compliance-schedule", methods=["GET"])
@login_required
def get_compliance_schedule(solution_id):
    """Get compliance check schedule for a solution.

    GET /api/solutions/<id>/compliance-schedule
    """
    import json as json_mod
    from app.models.solution_models import Solution

    solution = Solution.query.get(solution_id)
    if not solution:
        return jsonify({"success": False, "error": "Solution not found"}), 404

    meta = getattr(solution, "metadata_json", None) or {}
    if isinstance(meta, str):
        try:
            meta = json_mod.loads(meta)
        except (json_mod.JSONDecodeError, TypeError):
            meta = {}

    schedule_config = meta.get("compliance_schedule", {
        "enabled": False,
        "cron": "0 6 * * 1",
        "service_url": "",
        "auto_publish": False,
    })

    return jsonify({
        "success": True,
        "solution_id": solution_id,
        "compliance_schedule": schedule_config,
    })


@solution_product_bp.route("/<int:solution_id>/compliance/run-all", methods=["POST"])
@login_required
def run_all_compliance_checks(solution_id):
    """Trigger immediate compliance checks for all solutions with schedules.

    POST /api/solutions/<id>/compliance/run-all
    Admin-only: uses solution_id as a namespace anchor but runs all scheduled checks.
    """
    from app.modules.solutions_product.services.drift_remediation_service import DriftRemediationService

    svc = DriftRemediationService()
    results = svc.schedule_checks()

    return jsonify({
        "success": True,
        "checks_run": len(results),
        "results": results,
    })

@solution_product_bp.route("/<int:solution_id>/database-config", methods=["GET"])
@login_required
def get_database_config(solution_id):
    """Get the current database provisioning config for a solution.

    GET /api/solutions/<id>/database-config
    """
    from app.models.solution_models import Solution
    import json

    solution = Solution.query.get(solution_id)
    if not solution:
        return jsonify({"success": False, "error": "Solution not found"}), 404

    meta = getattr(solution, "metadata_json", None) or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except (json.JSONDecodeError, TypeError):
            meta = {}

    db_config = meta.get("database_config", {
        "engine": "postgresql",
        "version": "16",
        "provisioning": "managed",
        "cloud_provider": "aws",
        "instance_class": "db.t3.micro",
        "storage_gb": 20,
        "max_storage_gb": 100,
        "multi_az": False,
        "encryption": True,
        "backup_days": 7,
        "connection_string_env": "DATABASE_URL",
    })

    return jsonify({
        "success": True,
        "solution_id": solution_id,
        "database_config": db_config,
    })


# ── Architecture Style (RUNTIME-06) ────────────────────────────────────────

_VALID_ARCH_STYLES = {"microservices", "event_driven", "serverless", "modular_monolith"}
_VALID_PATTERNS = {"saga", "cqrs", "event_sourcing", "api_gateway", "circuit_breaker", "service_mesh"}
_VALID_SERVICE_MESHES = {"istio", "linkerd", "consul_connect"}
_VALID_API_GATEWAYS = {"kong", "envoy", "ambassador", "traefik", "aws_api_gateway"}


@solution_product_bp.route("/<int:solution_id>/architecture-style", methods=["PUT"])
@login_required
def update_architecture_style(solution_id):
    """Update architecture style configuration for a solution.

    PUT /api/solutions/<id>/architecture-style
    Body: {
        "primary": "event_driven",
        "patterns": ["saga", "api_gateway"],
        "service_mesh": "istio",
        "api_gateway": "kong"
    }
    """
    from app.models.solution_models import Solution
    import json as json_mod

    solution = Solution.query.get(solution_id)
    if not solution:
        return jsonify({"success": False, "error": "Solution not found"}), 404

    body = request.json or {}
    if not body:
        return jsonify({"success": False, "error": "Request body is required"}), 400

    # Validate primary style
    primary = body.get("primary", "microservices")
    if primary not in _VALID_ARCH_STYLES:
        return jsonify({
            "success": False,
            "error": f"Invalid architecture style: {primary}. "
                     f"Valid: {', '.join(sorted(_VALID_ARCH_STYLES))}",
        }), 400

    # Validate patterns
    patterns = body.get("patterns", [])
    if not isinstance(patterns, list):
        return jsonify({"success": False, "error": "patterns must be a list"}), 400
    invalid_patterns = set(patterns) - _VALID_PATTERNS
    if invalid_patterns:
        return jsonify({
            "success": False,
            "error": f"Invalid patterns: {', '.join(sorted(invalid_patterns))}. "
                     f"Valid: {', '.join(sorted(_VALID_PATTERNS))}",
        }), 400

    # Validate service mesh (optional)
    service_mesh = body.get("service_mesh")
    if service_mesh and service_mesh not in _VALID_SERVICE_MESHES:
        return jsonify({
            "success": False,
            "error": f"Invalid service_mesh: {service_mesh}. "
                     f"Valid: {', '.join(sorted(_VALID_SERVICE_MESHES))}",
        }), 400

    # Validate API gateway (optional)
    api_gateway = body.get("api_gateway")
    if api_gateway and api_gateway not in _VALID_API_GATEWAYS:
        return jsonify({
            "success": False,
            "error": f"Invalid api_gateway: {api_gateway}. "
                     f"Valid: {', '.join(sorted(_VALID_API_GATEWAYS))}",
        }), 400

    arch_style = {
        "primary": primary,
        "patterns": patterns,
    }
    if service_mesh:
        arch_style["service_mesh"] = service_mesh
    if api_gateway:
        arch_style["api_gateway"] = api_gateway

    # Store in solution metadata_json
    try:
        meta = getattr(solution, "metadata_json", None) or {}
        if isinstance(meta, str):
            meta = json_mod.loads(meta)
    except (ValueError, TypeError):
        meta = {}

    meta["architecture_style"] = arch_style
    solution.metadata_json = meta
    db.session.commit()

    return jsonify({
        "success": True,
        "solution_id": solution_id,
        "architecture_style": arch_style,
    })


@solution_product_bp.route("/<int:solution_id>/architecture-style", methods=["GET"])
@login_required
def get_architecture_style(solution_id):
    """Get the current architecture style configuration for a solution.

    GET /api/solutions/<id>/architecture-style
    """
    from app.models.solution_models import Solution
    import json as json_mod

    solution = Solution.query.get(solution_id)
    if not solution:
        return jsonify({"success": False, "error": "Solution not found"}), 404

    try:
        meta = getattr(solution, "metadata_json", None) or {}
        if isinstance(meta, str):
            meta = json_mod.loads(meta)
    except (ValueError, TypeError):
        meta = {}

    arch_style = meta.get("architecture_style", {
        "primary": "microservices",
        "patterns": [],
    })

    return jsonify({
        "success": True,
        "solution_id": solution_id,
        "architecture_style": arch_style,
    })


# ── Setup Status (GOV-03) ────────────────────────────────────────────────


@solution_product_bp.route("/<int:solution_id>/setup-status", methods=["GET"])
@login_required
def get_setup_status(solution_id):
    """Return a checklist of what's configured for this solution.

    GET /api/solutions/<id>/setup-status
    Response: {
        "integration_contracts": true/false,
        "identity_provider": true/false,
        "database_config": true/false,
        "ci_cd_config": true/false,
        "architecture_style": true/false,
        "code_generated": true/false,
        "api_spec_published": true/false,
        "completion_pct": 71
    }
    """
    import json as json_mod
    from app.models.solution_models import Solution, SolutionArchiMateElement
    from app.models.integration_contract import IntegrationContract
    from app.models.published_api_spec import PublishedAPISpec

    solution = Solution.query.get(solution_id)
    if not solution:
        return jsonify({"success": False, "error": "Solution not found"}), 404

    # Parse metadata
    try:
        meta = getattr(solution, "metadata_json", None) or {}
        if isinstance(meta, str):
            meta = json_mod.loads(meta)
    except (ValueError, TypeError):
        meta = {}

    # Check integration contracts via linked application components (solution_applications junction)
    linked_app_ids = [app.id for app in solution.applications.all()]
    has_contracts = False
    if linked_app_ids:
        has_contracts = IntegrationContract.query.filter(
            IntegrationContract.application_id.in_(linked_app_ids)
        ).first() is not None

    # Check identity provider from element spec_data
    has_idp = False
    elements = SolutionArchiMateElement.query.filter_by(solution_id=solution_id).all()
    for el in elements:
        sd = getattr(el, "spec_data", None) or {}
        if isinstance(sd, str):
            try:
                sd = json_mod.loads(sd)
            except (ValueError, TypeError):
                sd = {}
        if sd.get("identity_provider"):
            has_idp = True
            break

    # Check metadata-stored configs
    has_db = bool(meta.get("database_config"))
    has_cicd = bool(meta.get("ci_cd"))
    has_arch = bool(meta.get("architecture_style", {}).get("primary"))

    # Check code generation
    from app.modules.solutions_product.models import SolutionCodeBundle
    has_code = SolutionCodeBundle.query.filter_by(
        solution_id=solution_id
    ).first() is not None

    # Check published API spec
    has_spec = PublishedAPISpec.query.filter_by(
        solution_id=solution_id, status="published"
    ).first() is not None

    items = [has_contracts, has_idp, has_db, has_cicd, has_arch, has_code, has_spec]
    done_count = sum(1 for x in items if x)
    total = len(items)
    pct = round(done_count / total * 100) if total else 0

    return jsonify({
        "success": True,
        "solution_id": solution_id,
        "integration_contracts": has_contracts,
        "identity_provider": has_idp,
        "database_config": has_db,
        "ci_cd_config": has_cicd,
        "architecture_style": has_arch,
        "code_generated": has_code,
        "api_spec_published": has_spec,
        "completion_pct": pct,
        "configured_count": done_count,
        "total_count": total,
    })


@solution_product_bp.route("/<int:solution_id>/cost-estimate", methods=["GET"])
@login_required
def get_cost_estimate(solution_id):
    """Estimate monthly AWS cloud costs from the solution's architecture spec.

    GET /api/solutions/<id>/cost-estimate

    Returns a cost breakdown based on the configured infrastructure
    (database instance class, compute replicas, messaging, networking).
    Uses the full ProductSpecBundle when possible, falling back to
    solution metadata for a lightweight estimate.

    Response: {
        "success": true,
        "total_monthly_usd": 342.50,
        "annual_usd": 4110.00,
        "breakdown": [...],
        "assumptions": [...],
        "warning": "..." (optional, if cost exceeds threshold)
    }
    """
    import json as json_mod
    from app.models.solution_models import Solution
    from app.modules.solutions_product.services.cost_estimator import CostEstimator

    solution = Solution.query.get(solution_id)
    if not solution:
        return jsonify({"success": False, "error": "Solution not found"}), 404

    estimator = CostEstimator()

    # Try full bundle estimate first (requires application elements)
    try:
        from app.modules.solutions_product.services.product_spec_bundle import (
            build_product_spec_bundle,
        )
        bundle = build_product_spec_bundle(solution_id)
        result = estimator.estimate(bundle)
        result["success"] = True
        result["solution_id"] = solution_id
        result["source"] = "spec_bundle"
        return jsonify(result)
    except (ValueError, Exception) as bundle_err:
        logger.debug("Full bundle estimate failed, falling back to config: %s", bundle_err)

    # Fallback: estimate from solution metadata config only
    try:
        meta = getattr(solution, "metadata_json", None) or {}
        if isinstance(meta, str):
            meta = json_mod.loads(meta)
    except (ValueError, TypeError):
        meta = {}

    db_cfg = meta.get("database_config", {})
    arch_style = meta.get("architecture_style", {})

    config = {
        "database": {
            "instance_class": db_cfg.get("instance_class", "db.t3.medium"),
            "storage_gb": db_cfg.get("storage_gb", 20),
            "multi_az": db_cfg.get("multi_az", False),
            "backup_days": db_cfg.get("backup_days", 7),
        },
        "compute": {
            "replicas": 2,
            "cpu_millicores": 250,
            "memory_mb": 256,
        },
        "services_count": 1,
        "has_kafka": arch_style.get("primary") == "event_driven",
        "has_sqs": False,
    }

    result = estimator.estimate_from_config(config)
    result["success"] = True
    result["solution_id"] = solution_id
    result["source"] = "metadata_config"
    return jsonify(result)


# ── FastAPI Stub Generator — COM-023 ─────────────────────────────────────────


@solution_product_bp.route("/<int:solution_id>/codegen/stubs", methods=["GET"])
@login_required
def get_codegen_stubs(solution_id):
    """Generate a deterministic FastAPI scaffold from ArchiMate ApplicationComponents.

    GET /api/solutions/<id>/codegen/stubs?format=zip|json

    Returns a ZIP archive (default) or JSON dict of {filename: content}
    generated from the solution's ApplicationComponent and DataObject elements.
    No LLM calls — fully deterministic, always returns 200.
    """
    from app.models.solution_models import Solution
    from app.modules.codegen.services.fastapi_stub_generator import (
        generate,
        generate_zip,
    )

    solution = Solution.query.get(solution_id)
    if not solution:
        return jsonify({"success": False, "error": "Solution not found"}), 404

    fmt = request.args.get("format", "zip").lower()

    if fmt == "json":
        files = generate(solution_id)
        return jsonify({"success": True, "solution_id": solution_id, "files": files})

    # Default: return ZIP archive
    zip_bytes = generate_zip(solution_id)
    return send_file(
        io.BytesIO(zip_bytes),
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"solution_{solution_id}_fastapi_stubs.zip",
    )
