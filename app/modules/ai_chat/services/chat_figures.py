"""Figures in a chat answer: declared, referenced, resolved and rendered by the engine.

The engine never decides whether a number the model wrote is true. A figure reaches a
person only as the engine's rendering of a value that a declared field returned in the
same turn, for the signed-in organisation. The model writes ``[fig:F3]``; the engine
resolves the key against this turn's figure table and shows the declared phrase. A digit
or number word the model writes outside the six forms of ``scan_written_numbers`` is a
violation, whatever it says: nothing here matches a prose number to a tool number.

Used by ``Grounding`` (the table), ``find_uncited_claims`` (violations), the agent
runner (revision, fallback, envelope). The one formatter is
``intelligence.services.figure_format``.
"""

from __future__ import annotations

import copy
import functools
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.modules.intelligence.services import figure_format

PROMPT_VERSION = "figures-v1"
FIG_REF = re.compile(r"\[fig:\s*([^\]\s]*)\s*\]", re.IGNORECASE)
_FIG_KEY = re.compile(r"^F\d+$")
_RAW_FIG = re.compile(r"\[fig:", re.IGNORECASE)
REF = re.compile(r"\[ref:([^\]\s]+)\]", re.IGNORECASE)

ANNOTATION_TOKEN_BUDGET = 3000
ANNOTATION_TOKENS_PER_FIGURE = 10
FULL_ROWS_PER_RESULT = 25

REASON_WRITTEN = "written_number"
REASON_UNKNOWN = "unknown_reference"
REASON_OTHER_ORG = "other_organisation"
REASON_UNBOUND = "unbound"
REASONS = (REASON_WRITTEN, REASON_UNKNOWN, REASON_OTHER_ORG, REASON_UNBOUND)

PLAIN_LINE = (
    "I could not confirm one or more figures in this answer from the records I "
    "read, so I left them out."
)
RECORDS_HEADING = "What the records show:"
NEXT_STEP = "Ask me to list the records behind this question to see the exact figure."


# --------------------------------------------------------------------------- #
# The figure table of a turn                                                   #
# --------------------------------------------------------------------------- #


def _tokens(path: str) -> List[str]:
    out: List[str] = []
    for part in path.split("."):
        if part.endswith("[]"):
            if part[:-2]:
                out.append(part[:-2])
            out.append("[]")
        else:
            out.append(part)
    return out


def _leaves(node, tokens, row=None):
    """``(parent dict, key, row item)`` for each place *tokens* reach inside *node*."""
    if not tokens:
        return
    head, rest = tokens[0], tokens[1:]
    if head == "[]":
        if isinstance(node, list):
            for item in node:
                yield from _leaves(item, rest, item)
        return
    if not isinstance(node, dict) or head not in node:
        return
    if not rest:
        yield node, head, row
        return
    yield from _leaves(node[head], rest, row)


def _get(node, path: str):
    for token in _tokens(path):
        if token == "[]" or not isinstance(node, dict) or token not in node:
            return None
        node = node[token]
    return node


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


_NUMERIC_KINDS = ("count", "money", "score", "percent", "ratio", "duration", "rank")


def _fits_kind(kind: str, value) -> bool:
    if kind in _NUMERIC_KINDS:
        return _is_number(value)
    if kind == "year":
        return _is_number(value) and float(value) == int(value)
    return isinstance(value, str) and bool(value.strip()) or hasattr(value, "isoformat")


def _escape_fig_tokens(value):
    """Data cannot plant a reference: any ``[fig:`` inside a string is broken."""
    if isinstance(value, str):
        return re.sub(r"\[fig:", "[ fig:", value, flags=re.IGNORECASE)
    if isinstance(value, list):
        return [_escape_fig_tokens(v) for v in value]
    if isinstance(value, dict):
        return {k: _escape_fig_tokens(v) for k, v in value.items()}
    return value


