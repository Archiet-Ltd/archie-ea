"""The words a customer reads in a chat answer.

The model sometimes echoes the names the product uses inside (a change to the
model written as a "genome patch", a field called ``supported_by``, the name of
a lookup, the text of a database error). ``in_customer_words`` is the one place
that turns those into plain sentences before an answer is stored or shown, and
``known_pages`` is the one list of page paths the chat may turn into links.
"""

import re

# What a person is told when a write fails and nothing was changed.
WRITE_FAILED = (
    "That change could not be saved, so nothing was changed. "
    "Try again in a moment; if it keeps happening, tell your administrator."
)

_STATUS_WORDS = ("in_progress", "not_started", "on_hold", "at_risk", "end_of_life", "under_review")

# Text only a database driver writes. Ordinary English never matches these.
_DRIVER_TEXT = re.compile(
    r"null value in column \"[^\"]+\"|violates (?:not-null|unique|foreign key|check) constraint|"
    r"\bSQLSTATE\b|\bpsycopg2?\b|\bsqlalchemy\.|\bIntegrityError\b|\bProgrammingError\b|"
    r"\bOperationalError\b|duplicate key value violates|relation \"[^\"]+\" does not exist"
)
# SQL statements are only looked for in the error of a write that really failed.
_STATEMENT_TEXT = re.compile(r"\b(?:INSERT INTO \w+|UPDATE \w+ SET|DELETE FROM \w+|SELECT .+ FROM \w+)\b")


def is_database_error(error) -> bool:
    """True for a database driver or SQLAlchemy error, or the error text of one.

    Called only on the error of a tool that failed, so statement text is safe to look for.
    """
    module = type(error).__module__ or ""
    if module.startswith(("sqlalchemy", "psycopg2", "psycopg")):
        return True
    text = str(error or "")
    return bool(_DRIVER_TEXT.search(text) or _STATEMENT_TEXT.search(text))


def _registered_names() -> list:
    from app.modules.ai_chat.tools.registry import TOOL_SCHEMAS

    return sorted({s["name"] for s in TOOL_SCHEMAS if s.get("name")}, key=len, reverse=True)


def _spoken(name: str) -> str:
    return name.replace("_", " ")


_FENCE = re.compile(r"```.*?(?:```|\Z)", re.DOTALL)
_INLINE = re.compile(r"`([^`\n]+)`")
_SENTENCE_START = re.compile(r"(?:\A|[.!?]\s+|\n\s*)\Z")
_HOLD = "\x00%d\x00"


def _cased(replacement: str, text: str, start: int) -> str:
    """*replacement*, capitalised when it begins a sentence in *text*."""
    if _SENTENCE_START.search(text[:start]):
        return replacement[:1].upper() + replacement[1:]
    return replacement


def _is_raw_field(span: str, names: list) -> bool:
    """A whole inline-code span that is an internal field, status or lookup name."""
    span = span.strip()
    return bool(
        re.fullmatch(r"supported\s*[:=]\s*(?:true|false)", span)
        or span == "supported_by"
        or span in _STATUS_WORDS
        or span in names
        or (span.endswith("()") and span[:-2] in names)
    )


