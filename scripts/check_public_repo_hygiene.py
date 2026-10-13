#!/usr/bin/env python
r"""Three checks over this repository's tracked files and commit history.

1. No `docs/buckets/` directory tracked in this repository. (zero)
2. No tracked source file (app/, scripts/, tests/, templates) contains the
   literal string `docs/buckets/`. (zero)
3. No review-record-id token or process word, in a comment, docstring or
   string literal under app/, scripts/, tests/, templates or static JS, or
   in a commit message. A record-id token is a short letter-and-digit code
   this shape of review uses (see RECORD_ID_PATTERN below); a process word
   names a reviewing function rather than the change itself (see
   ROLE_WORDS below). Neither belongs in this repository's own source or
   history: both describe review machinery, not the product.

Two allowlists keep rule 3 from matching this codebase's own conventions:
RECORD_ID_ALLOWLIST_PREFIXES excludes this codebase's business-reference-
number families (architecture-decision, finding, task and deliverable
references) and the pre-existing standards-body, quarter and fiscal-year
prefixes that share the same short letter-and-digit shape; PRODUCT_TERMS
excludes the handful of multi-word product-feature names that legitimately
contain one of the process words as a substring. See each constant's own
comment for the exact reasoning.

Rule 3's source half is a ratchet: see RECORD_ID_PATTERN's own comment for
why the record-id shape cannot be told apart from a genuine business
reference number by shape alone in every case, and unrendered_model_fields
elsewhere in this codebase's verification suite for the same kind of
heuristic ratchet used for the same reason. Rule 3's commit-message half is
zero-tolerance over the range under review, not full history -- see
verify.py's `gate_public_repo_hygiene_commit_messages` for why a full-
history count cannot be a stable measurement.

Per-line escape hatch for rule 2 and rule 3's source half: `hygiene-ok:
<reason>` anywhere on the line, for a reference that is itself the point
(this file's own comments, or a test asserting the pattern is absent).
Rule 3's commit-message half checks the same marker per physical line of
the message; an attribution-trailer line is never excused by it.

Proven-against: a docs/buckets/ directory tracked in a synthetic tree, and
a tracked file containing the literal string "docs/buckets/" -- both
observed red, then green once removed. Rule 3: a tracked file containing a
record-id-shaped token and a file containing a process word -- both
observed red, then green once removed or marked hygiene-ok; a commit
message containing a process word observed red in a synthetic git
repository, then green once amended; every shape in
tests/test_gates_actually_fail.py's parametrised false-negative and
false-positive tables, each observed to match its expected outcome.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tokenize

import hygiene_text

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SCAN_DIRS = ("app", "scripts", "tests", "templates")
PATTERN = "docs/buckets/"
SELF_NAME = os.path.basename(__file__)
ESCAPE_HATCH = "hygiene-ok:"

# ---------------------------------------------------------------- rule 3: review-record-id tokens and pipeline role words

# The dashed shape a review record id takes: one process letter (D, T, Q or
# F), a dash, and one of four endings -- each its own alternative below, tried
# in order, rather than one pattern with every part optional, because the
# last of the four (the simplest: no leading digit, no letter segment, no
# second dash-digits segment -- just letter-dash-digits) needs its OWN, extra
# rule the other three do not: it must be at least two digits. One bare digit
# after a bare dash (T-1, T-0, D-0) is exactly as likely to be an ordinary
# English fraction or count as a review record id, and this codebase's own
# prose has used it that way -- while a token with a leading digit before the
# dash (D2-7, T4-3), a letter segment (D-ALL-1, T-FIX-103, T-DR-1) or a  # hygiene-ok: quoting this pattern's own example shapes, not a real hit
# second dash-digits segment (D-105-2) already carries enough of its own  # hygiene-ok: quoting this pattern's own example shapes, not a real hit
# structure to not need the same guard, and every one of those keeps a
# single trailing digit. The four endings, one example each (D2-7, D-ALL-1,  # hygiene-ok: quoting this pattern's own example shapes, not a real hit
# D-105-2, F-05) # hygiene-ok: quoting this pattern's own example shapes, not a real hit
# -- plus an optional trailing lowercase letter on every one (T-004b), for a  # hygiene-ok: quoting this pattern's own example shapes, not a real hit
# sub-task or sub-finding suffix this process also uses.
RECORD_ID_PATTERN = re.compile(
    r"\b(?:"
    r"(?:D|T|Q|F)[0-9]-[0-9]{1,4}(?:-[0-9]{1,4})?"        # leading digit before the dash: D2-7, T4-3  # hygiene-ok: quoting this pattern's own example shapes, not a real hit
    r"|(?:D|T|Q|F)-[A-Z]{1,4}-[0-9]{1,4}(?:-[0-9]{1,4})?"  # a letter segment: D-ALL-1, T-FIX-103, T-DR-1  # hygiene-ok: quoting this pattern's own example shapes, not a real hit
    r"|(?:D|T|Q|F)-[0-9]{1,4}-[0-9]{1,4}"                  # a second dash-digits segment: D-105-2  # hygiene-ok: quoting this pattern's own example shapes, not a real hit
    r"|(?:D|T|Q|F)-[0-9]{2,4}"                             # the bare shape: needs two digits or more, F-05, T-004  # hygiene-ok: quoting this pattern's own example shapes, not a real hit
    r")[a-z]?\b",
    re.IGNORECASE,
)
# The letter-dash-letters-digit shape with no digits-only ending (T-S1,  # hygiene-ok: quoting this pattern's own example shapes, not a real hit
# T-S1b) -- one id shape the pattern above cannot also match, since its own  # hygiene-ok: quoting this pattern's own example shapes, not a real hit
# final segment is a letter followed by one or two digits, not a bare digit
# run. An optional interior letter-segment-and-dash (T-CONS-R1) and an  # hygiene-ok: quoting this pattern's own example shapes, not a real hit
# optional trailing lowercase letter (T-S1b) apply here too, the same as  # hygiene-ok: quoting this pattern's own example shapes, not a real hit
# above. Matched case-insensitively, like the pattern above, so a lowercase
# id (t-s1) is not missed for its case alone.  # hygiene-ok: quoting this pattern's own example shapes, not a real hit
RECORD_ID_PATTERN_SEGMENT = re.compile(
    r"\b(?:D|T|Q|F)-(?:[A-Z]{1,4}-)?[A-Z]{1,4}[0-9]{1,2}[a-z]?\b", re.IGNORECASE
)

# A bare id (no dash at all -- D3, T14) is too easily an ordinary token to
# flag on its own; it counts only when the same line also carries a role
# word, a round<N> mention, or the word "finding". Case-insensitive, like
# both dashed patterns above.
BARE_RECORD_ID_PATTERN = re.compile(r"\b(?:D|T|Q|F)[0-9]{1,2}\b", re.IGNORECASE)
_FINDING_RE = re.compile(r"\bfinding\b", re.IGNORECASE)

# A prior version of this list also carried three broader prefixes on the  # hygiene-ok: describes the allowlist's own history, not a real hit
# theory that they were exclusively this codebase's own business-reference-
# number families. They are not: this codebase's task references and its
# readiness-finding references share exactly that shape, one and two digits  # hygiene-ok: describes the allowlist's own history, not a real hit
# after the leading letter and a dash, so the three broad prefixes were
# hiding review-record ids of the same letter-and-digit form, not just
# business references. Only three families
# actually need an exact-prefix entry here: "AD-" (architecture-decision
# references; never reaches this check anyway -- see the comment below),
# "DEL-D-" and (by the same construction, see the context check below)
# "DEL-F-" (ADM deliverable codes, one per phase letter; two of the eight
# phase letters happen to be D and F), and "F500-" (readiness-matrix finding
# numbers, also never reaches this check -- its digit run after "F" is never
# followed directly by a dash, so RECORD_ID_PATTERN never matches it at
# all). app/utils/reference_numbers.py supplies "AD-" at its one call site
# as a runtime value, not a constant, and has no list of the others;
# importing the application package into a static-analysis script to reach
# it would pull in Flask configuration and the database extensions for one
# string. The remaining prefixes (standards bodies, quarter and fiscal-year
# references, this codebase's other short reference families) are listed
# for documentation and defence-in-depth even where the D/T/Q/F restriction
# already excludes them by construction.
RECORD_ID_ALLOWLIST_PREFIXES = (
    "AD-", "DEL-D-", "F500-", "S0-",
    "CVE-", "RFC-", "ISO-", "IEC-", "UTF-", "SHA-", "SOC-", "GPT-", "BS-", "FY-",
)
_R_DIGIT_DASH_RE = re.compile(r"^R[0-9]-")
_Q_QUARTER_DASH_RE = re.compile(r"^Q[1-4]-")
# A deliverable code's own dash after the three-letter prefix is itself a  # hygiene-ok: describes the DEL- check's own reasoning, not a real hit
# word-boundary in the record-id pattern's terms, so the match it finds  # hygiene-ok: describes the DEL- check's own reasoning, not a real hit
# starts after that dash, never at the three-letter prefix -- which is why a
# plain prefix-of-token check (above) cannot exclude it. Checked against the
# text immediately before the match instead, the same way the WCAG "A-"
# check below reads its own preceding context rather than the token -- with
# a left boundary this one also needs: the three letters must not  # hygiene-ok: describes the DEL- check's own reasoning, not a real hit
# themselves be preceded by a letter, digit or underscore, or a longer word  # hygiene-ok: describes the DEL- check's own reasoning, not a real hit
# that merely ends the same way (a model name, say) is misread as the
# deliverable prefix and wrongly excluded.
_DEL_DELIVERABLE_CONTEXT_RE = re.compile(r"(?<![A-Za-z0-9_])DEL-$")


def _is_allowlisted_record_id(token: str, line: str, start: int) -> bool:
    """True if `token`, found at `start` in `line`, is this codebase's own
    business-reference-number convention rather than a review record id.
    Compared upper-cased throughout: the record-id patterns themselves now
    match case-insensitively, so a lowercase business reference must clear
    the same allowlist its uppercase form would."""
    upper = token.upper()
    if any(upper.startswith(p) for p in RECORD_ID_ALLOWLIST_PREFIXES):
        return True
    if _R_DIGIT_DASH_RE.match(upper) or _Q_QUARTER_DASH_RE.match(upper):
        return True
    if upper.startswith("A-") and line[max(0, start - 6):start].rstrip().upper().endswith("WCAG"):
        return True
    if (upper.startswith("D-") or upper.startswith("F-")) and (
        _DEL_DELIVERABLE_CONTEXT_RE.search(line[:start].upper())
    ):
        return True
    return False

# Case-insensitive, whole word. "round" matches one or more digits or a  # hygiene-ok: quoting this pattern's own example shapes, not a real hit
# spelled-out number one..nine (round-1, round 2, round 10, Round three),  # hygiene-ok: quoting this pattern's own example shapes, not a real hit
# the shape a pipeline round number takes, but not when followed by "of"
# (round of funding, a round of edits -- the ordinary English sense).
# "build report" matches a hyphen or a space. "qa-lead" matches a hyphen or  # hygiene-ok: quoting this pattern's own label, not a real hit
# a space, the same as "tech lead" below it. "builder" matches only its  # hygiene-ok: quoting this pattern's own label, not a real hit
# three role-shaped forms, not a class or identifier name built from the
# word (QueryBuilder, policy_builder) -- and not "the builder pattern", the
# unrelated Gang-of-Four design-pattern name, which "the builder" alone  # hygiene-ok: quoting this pattern's own label, not a real hit
# would otherwise also match. "orchestrator" requires the preceding  # hygiene-ok: quoting this pattern's own label, not a real hit
# character not be a letter or underscore, so an identifier such as
# `workflow_orchestrator_service` or a class name such as
# `UnifiedSeedOrchestrator` never matches.
ROLE_WORDS = [
    (re.compile(r"\brefuter\b", re.IGNORECASE), "refuter"),  # hygiene-ok: this pattern's own label, not a real hit
    (re.compile(r"\btech[\s-]lead\b", re.IGNORECASE), "tech-lead"),  # hygiene-ok: this pattern's own label, not a real hit
    (re.compile(r"\bqa[\s-]lead\b", re.IGNORECASE), "qa-lead"),  # hygiene-ok: this pattern's own label, not a real hit
    (re.compile(
        r"\bbuilder's\b|\bthe builder\b(?!\s+pattern\b)|\bbuilder:", re.IGNORECASE
    ), "builder"),  # hygiene-ok: this pattern's own label, not a real hit
    (re.compile(r"\bsolution-architect\b", re.IGNORECASE), "solution-architect"),  # hygiene-ok: this pattern's own label, not a real hit
    (re.compile(r"\bproduct-manager\b", re.IGNORECASE), "product-manager"),  # hygiene-ok: this pattern's own label, not a real hit
    (re.compile(r"\b(?<![A-Za-z_])orchestrator\b", re.IGNORECASE), "orchestrator"),  # hygiene-ok: this pattern's own label, not a real hit
    (re.compile(r"\bthe brief\b", re.IGNORECASE), "the brief"),  # hygiene-ok: this pattern's own label, not a real hit
    (re.compile(r"\bbuild[\s-]report\b", re.IGNORECASE), "build report"),  # hygiene-ok: this pattern's own label, not a real hit
    (re.compile(
        r"\bround[\s-]?(?:[0-9]+|one|two|three|four|five|six|seven|eight|nine)\b(?!\s+of\b)",
        re.IGNORECASE,
    ), "round<N>"),
]

# Product vocabulary that legitimately contains a role word as a substring
# (e.g. "orchestrator" inside "seed orchestrator") -- checked, and masked  # hygiene-ok: quoting this comment's own example, not a real hit
# out of the line, before ROLE_WORDS runs against it.
PRODUCT_TERMS = (
    "seed orchestrator",  # hygiene-ok: this allowlist's own entry, not a real hit
    "workflow orchestrator",  # hygiene-ok: this allowlist's own entry, not a real hit
    "dual-agent orchestrator",  # hygiene-ok: this allowlist's own entry, not a real hit
    "orchestration",
    "decision brief",  # hygiene-ok: this allowlist's own entry, not a real hit
    "codegen brief",  # hygiene-ok: this allowlist's own entry, not a real hit
)
_PRODUCT_TERM_RE = re.compile(
    "|".join(re.escape(term) for term in PRODUCT_TERMS), re.IGNORECASE
)


def _mask_product_terms(text: str) -> str:
    """Replace each PRODUCT_TERMS occurrence with spaces of the same
    length, so a role word matching only inside one of these phrases is
    never counted; length-preserving keeps every other match's column
    position in `text` correct."""
    return _PRODUCT_TERM_RE.sub(lambda m: " " * len(m.group(0)), text)


def _role_word_hits(text: str) -> list[str]:
    """Labels of every ROLE_WORDS pattern that matches `text`, after
    masking PRODUCT_TERMS."""
    masked = _mask_product_terms(text)
    return [label for pattern, label in ROLE_WORDS if pattern.search(masked)]


# Checked before the escape hatch, on every commit-message line, and never
# excused by one: an attribution trailer on a line of its own is exactly
# what the escape hatch exists to let a genuinely necessary role word or
# record id through, not a trailer. A space or a hyphen between each word,
# not only the git-trailer's own hyphenated spelling: "Co authored by  # hygiene-ok: describing this pattern's own example text, not a real hit
# GitHub Copilot", a tool's own free-text rendering of the same trailer,
# carries the identical disclosure with none of the hyphens.
_TRAILER_RE = re.compile(r"co[\s-]?authored[\s-]?by", re.IGNORECASE)
# A free-text attribution footer some tools append instead of (or beside) a
# Co-authored-by trailer -- "Generated with <tool>", with or without a  # hygiene-ok: describing this pattern's own example text, not a real hit
# leading emoji or a markdown link. The wording after "with" is not
# checked; a footer disclosing generation by anything is the thing being
# excluded here, not only a named one. The trailing \b matters: without it
# this also matches "generated without", an ordinary phrase this codebase's
# own docstrings and help text use for something that did NOT go through a
# tool ("Show what would be generated without creating elements") -- "with"
# is a literal prefix of "without", not a whole word inside it.
_GENERATED_WITH_RE = re.compile(r"generated\s+with\b", re.IGNORECASE)
# An assistant coding tool's own product name, whole word only (so this
# never fires on an unrelated word that merely contains one of them) --
# checked against the whole message line, not only a "Key: value" trailer
# shape: a prose sentence naming the same tool ("co-authored by <tool>",  # hygiene-ok: describing this pattern's own example text, not a real hit
# "written with <tool>") carries no trailer shape at all and discloses
# exactly what the trailer/footer checks above exist to catch. One name
# collides with this repository's own governance-file name, referenced
# constantly in ordinary commit messages having nothing to do with the
# tool that shares its name -- excluded by name, the same way rule 3's
# PRODUCT_TERMS excludes this codebase's own vocabulary from the role-word
# check above.
#
# "Copilot" is deliberately absent from this list: it is this product's
# own in-app feature name (an AI assistant surfaced throughout the
# product, with its own service module and its own tests), not only an
# unrelated coding tool that happens to share the word -- unlike the
# governance-file collision above, there is no narrower shape left to
# exclude and still catch a genuine disclosure, so the name is dropped
# entirely rather than partially excluded. "aider" is a fourth coding
# tool's own name, checked the same way as the other three: its default
# author-name suffix and its default commit-subject prefix both carry the
# bare word, with nothing else marking either as a disclosure.
_ASSISTANT_PRODUCT_RE = re.compile(r"\b(?:claude|codex|kilo|aider)\b", re.IGNORECASE)
_GOVERNANCE_FILE_NAME_RE = re.compile(r"\bclaude\.md\b", re.IGNORECASE)


def _assistant_product_hit(line: str) -> bool:
    """True if `line` names an assistant coding tool's own product,
    outside a mention of this repository's own governance-file name."""
    return bool(_ASSISTANT_PRODUCT_RE.search(_GOVERNANCE_FILE_NAME_RE.sub("", line)))


