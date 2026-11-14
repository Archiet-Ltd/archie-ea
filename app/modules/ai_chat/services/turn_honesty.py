"""What a chat reply may say about its own searching, and where a fact came from.

Three rules the assistant's reply is held to, all read from what actually ran in
the turn:

* a question that names business facts (revenue, price, cash, burn, funding, cost
  to serve, targets) is searched before the reply says anything is missing;
* a reply says it searched, found no such record, or that nothing is missing only
  when a lookup ran in that turn;
* a sentence that repeats what the person said earlier in the conversation, and
  that no record or lookup supports, is marked as coming from the conversation.
"""

from __future__ import annotations

import re
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
    (re.compile(r"\bcost to serve\b|\bcost per\b|\bunit cost\b", re.I), ("cost to serve", "cost per")),
    (re.compile(r"\btargets?\b|\bgoals?\b", re.I), ("target", "goal")),
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
_SEARCH_CLAIM = re.compile(
    r"\bI(?:'ve| have)?\s+(?:searched|looked\s+(?:it\s+)?up|looked\s+through|checked\s+the\s+(?:model|records))\b"
    r"|\bfound\s+no\s+such\b"
    r"|\bnothing\s+is\s+missing\b"
    r"|\bsearch(?:ed)?\s+(?:found|returned)\s+nothing\b",
    re.I,
)
_SENTENCE = re.compile(r"[^.!?\n]+[.!?]*\s*")

NO_SEARCH_NOTE = "No search ran for this reply, so it cannot say that nothing is recorded."


def remove_unbacked_search_claims(text: str, lookups_ran: int) -> str:
    """*text* without the sentences that claim a search or say nothing is missing, when no
    lookup ran in the turn. With a lookup it is returned as it was."""
    if lookups_ran > 0 or not isinstance(text, str) or not _SEARCH_CLAIM.search(text):
        return text
    kept: List[str] = []
    dropped = False
    for sentence in _SENTENCE.findall(text):
        if _SEARCH_CLAIM.search(sentence):
            dropped = True
        else:
            kept.append(sentence)
    if not dropped:
        return text
    return ("".join(kept).strip() + " " + NO_SEARCH_NOTE).strip()


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
