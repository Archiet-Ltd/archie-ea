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

_SQL_TEXT = re.compile(
    r"null value in column|violates (?:not-null|unique|foreign key|check) constraint|"
    r"\bSQLSTATE\b|psycopg2|sqlalchemy|\bIntegrityError\b|\bProgrammingError\b|"
    r"\bOperationalError\b|duplicate key value|relation \"[^\"]+\" does not exist|"
    r"\b(?:INSERT INTO|UPDATE \w+ SET|DELETE FROM|SELECT .+ FROM)\b",
    re.IGNORECASE,
)


def is_database_error(error) -> bool:
    """True for a database driver or SQLAlchemy error, or text that carries SQL."""
    module = type(error).__module__ or ""
    if module.startswith(("sqlalchemy", "psycopg2", "psycopg")):
        return True
    return bool(_SQL_TEXT.search(str(error or "")))


def _registered_names() -> list:
    from app.modules.ai_chat.tools.registry import TOOL_SCHEMAS

    return sorted({s["name"] for s in TOOL_SCHEMAS if s.get("name")}, key=len, reverse=True)


def _spoken(name: str) -> str:
    return name.replace("_", " ")


def in_customer_words(text):
    """*text* with internal names and raw fields said the way a CTO would say them."""
    if not isinstance(text, str) or not text.strip():
        return text
    out = text
    # A database error never reaches a reader: the sentence that carries it is replaced.
    if _SQL_TEXT.search(out):
        kept = [s for s in re.split(r"(?<=[.!?])\s+|\n+", out) if s and not _SQL_TEXT.search(s)]
        out = " ".join(kept + [WRITE_FAILED])
    out = re.sub(r"\bgenome patches\b", "proposed changes to your model", out, flags=re.IGNORECASE)
    out = re.sub(r"\bgenome patch\b", "proposed change to your model", out, flags=re.IGNORECASE)
    for name in _registered_names():
        if name in out:
            out = re.sub(r"(?:\b(?:the|a)\s+)?(?:\b(?:lookup|tool|step|function)\s+)?`?\b%s\b`?" % re.escape(name), "that lookup", out, flags=re.IGNORECASE)
    out = re.sub(r"\bwith\s+`?\bsupported\b`?\s*[:=]\s*`?false`?", "and it is not supported", out, flags=re.IGNORECASE)
    out = re.sub(r"(?:\bhas\s+)?`?\bsupported\b`?\s*[:=]\s*`?false`?", "is not supported", out, flags=re.IGNORECASE)
    out = re.sub(r"(?:\b(?:has|with)\s+)?`?\bsupported\b`?\s*[:=]\s*`?true`?", "is supported", out, flags=re.IGNORECASE)
    out = re.sub(r"`?\bsupported_by\b`?\s+(?:is\s+)?(?:empty|null|missing|None)", "nothing is recorded as supporting it", out)
    out = re.sub(r"\bevery `?(\w+)`? field is (?:null|empty)", lambda m: "no %s is recorded" % _spoken(m.group(1)), out, flags=re.IGNORECASE)
    out = re.sub(r"`?\b(\w+)\b`?\s*(?:[:=]|is|are)\s*`?(?:null|None)`?(?![\w])",
                 lambda m: "%s is not recorded" % _spoken(m.group(1)) if m.group(1).lower() not in ("is", "are") else m.group(0), out)
    for word in _STATUS_WORDS:
        out = re.sub(r"`?\b%s\b`?" % word, _spoken(word), out)
    # A field name in backticks (supported_by) is said as words.
    out = re.sub(r"`([a-z]+(?:_[a-z]+)+)`", lambda m: _spoken(m.group(1)), out)
    out = re.sub(r"\bsupported_by\b", "supported by", out)
    if "Architecture Review Board" not in out:
        out = re.sub(r"\bARB\b", "Architecture Review Board", out, count=1)
    out = re.sub(r"(?<!code )\brepository\b", "code repository", out)
    out = re.sub(r"(?<!code )\brepositories\b", "code repositories", out)
    return out


_SKIPPED_PREFIXES = ("/api", "/static", "/admin", "/account", "/auth", "/ai-chat", "/intelligence/answers")


def known_pages(app) -> dict:
    """Page paths the product serves with no placeholder in them, each with its name.

    The chat turns a path in an answer into a link only when it is in this list,
    so a path the model made up stays plain text instead of leading to a 404.
    """
    pages = {}
    for rule in app.url_map.iter_rules():
        path = rule.rule
        if "GET" not in (rule.methods or ()) or rule.arguments or "<" in path:
            continue
        path = path.rstrip("/") or "/"
        if path == "/" or path.startswith(_SKIPPED_PREFIXES):
            continue
        pages.setdefault(path, path.rsplit("/", 1)[-1].replace("-", " ").replace("_", " ").title())
    return pages
