"""Homepage layout contract: the section shapes the redesign introduced stay in place.

No database is needed beyond what the other homepage tests already use: the page is rendered with the
shared test client and the markup is read back.
"""

from __future__ import annotations

import re

import pytest

EXPECTED_H2 = [
    "Every enterprise runs on knowledge it cannot see in one place.",
    "Ask a question. Get an answer that shows its working.",
    "The seventeen capabilities of Enterprise Intelligence Management.",
    "Give your AI assistants one true source about your company.",
    "How the company brain grows.",
    "See it answered for where you are.",
    "Priced per company, never per application.",
    "A startup is an enterprise too.",
    "Licensed under AGPL-3.0.",
    "How Entelim compares.",
    "Questions.",
    "Connect your tools. Get a cited answer.",
]


@pytest.fixture
def home_html(app, client):
    resp = client.get("/")
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fragment)).strip()


def test_body_carries_the_marketing_theme(home_html):
    body = re.search(r'<body\s+class="([^"]*)"', home_html)
    assert body and "theme-marketing" in body.group(1).split()


@pytest.mark.parametrize("removed", ["bg-muted/30", "home-hero__grid", "blur-3xl", "py-20 sm:py-32"])
def test_removed_markup_is_gone(home_html, removed):
    assert removed not in home_html


def test_section_order_follows_the_design(home_html):
    page = home_html[home_html.index('data-testid="home-hero"'):].split("<footer")[0]  # the page's own sections, not the menus
    h2s = [_text(m) for m in re.findall(r"<h2[^>]*>(.*?)</h2>", page, flags=re.S)]
    assert h2s == EXPECTED_H2


def test_eyebrows_label_the_sections(home_html):
    eyebrows = [_text(m) for m in re.findall(r'<p class="home-eyebrow">(.*?)</p>', home_html, flags=re.S)]
    for label in ("The category", "Cited answers", "Capabilities", "AI assistants", "Roadmap", "Use cases",
                  "Pricing", "Compare", "Questions"):
        assert label in eyebrows


def test_every_details_sits_inside_the_faq_column(home_html):
    faq = re.search(r'<div class="public-faq[^"]*">(.*?)</div>\s*</div>\s*</div>\s*</section>', home_html, flags=re.S)
    assert faq, "FAQ column not found"
    assert home_html.count("<details") == 6
    assert faq.group(1).count("<details") == 6


def test_pricing_table_has_five_headers_and_four_rows(home_html):
    table = re.search(r'<table[^>]*class="[^"]*public-table[^"]*"[^>]*>(.*?)</table>', home_html, flags=re.S)
    assert table, "pricing table not found"
    head, body = table.group(1).split("</thead>")
    assert [_text(h) for h in re.findall(r"<th[^>]*>(.*?)</th>", head, flags=re.S)] == [
        "Plan", "Price", "Billed", "For", "Includes"]
    rows = re.findall(r"<tr>(.*?)</tr>", body, flags=re.S)
    assert len(rows) == 4
    assert [_text(re.findall(r"<td[^>]*>(.*?)</td>", r, flags=re.S)[0]) for r in rows] == [
        "Community", "Startup", "Team", "Enterprise"]
    assert all(len(re.findall(r"<td", r)) == 5 for r in rows)


def test_hero_figure_is_a_real_answer_and_not_the_stale_list(home_html):
    assert "img/home/chat-answer.webp" in home_html
    assert "img/home/chat-answer-mobile.webp" in home_html
    assert "product-portfolio" not in home_html


def test_closing_band_has_the_call_to_action_pair(home_html):
    closing = home_html[home_html.index('class="home-closing"'):]
    closing = closing[: closing.index("</section>")]
    assert closing.count("public-btn--primary") == 1
    assert closing.count("public-btn--outline") == 1
    assert 'href="/contact"' in closing
