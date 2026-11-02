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


# ---- shell: header ----------------------------------------------------------

def test_a_page_with_the_library_breadcrumb_has_its_header_and_a_bare_page_does_not(tree):
    tree("a.html", "{% extends 'layouts/admin_base.html' %}{% block content %}{{ ui.breadcrumb_page(text='A') }}<h1>A</h1>{% endblock %}")
    tree("b.html", "{% extends 'layouts/admin_base.html' %}{% block content %}<h1>B</h1>{% endblock %}")
    assert _files(shell.find_violations()) == ["b.html"]


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


# ---- shell: page column ---------------------------------------------------

def test_header_rule_counts_a_page_column_page_as_conformant(tree):
    tree("col.html", "{# design-block: dashboard #}{% extends 'layouts/admin_base.html' %}"
         "{% block breadcrumb %}x{% endblock %}{% block content %}<h1>Processes</h1>{% endblock %}")
    assert shell.find_violations() == []


def test_header_rule_fails_a_bare_page_with_no_heading(tree):
    tree("bare.html", "{# design-block: dashboard #}{% extends 'layouts/admin_base.html' %}"
         "{% block breadcrumb %}x{% endblock %}{% block content %}<p>body</p>{% endblock %}")
    tree("nocrumb.html", "{# design-block: chat #}{% extends 'layouts/admin_base.html' %}"
         "{% block content %}<h1>Chat</h1>{% endblock %}")
    assert _files(shell.find_violations()) == ["bare.html", "nocrumb.html"]


def test_header_rule_still_passes_an_old_page_header_page(tree):
    tree("old.html", "{% extends 'layouts/admin_base.html' %}"
         "{% from 'macros/page_header.html' import page_header %}{{ page_header(title='x') }}")
    assert shell.find_violations() == []


# ---- shell: the page column cannot be met without a visible heading and breadcrumb ----

_HEAD = "{# design-block: dashboard #}{% extends 'layouts/admin_base.html' %}"
_CRUMB = "{% block breadcrumb %}Home{% endblock %}"


@pytest.mark.parametrize("name,text", [
    ("comment.html", _HEAD + _CRUMB + "{% block content %}{# <h1>Hidden</h1> #}<p>x</p>{% endblock %}"),
    ("htmlcomment.html", _HEAD + _CRUMB + "{% block content %}<!-- <h1>Hidden</h1> --><p>x</p>{% endblock %}"),
    ("script.html", _HEAD + _CRUMB + "{% block content %}<script>var t='<h1>Hi</h1>';</script>{% endblock %}"),
    ("dialog.html", _HEAD + _CRUMB + "{% block content %}<div role=\"dialog\"><div><h1>Confirm</h1></div></div>{% endblock %}"),
    ("dialogtag.html", _HEAD + _CRUMB + "{% block content %}<dialog><h1>Confirm</h1></dialog>{% endblock %}"),
    ("modalcall.html", _HEAD + _CRUMB + "{% block content %}{% call modal(id='a', title='T') %}<h1>Hi</h1>{% endcall %}{% endblock %}"),
    ("template.html", _HEAD + _CRUMB + "{% block content %}<template><h1>Hi</h1></template>{% endblock %}"),
    ("emptycrumb.html", _HEAD + "{% block breadcrumb %}  {# nothing #}  {% endblock %}{% block content %}<h1>Hi</h1>{% endblock %}"),
    ("unknown.html", "{# design-block: zzz #}{% extends 'layouts/admin_base.html' %}" + _CRUMB + "{% block content %}<h1>Hi</h1>{% endblock %}"),
    ("markerincomment.html", "{% extends 'layouts/admin_base.html' %}<!-- {# design-block: dashboard #} -->" + _CRUMB + "{% block content %}<h1>Hi</h1>{% endblock %}"),
])
def test_header_rule_counts_a_hidden_heading_or_crumb_or_unknown_marker_as_header_less(tree, name, text):
    tree(name, text)
    assert _files(shell.find_violations()) == [name]


def test_header_rule_keeps_a_visible_heading_beside_a_dialog(tree):
    tree("both.html", _HEAD + _CRUMB + "{% block content %}<h1>Processes</h1>"
         "<div role=\"dialog\"><div>x</div></div>{% call modal(id='a', title='T') %}x{% endcall %}{% endblock %}")
    assert shell.find_violations() == []


# ---- shell: overlays, structure, visibility and the page's own block ----

