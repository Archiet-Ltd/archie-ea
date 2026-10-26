"""The data-type register is enforced: a page that holds a kind of data shows it with its component."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location("check_data_types_under_test", ROOT / "scripts/check_data_types.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


cdt = _load()

REGISTER = """
version: 1
types:
  - id: archimate-elements
    components: [components/element_table.html]
    requires: ["archimate.composer_page"]
    signals: ['\\b\\w+\\.element_type\\b']
    retired: [solutions/partials/_element_table.html]
  - id: record-list
    components: [components/list_table.html]
    signals: ['<table\\b']
    retired: [components/dense_table.html]
hand_pagination:
  signal: '\\b\\w+\\.(?:iter_pages\\(|has_next\\b)'
  allowed_with: [components/list_table.html]
placeholder_content:
  signed_in: 'layouts/admin_base\\.html'
  signal: '(?i)\\bwill hold\\b|\\bcoming soon\\b'
"""


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/data-type-register.yml").write_text(REGISTER, encoding="utf-8")
    (tmp_path / "app/templates/components").mkdir(parents=True)
    (tmp_path / "tests/smoke").mkdir(parents=True)

    def build(pages, design=None, journeys=None):
        for rel, body in pages.items():
            p = tmp_path / "app/templates" / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, encoding="utf-8")
        for rel, body in (journeys or {}).items():
            (tmp_path / rel).write_text(body, encoding="utf-8")
        (tmp_path / "docs/design-map.json").write_text(json.dumps({"pages": design or {}}), encoding="utf-8")
        return tmp_path

    return build


GOOD_RECORD = """{% extends "layouts/admin_base.html" %}
{% from 'components/element_table.html' import element_table %}
{{ button(href=url_for('archimate.composer_page')) }}
{% call(e) element_table(rows=x, columns=[], caption='c', testid='t', row_testid='requirement-row') %}{% endcall %}
"""
DECLARED = {"app/templates/solutions/record.html": {"data_types": [
    {"type": "archimate-elements", "testid": "requirement-row", "journey": "tests/smoke/test_record.py"}]}}


def test_a_declared_page_using_its_component_and_journey_passes(repo):
    root = repo({"solutions/record.html": GOOD_RECORD}, DECLARED,
                {"tests/smoke/test_record.py": "page.locator('[data-testid=requirement-row]')"})
    assert cdt.collect("declared", root) == []


def test_a_declared_page_without_the_component_fails(repo):
    body = '{% extends "layouts/admin_base.html" %}<div data-testid="requirement-row"></div> archimate.composer_page'
    root = repo({"solutions/record.html": body}, DECLARED,
                {"tests/smoke/test_record.py": "requirement-row"})
    found = cdt.collect("declared", root)
    assert len(found) == 1 and "imports none of components/element_table.html" in found[0][2]


def test_a_page_that_only_describes_its_elements_fails(repo):
    """The solution record of 25 Oct: a tab that said what it would hold, with no table."""
    body = ('{% extends "layouts/admin_base.html" %}{% from \'components/element_table.html\' import element_table %}'
            '<p>This tab will hold the requirements.</p>')
    root = repo({"solutions/record.html": body}, DECLARED, {"tests/smoke/test_record.py": "requirement-row"})
    msgs = [m for _, _, m in cdt.collect("declared", root)]
    assert any("never uses archimate.composer_page" in m for m in msgs)
    assert any("never renders the declared test id" in m for m in msgs)
    assert len(cdt.collect("placeholder-content", root)) == 1


def test_a_missing_or_silent_journey_fails(repo):
    root = repo({"solutions/record.html": GOOD_RECORD}, DECLARED)
    assert "does not exist" in cdt.collect("declared", root)[0][2]
    root = repo({"solutions/record.html": GOOD_RECORD}, DECLARED,
                {"tests/smoke/test_record.py": "assert response.status_code == 200"})
    assert "never names 'requirement-row'" in cdt.collect("declared", root)[0][2]


def test_an_unknown_data_type_fails(repo):
    design = {"app/templates/solutions/record.html": {"data_types": [{"type": "widgets", "testid": "x", "journey": "j"}]}}
    root = repo({"solutions/record.html": GOOD_RECORD}, design)
    assert "not in the data-type register" in cdt.collect("declared", root)[0][2]


def test_undecided_counts_pages_showing_data_without_its_component(repo):
    root = repo({
        "apps/list.html": "<table>{% for a in apps %}<tr><td>{{ a.name }}</td></tr>{% endfor %}</table>",
        "apps/elements.html": "{% for e in items %}{{ e.element_type }}{% endfor %}",
        "apps/good.html": "{% from 'components/list_table.html' import list_table %}<table>",
        "components/list_table.html": "<table>",
    })
    found = sorted(rel for rel, _, _ in cdt.collect("undecided", root))
    assert found == ["app/templates/apps/elements.html", "app/templates/apps/list.html"]


def test_comments_never_count(repo):
    root = repo({"apps/doc.html": "{# an example: <table> and e.element_type #}<p>ok</p>"})
    assert cdt.collect("undecided", root) == []


def test_retired_imports_are_counted(repo):
    root = repo({
        "apps/old.html": "{% from 'components/dense_table.html' import dense_table %}",
        "components/dense_table.html": "{% macro dense_table() %}{% endmacro %}",
    })
    assert [r for r, _, _ in cdt.collect("retired", root)] == ["app/templates/apps/old.html"]


def test_hand_pagination_is_counted_unless_the_list_table_pages_it(repo):
    root = repo({
        "apps/a.html": "{% for p in pagination.iter_pages() %}{{ p }}{% endfor %}",
        "apps/b.html": "{% from 'components/list_table.html' import list_table %}{% if pagination.has_next %}{% endif %}",
    })
    assert [r for r, _, _ in cdt.collect("hand-pagination", root)] == ["app/templates/apps/a.html"]


def test_placeholder_content_counts_signed_in_pages_only_and_ignores_attributes(repo):
    root = repo({
        "apps/stub.html": '{% extends "layouts/admin_base.html" %}<p>Coming soon</p>',
        "apps/form.html": '{% extends "layouts/admin_base.html" %}<input placeholder="Coming soon">',
        "public/early.html": '{% extends "layouts/public_base.html" %}<p>Coming soon</p>',
    })
    assert [r for r, _, _ in cdt.collect("placeholder-content", root)] == ["app/templates/apps/stub.html"]


def test_the_product_register_names_components_that_exist():
    register, _ = cdt.load(ROOT)
    for t in register["types"]:
        for c in t.get("components") or []:
            assert (ROOT / "app/templates" / c).exists(), (t["id"], c)


def test_the_solution_record_passes_its_declaration():
    assert [f for f in cdt.collect("declared", ROOT) if "solutions/record.html" in f[0]] == []
