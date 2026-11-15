"""Only the capability support module writes the support table: a second writer is a second authority."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL = "UnifiedApplicationCapabilityMapping"
TABLE = "unified_application_capability_mapping"
ALLOWED = {
    "app/services/capability_support.py",
    "app/models/unified_application_capability_mapping.py",
}


def _sources():
    for folder in ("app", "scripts"):
        for path in (ROOT / folder).rglob("*.py"):
            rel = path.relative_to(ROOT).as_posix()
            if rel not in ALLOWED and "venv" not in path.parts:
                yield rel, path


def _writes(tree):
    """Line numbers where the module builds a row of the support table or inserts into it with SQL."""
    names = {MODEL}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == MODEL and alias.asname:
                    names.add(alias.asname)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            called = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
            if called in names:
                found.append(node.lineno)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = " ".join(node.value.lower().split())
            if f"insert into {TABLE}" in text or f"insert into public.{TABLE}" in text:
                found.append(node.lineno)
    return found


def test_nothing_outside_the_support_module_writes_the_support_table():
    offenders = []
    for rel, path in _sources():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        offenders.extend(f"{rel}:{line}" for line in _writes(tree))
    assert offenders == [], "write support through record_support_for_records or record_capability_support: " + ", ".join(offenders)


def test_the_check_sees_a_direct_write():
    assert _writes(ast.parse(f"x = {MODEL}(application_component_id=1)")) == [1]
    assert _writes(ast.parse(f"from m import {MODEL} as Row\nRow(a=1)")) == [2]
    assert _writes(ast.parse(f"q = 'INSERT INTO  {TABLE} (a) VALUES (1)'")) == [1]
    assert _writes(ast.parse(f"q = {MODEL}.query.filter_by(id=1)")) == []
