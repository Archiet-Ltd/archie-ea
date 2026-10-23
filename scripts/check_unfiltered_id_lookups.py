#!/usr/bin/env python
"""Count lookups by id on organisation-scoped models that skip the organisation filter.

    python scripts/check_unfiltered_id_lookups.py            # list
    python scripts/check_unfiltered_id_lookups.py --count    # count only
    python scripts/check_unfiltered_id_lookups.py --json

The shapes::

    Model.query.get(record_id)
    db.session.get(Model, record_id)

``get`` answers from the session's identity map without running a query when the object is
already loaded, so the ``do_orm_execute`` tenant filter never runs. In a request-scoped session
that is unlikely to matter; in a long-lived session or a background job it is the shape of a
cross-tenant read, and every reviewer has to re-argue it. ``TenantMixin.get_in_org(record_id)``
(app/models/mixins/core.py) runs a real query with ``organization_id`` in it.

Only models that carry a tenant fence (``TenantMixin``, ``HybridTenantMixin``,
``HybridCapabilityTenantMixin``) are counted; an unfenced model is the business of
``check_untenanted_reads.py``. Model discovery is that script's. A model whose name is defined
twice with different fencing is skipped rather than guessed. Models under app/models/, app/commands/
and app/_bootstrap/ are not counted (cross-tenant on purpose), as there.

The count is a ratchet: it may fall, never rise. Convert a call to ``Model.get_in_org(id)``;
a deliberate one carries ``unfiltered-id-lookup-ok: <reason>`` on its statement or the line before.

Proven-against: a new ``SolutionAnalysisSession.query.get(workspace_id)`` added to a service --
the count rises by one and the gate fails (tests/test_check_unfiltered_id_lookups.py).
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_untenanted_reads as base  # noqa: E402  (model discovery is shared)

REPO = base.REPO
APP = base.APP
MARKER = re.compile(r"unfiltered-id-lookup-ok:\s*\S")


def fenced_models(classes):
    """Names of models that carry a tenant fence, skipping names defined with mixed fencing."""
    out = set()
    for name in classes:
        is_model, fenced, _ = base._resolve(name, classes)
        if not (is_model and fenced) or name in base.GLOBAL_MODELS:
            continue
        statuses = {base._resolve(name, {name: [i]})[1] for i in classes[name]}
        if len(classes[name]) > 1 and len(statuses) > 1:
            continue
        out.add(name)
    return out


def _find(tree, lines, models):
    hits = []
    for node in ast.walk(tree):
        name = None
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get":
            target = node.func.value
            if isinstance(target, ast.Attribute) and target.attr == "query":
                short = ast.unparse(target.value).split(".")[-1]
                name = short if short in models else None
            elif ast.unparse(target).endswith("session") and node.args:
                short = ast.unparse(node.args[0]).split(".")[-1]
                name = short if short in models else None
        if not name:
            continue
        lo, hi = node.lineno, node.end_lineno or node.lineno
        text = "\n".join(lines[lo - 1:hi]) + "\n" + (lines[lo - 2] if lo >= 2 else "")
        if MARKER.search(text):
            continue
        hits.append({"line": node.lineno, "model": name})
    return hits


def scan(app=None, repo=None):
    app, repo = app or APP, repo or REPO
    models = fenced_models(base.discover_models(app, repo))
    out = []
    for path in base._py_files(app):
        rel = path.relative_to(repo).as_posix()
        if any(rel.startswith(d) for d in base.SKIP_DIRS):
            continue
        tree = base._parse(path)
        if tree is None:
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for h in _find(tree, lines, models):
            out.append({"file": rel, **h})
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--root", default=None)
    args = parser.parse_args()
    if args.root:
        root = Path(args.root).resolve()
        hits = scan(app=root / "app", repo=root)
    else:
        hits = scan()
    if args.json:
        print(json.dumps({"count": len(hits), "hits": hits}, indent=2))
    elif args.count:
        print(len(hits))
    else:
        for h in hits:
            print(f"  {h['file']}:{h['line']}: {h['model']} looked up by id without the organisation filter")
        print(f"\n{len(hits)} lookup(s). Use Model.get_in_org(id); or `# unfiltered-id-lookup-ok: <reason>`.")
        print(len(hits))
    return 0


if __name__ == "__main__":
    sys.exit(main())
