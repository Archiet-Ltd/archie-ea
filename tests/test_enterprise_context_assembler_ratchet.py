"""The enterprise context assembler is frozen (ADR-genome-01 decision 1, LG-1).

It builds prompt context with no node ids, no spec hash and no provenance, so
nothing it supplies can be cited. No new caller may appear; each existing one
moves to a genome slice as its builder lands, and the module is retired at zero.
The allowance below can only shrink.
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE = "app/services/enterprise_context_assembler.py"
ALLOWED_CALLERS = {
    "app/modules/architecture/routes/archimate_routes.py",
}
_USE = re.compile(r"enterprise_context_assembler|EnterpriseContextAssembler")


def _callers():
    found = set()
    for folder in ("app", "scripts"):
        for path in (ROOT / folder).rglob("*.py"):
            rel = path.relative_to(ROOT).as_posix()
            if rel == MODULE or "/tests/" in rel:
                continue
            if _USE.search(path.read_text(encoding="utf-8", errors="ignore")):
                found.add(rel)
    return found


def test_no_new_caller_of_the_enterprise_context_assembler():
    callers = _callers()
    new = callers - ALLOWED_CALLERS
    assert not new, "read organisation facts through app/modules/genome/slices instead: %s" % sorted(new)


def test_the_allowance_shrinks_when_a_caller_moves():
    gone = ALLOWED_CALLERS - _callers()
    assert not gone, "remove these from ALLOWED_CALLERS: %s" % sorted(gone)
