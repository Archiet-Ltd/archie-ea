"""Spelling-blind text comparison, shared by every search surface.

The product's own copy is en-GB ("Licences", "Organisation"), but a user typing from habit may type
either spelling. `normalise_search_text` folds both directions of the four variant pairs this product's
labels are known to use, so `normalise_search_text(query) in normalise_search_text(label)` is spelling-blind
for those words without touching anything else. Add a pair here, not a special case at each call site.

Typo tolerance: for words of five letters or more, allow one or two letters off (Levenshtein distance <= 2).
Exact matches rank first.
"""
import re

# (British, American) pairs. Both spellings fold to the American form purely as a stable choice; nothing
# reads the canonical form itself, only compares it, so which side is "canonical" carries no meaning.
_VARIANT_PAIRS = [
    ("licence", "license"),
    ("organisation", "organization"),
    ("programme", "program"),
    ("rationalisation", "rationalization"),
]

# A query is typed one character at a time, so it is very often a PREFIX of a full word, not the word
# itself -- "licenc" while typing "licence". The two spellings diverge partway through ("licen" then
# "c" vs "s"), so folding only the complete British word left every prefix from the divergence point up
# to (but not including) the final character unfoldable: "licenc" stayed "licenc", which is not a
# substring of the folded label "licenses", and the box reported "No pages match" mid-word on exactly
# the words this feature exists to fix. Folding every prefix of the British spelling to the
# same-length prefix of the American spelling covers it: prefixes before the divergence point are
# identical in both spellings anyway (folding is a no-op there), and prefixes at or past it map to the
# American prefix, which the folded label's American form always starts with.
_PREFIX_PAIRS = [
    (british[:n], american[:n])
    for british, american in _VARIANT_PAIRS
    for n in range(len(british), 0, -1)
]
# Longest alternative first: re's alternation takes the first match at each position, not the longest,
# so a shorter prefix listed before a longer one would shadow it (e.g. "licen" matching before "licence").
_VARIANT_RE = re.compile(
    "|".join(re.escape(british) for british, _ in sorted(_PREFIX_PAIRS, key=lambda p: -len(p[0]))),
    re.IGNORECASE,
)
_CANONICAL = dict(_PREFIX_PAIRS)


def _fold(match: "re.Match[str]") -> str:
    return _CANONICAL[match.group(0).lower()]


def normalise_search_text(text: str) -> str:
    """Lowercase `text` and fold every known British spelling variant to its American form."""
    return _VARIANT_RE.sub(_fold, text.lower())


def _levenshtein(a: str, b: str, max_dist: int = 2) -> int:
    """Levenshtein distance with early exit when distance exceeds max_dist."""
    if abs(len(a) - len(b)) > max_dist:
        return max_dist + 1
    if a == b:
        return 0
    # Ensure a is the shorter string
    if len(a) > len(b):
        a, b = b, a
    previous_row = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        current_row = [i + 1]
        for j, cb in enumerate(b):
            insert_cost = current_row[j] + 1
            delete_cost = previous_row[j + 1] + 1
            replace_cost = previous_row[j] + (ca != cb)
            current_row.append(min(insert_cost, delete_cost, replace_cost))
        if min(current_row) > max_dist:
            return max_dist + 1
        previous_row = current_row
    return previous_row[-1]


def words_match_fuzzy(query_word: str, label_word: str, max_dist: int = 2) -> bool:
    """True if query_word matches label_word with up to max_dist edits (for words >= 5 letters)."""
    if len(query_word) < 5 or len(label_word) < 5:
        return query_word == label_word
    return _levenshtein(query_word, label_word, max_dist) <= max_dist


def fuzzy_match_score(query: str, label: str) -> tuple[int, int]:
    """
    Score a label against a query for fuzzy matching.
    Returns (exact_match_count, fuzzy_match_count) where higher is better.
    Exact matches (substring after normalisation) count more than fuzzy matches.
    """
    q_norm = normalise_search_text(query)
    l_norm = normalise_search_text(label)

    q_words = q_norm.split()
    l_words = l_norm.split()

    exact = 0
    fuzzy = 0

    for qw in q_words:
        # Check for exact substring match first (covers multi-word phrases)
        if qw in l_norm:
            exact += 1
            continue
        # Check word-level fuzzy match
        for lw in l_words:
            if words_match_fuzzy(qw, lw):
                fuzzy += 1
                break

    return (exact, fuzzy)