# A commit message is prose about a CHANGE: an assistant product name
# appearing anywhere in it is already unusual enough to be worth a look,
# which is why the commit-message half above fires on the bare name alone
# (see test_public_repo_hygiene_commit_message_assistant_product_name_fires_on_an_unrelated_mention_too).
# Tracked SOURCE is not the same kind of text: this codebase is itself an
# AI-integration product that names Claude/Codex-family models as ordinary,
# first-class vocabulary throughout its own LLM-routing code (provider
# lists, default-model ids, token-limit tables), hundreds of times over,
# and none of that is a disclosure. The source half of this same check
# therefore also requires a co-located attribution cue -- the same word an
# actual "written/generated/co-authored/reviewed/paired/assisted ... with
# or by <tool>" disclosure sentence would carry -- on the same line, the
# same shape of extra signal BARE_RECORD_ID_PATTERN already needs before it
# counts a bare id on its own.
_ATTRIBUTION_CUE_RE = re.compile(
    r"\b(?:generated|written|co-?authored|reviewed|paired|assisted)\b", re.IGNORECASE
)


def _assistant_product_hit_in_source(line: str) -> bool:
    """True if `line` both names an assistant product AND carries an
    attribution-shaped cue word -- see _ATTRIBUTION_CUE_RE's own comment
    for why the source half needs the extra signal the commit-message half
    does not."""
    return bool(_ATTRIBUTION_CUE_RE.search(line)) and _assistant_product_hit(line)


