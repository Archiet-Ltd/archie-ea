"""One capability count on every public page.

A visitor reading the home page, /features and /about should meet the same
number of capabilities each time. The home page's delivered list is the one
source; the markdown pages cannot take a value from it, so this test fails
the build whenever a page's count words differ from that list's length.
"""
import re
from pathlib import Path

import pytest

WORDS = {
    1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight",
    9: "nine", 10: "ten", 11: "eleven", 12: "twelve", 13: "thirteen", 14: "fourteen",
    15: "fifteen", 16: "sixteen", 17: "seventeen", 18: "eighteen", 19: "nineteen", 20: "twenty",
    21: "twenty-one", 22: "twenty-two", 23: "twenty-three", 24: "twenty-four", 25: "twenty-five",
}
WORD_TO_NUMBER = {word: number for number, word in WORDS.items()}
TOKEN = "|".join(sorted([re.escape(w) for w in WORD_TO_NUMBER] + [r"\d{1,2}"], key=len, reverse=True))
COUNT_PHRASE = re.compile(
    rf"\b({TOKEN})\b(?=\s+capabilities\b|\s+in\s+full\b)|\bamong\s+the\s+({TOKEN})\b",
    re.IGNORECASE,
)
ROOT = Path(__file__).resolve().parent.parent
HOME_TEMPLATE = ROOT / "app" / "templates" / "main" / "index.html"
PAGES = ["/", "/features", "/about"]


def _to_number(token: str) -> int:
    token = token.lower()
    return int(token) if token.isdigit() else WORD_TO_NUMBER[token]


def _visible_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", " ", html)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def _counts_in(text: str) -> list[int]:
    return [_to_number(a or b) for a, b in COUNT_PHRASE.findall(text)]


def _delivered_length(client) -> int:
    html = client.get("/").get_data(as_text=True)
    block = re.search(r'data-testid="home-capabilities-list"(.*?)data-testid="home-coming-soon"', html, re.S)
    assert block, "the home page has no delivered capabilities list"
    return len(re.findall(r"<li>\s*<a ", block.group(1)))


@pytest.mark.parametrize("path", PAGES)
def test_page_states_the_delivered_count(client, path):
    delivered = _delivered_length(client)
    response = client.get(path)
    assert response.status_code == 200
    counts = _counts_in(_visible_text(response.get_data(as_text=True)))
    assert counts, f"{path} states no capability count"
    assert set(counts) == {delivered}, (
        f"{path} says {sorted(set(counts))} but the delivered list holds {delivered}"
    )


def test_source_files_state_the_delivered_count(client):
    delivered = _delivered_length(client)
    for name in ("features.md", "about.md"):
        text = (ROOT / "content" / "pages" / "site" / name).read_text(encoding="utf-8")
        counts = _counts_in(text)
        assert counts, f"{name} states no capability count"
        assert set(counts) == {delivered}, f"{name} says {sorted(set(counts))}, list holds {delivered}"


def test_count_word_falls_back_to_the_digit(app):
    source = HOME_TEMPLATE.read_text(encoding="utf-8")
    start = source.index("{% set NUMBER_WORDS")
    end = source.index("\n", source.index("{% set capability_count_word"))
    snippet = source[start:end]
    for count in (3, 12, 21, 40):
        out = app.jinja_env.from_string("{% set capability_count = " + str(count) + " %}" + snippet + "[{{ capability_count_word }}]").render()
        assert re.fullmatch(r"\[\S+\]", out.strip()), f"count {count} rendered {out!r}"
