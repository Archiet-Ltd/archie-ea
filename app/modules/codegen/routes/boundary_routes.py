"""System boundary routes for the Code Workbench.

Covers:
- /codegen/system-boundaries                                    (GET) — management page
- /api/codegen/system-boundaries                               (GET, POST)
- /api/codegen/system-boundaries/<id>                          (GET, DELETE)
- /api/codegen/system-boundaries/<id>/solutions                (POST)
- /api/codegen/system-boundaries/<id>/solutions/<sol_id>       (DELETE)
- /api/codegen/system-boundaries/<id>/generate                 (POST)
- /api/codegen/system-boundaries/<id>/contracts                (GET)

Registered on ``codegen_bp`` (defined in codegen_routes.py).
Imported at the bottom of codegen_routes.py so routes attach automatically.
"""
import logging
import re

from flask import jsonify, render_template, request
from flask_login import current_user, login_required

from app.extensions import db
from app.modules.codegen.models import (
    CodegenGeneration,
    CodegenSystemBoundary,
    SystemBoundarySolution,
)
SystemBoundary = CodegenSystemBoundary
from app.models.solution_models import Solution
from app.utils.csrf_helper import require_csrf

from .codegen_routes import codegen_bp

logger = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────────────────────────────

_SB_TABLES_ENSURED = False


def _ensure_system_boundary_tables():
    """Create system boundary tables if they don't exist (migration-exempt)."""
    global _SB_TABLES_ENSURED
    if _SB_TABLES_ENSURED:
        return
    try:
        db.create_all()
        _SB_TABLES_ENSURED = True
    except Exception as e:
        logger.warning("System boundary table creation failed: %s", e)


def _boundary_to_dict(boundary, include_solutions=False):
    """Serialize a SystemBoundary to a dict."""
    d = {
        "id": boundary.id,
        "name": boundary.name,
        "description": boundary.description,
        "created_at": boundary.created_at.isoformat() if boundary.created_at else None,
        "generated_at": boundary.generated_at.isoformat() if boundary.generated_at else None,
        "has_artifacts": boundary.generated_artifacts is not None,
    }
    if include_solutions:
        sol_list = []
        for sbs in boundary.solutions.all():
            sol_list.append({
                "id": sbs.id,
                "solution_id": sbs.solution_id,
                "solution_name": sbs.solution.name if sbs.solution else f"Solution {sbs.solution_id}",
                "role": sbs.role,
                "service_port": sbs.service_port,
                "has_codegen": CodegenGeneration.query.filter_by(
                    solution_id=sbs.solution_id
                ).filter(CodegenGeneration.generated_files.isnot(None)).count() > 0,
            })
        d["solutions"] = sol_list
        d["solution_count"] = len(sol_list)
    return d


def _generate_docker_compose(solutions_data):
    """Generate docker-compose.yml content from list of {name, slug, port} dicts."""
    lines = ["version: '3.8'", "", "services:"]
    for svc in solutions_data:
        slug = svc["slug"]
        port = svc["port"]
        lines += [
            f"  {slug}:",
            f"    build: ./{svc['repo_name']}",
            "    ports:",
            f"      - \"{port}:8000\"",
            "    environment:",
            f"      - DATABASE_URL=postgresql://postgres:${{POSTGRES_PASSWORD:?set POSTGRES_PASSWORD in .env}}@db:5432/{slug}",
            f"      - SERVICE_NAME={slug}",
            "    depends_on:",
            "      - db",
            "    restart: unless-stopped",
            "",
        ]
    lines += [
        "  db:",
        "    image: postgres:15-alpine",
        "    environment:",
        "      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD in .env}",
        "    volumes:",
        "      - pgdata:/var/lib/postgresql/data",
        "    restart: unless-stopped",
        "",
        "  nginx:",
        "    image: nginx:alpine",
        "    ports:",
        '      - "80:80"',
        "    volumes:",
        "      - ./nginx.conf:/etc/nginx/conf.d/default.conf:ro",
        "    depends_on:",
    ]
    for svc in solutions_data:
        lines.append(f"      - {svc['slug']}")
    lines += [
        "    restart: unless-stopped",
        "",
        "volumes:",
        "  pgdata:",
    ]
    return "\n".join(lines)


def _generate_nginx_conf(solutions_data):
    """Generate nginx.conf for routing requests to microservices."""
    lines = ["server {", "    listen 80;", "    server_name _;", ""]
    for svc in solutions_data:
        slug = svc["slug"]
        lines += [
            f"    # {svc['name']}",
            f"    location /{slug}/ {{",
            f"        proxy_pass http://{slug}:8000/;",
            "        proxy_set_header Host $host;",
            "        proxy_set_header X-Real-IP $remote_addr;",
            "    }",
            "",
        ]
    lines.append("}")
    return "\n".join(lines)