# A disclosure that names the KIND of tool rather than one of the specific
# products above ("Generated by an AI coding assistant") -- no product name  # hygiene-ok: describing this pattern's own example text, not a real hit
# to anchor on, so this always needs the same attribution-cue co-location
# `_assistant_product_hit_in_source` needs for tracked source, on both
# halves. "coding" is required, not optional: this codebase is itself an
# AI-integration product whose own domain vocabulary pairs a cue word with
# a bare "AI"/"LLM" constantly and legitimately ("LLM-generated
# recommendation", "AI-generated proposal" describe data the PRODUCT
# generates for a user, not a disclosure about how the source itself was
# written) -- "coding assistant" is specific enough not to collide with
# any of it, where a bare "AI assistant" or "LLM" was found, on this exact
# codebase, to collide by the dozens.
_GENERIC_ASSISTANT_RE = re.compile(
    r"\b(?:an?\s+)?(?:ai|artificial[\s-]intelligence)[\s-]coding[\s-]assistant\b",
    re.IGNORECASE,
)


def _generic_assistant_disclosure_hit(line: str) -> bool:
    """True if `line` carries both an attribution-shaped cue word and a
    generic, unnamed assistant-tool phrase -- see _GENERIC_ASSISTANT_RE's
    own comment for why the bare phrase alone is not enough."""
    return bool(_ATTRIBUTION_CUE_RE.search(line)) and bool(_GENERIC_ASSISTANT_RE.search(line))

