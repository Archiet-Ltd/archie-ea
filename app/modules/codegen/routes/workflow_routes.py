"""Workflow designer, migration export, and acceptance criteria routes.

Extracted from codegen_routes.py for maintainability.
All routes register on codegen_bp (imported from the main module).
"""
import io
import zipfile

from flask import jsonify, render_template, request, send_file
from flask_login import login_required

from app.extensions import db
from app.models.solution_models import Solution
from .codegen_routes import codegen_bp
from ._helpers import _check_access


# -- Workflow Designer routes --------------------------------------------------


def _workflow_designs():
    """Workflow designs visible to the caller's organisation.

    A design carries no organisation column; it belongs to a solution, which does.
    The inner join lets the automatic tenant filter on ``Solution`` apply to every
    design read here.
    """
    from app.modules.codegen.models import WorkflowDesign

    return WorkflowDesign.query.join(Solution, WorkflowDesign.solution_id == Solution.id)


def _workflow_design_or_404(design_id):
    from flask import abort

    from app.modules.codegen.models import WorkflowDesign

    design = _workflow_designs().filter(WorkflowDesign.id == design_id).first()
    if design is None:
        abort(404)
    return design

@codegen_bp.route("/codegen/workflow-designer")
@login_required
def workflow_designer_page():
    """Serve the visual workflow designer."""
    return render_template("codegen/workflow_designer.html")


@codegen_bp.route("/api/codegen/workflow-designs", methods=["GET"])
@login_required
def list_workflow_designs():
    """List workflow designs, optionally filtered by solution_id."""
    from app.modules.codegen.models import WorkflowDesign

    solution_id = request.args.get("solution_id", type=int)
    query = _workflow_designs()
    if solution_id:
        query = query.filter(WorkflowDesign.solution_id == solution_id)
    query = query.order_by(WorkflowDesign.updated_at.desc())

    designs = query.all()
    return jsonify({
        "designs": [
            {
                "id": d.id,
                "solution_id": d.solution_id,
                "name": d.name,
                "description": d.description,
                "template_id": d.template_id,
                "version": d.version,
                "is_active": d.is_active,
                "created_at": d.created_at.isoformat() if d.created_at else None,
                "updated_at": d.updated_at.isoformat() if d.updated_at else None,
            }
            for d in designs
        ]
    })


@codegen_bp.route("/api/codegen/workflow-designs", methods=["POST"])
@login_required
def create_workflow_design():
    """Create a new workflow design."""
    from app.modules.codegen.models import WorkflowDesign

    data = request.get_json() or {}
    if not data.get("name"):
        return jsonify({"error": "name is required"}), 400
    if not data.get("workflow_definition"):
        return jsonify({"error": "workflow_definition is required"}), 400
    if data.get("solution_id") is not None:
        # The design is attached to this solution: it must be one the caller can see.
        Solution.query.get_or_404(data.get("solution_id"))

    design = WorkflowDesign(
        solution_id=data.get("solution_id"),
        name=data["name"],
        description=data.get("description"),
        workflow_definition=data["workflow_definition"],
        template_id=data.get("template_id"),
    )
    db.session.add(design)
    db.session.commit()

    return jsonify({"id": design.id, "name": design.name}), 201


@codegen_bp.route("/api/codegen/workflow-designs/<int:design_id>", methods=["GET"])
@login_required
def get_workflow_design(design_id):
    """Get a single workflow design."""
    design = _workflow_design_or_404(design_id)
    return jsonify({
        "id": design.id,
        "solution_id": design.solution_id,
        "name": design.name,
        "description": design.description,
        "workflow_definition": design.workflow_definition,
        "compiled_n8n": design.compiled_n8n,
        "template_id": design.template_id,
        "version": design.version,
        "is_active": design.is_active,
    })


@codegen_bp.route("/api/codegen/workflow-designs/<int:design_id>", methods=["PUT"])
@login_required
def update_workflow_design(design_id):
    """Update a workflow design."""
    design = _workflow_design_or_404(design_id)
    data = request.get_json() or {}

    if "name" in data:
        design.name = data["name"]
    if "description" in data:
        design.description = data["description"]
    if "workflow_definition" in data:
        design.workflow_definition = data["workflow_definition"]
        design.compiled_n8n = None  # invalidate compiled output on change

    db.session.commit()
    return jsonify({"id": design.id, "name": design.name})