def _generate_client_sdk(consumer_name, producer_name, contracts):
    """Generate a minimal Python SDK stub for inter-service calls."""
    re.sub(r"[^a-zA-Z0-9]", "_", consumer_name).lower()
    producer_slug = re.sub(r"[^a-zA-Z0-9]", "_", producer_name).lower()
    class_name = "".join(w.capitalize() for w in producer_slug.split("_")) + "Client"

    methods = []
    seen_endpoints = set()
    for c in contracts:
        endpoint = c.get("endpoint", "")
        method = c.get("method", "GET").upper()
        key = f"{method}:{endpoint}"
        if key in seen_endpoints:
            continue
        seen_endpoints.add(key)
        # Derive method name from endpoint
        parts = endpoint.strip("/").split("/")
        resource = parts[-2] if len(parts) >= 2 and "{" in parts[-1] else parts[-1]
        func_name = f"get_{resource.replace('-', '_')}" if method == "GET" else f"{method.lower()}_{resource.replace('-', '_')}"
        has_id = "{id}" in endpoint
        params = "self, id: int" if has_id else "self, skip: int = 0, limit: int = 100"
        url_expr = f'f"{{self.base_url}}{endpoint.replace("{id}", "{id}")}"' if has_id else f'f"{{self.base_url}}{endpoint}?skip={{skip}}&limit={{limit}}"'
        methods.append(
            f"    def {func_name}({params}):\n"
            f"        \"\"\"Call {producer_name}: {method} {endpoint}\"\"\"\n"
            f"        resp = self._session.{method.lower()}({url_expr})\n"
            f"        resp.raise_for_status()\n"
            f"        return resp.json()"
        )

    return f'''"""
Auto-generated client SDK: {consumer_name} → {producer_name}
Generated by Entelim Code Workbench (Multi-Solution Composition)
"""
import httpx


class {class_name}:
    """HTTP client for calling {producer_name} from {consumer_name}."""

    def __init__(self, base_url: str, api_key: str = ""):
        self.base_url = base_url.rstrip("/")
        self._session = httpx.Client(
            base_url=self.base_url,
            headers={{"Authorization": f"Bearer {{api_key}}"}} if api_key else {{}},
            timeout=30.0,
        )

{chr(10).join(methods) if methods else "    pass"}

    def close(self):
        self._session.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
'''


def _detect_cross_solution_contracts(boundary_solutions):
    """Detect ArchiMate relationships spanning solutions → inter-service contracts."""
    from app.models.solution_archimate_element import SolutionArchiMateElement

    # Build element_id → solution_id mapping
    sol_ids = [sbs.solution_id for sbs in boundary_solutions]
    junctions = SolutionArchiMateElement.query.filter(
        SolutionArchiMateElement.solution_id.in_(sol_ids)
    ).all()
    element_to_solution = {j.element_id: j.solution_id for j in junctions if j.element_id}

    contracts = []
    try:
        from app.models.archimate_core import ArchiMateRelationship
        # Find relationships where source and target belong to different solutions
        for j in junctions:
            if not j.element_id:
                continue
            rels = ArchiMateRelationship.query.filter_by(source_id=j.element_id).all()
            for rel in rels:
                target_sol_id = element_to_solution.get(rel.target_id)
                if target_sol_id and target_sol_id != j.solution_id:
                    # Cross-solution relationship found
                    target_junction = next(
                        (x for x in junctions if x.element_id == rel.target_id and x.solution_id == target_sol_id),
                        None,
                    )
                    target_slug = (target_junction.element_name or f"resource_{rel.target_id}").lower().replace(" ", "_")
                    contracts.append({
                        "consumer_solution_id": j.solution_id,
                        "producer_solution_id": target_sol_id,
                        "consumer_element": j.element_name or f"Element {j.element_id}",
                        "producer_element": target_junction.element_name if target_junction else f"Element {rel.target_id}",
                        "relationship_type": rel.relationship_type or "uses",
                        "endpoint": f"/api/{target_slug}s/{{id}}",
                        "method": "GET",
                        "description": f"{j.element_name} {rel.relationship_type or 'uses'} {target_junction.element_name if target_junction else 'remote element'}",
                    })
    except Exception as e:
        logger.warning("Cross-solution contract detection failed: %s", e)

    return contracts


# ── System Boundary page ───────────────────────────────────────────────────────

