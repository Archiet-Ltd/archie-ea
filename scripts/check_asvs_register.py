#!/usr/bin/env python
"""Every claim in the ASVS register must be backed by a test that actually exists.

A security register is only worth the trust people put in it. A row that says
"access control for the account family is covered by
tests/smoke/test_authorisation_matrix.py::test_account_switch_organization_authorisation"
is a promise; if that test is renamed, deleted or never existed, the register
keeps asserting a control that nobody runs. The usual way this rots is quiet: a
refactor renames a test, the suite still goes green because the old name is gone,
and the register goes on naming a node id that resolves to nothing.

So this gate reads docs/security/asvs-l2.yml and, for every applicable row,
proves the evidence it cites is real:

  - Evidence that looks like a test node id (``path/to/test.py::test_name`` or
    ``path/to/test.py::Class::test_name``) is resolved to a file and AST-parsed
    (ast.parse, no import, no database, no Flask boot). The named function or
    method must exist in that file.
  - Evidence that is a control description (free prose with no ``::``) passes
    only when it names an existing FILE (a path with a slash or a recognised
    extension). A directory on its own does not resolve -- the row must also
    name a file within it -- and a bare word never resolves even if a same-named
    path happens to exist, so ordinary prose can never pass by coincidence.
    Evidence that resolves to no test node or file -- a path that is not on
    disk, a bare directory, or prose naming no file at all -- fails with the row
    id and the unresolved text, so a control can never pass silently.
  - A row with ``status: fail`` must name a brief by id (a token matching
    ``T-[a-z]+-\\d+``, e.g. T-qa-13) so an open gap always points at the work
    that will close it. A fail row with no brief id is malformed.
  - A row with ``applies: false`` records why the requirement does not apply and
    needs no test evidence.
  - Every startup family (account, admin connectors, intelligence,
    applications, admin billing, admin team, dashboard -- the same
    STARTUP_FAMILIES map check_two_org_coverage uses) must carry at least one
    V4 (access control) row whose evidence is a GENUINE two-organisation test:
    the cited node resolves, its file builds a second organisation, and a
    two-organisation unit in that file drives the family's own routes. A startup
    family present in the register with no such V4 row fails the gate.

The register file is YAML, but this gate must run in the static-gates job and
locally without depending on PyYAML being importable: FEAT-level experience in
this repository is that verify.py spawns each gate as a child process that does
not reliably inherit PYTHONPATH, so a gate that hard-requires PyYAML fails under
`verify.py --tag static` even when PyYAML is installed elsewhere. To stay robust
the loader tries ``import yaml`` first and, only if that fails, falls back to a
small built-in parser restricted to the flat shape this register uses (a
top-level ``rows:`` list of mappings whose values are scalars or a block list of
strings under ``evidence:``). The register is authored in exactly that shape, so
both loaders read it identically.

The gate is must-be-0: one problem line per missing test node id, per piece of
evidence that resolves to no file or directory, or per malformed fail row, then
a trailing line that is the integer count.

    python scripts/check_asvs_register.py            # list problems
    python scripts/check_asvs_register.py --count    # trailing line = count
    python scripts/check_asvs_register.py --root DIR # scan another tree

Proven-against: a referenced test renamed (e.g.
test_account_switch_organization_authorisation removed on a scratch commit) --
red, the row that cites it is reported and the count rises; the test restored --
green at 0.
"""
from __future__ import annotations

import argparse
import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# One source of truth for the startup family -> blueprint map and the
# two-organisation signal: both live in check_two_org_coverage. This gate shares
# them rather than defining a second, divergent copy (reuse-register principle).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_two_org_coverage as two_org  # noqa: E402

REGISTER = os.path.join("docs", "security", "asvs-l2.yml")

# A brief id token, e.g. T-qa-13. A status:fail row must name one.
BRIEF_ID = re.compile(r"T-[a-z]+-\d+")

# A test node id: a .py file path, then :: and a node path. The node path may
# carry a parametrisation suffix on its final segment, e.g.
# ``test_name[param-1]`` or ``TestClass::test_name[case]``; the suffix is
# stripped before the base node is resolved against the file's real tests.
NODE_ID = re.compile(r"^(?P<path>[^\s:]+\.py)::(?P<rest>[^\s]+)$")

# A trailing pytest parametrisation suffix on a node's final segment.
_PARAM_SUFFIX = re.compile(r"\[.*\]$")


