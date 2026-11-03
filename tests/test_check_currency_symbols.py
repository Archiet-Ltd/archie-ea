"""No page prints a dollar sign where money is drawn.

The checker is proven against planted defects in a temporary tree, and the repository count is pinned to the
baseline so a new hard-coded symbol fails the build.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("check_currency_symbols", REPO / "scripts" / "check_currency_symbols.py")
checker = importlib.util.module_from_spec(_spec)
sys.modules["check_currency_symbols"] = checker
_spec.loader.exec_module(checker)


def _tree(tmp_path, monkeypatch, files):
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(checker, "ROOT", tmp_path)
    return sorted((rel, line) for rel, line, _ in checker.occurrences())


def test_every_hard_coded_dollar_shape_is_found(tmp_path, monkeypatch):
    found = _tree(tmp_path, monkeypatch, {
        "app/static/js/a.js": (
            "el.textContent = '$' + total.toFixed(2);\n"
            "msg = `cost $${data.cost}`;\n"
            "fmt = new Intl.NumberFormat('en-US', { currency: 'USD' });\n"
        ),
        "app/templates/b.html": (
            "<td>${{ row.cost }}</td>\n"
            "<span>$</span>\n"
            "<div>Under $50K a year</div>\n"
        ),
        "app/services/c.py": 'MESSAGE = "Estimated cost exceeds $10M"\n',
    })
    assert found == [("app/services/c.py", 1), ("app/static/js/a.js", 1), ("app/static/js/a.js", 2),
                     ("app/static/js/a.js", 3), ("app/templates/b.html", 1), ("app/templates/b.html", 2),
                     ("app/templates/b.html", 3)]


def test_the_shared_formatter_and_regex_back_references_are_not_found(tmp_path, monkeypatch):
    found = _tree(tmp_path, monkeypatch, {
        "app/static/js/a.js": (
            "el.textContent = window.currencyManager.format(total);\n"
            "name = type.replace(/([A-Z])/g, ' $1 $2').trim();\n"
            "// '$' + total was the old way\n"
        ),
        "app/templates/b.html": "<td>{{ row.cost|currency }}</td>\n<td>{{ currency_symbol }}50K</td>\n",
    })
    assert found == []


def test_a_marked_line_pricing_pages_and_tests_are_left_out(tmp_path, monkeypatch):
    found = _tree(tmp_path, monkeypatch, {
        "app/static/js/a.js": "x = '$' + n; // currency-symbol-ok: a contract states dollars\n",
        "app/templates/public/pricing.html": "<p>$490/year</p>\n",
        "app/modules/x/tests/test_y.py": 'assert fmt(1, "$") == "$1.00"\n',
    })
    assert found == []


def test_text_ending_in_a_dollar_sign_and_python_f_strings_are_found(tmp_path, monkeypatch):
    found = _tree(tmp_path, monkeypatch, {
        "app/static/js/a.js": "el.textContent = 'Est. cost: $' + Math.round(c);\n",
        "app/templates/b.html": """<span x-text="'Est. cost: $' + opt.cost"></span>\n""",
        "app/services/c.py": (
            'label = f"${total:,.0f} saved"\n'
            'logger.info(f"cost ${total:,.0f}")\n'
        ),
    })
    assert found == [("app/services/c.py", 1), ("app/static/js/a.js", 1), ("app/templates/b.html", 1)]


def test_a_figure_drawn_with_its_currency_named_is_not_found(tmp_path, monkeypatch):
    found = _tree(tmp_path, monkeypatch, {
        "app/static/js/a.js": "el.textContent = 'AI usage ' + window.currencyManager.format(cost, 'USD');\n",
        "app/templates/b.html": "<td>{{ row.ai_cost|currency('USD') }}</td>\n",
    })
    assert found == []


def test_the_repository_holds_the_baseline():
    baseline = json.loads((REPO / "verification_baseline.json").read_text())["ratchets"]["currency_symbols"]
    assert len(list(checker.occurrences())) <= baseline


def test_a_named_currency_does_not_hide_a_hard_coded_symbol_on_the_same_line(tmp_path, monkeypatch):
    found = _tree(tmp_path, monkeypatch, {
        "app/static/js/a.js": "el.textContent = '$' + base + window.currencyManager.format(cost, 'USD');\n",
    })
    assert found == [("app/static/js/a.js", 1)]


def test_python_format_and_percent_forms_are_found_and_cell_references_are_not(tmp_path, monkeypatch):
    found = _tree(tmp_path, monkeypatch, {
        "app/services/c.py": (
            'a = "Cost ${:,.0f}".format(t)\n'
            'b = "Cost $%.2f" % t\n'
            'c = f"=B{row}/$B${last}"\n'
            'd = "=SUM($B$4:$B$9)"\n'
        ),
    })
    assert found == [("app/services/c.py", 1), ("app/services/c.py", 2)]
