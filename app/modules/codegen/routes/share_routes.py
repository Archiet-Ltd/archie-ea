"""Share-link routes for the Code Workbench.

Public (no-login) share page and zip download, plus the POST endpoint that
generates/regenerates the share token for a solution's generated code.

These routes are registered on ``codegen_bp`` (defined in codegen_routes.py).
This file is imported at the bottom of codegen_routes.py so that the routes
are attached to the blueprint automatically.
"""
import io
import zipfile

from flask import jsonify, render_template, request, send_file
from flask_login import login_required

from app.extensions import db
from app.modules.codegen.models import CodegenGeneration
from app.models.solution_models import Solution
from app.utils.csrf_helper import require_csrf

from .codegen_routes import codegen_bp
from ._helpers import _check_access


def _generate_share_token():
    """Generate a cryptographically secure 32-char URL-safe token."""
    import secrets
    return secrets.token_urlsafe(24)  # 32 url-safe chars


@codegen_bp.route("/solutions/<int:solution_id>/codegen/share", methods=["POST"])
@login_required
@require_csrf
def create_share_link(solution_id):
    """Generate (or regenerate) a public share token for the generated code.

    Stores the token in gen.config["share_token"] — no schema change needed.
    Returns the full shareable URL.
    """
    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403

    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
    if not gen or not gen.generated_files:
        return jsonify({"error": "Generate code first before creating a share link"}), 400

    payload = request.get_json(silent=True) or {}
    # Allow regenerating the token if explicitly requested
    existing_config = gen.config or {}
    if payload.get("regenerate") or not existing_config.get("share_token"):
        token = _generate_share_token()
        gen.config = {**existing_config, "share_token": token}
        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            import logging
            logging.getLogger(__name__).error(
                "Failed to save share token for solution %s: %s", solution_id, e
            )
            return jsonify({"error": "Failed to save share link"}), 500
    else:
        token = existing_config["share_token"]

    share_url = f"{request.host_url}share/code/{token}"
    return jsonify({"success": True, "token": token, "share_url": share_url})


@codegen_bp.route("/share/code/<token>")
def public_share(token):
    """Public read-only share page — no login required.

    The token is the credential and names the solution, so the page is rendered
    in the platform scope: no one is signed in, and ``solutions`` is fenced by
    row-level security.
    """
    from app.jobs.tenant_safe_job import platform_scope

    with platform_scope("public share link: the token names the solution; no signed-in user"):
        return _render_public_share(token)


def _render_public_share(token):
    """Render the share page for ``token`` (see ``public_share``).

    Resolves the token from gen.config["share_token"] and renders a lightweight
    read-only view: solution name, architecture summary, file tree, README,
    and a download button.
    """
    if not token or len(token) > 64:
        from flask import abort
        abort(404)

    # Find the generation record by iterating configs that contain this token
    # Using JSON contains query (works on PostgreSQL)
    try:
        gen = CodegenGeneration.query.filter(
            CodegenGeneration.config["share_token"].astext == token
        ).first()
    except Exception:
        # Fallback: scan (slow, only for SQLite)
        gen = None
        for g in CodegenGeneration.query.filter(CodegenGeneration.config.isnot(None)).all():
            if (g.config or {}).get("share_token") == token:
                gen = g
                break

    if not gen or not gen.generated_files:
        from flask import abort
        abort(404)

    solution = Solution.query.get(gen.solution_id)
    if not solution:
        from flask import abort
        abort(404)

    # Extract README content for display
    readme = gen.generated_files.get("README.md", "")

    # Build file tree (paths only, no content — content loaded on demand)
    file_list = sorted(gen.generated_files.keys())

    # Architecture summary from UML snapshot
    arch_summary = ""
    if gen.uml_snapshot:
        classes = gen.uml_snapshot.get("class_diagram", {}).get("classes", [])
        arch_summary = f"{len(classes)} entities"
        comp = gen.uml_snapshot.get("component_diagram", {}).get("components", [])
        if comp:
            arch_summary += f", {len(comp)} components"

    config = gen.config or {}
    language = config.get("language")
    if not language:
        # Fall back to the most recent generation history entry
        from app.modules.codegen.models import CodegenGenerationHistory
        latest = (CodegenGenerationHistory.query
                  .filter_by(codegen_generation_id=gen.id)
                  .order_by(CodegenGenerationHistory.id.desc())
                  .first())
        if latest:
            language = latest.language
    if not language:
        language = "unknown"

    return render_template(
        "codegen/share.html",
        solution=solution,
        gen=gen,
        file_list=file_list,
        readme=readme,
        arch_summary=arch_summary,
        language=language,
        token=token,
        file_count=len(file_list),
    )


@codegen_bp.route("/share/code/<token>/download")
def public_share_download(token):
    """Public zip download — no login required. Token acts as the auth credential."""
    if not token or len(token) > 64:
        from flask import abort
        abort(404)

    try:
        gen = CodegenGeneration.query.filter(
            CodegenGeneration.config["share_token"].astext == token
        ).first()
    except Exception:
        gen = None
        for g in CodegenGeneration.query.filter(CodegenGeneration.config.isnot(None)).all():
            if (g.config or {}).get("share_token") == token:
                gen = g
                break

    if not gen or not gen.generated_files:
        from flask import abort
        abort(404)

    Solution.query.get(gen.solution_id)
    config = gen.config or {}
    repo_name = config.get("repo_name", f"solution-{gen.solution_id}")
    import re as _re
    repo_name = _re.sub(r"[^a-zA-Z0-9_\-]", "_", (repo_name or "project").strip("/").strip())[:64] or "project"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for filepath, content in gen.generated_files.items():
            if ".." in filepath or filepath.startswith("/"):
                continue
            zf.writestr(f"{repo_name}/{filepath}", content)
    buf.seek(0)

    gen.download_count = (gen.download_count or 0) + 1
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()

    return send_file(
        buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{repo_name}.zip",
    )
