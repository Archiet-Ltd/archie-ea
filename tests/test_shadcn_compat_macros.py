"""Render tests for the shadcn_compat substrate macro (the native select)."""
import re
from pathlib import Path

import jinja2
import pytest

TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "templates"


@pytest.fixture(scope="module")
def render():
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(TEMPLATES)), autoescape=True)
    imp = "{% from 'macros/shadcn_compat.html' import shadcn_select %}"
    return lambda src, **kw: env.from_string(imp + src).render(**kw)


def test_select_label_help_and_selected(render):
    out = render("{{ shadcn_select('p', 'Persona', options=[('a','A'),('b','B')], selected='b', help_text='hint') }}")
    assert '<label for="p"' in out and 'value="b" selected' in out
    assert 'aria-describedby="p-help"' in out and "hint" in out


def test_select_error_state(render):
    out = render("{{ shadcn_select('p', 'Persona', error='bad') }}")
    assert 'aria-invalid="true"' in out and 'aria-describedby="p-error"' in out and "border-destructive" in out


def test_select_bare_call_body(render):
    out = render("{% call shadcn_select('p', bare=true) %}<option>x</option>{% endcall %}")
    assert "<label" not in out and "<option>x</option>" in out


def test_new_macros_have_no_hardcoded_colours_or_shadows():
    src = (TEMPLATES / "macros" / "shadcn_compat.html").read_text()
    new = src[src.index("Component substrate"):]
    assert not re.search(r"(text|bg|border|ring)-(red|green|blue|amber|gray|slate|emerald|sky)-\d", new)
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b", new)
    assert not re.search(r"(?<![\w-])shadow-(sm|md|lg|xl)", new)


def test_select_bare_omits_id_and_honours_size_classes(render):
    out = render("{% call shadcn_select('p', bare=true, class='w-48 h-9') %}<option>x</option>{% endcall %}")
    assert " id=" not in out and "w-full" not in out and "h-10" not in out and "w-48 h-9" in out
    assert ' id="q"' in render("{% call shadcn_select('p', bare=true, id='q') %}<option>x</option>{% endcall %}")


def test_call_body_sees_enclosing_loop_variable(render):
    out = render("{% for o in ['a','b'] %}{% call shadcn_select('p', bare=true) %}<option>{{ o }}</option>{% endcall %}{% endfor %}")
    assert "<option>a</option>" in out and "<option>b</option>" in out


def test_select_attrs_are_escaped_not_trusted(render):
    payload = '"><script>alert(1)</script>'
    out = render("{{ shadcn_select('p', 'Persona', attrs={'data-x': payload, 'x-model': 'relType'}) }}", payload=payload)
    assert "<script>" not in out
    assert "&#34;&gt;&lt;script&gt;" in out
    assert 'x-model="relType"' in out