# .js so app/static's authored JS is covered; vendor/bundles/*.min.js are
# third-party or built output, never authored comments, and would be pure
# noise (minified variable names and CSS/coordinate data happen to match
# RECORD_ID_PATTERN by the thousand).
CONTENT_EXTENSIONS = (".py", ".html", ".j2", ".md", ".js")
CONTENT_SKIP_DIRNAMES = {"__pycache__", "node_modules", ".git", "vendor", "bundles"}
CONTENT_SKIP_SUFFIXES = (".min.js",)

# ---------------------------------------------------------------- rule 3: narrowing the source scan to comments, docstrings and string literals

# The comment, Jinja-expression/statement and tag patterns, and the
# length-preserving blanking primitive built on them, live in hygiene_text
# -- shared with check_duplicate_breadcrumb.py, check_placeholder_copy.py
# and check_broken_surfaces.py, which each needed one or more of the same
# patterns or the same primitive. Only the two block patterns below are
# unique to this checker (no sibling reads an HTML <script> or <style>
# element specifically) and stay local.
_HTML_SCRIPT_BLOCK_RE = re.compile(r"<script\b[^>]*>(.*?)</script\s*>", re.DOTALL | re.IGNORECASE)
_HTML_STYLE_BLOCK_RE = re.compile(r"<style\b[^>]*>.*?</style\s*>", re.DOTALL | re.IGNORECASE)
# title=/alt=/placeholder=/aria-label= attribute values -- also text a user
# reads or a screen reader announces, not markup, the same reasoning
# check_placeholder_copy.py's own `ARIA` pattern already applies to
# aria-label specifically. Read from whichever tags remain once comments,
# script/style bodies and Jinja code are blanked out, so a same-shaped JS
# assignment inside a <script> body is never read as one. Anchored on a
# preceding character that is neither a word character nor a hyphen, not a
# bare `\b` and not a bare preceding-whitespace requirement: a hyphen is
# itself a word boundary in regex terms, so `\btitle\b` alone also matches
# the "title" inside "data-title" -- a real, different attribute -- and a
# whitespace-only lookbehind misses an attribute with no space before it
# (`<a href="x"title="y">`, a real, if unusual, shape a hand-written or
# minified template can carry). Matched only within each tag's own span
# (below), never over the surrounding text, so a visible-text node that
# happens to contain this shape is not read as an attribute and then read
# again as text.
_HTML_TEXT_ATTR_RE = re.compile(
    r"(?<![\w-])(?:title|alt|placeholder|aria-label)\s*=\s*(?:\"([^\"]*)\"|'([^']*)')",
    re.IGNORECASE,
)


def _spans_to_lines(text: str, spans: list[tuple[int, str]]):
    """(lineno, text) for every physical line inside each `(start_offset,
    matched_text)` span, so a match inside a multi-line span still reports
    the line it is actually on."""
    for start, matched in spans:
        start_line = text.count("\n", 0, start) + 1
        for offset, line_text in enumerate(matched.split("\n")):
            yield start_line + offset, line_text


