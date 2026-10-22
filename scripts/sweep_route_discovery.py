#!/usr/bin/env python
"""Route discovery for the live sweep: fill every URL parameter of every GET route from the seeded database,
render each route once in process, and record which templates rendered it.

    set -a; . ~/verify/walk-<name>.env; . ~/verify/preview-demo.env; set +a
    python scripts/sweep_route_discovery.py --out routes-filled.json [--inventory docs/component-inventory.json]

What it does, in order:
  1. Reads app.url_map (every rule, not only the ones with a literal template).
  2. For each parameter, finds the table it names (``solution_id`` -> solutions, ``id`` after /applications/ ->
     applications, ``<layer>/<element_type>/<element_id>`` -> one real element of each type present) and takes real
     rows from the demo organisation. Up to three candidate addresses per route.
  3. Opens the first candidate that answers below 400 as the Architect (and records the Viewer's status), through the
     test client, capturing the templates that rendered (Flask's template_rendered signal).
  4. Compares the rendered templates with the component inventory's closure for that route and flags the templates that
     carry controls the inventory does not know about for that page ("uncovered").
  5. Writes the JSON the sweep reads, and a list of the routes that could not be filled, each with its reason.

Reads only. GET requests are issued; the walk database is throwaway. Never prints a password.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SKIP_RULE = re.compile(r"(^/static|/logout|/signout|/sign-out|/oauth|/callback)", re.I)
UNFILLABLE_NAMES = {
    "filename": "needs an uploaded file name; the demo company has no uploaded files",
    "path": "needs a file path under an upload folder",
    "rest": "catch-all path with no record behind it",
    "token": "needs a one-time secret token (invite, reset, share link) that does not exist in a seed",
    "address": "needs an external address",
}
ABBREV = {"app": "application", "wp": "work_package", "vp": "viewpoint", "cr": "change_request", "ri": "relationship",
          "sid": "solution", "doc": "document", "tmpl": "template", "org": "organization", "proc": "process"}


# Parameter names whose table cannot be guessed from the name. Kept short and explicit; everything else is ranked.
ALIAS_TABLE = {"application": "application_components", "app": "application_components", "vendor": "vendor_organizations",
               "programme": "strategic_initiatives", "initiative": "strategic_initiatives", "capability": "unified_capabilities",
               "work_package": "unified_work_packages", "wp": "unified_work_packages", "decision": "architecture_decisions",
               "solution": "solutions", "journey": "architecture_journeys", "element": "archimate_elements",
               "risk": "risks", "plateau": "plateaus", "gap": "gaps", "user": "users", "business_case": "business_cases",
               "value_stream": "value_streams", "canvas": "business_model_canvases"}


def snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def variants(stem: str) -> list[str]:
    stem = ABBREV.get(stem, stem)
    out = [stem, stem + "s", stem + "es"]
    if stem.endswith("y"):
        out.append(stem[:-1] + "ies")
    return out


class Registry:
    """Tables of the application with their model, organisation column and the demo organisation's row ids."""

    def __init__(self, db, org_id):
        self.db, self.org_id = db, org_id
        self.tables: dict[str, dict] = {}
        seen = set()
        for mapper in db.Model.registry.mappers:
            cls = mapper.class_
            t = getattr(cls, "__table__", None)
            if t is None or t.name in seen:
                continue
            seen.add(t.name)
            pk = [c.name for c in t.primary_key.columns]
            self.tables[t.name] = {"cls": cls.__name__, "table": t, "pk": pk[0] if len(pk) == 1 else None,
                                   "org": "organization_id" in t.columns}
        self._rows: dict[str, list] = {}

    def rows(self, table: str, column: str | None = None, limit: int = 4) -> list:
        key = f"{table}.{column or ''}"
        if key in self._rows:
            return self._rows[key]
        info = self.tables[table]
        t = info["table"]
        col = column or info["pk"]
        out = []
        if col and col in t.columns:
            from sqlalchemy import select
            q = select(t.c[col]).where(t.c[col].is_not(None))
            if info["org"]:
                q = q.where(t.c.organization_id == self.org_id)
            q = q.order_by(t.c[col]).limit(limit * 3)
            try:
                with self.db.engine.connect() as conn:
                    out = [r[0] for r in conn.execute(q)]
            except Exception:
                self.db.session.rollback()
                out = []
        # spread the picks: first, last, and one in the middle, so two candidates are not neighbours
        picks = []
        for i in (0, len(out) - 1, len(out) // 2):
            if 0 <= i < len(out) and out[i] not in picks:
                picks.append(out[i])
        self._rows[key] = picks[:limit]
        return self._rows[key]

    def rank(self, hints: list[str], arg_stem: str, tokens: set[str]) -> list[str]:
        """Tables whose names fit the parameter, best first. Only tables with rows for the organisation count."""
        for h in hints:
            hh = h.replace("-", "_")
            for key in (hh, hh[:-1] if hh.endswith("s") else hh, ABBREV.get(hh, hh)):
                alias = ALIAS_TABLE.get(key)
                if alias in self.tables:
                    return [alias] + [t for t in self._rank_inner(hints, tokens) if t != alias]
        return self._rank_inner(hints, tokens)

    def _rank_inner(self, hints, tokens):
        scored = []
        wanted = [v for h in hints for v in variants(h.replace("-", "_"))]
        for name, info in self.tables.items():
            score = 0
            cls = snake(info["cls"])
            for rank, w in enumerate(wanted):
                if name == w or cls == w:
                    score = max(score, 100 - rank)
                elif name.endswith("_" + w) or cls.endswith("_" + w):
                    score = max(score, 55 - rank)
                elif name.startswith(w + "_") or cls.startswith(w + "_"):
                    score = max(score, 42 - rank)
                elif ("_" + w + "_") in ("_" + name + "_"):
                    score = max(score, 30 - rank)
            if not score:
                continue
            score += sum(5 for tok in tokens if tok and tok in name)
            if info["org"]:
                score += 3
            scored.append((score, name))
        scored.sort(key=lambda s: (-s[0], len(s[1]), s[1]))
        return [n for _, n in scored]


def static_segments(rule) -> list[str]:
    return [seg for seg in rule.rule.split("/") if seg and not seg.startswith("<")]


def preceding_segment(rule, arg: str) -> str | None:
    parts = [p for p in rule.rule.split("/") if p]
    for i, p in enumerate(parts):
        if re.fullmatch(r"<(?:[^:>]+:)?%s>" % re.escape(arg), p) and i > 0 and not parts[i - 1].startswith("<"):
            return parts[i - 1]
    return None


def build_path(rule, values: dict) -> str:
    from werkzeug.routing import BuildError  # noqa: F401
    path = rule.rule
    for arg, v in values.items():
        path = re.sub(r"<(?:[^:>]+(?:\([^)]*\))?:)?%s>" % re.escape(arg), lambda m, v=v: str(v), path)
    return path


def converter_name(rule, arg):
    return type(rule._converters[arg]).__name__


def candidates_for(rule, reg: Registry, elements: dict, rel_types: list, el_types: list):
    """Up to three {arg: value} dicts for the rule, or (None, reason)."""
    args = sorted(a for a in rule.arguments if a in rule._converters)
    if not args:
        return [{}], None
    tokens = set(re.split(r"[^a-z]+", rule.rule.lower())) | set(re.split(r"[^a-z]+", rule.endpoint.lower()))
    tokens.discard("")
    per_arg: dict[str, list] = {}
    reasons = []
    handled_element = False
    if {"element_id"} <= set(args) and ({"layer"} & set(args) or {"element_type"} & set(args)):
        handled_element = True
        cands = []
        for typ, el in elements.items():
            cands.append({"element_id": el["id"], "element_type": typ, "layer": el["layer"].lower(), "archimate_type": typ})
        per_arg["__element__"] = cands
    for arg in args:
        if handled_element and arg in ("element_id", "element_type", "layer", "archimate_type"):
            continue
        conv = converter_name(rule, arg)
        if conv == "AnyConverter":
            m = re.search(r"any\(([^)]*)\)", rule.rule)
            opts = [o.strip().strip("'\"") for o in m.group(1).split(",")] if m else []
            per_arg[arg] = [{arg: o} for o in opts[:3]] or None
            if not per_arg[arg]:
                reasons.append(f"{arg}: any() with no options")
            continue
        if arg in UNFILLABLE_NAMES:
            reasons.append(f"{arg}: {UNFILLABLE_NAMES[arg]}")
            continue
        if arg in ("element_type", "archimate_type") and el_types:
            per_arg[arg] = [{arg: t} for t in el_types[:3]]
            continue
        if arg in ("source_type", "target_type") and el_types:
            per_arg[arg] = [{arg: t} for t in el_types[:3]]
            continue
        if arg == "relationship_type" and rel_types:
            per_arg[arg] = [{arg: t} for t in rel_types[:3]]
            continue
        stem = re.sub(r"_id$", "", arg)
        hints = []
        if stem not in ("", "id"):
            hints.append(stem)
        prev = preceding_segment(rule, arg)
        if prev:
            hints.append(prev)
        if arg == "id" or stem == "":
            hints = [prev] if prev else static_segments(rule)[-1:]
        is_int = conv == "IntegerConverter"
        ranked = reg.rank(hints, stem, tokens) if hints else []
        options = []
        for table in ranked[:4]:
            if is_int:
                for v in reg.rows(table):
                    options.append({arg: v, "_table": table})
            else:
                for col in (arg, "slug", "code", "key", "name"):
                    if col in reg.tables[table]["table"].columns:
                        for v in reg.rows(table, col):
                            options.append({arg: v, "_table": table})
                        break
        # interleave so candidate k varies the table before it varies the row
        if not options:
            if ranked:
                reasons.append(f"{arg}: table {ranked[0]} exists but holds no row for the demo organisation")
            else:
                reasons.append(f"{arg}: no table matches '{'/'.join(hints) or arg}'")
        else:
            per_arg[arg] = options[:6]
    if reasons or not per_arg and args:
        return None, "; ".join(reasons) or "no parameter could be resolved"
    out = []
    for k in range(3):
        values = {}
        tables = []
        for key, opts in per_arg.items():
            o = opts[min(k, len(opts) - 1)]
            if k >= len(opts) and k > 0 and len(opts) < 2:
                pass
            for kk, vv in o.items():
                if kk == "_table":
                    tables.append(vv)
                else:
                    values[kk] = vv
        if values and values not in out:
            out.append(values)
    return out, None


class TemplateRecorder:
    def __init__(self):
        self.names = []

    def __call__(self, sender, template, context, **extra):
        self.names.append(template.name or "")


def alarm_handler(signum, frame):
    raise TimeoutError("took longer than 25 seconds")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--inventory", default=str(ROOT / "docs" / "component-inventory.json"))
    ap.add_argument("--architect", default="sage.itops@lantern-quay.example.com")
    ap.add_argument("--viewer", default="casey.inventory@lantern-quay.example.com")
    ap.add_argument("--password-env", default="DEMO_USER_PASSWORD")
    ap.add_argument("--no-render", action="store_true", help="only fill the parameters; do not open the routes")
    ap.add_argument("--only", default="")
    ap.add_argument("--max-routes", type=int, default=0)
    args = ap.parse_args(argv)

    from flask import template_rendered
    from app import create_app, db
    from app.models.organization import Organization
    from app.commands.seed_demo_company import _ORG_SLUG

    app = create_app(os.environ.get("FLASK_CONFIG", "testing"))
    app.config["PROPAGATE_EXCEPTIONS"] = False
    app.config["TESTING"] = False
    app.config["WTF_CSRF_ENABLED"] = False
    password = os.environ[args.password_env]

    _ctx = app.app_context()
    _ctx.push()
    if True:
        org = Organization.query.filter_by(slug=_ORG_SLUG).first()
        if org is None:
            raise SystemExit("the demo company is not seeded in this database")
        org_id = org.id
        reg = Registry(db, org_id)
        # one real element of every type present, for the <layer>/<element_type>/<element_id> pages
        from sqlalchemy import text
        elements = {}
        for row in db.session.execute(text(
                "select min(id), type, min(layer) from archimate_elements where organization_id=:o and type is not null group by type order by type"), {"o": org_id}):
            elements[row[1]] = {"id": row[0], "layer": row[2] or "business"}
        el_types = list(elements)
        rel_types = [r[0] for r in db.session.execute(text(
            "select distinct type from archimate_relationships where organization_id=:o and type is not null order by 1 limit 6"), {"o": org_id})]

    inv = json.loads(Path(args.inventory).read_text(encoding="utf-8")) if Path(args.inventory).exists() else None
    inv_by_key, inv_templates, ctrl_templates = {}, [], set()
    if inv:
        for r in inv["routes"]:
            inv_by_key[(r["rule"], r["endpoint"])] = r
        inv_templates = list(inv["templates"]) if isinstance(inv["templates"], list) else list(inv["templates"].keys())
        ctrl_templates = {c["template"] for c in inv["controls"]}

    def inventory_path(jinja_name: str):
        for t in inv_templates:
            p = t["path"] if isinstance(t, dict) else t
            if p.endswith("/templates/" + jinja_name) or p == jinja_name:
                return p
        return None

    template_dirs = [ROOT / "app" / "templates"] + sorted((ROOT / "app" / "modules").glob("*/templates"))

    def template_has_controls(jinja_name: str) -> bool:
        p = inventory_path(jinja_name)
        if p and p in ctrl_templates:
            return True
        for d in template_dirs:
            f = d / jinja_name
            if f.exists():
                src = f.read_text(encoding="utf-8", errors="replace")
                return bool(re.search(r"<button\b|<form\b|role=\"button\"|data-modal-open|@click|x-on:click", src))
        return False

    rules = [r for r in app.url_map.iter_rules() if "GET" in r.methods and r.endpoint != "static"]
    rules.sort(key=lambda r: (r.rule, r.endpoint))
    if args.only:
        rules = [r for r in rules if args.only in r.rule]
    if args.max_routes:
        rules = rules[: args.max_routes]

    plans = {id(r): (None, "skipped") if SKIP_RULE.search(r.rule) else candidates_for(r, reg, elements, rel_types, el_types) for r in rules}
    _ctx.pop()  # requests must not share an application context, or one person's identity leaks into the next request

    def make_client(email):
        c = app.test_client()
        resp = c.post("/account/login", data={"email": email, "password": password}, follow_redirects=False)
        chk = c.get("/dashboard/overview", follow_redirects=False)
        if chk.status_code >= 300 and "/account/login" in (chk.headers.get("Location") or ""):
            raise SystemExit(f"could not sign in as {email} (login answered {resp.status_code})")
        return c

    clients = {} if args.no_render else {"architect": make_client(args.architect), "viewer": make_client(args.viewer)}
    signal.signal(signal.SIGALRM, alarm_handler)

    def fetch(client, path):
        rec = TemplateRecorder()
        template_rendered.connect(rec, app)
        t0 = time.time()
        signal.alarm(25)
        try:
            resp = client.get(path, follow_redirects=False)
            status, loc = resp.status_code, resp.headers.get("Location")
            size = len(resp.get_data())
            ctype = resp.headers.get("Content-Type", "")
            err = None
        except Exception as exc:  # noqa: BLE001
            status, loc, size, ctype, err = 500, None, 0, "", f"{type(exc).__name__}: {str(exc)[:160]}"
        finally:
            signal.alarm(0)
            template_rendered.disconnect(rec, app)
        with app.app_context():
            db.session.rollback()
        return {"status": status, "location": loc, "templates": list(dict.fromkeys(rec.names)), "bytes": size,
                "content_type": ctype, "error": err, "seconds": round(time.time() - t0, 2)}

    out_routes, unfilled, skipped = [], [], []
    for i, rule in enumerate(rules):
        base = {"rule": rule.rule, "endpoint": rule.endpoint, "args": sorted(a for a in rule.arguments if a in rule._converters),
                "blueprint": rule.endpoint.split(".")[0] if "." in rule.endpoint else ""}
        if SKIP_RULE.search(rule.rule):
            base["skipped"] = "signs out, authorises or is a callback: not opened"
            skipped.append(base)
            out_routes.append(base)
            continue
        cands, reason = plans[id(rule)]
        if cands is None:
            base["unfilled"] = reason
            unfilled.append(base)
            out_routes.append(base)
            continue
        base["candidates"] = [build_path(rule, v) for v in cands]
        base["fills"] = cands
        iv = inv_by_key.get((rule.rule, rule.endpoint))
        base["in_inventory"] = bool(iv)
        closure = set(iv["closure"]) if iv else set()
        if not args.no_render:
            tried = []
            chosen = None
            for path in base["candidates"]:
                got = fetch(clients["architect"], path)
                tried.append({"path": path, "status": got["status"]})
                chosen = (path, got)
                if got["status"] < 400:
                    break
            path, got = chosen
            base.update(path=path, tried=tried, status=got["status"], location=got["location"],
                        content_type=got["content_type"], error=got["error"], seconds=got["seconds"],
                        templates=got["templates"])
            base["viewer_status"] = fetch(clients["viewer"], path)["status"]
            rendered_paths = [inventory_path(t) or t for t in got["templates"]]
            base["templates_repo_paths"] = rendered_paths
            base["uncovered_templates"] = [p for p, t in zip(rendered_paths, got["templates"])
                                           if p not in closure and template_has_controls(t) and not t.endswith(("_macros.html",)) and "/errors/" not in p] if inv else []
            base["template"] = next((p for p in rendered_paths if "/components/" not in p and "/layouts/" not in p and "/partials/" not in p), rendered_paths[0] if rendered_paths else None)
        else:
            base["path"] = base["candidates"][0]
        out_routes.append(base)
        if (i + 1) % 100 == 0:
            print(f"{i + 1}/{len(rules)} routes", flush=True)

    opened = [r for r in out_routes if r.get("status") is not None and r["status"] < 400]
    redirected = [r for r in out_routes if r.get("status") is not None and 300 <= r["status"] < 400]
    summary = {
        "routes_total": len(out_routes), "filled_or_literal": len([r for r in out_routes if "candidates" in r]),
        "unfilled": len(unfilled), "skipped": len(skipped), "opened_below_400": len(opened), "of_which_redirects": len(redirected),
        "answered_404": len([r for r in out_routes if r.get("status") == 404]),
        "answered_500": len([r for r in out_routes if r.get("status", 0) >= 500]),
        "answered_403_for_architect": len([r for r in out_routes if r.get("status") == 403]),
        "routes_with_uncovered_templates": len([r for r in out_routes if r.get("uncovered_templates")]),
        "element_types_present": el_types,
    }
    Path(args.out).write_text(json.dumps({"summary": summary, "routes": out_routes}, indent=1), encoding="utf-8")
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