class FigureTable:
    """One entry for each declared figure of the organisation summary and of every
    tool result of one turn, in the order they arrive. Built per run and never shared
    between runs or carried to another turn."""

    def __init__(self, organization_id=None) -> None:
        self.organization_id = organization_id
        self.entries: List[Dict[str, Any]] = []
        self.names: set = set()
        self.calls = 0
        self.annotation_tokens = 0
        self._sequence = 0

    @staticmethod
    def escaped(result):
        """*result* as the model sees a result with no figures: any ``[fig:`` broken."""
        return _escape_fig_tokens(copy.deepcopy(result))

    # -- reading ----------------------------------------------------------- #

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        key = str(key).upper()
        return next((e for e in self.entries if e["key"] == key), None)

    def summary_entries(self) -> List[Dict[str, Any]]:
        return [e for e in self.entries if e["tool"] == "organisation_summary"]

    def entries_of_record(self, record_id=None, label=None) -> List[Dict[str, Any]]:
        out = []
        for entry in self.entries:
            if record_id is not None and entry.get("record_id") is not None \
                    and str(entry["record_id"]) == str(record_id):
                out.append(entry)
            elif label and entry.get("record_id") is None and entry.get("label") \
                    and entry["label"].strip().lower() == label.strip().lower():
                out.append(entry)
        return out

    # -- building ---------------------------------------------------------- #

    def _next_key(self) -> str:
        self._sequence += 1
        return "F%d" % self._sequence

    def _unit_of(self, field: Dict[str, Any], parent: dict, organization_id) -> Dict[str, Any]:
        unit: Dict[str, Any] = {}
        kind = field["kind"]
        if kind == "money":
            if field.get("currency_field"):
                unit["currency"] = parent.get(field["currency_field"])
            else:
                from app.services.application_cost_accessor import get_reporting_currency

                unit["currency"] = get_reporting_currency(organization_id)
        elif kind == "score":
            unit["out_of"] = field["out_of"] if field.get("out_of") else parent.get(field.get("out_of_field"))
        elif kind == "duration":
            unit["unit"] = field["unit"]
        elif kind == "rank":
            unit["of"] = parent.get(field.get("of_field"))
        return unit

    def annotate(self, tool: str, declaration: Optional[dict], result: Any,
                 organization_id=None, as_of=None) -> Any:
        """Add the declared figures of *result* to the table and return the copy the
        model sees: each declared value replaced by ``{"fig": key, "reads": phrase}``,
        everything else as returned, any ``[fig:`` in a string broken."""
        self.calls += 1
        call = self.calls
        shown = _escape_fig_tokens(copy.deepcopy(result))
        declaration = declaration or {}
        for key in declaration.get("names") or ():
            self._collect_names(result, key)
        rows = {r["path"]: r for r in declaration.get("rows", [])}
        for row in rows.values():
            for field_name in (row.get("name"), row.get("label_field")):
                if field_name:
                    self._collect_names(result, field_name)
        subject = self._subject_of(declaration.get("subject"), result)
        for field in declaration.get("fields", []):
            when = field.get("when") or {}
            if any(_get(result, path) != wanted for path, wanted in when.items()):
                continue
            tokens = _tokens(field["path"])
            prefix = field["path"][: field["path"].rfind("[]") + 2] if "[]" in field["path"] else None
            row_meta = rows.get(prefix) if prefix else None
            originals = list(_leaves(result, tokens))
            copies = list(_leaves(shown, tokens))
            for index, ((parent, name, row), (shown_parent, shown_name, _row)) in enumerate(zip(originals, copies)):
                value = parent.get(name)
                if value is not None and not _fits_kind(field["kind"], value):
                    continue
                entry = self._entry(tool, call, field, value, parent, row, row_meta, subject,
                                    organization_id, as_of)
                if entry is None:
                    continue
                self.entries.append(entry)
                self.annotation_tokens += ANNOTATION_TOKENS_PER_FIGURE
                full = not (self.annotation_tokens > ANNOTATION_TOKEN_BUDGET and index >= FULL_ROWS_PER_RESULT)
                shown_parent[shown_name] = (
                    {"fig": entry["key"], "reads": entry["rendered"]} if full else {"fig": entry["key"]}
                )
        return shown

    def _collect_names(self, node, key: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if k == key and isinstance(v, str) and v.strip():
                    self.names.add(v.strip())
                else:
                    self._collect_names(v, key)
        elif isinstance(node, list):
            for item in node:
                self._collect_names(item, key)

    @staticmethod
    def _subject_of(spec, result) -> Optional[Dict[str, Any]]:
        if not spec:
            return None
        if spec.get("map"):
            mapping = _get(result, spec["map"])
            if isinstance(mapping, dict):
                for ident, element in mapping.items():
                    if isinstance(element, dict) and isinstance(element.get("name"), str):
                        return {"id": ident, "name": element["name"]}
            return None
        ident, name = _get(result, spec.get("id", "")), _get(result, spec.get("name", ""))
        return {"id": ident, "name": name} if name else None

    def _entry(self, tool, call, field, value, parent, row, row_meta, subject, organization_id, as_of):
        record_id = record_name = label = None
        if row_meta and isinstance(row, dict):
            if row_meta.get("id"):
                record_id = row.get(row_meta["id"])
                record_name = row.get(row_meta.get("name"))
            else:
                label = row.get(row_meta["label_field"])
        elif subject:
            record_id, record_name = subject["id"], subject["name"]
        entry = {
            "key": self._next_key(),
            "call": call,
            "tool": tool,
            "org": organization_id,
            "record_id": record_id,
            "record_name": record_name,
            "label": label if isinstance(label, str) else None,
            "row": bool(row_meta),
            "field": field["path"],
            "kind": field["kind"],
            "value": value,
            "scope": field.get("scope", "organisation"),
            "concept": field.get("concept"),
            "as_of": as_of,
            "declaration": field,
            "label_text": field.get("label"),
        }
        entry["unit"] = self._unit_of(field, parent, organization_id)
        window = field.get("window")
        if window:
            entry["window"] = parent.get(window["field"])
            entry["window_start"] = parent.get(window["start_field"])
        if field["kind"] == "score" and not entry["unit"].get("out_of"):
            return None
        if field["kind"] == "money" and not entry["unit"].get("currency"):
            return None
        entry["rendered"] = figure_format.render_figure(entry)
        return entry

    def declare_summary(self, context: dict, declaration: dict, organization_id=None) -> Tuple[dict, str]:
        """Add the summary's declared figures; return the context without those raw
        values and the one line that carries them to the model."""
        stripped = copy.deepcopy(context) if isinstance(context, dict) else {}
        parts = []
        for field in declaration.get("fields", []):
            tokens = _tokens(field["path"])
            for parent, name, _row in list(_leaves(stripped, tokens)):
                value = parent.get(name)
                if value is not None and not _fits_kind(field["kind"], value):
                    continue
                entry = self._entry("organisation_summary", 0, field, value, parent, None, None, None,
                                    organization_id, None)
                if entry is None:
                    continue
                self.entries.append(entry)
                parts.append('[fig:%s] reads "%s"' % (entry["key"], entry["rendered"]))
                parent.pop(name, None)
        line = ("Organisation summary: " + "; ".join(parts) + ".") if parts else ""
        return stripped, line


# --------------------------------------------------------------------------- #
# Units: the sentence, list item or table row a figure must sit in             #
# --------------------------------------------------------------------------- #

_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d{1,3}[.)])\s")
_ORDERED_MARKER = re.compile(r"^(\s*)(\d{1,3})([.)])(\s)")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def _is_table_row(line: str) -> bool:
    return line.lstrip().startswith("|")


