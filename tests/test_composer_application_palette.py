"""The Composer offers Application and Application component, and the search returns the right kinds
(ADR-0014 Decision 8, T-app-05).

Serves release-rule line 3 (agreeing figures) and Job 1: the palette, the pickers and the drawing read the
registry's specialisation, so an Application is offered, drawn and found wherever its parent is.
"""
import pathlib
import re

import pytest

from app.config.archimate_relationship_matrix import ALL_ELEMENTS, EXTENSION_ELEMENTS
from app.models.archimate_element_types import (
    SPECIALIZATIONS,
    application_kind_types,
    base_type,
    plain_name_for,
    type_registry_for_page,
)

ROOT = pathlib.Path(__file__).resolve().parent.parent
JS = ROOT / "app/static/js/archimate"
RENDERER = (JS / "composer_renderer.js").read_text(encoding="utf-8")
COMPOSER = (JS / "composer.js").read_text(encoding="utf-8")
SEARCH = (JS / "composer_search.js").read_text(encoding="utf-8")
# The palette's own entries that are drawing tools, not ArchiMate concepts.
NOT_CONCEPTS = {"Note", "AndJunction", "OrJunction", "Junction"}


def _palette():
    start = RENDERER.index("let PALETTE = {")
    end = RENDERER.index("/* Layer lookup from element type */")
    groups, current = {}, None
    for line in RENDERER[start:end].splitlines():
        head = re.match(r"\s*'([A-Za-z &]+)':\s*\[", line)
        if head:
            current = head.group(1)
            groups[current] = []
            continue
        item = re.search(r"type:\s*'(\w+)',\s*label:\s*'([^']+)'", line)
        if item and current:
            groups[current].append((item.group(1), item.group(2)))
    return groups


def test_every_palette_type_is_in_the_registry():
    known = set(ALL_ELEMENTS) | set(EXTENSION_ELEMENTS) | NOT_CONCEPTS
    types = [t for items in _palette().values() for t, _ in items]
    assert types, "palette not parsed"
    assert [t for t in types if t not in known] == []
    assert len(types) == len(set(types)), "a type is offered twice"


def test_application_group_leads_with_application_then_the_component():
    group = _palette()["Application"]
    labels = type_registry_for_page()
    assert group[0] == ("Application", labels["Application"]["label"])
    # the chip is short like its siblings (App Collaboration, App Interface); the registry holds the full name
    assert group[1] == (base_type("Application"), "App Component")
    assert labels["Application"]["label"] == plain_name_for("Application") == "Application"
    assert labels[base_type("Application")]["label"] == "Application component"
    assert labels["Application"]["base"] == labels[base_type("Application")]["base"]


def test_the_specialisation_is_read_through_the_parent_not_listed_again():
    # icon, shape kind and container behaviour resolve a specialisation to its parent
    assert re.search(r"function typeIconPath\(elType\) \{\s*return TYPE_ICONS\[baseTypeOf\(elType\)\]", RENDERER)
    assert re.search(r"function shapeCategory\(elType\) \{\s*return SHAPE_CATEGORY\[baseTypeOf\(elType\)\]", RENDERER)
    assert "CONTAINER_TYPES[ComposerRenderer.baseTypeOf(elType)]" in COMPOSER
    assert not re.search(r"^\s+Application:\s*'M", RENDERER, re.M), "a second icon entry"
    assert "Application: 'node'" not in RENDERER


def test_defaults_that_mean_the_systems_are_application():
    assert COMPOSER.count("landscapeColType: 'Application'") == 1
    assert COMPOSER.count("matrixColType: 'Application'") == 1
    assert COMPOSER.count("_quickAddNewType: 'Application'") == 1
    assert "matrixColType: 'Application'" in SEARCH
    assert "this.searchTypeFilter || 'Application'" in SEARCH


def test_the_type_registry_reaches_the_page_and_labels_the_notation():
    template = (ROOT / "app/templates/archimate/composer.html").read_text(encoding="utf-8")
    assert "typeRegistry: {{ type_registry|tojson }}" in template
    assert "ComposerRenderer.applyTypeRegistry(" in template
    assert ':title="t.title || t.label" x-text="t.label"' in template, "the chip's full name is its tooltip"
    assert "item.title = TYPE_REGISTRY[item.type].label" in RENDERER
    assert "typeLabel/style', 'fill: hsl(var(--muted-foreground))'" in RENDERER
    assert "typeLabel/fontSize', 12" in RENDERER
    assert "«" in RENDERER or "\u00ab" in RENDERER


def test_pickers_list_both_kinds():
    for rel in (
        "app/templates/archimate/partials/_composer_landscape.html",
        "app/templates/archimate/partials/_composer_matrix_view.html",
        "app/templates/archimate/partials/_composer_overlays.html",
    ):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert '<option value="Application">Application</option>' in text, rel
        assert '<option value="ApplicationComponent">Application component</option>' in text, rel


def _login_user(db_session, make_org):
    from app.models.user import User

    org = make_org("composer-palette")
    user = User(email=f"palette-{org.id}@example.com", organization_id=org.id, confirmed=True)
    user.password = "x"
    db_session.add(user)
    db_session.flush()
    return org, user


@pytest.fixture
def seeded(db_session, make_org):
    from app.models import ArchiMateElement

    org, user = _login_user(db_session, make_org)
    rows = {}
    for name, typ in (("Palette Billing", "Application"), ("Palette Billing API", base_type("Application")),
                      ("Palette Ledger", "Node")):
        el = ArchiMateElement(name=name, type=typ, layer="application", organization_id=org.id)
        db_session.add(el)
        rows[name] = el
    db_session.commit()
    return user


def _names(resp):
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return {row["name"]: row["type"] for row in resp.get_json()["data"]}


def test_search_type_application_returns_applications_only(client, seeded, login_as):
    login_as(client, seeded)
    found = _names(client.get("/archimate/api/elements/search?q=Palette&type=Application"))
    assert found == {"Palette Billing": "Application"}


def test_search_for_applications_in_general_returns_both_kinds(client, seeded, login_as):
    login_as(client, seeded)
    found = _names(client.get("/archimate/api/elements/search?q=Palette&kind=application"))
    assert set(found) == {"Palette Billing", "Palette Billing API"}
    assert set(found.values()) == set(application_kind_types())


def test_search_type_component_still_returns_components_only(client, seeded, login_as):
    login_as(client, seeded)
    found = _names(client.get("/archimate/api/elements/search?q=Palette&type=" + base_type("Application")))
    assert found == {"Palette Billing API": base_type("Application")}


def test_composer_page_carries_the_registry_map(client, seeded, login_as):
    login_as(client, seeded)
    resp = client.get("/archimate/composer")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "typeRegistry:" in html
    assert '"label": "Application component"' in html or '"label":"Application component"' in html
    assert SPECIALIZATIONS  # the map is built from it
