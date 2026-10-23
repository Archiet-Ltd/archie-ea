"""File CRUD routes for the Code Workbench.

Extracted from codegen_routes.py — handles file content, listing,
save, delete, create, rename, duplicate, and search operations on
generated code bundles.
"""
import hashlib
import logging
import re

from flask import jsonify, request
from flask_login import login_required

from app.extensions import db
from app.models.solution_models import Solution
from app.modules.codegen.models import CodegenGeneration
from .codegen_routes import codegen_bp
from ._helpers import _check_access
from app.utils.csrf_helper import require_csrf

logger = logging.getLogger(__name__)


@codegen_bp.route("/solutions/<int:solution_id>/codegen/file-content")
@login_required
def get_file_content(solution_id):
    """Get content of a single generated file (for preview on page refresh)."""
    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403

    path = request.args.get("path", "")
    if not path:
        return jsonify({"error": "path parameter required"}), 400

    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
    if not gen or not gen.generated_files:
        return jsonify({"error": "No generated files"}), 404

    content = gen.generated_files.get(path)
    if content is None:
        return jsonify({"error": f"File not found: {path}"}), 404

    return jsonify({"success": True, "path": path, "content": content})


@codegen_bp.route("/solutions/<int:solution_id>/codegen/file-list")
@login_required
def get_file_list(solution_id):
    """Get generated file paths only (no content -- lightweight for page load)."""
    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403

    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
    if not gen or not gen.generated_files:
        # Nothing generated yet is an answer, not an error: every wizard load asks.
        return jsonify({"success": True, "files": []})

    return jsonify({"success": True, "files": sorted(gen.generated_files.keys())})


@codegen_bp.route("/solutions/<int:solution_id>/codegen/files-content")
@login_required
def get_all_file_contents(solution_id):
    """Get all generated file contents in one response (bulk preload).

    WARNING: This can return 5-15MB for large projects. Prefer /file-list + /file-content?path=
    for normal page loads. This endpoint is kept for ZIP download and export flows only.
    """
    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403

    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
    if not gen or not gen.generated_files:
        return jsonify({"error": "No generated files"}), 404

    return jsonify({"success": True, "files": gen.generated_files})


@codegen_bp.route("/solutions/<int:solution_id>/codegen/files", methods=["PATCH"])
@login_required
@require_csrf
def save_generated_file(solution_id):
    """Save a manually-edited generated file."""
    from sqlalchemy.orm.attributes import flag_modified

    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403

    payload = request.get_json(silent=True) or {}
    path = payload.get("path", "").strip()
    if not path or '..' in path or path.startswith('/') or not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_\-/\.]*$', path):
        return jsonify({"error": "Invalid file path"}), 400
    content = payload.get("content")
    if content is None:
        return jsonify({"error": "path and content are required"}), 400

    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
    if not gen or not gen.generated_files:
        return jsonify({"error": "No generated files"}), 404

    if path not in gen.generated_files:
        return jsonify({"error": f"File not found: {path}"}), 404

    files = dict(gen.generated_files)
    files[path] = content
    gen.generated_files = files

    config = dict(gen.config or {})
    manual_edits = dict(config.get("manual_edits", {}))
    manual_edits[path] = hashlib.sha256(content.encode()).hexdigest()[:12]
    config["manual_edits"] = manual_edits
    gen.config = config

    gen.version += 1
    flag_modified(gen, "generated_files")
    flag_modified(gen, "config")
    db.session.commit()

    logger.info("Manual file save: solution=%s path=%s version=%s", solution_id, path, gen.version)
    return jsonify({"success": True, "version": gen.version, "path": path})


@codegen_bp.route("/solutions/<int:solution_id>/codegen/files", methods=["DELETE"])
@login_required
@require_csrf
def delete_generated_file(solution_id):
    """Delete a file from the generated bundle."""
    from sqlalchemy.orm.attributes import flag_modified

    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403

    payload = request.get_json(silent=True) or {}
    path = payload.get("path", "").strip()
    if not path:
        return jsonify({"error": "path is required"}), 400

    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
    if not gen or not gen.generated_files:
        return jsonify({"error": "No generated files"}), 404

    if path not in gen.generated_files:
        return jsonify({"error": f"File not found: {path}"}), 404

    files = dict(gen.generated_files)
    del files[path]
    gen.generated_files = files
    gen.version += 1
    flag_modified(gen, "generated_files")
    db.session.commit()

    logger.info("File deleted: solution=%s path=%s version=%s", solution_id, path, gen.version)
    return jsonify({"success": True, "version": gen.version, "path": path, "remaining_files": len(files)})