# An f-string's own literal text tokenises as one or more FSTRING_MIDDLE
# tokens, one per span between `{expression}` parts, never as a single
# STRING token the way every other string literal does -- present from the
# tokenizer version that ships with this codebase's interpreter, absent on
# an older one, so read defensively rather than assumed.
_FSTRING_MIDDLE = getattr(tokenize, "FSTRING_MIDDLE", None)
_PY_STRING_TOKEN_TYPES = (tokenize.COMMENT, tokenize.STRING) + (
    (_FSTRING_MIDDLE,) if _FSTRING_MIDDLE is not None else ()
)


class _UntrustedUnparsedFile(Exception):
    """A tracked `.py` file could not be tokenised -- see
    `_python_comment_and_string_lines`. Not trusted as "nothing to
    report": a file this checker cannot read the comments and strings of
    is a file this checker cannot vouch for, the same reasoning
    `_UntrustedZeroFileCount` already applies to a suspiciously empty
    tracked-file list -- yielding nothing for an unparsed file would be a
    silent clean report on a file that was never actually scanned."""

    def __init__(self, path: str, reason: str):
        super().__init__(f"{path}: {reason}")
        self.path = path
        self.reason = reason


def _python_comment_and_string_lines(path: str):
    """(lineno, text) for every COMMENT, STRING and FSTRING_MIDDLE token --
    a docstring is a STRING token, so this covers both without a separate
    case, and an f-string's literal text needs FSTRING_MIDDLE specifically
    (see above) to be scanned at all. A file that fails to tokenise (a
    syntax error, or a decode failure) raises `_UntrustedUnparsedFile`
    rather than silently yielding nothing -- see that exception's own
    docstring."""
    try:
        with open(path, "rb") as fh:
            tokens = list(tokenize.tokenize(fh.readline))
    except (tokenize.TokenError, SyntaxError, IndentationError, OSError, UnicodeDecodeError) as exc:
        raise _UntrustedUnparsedFile(path, str(exc)) from exc
    for tok in tokens:
        if tok.type not in _PY_STRING_TOKEN_TYPES:
            continue
        start_line = tok.start[0]
        for offset, line_text in enumerate(tok.string.split("\n")):
            yield start_line + offset, line_text


def _markup_comment_lines(path: str):
    """(lineno, text) for every `{# #}` and `<!-- -->` block, .html/.j2."""
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            text = fh.read()
    except OSError:
        return
    spans = [(m.start(), m.group(0)) for m in hygiene_text.HTML_COMMENT_RE.finditer(text)]
    spans += [(m.start(), m.group(0)) for m in hygiene_text.JINJA_COMMENT_RE.finditer(text)]
    yield from _spans_to_lines(text, spans)


def _js_comment_lines(path: str):
    """(lineno, text) for every `//` and `/* */` comment, .js."""
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            text = fh.read()
    except OSError:
        return
    yield from _spans_to_lines(text, hygiene_text.js_comment_spans(text))


def _html_script_and_text_lines(path: str):
    """(lineno, text) for an HTML/Jinja file's inline <script> body --
    scanned with the same comment rules as a .js file, because a script
    element's content is JavaScript, not markup -- its title=/alt=/
    placeholder=/aria-label= attribute values (also text a user reads or a
    screen reader announces, not markup), and its visible text nodes: the
    literal text a browser actually renders between tags. A <!-- --> /
    {# #} comment (scanned separately by _markup_comment_lines), a
    <script> or <style> block, and a `{{ }}` expression or `{% %}`
    statement are blanked out first (length-preserving, see
    hygiene_text.blank) -- leaving tags and their attributes intact, so the
    four text-bearing attributes can be read off them -- and every
    remaining tag is then itself blanked, in turn, to leave only the
    visible-text lines."""
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            text = fh.read()
    except OSError:
        return

    script_spans: list[tuple[int, str]] = []
    for m in _HTML_SCRIPT_BLOCK_RE.finditer(text):
        inner, offset = m.group(1), m.start(1)
        script_spans.extend(
            (offset + start, matched) for start, matched in hygiene_text.js_comment_spans(inner)
        )
    yield from _spans_to_lines(text, script_spans)

    pre_tag = text
    for pattern in (
        hygiene_text.HTML_COMMENT_RE, hygiene_text.JINJA_COMMENT_RE, _HTML_SCRIPT_BLOCK_RE,
        _HTML_STYLE_BLOCK_RE, hygiene_text.JINJA_EXPR_OR_STMT_RE,
    ):
        pre_tag = hygiene_text.mask(pre_tag, pattern)

    # Read title=/alt=/placeholder=/aria-label= attribute values while the
    # tags that carry them are still intact -- a same-shaped assignment
    # inside a <script> body is already blanked out above by this point,
    # so only a real HTML attribute is read here. Matched inside each
    # tag's own span, one tag at a time, not over the whole blob: the
    # attribute value pattern alone has no notion of "inside a tag", so
    # run unscoped it would also match the same shape sitting in a text
    # node between tags -- counting that node once as an "attribute" here
    # and a second time as visible text once tags are blanked out below.
    attr_spans: list[tuple[int, str]] = []
    for tag_m in hygiene_text.HTML_TAG_RE_LOOSE.finditer(pre_tag):
        tag_text, tag_start = tag_m.group(0), tag_m.start()
        for m in _HTML_TEXT_ATTR_RE.finditer(tag_text):
            value, value_start = (
                (m.group(1), m.start(1)) if m.group(1) is not None else (m.group(2), m.start(2))
            )
            attr_spans.append((tag_start + value_start, value))
    yield from _spans_to_lines(text, attr_spans)

    masked = hygiene_text.mask(pre_tag, hygiene_text.HTML_TAG_RE_LOOSE)
    yield from enumerate(masked.split("\n"), start=1)


