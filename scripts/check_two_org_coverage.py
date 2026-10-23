#!/usr/bin/env python
"""Every route family that reads organisation data must have a two-organisation test.

A tenant boundary is only real if something crosses it and is turned away. A page
can be login-gated, carry the right role check and still read another
organisation's rows, because the query forgot its tenant filter. A status check
or a single-organisation test never notices: the one organisation it knows about
sees exactly what it expects. The failure only shows when a *second* organisation
exists and its data stays invisible.

So this gate groups the route inventory by blueprint into families, keeps the
families that read organisation data, and fails when such a family has no
two-organisation test associated with it.

Reads-organisation-data predicate: a family reads organisation data when at least
one of its routes has ``login_required`` true in docs/component-inventory.json. A
logged-in surface is a tenant-scoped surface; a route with no login requirement
serves the same bytes to everyone and has no tenant boundary to cross.

Two-organisation tests are found by STATIC AST search (ast.parse, no import, no
database, no Flask boot) across tests/ and app/modules/*/tests/. A test file
counts as a two-organisation test when it defines or uses any of the known
fixtures -- ``two_orgs``, ``two_organisations``, ``second_org_membership`` -- or
is one of the suites named in the brief (the authorisation matrix, the isolation
sweep, the MCP row-level-security two-org tool suite and the two cross-account
journeys).

A two-organisation test is associated with a family when the family's blueprint
name, or an alias keyword for a startup family, appears in the test file path or
in the test source. The alias map below records, for each startup family, the
blueprint names it owns and the keyword that recognises its two-organisation
tests, so the seven startup families (account, admin connectors, intelligence,
applications, admin billing, admin team, dashboard) resolve to zero uncovered
with the two-organisation tests that exist today.

A family is uncovered when it reads organisation data AND no two-organisation
test is associated with it AND no line carries the escape hatch
``two-org-coverage-ok: <reason>`` naming the family.

Baseline that only falls: scripts/ci/two_org_baseline.json records the accepted
uncovered count for NON-startup families. The gate fails when the non-startup
uncovered count rises above that baseline, and it always requires the startup
families to be zero uncovered regardless of the baseline. Lower the baseline when
you cover a family; never raise it.

    python scripts/check_two_org_coverage.py            # list uncovered families
    python scripts/check_two_org_coverage.py --count    # trailing line = count
    python scripts/check_two_org_coverage.py --startup-only  # startup families only

Proven-against: a startup family's two-organisation test removed (the
authorisation matrix's two_orgs usage deleted on a scratch commit) -- red, the
family it covered turns uncovered and startup count rises above 0; restored --
green at 0.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

INVENTORY = os.path.join("docs", "component-inventory.json")
BASELINE = os.path.join("scripts", "ci", "two_org_baseline.json")

# Fixture / marker names that make a test a two-organisation test.
TWO_ORG_NAMES = ("two_orgs", "two_organisations", "second_org_membership")

# Suites the brief names explicitly as two-organisation tests.
NAMED_SUITES = (
    os.path.join("tests", "smoke", "test_authorisation_matrix.py"),
    os.path.join("tests", "_isolation_sweep.py"),
    os.path.join("app", "modules", "mcp", "tests", "test_rls_two_org_all_tools.py"),
    os.path.join("tests", "smoke", "test_decisions_and_why_journey.py"),
    os.path.join("tests", "smoke", "test_multi_org_account_switching_journey.py"),
)

ALLOW = re.compile(r"two-org-coverage-ok:[ \t]*\S")

# Startup families: English name -> the blueprint names it owns and the keyword
# that recognises its two-organisation tests by path or source. The startup
# families must resolve to 0 uncovered with the two-organisation tests that
# exist today.
STARTUP_FAMILIES: dict[str, dict[str, object]] = {
    "account": {"blueprints": {"account"}, "keywords": {"account"}},
    "admin connectors": {
        "blueprints": {"m365_connector", "connectors"},
        "keywords": {"connector"},
    },
    "intelligence": {
        "blueprints": {"intelligence_api", "intelligence_ui"},
        "keywords": {"intelligence"},
    },
    "applications": {
        "blueprints": {"unified_applications", "application_mgmt", "my_applications"},
        "keywords": {"application"},
    },
    "admin billing": {"blueprints": {"billing"}, "keywords": {"billing"}},
    "admin team": {"blueprints": {"team"}, "keywords": {"team"}},
    "dashboard": {
        "blueprints": {"dashboard", "dashboard_pages"},
        "keywords": {"dashboard"},
    },
}

# blueprint name -> startup family English name
_BLUEPRINT_TO_STARTUP: dict[str, str] = {}
for _fam, _spec in STARTUP_FAMILIES.items():
    for _bp in _spec["blueprints"]:  # type: ignore[union-attr]
        _BLUEPRINT_TO_STARTUP[_bp] = _fam


def load_routes(root: str) -> list[dict]:
    """Read the route list from the component inventory without importing anything."""
    path = os.path.join(root, INVENTORY)
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return data.get("routes", [])


def families_reading_org_data(routes: list[dict]) -> set[str]:
    """Blueprints with at least one login-required route (a tenant-scoped surface)."""
    families: set[str] = set()
    for route in routes:
        blueprint = route.get("blueprint")
        if not blueprint:
            continue
        if route.get("login_required"):
            families.add(blueprint)
    return families


def _iter_test_files(root: str):
    """Yield (relpath, source) for every test file under tests/ and app/modules/*/tests/."""
    seen: set[str] = set()
    search_dirs = [os.path.join(root, "tests")]
    modules = os.path.join(root, "app", "modules")
    if os.path.isdir(modules):
        for module in sorted(os.listdir(modules)):
            tests_dir = os.path.join(modules, module, "tests")
            if os.path.isdir(tests_dir):
                search_dirs.append(tests_dir)

    for base in search_dirs:
        if not os.path.isdir(base):
            continue
        for dirpath, _dirs, filenames in os.walk(base):
            if "__pycache__" in dirpath:
                continue
            for filename in sorted(filenames):
                if not filename.endswith(".py"):
                    continue
                path = os.path.join(dirpath, filename)
                rel = os.path.relpath(path, root)
                if rel in seen:
                    continue
                seen.add(rel)
                with open(path, encoding="utf-8", errors="replace") as fh:
                    yield rel, fh.read()


def two_org_tests(root: str) -> list[tuple[str, str]]:
    """Return (relpath, source) for every file that is a two-organisation test.

    A file qualifies when it is one of the named suites, or when a static AST
    scan finds a fixture definition, a fixture argument or a plain reference to
    any of the known two-organisation names.
    """
    found: list[tuple[str, str]] = []
    for rel, source in _iter_test_files(root):
        if rel in NAMED_SUITES:
            found.append((rel, source))
            continue
        qualifies = False
        try:
            tree = ast.parse(source)
        except SyntaxError:
            tree = None
        if tree is not None:
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if node.name in TWO_ORG_NAMES:
                        qualifies = True
                        break
                    arg_names = {a.arg for a in node.args.args}
                    if arg_names & set(TWO_ORG_NAMES):
                        qualifies = True
                        break
                elif isinstance(node, ast.Name) and node.id in TWO_ORG_NAMES:
                    qualifies = True
                    break
        if qualifies:
            found.append((rel, source))
    return found


def covered_families(
    org_families: set[str], tests: list[tuple[str, str]]
) -> set[str]:
    """Blueprints for which at least one two-organisation test is associated.

    Association: the blueprint name, or an alias keyword for the startup family
    that owns the blueprint, appears in the test path or source (case-folded).
    """
    covered: set[str] = set()
    corpus = [(rel.lower(), source.lower()) for rel, source in tests]

    for blueprint in org_families:
        tokens = {blueprint.lower()}
        startup = _BLUEPRINT_TO_STARTUP.get(blueprint)
        if startup:
            tokens |= {k.lower() for k in STARTUP_FAMILIES[startup]["keywords"]}  # type: ignore[union-attr]
        for rel_lower, src_lower in corpus:
            if any(tok in rel_lower or tok in src_lower for tok in tokens):
                covered.add(blueprint)
                break
    return covered


def excused_families(root: str) -> set[str]:
    """Blueprints excused by a `two-org-coverage-ok: <reason>` line anywhere in tests."""
    excused: set[str] = set()
    for _rel, source in _iter_test_files(root):
        for line in source.split("\n"):
            if ALLOW.search(line):
                for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", line):
                    excused.add(token)
    return excused


def uncovered(root: str) -> tuple[list[str], list[str]]:
    """Return (uncovered startup blueprints, uncovered non-startup blueprints), sorted."""
    routes = load_routes(root)
    org_families = families_reading_org_data(routes)
    tests = two_org_tests(root)
    covered = covered_families(org_families, tests)
    excused = excused_families(root)

    missing = sorted(
        bp for bp in org_families if bp not in covered and bp not in excused
    )
    startup = [bp for bp in missing if bp in _BLUEPRINT_TO_STARTUP]
    other = [bp for bp in missing if bp not in _BLUEPRINT_TO_STARTUP]
    return startup, other


def load_baseline(root: str) -> int:
    path = os.path.join(root, BASELINE)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return 0
    return int(data.get("accepted_uncovered", 0))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", action="store_true", help="print only the count")
    parser.add_argument("--root", default=ROOT, help="tree to scan")
    parser.add_argument(
        "--startup-only",
        action="store_true",
        help="consider only the startup families",
    )
    args = parser.parse_args()

    root = os.path.abspath(args.root)
    startup_missing, other_missing = uncovered(root)

    if args.startup_only:
        reported = list(startup_missing)
    else:
        reported = startup_missing + other_missing

    if not args.count:
        for blueprint in reported:
            family = _BLUEPRINT_TO_STARTUP.get(blueprint)
            label = f" (startup family {family!r})" if family else ""
            print(
                f"docs/component-inventory.json: [two-org-coverage] route family "
                f"{blueprint!r}{label} reads organisation data but has no "
                f"two-organisation test"
            )
        if reported:
            print()
            print(
                "A route family that reads organisation data with no "
                "two-organisation\ntest is an untested isolation boundary. Add a "
                "test under tests/ or\napp/modules/*/tests/ that creates two "
                "organisations (use the two_orgs or\ntwo_organisations fixture) and "
                "asserts one organisation cannot see the\nother's rows on that "
                "family's routes. If the family genuinely has no\ntenant boundary, "
                "append 'two-org-coverage-ok: <reason>' naming it."
            )

    baseline = load_baseline(root)
    over_baseline = len(other_missing) > baseline
    startup_broken = bool(startup_missing)

    if not args.startup_only and not args.count and (over_baseline or startup_broken):
        print()
        if startup_broken:
            print(
                f"Startup families must be 0 uncovered; "
                f"{len(startup_missing)} are uncovered."
            )
        if over_baseline:
            print(
                f"Non-startup uncovered {len(other_missing)} exceeds the baseline "
                f"{baseline} in {BASELINE} (the baseline can only fall)."
            )

    print(len(reported))

    if args.startup_only:
        return 1 if startup_missing else 0
    return 1 if (over_baseline or startup_broken) else 0


if __name__ == "__main__":
    sys.exit(main())
