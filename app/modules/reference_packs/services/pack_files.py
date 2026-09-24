"""Reference pack file format: reading, structural validation and the closed
licence vocabulary (decision B). One module owns this so the static tests, the
loader and the fold script share a single reader and a single set of rules."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from app.config.apqc_capability_mapping_rules import APQCCapabilityMappingRulesConfig
from app.config.archimate_relationship_matrix import (
    ALL_ELEMENTS,
    RELATIONSHIP_TYPES,
    RelationshipValidator,
    get_element_layer,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_ROOT = REPO_ROOT / "app" / "seed_data" / "reference_packs"

# Licence identifiers — a closed vocabulary (decision B).
OPEN_LICENCES = ("apqc-pcf", "cc-by-4.0", "cc-by-sa-4.0", "mit", "apache-2.0")
LICENCE_VOCABULARY = OPEN_LICENCES + ("proprietary-facts-only", "product-licence")

# FR-P15 (c) — the exact sentence every pack with capability_mappings must carry.
APQC_ATTRIBUTION_SENTENCE = (
    "Process names and codes are from the APQC Process Classification Framework, "
    "used under its open licence."
)

# FR-P15 — the exact sentence every pack carries.
TRADEMARK_LINE = (
    "Product and company names are the property of their respective owners; "
    "no affiliation or endorsement is implied."
)

# FR-P14 — no owner, cost, maturity, date or amount in any pack.
FORBIDDEN_KEYS = {
    "owner",
    "owner_id",
    "cost",
    "annual_cost",
    "maturity",
    "current_maturity_level",
    "target_maturity_level",
    "date",
    "start_date",
    "end_date",
    "amount",
    "budget",
    "licence_cost",
}

PLACEHOLDER_RE = re.compile(r"(lorem|ipsum|example|placeholder|todo|tbd|xxx)", re.IGNORECASE)
PACK_KEY_RE = re.compile(r"^[a-z0-9-]+$")
ELEMENT_SLUG_RE = re.compile(r"^[a-z0-9-]+$")
PACK_VERSION_RE = re.compile(r"^\d+\.\d+$")
PCF_CODE_RE = re.compile(r"^\d+(\.\d+)*$")
URL_RE = re.compile(r"^https://")

LAYER_NAMES = {
    "strategy",
    "business",
    "application",
    "technology",
    "physical",
    "motivation",
    "implementation",
    "other",
}

_OTHER_TYPES = {"Grouping", "Location", "Junction"}


class PackValidationError(ValueError):
    """Raised (and collected) when a pack file fails a static check."""

    def __init__(self, pack_key: str, messages: list[str]):
        self.pack_key = pack_key
        self.messages = messages
        super().__init__(f"{pack_key}: {'; '.join(messages)}")


def discover_pack_files(root: Path | None = None) -> list[Path]:
    """Every ``<pack_key>/<version>.yml`` under ``root``, sorted for determinism."""
    root = root or DEFAULT_ROOT
    if not root.exists():
        return []
    found = []
    for pack_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for yml in sorted(pack_dir.glob("*.yml")):
            if yml.name == "sources.yml":
                continue
            found.append(yml)
    return found


def load_pack_file(path: Path) -> dict[str, Any]:
    """Read one pack file plus its sibling ``sources.yml``. Raises PackValidationError
    if either is missing or malformed; never a half-read pack (decision D)."""
    pack_key_from_dir = path.parent.name
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except yaml.YAMLError as exc:
        raise PackValidationError(pack_key_from_dir, [f"invalid YAML in {path.name}: {exc}"])

    sources_path = path.parent / "sources.yml"
    if not sources_path.exists():
        raise PackValidationError(pack_key_from_dir, ["missing sources.yml"])
    try:
        with open(sources_path, "r", encoding="utf-8") as fh:
            sources_data = yaml.safe_load(fh) or {}
    except yaml.YAMLError as exc:
        raise PackValidationError(pack_key_from_dir, [f"invalid YAML in sources.yml: {exc}"])

    data["_sources_doc"] = sources_data
    data["_path"] = path
    data["_sources_path"] = sources_path
    return data


def _word_count(text: str) -> int:
    return len(text.split())


def validate_pack(pack: dict[str, Any]) -> list[str]:
    """Run every static check of decision C against one loaded pack dict.
    Returns a list of human-readable defects; empty means the pack is valid."""
    errors: list[str] = []
    elements = pack.get("elements") or []
    relationships = pack.get("relationships") or []
    modules = pack.get("modules") or []
    capability_mappings = pack.get("capability_mappings") or []
    sources_doc = pack.get("_sources_doc") or {}
    source_entries = sources_doc.get("sources") or []

    # (8) pack_key / element_key / pack_version shape
    if not pack.get("pack_key") or len(pack["pack_key"]) > 24 or not PACK_KEY_RE.match(pack["pack_key"]):
        errors.append(f"pack_key '{pack.get('pack_key')}' invalid or over 24 characters")
    if not pack.get("pack_version") or not PACK_VERSION_RE.match(str(pack["pack_version"])):
        errors.append(f"pack_version '{pack.get('pack_version')}' does not match ^\\d+\\.\\d+$")

    element_keys_seen: set[str] = set()
    element_by_key: dict[str, dict] = {}
    for el in elements:
        ek = el.get("element_key")
        if not ek:
            errors.append("element with no element_key")
            continue
        if ek in element_keys_seen:
            errors.append(f"duplicate element_key '{ek}'")
        element_keys_seen.add(ek)
        element_by_key[ek] = el

    module_keys_declared = {m.get("key") for m in modules if m.get("key")}

    validator = RelationshipValidator()

    # (1) element type / layer
    for el in elements:
        etype = el.get("type")
        layer = el.get("layer")
        if etype not in ALL_ELEMENTS:
            errors.append(f"element '{el.get('element_key')}' has unknown type '{etype}'")
            continue
        if etype in _OTHER_TYPES:
            expected_layer = "other"
        else:
            matrix_layer = get_element_layer(etype)
            expected_layer = matrix_layer.lower() if matrix_layer else None
        if layer != expected_layer:
            errors.append(
                f"element '{el.get('element_key')}' has layer '{layer}', expected '{expected_layer}'"
            )

        # (10) description word count
        description = el.get("description") or ""
        if _word_count(description) > 60:
            errors.append(f"element '{el.get('element_key')}' description exceeds 60 words")

        # (6) forbidden keys and placeholder text
        for key in el.keys():
            if key in FORBIDDEN_KEYS:
                errors.append(f"element '{el.get('element_key')}' carries forbidden key '{key}'")
        props = el.get("properties") or {}
        for key in props.keys():
            if key in FORBIDDEN_KEYS:
                errors.append(
                    f"element '{el.get('element_key')}' properties carries forbidden key '{key}'"
                )
        for field_name in ("name", "description"):
            value = el.get(field_name) or ""
            if PLACEHOLDER_RE.search(value):
                errors.append(f"element '{el.get('element_key')}' {field_name} matches placeholder pattern")

        # (5) source_urls present, and every entry in sources.yml
        el_source_urls = el.get("source_urls") or []
        if not el_source_urls:
            errors.append(f"element '{el.get('element_key')}' has no source_urls")
        known_urls = {s.get("url") for s in source_entries}
        for url in el_source_urls:
            if url not in known_urls:
                errors.append(f"element '{el.get('element_key')}' source_url '{url}' not in sources.yml")

    # (3) modules: every element in >=1 module, every module key in modules[],
    # every module has >=1 element
    module_element_counts: dict[str, int] = {m: 0 for m in module_keys_declared}
    for el in elements:
        el_modules = el.get("module_keys") or []
        if not el_modules:
            errors.append(f"element '{el.get('element_key')}' belongs to no module")
        for mk in el_modules:
            if mk not in module_keys_declared:
                errors.append(f"element '{el.get('element_key')}' references undeclared module '{mk}'")
            else:
                module_element_counts[mk] = module_element_counts.get(mk, 0) + 1
    for mk, count in module_element_counts.items():
        if count == 0:
            errors.append(f"module '{mk}' has no elements")

    # (2) + relationship-level checks
    element_relationship_modules: dict[str, set[str]] = {ek: set() for ek in element_keys_seen}
    seen_triples: set[tuple[str, str, str]] = set()
    for rel in relationships:
        src = rel.get("source_key")
        tgt = rel.get("target_key")
        rtype = rel.get("type")
        rel_modules = rel.get("module_keys") or []
        if rtype not in RELATIONSHIP_TYPES:
            errors.append(f"relationship {src}->{tgt} has unknown type '{rtype}'")
            continue
        if src == tgt:
            errors.append(f"relationship '{src}'->'{tgt}' is a self-reference")
            continue
        src_el = element_by_key.get(src)
        tgt_el = element_by_key.get(tgt)
        if src_el is None:
            errors.append(f"relationship source_key '{src}' does not resolve to an element")
            continue
        if tgt_el is None:
            errors.append(f"relationship target_key '{tgt}' does not resolve to an element")
            continue
        if not validator.validate(src_el["type"], tgt_el["type"], rtype):
            errors.append(
                f"relationship '{src}' --{rtype}--> '{tgt}' "
                f"({src_el['type']} -> {tgt_el['type']}) is refused by the matrix"
            )
        triple = (src, tgt, rtype)
        if triple in seen_triples:
            errors.append(f"duplicate relationship {triple}")
        seen_triples.add(triple)
        for mk in rel_modules:
            if mk not in module_keys_declared:
                errors.append(f"relationship {src}->{tgt} references undeclared module '{mk}'")
        element_relationship_modules.setdefault(src, set()).update(rel_modules)
        element_relationship_modules.setdefault(tgt, set()).update(rel_modules)

    for el in elements:
        ek = el.get("element_key")
        el_modules = set(el.get("module_keys") or [])
        rel_modules = element_relationship_modules.get(ek, set())
        if not el_modules & rel_modules:
            errors.append(
                f"element '{ek}' has no relationship whose module_keys intersect its own"
            )

    # (4) capability mappings
    for cm in capability_mappings:
        level = cm.get("level")
        pcf_code = cm.get("pcf_code", "")
        if level not in APQCCapabilityMappingRulesConfig.LEVEL_MAPPING_RULES:
            errors.append(f"capability mapping level '{level}' is not a known APQC level")
            continue
        if not PCF_CODE_RE.match(str(pcf_code)):
            errors.append(f"capability mapping pcf_code '{pcf_code}' does not match ^\\d+(\\.\\d+)*$")
            continue
        depth = str(pcf_code).count(".") + 1
        if depth != level:
            errors.append(
                f"capability mapping pcf_code '{pcf_code}' has depth {depth}, expected {level}"
            )
        el_key = cm.get("element_key")
        if el_key and el_key not in element_by_key:
            errors.append(f"capability mapping element_key '{el_key}' does not resolve to an element")

    # (5) sources.yml shape
    if not source_entries:
        errors.append("sources.yml has no entries")
    for entry in source_entries:
        if not entry.get("url") or not URL_RE.match(entry["url"]):
            errors.append(f"source entry has no https:// url: {entry}")
        if not entry.get("read_at"):
            errors.append(f"source entry has no read_at: {entry}")
        licence = entry.get("licence")
        if licence not in LICENCE_VOCABULARY:
            errors.append(f"source entry licence '{licence}' not in the licence vocabulary")

    if not (pack.get("attribution_text") or "").strip():
        errors.append("attribution_text is empty")
    if pack.get("trademark_line") != TRADEMARK_LINE:
        errors.append("trademark_line does not match the FR-P15 sentence byte for byte")

    # (7) APQC sentence required whenever capability_mappings is non-empty
    if capability_mappings and APQC_ATTRIBUTION_SENTENCE not in (pack.get("attribution_text") or ""):
        errors.append("capability_mappings present but attribution_text lacks the APQC sentence")

    # (9) word-window test — 21-word windows of descriptions must not appear
    # verbatim in a non-open-licence source's text_file.
    skipped_no_text_file = 0
    for entry in source_entries:
        licence = entry.get("licence")
        if licence in OPEN_LICENCES:
            continue
        text_file = entry.get("text_file")
        if not text_file:
            skipped_no_text_file += 1
            continue
        text_path = pack["_path"].parent / "sources" / text_file
        if not text_path.exists():
            errors.append(f"sources.yml names text_file '{text_file}' that does not exist")
            continue
        haystack_words = text_path.read_text(encoding="utf-8").lower().split()
        haystack_windows = {
            " ".join(haystack_words[i : i + 21])
            for i in range(0, max(0, len(haystack_words) - 20))
        }
        for el in elements:
            words = [w.lower() for w in (el.get("description") or "").split()]
            for i in range(0, max(0, len(words) - 20)):
                window = " ".join(words[i : i + 21])
                if window and window in haystack_windows:
                    errors.append(
                        f"element '{el.get('element_key')}' description shares a 21-word window "
                        f"with a non-open source ({entry.get('url')})"
                    )
                    break
    pack["_word_window_skipped_no_text_file"] = skipped_no_text_file

    pack_key = pack.get("pack_key") or (pack["_path"].parent.name if pack.get("_path") else "?")
    return [f"{pack_key}: {e}" for e in errors]


def compute_pack_content(pack: dict[str, Any]) -> dict[str, Any]:
    """The document decision A stores as ``content`` — every pack-level key
    except the record's own columns (pack_key, pack_version, vendor, product,
    product_edition, segment_tags, industry_tags, attribution_text,
    trademark_line, plus the file-loader bookkeeping keys)."""
    excluded = {
        "pack_key",
        "pack_version",
        "vendor",
        "product",
        "product_edition",
        "segment_tags",
        "industry_tags",
        "attribution_text",
        "trademark_line",
        "_sources_doc",
        "_path",
        "_sources_path",
        "_word_window_skipped_no_text_file",
    }
    return {k: v for k, v in pack.items() if k not in excluded}
