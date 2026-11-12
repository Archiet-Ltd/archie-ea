"""A question about one element is sent to the part that answers it.

"Who owns X" asks for owners, "what does X depend on" and "what does X run on"
ask what X depends on, and only "what breaks" (or "what depends on X") asks what
breaks. Pure functions: no app or database needed.
"""
import pytest

from app.modules.ai_chat.services.lens_answer import classify_lens_question
from app.modules.intelligence.services.answer_envelope import (
    KIND_IMPACT,
    PART_BREAKS,
    PART_DEPENDS_ON,
    PART_OWNER,
    PART_RUNS_ON,
    classify_estate_parts,
    classify_estate_question,
)

THREE = "What does Ifiok run on, which suppliers does it depend on, and who owns it?"


@pytest.mark.parametrize(
    "message",
    ["Who owns Ifiok?", "Ifiok's owner?", "Which team is Ifiok owned by?", "Who's responsible for Ifiok?"],
)
def test_ownership_questions_go_to_the_accountability_lens(message):
    assert classify_lens_question(message) == "accountability"
    assert classify_estate_parts(message) == [PART_OWNER]


@pytest.mark.parametrize(
    "message,part",
    [
        ("What does Ifiok depend on?", PART_DEPENDS_ON),
        ("What does Ifiok rely on?", PART_DEPENDS_ON),
        ("Which suppliers does Ifiok depend on?", PART_DEPENDS_ON),
        ("What does Ifiok run on?", PART_RUNS_ON),
        ("Where is Ifiok hosted?", PART_RUNS_ON),
    ],
)
def test_dependency_questions_ask_the_impact_walk_upstream(message, part):
    assert classify_estate_parts(message) == [part]
    assert classify_estate_question(message) == KIND_IMPACT
    assert classify_lens_question(message) is None


@pytest.mark.parametrize(
    "message",
    [
        "What breaks if Ifiok fails?",
        "What if we retire Ifiok?",
        "What depends on Ifiok?",
        "What relies on Ifiok?",
    ],
)
def test_what_breaks_questions_keep_the_downstream_card(message):
    assert classify_estate_parts(message) == [PART_BREAKS]
    assert classify_estate_question(message) == KIND_IMPACT
    assert classify_lens_question(message) is None


def test_the_three_part_question_is_recognised_as_asking_all_three_in_order():
    assert classify_estate_parts(THREE) == [PART_RUNS_ON, PART_DEPENDS_ON, PART_OWNER]


def test_a_question_asking_what_breaks_and_who_owns_gets_both_parts():
    assert classify_estate_parts("What breaks if Ifiok fails, and who owns it?") == [PART_BREAKS, PART_OWNER]


@pytest.mark.parametrize("message", ["", "Hello, how are you?", None])
def test_other_messages_ask_for_no_part(message):
    assert classify_estate_parts(message) == []