def _whole_file_lines(path: str):
    """(lineno, text) for every line, .md -- a documentation file is prose
    throughout, so there is no code to exclude."""
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            lines = fh.readlines()
    except OSError:
        return
    yield from enumerate(lines, start=1)


def _is_html_like_j2(path: str) -> bool:
    """True for `.html`, and for a `.j2` template whose own base name is
    itself `....html.j2` or carries no other recognisable extension at all
    (a bare `foo.j2` fragment, an include with no second extension) --
    the shape `_html_script_and_text_lines` is written for: real markup,
    real tags, real attributes, real visible text between them. A `.j2`
    template whose base name carries a DIFFERENT extension (`.go.j2`,
    `.py.j2`, `.sql.j2`, `.sh.j2` and the rest of this codebase's own
    multi-language code generator output under templates/go_chi and
    templates/python_fastapi) is not markup at all -- its "visible text"
    is executable source in another language entirely, and reading it as
    HTML would scan that source line for line as if it were rendered
    page text, contrary to this module's own "never executable code"
    claim. Its Jinja comments are still read regardless, by
    `_markup_comment_lines`: a `{# #}` comment is the templating engine's
    own syntax, independent of what language the template renders."""
    if path.endswith(".html"):
        return True
    if not path.endswith(".j2"):
        return False
    base = os.path.basename(path[: -len(".j2")])
    _, ext = os.path.splitext(base)
    return ext == "" or ext.lower() == ".html"


def _scannable_lines(path: str):
    """Dispatch to the extension-appropriate narrowing above."""
    if path.endswith(".py"):
        yield from _python_comment_and_string_lines(path)
    elif path.endswith((".html", ".j2")):
        yield from _markup_comment_lines(path)
        if _is_html_like_j2(path):
            yield from _html_script_and_text_lines(path)
    elif path.endswith(".js"):
        yield from _js_comment_lines(path)
    elif path.endswith(".md"):
        yield from _whole_file_lines(path)


def _tracked_bucket_paths(root: str) -> list[str]:
    bucket_dir = os.path.join(root, "docs", "buckets")
    if not os.path.isdir(bucket_dir):
        return []
    found = []
    for dirpath, _dirnames, filenames in os.walk(bucket_dir):
        for name in filenames:
            found.append(os.path.relpath(os.path.join(dirpath, name), root).replace(os.sep, "/"))
    return found


def _scan_for_references(root: str) -> list[str]:
    problems = []
    for scan_dir in SCAN_DIRS:
        base = os.path.join(root, scan_dir)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in ("__pycache__", "node_modules", ".git")]
            for name in filenames:
                if name == SELF_NAME:
                    continue
                if not name.endswith((".py", ".html", ".j2", ".md")):
                    continue
                path = os.path.join(dirpath, name)
                try:
                    with open(path, encoding="utf-8", errors="ignore") as fh:
                        lines = fh.readlines()
                except OSError:
                    continue
                for lineno, line in enumerate(lines, start=1):
                    if PATTERN in line and ESCAPE_HATCH not in line:
                        rel = os.path.relpath(path, root).replace(os.sep, "/")
                        problems.append(f"{rel}:{lineno}: references '{PATTERN}'")
    return problems


def _tracked_content_paths(root: str) -> list[str] | None:
    """Relative paths (forward slashes) of every git-tracked file under
    SCAN_DIRS, via `git ls-files`, so an untracked local file is never
    scanned as if it were already committed source. Returns None when
    `root` is not a git working tree or git is unavailable -- the caller
    falls back to a directory walk and says so."""
    try:
        proc = subprocess.run(
            ["git", "-C", root, "ls-files", "-z", "--", *SCAN_DIRS],
            capture_output=True, timeout=120,
            encoding="utf-8", errors="replace",
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return [p for p in (proc.stdout or "").split("\x00") if p]


class _UntrustedZeroFileCount(Exception):
    """`git ls-files` ran cleanly and reported zero tracked files under a
    scan directory that exists on disk -- see `_iter_content_files`. Not a
    subclass of a git-related error: this is not a git failure, it is a git
    success this checker refuses to trust."""


def _iter_content_files(root: str):
    tracked = _tracked_content_paths(root)
    if tracked is not None:
        if not tracked and any(os.path.isdir(os.path.join(root, d)) for d in SCAN_DIRS):
            # `git ls-files` exiting 0 with no output normally means "nothing
            # tracked here" -- correct for a scan directory that doesn't
            # exist. It does not mean that when the directory exists on disk:
            # a real, populated app/scripts/tests/templates tree with git
            # reporting zero tracked files under any of them is a broken
            # read (wrong cwd, a detached or partial checkout, a `-C root`
            # pointed somewhere git does not expect), not an empty
            # repository, and reporting a clean scan of zero files would be
            # exactly the silent-pass this checker's own commit-message half
            # already refuses to allow on a git failure.
            print(
                f"error: `git ls-files` reported zero tracked files under "
                f"{', '.join(SCAN_DIRS)} in {root}, although at least one of "
                f"those directories exists on disk -- refusing to report a "
                f"clean scan of zero files",
                file=sys.stderr,
            )
            raise _UntrustedZeroFileCount()
        for rel in tracked:
            parts = rel.split("/")  # git ls-files always uses forward slashes
            name = parts[-1]
            if name == SELF_NAME:
                continue
            if not name.endswith(CONTENT_EXTENSIONS):
                continue
            if name.endswith(CONTENT_SKIP_SUFFIXES):
                continue
            if any(part in CONTENT_SKIP_DIRNAMES for part in parts[:-1]):
                continue
            yield os.path.join(root, *parts)
        return

    print(f"note: {root} is not a git working tree (or git is unavailable) -- "
          f"falling back to a directory walk, which may include untracked files",
          file=sys.stderr)
    for scan_dir in SCAN_DIRS:
        base = os.path.join(root, scan_dir)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in CONTENT_SKIP_DIRNAMES]
            for name in filenames:
                if name == SELF_NAME:
                    continue
                if not name.endswith(CONTENT_EXTENSIONS):
                    continue
                if name.endswith(CONTENT_SKIP_SUFFIXES):
                    continue
                yield os.path.join(dirpath, name)