@codegen_bp.route("/api/codegen/workflow-designs/compile", methods=["POST"])
@login_required
def compile_workflow_design():
    """Compile a workflow definition to n8n JSON without saving."""
    from app.modules.codegen.services.workflow_n8n_compiler import WorkflowToN8nCompiler

    data = request.get_json() or {}
    compiler = WorkflowToN8nCompiler()
    try:
        n8n_workflow = compiler.compile(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 400

    return jsonify({"n8n_workflow": n8n_workflow})


@codegen_bp.route("/api/codegen/workflow-templates", methods=["GET"])
@login_required
def list_workflow_templates():
    """List available workflow templates."""
    from app.modules.codegen.services.workflow_template_library import WorkflowTemplateLibrary

    lib = WorkflowTemplateLibrary()
    return jsonify({"templates": lib.list_templates()})


@codegen_bp.route("/api/codegen/workflow-templates/<template_id>/instantiate", methods=["POST"])
@login_required
def instantiate_workflow_template(template_id):
    """Instantiate a workflow template with parameters."""
    from app.modules.codegen.services.workflow_template_library import WorkflowTemplateLibrary

    data = request.get_json() or {}
    lib = WorkflowTemplateLibrary()
    try:
        wf_def = lib.instantiate(template_id, data)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404

    return jsonify({"workflow_definition": wf_def})


# -- Migration Export routes ---------------------------------------------------

@codegen_bp.route("/api/codegen/migrations/platforms", methods=["GET"])
@login_required
def list_migration_platforms():
    """List available target platforms for migration export."""
    platforms = [
        {"id": "docker-compose", "name": "Docker Compose", "description": "Self-managed Docker deployment"},
        {"id": "kubernetes", "name": "Kubernetes", "description": "K8s manifests (any cluster)"},
        {"id": "aws", "name": "AWS", "description": "ECS + RDS + ALB via Terraform"},
        {"id": "azure", "name": "Azure", "description": "App Service + PostgreSQL + Front Door via Terraform"},
        {"id": "gcp", "name": "GCP", "description": "Cloud Run + Cloud SQL via Terraform"},
    ]
    return jsonify({"platforms": platforms})


@codegen_bp.route("/api/codegen/migrations/export", methods=["POST"])
@login_required
def export_migration_package():
    """Export a complete solution package for the given platform."""
    from app.modules.codegen.services.migration_packager import MigrationPackager

    data = request.get_json() or {}
    solution_id = data.get("solution_id")
    target_platform = data.get("target_platform", "docker-compose")

    if not solution_id:
        return jsonify({"error": "solution_id is required"}), 400

    packager = MigrationPackager()
    try:
        result = packager.export(solution_id, target_platform)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    return jsonify(result)


@codegen_bp.route("/api/codegen/migrations/export/zip", methods=["POST"])
@login_required
def export_migration_zip():
    """Export solution package as a downloadable ZIP file."""
    from app.modules.codegen.services.migration_packager import MigrationPackager

    data = request.get_json() or {}
    solution_id = data.get("solution_id")
    target_platform = data.get("target_platform", "docker-compose")

    if not solution_id:
        return jsonify({"error": "solution_id is required"}), 400

    packager = MigrationPackager()
    try:
        result = packager.export(solution_id, target_platform)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for filepath, content in result["files"].items():
            zf.writestr(filepath, content if isinstance(content, str) else str(content))

    buf.seek(0)
    return send_file(
        buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"solution-{solution_id}-{target_platform}.zip",
    )


# ─── Acceptance Criteria ─────────────────────────────────────────────────

@codegen_bp.route("/solutions/<int:solution_id>/codegen/acceptance-criteria", methods=["GET"])
@login_required
def get_acceptance_criteria(solution_id):
    """Generate acceptance criteria from motivation elements + business rules."""
    solution = Solution.query.get_or_404(solution_id)
    _check_access(solution)

    from app.modules.codegen.services.acceptance_criteria_generator import AcceptanceCriteriaGenerator
    gen = AcceptanceCriteriaGenerator()
    criteria = gen.generate(solution_id)
    return jsonify({"criteria": criteria, "count": len(criteria)})


# -- Genome Marketplace Template routes ----------------------------------------

import logging
import os
import yaml as _yaml

_genome_logger = logging.getLogger(__name__)
_TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "genome_templates")