@codegen_bp.route("/codegen/system-boundaries")
@login_required
def system_boundaries_page():
    """Management page for multi-solution system boundaries."""
    _ensure_system_boundary_tables()
    boundaries = SystemBoundary.query.filter_by(
        created_by_id=current_user.id
    ).order_by(SystemBoundary.created_at.desc()).all()
    # Serialize to dicts so tojson works in the template
    boundaries_data = [_boundary_to_dict(b, include_solutions=True) for b in boundaries]
    # All solutions available to add to a boundary
    solutions = Solution.query.order_by(Solution.name).all()
    return render_template(
        "codegen/system_boundaries.html",
        boundaries=boundaries_data,
        solutions=solutions,
    )


# ── System Boundary API ────────────────────────────────────────────────────────

@codegen_bp.route("/api/codegen/system-boundaries")
@login_required
def list_system_boundaries():
    """List all system boundaries for the current user."""
    _ensure_system_boundary_tables()
    boundaries = SystemBoundary.query.filter_by(
        created_by_id=current_user.id
    ).order_by(SystemBoundary.created_at.desc()).all()
    return jsonify({
        "boundaries": [_boundary_to_dict(b, include_solutions=True) for b in boundaries]
    })


@codegen_bp.route("/api/codegen/system-boundaries", methods=["POST"])
@login_required
@require_csrf
def create_system_boundary():
    """Create a new system boundary."""
    _ensure_system_boundary_tables()
    payload = request.get_json(silent=True) or {}
    name = (payload.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400

    boundary = SystemBoundary(
        name=name,
        description=(payload.get("description") or "").strip() or None,
        created_by_id=current_user.id,
    )
    db.session.add(boundary)
    db.session.commit()
    return jsonify({"success": True, "boundary": _boundary_to_dict(boundary, include_solutions=True)}), 201


@codegen_bp.route("/api/codegen/system-boundaries/<int:boundary_id>")
@login_required
def get_system_boundary(boundary_id):
    """Get a system boundary with its solutions."""
    _ensure_system_boundary_tables()
    boundary = SystemBoundary.query.get_or_404(boundary_id)
    if boundary.created_by_id != current_user.id:
        return jsonify({"error": "Access denied"}), 403
    return jsonify(_boundary_to_dict(boundary, include_solutions=True))


@codegen_bp.route("/api/codegen/system-boundaries/<int:boundary_id>", methods=["DELETE"])
@login_required
def delete_system_boundary(boundary_id):
    """Delete a system boundary."""
    boundary = SystemBoundary.query.get_or_404(boundary_id)
    if boundary.created_by_id != current_user.id:
        return jsonify({"error": "Access denied"}), 403
    db.session.delete(boundary)
    db.session.commit()
    return jsonify({"success": True})


@codegen_bp.route("/api/codegen/system-boundaries/<int:boundary_id>/solutions", methods=["POST"])
@login_required
@require_csrf
def add_solution_to_boundary(boundary_id):
    """Add a solution to a system boundary."""
    boundary = SystemBoundary.query.get_or_404(boundary_id)
    if boundary.created_by_id != current_user.id:
        return jsonify({"error": "Access denied"}), 403

    payload = request.get_json(silent=True) or {}
    solution_id = payload.get("solution_id")
    if not solution_id:
        return jsonify({"error": "solution_id is required"}), 400

    solution = Solution.query.get(solution_id)
    if not solution:
        return jsonify({"error": "Solution not found"}), 404

    # Dedup
    existing = SystemBoundarySolution.query.filter_by(
        boundary_id=boundary_id, solution_id=solution_id
    ).first()
    if existing:
        return jsonify({"error": "Solution already in this boundary"}), 409

    # Assign next port
    used_ports = {sbs.service_port for sbs in boundary.solutions.all() if sbs.service_port}
    port = 8001
    while port in used_ports:
        port += 1

    sbs = SystemBoundarySolution(
        boundary_id=boundary_id,
        solution_id=solution_id,
        role=payload.get("role", "service"),
        service_port=port,
    )
    db.session.add(sbs)
    db.session.commit()
    return jsonify({"success": True, "boundary": _boundary_to_dict(boundary, include_solutions=True)}), 201


@codegen_bp.route(
    "/api/codegen/system-boundaries/<int:boundary_id>/solutions/<int:solution_id>",
    methods=["DELETE"],
)
@login_required
@require_csrf
def remove_solution_from_boundary(boundary_id, solution_id):
    """Remove a solution from a system boundary."""
    boundary = SystemBoundary.query.get_or_404(boundary_id)
    if boundary.created_by_id != current_user.id:
        return jsonify({"error": "Access denied"}), 403

    sbs = SystemBoundarySolution.query.filter_by(
        boundary_id=boundary_id, solution_id=solution_id
    ).first()
    if not sbs:
        return jsonify({"error": "Solution not in this boundary"}), 404

    db.session.delete(sbs)
    db.session.commit()
    return jsonify({"success": True})


@codegen_bp.route("/api/codegen/system-boundaries/<int:boundary_id>/generate", methods=["POST"])
@login_required
@require_csrf
def generate_system_artifacts(boundary_id):
    """Generate docker-compose, nginx config, contracts, and client SDKs for all solutions."""
    boundary = SystemBoundary.query.get_or_404(boundary_id)
    if boundary.created_by_id != current_user.id:
        return jsonify({"error": "Access denied"}), 403

    boundary_solutions = boundary.solutions.all()
    if len(boundary_solutions) < 2:
        return jsonify({"error": "Need at least 2 solutions to compose a system"}), 400

    # Build per-solution metadata
    solutions_data = []
    solution_map = {}  # solution_id → metadata
    for sbs in boundary_solutions:
        sol = sbs.solution
        if not sol:
            continue
        gen = CodegenGeneration.query.filter_by(solution_id=sbs.solution_id).first()
        config = gen.config or {} if gen else {}
        repo_name = config.get("repo_name") or re.sub(r"[^a-zA-Z0-9-]", "-", (sol.name or f"solution-{sol.id}").lower())
        slug = re.sub(r"[^a-zA-Z0-9]", "_", (sol.name or f"solution_{sol.id}").lower())
        meta = {
            "id": sol.id,
            "name": sol.name or f"Solution {sol.id}",
            "slug": slug,
            "repo_name": repo_name,
            "port": sbs.service_port or (8000 + sol.id % 100),
            "role": sbs.role,
            "has_codegen": gen is not None and gen.generated_files is not None,
        }
        solutions_data.append(meta)
        solution_map[sol.id] = meta

    # Generate docker-compose.yml + nginx.conf
    docker_compose = _generate_docker_compose(solutions_data)
    nginx_conf = _generate_nginx_conf(solutions_data)

    # Detect cross-solution contracts
    raw_contracts = _detect_cross_solution_contracts(boundary_solutions)

    # Enrich contracts with solution names
    contracts = []
    for c in raw_contracts:
        consumer_meta = solution_map.get(c["consumer_solution_id"])
        producer_meta = solution_map.get(c["producer_solution_id"])
        if consumer_meta and producer_meta:
            contracts.append({
                **c,
                "consumer_name": consumer_meta["name"],
                "producer_name": producer_meta["name"],
            })

    # Generate client SDK per consumer→producer pair
    client_sdks = {}
    producer_per_consumer = {}
    for c in contracts:
        key = (c["consumer_solution_id"], c["producer_solution_id"])
        producer_per_consumer.setdefault(key, []).append(c)

    for (consumer_id, producer_id), pair_contracts in producer_per_consumer.items():
        consumer_name = solution_map.get(consumer_id, {}).get("name", f"Solution {consumer_id}")
        producer_name = solution_map.get(producer_id, {}).get("name", f"Solution {producer_id}")
        sdk_key = f"{solution_map.get(consumer_id, {}).get('slug', consumer_id)}_calls_{solution_map.get(producer_id, {}).get('slug', producer_id)}"
        client_sdks[f"sdks/{sdk_key}_client.py"] = _generate_client_sdk(
            consumer_name, producer_name, pair_contracts
        )

    from datetime import datetime as _dt
    artifacts = {
        "docker_compose": docker_compose,
        "nginx_conf": nginx_conf,
        "contracts": contracts,
        "client_sdks": client_sdks,
        "solution_count": len(solutions_data),
        "contract_count": len(contracts),
    }

    boundary.generated_artifacts = artifacts
    boundary.generated_at = _dt.utcnow()
    db.session.commit()

    return jsonify({
        "success": True,
        "solution_count": len(solutions_data),
        "contract_count": len(contracts),
        "sdk_count": len(client_sdks),
        "artifacts": artifacts,
    })


@codegen_bp.route("/api/codegen/system-boundaries/<int:boundary_id>/contracts")
@login_required
def get_system_contracts(boundary_id):
    """Get inter-service contracts for a system boundary."""
    boundary = SystemBoundary.query.get_or_404(boundary_id)
    if boundary.created_by_id != current_user.id:
        return jsonify({"error": "Access denied"}), 403

    if not boundary.generated_artifacts:
        return jsonify({"error": "Run /generate first"}), 404

    return jsonify({
        "boundary_id": boundary_id,
        "boundary_name": boundary.name,
        "contracts": boundary.generated_artifacts.get("contracts", []),
        "generated_at": boundary.generated_at.isoformat() if boundary.generated_at else None,
    })