@pytest.mark.parametrize("name,text", [
    ("uidialog.html", _HEAD + _CRUMB + "{% block content %}{% call ui.dialog(id='a', title='T') %}<h1>Hi</h1>{% endcall %}{% endblock %}"),
    ("uisheet.html", _HEAD + _CRUMB + "{% block content %}{% call ui.sheet(id='a') %}<h1>Hi</h1>{% endcall %}{% endblock %}"),
    ("uidrawer.html", _HEAD + _CRUMB + "{% block content %}{% call ui.drawer(id='a') %}<h1>Hi</h1>{% endcall %}{% endblock %}"),
    ("alertdialog.html", _HEAD + _CRUMB + "{% block content %}{% call ui.alert_dialog(id='a') %}<h1>Hi</h1>{% endcall %}{% endblock %}"),
    ("popover.html", _HEAD + _CRUMB + "{% block content %}{% call popover(id='a') %}<h1>Hi</h1>{% endcall %}{% endblock %}"),
    ("ariamodal.html", _HEAD + _CRUMB + "{% block content %}<div aria-modal=\"true\"><h1>Hi</h1></div>{% endblock %}"),
    ("iffalse.html", _HEAD + _CRUMB + "{% block content %}{% if false %}<h1>Hi</h1>{% endif %}{% endblock %}"),
    ("rawh1.html", _HEAD + _CRUMB + "{% block content %}{% raw %}<h1>Hi</h1>{% endraw %}{% endblock %}"),
    ("macroh1.html", _HEAD + _CRUMB + "{% macro title() %}<h1>Hi</h1>{% endmacro %}{% block content %}<p>x</p>{% endblock %}"),
    ("outside.html", _HEAD + _CRUMB + "<h1>Hi</h1>{% block content %}<p>x</p>{% endblock %}"),
])
def test_header_rule_ignores_a_heading_that_never_renders(tree, name, text):
    tree(name, text)
    assert _files(shell.find_violations()) == [name]


@pytest.mark.parametrize("name,crumb", [
    ("emptyexpr.html", "{{ '' }}"),
    ("superonly.html", "{{ super() }}"),
    ("emptyspan.html", "<span></span>"),
    ("hiddennav.html", "<nav class=\"hidden\">Home</nav>"),
])
def test_header_rule_needs_visible_text_or_a_breadcrumb_call_in_the_crumb(tree, name, crumb):
    tree(name, _HEAD + "{% block breadcrumb %}" + crumb + "{% endblock %}{% block content %}<h1>Hi</h1>{% endblock %}")
    assert _files(shell.find_violations()) == [name]


@pytest.mark.parametrize("name,heading", [
    ("emptyh1.html", "<h1></h1>"),
    ("blankh1.html", "<h1> {{ '' }} </h1>"),
    ("hiddenclass.html", "<h1 class=\"hidden\">Hi</h1>"),
    ("srh1.html", "<h1 class=\"text-xl sr-only\">Hi</h1>"),
    ("hiddenattr.html", "<h1 hidden>Hi</h1>"),
    ("xshow.html", "<h1 x-show=\"false\">Hi</h1>"),
])
def test_header_rule_rejects_an_empty_or_hidden_heading(tree, name, heading):
    tree(name, _HEAD + _CRUMB + "{% block content %}" + heading + "{% endblock %}")
    assert _files(shell.find_violations()) == [name]


def test_header_rule_accepts_a_crumb_call_a_computed_heading_and_a_responsive_class(tree):
    tree("ok.html", _HEAD + "{% block breadcrumb %}{{ breadcrumb_nav(items) }}{% endblock %}"
         "{% block content %}<h1 class=\"md:hidden text-xl\">{{ title }}</h1>{% endblock %}")
    assert shell.find_violations() == []


def test_header_rule_needs_the_marker_to_name_the_pages_own_block(tree):
    own = json.loads((ROOT / "docs" / "design-map.json").read_text(encoding="utf-8"))["pages"][
        "app/templates/dashboards/overview.html"]["block"]
    other = "chat" if own != "chat" else "dashboard"
    body = "{% extends 'layouts/admin_base.html' %}" + _CRUMB + "{% block content %}<h1>Hi</h1>{% endblock %}"
    tree("dashboards/overview.html", "{# design-block: " + other + " #}" + body)
    assert _files(shell.find_violations()) == ["overview.html"]
    tree("dashboards/overview.html", "{# design-block: " + own + " #}" + body)
    assert shell.find_violations() == []
    tree("unmapped.html", "{# design-block: " + other + " #}" + body)
    assert shell.find_violations() == []


# ---- shell: every library overlay, call arguments, the modals block, responsive headings ----