def units_of(text: str) -> List[Tuple[int, int]]:
    """Spans of *text*: each table row, each list item, and the sentences of every other
    line (a marker placed after a full stop belongs to the sentence before)."""
    spans: List[Tuple[int, int]] = []
    position = 0
    for line in text.split("\n"):
        start, end = position, position + len(line)
        position = end + 1
        if not line.strip():
            continue
        if _is_table_row(line) or _LIST_ITEM.match(line):
            spans.append((start, end))
            continue
        cursor = start
        pieces = []
        for match in _SENTENCE_END.finditer(line):
            pieces.append((cursor, start + match.start()))
            cursor = start + match.end()
        pieces.append((cursor, end))
        for a, b in pieces:
            piece = text[a:b]
            if not piece.strip():
                continue
            only_markers = not REF.sub("", FIG_REF.sub("", piece)).strip(" .,;")
            if spans and only_markers and spans[-1][1] <= a and "\n" not in text[spans[-1][1]:a]:
                spans[-1] = (spans[-1][0], b)
            else:
                spans.append((a, b))
    return spans


def unit_at(text: str, units: List[Tuple[int, int]], position: int) -> Tuple[int, int]:
    for a, b in units:
        if a <= position <= b:
            return a, b
    return 0, len(text)


# --------------------------------------------------------------------------- #
# Resolution                                                                   #
# --------------------------------------------------------------------------- #


