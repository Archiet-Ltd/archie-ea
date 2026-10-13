"""Schema tests for the six rules-as-data files (ADR-0012 decision 4).

Each guard of the loader (:mod:`app.modules.company_context.services
page_rules`) is asserted against the real files and against a mutated fixture
copy, so every guard is proven to fire on its own.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path

import pytest

from app.modules.company_context.services import page_rules

DATA_ROOT = Path(__file__).resolve().parents[3] / "seed_data" / "company_context"

_SEGMENTS = ("S1", "S2", "S3", "S4")


def _load_mapping():
    import yaml

    mapping = {}
    for name in (
        "page_rules.yml",
        "segment_signals.yml",
        "template_families.yml",
        "tool_aliases.yml",
        "personal_data_patterns.yml",
        "refused_domains.yml",
    ):
        with open(DATA_ROOT / name, encoding="utf-8") as handle:
            mapping[name] = yaml.safe_load(handle)
    return mapping


@pytest.fixture(scope="module")
def rules():
    return page_rules.RULES


def test_rules_loaded_once_and_frozen():
    assert page_rules.RULES is page_rules.RULES
    with pytest.raises(AttributeError):
        page_rules.RULES.purposes = ()
    with pytest.raises(AttributeError):
        del page_rules.RULES.tools


def test_every_tool_names_a_rung_and_native_needs_a_connector_type(rules):
    for tool in rules.tools:
        assert tool["rung"] in page_rules.RUNG_VOCABULARY, tool["tool"]
    for tool in rules.tools:
        if tool["rung"] == "native":
            assert (
                page_rules._normalise_tool_name(tool["tool"])
                in page_rules.VALID_CONNECTOR_TYPES
            ), tool["tool"]


def test_native_rung_fixture_fails_naming_the_tool():
    mapping = _load_mapping()
    tools = mapping["tool_aliases.yml"]["tools"]
    tools = copy.deepcopy(tools)
    tools.append(
        {
            "tool": "Clipboard Pencil",
            "aliases": ["clipboard pencil"],
            "category": "ticketing",
            "rung": "native",
            "pack_key": "-",
            "status_page_hosts": [],
            "marketplace_hosts": [],
        }
    )
    mapping["tool_aliases.yml"]["tools"] = tools
    with pytest.raises(
        page_rules.RulesError, match=r"tools\.Clipboard Pencil\.rung"
    ):
        page_rules.validate(mapping)


def test_every_purpose_has_patterns_and_a_cap(rules):
    for purpose in rules.purposes:
        assert purpose["path_patterns"] or purpose["link_text_patterns"]
        assert isinstance(purpose["max_pages"], int) and purpose["max_pages"] >= 1
    assert len(rules.purposes) == len(rules.purpose_by_key)


def test_every_pattern_compiles(rules):
    for purpose in rules.purposes:
        for pattern in purpose["path_patterns"] + purpose["link_text_patterns"]:
            re.compile(pattern, re.IGNORECASE)
    for rule in rules.segment_rules:
        matcher = rule["matcher"]
        if matcher["kind"] == "numeric_statement":
            re.compile(matcher["pattern"])
    for name, compiled in rules.remove_patterns.items():
        assert compiled.pattern


def test_every_segment_rule_names_a_purpose_and_a_segment(rules):
    purposes = {p["key"] for p in rules.purposes}
    for rule in rules.segment_rules:
        assert rule["purpose"] in purposes, rule["rule_id"]
        assert rule["segment"] in _SEGMENTS, rule["rule_id"]


def test_no_alias_under_three_characters(rules):
    for tool in rules.tools:
        for alias in tool["aliases"]:
            assert len(alias) >= 3, f"{tool['tool']}: {alias}"
    for vendor in rules.long_tail_vendors:
        for alias in vendor["aliases"]:
            assert len(alias) >= 3, f"{vendor['name']}: {alias}"


def test_forbidden_words_absent():
    mapping = _load_mapping()
    for file_key, location, string in page_rules._guarded_strings(mapping):
        assert not page_rules._FORBIDDEN_MATCHER_TOKENS.search(string), (
            f"{file_key}: {location}"
        )


def test_headcount_rule_id_fixture_fails_naming_file_and_key():
    mapping = _load_mapping()
    rules = copy.deepcopy(mapping["segment_signals.yml"]["rules"])
    rules.append(
        {
            "rule_id": "s3_headcount_wide",
            "purpose": "careers",
            "matcher": {"kind": "phrase", "any_of": ["enterprise platform"]},
            "segment": "S3",
            "note": "careers naming enterprise platforms",
        }
    )
    mapping["segment_signals.yml"]["rules"] = rules
    with pytest.raises(
        page_rules.RulesError,
        match=r"segment_signals\.yml: rules\.s3_headcount_wide: forbidden word",
    ):
        page_rules.validate(mapping)


def test_intake_zones_rows_are_pinned_pairs(rules):
    for zone in rules.intake_zones:
        for box in zone["boxes"]:
            expected = page_rules.PINNED_INTAKE_PAIR_BY_BOX[box["box_key"]]
            assert (box["element_type"], box["profile"]) == expected


def test_intake_zones_pair_pin_is_thirteen_rows():
    assert len(page_rules.PINNED_INTAKE_ROWS) == 13
    assert len(page_rules.PINNED_INTAKE_PAIR_BY_BOX) == 13
    assert set(page_rules.PINNED_INTAKE_PAIR_BY_BOX) == {
        "customer_segment", "problem", "value_proposition", "channel",
        "key_metric", "market_driver", "objective", "reason",
        "solution_feature", "revenue_model", "customer_relationship",
        "key_partner", "existing_alternative",
    }


def test_purpose_of_each_intake_zone_exists(rules):
    for zone in rules.intake_zones:
        assert zone["purpose"] in rules.purpose_by_key
    zone_purposes = {z["purpose"] for z in rules.intake_zones}
    never = set(rules.never_sent)
    assert zone_purposes.isdisjoint(never)


def test_families_cover_exactly_the_four_segments(rules):
    assert set(rules.families.keys()) == set(_SEGMENTS)
    for segment in _SEGMENTS:
        row = rules.families[segment]
        assert row["family"]
        assert row["second"]
        assert row["reason"]
    for segment, steps in rules.guided_paths.items():
        assert segment in _SEGMENTS
        assert steps


def test_two_part_suffixes_have_no_duplicates(rules):
    assert len(rules.two_part_suffixes) == len(set(rules.two_part_suffixes))


def test_remove_patterns_compile_and_person_block_present(rules):
    assert tuple(rules.remove_patterns.keys()) == page_rules.REMOVE_KEYS
    assert page_rules._compile_text_patterns(rules.data)["remove"]


def test_refused_domains_is_empty_in_repository(rules):
    assert rules.data["refused_domains.yml"]["domains"] == ()