"""What a chat reply may say about its own searching, and where a fact came from.

Three rules the assistant's reply is held to, all read from what actually ran in
the turn:

* a question that names business facts (revenue, price, cash, burn, funding, cost
  to serve, targets) is searched before the reply says anything is missing;
* a reply says it searched, found no such record, or that nothing is missing only
  when a lookup that succeeded, and that is about what the sentence says was searched,
  ran in that turn, and "nothing is missing" only when that lookup found nothing;
* a sentence that repeats what the person said earlier in the conversation, and
  that no record or lookup supports, is marked as coming from the conversation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence

# Words a question uses for a business fact, and the names to search the model for.
_FACT_SEARCHES = (
    (re.compile(r"\brevenue\b|\bsales\b|\barr\b|\bmrr\b", re.I), ("revenue",)),
    (re.compile(r"\bprice\b|\bpricing\b|\bprices\b", re.I), ("price", "pricing")),
    (re.compile(r"\bcash\b|\brunway\b", re.I), ("cash", "runway")),
    (re.compile(r"\bburn\b", re.I), ("burn",)),
    (
        re.compile(r"\bfunding\b|\braise\b|\braising\b|\bfundrais\w*\b|\binvest(?:or|ment)s?\b|\bpre-?seed\b|\bseed round\b", re.I),
        ("raise", "funding", "pre-seed"),
    ),
    (
        re.compile(
            r"\bcosts?\s+(?:us\s+|me\s+|we\s+)?to\s+serve\b|\bcost\s+of\s+serving\b|\bcosts?\s+per\b"
            r"|\bunit\s+costs?\b|\bserv(?:e|ing)\s+(?:one|each|a|an|every)\s+customer\b"
            r"|\bcustomers?\s+costs?\b|\bcosts?\s+(?:us\s+|me\s+)?(?:for\s+)?(?:each|every|one)\s+customer\b",
            re.I,
        ),
        ("cost to serve", "cost per"),
    ),
    (re.compile(r"\btargets?\b|\bgoals?\b", re.I), ("target", "goal")),
    (
        re.compile(
            r"\bwhen\s+(?:does|do|will|did)\b|\bwhen\s+(?:is|are)\s+(?:the|our|my|this|that|these|those)\b"
            r"|\bwhen\s+(?:can|should)\s+(?:we|i)\s+(?:launch|ship|release|open|close|start|expect|go)\b"
            r"|\bby\s+when\b|\bwhat\s+dates?\b|\b(?:their|its|our|with|due|launch|target|start|end)\s+dates?\b"
            r"|\bdeadlines?\b",
            re.I,
        ),
        ("date", "deadline"),
    ),
)

LOOKUPS_PER_QUESTION = 8
RESULTS_PER_LOOKUP = 10


def fact_searches(message: Optional[str]) -> List[str]:
    """The names to search the model for when *message* asks about business facts, else none."""
    if not isinstance(message, str):
        return []
    names: List[str] = []
    for pattern, searches in _FACT_SEARCHES:
        if pattern.search(message):
            names.extend(name for name in searches if name not in names)
    return names[:LOOKUPS_PER_QUESTION]


# A sentence that claims a search was made, or that nothing is missing.
_ABSENCE_CLAIM = re.compile(
    r"\bfound\s+no\s+such\b|\bnothing\s+is\s+missing\b|\bsearch(?:ed)?\s+(?:found|returned)\s+nothing\b"
    r"|\b(?:could\s*n[o']t|can\s*n[o']t|can't|couldn't|did\s*n[o']t|didn't)\s+find\b",
    re.I,
)
_NOTHING_MISSING = re.compile(r"\bnothing\s+is\s+missing\b", re.I)
# What a sentence or a question can be about: the business facts above, plus two the platform
# does not search for on its own. A claim about a fact is backed by a lookup about that fact.
_TOPICS = tuple((searches[0], pattern) for pattern, searches in _FACT_SEARCHES) + (
    ("owner", re.compile(r"\bowners?\b|\bowns?\b|\bowned\b|\bownership\b|\baccountable\b", re.I)),
    ("dependency", re.compile(r"\bbreaks?\b|\bfails?\b|\bfailure\b|\bdepend\w*\b|\bimpacts?\b", re.I)),
)


def _topics(text: str) -> frozenset:
    return frozenset(name for name, pattern in _TOPICS if pattern.search(text or ""))
_SEARCH_CLAIM = re.compile(
    r"\bI(?:'ve| have)?\s+(?:searched|looked\s+(?:it\s+)?up|looked\s+(?:for|into|at|through)|checked\s+the\s+(?:model|records))\b",
    re.I,
)
_SENTENCE = re.compile(r"[^.!?\n]+[.!?]*\s*")
_TERM = re.compile(r"[a-z0-9]{4,}")

NO_SEARCH_NOTE = "No search ran, so this answer cannot say nothing is recorded."
PARTIAL_SEARCH_NOTE = "The search did not cover everything you asked, so this answer cannot say nothing is missing."


@dataclass(frozen=True)
class Lookup:
    """One read lookup of the turn: what it was asked, whether it succeeded, whether it found nothing."""

    terms: frozenset
    succeeded: bool
    found_nothing: bool
    for_question: bool = False
    topics: frozenset = frozenset()


def record_lookup(arguments, result, for_question: bool = False) -> Lookup:
    """A *Lookup* from a tool's arguments and the result it returned."""
    values = list(arguments.values()) if isinstance(arguments, dict) else []
    terms = frozenset(t for v in values if isinstance(v, str) for t in _TERM.findall(v.lower()))
    ok = isinstance(result, dict) and result.get("success") is not False and not result.get("error")
    payload = result.get("result") if isinstance(result, dict) and "result" in result else None
    empty = ok and isinstance(payload, (list, dict, str)) and not payload
    asked = " ".join(v.lower() for v in values if isinstance(v, str)) if isinstance(arguments, dict) else ""
    return Lookup(terms, bool(ok), bool(empty), for_question, _topics(asked))