def resolve_references(text: str, table: FigureTable, turn_org) -> List[Dict[str, Any]]:
    """The references in *text* that cannot be shown, each with its reason:
    unknown_reference, other_organisation, or unbound (a row figure outside a sentence
    that cites its record)."""
    problems: List[Dict[str, Any]] = []
    units = units_of(text)
    for match in FIG_REF.finditer(text):
        key = match.group(1).upper()
        entry = table.get(key) if _FIG_KEY.match(key) else None
        base = {"key": key, "start": match.start(), "end": match.end()}
        if entry is None:
            problems.append({**base, "reason": REASON_UNKNOWN})
            continue
        if entry["scope"] != "public" and entry["org"] != turn_org:
            problems.append({**base, "reason": REASON_OTHER_ORG})
            continue
        if entry["row"]:
            a, b = unit_at(text, units, match.start())
            sentence = text[a:b]
            if entry.get("record_id") is not None:
                cited = {m.group(1) for m in REF.finditer(sentence)}
                bound = str(entry["record_id"]) in cited
            else:
                label = (entry.get("label") or "").strip().lower()
                bound = bool(label) and label in sentence.lower()
            if not bound:
                problems.append({**base, "reason": REASON_UNBOUND, "entry": entry})
    stripped = FIG_REF.sub("", text)
    if _RAW_FIG.search(stripped):
        at = _RAW_FIG.search(text.replace("\x00", ""))
        problems.append({"key": "", "start": at.start() if at else 0, "end": (at.end() if at else 0),
                         "reason": REASON_UNKNOWN})
    return problems


# --------------------------------------------------------------------------- #
# The closed rule for digits outside a reference                               #
# --------------------------------------------------------------------------- #

_WORDS = (
    "zero two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety "
    "hundred thousand million billion trillion dozen"
).split()


def _plurals(word: str) -> List[str]:
    return [word[:-1] + "ies"] if word.endswith("y") else [word + "s", word + "es"]


_NUMBER_WORD = re.compile(
    r"(?<![\w])(?:" + "|".join(sorted(_WORDS + [p for w in _WORDS for p in _plurals(w)], key=len, reverse=True))
    + r")(?![\w])",
    re.IGNORECASE,
)
_DIGIT = re.compile(r"[\d²³¹¼-¾]")
_SPAN = re.compile(
    r"(?:(?:[$£€¥]|(?<![A-Za-z])(?:GBP|USD|EUR|CHF|JPY|CAD|AUD)(?![A-Za-z]))\s?)?"
    r"(?:\d{1,3}(?:[,    ]\d{3})+|\d+)(?:[.,]\d+)?"
    r"(?:\s?(?:thousand|million|billion|trillion|tn|bn|mn|k|m|b)(?![A-Za-z]))?"
    r"(?:\s?(?:%|per\s?cent|percent)(?![A-Za-z]))?",
    re.IGNORECASE,
)
_URL = re.compile(r"(?:https?://|www\.)[^\s)\]>]+|\]\([^)]*\)", re.IGNORECASE)