def _load_genome_template(slug):
    """Load a genome template YAML by slug. Returns (genome_dict, error_string)."""
    path = os.path.join(_TEMPLATES_DIR, f"{slug}.yaml")
    if not os.path.isfile(path):
        return None, f"Template '{slug}' not found"
    with open(path) as f:
        return _yaml.safe_load(f), None


def _list_genome_templates():
    """Return metadata list for all genome templates."""
    from app.modules.codegen.services.genome_validator import validate_genome, compute_quality_score
    templates = []
    if not os.path.isdir(_TEMPLATES_DIR):
        return templates
    for fname in sorted(os.listdir(_TEMPLATES_DIR)):
        if not fname.endswith(".yaml"):
            continue
        slug = fname.replace(".yaml", "")
        path = os.path.join(_TEMPLATES_DIR, fname)
        try:
            with open(path) as f:
                genome = _yaml.safe_load(f)
            errors = validate_genome(genome)
            score = compute_quality_score(genome)
            modules = genome.get("modules", {})
            templates.append({
                "slug": slug,
                "name": genome.get("solution_name", slug),
                "description": genome.get("problem", {}).get("statement", "")[:200],
                "business_domain": genome.get("problem", {}).get("business_domain", ""),
                "module_count": len(modules),
                "entity_count": sum(len(m.get("entities", [])) for m in modules.values()),
                "has_state_machine": any(m.get("state_machine") for m in modules.values()),
                "quality_score": score.get("total", 0),
                "valid": len(errors) == 0,
                "roles": genome.get("identity_provider", {}).get("roles", []),
                "compliance": genome.get("compliance", {}).get("frameworks", []),
            })
        except Exception as e:
            _genome_logger.warning("Failed to load genome template %s: %s", fname, e)
    return templates


@codegen_bp.route("/api/codegen/genome-templates", methods=["GET"])
@login_required
def list_genome_templates():
    """List available genome marketplace templates."""
    return jsonify({"templates": _list_genome_templates()})


@codegen_bp.route("/api/codegen/genome-templates/<slug>", methods=["GET"])
@login_required
def get_genome_template(slug):
    """Return the full genome template for a given slug."""
    genome, err = _load_genome_template(slug)
    if err:
        return jsonify({"error": err}), 404
    from app.modules.codegen.services.genome_validator import validate_genome, compute_quality_score
    errors = validate_genome(genome)
    score = compute_quality_score(genome)
    return jsonify({
        "slug": slug,
        "genome": genome,
        "quality_score": score,
        "validation_errors": errors,
    })


