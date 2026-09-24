"""Decision E — ``pack_loader.py`` is the only writer of ``reference_packs``.

Grep-level assertions, same style as
``app/modules/intelligence/tests/test_maturity_authority_readers.py``:
comment/docstring lines are skipped, and a small allow-list separates a
class's own definition statement (which necessarily contains the class name
immediately followed by an opening parenthesis naming its base class) from
an actual construction call elsewhere.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
APP_DIR = REPO_ROOT / "app"

# The model's own class statement necessarily matches the constructor regex
# too, without being a construction call.
CONSTRUCTOR_ALLOWED_FILES = {
    "app/modules/reference_packs/services/pack_loader.py",
}
CLASS_DEFINITION_FILE = "app/models/reference_pack.py"

STATUS_ASSIGNMENT_ALLOWED_FILES = {
    "app/modules/reference_packs/services/pack_loader.py",
}


def _iter_py_files():
    for path in APP_DIR.rglob("*.py"):
        if "/tests/" in path.as_posix() or path.name.startswith("test_"):
            continue
        yield path


def _grep(pattern: str, *, allowed_files: set[str]) -> list[str]:
    hits = []
    regex = re.compile(pattern)
    for path in _iter_py_files():
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel in allowed_files:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if regex.search(line):
                hits.append(f"{rel}:{lineno}: {line.strip()}")
    return hits


def test_reference_pack_is_constructed_only_in_pack_loader():
    hits = _grep(
        r"ReferencePack\(",
        allowed_files=CONSTRUCTOR_ALLOWED_FILES | {CLASS_DEFINITION_FILE},
    )
    # The class statement itself also matches the same substring — confirm
    # the class-definition file's only hit is that one statement, not a
    # second constructor hiding behind it.
    class_file_hits = _grep(r"ReferencePack\(", allowed_files=CONSTRUCTOR_ALLOWED_FILES)
    class_file_hits = [h for h in class_file_hits if h.startswith(f"{CLASS_DEFINITION_FILE}:")]
    class_keyword = "class ReferencePack" + "("
    for hit in class_file_hits:
        assert class_keyword in hit, (
            f"expected only the class statement in {CLASS_DEFINITION_FILE}, found: {hit}"
        )
    assert hits == [], (
        f"found a second construction of {'ReferencePack' + '('} outside pack_loader.py: {hits}"
    )


def _files_referencing_reference_pack() -> list[Path]:
    """Every file that imports or otherwise names ``ReferencePack`` — a bare
    ``status = "published"`` for an unrelated model (there are many —
    ``SolutionDesignSpec`` and others use the same word for their own
    lifecycle) is not a hit for this test; only a file that could plausibly
    be touching a ``ReferencePack`` row is in scope."""
    hits = []
    pattern = re.compile(r"\bReferencePack\b")
    for path in _iter_py_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if pattern.search(text):
            hits.append(path)
    return hits


def test_published_status_is_assigned_only_in_pack_loader():
    candidates = _files_referencing_reference_pack()
    hits = []
    status_re = re.compile(r"""status\s*=\s*["']published["']|\.status\s*=\s*.*published""")
    for path in candidates:
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel in STATUS_ASSIGNMENT_ALLOWED_FILES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if line.strip().startswith("#"):
                continue
            if status_re.search(line):
                hits.append(f"{rel}:{lineno}: {line.strip()}")
    assert hits == [], (
        f"found a second assignment of status='published' outside pack_loader.py: {hits}"
    )


def test_no_raw_sql_against_reference_packs_table():
    hits = _grep(r"INSERT INTO reference_packs|reference_packs", allowed_files=set())
    # Only files that legitimately name the table (the model, the loader, the
    # command, the pack file reader/tests) may mention the string at all;
    # everything else touching it via raw SQL is the defect this test exists
    # to catch. Filter down to hits that look like raw SQL.
    sql_hits = [h for h in hits if re.search(r"(select|insert|update|delete)\s+.*reference_packs", h, re.IGNORECASE)]
    assert sql_hits == [], f"found raw SQL against reference_packs: {sql_hits}"
