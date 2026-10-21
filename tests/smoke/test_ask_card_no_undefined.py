"""The answer card's source tooltip never spells out a missing value.

Alpine evaluates x-text even while x-show hides the element, so a row without a
confidence figure must give an empty sentence, not "Confidence undefined".
"""
from .test_intelligence_us1_journey import _ask_about, _login, graph, page  # noqa: F401


def test_the_cards_source_tooltips_never_say_undefined_or_null(page, live_server, seeded, graph):
    _login(page, live_server, seeded["emails"]["solution_architect"])
    _ask_about(page, live_server, graph)
    tooltips = page.locator("#ask-cascade [role=tooltip]")
    assert tooltips.count() >= 1
    for i in range(tooltips.count()):
        text = tooltips.nth(i).text_content()
        assert "undefined" not in text and "null" not in text, text