@codegen_bp.route("/api/codegen/genome-templates/<slug>/apply", methods=["POST"])
@login_required
def apply_genome_template(slug):
    """Apply a genome template to a solution.

    Creates ArchiMate elements from genome modules, populates
    SolutionArchiMateElement junctions with spec_data containing
    field definitions, and stores the genome in CodegenGeneration.
    """
    from app.models.solution_models import Solution
    from app.models.archimate_core import ArchiMateElement
    from app.models.solution_archimate_element import SolutionArchiMateElement
    from app.modules.codegen.models import CodegenGeneration

    data = request.get_json(silent=True) or request.args
    solution_id = data.get("solution_id") or request.args.get("solution_id")
    if not solution_id:
        return jsonify({"error": "solution_id required"}), 400

    solution = Solution.query.get(int(solution_id))
    if not solution:
        return jsonify({"error": "Solution not found"}), 404
    _check_access(solution)

    genome, err = _load_genome_template(slug)
    if err:
        return jsonify({"error": err}), 404

    # Stamp the genome with the actual solution
    genome["solution_id"] = solution.id
    genome["solution_name"] = solution.name or genome.get("solution_name", slug)

    # Update solution description from genome problem statement if empty
    if not solution.description and genome.get("problem", {}).get("statement"):
        solution.description = genome["problem"]["statement"]

    # Create ArchiMate elements and junctions from genome modules.
    # For each module: create an ApplicationComponent (so the AABL compiler
    # discovers it as a module) + DataObjects for each entity + Composition
    # relationships linking them.
    from app.models.archimate_core import ArchiMateRelationship

    created_elements = 0
    created_junctions = 0
    created_relationships = 0
    modules = genome.get("modules", {})

    for mod_key, mod_def in modules.items():
        entities = mod_def.get("entities", [])
        fields_by_entity = mod_def.get("fields", {})
        aggregate_root = mod_def.get("aggregate_root", "")

        # Create ApplicationComponent for the module
        mod_name = aggregate_root + " Service" if aggregate_root else mod_key.replace("_", " ").title() + " Service"
        existing_comp = ArchiMateElement.query.filter_by(
            name=mod_name, type="ApplicationComponent"
        ).first()
        if not existing_comp:
            comp_elem = ArchiMateElement(
                name=mod_name,
                type="ApplicationComponent",
                layer="Application",
                description=mod_def.get("_rationale", f"Module '{mod_key}' from genome template '{slug}'"),
            )
            db.session.add(comp_elem)
            db.session.flush()
            created_elements += 1
        else:
            comp_elem = existing_comp

        # Link ApplicationComponent to solution
        if not SolutionArchiMateElement.query.filter_by(
            solution_id=solution.id, element_id=comp_elem.id
        ).first():
            db.session.add(SolutionArchiMateElement(
                solution_id=solution.id,
                element_id=comp_elem.id,
                layer_type="application",
                element_table="archimate_elements",
                element_name=comp_elem.name,
                element_role="primary",
            ))
            created_junctions += 1

        # Create DataObject elements for each entity in this module
        for entity_name in entities:
            existing = ArchiMateElement.query.filter_by(
                name=entity_name, type="DataObject"
            ).first()

            if not existing:
                elem = ArchiMateElement(
                    name=entity_name,
                    type="DataObject",
                    layer="Application",
                    description=f"Entity in module '{mod_key}' from genome template '{slug}'",
                )
                db.session.add(elem)
                db.session.flush()
                created_elements += 1
            else:
                elem = existing

            # Link DataObject to solution with spec_data
            if not SolutionArchiMateElement.query.filter_by(
                solution_id=solution.id, element_id=elem.id
            ).first():
                entity_fields = fields_by_entity.get(entity_name, [])
                db.session.add(SolutionArchiMateElement(
                    solution_id=solution.id,
                    element_id=elem.id,
                    layer_type="application",
                    element_table="archimate_elements",
                    element_name=entity_name,
                    element_role="primary",
                    spec_data={"fields": entity_fields} if entity_fields else None,
                ))
                created_junctions += 1

            # Create Composition relationship: ApplicationComponent → DataObject
            # so the AABL compiler assigns this entity to the correct module
            if not ArchiMateRelationship.query.filter_by(
                source_id=comp_elem.id, target_id=elem.id, type="Composition"
            ).first():
                db.session.add(ArchiMateRelationship(
                    source_id=comp_elem.id,
                    target_id=elem.id,
                    type="Composition",
                    description=f"Module '{mod_key}' owns entity '{entity_name}'",
                ))
                created_relationships += 1

    # Store genome in CodegenGeneration
    gen = CodegenGeneration.query.filter_by(solution_id=solution.id).first()
    if not gen:
        gen = CodegenGeneration(solution_id=solution.id, version=1)
        db.session.add(gen)
    gen.genome = genome

    db.session.commit()

    return jsonify({
        "success": True,
        "template": slug,
        "solution_id": solution.id,
        "created_elements": created_elements,
        "created_junctions": created_junctions,
        "created_relationships": created_relationships,
        "total_modules": len(modules),
    })
