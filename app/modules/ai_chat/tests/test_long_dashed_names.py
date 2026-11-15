"""A question naming an element whose name is long and carries a dash and brackets is matched
to that element's stored name."""

from app.modules.ai_chat.services.ai_hallucination_detector import candidate_phrases
from app.utils.duplicate_guard import normalize_name

NAME = "MarketingIQ — backend (Go/Fiber, 441 routes) and scheduler workers"


def test_a_long_name_with_a_dash_and_brackets_is_a_phrase_of_the_question():
    phrases = candidate_phrases("What is the rationalization status of %s" % NAME)
    assert normalize_name(NAME) in phrases
