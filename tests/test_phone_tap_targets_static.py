"""Phone tap targets and field text are set in the templates themselves.

At 390 px wide a link or label must have a box of at least 24 by 24 and a field must
have text of at least 16 px. The browser journeys measure that on a rendered page; this
file pins the template source of the controls those journeys cover, so a change that
drops the phone classes fails without a browser. It fails at the commit before the
change and passes after.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "app" / "templates"


def _read(rel):
    return (TEMPLATES / rel).read_text(encoding="utf-8")


def _tags(source, tag):
    return re.findall(r"<%s\b[^<]*?>" % tag, source, re.S)


def test_public_footer_links_have_a_phone_hit_box():
    source = _read("components/public_footer.html")
    anchors = [a for a in _tags(source, "a") if "href=" in a]
    assert anchors
    missing = [a[:90] for a in anchors if "max-md:min-h-6" not in a and "max-md:py-1.5" not in a
               and "aria-label=\"Entelim home\"" not in a]
    assert not missing, missing


def test_public_prose_wrappers_pad_their_list_links_on_a_phone():
    pages = ["page.html", "early_access.html", "early_access_hub.html", "vs_hub.html", "hub_index.html"]
    for name in pages:
        source = _read("public/" + name)
        articles = [a for a in _tags(source, "article") if "public-prose" in a]
        assert articles, name
        for tag in articles:
            assert "max-md:[&_li_a]:py-1.5" in tag, (name, tag)


def test_no_tag_class_holds_a_closing_angle_bracket():
    # A '>' inside a class value ends the tag for any reader that matches '<article[^>]*'.
    for name in ["page.html", "early_access.html", "early_access_hub.html", "vs_hub.html", "hub_index.html"]:
        for tag in _tags(_read("public/" + name), "article"):
            assert ">" not in tag[:-1], (name, tag)


def test_checkbox_and_radio_labels_in_the_form_macros_have_a_phone_hit_box():
    source = _read("macros/form_macros.html")
    macro = source[source.index("macro render_form_input"):]
    checkbox = macro[macro.index("input_type == 'checkbox'"):macro.index("{% elif field.type == 'RadioField'")]
    radio = macro[macro.index("{% elif field.type == 'RadioField'"):]
    radio = radio[:radio.index("{% elif", 10)] if "{% elif" in radio[10:] else radio
    for branch in (checkbox, radio):
        labels = re.findall(r'<label for="\{\{ (?:field|item)\.id \}\}"[^>]*>', branch)
        assert labels, branch[:200]
        for label in labels:
            assert "max-md:min-h-6" in label, label


def test_hand_written_selects_and_inputs_use_phone_text_size():
    expected = {
        "code_generation/partials/config_panel.html": ['id="frameworkSelect"', 'id="databaseSelect"'],
        "application_mgmt/rationalization_assessment.html": ['id="application-select"'],
        "application_mgmt/response_validation.html": ['id="app-selector"'],
        "applications/create.html": ["form[field_id](id=field_id"],
    }
    for name, markers in expected.items():
        source = _read(name)
        for marker in markers:
            line = next(line for line in source.splitlines() if marker in line)
            assert "text-base md:text-sm" in line, (name, marker)


def test_sso_client_id_input_keeps_its_class_attribute():
    source = _read("admin/sso.html")
    start = source.index('id="client_id"')
    end = source.index(">", source.index("class=", start))
    assert "<!--" not in source[start:end]
