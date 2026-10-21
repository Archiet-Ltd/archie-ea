"""Ratchet: no new direct comparison of an element type with "ApplicationComponent" (ADR-0014 Decision 8).

An Application is a specialisation of ApplicationComponent. Code that means "an application of either kind" must
call ``is_application_kind``; code that reads a table keyed by standard type must go through ``base_type``
(both in ``app/models/archimate_element_types.py``). A literal comparison silently drops every Application.

The scan is static (AST) over ``app/**/*.py``. It counts, per file:

* equality, inequality, ``in`` and ``not in`` comparisons where one side is the literal or a tuple, list or set
  holding it;
* ``filter_by(type="ApplicationComponent")`` (and ``element_type=``);
* ``.in_([... "ApplicationComponent" ...])``;
* lookups keyed by the literal: ``table["ApplicationComponent"]`` and ``table.get("ApplicationComponent")``.

The registry, the matrix and the exporter modules are exempt. ``BASELINE`` lists what remains and may only
fall: a count above the baseline in any file fails, and so does a file that is not in the baseline.
"""
import ast
import json
import os
from collections import Counter
from pathlib import Path

LITERAL = "ApplicationComponent"
REPO = Path(__file__).resolve().parent.parent
APP = REPO / "app"

EXEMPT = {
    "app/models/archimate_element_types.py",
    "app/config/archimate_relationship_matrix.py",
    "app/modules/architecture/services/archimate_xml_export_service.py",
    "app/services/archimate_export_service.py",
    "app/services/composer_export_formats.py",
    "app/services/archimate_oef_service.py",
    "app/services/archimate_oef_export_service.py",
    "app/modules/architecture/services/archimate_exchange_service.py",
}

BASELINE_PATH = Path(__file__).with_name("application_component_check_baseline.json")


def _is_literal(node):
    return isinstance(node, ast.Constant) and node.value == LITERAL


def _holds_literal(node):
    if _is_literal(node):
        return True
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return any(_is_literal(e) for e in node.elts)
    return False


def violations_in(source):
    """Return a list of (line, kind) for every direct use of the literal as a type check."""
    tree = ast.parse(source)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            sides = [node.left, *node.comparators]
            if any(isinstance(op, (ast.Eq, ast.NotEq, ast.In, ast.NotIn)) for op in node.ops) and any(
                _holds_literal(s) for s in sides
            ):
                found.append((node.lineno, "comparison"))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            name = node.func.attr
            if name == "filter_by" and any(
                kw.arg in ("type", "element_type") and _holds_literal(kw.value) for kw in node.keywords
            ):
                found.append((node.lineno, "filter_by"))
            elif name == "in_" and node.args and _holds_literal(node.args[0]):
                found.append((node.lineno, "in_"))
            elif name == "get" and node.args and _is_literal(node.args[0]):
                found.append((node.lineno, "lookup"))
        elif isinstance(node, ast.Subscript) and _is_literal(node.slice) and isinstance(node.ctx, ast.Load):
            found.append((node.lineno, "lookup"))
    return found


def scan():
    counts = Counter()
    kinds = Counter()
    for path in sorted(APP.rglob("*.py")):
        rel = path.relative_to(REPO).as_posix()
        if rel in EXEMPT:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if LITERAL not in text:
            continue
        for _line, kind in violations_in(text):
            counts[rel] += 1
            kinds[kind] += 1
    return counts, kinds


def test_scanner_detects_each_pattern():
    samples = {
        "comparison": 'x = el.type == "ApplicationComponent"',
        "comparison_ne": 'x = el.type != "ApplicationComponent"',
        "comparison_in": 'x = t in ("ApplicationService", "ApplicationComponent")',
        "filter_by": 'Q.query.filter_by(name=n, type="ApplicationComponent")',
        "in_": 'Q.where(E.type.in_(["ApplicationComponent", "Node"]))',
        "lookup_get": 'm.get("ApplicationComponent", 0)',
        "lookup_sub": 'm["ApplicationComponent"]',
    }
    for label, code in samples.items():
        assert violations_in(code), label
    assert not violations_in('x = el.type == "Node"')
    assert not violations_in('Q.create(type="ApplicationComponent")')


def test_no_new_literal_application_component_checks():
    counts, _kinds = scan()
    baseline = json.loads(BASELINE_PATH.read_text()) if BASELINE_PATH.exists() else {}
    worse = {
        f: (n, baseline.get(f, 0)) for f, n in counts.items() if n > baseline.get(f, 0)
    }
    assert not worse, (
        "New direct comparison with 'ApplicationComponent' (file: found, allowed). "
        "Use is_application_kind() or base_type() from app.models.archimate_element_types: "
        + json.dumps(worse, indent=1)
    )


def test_baseline_only_falls():
    """Fail when the baseline is stale-high, so the ratchet tightens as sites are fixed."""
    counts, _kinds = scan()
    baseline = json.loads(BASELINE_PATH.read_text()) if BASELINE_PATH.exists() else {}
    stale = {f: (counts.get(f, 0), n) for f, n in baseline.items() if counts.get(f, 0) < n}
    assert not stale, "Lower the baseline (file: found, baseline): " + json.dumps(stale, indent=1)
