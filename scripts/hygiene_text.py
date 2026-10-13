#!/usr/bin/env python
"""Text-masking primitives shared by more than one scripts/check_*.py
static-analysis script.

The common shape across these checkers: find a span of text that should
not be read as the thing being searched for (a comment, a Jinja
expression, an HTML tag), and blank it out -- every character replaced by
a space, except a newline, which stays a newline -- rather than delete it,
so every other match's line and column position in the surrounding text
stays correct. One definition of that primitive and the handful of
comment/markup patterns built on it, imported by each checker that needs
one, rather than a separate copy re-typed in every file.

Not every name here is used by every importer, and two related pairs are
kept deliberately apart rather than unified into one:

* `HTML_TAG_RE` requires at least one interior character (an empty `<>` is
  not a real tag in the caller that uses it); `HTML_TAG_RE_LOOSE` allows
  zero and is DOTALL, for a caller masking a tag to read what is left
  between and around it rather than matching the tag as a unit of text.
* `blank_comments()` is a single left-to-right character scan: an
  unterminated `/*` or `{#` with no closing delimiter blanks to
  end-of-file (whatever follows an unclosed comment marker was meant to be
  inside it). `js_comment_spans()` is regex-based and returns each span
  rather than blanking in place, for a caller that reports a line number
  per comment; it requires a closing `*/` to match a block comment at all,
  so an unterminated one is left unmatched rather than consuming the rest
  of the file. Both are kept, each behind the name the shape it solves
  needs.
"""
from __future__ import annotations

import re


def blank(segment: str) -> str:
    """`segment`, every character replaced by a space except a newline,
    which stays a newline -- same length, same line breaks, so a match
    found in the surviving text keeps its true line and column."""
    return "".join(ch if ch == "\n" else " " for ch in segment)


def mask(text: str, pattern: re.Pattern) -> str:
    """`text` with every non-overlapping `pattern` match blanked (see
    `blank`); everything else is returned unchanged."""
    return pattern.sub(lambda m: blank(m.group(0)), text)


# `{# ... #}` / `<!-- ... -->` -- apart, for a caller collecting each
# delimiter's spans separately, and combined, for a caller masking both in
# one pass (the two delimiter pairs never overlap, so one pattern and two
# find the same spans).
JINJA_COMMENT_RE = re.compile(r"\{#.*?#\}", re.DOTALL)
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
HTML_OR_JINJA_COMMENT_RE = re.compile(r"\{#.*?#\}|<!--.*?-->", re.DOTALL)

# `{{ ... }}` / `{% ... %}` -- a Jinja expression or statement.
JINJA_EXPR_OR_STMT_RE = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.DOTALL)

# `{% ... %}` / `{# ... #}` -- a Jinja statement or comment, deliberately
# not an expression: a caller stripping this before reading label text
# wants a `{{ }}` output kept (it renders real text a user reads) and only
# the code/comment code around it removed.
JINJA_STMT_OR_COMMENT_RE = re.compile(r"\{%.*?%\}|\{#.*?#\}", re.DOTALL)

# `{{ ... }}` / `{% ... %}` / `{# ... #}` -- any Jinja construct at all,
# expression, statement or comment together, for a caller that needs to mask
# out everything Jinja renders as code before parsing what is left as the
# surrounding attribute's own target language, not the narrower code/comment
# split JINJA_STMT_OR_COMMENT_RE draws for a caller that wants `{{ }}`
# output kept.
JINJA_ANY_RE = re.compile(
    JINJA_EXPR_OR_STMT_RE.pattern + "|" + JINJA_COMMENT_RE.pattern, re.DOTALL
)

# An HTML tag -- see the module docstring for why two shapes stay apart.
HTML_TAG_RE = re.compile(r"<[^>]+>")
HTML_TAG_RE_LOOSE = re.compile(r"<[^>]*>", re.DOTALL)

_JS_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
# Not preceded by `:` -- a `//` immediately after a colon is a URL scheme
# separator (`https://`, `ws://`), not a line comment starting; an ordinary
# line comment after a real `:` (an object-literal key, a label) always has
# at least a space before the `//` in practice, so this excludes the URL
# shape without excluding a genuine comment.
_JS_LINE_COMMENT_RE = re.compile(r"(?<!:)//[^\r\n]*")


def js_comment_spans(text: str) -> list[tuple[int, str]]:
    """(start_offset, matched_text) for every `//` and `/* */` comment in
    `text` -- a `//` already inside a matched `/* */` block is not counted
    a second time, and a `//` immediately after `:` (a URL scheme, not a
    comment marker) is never matched at all."""
    blocks = [(m.start(), m.end(), m.group(0)) for m in _JS_BLOCK_COMMENT_RE.finditer(text)]
    spans = [(start, matched) for start, _end, matched in blocks]
    for m in _JS_LINE_COMMENT_RE.finditer(text):
        if any(start <= m.start() < end for start, end, _ in blocks):
            continue
        spans.append((m.start(), m.group(0)))
    return spans


def blank_comments(text: str, jinja: bool) -> str:
    """`text` with every `/* */` and `//` comment, and (when `jinja`)
    every `{# #}` block, blanked out (see `blank`). A left-to-right
    character scan rather than a regex substitution: an unterminated `/*`
    or `{#` block blanks to end-of-file, on the theory that whatever comes
    after a comment marker with no closing delimiter was meant to be
    inside it."""
    out = list(text)
    spans: list[tuple[int, int]] = []
    i, n = 0, len(text)
    while i < n:
        if jinja and text.startswith("{#", i):
            j = text.find("#}", i + 2)
            j = n if j == -1 else j + 2
            spans.append((i, j))
            i = j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j == -1 else j + 2
            spans.append((i, j))
            i = j
        elif text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j == -1 else j
            spans.append((i, j))
            i = j
        else:
            i += 1
    for a, b in spans:
        b = min(b, len(out))
        out[a:b] = blank(text[a:b])
    return "".join(out)
