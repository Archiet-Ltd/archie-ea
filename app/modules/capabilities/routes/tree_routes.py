"""
Generic Tree API — serves any tree registered in TREE_REGISTRY.

Routes:
    GET  /trees                             — tree browser index
    GET  /api/trees                         — list available trees
    GET  /api/trees/<key>                   — nested tree JSON
    GET  /api/trees/<key>/<int:node_id>     — single node
    POST /api/trees/<key>                   — create node
    PATCH /api/trees/<key>/<int:node_id>    — update node
    DELETE /api/trees/<key>/<int:node_id>   — delete node
    GET  /tree/<key>                        — tree viewer page
"""

from flask import jsonify, render_template, request
from flask_login import login_required

from app.decorators import require_roles

from . import capability_map

# One write-role gate for every mutating tree route, matching the generic
# Elements page (architecture_crud_routes.py): admin or architect. Reads stay
# open to any signed-in user.
write_role_required = require_roles("admin", "architect")


def _error_status(error, default=400):
    """Map a tree_service error string to an HTTP status."""
    from app.services.tree_service import CONFLICT, FORBIDDEN, INVALID

    if error.startswith(FORBIDDEN):
        return 403
    if error.startswith(CONFLICT):
        return 409
    if error.startswith(INVALID):
        return 400
    if error == "Not found":
        return 404
    return default


@capability_map.route("/trees")
@login_required
def tree_browser():
    """Tree browser index — lists all available tree types with node counts."""
    from app.services.tree_service import TREE_REGISTRY, build_tree

    trees = []
    for key, cfg in TREE_REGISTRY.items():
        try:
            data = build_tree(key)
            node_count = _count_nodes(data)
            root_count = len(data.get("children", []))
        except Exception:
            node_count = 0
            root_count = 0
        trees.append({
            "key": key,
            "label": cfg["root_label"],
            "fields": cfg["fields"],
            "node_count": node_count,
            "root_count": root_count,
        })
    return render_template("capability_map/tree_index.html", trees=trees)


def _count_nodes(node):
    """Count all nodes with an id in a nested tree dict."""
    c = 1 if node.get("id") else 0
    for ch in node.get("children", []):
        c += _count_nodes(ch)
    return c


@capability_map.route("/api/trees")
@login_required
def api_generic_tree_list():
    """List all available tree types."""
    from app.services.tree_service import TREE_REGISTRY

    trees = []
    for key, cfg in TREE_REGISTRY.items():
        trees.append({
            "key": key,
            "label": cfg["root_label"],
            "name_field": cfg["name_field"],
        })
    return jsonify(trees)


@capability_map.route("/api/trees/<key>")
@login_required
def api_generic_tree_data(key):
    """Get full nested tree for a given tree type."""
    from app.services.tree_service import TREE_REGISTRY, build_tree

    if key not in TREE_REGISTRY:
        return jsonify({"error": f"Unknown tree: {key}"}), 404
    try:
        data = build_tree(key)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@capability_map.route("/api/trees/<key>/<int:node_id>", methods=["GET"])
@login_required
def api_generic_tree_get_node(key, node_id):
    """Get a single tree node."""
    from app.services.tree_service import TREE_REGISTRY, get_node

    if key not in TREE_REGISTRY:
        return jsonify({"error": f"Unknown tree: {key}"}), 404
    node = get_node(key, node_id)
    if not node:
        return jsonify({"error": "Not found"}), 404
    return jsonify(node)


@capability_map.route("/api/trees/<key>", methods=["POST"])
@login_required
@write_role_required
def api_generic_tree_create_node(key):
    """Create a new node in a tree."""
    from app.services.tree_service import TREE_REGISTRY, create_node

    if key not in TREE_REGISTRY:
        return jsonify({"error": f"Unknown tree: {key}"}), 404
    data = request.get_json(silent=True) or {}
    node, error = create_node(key, data)
    if error:
        return jsonify({"error": error}), _error_status(error)
    return jsonify(node), 201


@capability_map.route("/api/trees/<key>/<int:node_id>", methods=["PATCH"])
@login_required
@write_role_required
def api_generic_tree_update_node(key, node_id):
    """Update a tree node."""
    from app.services.tree_service import TREE_REGISTRY, update_node

    if key not in TREE_REGISTRY:
        return jsonify({"error": f"Unknown tree: {key}"}), 404
    data = request.get_json(silent=True) or {}
    node, error = update_node(key, node_id, data)
    if error:
        return jsonify({"error": error}), _error_status(error)
    return jsonify(node)


@capability_map.route("/api/trees/<key>/<int:node_id>", methods=["DELETE"])
@login_required
@write_role_required
def api_generic_tree_delete_node(key, node_id):
    """Delete a tree node (reparents children)."""
    from app.services.tree_service import TREE_REGISTRY, delete_node

    if key not in TREE_REGISTRY:
        return jsonify({"error": f"Unknown tree: {key}"}), 404
    error = delete_node(key, node_id)
    if error:
        return jsonify({"error": error}), _error_status(error, 404)
    return jsonify({"success": True})


@capability_map.route("/tree/<key>")
@login_required
def generic_tree_view(key):
    """Render the generic tree viewer for any registered tree type."""
    from app.services.tree_service import TREE_REGISTRY

    if key not in TREE_REGISTRY:
        return render_template("capability_map/error.html", error=f"Unknown tree: {key}"), 404

    config = TREE_REGISTRY[key]
    return render_template(
        "capability_map/tree_generic.html",
        tree_key=key,
        tree_label=config["root_label"],
        tree_fields=config["fields"],
        api_url=f"/capability-map/api/trees/{key}",
    )
