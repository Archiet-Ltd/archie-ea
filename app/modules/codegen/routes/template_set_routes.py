"""Template Marketplace routes — CRUD for CodegenTemplateSet and its file overrides.

Extracted from codegen_routes.py (Feature 6: Template Marketplace).
Routes register on codegen_bp which is defined in codegen_routes.py.
"""
from flask import jsonify, request
from flask_login import current_user, login_required
from app.utils.csrf_helper import require_csrf
from app.extensions import db
from app.modules.codegen.models import (
    CodegenTemplateSet, CodegenTemplateFile, CodegenGeneration,
)
from app.models.solution_models import Solution
from app.modules.codegen.routes.codegen_routes import codegen_bp
from app.modules.codegen.routes._helpers import _check_access, SUPPORTED_LANGUAGES


# ── Feature 6: Template Marketplace ──────────────────────────────────────────

@codegen_bp.route("/api/codegen/template-sets", methods=["GET"])
@login_required
def list_template_sets():
    """List all template sets owned by the current user."""
    sets = CodegenTemplateSet.query.filter_by(created_by_id=current_user.id).order_by(
        CodegenTemplateSet.created_at.desc()
    ).all()
    return jsonify([
        {
            "id": ts.id,
            "name": ts.name,
            "language": ts.language,
            "description": ts.description,
            "created_at": ts.created_at.isoformat() if ts.created_at else None,
            "file_count": ts.files.count(),
        }
        for ts in sets
    ])


@codegen_bp.route("/api/codegen/template-sets", methods=["POST"])
@login_required
def create_template_set():
    """Create a new template set."""
    payload = request.get_json(silent=True) or {}
    name = (payload.get("name") or "").strip()
    language = (payload.get("language") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    if language not in SUPPORTED_LANGUAGES:
        return jsonify({"error": f"language must be one of: {', '.join(sorted(SUPPORTED_LANGUAGES))}"}), 400

    ts = CodegenTemplateSet(
        name=name,
        language=language,
        description=(payload.get("description") or "").strip() or None,
        created_by_id=current_user.id,
    )
    db.session.add(ts)
    db.session.commit()
    return jsonify({"id": ts.id, "name": ts.name, "language": ts.language}), 201


@codegen_bp.route("/api/codegen/template-sets/<int:set_id>", methods=["GET"])
@login_required
def get_template_set(set_id):
    """Get a template set with its file list."""
    ts = CodegenTemplateSet.query.get_or_404(set_id)
    if ts.created_by_id != current_user.id:
        return jsonify({"error": "Access denied"}), 403
    files = [
        {"template_name": tf.template_name, "version": tf.version,
         "updated_at": tf.updated_at.isoformat() if tf.updated_at else None}
        for tf in ts.files.order_by(CodegenTemplateFile.template_name)
    ]
    return jsonify({
        "id": ts.id,
        "name": ts.name,
        "language": ts.language,
        "description": ts.description,
        "created_at": ts.created_at.isoformat() if ts.created_at else None,
        "files": files,
    })


@codegen_bp.route("/api/codegen/template-sets/<int:set_id>", methods=["DELETE"])
@login_required
def delete_template_set(set_id):
    """Delete a template set and all its files."""
    ts = CodegenTemplateSet.query.get_or_404(set_id)
    if ts.created_by_id != current_user.id:
        return jsonify({"error": "Access denied"}), 403
    db.session.delete(ts)
    db.session.commit()
    return jsonify({"success": True})


@codegen_bp.route("/api/codegen/template-sets/<int:set_id>/files/<path:template_name>", methods=["GET"])
@login_required
def get_template_file(set_id, template_name):
    """Get the content of a single template file override."""
    ts = CodegenTemplateSet.query.get_or_404(set_id)
    if ts.created_by_id != current_user.id:
        return jsonify({"error": "Access denied"}), 403
    tf = CodegenTemplateFile.query.filter_by(set_id=set_id, template_name=template_name).first()
    if not tf:
        return jsonify({"error": "Template file not found"}), 404
    return jsonify({
        "template_name": tf.template_name,
        "content": tf.content,
        "version": tf.version,
        "updated_at": tf.updated_at.isoformat() if tf.updated_at else None,
    })


@codegen_bp.route("/api/codegen/template-sets/<int:set_id>/files/<path:template_name>", methods=["PUT"])
@login_required
def upsert_template_file(set_id, template_name):
    """Create or update a template file override."""
    ts = CodegenTemplateSet.query.get_or_404(set_id)
    if ts.created_by_id != current_user.id:
        return jsonify({"error": "Access denied"}), 403

    payload = request.get_json(silent=True) or {}
    content = payload.get("content")
    if content is None:
        return jsonify({"error": "content is required"}), 400

    tf = CodegenTemplateFile.query.filter_by(set_id=set_id, template_name=template_name).first()
    if tf:
        tf.content = content
        tf.version += 1
    else:
        tf = CodegenTemplateFile(set_id=set_id, template_name=template_name, content=content)
        db.session.add(tf)
    db.session.commit()
    return jsonify({
        "template_name": tf.template_name,
        "version": tf.version,
        "updated_at": tf.updated_at.isoformat() if tf.updated_at else None,
    })


@codegen_bp.route("/solutions/<int:solution_id>/codegen/save-as-template", methods=["POST"])
@login_required
@require_csrf
def save_generation_as_template(solution_id):
    """Snapshot the current generated files into a new CodegenTemplateSet."""
    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution, write=True):
        return jsonify({"error": "Access denied"}), 403

    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
    if not gen or not gen.generated_files:
        return jsonify({"error": "No generated files found — generate code first."}), 400

    payload = request.get_json(silent=True) or {}
    name = (payload.get("name") or "").strip()
    language = (payload.get("language") or "python-fastapi").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400

    ts = CodegenTemplateSet(
        name=name,
        language=language,
        description=(payload.get("description") or "").strip() or None,
        created_by_id=current_user.id,
    )
    db.session.add(ts)
    db.session.flush()  # get ts.id before creating files

    files = gen.generated_files if isinstance(gen.generated_files, dict) else {}
    for path, content in files.items():
        if not isinstance(content, str):
            continue
        tf = CodegenTemplateFile(set_id=ts.id, template_name=path, content=content)
        db.session.add(tf)

    db.session.commit()
    return jsonify({
        "id": ts.id,
        "name": ts.name,
        "language": ts.language,
        "description": ts.description,
        "file_count": len(files),
        "created_at": ts.created_at.isoformat() if ts.created_at else None,
    }), 201


@codegen_bp.route("/api/codegen/template-sets/<int:set_id>/files/<path:template_name>", methods=["DELETE"])
@login_required
def delete_template_file(set_id, template_name):
    """Delete a template override, reverting to the built-in default."""
    ts = CodegenTemplateSet.query.get_or_404(set_id)
    if ts.created_by_id != current_user.id:
        return jsonify({"error": "Access denied"}), 403
    tf = CodegenTemplateFile.query.filter_by(set_id=set_id, template_name=template_name).first()
    if not tf:
        return jsonify({"error": "Template file not found"}), 404
    db.session.delete(tf)
    db.session.commit()
    return jsonify({"success": True})
