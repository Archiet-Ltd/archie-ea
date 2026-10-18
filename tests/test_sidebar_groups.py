"""The sidebar's display groups (get_sidebar_groups) lay out the SAME links as the zones do.

Zones (home, my_work, library, governance, admin) stay the model the modules directory, the
dashboard and the link budget read. get_sidebar_groups only decides where a link is displayed:
six question-shaped headings for every role, in a fixed order, empty ones hidden. These tests
prove it neither loses nor invents a link, lists each once, keeps the order, and keeps admin
links in the Admin group.
"""

from __future__ import annotations

import pytest

from app.utils import role_access
from app.utils.role_access import (
    _GROUP_FOR_ENDPOINT,
    _SIDEBAR_GROUPS,
    SIDEBAR_LINK_BUDGET,
    SIDEBAR_ZONES,
    get_sidebar_groups,
)

ROLES = sorted(SIDEBAR_ZONES)
TITLES = [title for _key, title in _SIDEBAR_GROUPS]
ASK = role_access._ASK_LINK["endpoint"]


@pytest.fixture
def groups_for(monkeypatch):
    """The groups a role gets from its full, unfiltered zones (every link's guard satisfied)."""

    def build(role):
        monkeypatch.setattr(role_access, "get_sidebar_zones", lambda user: SIDEBAR_ZONES[role])
        return get_sidebar_groups(object())

    return build


def _zone_endpoints(role):
    return [link["endpoint"] for zone in SIDEBAR_ZONES[role] for link in zone["links"]]


def test_the_six_headings_are_the_approved_ones_in_the_approved_order():
    assert TITLES == [
        "Getting started", "What we do", "What supports it", "Goals and changes",
        "What if we change it", "Build and model", "Admin",
    ]


@pytest.mark.parametrize("role", ROLES)
def test_no_link_is_lost_or_invented(groups_for, role):
    grouped = [link["endpoint"] for group in groups_for(role) for link in group["links"]]

    assert sorted(grouped) == sorted(set(_zone_endpoints(role)))


@pytest.mark.parametrize("role", ROLES)
def test_each_link_appears_once_and_the_budget_is_unchanged(groups_for, role):
    grouped = [link["endpoint"] for group in groups_for(role) for link in group["links"]]

    assert len(grouped) == len(set(grouped))
    assert len(grouped) <= SIDEBAR_LINK_BUDGET


@pytest.mark.parametrize("role", ROLES)
def test_ask_is_first_and_outside_every_titled_group(groups_for, role):
    groups = groups_for(role)

    assert groups[0]["title"] is None and [link["endpoint"] for link in groups[0]["links"]] == [ASK]
    for group in groups[1:]:
        assert ASK not in [link["endpoint"] for link in group["links"]]


@pytest.mark.parametrize("role", ROLES)
def test_groups_keep_one_fixed_order_and_never_show_an_empty_heading(groups_for, role):
    titled = [g for g in groups_for(role) if g["title"]]

    assert all(g["links"] for g in titled)
    order = [TITLES.index(g["title"]) for g in titled]
    assert order == sorted(order)


@pytest.mark.parametrize("role", ROLES)
def test_admin_zone_links_are_only_ever_in_the_admin_group(groups_for, role):
    admin_zone = {link["endpoint"] for z in SIDEBAR_ZONES[role] if z["zone"] == "admin" for link in z["links"]}
    groups = {g["group"]: {link["endpoint"] for link in g["links"]} for g in groups_for(role)}

    assert admin_zone == groups.get("admin", set())


def test_governance_links_sit_under_what_if_we_change_it(groups_for):
    groups = {g["group"]: {link["endpoint"] for link in g["links"]} for g in groups_for("cto")}

    assert {"arb.dashboard", "arb.reviews", "arb.sessions", "arch_decisions.list_decisions"} <= groups["what_if"]


def test_every_non_admin_endpoint_in_the_zones_is_placed_explicitly():
    """A link added to a zone later still lands somewhere sensible, but the current ones must be
    listed on purpose, so a new one is a decision made in review."""
    zone_endpoints = {
        link["endpoint"]
        for zones in SIDEBAR_ZONES.values()
        for zone in zones if zone["zone"] != "admin"
        for link in zone["links"]
    } - {ASK}

    assert zone_endpoints - set(_GROUP_FOR_ENDPOINT) == set()


def test_every_explicit_placement_names_a_real_group():
    keys = {key for key, _title in _SIDEBAR_GROUPS}

    assert set(_GROUP_FOR_ENDPOINT.values()) <= keys


def test_a_link_with_no_explicit_placement_falls_back_to_its_zones_group(monkeypatch):
    unknown = {"label": "New page", "endpoint": "brand_new.endpoint", "icon": "x", "requires": None, "query_params": None}
    zones = [{"zone": "library", "title": "Library", "links": [unknown]}]
    monkeypatch.setattr(role_access, "get_sidebar_zones", lambda user: zones)

    groups = get_sidebar_groups(object())

    assert [g["group"] for g in groups] == ["supports"]
