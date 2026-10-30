"""The shell and component rules: each fires on a failing fixture template and
stays quiet on a passing one, and each ratchet is registered with its baseline."""
import json
from pathlib import Path

import pytest

from scripts import check_shell_conformance as shell
from scripts import check_ui_contract as ui

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """A template tree with the three approved layouts; returns a writer."""
    base = tmp_path / "app" / "templates"
    monkeypatch.setattr(shell, "ROOT", tmp_path)
    monkeypatch.setattr(shell, "TEMPLATE_DIRS", [base])
    monkeypatch.setattr(ui, "ROOT", tmp_path)
    monkeypatch.setattr(ui, "TPL", base)

    def write(name, text):
        path = base / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    for layout in ("admin_base", "public_base", "composer_base"):
        write(f"layouts/{layout}.html", "<html><body>{% block content %}{% endblock %}</body></html>")
    return write


def _files(found):
    return sorted({item[0].rsplit("/", 1)[-1] for item in found})


# ---- shell: layout --------------------------------------------------------

def test_layout_passes_for_each_approved_layout_and_a_sub_layout(tree):
    tree("a.html", "{% extends 'layouts/admin_base.html' %}")
    tree("b.html", "{% extends 'layouts/public_base.html' %}")
    tree("c.html", "{% extends 'layouts/composer_base.html' %}")
    tree("area/layout.html", "{% extends 'layouts/admin_base.html' %}")
    tree("area/page.html", "{% extends 'area/layout.html' %}")
    assert shell.layout_violations() == []


def test_layout_fails_for_an_unapproved_standalone_or_dangling_page(tree):
    tree("old.html", "{% extends 'layouts/base.html' %}")
    tree("lone.html", "<!DOCTYPE html><html><body>hi</body></html>")
    tree("area/layout.html", "<html></html>")
    tree("area/page.html", "{% extends 'area/layout.html' %}")
    tree("dyn.html", "{% extends base_template %}")
    tree("excused.html", "{# shell-ok: print layout #}<html></html>")
    found = shell.layout_violations()
    assert _files(found) == ["dyn.html", "layout.html", "lone.html", "old.html", "page.html"]
    assert any("lone.html" in rel and "extends no layout" in why for rel, why in found)


# ---- shell: chrome --------------------------------------------------------

def test_chrome_passes_when_navigation_comes_from_the_shell(tree):
    tree("p.html", "{% extends 'layouts/admin_base.html' %}"
         "{% from 'macros/page_shell.html' import page_shell %}"
         "{% call page_shell(title='x', breadcrumb=[('Home','/')]) %}<div>body</div>{% endcall %}")
    assert shell.chrome_violations() == []


def test_chrome_fails_for_own_sidebar_nav_header_and_breadcrumb(tree):
    tree("p.html", "{% extends 'layouts/admin_base.html' %}\n<aside>x</aside>\n<nav><a href='/'>a</a></nav>\n"
         "<header class='border-b'>t</header>\n<div aria-label=\"Breadcrumb\"></div>")
    found = shell.chrome_violations()
    assert len(found) == 4
    assert any("line 2" in why and "<aside>" in why for _, why in found)


def test_chrome_ignores_macro_definitions_and_comments(tree):
    tree("m.html", "{% extends 'layouts/admin_base.html' %}{% macro bar() %}<nav>x</nav>{% endmacro %}{# <aside> #}")
    assert shell.chrome_violations() == []


# ---- shell: escapes -------------------------------------------------------

def test_escapes_lists_every_hatch_and_none_when_absent(tree):
    tree("clean.html", "{% extends 'layouts/admin_base.html' %}")
    assert shell.escape_hatches() == []
    tree("print.html", "{# shell-ok: print layout #}<html></html>")
    found = shell.escape_hatches()
    assert len(found) == 1 and "print layout" in found[0][1]


# ---- dialog title ---------------------------------------------------------