def in_customer_words(text, write_failed: bool = False):
    """*text* with internal names and raw fields said the way a CTO would say them.

    Only words that are certainly internal change. Code is left alone: a fenced
    block, and an inline span unless the whole span is an internal name.
    *write_failed* says the turn really had a write that failed; only then is a
    removed database error followed by the "nothing was changed" sentence.
    """
    if not isinstance(text, str) or not text.strip():
        return text
    names = _registered_names()
    held = []

    def hold(chunk):
        held.append(chunk)
        return _HOLD % (len(held) - 1)

    out = _FENCE.sub(lambda m: hold(m.group(0)), text)
    out = re.sub(r"\bevery `(\w+)` field is (?:null|empty)", lambda m: "no %s is recorded" % _spoken(m.group(1)), out, flags=re.IGNORECASE)
    out = re.sub(r"`(\w+)` (?:is|are) (?:null|None)\b", lambda m: "%s is not recorded" % _spoken(m.group(1)), out)
    out = _INLINE.sub(lambda m: m.group(1).strip() if _is_raw_field(m.group(1), names) else hold(m.group(0)), out)

    # A database error never reaches a reader: the sentence that carries it is replaced.
    if _DRIVER_TEXT.search(out):
        parts = re.split(r"(?<=[.!?])\s+|\n+", out)
        kept, swapped = [], False
        for part in parts:
            if not part:
                continue
            if _DRIVER_TEXT.search(part):
                if write_failed and not swapped:
                    kept.append(WRITE_FAILED)
                    swapped = True
            else:
                kept.append(part)
        out = " ".join(kept)

    out = re.sub(r"\bgenome patches\b", "proposed changes to your model", out, flags=re.IGNORECASE)
    out = re.sub(r"\bgenome patch\b", "proposed change to your model", out, flags=re.IGNORECASE)
    for name in names:
        if name not in out:
            continue
        # An underscored name is internal wherever it appears; a plain word such as
        # "answer" is a lookup only when written as a call, name(...), which inline
        # code already unwrapped above.
        word = r"(?:\b(?:[Tt]he|[Aa])\s+)?(?:(?:lookup|tool|step|function)\s+)?\b%s" % re.escape(name)
        pattern = word + (r"\b(?:\s+(?:lookup|tool|step|function)\b)?" if "_" in name else r"\(\)")
        out = re.sub(pattern, lambda m: _cased("that lookup", m.string, m.start()), out)
    out = re.sub(r"\bwith\s+supported\s*[:=]\s*false\b", "which is not supported", out)
    out = re.sub(r"\b(?:has\s+)?supported\s*[:=]\s*false\b", "is not supported", out)
    out = re.sub(r"\b(?:(?:has|with)\s+)?supported\s*[:=]\s*true\b", "is supported", out)
    out = re.sub(r"\bsupported_by\s+(?:is\s+)?(?:empty|null|missing|None)\b", "nothing is recorded as supporting it", out)
    out = re.sub(r"\bsupported_by\b", "supported by", out)
    for word in _STATUS_WORDS:
        out = re.sub(r"\b%s\b" % word, _spoken(word), out)
    if "Architecture Review Board" not in out:
        out = re.sub(r"\bARB\b", "Architecture Review Board", out, count=1)
    out = re.sub(r"\b(the|this|that|your|our) repository\b", r"\1 code repository", out)
    out = re.sub(r"\b(the|these|those|your|our) repositories\b", r"\1 code repositories", out)
    return re.sub("\x00(\\d+)\x00", lambda m: held[int(m.group(1))], out)


class StreamingWords:
    """Says a streamed answer in customer words, a chunk at a time.

    ``feed`` returns the text that is safe to show now; the last few words
    (and any unclosed code span) are held back so a name split across two chunks
    is still caught. ``finish`` returns the rest.
    """

    HOLD_WORDS = 5  # the longest rewritten phrase, "every `x` field is null", is five words

    def __init__(self, write_failed=lambda: False):
        self._raw = ""
        self._shown = ""
        self._write_failed = write_failed

    def _emit(self, final: bool) -> str:
        said = in_customer_words(self._raw, self._write_failed())
        cut = len(said)
        if not final:
            # Words that may still become part of a longer phrase stay held back.
            cut = re.search(r"(?:\S+\s+){0,%d}\S*\Z" % self.HOLD_WORDS, said).start()
            # So does an inline code span that has not closed yet.
            head = _FENCE.sub(lambda m: " " * len(m.group(0)), said[:cut])
            if head.count("`") % 2:
                cut = head.rfind("`")
        if cut < len(self._shown) or not said.startswith(self._shown):
            return ""
        delta = said[len(self._shown):cut]
        self._shown = said[:cut]
        return delta

    def feed(self, chunk: str) -> str:
        self._raw += chunk or ""
        return self._emit(False)

    def finish(self) -> str:
        return self._emit(True)


def known_pages(app, user) -> dict:
    """Page paths the signed-in person can open, each with the name their sidebar gives it.

    The chat turns a path in an answer into a link only when it is in this list,
    so a path the model made up, or one this role is refused, stays plain text.
    """
    from app.utils.role_access import get_sidebar_zones

    pages = {}
    try:
        zones = get_sidebar_zones(user)
    except Exception:
        return pages
    for zone in zones:
        for link in zone.get("links", ()):
            if link.get("query_params") or link.get("endpoint") not in app.view_functions:
                continue
            for rule in app.url_map.iter_rules(link["endpoint"]):
                if "GET" in (rule.methods or ()) and not rule.arguments and rule.rule != "/":
                    pages.setdefault(rule.rule.rstrip("/") or "/", link["label"])
                    break
    return pages
