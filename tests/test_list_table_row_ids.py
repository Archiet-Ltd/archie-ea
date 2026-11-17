"""A table row id is the page's anchor target, so two tables on one page must never share a prefix."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIRS = [ROOT / "app" / "templates", *(ROOT / "app" / "modules").glob("*/templates")]
PREFIX_RE = re.compile(r"row_id_prefix\s*=\s*(['\"])(.*?)\1")
DEFINITION = Path("components") / "list_table.html"


def _uses(text):
    return [m.group(2) for m in PREFIX_RE.finditer(text)]


def _call_sites():
    found = []
    for base in TEMPLATE_DIRS:
        for path in base.rglob("*.html"):
            if path.as_posix().endswith(DEFINITION.as_posix()):
                continue
            for prefix in _uses(path.read_text(encoding="utf-8")):
                found.append((prefix, path.relative_to(ROOT).as_posix()))
    return found


def test_a_row_id_prefix_is_a_literal_that_ends_in_a_hyphen():
    for prefix, where in _call_sites():
        assert prefix.endswith("-") and len(prefix) > 1, f"{where}: row_id_prefix {prefix!r} must end in a hyphen"


def test_no_two_tables_share_a_row_id_prefix():
    seen = {}
    for prefix, where in _call_sites():
        assert prefix not in seen, f"row_id_prefix {prefix!r} is used in {seen[prefix]} and in {where}: duplicate ids"
        seen[prefix] = where


def test_the_guard_sees_a_shared_or_unhyphenated_prefix():
    text = "{% call x(row_id_prefix='gap-') %}{% call y(row_id_prefix='gap-') %}{% call z(row_id_prefix=\"risk\") %}"
    assert _uses(text) == ["gap-", "gap-", "risk"]