def _backs(lookup: Lookup, sentence: str, question: str, absence: bool) -> bool:
    if not lookup.succeeded or (absence and not lookup.found_nothing):
        return False
    about = _topics(sentence)
    if about:
        return about <= lookup.topics
    if lookup.for_question:
        return True
    words = set(_TERM.findall((sentence + " " + (question or "")).lower()))
    return bool(lookup.terms & words)


def _backs_nothing_missing(lookups: Sequence[Lookup], sentence: str, question: str) -> bool:
    """Nothing is missing only when every fact the question or the sentence is about was looked
    up, by a lookup that succeeded and found nothing; with no named fact, any matching lookup."""
    needed = _topics(sentence) | _topics(question)
    if not needed:
        return any(_backs(look, sentence, question, True) for look in lookups)
    covered = set()
    for look in lookups:
        if look.succeeded and look.found_nothing:
            covered |= look.topics
    return needed <= covered


def remove_unbacked_search_claims(text: str, lookups: Sequence[Lookup], question: str = "") -> str:
    """*text* without the sentences that claim a search, or say nothing is missing, that no
    lookup of the turn backs: one that succeeded and is about what the sentence says was
    searched (for "nothing is missing", one that found nothing)."""
    if not isinstance(text, str) or not (_SEARCH_CLAIM.search(text) or _ABSENCE_CLAIM.search(text)):
        return text
    kept: List[str] = []
    dropped = False
    for sentence in _SENTENCE.findall(text):
        absence = bool(_ABSENCE_CLAIM.search(sentence))
        if _NOTHING_MISSING.search(sentence):
            backed = _backs_nothing_missing(lookups or (), sentence, question)
        else:
            backed = any(_backs(look, sentence, question, absence) for look in lookups or ())
        if (absence or _SEARCH_CLAIM.search(sentence)) and not backed:
            dropped = True
        else:
            kept.append(sentence)
    if not dropped:
        return text
    searched = any(look.succeeded and look.for_question for look in lookups or ())
    return ("".join(kept).strip() + " " + (PARTIAL_SEARCH_NOTE if searched else NO_SEARCH_NOTE)).strip()


_WORD = re.compile(r"[a-z0-9$%][a-z0-9$%,.'-]*", re.I)
_RUN = 5
_ATTRIBUTED = re.compile(r"\byou\s+(?:said|told|mentioned|wrote|noted)\b|\bfrom\s+(?:our|this)\s+conversation\b", re.I)
CONVERSATION_MARK = "From what you told me earlier in this conversation (not a record): "


def _runs(text: str) -> set:
    words = [w.lower().strip(".,") for w in _WORD.findall(text or "")]
    return {" ".join(words[i:i + _RUN]) for i in range(len(words) - _RUN + 1)}


def mark_conversation_facts(text: str, earlier_user_messages: Sequence[str], evidence: Iterable[str]) -> str:
    """*text* with each sentence marked that repeats the person's earlier words and that no
    record, context or lookup of this turn contains."""
    if not isinstance(text, str) or not text.strip():
        return text
    said = set()
    for message in earlier_user_messages or []:
        said |= _runs(str(message))
    if not said:
        return text
    backed = set()
    for chunk in evidence or []:
        backed |= _runs(str(chunk))
    out: List[str] = []
    for sentence in _SENTENCE.findall(text):
        runs = _runs(sentence)
        if runs & said and not runs & backed and not _ATTRIBUTED.search(sentence):
            lead = sentence[: len(sentence) - len(sentence.lstrip())]
            sentence = lead + CONVERSATION_MARK + sentence.lstrip()
        out.append(sentence)
    return "".join(out)