# --------------------------------------------------------------------------- #
# Register loading: PyYAML when available, a minimal fallback when not.
# --------------------------------------------------------------------------- #
def _fallback_load(text: str) -> dict:
    """Parse the flat register shape without PyYAML.

    Handles exactly what the register uses: a top-level ``rows:`` key whose
    value is a list of mappings, each mapping a few scalar fields plus an
    ``evidence:`` block list of strings. Indentation is two spaces per level.
    Comments (``#``) and blank lines are ignored.
    """
    rows: list[dict] = []
    current: dict | None = None
    evidence: list[str] | None = None
    row_indent: int | None = None

    def _scalar(value: str):
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            return value[1:-1]
        low = value.lower()
        if low == "true":
            return True
        if low == "false":
            return False
        return value

    for raw in text.split("\n"):
        line = raw.rstrip()
        if not line.strip():
            continue
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))

        # Top-level 'rows:' header.
        if indent == 0 and stripped == "rows:":
            continue

        # A new row begins with '- ' at the row indent (the first '- ' sets it).
        is_row_start = stripped.startswith("- ") and (
            row_indent is None or indent <= row_indent
        )
        if is_row_start:
            row_indent = indent
            item = stripped[2:].strip()
            current = {}
            rows.append(current)
            evidence = None
            key, sep, value = item.partition(":")
            key = key.strip()
            if key == "evidence":
                evidence = []
                current["evidence"] = evidence
            elif sep:
                current[key] = _scalar(value)
            continue

        # A deeper-indented '- item' is an element of the current block list.
        if stripped.startswith("- ") and evidence is not None:
            evidence.append(_scalar(stripped[2:].strip()))
            continue
        if stripped.startswith("-") and not stripped.startswith("- ") and (
            evidence is not None
        ):
            evidence.append(_scalar(stripped[1:].strip()))
            continue

        # A plain 'key: value' line belonging to the current row.
        if current is not None and ":" in stripped:
            key, _sep, value = stripped.partition(":")
            key = key.strip()
            if key == "evidence":
                evidence = []
                current["evidence"] = evidence
            else:
                evidence = None
                current[key] = _scalar(value)
            continue

    return {"rows": rows}


def load_register(root: str) -> dict:
    """Read the register with PyYAML if importable, else the fallback parser."""
    path = os.path.join(root, REGISTER)
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    try:
        import yaml  # type: ignore
    except Exception:
        return _fallback_load(text)
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        return {"rows": []}
    return data


# --------------------------------------------------------------------------- #
# Evidence resolution (static AST, no import).
# --------------------------------------------------------------------------- #
def _test_names(tree: ast.AST) -> tuple[set[str], set[str]]:
    """Return (module-level function names, 'Class::method' names) in a parsed file."""
    functions: set[str] = set()
    methods: set[str] = set()
    for node in getattr(tree, "body", []):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.add(node.name)
        elif isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.add("%s::%s" % (node.name, child.name))
    return functions, methods


def _strip_parametrisation(rest: str) -> str:
    """Drop a trailing ``[...]`` parametrisation suffix from the node's last segment.

    ``test_name[param-1]`` -> ``test_name`` and
    ``TestClass::test_name[case]`` -> ``TestClass::test_name``, so a
    parametrised id resolves to the base test collected from the AST while a
    typo in the base name still fails.
    """
    head, sep, tail = rest.rpartition("::")
    tail = _PARAM_SUFFIX.sub("", tail)
    return f"{head}{sep}{tail}" if sep else tail


def node_exists(root: str, node_id: str) -> bool:
    """True if the base test named by a node id exists among the file's real tests.

    The file is AST-parsed (never imported) and its actual test functions and
    ``Class::method`` names are collected; membership is checked against that
    set. A parametrised id resolves to its base test; a mistyped name fails
    because it is not among the collected names.
    """
    match = NODE_ID.match(node_id)
    if not match:
        return False
    rel = match.group("path")
    rest = _strip_parametrisation(match.group("rest"))
    path = os.path.join(root, rel)
    if not os.path.isfile(path):
        return False
    with open(path, encoding="utf-8", errors="replace") as fh:
        try:
            tree = ast.parse(fh.read())
        except SyntaxError:
            return False
    functions, methods = _test_names(tree)
    if "::" in rest:
        # Class::method form.
        return rest in methods
    return rest in functions


