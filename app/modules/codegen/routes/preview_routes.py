"""Preview routes for the Code Workbench.

Covers:
- change-preview  (what files would change on regen)
- mock-server     (download standalone mock server)
- Live API preview (OpenAPI / Swagger UI)
- stackblitz-data / expo-snack
- docker-preview  (build + run generated code in Docker)

Registered on ``codegen_bp`` (defined in codegen_routes.py).
Imported at the bottom of codegen_routes.py so routes attach automatically.
"""
import hashlib
import logging
import re
from html import escape
import secrets

from flask import abort, jsonify, request
from flask_login import login_required

from app.extensions import db
from app.modules.codegen.models import CodegenGeneration, CodegenGenerationHistory
from app.models.solution_models import Solution
from app.utils.csrf_helper import require_csrf

from .codegen_routes import codegen_bp
from ._helpers import _check_access

logger = logging.getLogger(__name__)


# ── Change Preview ─────────────────────────────────────────────────────────────

@codegen_bp.route("/solutions/<int:solution_id>/codegen/change-preview")
@login_required
def change_preview(solution_id):
    """Preview which files would change if regenerated now.

    Compares current UML class hashes against last generation's manifest.
    Returns lists of added/removed/changed file paths without actually regenerating.
    """
    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        abort(403)

    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
    if not gen or not gen.uml_snapshot or not gen.generated_files:
        return jsonify({"error": "Need both UML and generated files"}), 400

    uml = gen.uml_snapshot
    classes = uml.get("class_diagram", {}).get("classes", [])
    flows = uml.get("sequence_diagram", {}).get("flows", [])

    # Hash each class by its fields to detect schema changes
    import json
    class_hashes = {}
    for cls in classes:
        key = cls.get("name", "")
        field_sig = json.dumps(cls.get("fields", []), sort_keys=True)
        class_hashes[key] = hashlib.sha256(field_sig.encode()).hexdigest()[:12]

    # Get last generation manifest
    last_history = CodegenGenerationHistory.query.filter_by(
        codegen_generation_id=gen.id
    ).order_by(CodegenGenerationHistory.generated_at.desc()).first()

    if not last_history or not last_history.file_manifest:
        return jsonify({
            "success": True,
            "status": "no_previous",
            "message": "No previous generation to compare against — full regeneration needed",
            "total_classes": len(classes),
            "total_flows": len(flows),
        })

    # Compare class names in UML vs route files in manifest
    import re as _re
    old_route_files = [
        f["path"] for f in last_history.file_manifest
        if f["path"].startswith("app/routes/") and not f["path"].endswith("__init__.py")
    ]

    def _snake_name(name):
        s = _re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", (name or "").strip())
        s = _re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s)
        s = _re.sub(r"[^a-zA-Z0-9]", "_", s)
        return _re.sub(r"_+", "_", s).strip("_").lower()

    current_route_files = {f"app/routes/{_snake_name(cls['name'])}s.py" for cls in classes}
    old_route_set = set(old_route_files)

    added = sorted(current_route_files - old_route_set)
    removed = sorted(old_route_set - current_route_files)
    # Changed = same file exists but class fields changed
    changed = []
    for cls in classes:
        route_path = f"app/routes/{_snake_name(cls['name'])}s.py"
        if route_path in old_route_set:
            # Check if the file content would differ
            old_hash = next(
                (f["hash"] for f in last_history.file_manifest if f["path"] == route_path),
                None
            )
            if old_hash and class_hashes.get(cls["name"]) != old_hash:
                changed.append(route_path)

    # Shared files that always regenerate
    shared_files = ["app/main.py", "app/models/entities.py", "app/schemas/models.py",
                    "README.md", "GENERATED.md"]

    return jsonify({
        "success": True,
        "status": "preview",
        "added": added,
        "removed": removed,
        "changed": changed,
        "shared_always": shared_files,
        "total_affected": len(added) + len(removed) + len(changed) + len(shared_files),
        "total_files": len(last_history.file_manifest),
    })


# ── Mock Server Download ───────────────────────────────────────────────────────

@codegen_bp.route("/solutions/<int:solution_id>/codegen/mock-server")
@login_required
def download_mock_server(solution_id):
    """Download standalone mock server file (GAP-07)."""
    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403

    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
    if not gen or not gen.generated_files:
        return jsonify({"error": "Generate code first"}), 400

    mock = gen.generated_files.get("mock_server.py")
    if not mock:
        return jsonify({"error": "Mock server not available for this language"}), 404

    return mock, 200, {
        "Content-Type": "text/plain; charset=utf-8",
        "Content-Disposition": "attachment; filename=mock_server.py",
    }


# ── Live Preview (Feature 3) ──────────────────────────────────────────────────

_PREVIEW_TTL_SECONDS = 600  # 10 minutes