def test_dialog_title_passes_with_a_title_or_a_real_label_target(tree):
    tree("ok.html", "{% call modal(id='a', title='Delete item') %}x{% endcall %}\n"
         "<div role=\"dialog\" aria-labelledby=\"t1\"><h2 id=\"t1\">Hi</h2></div>")
    assert ui.collect_component_rule("dialog-title") == []


def test_dialog_title_fails_for_untitled_calls_and_dangling_labels(tree):
    tree("bad.html", "{% call modal(id='a', size='sm') %}x{% endcall %}\n"
         "{% call drawer(id='d', title='') %}x{% endcall %}\n"
         "<div role=\"dialog\">x</div>\n"
         "<div role=\"dialog\" aria-labelledby=\"missing\">x</div>")
    found = ui.collect_component_rule("dialog-title")
    assert [ln for _, ln, _ in found] == [1, 2, 3, 4]
    assert found[0][0].endswith("bad.html")


# ---- empty state ----------------------------------------------------------

def test_empty_state_passes_with_the_macro(tree):
    tree("ok.html", "{% from 'components/empty_state.html' import empty_state %}"
         "{{ empty_state(icon='inbox', title='No risks yet') }}")
    assert ui.collect_component_rule("empty-state") == []


def test_empty_state_fails_for_a_hand_written_block(tree):
    tree("bad.html", "<div class=\"text-center\">\n<i data-lucide=\"inbox\"></i>\n"
         "<p class=\"text-muted-foreground\">No risks recorded yet</p>\n</div>")
    found = ui.collect_component_rule("empty-state")
    assert [ln for _, ln, _ in found] == [3]


# ---- form field -----------------------------------------------------------

def test_form_field_passes_for_macros_groups_and_non_field_inputs(tree):
    tree("ok.html", "<form>{{ form_field(id='n', label='Name') }}\n"
         "{% call form_group(id='o', label='Owner') %}<select id=\"o\"></select>{% endcall %}\n"
         "<input type=\"hidden\" name=\"csrf\"><button type=\"submit\">Go</button></form>\n"
         "<input name=\"q\">")
    assert ui.collect_component_rule("form-field") == []


def test_form_field_fails_for_a_bare_label_and_controls(tree):
    tree("bad.html", "<form>\n<label for=\"n\">Name</label><input id=\"n\" name=\"n\">\n"
         "<select name=\"s\"></select>\n<textarea name=\"t\"></textarea>\n</form>")
    found = ui.collect_component_rule("form-field")
    assert [ln for _, ln, _ in found] == [2, 3, 4]


def test_a_hatch_line_excuses_a_component_finding(tree):
    tree("h.html", "<form>\n{# ui-contract-ok: search box #}\n<input name=\"q\">\n</form>")
    assert ui.collect_component_rule("form-field") == []


# ---- registration ---------------------------------------------------------

@pytest.mark.parametrize("name,key", [
    ("shell-layout", "shell_layout"), ("shell-chrome", "shell_chrome"),
    ("shell-escapes", "shell_escapes"), ("dialog-title", "dialog_title"),
    ("empty-state-macro", "empty_state_macro"), ("form-field-macro", "form_field_macro"),
])
def test_each_rule_is_a_registered_ratchet_with_a_baseline(name, key):
    source = (ROOT / "scripts" / "verify.py").read_text(encoding="utf-8")
    assert f'Gate("{name}"' in source
    baseline = json.loads((ROOT / "verification_baseline.json").read_text(encoding="utf-8"))
    assert key in baseline["ratchets"]


def test_the_real_tree_is_at_or_below_every_baseline():
    baseline = json.loads((ROOT / "verification_baseline.json").read_text(encoding="utf-8"))
    flat = baseline["ratchets"]
    assert len(shell.layout_violations()) <= flat["shell_layout"]
    assert len(shell.chrome_violations()) <= flat["shell_chrome"]
    assert len(shell.escape_hatches()) <= flat["shell_escapes"]