def _looks_like_node_id(evidence: str) -> bool:
    return bool(NODE_ID.match(evidence.strip()))


def _node_base_name(rest: str) -> str:
    """The final base function/method name of a node path, suffix stripped."""
    rest = _strip_parametrisation(rest)
    return rest.rsplit("::", 1)[-1]


def _genuine_two_org_node(
    root: str, node_id: str, blueprints: set[str], route_prefixes: dict[str, set[str]]
) -> bool:
    """True when a cited node is a genuine two-organisation test bound to a family.

    'Genuine' means all of:

    * the node resolves to a real test in its file (:func:`node_exists`);
    * the file carries a second-organisation signal; and
    * a two-organisation UNIT in that file drives one of the family's own
      route prefixes -- the same family-targeted association
      check_two_org_coverage uses, so the second-org assertion is bound to the
      family rather than merely co-occurring in the file.

    The two-org signal helpers and the association predicate are reused from
    check_two_org_coverage; nothing is reimplemented here.
    """
    match = NODE_ID.match(node_id.strip())
    if not match:
        return False
    if not node_exists(root, node_id):
        return False
    rel = match.group("path")
    path = os.path.join(root, rel)
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            source = fh.read()
    except OSError:
        return False

    units = two_org.two_org_units(source)
    if not units:
        return False
    for blueprint in blueprints:
        prefixes = route_prefixes.get(blueprint, set())
        for unit in units:
            if two_org._associates(blueprint, unit, prefixes):
                return True
    return False


def startup_v4_problems(root: str, rows: list) -> list[str]:
    """One problem per startup family missing a V4 two-organisation row.

    A startup family must carry at least one V4 (access control; id starting
    ``V4.``) row, applicable and not failing, whose evidence resolves to a
    genuine two-organisation test node bound to that family. A startup family
    present in the register with no such row is a hole the gate must refuse.
    """
    try:
        routes = two_org.load_routes(root)
    except (OSError, ValueError):
        # No route inventory in this tree: startup families cannot be scoped,
        # so there is nothing to enforce. (A synthetic register under --root may
        # ship no inventory; the real tree always has one.)
        return []
    route_prefixes = two_org.blueprint_route_prefixes(routes)
    org_families = two_org.families_reading_org_data(routes)

    # Register family name -> startup family; the register's `family` is the
    # blueprint name, which is exactly what STARTUP_FAMILIES maps.
    blueprint_to_startup = two_org._BLUEPRINT_TO_STARTUP

    # Startup family -> whether any of its V4 rows cites a genuine two-org node.
    satisfied: dict[str, bool] = {}
    present: set[str] = set()

    for row in rows:
        if not isinstance(row, dict):
            continue
        family = str(row.get("family", "")).strip()
        startup = blueprint_to_startup.get(family)
        if startup is None:
            continue
        # Only a family whose blueprint actually reads organisation data in the
        # inventory is in scope -- the same scoping two_org uses.
        if not (two_org.STARTUP_FAMILIES.get(startup, set()) & org_families):
            continue
        present.add(startup)
        satisfied.setdefault(startup, False)

        row_id = str(row.get("id", "")).strip()
        if not row_id.startswith("V4."):
            continue
        if row.get("applies") is False:
            continue
        if str(row.get("status", "")).strip().lower() == "fail":
            continue

        evidence = row.get("evidence") or []
        if isinstance(evidence, str):
            evidence = [evidence]
        blueprints = two_org.STARTUP_FAMILIES.get(startup, set())
        for entry in evidence:
            entry = str(entry).strip()
            if _looks_like_node_id(entry) and _genuine_two_org_node(
                root, entry, blueprints, route_prefixes
            ):
                satisfied[startup] = True
                break

    issues: list[str] = []
    for startup in sorted(present):
        if not satisfied.get(startup, False):
            issues.append(
                f"{REGISTER}: [asvs-register] startup family {startup!r} has no "
                f"V4 row whose evidence is a genuine two-organisation test node "
                f"(a V4.x row, applies:true, citing a resolvable test that builds "
                f"a second organisation and drives this family's routes)"
            )
    return issues


# A path-like token in free prose: a run of word/dot/slash/dash characters.
# This catches a file with an extension (app/services/billing_plans.py), a
# bare top-level file (Dockerfile), and a directory path (app/auth, app/modules).
_PATH_TOKEN = re.compile(r"[\w./-]*[\w]")


