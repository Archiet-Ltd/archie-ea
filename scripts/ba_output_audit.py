"""Audit Iain's 12 Business Architecture outputs against what the app can serve.

For each output: does a route exist, is its blueprint actually registered, and is
there any navigation path to it? A route that exists but is unreachable or
unlinked is why a competent architect concluded the feature was missing.

    python scripts/ba_output_audit.py
    python scripts/ba_output_audit.py --count   # outputs with routes but no sidebar link
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REPO = Path(__file__).resolve().parent.parent

# (label, regex matching the URL path of a route that would serve this output)
OUTPUTS = [
    ("1. Capability maps", r"capability-map|capability_map"),
    ("2. Capability maturity", r"capability-maturity"),
    ("3. Value stream maps", r"value-stream|value_stream"),
    ("4. Org & ownership views", r"ownership|org-chart|organisation|organization-view"),
    ("5. Information/data maps", r"data-lineage|information-model|data-map|business-information"),
    ("6. Strategy-to-execution", r"strategy-map|strategy-to-execution|motivation|strategy_map"),
    ("7. Stakeholder maps", r"stakeholder"),
    ("8. Initiative/project alignment", r"work-package|workpackage|initiative|programme|portfolio-roadmap"),
    ("9. KPI/metric dashboards", r"/metric|kpi|measure"),
    ("10. Products & services", r"product-service|products|business-service|service-catalog"),
    ("11. Policies & governance", r"policy|policies|governance"),
    ("12. Gap analysis & roadmaps", r"gap-analysis|gap_analysis|roadmap"),
]

ROUTE_RE = re.compile(r"@(\w+)\.route\(\s*[\"']([^\"']+)[\"']")
# `bp = Blueprint("name", __name__ ...)` — the variable is what decorates the
# route, the first argument is what the endpoint name is built from.
BLUEPRINT_RE = re.compile(r"(\w+)\s*=\s*Blueprint\(\s*[\"']([\w.]+)[\"']")
# a route decorator (possibly stacked with others) followed by its function
ROUTE_FUNC_RE = re.compile(
    r"@(\w+)\.route\(\s*[\"']([^\"']+)[\"'][^\n]*\n(?:\s*@[^\n]*\n)*\s*def\s+(\w+)"
)


def collect_routes() -> list[tuple[str, str, Path]]:
    out = []
    for py in REPO.joinpath("app").rglob("*.py"):
        if "__pycache__" in str(py):
            continue
        try:
            src = py.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for bp, path in ROUTE_RE.findall(src):
            out.append((bp, path, py))
    return out


def registered_blueprint_vars() -> set[str]:
    """Blueprint *variable* names that appear in a register_blueprint call anywhere."""
    names: set[str] = set()
    for py in REPO.joinpath("app").rglob("*.py"):
        if "__pycache__" in str(py):
            continue
        try:
            src = py.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in re.finditer(r"register_blueprint\(\s*([A-Za-z_][\w.]*)", src):
            names.add(m.group(1).split(".")[-1])
        for m in re.finditer(r"fromlist=\[[\"'](\w+)[\"']\]", src):
            names.add(m.group(1))
        for m in re.finditer(r"\(\s*[\"'][\w.]+[\"']\s*,\s*[\"'](\w+)[\"']\s*,", src):
            names.add(m.group(1))
    return names


def endpoint_urls() -> dict[str, str]:
    """Map Flask endpoint name -> URL rule, resolved statically.

    `blueprint_name.function_name` is how Flask builds an endpoint, so the
    blueprint's *first constructor argument* (not its variable name) plus the
    decorated function name is enough to resolve a sidebar link's endpoint back
    to the URL path it serves — without booting the app.
    """
    urls: dict[str, str] = {}
    for py in REPO.joinpath("app").rglob("*.py"):
        if "__pycache__" in str(py):
            continue
        try:
            src = py.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        var_to_name = dict(BLUEPRINT_RE.findall(src))
        for bp_var, path, func in ROUTE_FUNC_RE.findall(src):
            name = var_to_name.get(bp_var)
            if name:
                urls.setdefault(f"{name}.{func}", path)
    return urls


def sidebar_blob() -> str:
    """Everything reachable from any persona's sidebar, as one searchable blob.

    Read from SIDEBAR_ZONES in app/utils/role_access.py — the real mechanism the
    shell renders — by importing it, because the zones are *built* by
    `_build_zones()` and so cannot be grepped out of the source. Each link
    contributes its label, its endpoint name and the URL path that endpoint
    serves, since an output can be recognisable in any of the three.
    """
    from app.utils.role_access import SIDEBAR_ZONES

    urls = endpoint_urls()
    parts: list[str] = []
    for zones in SIDEBAR_ZONES.values():
        for zone in zones:
            for link in zone.get("links", []):
                endpoint = link.get("endpoint", "")
                parts.append(f"{link.get('label', '')} {endpoint} {urls.get(endpoint, '')}")
    return "\n".join(parts)


def dark_blob() -> str:
    """The label, endpoint and resolved URL for every entry
    app/modules/modules_directory/routes.py's `_DARK` names, in the same
    shape `sidebar_blob()` emits.

    A page this audit's pattern matches is not a navigation gap merely
    because `unreachable_count()` cannot find its own string in a persona's
    sidebar -- a broad output pattern like output 5's also matches that
    page's own `/create` or `/api/...` sibling routes, which are real routes
    too and were never individually in any sidebar either, dark or not. What
    changed for a page in `_DARK` is a written, deliberate product decision
    to stop advertising it, not an accidental navigation regression; this
    blob is unioned into the coverage check specifically so that decision
    reads as accounted for, not as a fresh S-11-style defect. The `IN NAV`
    column in `main()`'s table intentionally does NOT read this blob: it
    reports the plain, current fact (in a sidebar or not), which for a dark
    page is genuinely "NO".
    """
    from app.modules.modules_directory.routes import _DARK, _MORE_TOOLS

    urls = endpoint_urls()
    labels = {endpoint: label for label, endpoint, _icon in _MORE_TOOLS}
    parts = [
        f"{labels.get(endpoint, '')} {endpoint} {urls.get(endpoint, '')}"
        for endpoint in _DARK
    ]
    return "\n".join(parts)


def unreachable_count() -> int:
    """Outputs that have a route but appear in no persona's sidebar and are
    not accounted for by `_DARK`."""
    routes = collect_routes()
    nav = sidebar_blob() + "\n" + dark_blob()
    missing = 0
    for _label, pat in OUTPUTS:
        rx = re.compile(pat, re.I)
        if any(rx.search(path) for _bp, path, _f in routes) and not rx.search(nav):
            missing += 1
    return missing


def main() -> int:
    if "--count" in sys.argv:
        print(unreachable_count())
        return 0

    routes = collect_routes()
    registered = registered_blueprint_vars()
    # Same source as unreachable_count(): resolved SIDEBAR_ZONES, not a text grep.
    nav = sidebar_blob()

    print(f"scanned {len(routes)} routes; {len(registered)} registered blueprint names\n")
    print(f"{'OUTPUT':34} {'ROUTES':>6} {'REACHABLE':>10} {'IN NAV':>7}")
    print("-" * 62)

    for label, pat in OUTPUTS:
        rx = re.compile(pat, re.I)
        hits = [(bp, path) for bp, path, _ in routes if rx.search(path)]
        bps = {bp for bp, _ in hits}
        # a route is reachable if its blueprint var looks registered
        reachable = sum(1 for bp in bps if bp in registered or f"{bp}_bp" in registered)
        in_nav = "yes" if rx.search(nav) else "NO"
        print(f"{label:34} {len(hits):>6} {reachable:>4}/{len(bps):<5} {in_nav:>7}")

    print("\nlegend: REACHABLE = blueprints registered / blueprints owning those routes")
    print("        IN NAV    = the output is referenced anywhere nav is built")

    coverage = sidebar_blob() + "\n" + dark_blob()
    orphans = [
        label for label, pat in OUTPUTS
        if any(re.search(pat, path, re.I) for _bp, path, _f in routes)
        and not re.search(pat, coverage, re.I)
    ]
    print(f"\nnav-coverage gate: {len(orphans)} outputs have routes but no sidebar link")
    for label in orphans:
        print(f"  - {label}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
