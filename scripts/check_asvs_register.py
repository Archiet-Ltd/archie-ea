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
  - Evidence that is a control description (free prose that names a real file
    path, with no ``::``) passes when the file path it names exists.
  - A row with ``status: fail`` must name a brief by id (a token matching
    ``T-[a-z]+-\\d+``, e.g. T-qa-13) so an open gap always points at the work
    that will close it. A fail row with no brief id is malformed.
  - A row with ``applies: false`` records why the requirement does not apply and
    needs no test evidence.

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

The gate is must-be-0: one problem line per missing test node id, per missing
control file, or per malformed fail row, then a trailing line that is the
integer count.

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

REGISTER = os.path.join("docs", "security", "asvs-l2.yml")

# A brief id token, e.g. T-qa-13. A status:fail row must name one.
BRIEF_ID = re.compile(r"T-[a-z]+-\d+")

# A test node id: a .py file path, then :: and a test (optionally Class::test).
NODE_ID = re.compile(r"^(?P<path>[^\s:]+\.py)::(?P<rest>[\w:]+)$")


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


def node_exists(root: str, node_id: str) -> bool:
    """True if the test function/method named by a node id exists in its file."""
    match = NODE_ID.match(node_id)
    if not match:
        return False
    rel = match.group("path")
    rest = match.group("rest")
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


def _referenced_file(evidence: str) -> str | None:
    """Pull the first token that looks like a repo file path out of free prose."""
    for token in re.findall(r"[\w./-]+\.[A-Za-z0-9_]+", evidence):
        if "/" in token or token.endswith((".py", ".md", ".json", ".yml", ".yaml")):
            return token
    return None


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
                referenced = _referenced_file(entry)
                if referenced is not None:
                    if not os.path.exists(os.path.join(root, referenced)):
                        issues.append(
                            f"{REGISTER}: [asvs-register] row {row_id!r} (family "
                            f"{family!r}) names control file {referenced!r} but it "
                            f"does not exist in the repository"
                        )
                # Free prose with no file path and no node id is accepted as a
                # recorded control note (e.g. an applies:false reason on a pass
                # row), nothing to resolve.
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