def _build_preview_openapi(solution_name, uml):
    """Build OpenAPI 3.1 spec dict from UML snapshot (reuses export_openapi logic)."""
    type_map = {
        "int": "integer", "str": "string", "float": "number",
        "Decimal": "number", "datetime": "string", "bool": "boolean", "UUID": "string",
    }
    format_map = {"Decimal": "decimal", "datetime": "date-time", "UUID": "uuid"}

    classes = (uml or {}).get("class_diagram", {}).get("classes", [])
    schemas = {}
    for cls in classes:
        props = {}
        required = []
        for f in cls.get("fields", []):
            prop = {"type": type_map.get(f.get("type", "str"), "string")}
            fmt = format_map.get(f.get("type"))
            if fmt:
                prop["format"] = fmt
            if f.get("description"):
                prop["description"] = f["description"]
            props[f["name"]] = prop
            if not f.get("nullable", True) and not f.get("primary_key"):
                required.append(f["name"])
        schema = {"type": "object", "properties": props}
        if required:
            schema["required"] = required
        schemas[cls["name"]] = schema

    paths = {}
    for flow in (uml or {}).get("sequence_diagram", {}).get("flows", []):
        p = flow.get("path", "")
        m = flow.get("http_method", "GET").lower()
        if p and m:
            paths.setdefault(p, {})[m] = {
                "summary": flow.get("name", ""),
                "responses": {"200": {"description": "Success"}},
            }

    for cls in classes:
        slug = cls.get("table_name") or cls["name"].lower() + "s"
        ref = {"$ref": f"#/components/schemas/{cls['name']}"}
        list_path = f"/api/{slug}"
        detail_path = f"/api/{slug}/{{id}}"
        if list_path not in paths:
            paths[list_path] = {
                "get": {
                    "summary": f"List {cls['name']}", "tags": [cls["name"]],
                    "responses": {"200": {"description": "Success", "content": {"application/json": {"schema": {"type": "array", "items": ref}}}}},
                },
                "post": {
                    "summary": f"Create {cls['name']}", "tags": [cls["name"]],
                    "requestBody": {"required": True, "content": {"application/json": {"schema": ref}}},
                    "responses": {"201": {"description": "Created", "content": {"application/json": {"schema": ref}}}},
                },
            }
        if detail_path not in paths:
            paths[detail_path] = {
                "get": {
                    "summary": f"Get {cls['name']}", "tags": [cls["name"]],
                    "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "Success", "content": {"application/json": {"schema": ref}}}, "404": {"description": "Not found"}},
                },
                "put": {
                    "summary": f"Update {cls['name']}", "tags": [cls["name"]],
                    "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "requestBody": {"required": True, "content": {"application/json": {"schema": ref}}},
                    "responses": {"200": {"description": "Updated", "content": {"application/json": {"schema": ref}}}},
                },
                "delete": {
                    "summary": f"Delete {cls['name']}", "tags": [cls["name"]],
                    "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"204": {"description": "Deleted"}},
                },
            }

    return {
        "openapi": "3.1.0",
        "info": {
            "title": f"{solution_name} API",
            "version": "1.0.0",
            "description": "Live API preview generated by Entelim Code Workbench",
        },
        "paths": paths,
        "components": {"schemas": schemas},
    }


@codegen_bp.route("/solutions/<int:solution_id>/codegen/preview/start", methods=["POST"])
@login_required
def preview_start(solution_id):
    """Start live API preview — derive OpenAPI spec from UML and store it."""
    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403

    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
    if not gen or not gen.uml_snapshot:
        return jsonify({"error": "Run enrichment first to generate UML"}), 400

    spec = _build_preview_openapi(solution.name or f"Solution {solution_id}", gen.uml_snapshot)
    endpoint_count = sum(len(methods) for methods in spec.get("paths", {}).values())

    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()

    config = dict(gen.config or {})
    config["_preview"] = {
        "active": True,
        "started_at": now_iso,
        "spec": spec,
        "endpoint_count": endpoint_count,
        "schema_count": len(spec.get("components", {}).get("schemas", {})),
    }
    gen.config = config
    db.session.commit()

    return jsonify({
        "success": True,
        "spec_url": f"/solutions/{solution_id}/codegen/preview/spec",
        "ui_url": f"/solutions/{solution_id}/codegen/preview/ui",
        "endpoint_count": endpoint_count,
        "schema_count": len(spec.get("components", {}).get("schemas", {})),
        "started_at": now_iso,
        "ttl_seconds": _PREVIEW_TTL_SECONDS,
    })


@codegen_bp.route("/solutions/<int:solution_id>/codegen/preview/spec")
@login_required
def preview_spec(solution_id):
    """Serve OpenAPI spec JSON for Swagger UI."""
    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403

    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
    if not gen:
        return jsonify({"error": "No workbench session found"}), 404

    preview = (gen.config or {}).get("_preview", {})
    spec = preview.get("spec")
    if not spec:
        # Build spec on-the-fly if UML exists (allows direct URL access)
        if gen.uml_snapshot:
            spec = _build_preview_openapi(solution.name or f"Solution {solution_id}", gen.uml_snapshot)
        else:
            return jsonify({"error": "No spec available — start preview first"}), 404

    import json
    return json.dumps(spec), 200, {"Content-Type": "application/json; charset=utf-8"}


