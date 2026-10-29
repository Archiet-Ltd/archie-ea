"""R34 (design lead, 29 Oct 2026): the parent crumb is the sidebar section that lists a page as
an entry a person can click. When a page is reachable from two places, an exact endpoint match
wins over a prefix in ``owns``, and the Work and admin entries win over a Model layer.

``entry_for_endpoint`` and ``page_trail`` (app/navigation/registry.py) are the one lookup both the
sidebar and the page header's breadcrumb read, so pinning its precedence here is what keeps a page
filed under the section its own sidebar entry sits in, rather than under the ArchiMate layer that
happens to back it.
"""

from __future__ import annotations

import pytest

from app.navigation import registry

pytestmark = pytest.mark.usefixtures("db_session")


def test_vendors_gives_spend_then_vendors():
    """The Vendors page sits under Spend in the sidebar (Spend > Vendors): its own exact endpoint
    match must win, not any prefix a Model layer or another Work entry might also offer."""
    assert registry.entry_for_endpoint("unified_applications.vendors").key == "spend"
    assert registry.page_trail(None, "unified_applications.vendors") == [
        ("Spend", None), ("Vendors", None),
    ]


def test_a_model_element_page_still_gives_its_own_layer():
    """An ArchiMate element-type list page (Business actor, under the Business layer) is reachable
    only through the Model group: it keeps its own layer as parent, not a Work entry's prefix."""
    entry = registry.entry_for_endpoint("archimate_crud.list_elements")
    assert entry.key == "archimate_model"
    assert registry.page_trail(
        None, "archimate_crud.list_elements", args={"layer": "business", "element_type": "BusinessActor"},
    ) == [("Business", None), ("Business actor", None)]


def test_capability_roadmap_gives_the_section_the_sidebar_shows():
    """The roadmap page is filed under Changes in the sidebar (Changes > Roadmap aliases it as the
    capability roadmap); the breadcrumb must agree, not read a different, ArchiMate-layer answer."""
    assert registry.entry_for_endpoint("main.capability_roadmap").key == "changes"
    assert registry.page_trail(None, "main.capability_roadmap") == [("Changes", None)]


def test_work_owner_beats_a_longer_model_prefix(monkeypatch):
    """The precedence rule itself, not just today's data: a synthetic page reachable through both a
    Work-group owner (a short prefix) and a Model-group owner (a longer, more specific prefix) is
    filed under the Work entry. Before the two-pass search in entry_for_endpoint, the longer Model
    prefix would have won on string length alone."""
    endpoint = "zz_precedence_test.page_detail"
    work_decoy = registry.NavLink(key="zz_work", label="Zz work", endpoint="zz_precedence_test.home",
                                   group="work", owns=("zz_precedence_test.",))
    model_decoy = registry.NavLink(key="zz_model", label="Zz model", endpoint="zz_precedence_test.home2",
                                    group="model", owns=("zz_precedence_test.page_",))
    assert endpoint.startswith(work_decoy.owns[0])
    assert endpoint.startswith(model_decoy.owns[0])
    assert len(model_decoy.owns[0]) > len(work_decoy.owns[0])  # the Model owner's prefix is the longer one
    monkeypatch.setattr(registry, "ALL_ENTRIES", registry.ALL_ENTRIES + (work_decoy, model_decoy))
    assert registry.entry_for_endpoint(endpoint).key == "zz_work"


@pytest.mark.parametrize("endpoint,entry", [
    ("unified_applications.vendors", "spend"),
    ("main.capability_roadmap", "changes"),
    ("archimate_crud.list_elements", "archimate_model"),
    ("procurement.spend_analytics", "spend"),
])
def test_pages_keep_their_own_section_not_a_model_layer(endpoint, entry):
    assert registry.entry_for_endpoint(endpoint).key == entry