def _resolve_evidence(root: str, evidence: str) -> bool:
    """True when the evidence names a real control precisely enough to resolve.

    The rules tightened for the second review:

    * A token that names an existing FILE resolves (an existing ``.py``,
      ``Dockerfile`` with a slash path, ``docs/.../x.md`` and so on).
    * A token that names an existing DIRECTORY resolves ONLY when some OTHER
      token in the same evidence names an existing file inside that directory,
      so ``app/auth`` alone no longer passes but
      ``app/auth app/auth/__init__.py`` does.
    * A BARE WORD (no slash and no recognised file extension) NEVER resolves,
      even if a same-named file or directory happens to exist on disk, so
      ordinary prose can never pass by coincidence. A control named by a bare
      top-level file must be cited with its path (``./Dockerfile``) or a real
      extension.
    """
    tokens = _PATH_TOKEN.findall(evidence)

    has_named_file = False
    has_directory = False
    for token in tokens:
        has_extension = token.endswith((".py", ".md", ".json", ".yml", ".yaml"))
        looks_like_path = "/" in token or has_extension
        if not looks_like_path:
            # Bare word: never resolves, whatever happens to exist on disk.
            continue
        candidate = os.path.join(root, token)
        if os.path.isfile(candidate):
            has_named_file = True
        elif os.path.isdir(candidate):
            has_directory = True

    # A named existing file always resolves. This also satisfies the rule that a
    # directory counts only when a file within it is named: that named file is
    # itself the resolving token. A directory on its own never resolves.
    if has_named_file:
        return True
    _ = has_directory  # a bare directory does not resolve
    return False


# --------------------------------------------------------------------------- #
# Checking.
# --------------------------------------------------------------------------- #
def problems(root: str) -> list[str]:
    """Return one problem string per missing node, missing file or malformed row."""
    register = load_register(root)
    rows = register.get("rows") or []
    issues: list[str] = []

    for row in rows:
        if not isinstance(row, dict):
            continue
        row_id = row.get("id", "?")
        family = row.get("family", "?")
        applies = row.get("applies")
        status = str(row.get("status", "")).strip().lower()
        evidence = row.get("evidence") or []
        if isinstance(evidence, str):
            evidence = [evidence]

        if status == "fail":
            joined = " ".join(str(e) for e in evidence) + " " + str(row.get("brief", ""))
            if not BRIEF_ID.search(joined):
                issues.append(
                    f"{REGISTER}: [asvs-register] row {row_id!r} (family {family!r}) "
                    f"has status:fail but names no brief id (expected a token like "
                    f"T-qa-13 in its evidence)"
                )
            # A fail row records an open gap; it is not required to carry a
            # resolvable test node id.
            continue

        if applies is False:
            # Not applicable: the evidence is a reason, not a control to resolve.
            continue

        for entry in evidence:
            entry = str(entry).strip()
            if _looks_like_node_id(entry):
                if not node_exists(root, entry):
                    issues.append(
                        f"{REGISTER}: [asvs-register] row {row_id!r} (family "
                        f"{family!r}) cites test node {entry!r} but it does not "
                        f"exist in the repository"
                    )
            else:
                # Every piece of evidence must resolve to a real control: a test
                # node id (handled above), an existing file, or an existing
                # directory. Evidence that resolves to none of these -- a path
                # that is not on disk, or free prose naming no path at all --
                # fails the gate so a control can never pass silently.
                if not _resolve_evidence(root, entry):
                    issues.append(
                        f"{REGISTER}: [asvs-register] row {row_id!r} (family "
                        f"{family!r}) cites evidence {entry!r} but it resolves to "
                        f"no test node or control file in the repository"
                    )

    issues.extend(startup_v4_problems(root, rows))
    return issues


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", action="store_true", help="print only the count")
    parser.add_argument("--root", default=ROOT, help="tree to scan")
    args = parser.parse_args()

    root = os.path.abspath(args.root)
    issues = problems(root)

    if not args.count:
        for issue in issues:
            print(issue)
        if issues:
            print()
            print(
                "A row in docs/security/asvs-l2.yml cites evidence that does not\n"
                "exist. Point the row at a real test node id "
                "(path/to/test.py::test_name)\n"
                "or a control file that exists, or mark the row status:fail and name\n"
                "the brief that will close the gap (a token like T-qa-13). The\n"
                "register must never promise a control nobody runs."
            )
    print(len(issues))
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