@functools.lru_cache(maxsize=1)
def names_file_entries() -> Tuple[str, ...]:
    """The entries of ``figure_names.yml``: exact strings, never patterns."""
    import yaml

    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "figure_names.yml")
    with open(path, encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    entries = [str(n) for n in data.get("names") or [] if str(n).strip()]
    return tuple(sorted(set(entries), key=lambda s: (-len(s), s.lower())))


@functools.lru_cache(maxsize=64)
def _name_pattern(turn_names: Tuple[str, ...]):
    names = sorted(set(turn_names) | set(names_file_entries()), key=lambda s: (-len(s), s.lower()))
    names = [n for n in names if _DIGIT.search(n) or _NUMBER_WORD.search(n)]
    if not names:
        return None
    parts = [r"\s+".join(re.escape(piece) for piece in n.split()) for n in names]
    return re.compile(r"(?<![\w])(?:" + "|".join(parts) + r")(?![\w])", re.IGNORECASE)


def _blank(text: str, spans: Iterable[Tuple[int, int]]) -> str:
    chars = list(text)
    for a, b in spans:
        for i in range(a, b):
            if chars[i] not in "\n":
                chars[i] = " "
    return "".join(chars)


def mask_names(text: str, turn_names: Iterable[str]) -> str:
    """*text* with every whole name of this turn (forms 4) and of the names file (form 5)
    blanked, so the digits inside a name are never a figure."""
    pattern = _name_pattern(tuple(sorted(set(turn_names))))
    if pattern is None:
        return text
    return _blank(text, [m.span() for m in pattern.finditer(text)])


def _collapse(span: str) -> str:
    return re.sub(r"\s+", " ", span.strip())


def typed_spans(question: str, turn_names: Iterable[str]) -> List[str]:
    """The number spans the person typed, read after the names in the question are taken
    out, whitespace collapsed."""
    text = mask_names(question or "", turn_names)
    return [_collapse(m.group(0)) for m in _SPAN.finditer(text)]


def _first_column_ranks(lines: List[str], offsets: List[int]) -> List[Tuple[int, int]]:
    """Spans of a table's first-column cells when they read exactly 1, 2, ... N down."""
    spans: List[Tuple[int, int]] = []
    block: List[Tuple[int, str]] = []

    def flush():
        data = [(i, ln) for i, ln in block if not re.match(r"^\s*\|?\s*:?-{2,}", ln)]
        data = data[1:]
        cells = []
        for i, ln in data:
            parts = ln.strip().strip("|").split("|")
            cells.append((i, parts[0].strip() if parts else ""))
        if cells and [c for _i, c in cells] == [str(n) for n in range(1, len(cells) + 1)]:
            for i, cell in cells:
                at = lines[i].find(cell)
                if at >= 0:
                    spans.append((offsets[i] + at, offsets[i] + at + len(cell)))

    for i, line in enumerate(lines):
        if _is_table_row(line):
            block.append((i, line))
        elif block:
            flush()
            block = []
    if block:
        flush()
    return spans


def scan_written_numbers(text: str, turn_names: Iterable[str], question: str = "") -> List[Dict[str, Any]]:
    """Every digit or number word *text* holds outside the six allowed forms: a reference,
    a URL or link target, an ordered-list marker or a 1..N rank column, a name returned
    this turn, a names-file entry, a span the person typed (character for character)."""
    lines = text.split("\n")
    offsets, position = [], 0
    for line in lines:
        offsets.append(position)
        position += len(line) + 1
    hidden: List[Tuple[int, int]] = []
    for pattern in (REF, FIG_REF, _URL):
        hidden.extend(m.span() for m in pattern.finditer(text))
    for i, line in enumerate(lines):
        marker = _ORDERED_MARKER.match(line)
        if marker:
            hidden.append((offsets[i] + marker.start(2), offsets[i] + marker.end(3)))
    hidden.extend(_first_column_ranks(lines, offsets))
    scanned = mask_names(_blank(text, hidden), turn_names)
    typed = set(typed_spans(question, turn_names))
    violations: List[Dict[str, Any]] = []
    covered: List[Tuple[int, int]] = []
    for match in _SPAN.finditer(scanned):
        span = _collapse(match.group(0))
        covered.append(match.span())
        if span in typed:
            continue
        violations.append({"span": span, "start": match.start(), "end": match.end()})
    for match in _NUMBER_WORD.finditer(scanned):
        violations.append({"span": match.group(0), "start": match.start(), "end": match.end()})
    # A digit no span grammar reached (a superscript, a stray character) is still a figure.
    for match in _DIGIT.finditer(scanned):
        if not any(a <= match.start() < b for a, b in covered):
            violations.append({"span": match.group(0), "start": match.start(), "end": match.end()})
    violations.sort(key=lambda v: v["start"])
    return violations


# --------------------------------------------------------------------------- #
# All violations of a draft                                                    #
# --------------------------------------------------------------------------- #


def violations_in(text: str, table: FigureTable, turn_org, question: str = "") -> List[Dict[str, Any]]:
    """Every reason the draft *text* cannot be shown as written, each with the unit
    (sentence, item or row) it sits in."""
    units = units_of(text)
    found = resolve_references(text, table, turn_org)
    for item in found:
        item["span"] = text[item["start"]:item["end"]] if item["end"] > item["start"] else item["key"]
    for item in scan_written_numbers(text, table.names, question):
        found.append({**item, "reason": REASON_WRITTEN})
    for item in found:
        a, b = unit_at(text, units, item["start"])
        item["unit"] = (a, b)
        item["clause"] = text[a:b].strip()
    found.sort(key=lambda v: v["start"])
    return found


# --------------------------------------------------------------------------- #
# Rendering                                                                    #
# --------------------------------------------------------------------------- #


def renumber_lists(text: str) -> str:
    """The markers of every ordered list read 1, 2, ... N, so a number shown is always the
    item's position."""
    lines = text.split("\n")
    counters: Dict[int, int] = {}
    for i, line in enumerate(lines):
        marker = _ORDERED_MARKER.match(line)
        if not marker:
            if line.strip() and not _LIST_ITEM.match(line):
                counters.clear()
            continue
        indent = len(marker.group(1))
        for deeper in [k for k in counters if k > indent]:
            del counters[deeper]
        counters[indent] = counters.get(indent, 0) + 1
        lines[i] = "%s%d%s%s%s" % (marker.group(1), counters[indent], marker.group(3), marker.group(4),
                                   line[marker.end():])
    return "\n".join(lines)


_WORD = re.compile(r"[A-Za-z']+")


def _norm_word(word: str) -> str:
    word = word.lower().strip(".,;:!?)\"'")
    return word[:-1] if word.endswith("s") and len(word) > 3 else word


def drop_repeated_words(after: str, phrase: str) -> Tuple[int, str]:
    """``(characters to skip, text to put back)`` for the one to three words right after a
    rendered phrase that repeat a run of that phrase's own words (ignoring case and a final
    s). A word holding a digit is never touched. ``(0, "")`` when nothing repeats."""
    if not after or not after[0].isspace():
        return 0, ""
    phrase_words = [_norm_word(w) for w in phrase.split()]
    tokens = list(re.finditer(r"\S+", after))
    for n in (3, 2, 1):
        if len(tokens) < n:
            continue
        chosen = tokens[:n]
        raw = [t.group(0) for t in chosen]
        if any(_DIGIT.search(w) for w in raw):
            continue
        words = [_norm_word(w) for w in raw]
        if not all(words):
            continue
        if any(phrase_words[i:i + n] == words for i in range(len(phrase_words) - n + 1)):
            trailing = re.search(r"[.,;:!?)]+$", raw[-1])
            return chosen[-1].end(), (trailing.group(0) if trailing else "")
    return 0, ""


def _split_row(line: str) -> List[str]:
    return line.strip().strip("|").split("|")


def _render_inline(segment: str, base: int, text: str, units, table: FigureTable):
    """*segment* with each reference shown as its phrase; ``figures`` are
    ``(entry, shown, start, end)`` with offsets inside the returned text."""
    out: List[str] = []
    figures = []
    cursor = length = 0
    for match in FIG_REF.finditer(segment):
        if match.start() < cursor:
            continue
        entry = table.get(match.group(1))
        if entry is None:
            continue
        before = segment[cursor:match.start()]
        out.append(before)
        length += len(before)
        phrase = entry["rendered"]
        a, b = unit_at(text, units, base + match.start())
        distinct = {m.group(1) for m in REF.finditer(text[a:b])}
        if entry["row"] and len(distinct) > 1 and entry.get("record_name"):
            phrase += " (%s)" % entry["record_name"]
        figures.append((entry, phrase, length, length + len(phrase)))
        out.append(phrase)
        length += len(phrase)
        cursor = match.end()
        skip, put_back = drop_repeated_words(segment[cursor:], entry["rendered"])
        if skip:
            cursor += skip
            out.append(put_back)
            length += len(put_back)
    out.append(segment[cursor:])
    return "".join(out), figures


def _table_blocks(lines: List[str]) -> List[Tuple[int, int]]:
    blocks, start = [], None
    for i, line in enumerate(lines + [""]):
        if i < len(lines) and _is_table_row(line):
            start = i if start is None else start
        elif start is not None:
            blocks.append((start, i))
            start = None
    return blocks


def _column_labels(lines: List[str], lo: int, hi: int, table: FigureTable) -> Dict[int, str]:
    """For a table, the declared label of each column whose cells are all one declared
    field's references alone; those columns show the value only under that label."""
    rows = [_split_row(lines[i]) for i in range(lo, hi)]
    labels: Dict[int, str] = {}
    for col in range(max(len(r) for r in rows)):
        fields, mixed = set(), False
        for r_index, row in enumerate(rows):
            cell = row[col].strip() if col < len(row) else ""
            if r_index == 0 or re.match(r"^:?-{2,}:?$", cell):
                continue
            found = FIG_REF.findall(cell)
            if not found:
                continue
            entry = table.get(found[0])
            if len(found) == 1 and FIG_REF.sub("", cell).strip() == "" and entry:
                fields.add((entry["tool"], entry["field"], entry.get("label_text")))
            else:
                mixed = True
        if len(fields) == 1 and not mixed:
            labels[col] = next(iter(fields))[2]
    return labels


def render(text: str, table: FigureTable) -> Tuple[str, List[Dict[str, Any]]]:
    """The delivered text and each figure shown, with its key and its character offsets in
    that text. *text* has no unresolved reference left in it."""
    text = renumber_lists(text)
    units = units_of(text)
    lines = text.split("\n")
    blocks = _table_blocks(lines)
    labels = {b: _column_labels(lines, lo, hi, table) for b, (lo, hi) in enumerate(blocks)}
    out_lines: List[str] = []
    figures: List[Dict[str, Any]] = []
    out_offset = 0
    in_offset = 0
    for index, line in enumerate(lines):
        block = next((n for n, (lo, hi) in enumerate(blocks) if lo <= index < hi), None)
        found: List[tuple] = []
        if block is None:
            rendered, found = _render_inline(line, in_offset, text, units, table)
        else:
            lo = blocks[block][0]
            pieces, length = ["|"], 1
            for col, cell in enumerate(_split_row(line)):
                core = cell.strip()
                label = labels[block].get(col)
                refs = FIG_REF.findall(core)
                if label and index == lo:
                    shown = " %s " % label
                    pieces.append(shown)
                elif label and refs and len(refs) == 1 and FIG_REF.sub("", core).strip() == "":
                    entry = table.get(refs[0])
                    value = figure_format.value_only(entry)
                    shown = " %s " % value
                    found.append((entry, value, length + 1, length + 1 + len(value)))
                    pieces.append(shown)
                else:
                    shown, cell_figures = _render_inline(cell, in_offset, text, units, table)
                    found.extend((e, p, length + a, length + b) for e, p, a, b in cell_figures)
                    pieces.append(shown)
                length += len(pieces[-1])
                pieces.append("|")
                length += 1
            rendered = "".join(pieces)
        for entry, shown, a, b in found:
            figures.append(envelope_figure(entry, shown, out_offset + a, out_offset + b))
        out_lines.append(rendered)
        out_offset += len(rendered) + 1
        in_offset += len(line) + 1
    return "\n".join(out_lines), figures


def envelope_figure(entry: Dict[str, Any], shown: str, start: int, end: int) -> Dict[str, Any]:
    """One figure as the envelope carries it."""
    return {
        "key": entry["key"], "tool": entry["tool"], "call": entry["call"],
        "record_id": entry.get("record_id"), "field": entry["field"], "concept": entry.get("concept"),
        "kind": entry["kind"], "value": entry.get("value"), "unit": entry.get("unit") or {},
        "reads": shown, "as_of": entry.get("as_of"), "start": start, "end": end,
    }


# --------------------------------------------------------------------------- #
# The records block (the fallback's restoration of true figures)               #
# --------------------------------------------------------------------------- #


def records_block(table: FigureTable, removed: List[str], shown_keys: Iterable[str],
                  tools_called: int = 1):
    """``(text, figures)``: "What the records show:" and one line for each record a removed
    clause cited, with its declared figures from every call of the turn, then each call's
    top-level figures (on its subject's line, else on "Your organisation"). Figures already
    shown in the answer are left out; ``("", [])`` when nothing is left to show. *figures*
    are ``(entry, phrase, start, end)`` with offsets inside the text. The organisation
    summary's figures are listed only when no tool ran."""
    shown = set(shown_keys)
    removed_text = "\n".join(removed)
    cited_ids = {m.group(1) for m in REF.finditer(removed_text)}
    lines: List[Tuple[str, List[Dict[str, Any]]]] = []
    index: Dict[str, int] = {}

    def add(entry: Dict[str, Any], identity: str, heading: str) -> None:
        if entry["key"] in shown:
            return
        if identity not in index:
            index[identity] = len(lines)
            lines.append((heading, []))
        bucket = lines[index[identity]][1]
        if all(e["rendered"] != entry["rendered"] for e in bucket):
            bucket.append(entry)

    for entry in table.entries:
        if entry["tool"] == "organisation_summary" and tools_called:
            continue
        if entry["row"]:
            if entry.get("record_id") is not None:
                if str(entry["record_id"]) in cited_ids:
                    add(entry, "id:%s" % entry["record_id"],
                        "%s [ref:%s]" % (entry.get("record_name") or "Record", entry["record_id"]))
            elif entry.get("label") and entry["label"].lower() in removed_text.lower():
                add(entry, "label:%s" % entry["label"].lower(), entry["label"])
        elif entry.get("record_id") is not None:
            add(entry, "id:%s" % entry["record_id"],
                "%s [ref:%s]" % (entry.get("record_name") or "Record", entry["record_id"]))
        else:
            add(entry, "org", "Your organisation")
    parts = [RECORDS_HEADING]
    figures = []
    length = len(RECORDS_HEADING)
    for heading, entries in lines:
        if not entries:
            continue
        prefix = "\n- %s: " % heading
        parts.append(prefix)
        length += len(prefix)
        for i, entry in enumerate(entries):
            if i:
                parts.append("; ")
                length += 2
            phrase = entry["rendered"]
            figures.append((entry, phrase, length, length + len(phrase)))
            parts.append(phrase)
            length += len(phrase)
    if not figures:
        return "", []
    return "".join(parts), figures


# --------------------------------------------------------------------------- #
# The revision message                                                         #
# --------------------------------------------------------------------------- #


def revision_message(violations: List[Dict[str, Any]], table: FigureTable, question: str,
                     tools_called: int) -> str:
    """The one message that sends a draft back: built only from this turn's table and the
    question, one line per violation."""
    lines = ["Your answer cannot be shown as written. Fix each point and send the whole answer again:"]
    if not tools_called:
        summary = "; ".join(
            '[fig:%s] reads "%s"' % (e["key"], e["rendered"]) for e in table.summary_entries()
        )
        lines.append(
            "No tool was called in this turn. The organisation summary holds: %s You may also call a tool."
            % (summary + "." if summary else "nothing recorded.")
        )
    typed = typed_spans(question, table.names)
    for item in violations:
        reason = item["reason"]
        if reason == REASON_WRITTEN:
            clause = item["clause"][:120]
            lines.append('"%s" in "%s" is written as text. Use its reference from the tool results, or leave it out.'
                         % (item["span"], clause))
        elif reason == REASON_UNKNOWN:
            lines.append("[fig:%s] does not exist in this turn." % (item["key"] or "?"))
        elif reason == REASON_OTHER_ORG:
            lines.append("[fig:%s] cannot be used here." % item["key"])
        elif reason == REASON_UNBOUND:
            entry = item["entry"]
            if entry.get("record_id") is not None:
                lines.append(
                    "[fig:%s] belongs to %s [ref:%s]. Put it in a sentence that cites that record, or use the "
                    "figure of the record you named." % (item["key"], entry.get("record_name") or "a record",
                                                          entry["record_id"]))
            else:
                lines.append(
                    "[fig:%s] belongs to %s. Put it in a sentence that names that record, or use the figure "
                    "of the record you named." % (item["key"], entry.get("label") or "a record"))
    if typed and any(v["reason"] == REASON_WRITTEN for v in violations):
        lines.append('The person typed: %s. Copy a typed number exactly as they wrote it, or leave it out.'
                     % ", ".join('"%s"' % t for t in typed))
    return "\n".join(lines)