@codegen_bp.route("/solutions/<int:solution_id>/codegen/files/create", methods=["POST"])
@login_required
@require_csrf
def create_generated_file(solution_id):
    """Create a new file in the generated bundle."""
    from sqlalchemy.orm.attributes import flag_modified

    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403

    payload = request.get_json(silent=True) or {}
    path = payload.get("path", "").strip()
    content = payload.get("content", "")

    if not path or '..' in path or path.startswith('/'):
        return jsonify({"error": "Invalid file path"}), 400
    if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_\-/\.]*$', path):
        return jsonify({"error": "Path contains invalid characters"}), 400

    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
    if not gen:
        gen = CodegenGeneration(solution_id=solution_id, version=0, generated_files={})
        db.session.add(gen)

    files = dict(gen.generated_files or {})
    if path in files:
        return jsonify({"error": f"File already exists: {path}. Use PATCH to edit."}), 409

    files[path] = content
    gen.generated_files = files
    gen.version = (gen.version or 0) + 1
    flag_modified(gen, "generated_files")
    db.session.commit()

    logger.info("File created: solution=%s path=%s version=%s", solution_id, path, gen.version)
    return jsonify({"success": True, "version": gen.version, "path": path}), 201


@codegen_bp.route("/solutions/<int:solution_id>/codegen/files/rename", methods=["POST"])
@login_required
@require_csrf
def rename_generated_file(solution_id):
    """Rename or move a file within the generated bundle."""
    from sqlalchemy.orm.attributes import flag_modified

    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403

    payload = request.get_json(silent=True) or {}
    old_path = payload.get("old_path", "").strip()
    new_path = payload.get("new_path", "").strip()

    if not old_path or not new_path:
        return jsonify({"error": "old_path and new_path are required"}), 400
    for p in (old_path, new_path):
        if '..' in p or p.startswith('/') or not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_\-/\.]*$', p):
            return jsonify({"error": f"Invalid path: {p}"}), 400

    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
    if not gen or not gen.generated_files:
        return jsonify({"error": "No generated files"}), 404

    files = dict(gen.generated_files)
    if old_path not in files:
        return jsonify({"error": f"Source file not found: {old_path}"}), 404
    if new_path in files:
        return jsonify({"error": f"Destination already exists: {new_path}"}), 409

    files[new_path] = files.pop(old_path)
    gen.generated_files = files

    config = dict(gen.config or {})
    manual_edits = dict(config.get("manual_edits", {}))
    if old_path in manual_edits:
        manual_edits[new_path] = manual_edits.pop(old_path)
        config["manual_edits"] = manual_edits
        gen.config = config
        flag_modified(gen, "config")

    gen.version += 1
    flag_modified(gen, "generated_files")
    db.session.commit()

    logger.info("File renamed: solution=%s %s -> %s version=%s", solution_id, old_path, new_path, gen.version)
    return jsonify({"success": True, "version": gen.version, "old_path": old_path, "new_path": new_path})


@codegen_bp.route("/solutions/<int:solution_id>/codegen/files/duplicate", methods=["POST"])
@login_required
@require_csrf
def duplicate_generated_file(solution_id):
    """Duplicate a file in the generated bundle."""
    from sqlalchemy.orm.attributes import flag_modified

    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403

    payload = request.get_json(silent=True) or {}
    source = payload.get("source_path", "").strip()
    dest = payload.get("dest_path", "").strip()

    if not source or not dest:
        return jsonify({"error": "source_path and dest_path are required"}), 400
    if '..' in dest or dest.startswith('/') or not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_\-/\.]*$', dest):
        return jsonify({"error": f"Invalid destination path: {dest}"}), 400

    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
    if not gen or not gen.generated_files:
        return jsonify({"error": "No generated files"}), 404

    files = dict(gen.generated_files)
    if source not in files:
        return jsonify({"error": f"Source file not found: {source}"}), 404
    if dest in files:
        return jsonify({"error": f"Destination already exists: {dest}"}), 409

    files[dest] = files[source]
    gen.generated_files = files
    gen.version += 1
    flag_modified(gen, "generated_files")
    db.session.commit()

    return jsonify({"success": True, "version": gen.version, "path": dest})


@codegen_bp.route("/solutions/<int:solution_id>/codegen/files/search", methods=["POST"])
@login_required
def search_generated_files(solution_id):
    """Search across all generated files for a string or regex."""
    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403

    payload = request.get_json(silent=True) or {}
    query = payload.get("query", "").strip()
    use_regex = payload.get("regex", False)

    if not query:
        return jsonify({"error": "query is required"}), 400
    if len(query) > 500:
        return jsonify({"error": "Query too long (max 500 chars)"}), 400

    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
    if not gen or not gen.generated_files:
        return jsonify({"error": "No generated files"}), 404

    if use_regex:
        try:
            pattern = re.compile(query, re.IGNORECASE)
        except re.error as e:
            return jsonify({"error": f"Invalid regex: {e}"}), 400
    else:
        pattern = re.compile(re.escape(query), re.IGNORECASE)

    results = []
    for path, content in gen.generated_files.items():
        if not isinstance(content, str):
            continue
        matches = []
        for line_num, line in enumerate(content.splitlines(), 1):
            if pattern.search(line):
                matches.append({"line": line_num, "text": line.strip()[:200]})
                if len(matches) >= 10:
                    break
        if matches:
            results.append({"path": path, "matches": matches, "match_count": len(matches)})

    results.sort(key=lambda r: r["match_count"], reverse=True)
    return jsonify({
        "success": True,
        "query": query,
        "total_files": len(results),
        "results": results[:50],
    })
