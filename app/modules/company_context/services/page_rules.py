"""Load the six company-context rules files into frozen structures.

The data files under ``app/seed_data/company_context/`` are loaded once at
import into immutable structures (frozen dataclasses, tuples and mapping
proxy views, compiled regular expressions). Nothing here reads a tenant, a
database or a request; a change to the reading rules is a change to a data
file, reviewed as data.

``validate(rules)`` raises :class:`RulesError` naming the file and the key on
the first failure, so the schema test can assert each guard separately.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import MappingProxyType

import yaml

_DEFAULT_ROOT = Path(__file__).resolve().parents[3] / "seed_data" / "company_context"

REMOVE_KEYS = ("email", "phone", "postal_address_line")

# Words that must never appear as a rule, key, pattern or note in the matcher
# files (page_rules, segment_signals, template_families, tool_aliases). The
# removal list (personal_data_patterns.yml) is exempt by design. The guard
# matches the token anywhere in the guarded string, so a rule id that merely
# *contains* an owner/cost/contract/maturity/headcount/salary/person token is
# refused (FR-CX-17).
_FORBIDDEN_MATCHER_TOKENS = re.compile(
    r"(owner|cost|contract|maturity|headcount|salary|person)", re.IGNORECASE
)

# Connector types that exist in the product (app/models/connector_config.py:
# OrgConnectorConfig.connector_type in servicenow, jira, m365;
# DevOpsConnectorConfig.connector_type devops). A tool may claim the native
# rung only when its name normalises to one of these.
VALID_CONNECTOR_TYPES = frozenset({"servicenow", "jira", "m365", "devops"})

RUNG_VOCABULARY = frozenset({"byo_feed", "unified_ticketing", "native"})

SEGMENTS = ("S1", "S2", "S3", "S4")

# The thirteen FR-CX-11 (box key, element type, profile) rows of the page-class
# table, pinned as a mapping so the schema test and the loader agree on every
# zone row. The D-18 table copies these rows into each purpose's zone table.
PINNED_INTAKE_ROWS = (
    ("customer_segment", "Stakeholder", "customer_segment"),
    ("problem", "Driver", "problem"),
    ("value_proposition", "Value", "value_proposition"),
    ("channel", "BusinessInterface", "channel"),
    ("key_metric", "Outcome", "key_metric"),
    ("market_driver", "Driver", "market_driver"),
    ("objective", "Goal", "objective"),
    ("reason", "Driver", "reason"),
    ("solution_feature", "Requirement", "solution_feature"),
    ("revenue_model", "Value", "value_proposition"),
    ("customer_relationship", "BusinessService", "customer_relationship"),
    ("key_partner", "BusinessActor", "key_partner"),
    ("existing_alternative", "Assessment", "existing_alternative"),
)

PINNED_INTAKE_PAIR_BY_BOX = MappingProxyType(
    {box_key: (element_type, profile)
     for box_key, element_type, profile in PINNED_INTAKE_ROWS}
)

PINNED_INTAKE_PAIRS = frozenset(PINNED_INTAKE_PAIR_BY_BOX.values())


class RulesError(ValueError):
    """Raised when a rules file fails validation, naming file and key."""


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType(
            {k: _freeze(v) for k, v in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze(v) for v in value)
    return value


class Rules:
    """Frozen view over the six rules files.

    Every attribute is a tuple or mapping view set once at construction;
    assignment and deletion raise, so a loaded view can never change in
    process. ``compiled`` holds the compiled text-processing patterns.
    """

    __slots__ = (
        "_data",
        "compiled",
        "two_part_suffixes",
        "purposes",
        "purpose_by_key",
        "excluded_paths",
        "excluded_query_strings",
        "long_tail_vendors",
        "intake_zones",
        "never_sent",
        "pages_to_model_max",
        "segment_rules",
        "answer_rules",
        "families",
        "guided_paths",
        "tools",
        "tool_by_name",
        "status_page_providers",
        "marketplace_hosts",
        "remove_patterns",
        "person_name",
        "role_title_tokens",
        "organisation_tokens",
        "window_chars",
    )

    def __init__(self, data):
        object.__setattr__(self, "_data", _freeze(data))
        object.__setattr__(self, "compiled", _compile_text_patterns(data))
        object.__setattr__(
            self, "two_part_suffixes", tuple(data["page_rules.yml"]["two_part_suffixes"])
        )
        purposes = tuple(data["page_rules.yml"]["purposes"])
        object.__setattr__(self, "purposes", purposes)
        object.__setattr__(
            self,
            "purpose_by_key",
            MappingProxyType({p["key"]: p for p in purposes}),
        )
        object.__setattr__(
            self,
            "excluded_paths",
            tuple(data["page_rules.yml"].get("excluded_paths", ())),
        )
        object.__setattr__(
            self,
            "excluded_query_strings",
            bool(data["page_rules.yml"].get("excluded_query_strings", False)),
        )
        object.__setattr__(
            self,
            "long_tail_vendors",
            tuple(data["page_rules.yml"].get("long_tail_vendors", ())),
        )
        object.__setattr__(
            self,
            "intake_zones",
            tuple(data["page_rules.yml"].get("intake_zones", ())),
        )
        object.__setattr__(
            self, "never_sent", tuple(data["page_rules.yml"].get("never_sent", ()))
        )
        object.__setattr__(
            self,
            "pages_to_model_max",
            int(data["page_rules.yml"].get("pages_to_model_max", 12)),
        )
        object.__setattr__(
            self,
            "segment_rules",
            tuple(data["segment_signals.yml"].get("rules", ())),
        )
        object.__setattr__(
            self,
            "answer_rules",
            _freeze(data["segment_signals.yml"].get("answer_rules", {})),
        )
        object.__setattr__(
            self,
            "families",
            _freeze(data["template_families.yml"].get("families", {})),
        )
        guided = data["template_families.yml"].get("guided_paths", {})
        object.__setattr__(
            self,
            "guided_paths",
            MappingProxyType({k: tuple(v) for k, v in guided.items()}),
        )
        tools = tuple(data["tool_aliases.yml"].get("tools", ()))
        object.__setattr__(self, "tools", tools)
        object.__setattr__(
            self,
            "tool_by_name",
            MappingProxyType({t["tool"]: t for t in tools}),
        )
        object.__setattr__(
            self,
            "status_page_providers",
            tuple(data["tool_aliases.yml"].get("status_page_providers", ())),
        )
        object.__setattr__(
            self,
            "marketplace_hosts",
            tuple(data["tool_aliases.yml"].get("marketplace_hosts", ())),
        )
        remove = data["personal_data_patterns.yml"].get("remove", {})
        object.__setattr__(
            self,
            "remove_patterns",
            MappingProxyType(
                {k: self.compiled["remove"][k] for k in REMOVE_KEYS if k in remove}
            ),
        )
        object.__setattr__(self, "person_name", self.compiled.get("person_name"))
        object.__setattr__(
            self,
            "role_title_tokens",
            tuple(data["personal_data_patterns.yml"].get("role_title_tokens", ())),
        )
        object.__setattr__(
            self,
            "organisation_tokens",
            tuple(
                data["personal_data_patterns.yml"].get("organisation_tokens", ())
            ),
        )
        object.__setattr__(
            self,
            "window_chars",
            int(data["personal_data_patterns.yml"].get("window_chars", 60)),
        )

    def __setattr__(self, name, value):
        raise AttributeError(f"Rules is frozen: cannot set {name}")

    def __delattr__(self, name):
        raise AttributeError(f"Rules is frozen: cannot delete {name}")

    @property
    def data(self):
        return self._data


def _compile_text_patterns(data):
    compiled = {}
    remove = data["personal_data_patterns.yml"].get("remove", {})
    compiled["remove"] = {
        key: re.compile(pattern, re.IGNORECASE)
        for key, pattern in remove.items()
    }
    if data["personal_data_patterns.yml"].get("person_name"):
        compiled["person_name"] = re.compile(
            data["personal_data_patterns.yml"]["person_name"]
        )
    return compiled


def _normalise_tool_name(name):
    return name.strip().lower().replace(" ", "_")


# Locations whose string values the FR-CX-17 guard must check: file keys,
# rule ids, patterns, notes and matcher tokens.
_GUARDED_LOCATIONS = (
    ("page_rules.yml", ("purposes", "path_patterns", "link_text_patterns")),
    ("segment_signals.yml", ("rules", "rule_id", "note", "matcher")),
    ("template_families.yml", ("families", "reason", "guided_paths", "title")),
    ("tool_aliases.yml", ("tools", "tool", "aliases", "category", "rung", "pack_key")),
)


def _guarded_strings(data):
    """Yield ``(file_key, location, string)`` triples for every guarded value."""
    for file_key, locations in _GUARDED_LOCATIONS:
        block = data.get(file_key, {})
        for location in locations:
            if location == "purposes":
                value = block.get("purposes", [])
            elif location == "rules":
                value = block.get("rules", [])
            elif location == "families":
                value = block.get("families", {})
            elif location == "guided_paths":
                value = block.get("guided_paths", {})
            elif location == "tools":
                value = block.get("tools", [])
            else:
                value = None

            if location == "purposes":
                for purpose in value:
                    yield file_key, "purposes.key", purpose.get("key", "")
                    for pattern in purpose.get("path_patterns", []) + purpose.get(
                        "link_text_patterns", []
                    ):
                        yield file_key, "purposes.pattern", pattern
                    for path in purpose.get("conventional_paths", []):
                        yield file_key, "purposes.conventional_path", path
            elif location == "rules":
                for rule in value:
                    yield file_key, "rules.rule_id", rule.get("rule_id", "")
                    yield file_key, "rules.note", rule.get("note", "")
                    matcher = rule.get("matcher", {})
                    if isinstance(matcher, dict):
                        for key in matcher:
                            yield file_key, "rules.matcher.key", key
                        for token in matcher.get("any_of", []):
                            yield file_key, "rules.matcher.token", token
            elif location == "families":
                for key, row in value.items():
                    yield file_key, "families.key", key
                    if isinstance(row, dict):
                        for k, v in row.items():
                            yield file_key, f"families.{key}.{k}", str(k)
                            if isinstance(v, str):
                                yield file_key, f"families.{key}.{k}", v
            elif location == "guided_paths":
                for key, steps in value.items():
                    yield file_key, "guided_paths.key", key
                    for step in steps:
                        yield file_key, "guided_paths.step_key", step.get("key", "")
                        yield file_key, "guided_paths.title", step.get("title", "")
                        yield file_key, "guided_paths.needs", str(step.get("needs", ""))
            elif location == "tools":
                for tool in value:
                    yield file_key, "tools.tool", tool.get("tool", "")
                    for alias in tool.get("aliases", []):
                        yield file_key, "tools.alias", alias
                    for host in tool.get("status_page_hosts", []) + tool.get(
                        "marketplace_hosts", []
                    ):
                        yield file_key, "tools.host", host
                    yield file_key, "tools.rung", tool.get("rung", "")
                    yield file_key, "tools.pack_key", tool.get("pack_key", "")
            else:
                continue


def _validate_no_forbidden_words(data):
    for file_key, location, string in _guarded_strings(data):
        if _FORBIDDEN_MATCHER_TOKENS.search(string):
            _raise(
                file_key,
                location,
                f"forbidden word in {string!r}",
            )


def _raise(file_key, key, message):
    raise RulesError(f"{file_key}: {key}: {message}")


def _validate_page_rules(data):
    page_rules = data.get("page_rules.yml", {})
    if not isinstance(page_rules, dict):
        _raise("page_rules.yml", "<root>", "expected a mapping")

    purposes = page_rules.get("purposes")
    if not isinstance(purposes, list) or not purposes:
        _raise("page_rules.yml", "purposes", "expected a non-empty list")
    seen = set()
    for purpose in purposes:
        key = purpose.get("key")
        if not key or not isinstance(key, str):
            _raise("page_rules.yml", "purposes", "every purpose has a string key")
        if key in seen:
            _raise("page_rules.yml", f"purposes.{key}", "duplicate purpose key")
        seen.add(key)
        patterns = purpose.get("path_patterns", []) + purpose.get(
            "link_text_patterns", []
        )
        if not patterns:
            _raise(
                "page_rules.yml",
                f"purposes.{key}",
                "at least one path or link-text pattern required",
            )
        for pattern in patterns:
            try:
                re.compile(pattern, re.IGNORECASE)
            except re.error as exc:
                _raise(
                    "page_rules.yml",
                    f"purposes.{key}",
                    f"pattern {pattern!r} does not compile: {exc}",
                )
        if not isinstance(purpose.get("max_pages"), int) or purpose["max_pages"] < 1:
            _raise("page_rules.yml", f"purposes.{key}.max_pages", "expected a positive int")

    suffixes = page_rules.get("two_part_suffixes")
    if not isinstance(suffixes, list) or not all(
        isinstance(s, str) and len(s) >= 4 for s in suffixes
    ):
        _raise("page_rules.yml", "two_part_suffixes", "expected strings of length >= 4")
    if len(set(suffixes)) != len(suffixes):
        _raise("page_rules.yml", "two_part_suffixes", "no duplicates allowed")

    excluded = page_rules.get("excluded_paths", [])
    if not isinstance(excluded, list) or not all(
        isinstance(p, str) for p in excluded
    ):
        _raise("page_rules.yml", "excluded_paths", "expected a list of strings")

    zones = page_rules.get("intake_zones", [])
    if not isinstance(zones, list):
        _raise("page_rules.yml", "intake_zones", "expected a list")
    for zone in zones:
        purpose_key = zone.get("purpose")
        if purpose_key not in seen:
            _raise("page_rules.yml", f"intake_zones.{purpose_key}", "unknown purpose")
        calls = zone.get("calls")
        if not isinstance(calls, list) or not all(
            c in ("A", "B") for c in calls
        ):
            _raise(
                "page_rules.yml",
                f"intake_zones.{purpose_key}.calls",
                "expected a list of A and B",
            )
        boxes = zone.get("boxes")
        if not isinstance(boxes, list) or not boxes:
            _raise(
                "page_rules.yml",
                f"intake_zones.{purpose_key}.boxes",
                "expected a non-empty list",
            )
        for box in boxes:
            box_key = box.get("box_key")
            expected = PINNED_INTAKE_PAIR_BY_BOX.get(box_key)
            if expected is None:
                _raise(
                    "page_rules.yml",
                    f"intake_zones.{purpose_key}.boxes.{box_key}",
                    "unknown box key (not one of the thirteen FR-CX-11 rows)",
                )
            pair = (box.get("element_type"), box.get("profile"))
            if pair != expected:
                _raise(
                    "page_rules.yml",
                    f"intake_zones.{purpose_key}.boxes.{box_key}",
                    f"pair {pair} is not the pinned FR-CX-11 pair for {box_key}: {expected}",
                )


def _validate_segment_signals(data, purpose_keys):
    signals = data.get("segment_signals.yml", {})
    rules = signals.get("rules", [])
    if not isinstance(rules, list):
        _raise("segment_signals.yml", "rules", "expected a list")
    for rule in rules:
        rule_id = rule.get("rule_id")
        if not rule_id or not isinstance(rule_id, str):
            _raise("segment_signals.yml", "rules", "every rule has a string id")
        if _FORBIDDEN_MATCHER_TOKENS.search(rule_id):
            _raise("segment_signals.yml", f"rules.{rule_id}", "forbidden word in rule id")
        purpose = rule.get("purpose")
        if purpose not in purpose_keys:
            _raise("segment_signals.yml", f"rules.{rule_id}.purpose", "unknown purpose")
        segment = rule.get("segment")
        if segment not in SEGMENTS:
            _raise(
                "segment_signals.yml",
                f"rules.{rule_id}.segment",
                "segment must be one of S1..S4",
            )
        matcher = rule.get("matcher", {})
        kind = matcher.get("kind")
        if kind not in (
            "phrase",
            "numeric_statement",
            "open_roles_count",
            "page_present",
            "links_out_host",
        ):
            _raise(
                "segment_signals.yml",
                f"rules.{rule_id}.matcher.kind",
                "unknown matcher kind",
            )
        if kind == "phrase":
            any_of = matcher.get("any_of")
            if not isinstance(any_of, list) or not all(
                isinstance(a, str) and len(a) >= 3 for a in any_of
            ):
                _raise(
                    "segment_signals.yml",
                    f"rules.{rule_id}.matcher.any_of",
                    "expected strings of length >= 3",
                )
            for token in any_of:
                if _FORBIDDEN_MATCHER_TOKENS.search(token):
                    _raise(
                        "segment_signals.yml",
                        f"rules.{rule_id}.matcher.any_of",
                        "forbidden word in phrase token",
                    )
        elif kind == "numeric_statement":
            pattern = matcher.get("pattern")
            try:
                re.compile(pattern)
            except (re.error, TypeError) as exc:
                _raise(
                    "segment_signals.yml",
                    f"rules.{rule_id}.matcher.pattern",
                    f"pattern does not compile: {exc}",
                )
            if "min" not in matcher and "max" not in matcher:
                _raise(
                    "segment_signals.yml",
                    f"rules.{rule_id}.matcher",
                    "numeric_statement needs min or max",
                )
        elif kind == "page_present":
            if matcher.get("purpose") not in purpose_keys:
                _raise(
                    "segment_signals.yml",
                    f"rules.{rule_id}.matcher.purpose",
                    "unknown purpose",
                )
        elif kind == "links_out_host":
            host_list = matcher.get("host_list")
            if not isinstance(host_list, list) or not host_list:
                _raise(
                    "segment_signals.yml",
                    f"rules.{rule_id}.matcher.host_list",
                    "expected a non-empty list",
                )

    answers = signals.get("answer_rules", {})
    for stage in answers.get("stage", []):
        if not isinstance(stage, str):
            _raise("segment_signals.yml", "answer_rules.stage", "expected strings")
    for size in answers.get("size", []):
        if not isinstance(size, str):
            _raise("segment_signals.yml", "answer_rules.size", "expected strings")
    for industry in answers.get("industry_kind", []):
        if not isinstance(industry, str):
            _raise("segment_signals.yml", "answer_rules.industry_kind", "expected strings")
    for answ in answers.get("rules", []):
        segment = answ.get("segment")
        if segment not in SEGMENTS:
            _raise(
                "segment_signals.yml",
                "answer_rules.rules",
                "segment must be one of S1..S4",
            )


def _validate_template_families(data):
    families = data.get("template_families.yml", {})
    fam = families.get("families", {})
    for segment in SEGMENTS:
        row = fam.get(segment)
        if not row or not isinstance(row, dict):
            _raise(
                "template_families.yml",
                f"families.{segment}",
                "expected a mapping",
            )
    if set(fam.keys()) != set(SEGMENTS):
        _raise(
            "template_families.yml",
            "families",
            "families must cover exactly S1..S4",
        )
    guided = families.get("guided_paths", {})
    for segment in SEGMENTS:
        if segment not in guided:
            _raise("template_families.yml", f"guided_paths.{segment}", "missing segment")
        for step in guided[segment]:
            if step.get("needs") not in ("packs", "canvas", "none"):
                _raise(
                    "template_families.yml",
                    f"guided_paths.{segment}.{step.get('key')}.needs",
                    "needs must be packs, canvas or none",
                )
            if not isinstance(step.get("title"), str) or not step["title"]:
                _raise(
                    "template_families.yml",
                    f"guided_paths.{segment}.{step.get('key')}.title",
                    "expected a non-empty string",
                )


def _validate_tool_aliases(data):
    tools = data.get("tool_aliases.yml", {}).get("tools", [])
    if not isinstance(tools, list):
        _raise("tool_aliases.yml", "tools", "expected a list")
    seen = set()
    for tool in tools:
        name = tool.get("tool")
        if not name or not isinstance(name, str):
            _raise("tool_aliases.yml", "tools", "every tool has a string name")
        if name in seen:
            _raise("tool_aliases.yml", f"tools.{name}", "duplicate tool name")
        seen.add(name)
        aliases = tool.get("aliases", [])
        if not isinstance(aliases, list) or not all(
            isinstance(a, str) and len(a) >= 3 for a in aliases
        ):
            _raise(
                "tool_aliases.yml",
                f"tools.{name}.aliases",
                "expected strings of length >= 3",
            )
        rung = tool.get("rung")
        if rung not in RUNG_VOCABULARY:
            _raise(
                "tool_aliases.yml",
                f"tools.{name}.rung",
                "rung must be one of byo_feed | unified_ticketing | native",
            )
        if rung == "native":
            if _normalise_tool_name(name) not in VALID_CONNECTOR_TYPES:
                _raise(
                    "tool_aliases.yml",
                    f"tools.{name}.rung",
                    f"native claimed for a tool without a product connector type "
                    f"({sorted(VALID_CONNECTOR_TYPES)})",
                )
        for host in tool.get("status_page_hosts", []) + tool.get(
            "marketplace_hosts", []
        ):
            if not isinstance(host, str):
                _raise(
                    "tool_aliases.yml",
                    f"tools.{name}",
                    "host entries must be strings",
                )





def validate(data):
    """Validate the loaded mapping; raise :class:`RulesError` on first failure."""
    purpose_keys = {
        p.get("key")
        for p in data.get("page_rules.yml", {}).get("purposes", [])
    }
    _validate_page_rules(data)
    _validate_segment_signals(data, purpose_keys)
    _validate_template_families(data)
    _validate_tool_aliases(data)
    _validate_no_forbidden_words(data)
    return True


def load_rules(root=_DEFAULT_ROOT):
    """Load and validate the six data files under ``root``.

    Returns a :class:`Rules` instance. Raises a :class:`RulesError` naming the
    file and key when a file is missing, unparsable or fails validation.
    """
    data = {}
    for file_key in (
        "page_rules.yml",
        "segment_signals.yml",
        "template_families.yml",
        "tool_aliases.yml",
        "personal_data_patterns.yml",
        "refused_domains.yml",
    ):
        path = Path(root) / file_key
        if not path.exists():
            _raise(file_key, "<file>", "missing data file")
        try:
            with open(path, encoding="utf-8") as handle:
                data[file_key] = yaml.safe_load(handle)
        except yaml.YAMLError as exc:
            _raise(file_key, "<file>", f"unparsable YAML: {exc}")
    validate(data)
    return Rules(data)


#: The rules, loaded once at import. Nothing mutates them afterwards.
RULES = load_rules()


def refused_domains():
    """The repository refusals only (the instance file is read at run start)."""
    data = RULES.data.get("refused_domains", {})
    return tuple(data.get("domains", ()))