@codegen_bp.route("/solutions/<int:solution_id>/codegen/preview/ui")
@login_required
def preview_ui(solution_id):
    """Serve Swagger UI HTML page pointing at the preview spec."""
    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return "Access denied", 403

    # Allow this page to be embedded in a same-origin iframe (the workbench)
    from flask import g as _g
    _g.allow_framing = True

    spec_url = f"/solutions/{solution_id}/codegen/preview/spec"
    # solution.name is architect-authored freeform text -- escape() before
    # it reaches the hand-built HTML page below.
    title = escape(f"{(solution.name or f'Solution {solution_id}')} — API Preview")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>
{title}</title>
  <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css">
  <style>
    body {{ margin: 0; background: #fafafa; }}
    #swagger-ui .topbar {{ display: none; }}
    #swagger-ui .swagger-ui .info .title {{ font-size: 1.4rem; }}
  </style>
</head>
<body>
  <div id="swagger-ui"></div>
  <script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
  <script>
    SwaggerUIBundle({{
      url: "{spec_url}",
      dom_id: "#swagger-ui",
      presets: [SwaggerUIBundle.presets.apis, SwaggerUIBundle.SwaggerUIStandalonePreset],
      layout: "BaseLayout",
      deepLinking: true,
      defaultModelsExpandDepth: 1,
      defaultModelExpandDepth: 2,
    }});
  </script>
</body>
</html>"""
    return html, 200, {"Content-Type": "text/html; charset=utf-8"}


@codegen_bp.route("/solutions/<int:solution_id>/codegen/preview/status")
@login_required
def preview_status(solution_id):
    """Get live preview status and auto-expire after TTL."""
    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403

    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
    if not gen:
        return jsonify({"active": False})

    preview = (gen.config or {}).get("_preview", {})
    if not preview.get("active"):
        return jsonify({"active": False, "has_uml": gen.uml_snapshot is not None})

    from datetime import datetime, timezone
    started_at_str = preview.get("started_at", "")
    minutes_remaining = _PREVIEW_TTL_SECONDS // 60

    if started_at_str:
        try:
            started = datetime.fromisoformat(started_at_str)
            elapsed = (datetime.now(timezone.utc) - started).total_seconds()
            if elapsed >= _PREVIEW_TTL_SECONDS:
                # Auto-expire
                config = dict(gen.config or {})
                config["_preview"]["active"] = False
                gen.config = config
                db.session.commit()
                return jsonify({"active": False, "expired": True, "has_uml": True})
            minutes_remaining = max(0, int((_PREVIEW_TTL_SECONDS - elapsed) / 60))
        except Exception as _ttl_exc:
            logger.debug("Preview TTL check failed (non-critical): %s", _ttl_exc)

    return jsonify({
        "active": True,
        "started_at": started_at_str,
        "minutes_remaining": minutes_remaining,
        "endpoint_count": preview.get("endpoint_count", 0),
        "schema_count": preview.get("schema_count", 0),
        "spec_url": f"/solutions/{solution_id}/codegen/preview/spec",
        "ui_url": f"/solutions/{solution_id}/codegen/preview/ui",
        "has_uml": True,
    })


@codegen_bp.route("/solutions/<int:solution_id>/codegen/preview/stop", methods=["DELETE"])
@login_required
def preview_stop(solution_id):
    """Stop live API preview."""
    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403

    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
    if gen and gen.config and "_preview" in gen.config:
        config = dict(gen.config)
        config["_preview"]["active"] = False
        gen.config = config
        db.session.commit()

    return jsonify({"success": True})


@codegen_bp.route("/solutions/<int:solution_id>/codegen/preview/admin")
@login_required
def preview_admin(solution_id):
    """Serve the generated admin UI HTML directly from the stored generated_files.

    The admin HTML is a fully self-contained CRUD interface built from the
    entity definitions at generation time. API calls will fail without the
    generated backend running, but the UI structure (forms, tables, navigation)
    is immediately visible — useful for reviewing the generated admin layout.

    Tries keys: app/static/admin.html (FastAPI) then static/admin.html (Go).
    """
    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return "Access denied", 403

    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
    if not gen or not gen.generated_files:
        return (
            "<html><body style='font-family:sans-serif;padding:2rem;color:#64748b'>"
            "<p>No generated files yet — run Phase 4 first.</p></body></html>",
            404,
            {"Content-Type": "text/html; charset=utf-8"},
        )

    # Try paths in priority order:
    # 1. nextjs_shadcn standalone preview  (frontend/admin.html)
    # 2. python-fastapi static UI          (app/static/admin.html)
    # 3. go-chi static UI                  (static/admin.html)
    html = (
        gen.generated_files.get("frontend/admin.html")
        or gen.generated_files.get("app/static/admin.html")
        or gen.generated_files.get("static/admin.html")
    )
    if not html:
        return (
            "<html><body style='font-family:sans-serif;padding:2rem;color:#64748b'>"
            "<p>Admin UI not found — re-generate with entity definitions to enable "
            "this preview. Supported: python-fastapi, go-chi, nextjs-shadcn.</p>"
            "</body></html>",
            404,
            {"Content-Type": "text/html; charset=utf-8"},
        )

    # Allow same-origin iframe embedding (workbench preview tab)
    from flask import g as _g
    _g.allow_framing = True

    return html, 200, {"Content-Type": "text/html; charset=utf-8"}


# ── StackBlitz / Expo Snack ───────────────────────────────────────────────────

@codegen_bp.route("/solutions/<int:solution_id>/codegen/stackblitz-data")
@login_required
def stackblitz_data(solution_id):
    """Return frontend/ files as JSON for StackBlitz WebContainers live preview.

    The client POSTs these files directly to https://stackblitz.com/run using
    the StackBlitz form-POST API, opening a live Next.js dev server in the
    browser without any local setup or GitHub account required.
    """
    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403

    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
    if not gen or not gen.generated_files:
        return jsonify({"error": "Generate code first"}), 400

    # Extract frontend/ files, strip the prefix so paths are root-relative in StackBlitz
    files = {}
    for path, content in gen.generated_files.items():
        if path.startswith("frontend/"):
            sb_path = path[len("frontend/"):]
            if sb_path:
                files[sb_path] = content

    if not files:
        return jsonify({"error": "No frontend files — generate with shadcn/ui + Next.js enabled"}), 400

    return jsonify({
        "title": f"{solution.name or 'Generated App'} — Next.js Frontend",
        "files": files,
        "file_count": len(files),
    })


@codegen_bp.route("/solutions/<int:solution_id>/codegen/expo-snack", methods=["POST"])
@login_required
@require_csrf
def expo_snack(solution_id):
    """POST mobile files to Expo Snack and return the shareable Snack URL.

    Extracts all files under mobile/ from gen.generated_files, maps them into
    the Snack file tree format, and calls the Snack save API.  The returned URL
    can be opened in a browser and scanned with the Expo Go app on a device.
    """
    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403

    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
    if not gen or not gen.generated_files:
        return jsonify({"error": "Generate code first"}), 400

    import urllib.request
    import json as _json

    # Prefer the Snack-compatible single-file preview (works in browser simulator)
    # Falls back to full multi-file upload (may not render in Snack)
    preview_code = gen.generated_files.get("mobile/_snack_preview.js")
    if preview_code:
        # Single-file mode — guaranteed to work in Snack
        manifest = {"sdkVersion": "52.0.0", "expo": {"sdkVersion": "52.0.0", "name": solution.name or "Entelim App"}}
        payload = _json.dumps({
            "code": preview_code,
            "files": {},
            "manifest": manifest,
            "dependencies": {},
            "name": f"{solution.name or 'Generated App'} — Preview",
            "description": "Generated by Entelim — Architecture & Code Generator",
            "sdkVersion": "52.0.0",
        }).encode()
    else:
        # Full multi-file upload. Snack API expects:
        #   { manifest: {name, sdkVersion}, code: <entry string>,
        #     files: {path: {type, contents}}, dependencies: {pkg: "version"} }
        # dependencies must be flat strings at the top level, NOT inside manifest.
        snack_files = {}
        for path, content in gen.generated_files.items():
            if path.startswith("mobile/") and path.endswith((".ts", ".tsx", ".js", ".jsx", ".css")):
                snack_path = path[len("mobile/"):]
                if snack_path and snack_path not in ("app.json", "package.json", "global.css"):
                    snack_files[snack_path] = {"contents": content, "type": "CODE"}

        if not snack_files:
            return jsonify({"error": "No mobile files found — generate with Expo + React Native enabled"}), 400

        # Entry point as a string (required by Snack as "code")
        entry = gen.generated_files.get("mobile/app/_layout.tsx", "// entry")

        # Flat string dependencies from package.json — Snack rejects {version:...} objects
        try:
            pkg = _json.loads(gen.generated_files.get("mobile/package.json", "{}"))
            deps = {k: v for k, v in pkg.get("dependencies", {}).items() if isinstance(v, str)}
        except Exception:
            deps = {}

        payload = _json.dumps({
            "manifest": {
                "name": f"{solution.name or 'Generated App'} — Preview",
                "description": "Generated by Entelim — Architecture & Code Generator",
                "sdkVersion": "52.0.0",
            },
            "code": entry,
            "files": snack_files,
            "dependencies": deps,
        }).encode()

    req = urllib.request.Request(
        "https://exp.host/--/api/v2/snack/save",
        data=payload,
        headers={"Content-Type": "application/json", "Expo-Platform": "snackager"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            result = _json.loads(resp.read())
        snack_id = result.get("id") or result.get("hashId")
        if not snack_id:
            return jsonify({"error": "Snack API returned no ID", "detail": str(result)}), 502
        snack_url = f"https://snack.expo.dev/{snack_id}"
        return jsonify({"success": True, "snack_url": snack_url, "snack_id": snack_id, "file_count": 1 if preview_code else len(snack_files)})
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        logger.warning("Expo Snack API %s for solution %s: %s", exc.code, solution_id, body)
        return jsonify({"error": f"Snack API {exc.code}: {body}"}), 502
    except Exception as exc:
        logger.warning("Expo Snack API error for solution %s: %s", solution_id, exc)
        return jsonify({"error": f"Expo Snack API unavailable: {exc}"}), 502


# ── Full-Stack Live Preview ────────────────────────────────────────────────────
#
# Deploys the generated docker-compose.yml (api + frontend + db) automatically.
# Ports are allocated per solution: api=9100+(id%100), frontend=3100+(id%100).
# Preview dirs are persistent (/opt/archie-previews/solution-N) so Docker layer
# cache survives between requests — subsequent launches are near-instant.

_PREVIEW_ROOT = "/opt/archie-previews"
_API_PORT_BASE = 9100
_FE_PORT_BASE = 3100
_COMPOSE_BUILD_TIMEOUT = 600  # 10 min first build; cached rebuilds ~30 s


def _docker_available():
    import subprocess
    try:
        subprocess.run(["/usr/bin/docker", "info"], capture_output=True, timeout=5)
        return True
    except Exception:
        return False


def _container_name(solution_id):
    return f"archie-preview-{solution_id}"


def _host_port(solution_id):
    return _API_PORT_BASE + (solution_id % 100)


def _preview_dir(solution_id):
    import os
    d = os.path.join(_PREVIEW_ROOT, f"solution-{solution_id}")
    os.makedirs(d, exist_ok=True)
    return d


def _patch_missing_ui_components(pdir: str) -> None:
    """Scan frontend files for @/components/ui/<name> imports and inject any missing
    shadcn/ui component files from the nextjs_shadcn templates.

    This is a safety net so the docker build doesn't fail when a generated page
    references a shadcn component that wasn't included in the generated file set
    (e.g. Progress, Tabs added after initial generation).
    """
    import os
    from jinja2 import Environment, FileSystemLoader

    # Locate the template directory for shadcn/ui components
    tmpl_dir = os.path.normpath(os.path.join(
        os.path.dirname(__file__),
        "..", "..", "..", "..",
        "app", "modules", "solutions_product", "templates",
        "nextjs_shadcn", "components", "ui",
    ))
    if not os.path.isdir(tmpl_dir):
        return

    # Determine where the frontend components/ui directory lives
    # Generated code uses either  frontend/components/ui/  or  frontend/src/components/ui/
    candidates = [
        os.path.join(pdir, "frontend", "components", "ui"),
        os.path.join(pdir, "frontend", "src", "components", "ui"),
    ]
    ui_dir = next((c for c in candidates if os.path.isdir(c)), None)
    if ui_dir is None:
        # Try to detect from any existing component file
        for root, _dirs, fnames in os.walk(pdir):
            if any(f.endswith(".tsx") and "button" in f for f in fnames):
                ui_dir = root
                break
    if ui_dir is None:
        return

    # Scan all .tsx/.ts files for @/components/ui/<name> imports
    referenced: set[str] = set()
    pattern = re.compile(r'from\s+"@/components/ui/([a-z0-9-]+)"')
    for root, _dirs, fnames in os.walk(pdir):
        for fname in fnames:
            if not fname.endswith((".tsx", ".ts")):
                continue
            try:
                txt = open(os.path.join(root, fname), encoding="utf-8").read()
                for m in pattern.finditer(txt):
                    referenced.add(m.group(1))
            except Exception as exc:
                logger.debug("suppressed error in _patch_missing_ui_components (app/modules/codegen/routes/preview_routes.py): %s", exc)

    # Map: component name → radix-ui package needed (only for components that need a dep)
    _RADIX_DEPS: dict[str, str] = {
        "progress": "@radix-ui/react-progress",
        "tabs": "@radix-ui/react-tabs",
    }

    # Inject any referenced component that doesn't exist on disk
    try:
        env = Environment(loader=FileSystemLoader(tmpl_dir), autoescape=False)
    except Exception:
        return

    missing_deps: list[str] = []
    for comp_name in sorted(referenced):
        target_file = os.path.join(ui_dir, f"{comp_name}.tsx")
        if not os.path.exists(target_file):
            tpl_name = f"{comp_name}.tsx.j2"
            try:
                tpl = env.get_template(tpl_name)
                content = tpl.render()
            except Exception:
                continue
            try:
                os.makedirs(ui_dir, exist_ok=True)
                with open(target_file, "w", encoding="utf-8") as f:
                    f.write(content)
            except Exception:
                continue
        # Always collect the radix dep for referenced components (even if file already existed)
        if comp_name in _RADIX_DEPS:
            missing_deps.append(_RADIX_DEPS[comp_name])

    # Patch frontend/package.json to add any missing radix-ui packages
    if missing_deps:
        import json as _json
        pkg_path = os.path.join(pdir, "frontend", "package.json")
        if os.path.exists(pkg_path):
            try:
                with open(pkg_path) as f:
                    pkg = _json.load(f)
                deps = pkg.setdefault("dependencies", {})
                changed = False
                for dep in missing_deps:
                    if dep not in deps:
                        # Use a safe version that matches the rest of the radix-ui suite
                        deps[dep] = "^1.1.0"
                        changed = True
                if changed:
                    with open(pkg_path, "w") as f:
                        _json.dump(pkg, f, indent=2)
            except Exception as exc:
                logger.debug("suppressed error in _patch_missing_ui_components (app/modules/codegen/routes/preview_routes.py): %s", exc)


def _write_preview_files(files, target_dir):
    """Write generated_files dict to target_dir, stripping any leading 'project/' prefix."""
    import os
    for path, content in files.items():
        if not isinstance(content, str):
            continue
        # Strip the 'project/' wrapper that the zip adds
        rel = path.removeprefix("project/").lstrip("/")
        full = os.path.join(target_dir, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)


PREVIEW_ADMIN_EMAIL = "admin@archie.demo"


def _preview_secrets(gen):
    """Per-preview secrets, generated once and persisted on the generation row.

    These used to be four hardcoded constants - POSTGRES_PASSWORD "archie-preview",
    ADMIN_PASSWORD "Admin2026!", and two "not-for-production" signing keys. Preview
    stacks are published through _register_nginx_proxy() under /apps/<id>/, so those
    were reachable credentials, and this repository is public: the admin password of
    every deployed preview was in the open-source tree. Naming them
    "not-for-production" documented the risk without removing it.

    Generated once and stored under config["_docker_preview"]["secrets"] rather than
    minted per call, because _compose_env() is called twice per deploy - once to write
    .env and once to build the subprocess environment. Fresh randoms at each call site
    would hand docker compose a different password than the one in .env, and the stack
    would fail to start.
    """
    meta = (gen.config or {}).get("_docker_preview", {}) if gen else {}
    existing = meta.get("secrets")
    if existing:
        return existing
    return {
        "POSTGRES_PASSWORD": secrets.token_urlsafe(24),
        "JWT_SECRET": secrets.token_urlsafe(48),
        "SECRET_KEY": secrets.token_urlsafe(48),
        # Typed by a human into the preview's login form, so keep it transcribable.
        "ADMIN_PASSWORD": secrets.token_urlsafe(12),
    }


def _compose_env(solution_id, api_port, fe_port, preview_secrets):
    """Return environment dict to pass to docker compose so ports are correct."""
    return {
        "API_PORT": str(api_port),
        "FRONTEND_PORT": str(fe_port),
        "POSTGRES_DB": f"solution_{solution_id}",
        "POSTGRES_USER": "app",
        "ENV": "development",
        # Seeded admin credentials — auto-created on first startup if users table is empty
        "ADMIN_EMAIL": PREVIEW_ADMIN_EMAIL,
        **preview_secrets,
    }


def _register_nginx_proxy(solution_id: int, fe_port: int, api_port: int) -> None:
    """Add nginx upstream + location blocks for a deployed solution, then reload nginx.

    Idempotent: skips if the solution is already registered.
    Silently no-ops if nginx config files aren't writable (e.g. dev/test env).
    """
    import subprocess as _sp

    UPSTREAMS_CONF = "/etc/nginx/conf.d/archie-apps.conf"
    SITES_CONF = "/etc/nginx/sites-enabled/archie"

    upstream_frontend = f"archie_app_{solution_id}_frontend"
    upstream_api = f"archie_app_{solution_id}_api"

    try:
        # Check if already registered (idempotent)
        with open(UPSTREAMS_CONF) as _f:
            existing = _f.read()
        if f"archie_app_{solution_id}_frontend" in existing:
            return

        # Append upstream blocks
        with open(UPSTREAMS_CONF, "a") as _f:
            _f.write(
                f"\n# Solution {solution_id} — registered by Entelim preview launch\n"
                f"upstream {upstream_frontend} {{\n"
                f"    server 127.0.0.1:{fe_port};\n"
                f"}}\n"
                f"upstream {upstream_api} {{\n"
                f"    server 127.0.0.1:{api_port};\n"
                f"}}\n"
            )

        # Insert location block before the catch-all "location /" in sites conf
        with open(SITES_CONF) as _f:
            sites = _f.read()

        location_block = (
            f"\n    # Solution {solution_id} — registered by Entelim preview launch\n"
            f"    location ^~ /apps/{solution_id} {{\n"
            f"        proxy_pass http://{upstream_frontend};\n"
            f"        proxy_set_header Host $host;\n"
            f"        proxy_set_header X-Real-IP $remote_addr;\n"
            f"        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
            f"        proxy_set_header X-Forwarded-Proto $scheme;\n"
            f"        proxy_http_version 1.1;\n"
            f"        proxy_set_header Upgrade $http_upgrade;\n"
            f"        proxy_set_header Connection \"upgrade\";\n"
            f"        proxy_read_timeout 60s;\n"
            f"    }}\n"
        )
        # Insert before the final catch-all "location /"
        sites = sites.replace("\n    location / {", location_block + "\n    location / {", 1)
        with open(SITES_CONF, "w") as _f:
            _f.write(sites)

        _sp.run(["nginx", "-t"], check=True, capture_output=True)
        _sp.run(["systemctl", "reload", "nginx"], check=True, capture_output=True)
        import logging as _log
        _log.getLogger(__name__).info(
            "nginx proxy registered for solution %d (fe=%d api=%d)", solution_id, fe_port, api_port
        )
    except Exception as _exc:
        import logging as _log
        _log.getLogger(__name__).warning(
            "Could not register nginx proxy for solution %d: %s", solution_id, _exc
        )


@codegen_bp.route("/solutions/<int:solution_id>/codegen/docker-preview", methods=["POST"])
@login_required
def docker_preview_start(solution_id):
    """Deploy the full generated stack (api + frontend + db) via docker compose.

    Uses the docker-compose.yml that the pipeline generates, so no manual
    wiring is needed — the frontend proxy to the api is already configured.
    Returns the live frontend URL and the api URL.

    Port allocation (per solution, collision-free for up to 100 concurrent previews):
      API:      9100 + (solution_id % 100)
      Frontend: 3100 + (solution_id % 100)
    """
    import os
    import subprocess
    from sqlalchemy.orm.attributes import flag_modified

    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403

    if not _docker_available():
        return jsonify({"error": "Docker is not available on this server"}), 503

    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
    if not gen or not gen.generated_files:
        return jsonify({"error": "No generated files — run Generate first"}), 400

    files = gen.generated_files
    if not isinstance(files, dict) or not files:
        return jsonify({"error": "Generated files are empty"}), 400

    # Detect capability from file contents rather than gen.config["language"],
    # which may reflect a different prior generation (e.g. azure-logic-app).
    has_compose = any("docker-compose" in p for p in files)
    has_backend = any(
        p.endswith("app/main.py") or p == "main.py" or p.endswith("/main.go")
        for p in files
    )
    if not has_compose or not has_backend:
        return jsonify({
            "error": "Full-stack preview requires a python-fastapi or go-chi generation "
                     "(needs docker-compose.yml + backend entry point). "
                     "Re-generate with language=python-fastapi first."
        }), 400

    api_port = _API_PORT_BASE + (solution_id % 100)
    fe_port = _FE_PORT_BASE + (solution_id % 100)
    pdir = _preview_dir(solution_id)
    project_name = f"archie-{solution_id}"

    # Tear down any existing compose project for this solution
    subprocess.run(
        ["/usr/bin/docker", "compose", "--project-name", project_name, "down", "--remove-orphans"],
        cwd=pdir, capture_output=True, timeout=30,
    )

    # Wipe preview directory contents before writing new files so stale files
    # from old generator versions (e.g. src/ layout vs root layout) don't
    # interfere with the build. Override files are written after this step.
    import shutil
    for item in os.listdir(pdir):
        item_path = os.path.join(pdir, item)
        try:
            if os.path.isdir(item_path):
                shutil.rmtree(item_path)
            else:
                os.unlink(item_path)
        except Exception as exc:
            logger.debug("suppressed error in docker_preview_start (app/modules/codegen/routes/preview_routes.py): %s", exc)

    # Write generated files to the persistent preview directory
    _write_preview_files(files, pdir)

    # Inject any missing shadcn/ui component files (e.g. Progress, Tabs)
    # that pages reference but weren't included in the generated file set.
    _patch_missing_ui_components(pdir)

    # Write a docker-compose.override.yml to pin the host ports for this solution.
    # NEXT_PUBLIC_BASE_PATH must match the nginx prefix so Next.js basePath is correct.
    # Nginx routes /apps/<id>/* to this frontend container, so basePath must be /apps/<id>.
    base_path = f"/apps/{solution_id}"
    override_yml = f"""services:
  api:
    ports:
      - "{api_port}:8000"
  frontend:
    ports:
      - "{fe_port}:3000"
    environment:
      BACKEND_URL: http://api:8000
      NEXT_PUBLIC_BASE_PATH: "{base_path}"
    build:
      args:
        NEXT_PUBLIC_BASE_PATH: "{base_path}"
"""
    with open(os.path.join(pdir, "docker-compose.override.yml"), "w") as f:
        f.write(override_yml)

    # Resolve once: .env and the subprocess environment must agree, and a redeploy of
    # a still-running stack must not rotate the password out from under its database.
    preview_secrets = _preview_secrets(gen)
    compose_env = _compose_env(solution_id, api_port, fe_port, preview_secrets)

    # Write .env so docker compose picks up database credentials etc.
    env_content = "\n".join(f"{k}={v}" for k, v in compose_env.items())
    with open(os.path.join(pdir, ".env"), "w") as f:
        f.write(env_content + "\n")
    os.chmod(os.path.join(pdir, ".env"), 0o600)

    # Launch docker compose in background (--build triggers rebuild only if files changed)
    env = {**os.environ, **compose_env}
    log_path = os.path.join(pdir, "compose.log")
    with open(log_path, "w") as log_f:
        proc = subprocess.Popen(
            ["/usr/bin/docker", "compose", "--project-name", project_name, "up", "--build", "-d"],
            cwd=pdir, env=env, stdout=log_f, stderr=log_f,
        )
    exit_code = proc.wait(timeout=_COMPOSE_BUILD_TIMEOUT)
    if exit_code != 0:
        with open(log_path) as f:
            tail = f.read()[-1200:]
        return jsonify({"error": "docker compose up failed", "detail": tail}), 500

    server_host = request.host.split(":")[0]
    app_url = f"http://{server_host}:{fe_port}"
    api_url = f"http://{server_host}:{api_port}"

    # Register nginx proxy so /apps/<id>/* routes to the frontend container
    _register_nginx_proxy(solution_id, fe_port, api_port)

    # Persist preview metadata
    config = dict(gen.config or {})
    config["_docker_preview"] = {
        "project_name": project_name,
        "api_port": api_port,
        "fe_port": fe_port,
        "preview_dir": pdir,
        "app_url": app_url,
        "api_url": api_url,
        "secrets": preview_secrets,
    }
    gen.config = config
    flag_modified(gen, "config")
    db.session.commit()

    return jsonify({
        "success": True,
        "app_url": app_url,
        "api_url": api_url,
        "api_port": api_port,
        "fe_port": fe_port,
        # The seeded login is now random per preview, so it has to be reported back or
        # the stack is unusable. It was never displayed before - the only copy of
        # "Admin2026!" lived in this source file, so the only way in was to read it.
        "admin_email": PREVIEW_ADMIN_EMAIL,
        "admin_password": preview_secrets["ADMIN_PASSWORD"],
    })


@codegen_bp.route("/solutions/<int:solution_id>/codegen/docker-preview/status")
@login_required
def docker_preview_status(solution_id):
    """Return running status and URLs for the full-stack preview."""
    import subprocess

    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403

    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
    meta = (gen.config or {}).get("_docker_preview", {}) if gen else {}
    project_name = meta.get("project_name", f"archie-{solution_id}")
    api_port = meta.get("api_port", _API_PORT_BASE + (solution_id % 100))
    fe_port = meta.get("fe_port", _FE_PORT_BASE + (solution_id % 100))

    try:
        result = subprocess.run(
            ["/usr/bin/docker", "compose", "--project-name", project_name, "ps", "--format", "json"],
            cwd=_preview_dir(solution_id), capture_output=True, text=True, timeout=5,
        )
        import json as _json
        services = []
        for line in result.stdout.strip().splitlines():
            try:
                services.append(_json.loads(line))
            except Exception as exc:
                logger.debug("suppressed error in docker_preview_status (app/modules/codegen/routes/preview_routes.py): %s", exc)
        running = len(services) > 0
    except Exception:
        running = False
        services = []

    server_host = request.host.split(":")[0]
    app_url = meta.get("app_url") or (f"http://{server_host}:{fe_port}" if running else None)
    api_url = meta.get("api_url") or (f"http://{server_host}:{api_port}" if running else None)
    # Re-report the seeded login while the stack is up. The password is random per
    # preview now, so a user who navigated away after deploying has no other way to
    # recover it. Withheld once the stack is down, since there is nothing to log into.
    stack_secrets = meta.get("secrets") or {}
    return jsonify({
        "running": running,
        "app_url": app_url,
        "api_url": api_url,
        "services": [s.get("Service", "") for s in services],
        "admin_email": PREVIEW_ADMIN_EMAIL if (running and stack_secrets) else None,
        "admin_password": stack_secrets.get("ADMIN_PASSWORD") if running else None,
    })


@codegen_bp.route("/solutions/<int:solution_id>/codegen/docker-preview/stop", methods=["POST"])
@login_required
def docker_preview_stop(solution_id):
    """Stop and remove the full-stack preview compose project."""
    import subprocess

    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403

    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
    project_name = ((gen.config or {}).get("_docker_preview", {}).get("project_name")
                    if gen else None) or f"archie-{solution_id}"
    try:
        subprocess.run(
            ["/usr/bin/docker", "compose", "--project-name", project_name, "down", "--remove-orphans"],
            cwd=_preview_dir(solution_id), capture_output=True, timeout=30,
        )
    except Exception as exc:
        logger.debug("Compose down cleanup failed (non-critical): %s", exc)
    return jsonify({"success": True})
