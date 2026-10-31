"""classify_estate_question recognises every phrasing of the impact question.

Pure function, no app or database needed: ``classify_estate_question`` only
pattern-matches the message text (app/modules/intelligence/services/
answer_envelope.py). The function matched "if X is retired" and "if X is decommissioned" but not "what if we retire X",
"what if we decommission X", "what if we remove X", "what if X is removed"
or "what if we replace X" -- the phrasing a person actually typed in the
chat box, which is what sent that question to the model uncited instead of
to the impact tool.
"""
import pytest

from app.modules.intelligence.services.answer_envelope import (
    KIND_IMPACT,
    classify_estate_question,
)

IMPACT_PHRASINGS = [
    "What if we retire Payments service?",
    "What if we decommission Payments service?",
    "What if Payments service is retired?",
    "What happens if we remove Payments service?",
    "What happens if Payments service is removed?",
    "What if we replace Payments service?",
    # existing phrasings must keep matching
    "What breaks if Payments service fails?",
     "What if Payments service is decommissioned?",
     "What if Payments service is replaced?",
     "What if Payments service was retired?",
     "What if Payments service was decommissioned?",
     "What if Payments service was removed?",
     "What if Payments service was replaced?",
     "If Payments service fails, what happens?",
 ]


@pytest.mark.parametrize("message", IMPACT_PHRASINGS)
def test_impact_phrasings_classify_as_impact(message):
    assert classify_estate_question(message) == KIND_IMPACT, message


@pytest.mark.parametrize(
    "message",
    [
        "Hello, how are you?",
        "Tell me about our capabilities.",
        "",
    ],
)
def test_non_estate_messages_classify_as_none(message):
    assert classify_estate_question(message) is None
