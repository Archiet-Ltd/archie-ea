"""The shared macros take extra attributes as a dict and escape every value."""

import pytest

PAYLOAD = '"><img src=x onerror=alert(1)>'

CASES = [
    ("components/dense_table.html", "dense_table(columns=[('Name', '')], rows=0, attrs={'data-x': p}, tbody_attrs={'data-y': p})"),
    ("components/dense_table.html", "matrix_row_header('Row', attrs={'data-x': p})"),
    ("components/dense_table.html", "matrix_cell('empty', attrs={'data-x': p})"),
    ("components/form_field.html", "form_footer(cancel_attrs={'data-x': p}, submit_attrs={'data-y': p})"),
]


@pytest.fixture
def env(app):
    return app.jinja_env


@pytest.mark.parametrize("template,call", CASES)
def test_attribute_values_are_escaped(app, env, template, call):
    macro = call.split("(", 1)[0]
    src = "{%% from '%s' import %s %%}{{ %s }}" % (template, macro, call)
    with app.test_request_context():
        out = env.from_string(src).render(p=PAYLOAD)
    assert "<img" not in out
    assert "&#34;&gt;&lt;img" in out or "&quot;&gt;&lt;img" in out


def test_form_group_attrs_render_as_attributes(app, env):
    src = ("{% from 'components/form_field.html' import form_group %}"
           "{% call form_group(id='f', label='File', attrs={'id': 'drop-zone'}) %}<input id='f'>{% endcall %}")
    with app.test_request_context():
        out = env.from_string(src).render()
    assert 'id="drop-zone"' in out