def _adjacent_to_bracket(line: str, start: int, end: int) -> bool:
    """True if the match at [start, end) sits immediately inside [ ] -- a
    regex character class (the "Z0-9" out of "[A-Z0-9]"), not a record id."""
    before = line[start - 1] if start > 0 else ""
    after = line[end:end + 1]
    return before in ("[", "]") or after in ("[", "]")


def _record_id_matches(line: str):
    """(start, token) for every non-allowlisted dashed record-id-shaped
    match on `line`."""
    for pattern in (RECORD_ID_PATTERN, RECORD_ID_PATTERN_SEGMENT):
        for m in pattern.finditer(line):
            token = m.group(0)
            if _is_allowlisted_record_id(token, line, m.start()):
                continue
            if _adjacent_to_bracket(line, m.start(), m.end()):
                continue
            yield m.start(), token


def _bare_record_id_matches(line: str):
    """(start, token) for every bare (no-dash) record-id-shaped match on
    `line` -- the caller filters these by same-line context."""
    for m in BARE_RECORD_ID_PATTERN.finditer(line):
        token = m.group(0)
        if _adjacent_to_bracket(line, m.start(), m.end()):
            continue
        yield m.start(), token


def _scan_record_ids_and_role_words(root: str) -> list[str] | None:
    """Rule 3, source half: review-record-id tokens, pipeline role words,
    an attribution trailer, a generated-with footer, an assistant product
    name, and a generic (unnamed) assistant-tool disclosure, scanned only
    inside comments, docstrings and string literals (never executable
    code) under app/, scripts/, tests/, templates and static JS -- see
    `_scannable_lines` for the per-extension narrowing; .md files are
    scanned whole, being prose throughout. The trailer and generated-with
    checks reuse `_TRAILER_RE`/`_GENERATED_WITH_RE` unchanged from the
    commit-message half -- a disclosure written into a comment is the same
    disclosure whether it sits in a commit message or the file it was
    committed alongside. The two assistant-name checks do not reuse
    `_assistant_product_hit` directly; see `_assistant_product_hit_in_source`
    and `_generic_assistant_disclosure_hit` for why tracked source needs a
    narrower rule than a commit message does.

    Unlike the commit-message half, where none of the checks is ever
    excused, every check here is excusable by the same per-line marker the
    record-id and role-word checks already use: the escape hatch's own
    documented scope is "rule 3's source half", with no carve-out, and a
    test fixture that has to construct one of these shapes to prove this
    checker catches it (this file's own tests included) needs a way to say
    so without rewording the probe out of the shape being tested.

    The escape hatch is checked against the physical source line, not the
    comment/string fragment: a string literal and a trailing `#
    hygiene-ok:` comment can share one physical line as two separate
    tokens, and the marker still has to excuse the whole line, not just
    the token it happens to sit in.

    Returns None, not an empty list, when the file list itself could not be
    trusted -- `git ls-files` reporting zero tracked files under a scan
    directory that exists on disk (see `_iter_content_files`), or a
    tracked `.py` file that failed to tokenise (see
    `_python_comment_and_string_lines` and `_UntrustedUnparsedFile`). A
    caller must not read either as a clean scan with nothing to report."""
    problems = []
    try:
        for path in _iter_content_files(root):
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            try:
                with open(path, encoding="utf-8", errors="ignore") as fh:
                    raw_lines = fh.readlines()
            except OSError:
                raw_lines = []
            for lineno, line in _scannable_lines(path):
                raw = raw_lines[lineno - 1] if 0 < lineno <= len(raw_lines) else line
                if ESCAPE_HATCH in raw:
                    continue
                if _TRAILER_RE.search(line):
                    problems.append(f"{rel}:{lineno}: an attribution trailer in tracked source")
                if _GENERATED_WITH_RE.search(line):
                    problems.append(f"{rel}:{lineno}: a 'generated with' footer line in tracked source")  # hygiene-ok: describing this error message's own label, not a real hit
                if _assistant_product_hit_in_source(line):
                    problems.append(f"{rel}:{lineno}: an assistant product name in tracked source")
                if _generic_assistant_disclosure_hit(line):
                    problems.append(f"{rel}:{lineno}: a generic AI-assistant disclosure in tracked source")
                for _start, token in _record_id_matches(line):
                    problems.append(f"{rel}:{lineno}: looks like a review record id: '{token}'")
                labels = _role_word_hits(line)
                has_context = bool(labels) or bool(_FINDING_RE.search(_mask_product_terms(line)))
                if has_context:
                    for _start, token in _bare_record_id_matches(line):
                        problems.append(f"{rel}:{lineno}: looks like a review record id: '{token}'")
                for label in labels:
                    problems.append(f"{rel}:{lineno}: pipeline role word '{label}'")
    except _UntrustedZeroFileCount:
        return None
    except _UntrustedUnparsedFile as exc:
        print(
            f"error: {exc.path} could not be tokenised as Python ({exc.reason}) -- "
            f"refusing to report a clean scan that silently skipped it",
            file=sys.stderr,
        )
        return None
    return problems