def _page(content):
    return _HEAD + _CRUMB + "{% block content %}" + content + "{% endblock %}"


@pytest.mark.parametrize("name,content", [
    ("callargs.html", "{% call(row) ui.dialog(id='a') %}<h1>Hi</h1>{% endcall %}"),
    ("callargs2.html", "{% call (row, i) ui.sheet(id='a') %}<h1>Hi</h1>{% endcall %}"),
    ("combocontent.html", "{% call ui.combobox_content() %}<h1>Hi</h1>{% endcall %}"),
    ("selectcontent.html", "{% call ui.select_content() %}<h1>Hi</h1>{% endcall %}"),
    ("navcontent.html", "{% call ui.navigation_menu_content() %}<h1>Hi</h1>{% endcall %}"),
    ("toast.html", "{% call ui.toast() %}<h1>Hi</h1>{% endcall %}"),
    ("collapsible.html", "{% call ui.collapsible_content() %}<h1>Hi</h1>{% endcall %}"),
    ("accordion.html", "{% call ui.accordion_content() %}<h1>Hi</h1>{% endcall %}"),
    ("dialogcontent.html", "{% call ui.dialog_content() %}<h1>Hi</h1>{% endcall %}"),
    ("dropdown.html", "{% call ui.dropdown_menu_content() %}<h1>Hi</h1>{% endcall %}"),
    ("nested.html", "{% call ui.dialog() %}{% call ui.card() %}x{% endcall %}<h1>Hi</h1>{% endcall %}"),
    ("sectiondialog.html", "<section role=\"dialog\"><div>x</div><h1>Hi</h1></section>"),
    ("formmodal.html", "<form aria-modal=\"true\"><h1>Hi</h1></form>"),
    ("hiddenattrclass.html", "<h1 class=\"mb-2 hidden\">Hi</h1>"),
    ("modalsblock.html", "<p>x</p>{% endblock %}{% block modals %}<h1>Hi</h1>"),
])
def test_header_rule_ignores_a_heading_inside_any_library_overlay(tree, name, content):
    tree(name, _page(content))
    assert _files(shell.find_violations()) == [name]


def test_header_rule_keeps_a_heading_beside_overlays_that_close(tree):
    tree("ok.html", _page("<h1>Hi</h1>{% call(row) ui.dialog(id='a') %}x{% endcall %}"
                          "{% call ui.select_content() %}y{% endcall %}<section role=\"dialog\">z</section>"))
    assert shell.find_violations() == []


@pytest.mark.parametrize("heading", [
    "<h1 class=\"hidden md:block\">Hi</h1>",
    "<h1 class=\"hidden sm:flex\">Hi</h1>",
    "<h1 class=\"sr-only sm:not-sr-only\">Hi</h1>",
])
def test_header_rule_counts_a_heading_visible_at_some_width(tree, heading):
    tree("responsive.html", _page(heading))
    assert shell.find_violations() == []


@pytest.mark.parametrize("heading", [
    "<h1 class=\"hidden md:text-xl\">Hi</h1>",
    "<h1 class=\"sr-only md:text-xl\">Hi</h1>",
    "<h1 class=\"md:hidden\" hidden>Hi</h1>",
])
def test_header_rule_still_rejects_a_heading_hidden_at_every_width(tree, heading):
    tree("hiddenall.html", _page(heading))
    assert _files(shell.find_violations()) == ["hiddenall.html"]


def test_every_library_file_is_classified_as_overlay_or_shown():
    on_disk = {p.stem for p in shell.LIBRARY_DIR.glob("*.html")}
    classified = set(shell.OVERLAY_LIBRARY_FILES) | set(shell.SHOWN_LIBRARY_FILES)
    assert on_disk - classified == set(), "classify each new library file as overlay or shown in the shell checker"
    assert classified - on_disk == set()
    assert not set(shell.OVERLAY_LIBRARY_FILES) & set(shell.SHOWN_LIBRARY_FILES)


def test_every_content_macro_in_the_library_is_an_overlay_unless_its_file_is_shown():
    names = shell.MACRO_NAME_RE
    for path in shell.LIBRARY_DIR.glob("*.html"):
        for macro in names.findall(path.read_text(encoding="utf-8")):
            if macro.endswith(("_content", "_portal", "_overlay")) and path.stem in shell.OVERLAY_LIBRARY_FILES:
                assert macro in shell.OVERLAY_MACROS, macro


def test_the_unused_design_block_helper_is_gone():
    assert not hasattr(shell, "design_blocks")