def _iter_commit_messages(root: str, rev_range: str | None) -> list[tuple[str, str, str, str]] | None:
    """(sha, author name, committer name, full message) for every commit
    `git -C root log` can reach, oldest first. Uses ASCII unit/record
    separators (not present in any real commit message) to split reliably
    on multi-line messages. The author and committer names are read
    alongside the message body -- not only `%B` -- because a coding
    tool's own disclosure is not always written into the message text: a
    tool that signs its own commits with a name suffix (one tool's default
    author name is the ordinary git user name plus a literal `(aider)`)
    discloses itself there and nowhere else.

    Returns None, not an empty list, when git itself could not be run or
    failed -- the caller must not treat that the same as a clean history
    with nothing to report; see _scan_commit_messages and main() below."""
    args = ["git", "-C", root, "log", "--format=%H%x1f%an%x1f%cn%x1f%B%x1e"]
    if rev_range:
        args.append(rev_range)
    try:
        # encoding/errors pinned explicitly: text=True alone decodes with the
        # PARENT's locale encoding (cp1252 on a default Windows console)
        # regardless of the child's actual output, and this repository's own
        # history contains non-cp1252 bytes -- see scripts/verify.py's _run()
        # for the same fix, made once there already; reused here rather than
        # re-discovering it (this script has no import of verify.py to share
        # the helper directly -- each scripts/check_*.py is standalone).
        proc = subprocess.run(
            args, capture_output=True, timeout=120,
            encoding="utf-8", errors="replace",
        )
    except OSError as exc:
        print(f"error: could not run git: {exc}", file=sys.stderr)
        return None
    if proc.returncode != 0:
        print(f"error: git log failed (exit {proc.returncode}): {proc.stderr.strip()}",
              file=sys.stderr)
        return None
    records = [r for r in (proc.stdout or "").split("\x1e") if r.strip()]
    result = []
    for rec in records:
        parts = rec.split("\x1f", 3)
        if len(parts) != 4:
            continue
        sha, author, committer, message = parts
        result.append((sha.strip(), author, committer, message))
    return result


def _scan_commit_messages(root: str, rev_range: str | None = None) -> list[str] | None:
    """Rule 3, commit-message half: review-record-id tokens, pipeline role
    words, an attribution trailer, a generated-with footer, a named
    assistant product, and a generic (unnamed) assistant-tool disclosure,
    anywhere in the message, in the commit message itself (not the diff),
    plus the same named-product check against the commit's own author and
    committer names -- see `_iter_commit_messages` for why those two
    fields are read at all. Record ids and role words are found with the
    same `_record_id_matches`/`_bare_record_id_matches`/`_role_word_hits`
    helpers the source half uses, so the two halves cannot drift apart on
    what counts as a hit -- only on what they scan. The escape hatch
    excuses only the physical line it sits on, not the whole message; none
    of the attribution checks below is ever excused by it, marker or not
    -- each is checked, and can report, before the escape hatch is even
    read (and the author/committer name checks are never excusable at
    all: a marker sits in the message body, never in a name field).
    Zero tolerance, scoped to the commits under review -- see verify.py's
    ``gate_public_repo_hygiene_commit_messages`` for why a full-history
    count cannot be a stable measurement here.

    Returns None when the underlying git read failed -- never an empty
    list, which a caller could otherwise mistake for zero hits."""
    messages = _iter_commit_messages(root, rev_range)
    if messages is None:
        return None
    problems = []
    for sha, author, committer, message in messages:
        for who, name in (("author", author), ("committer", committer)):
            if _assistant_product_hit(name):
                problems.append(f"{sha[:8]}: an assistant product name in the {who} name")
        for line in message.splitlines():
            if _TRAILER_RE.search(line):
                problems.append(f"{sha[:8]}: an attribution trailer in the commit message")
                continue
            if _GENERATED_WITH_RE.search(line):
                problems.append(f"{sha[:8]}: a 'generated with' footer line in the commit message")  # hygiene-ok: describing this error message's own label, not a real hit
                continue
            if _assistant_product_hit(line):
                problems.append(f"{sha[:8]}: an assistant product name in the commit message")
                continue
            if _generic_assistant_disclosure_hit(line):
                problems.append(f"{sha[:8]}: a generic AI-assistant disclosure in the commit message")
                continue
            if ESCAPE_HATCH in line:
                continue
            for _start, token in _record_id_matches(line):
                problems.append(f"{sha[:8]}: looks like a review record id: '{token}'")
            labels = _role_word_hits(line)
            has_context = bool(labels) or bool(_FINDING_RE.search(_mask_product_terms(line)))
            if has_context:
                for _start, token in _bare_record_id_matches(line):
                    problems.append(f"{sha[:8]}: looks like a review record id: '{token}'")
            for label in labels:
                problems.append(f"{sha[:8]}: '{label}' in the commit message")
    return problems


def scan(root: str) -> list[str]:
    problems = [f"tracked in docs/buckets/: {p}" for p in _tracked_bucket_paths(root)]
    problems += _scan_for_references(root)
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", action="store_true")
    parser.add_argument("--root", default=ROOT)
    parser.add_argument(
        "--rule", action="append", choices=["buckets", "content", "commits"],
        help="which rule(s) to run; repeatable. Default: buckets only, "
             "matching this script's original, still-zero-tolerance behaviour.",
    )
    parser.add_argument("--range", dest="rev_range", default=None,
                        help="commit range for --rule commits (default: full history)")
    args = parser.parse_args()

    rules = args.rule or ["buckets"]
    root = os.path.abspath(args.root)

    problems: list[str] = []
    if "buckets" in rules:
        problems += scan(root)
    if "content" in rules:
        content_problems = _scan_record_ids_and_role_words(root)
        if content_problems is None:
            # Same reasoning as the commits branch below: no count printed
            # at all, so a caller parsing the last stdout line as an
            # integer fails to parse it and reports FAIL, not zero.
            print("error: could not read the tracked file list for --rule "
                  "content -- see stderr above", file=sys.stderr)
            return 2
        problems += content_problems
    if "commits" in rules:
        commit_problems = _scan_commit_messages(root, args.rev_range)
        if commit_problems is None:
            # Do not print a count at all: a caller parsing the last stdout
            # line as an integer (scripts/verify.py's gate) must fail to
            # parse it and report FAIL, not read an absent problem as zero.
            print("error: could not read commit history for --rule commits "
                  "-- see stderr above", file=sys.stderr)
            return 2
        problems += commit_problems

    if not args.count:
        for line in problems:
            print("  " + line)
        if problems:
            print()
            print(
                "Reword the line; keep process references out of this repository.\n"
                "Mark a genuinely necessary line (or commit-message line) with\n"
                "'hygiene-ok: <reason>' (e.g. this checker's own comments)."
            )
    print(len(problems))
    return 0


if __name__ == "__main__":
    sys.exit(main())
