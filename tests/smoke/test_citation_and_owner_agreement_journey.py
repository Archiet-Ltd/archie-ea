"""A citation lands on its fact with its source, and one owner reads the same on every screen.

The person is a startup CTO who has connected GitHub and approved what was found (the recorded
organisation of tests/fixtures/github_acme_org.json; GitHub is never called). They ask the AI chat
what breaks if Payments Service fails, open each citation the answer offers, and compare what the
chat card, the saved answer and the Applications list say about who owns Customer Portal.

Written from the goal ("I follow a citation and see the fact it rests on, with where it came from")
and asserting that outcome: the landing page marks the cited row and shows its source, a date in
words and its confidence; the owner is the same words on every screen; no date is a raw timestamp.
"""

import re

import pytest
from playwright.sync_api import expect

# The fixtures of the Connect-GitHub journey: a server whose GitHub is the recorded organisation,
# two fresh organisations, and the first one's administrator signed in.
from tests.smoke.chat_ask import ask
from tests.smoke.conftest import PAGE_TIMEOUT
from tests.smoke.test_connect_github_to_cited_answer_journey import (  # noqa: F401
    TOKEN,
    admin,
    github_server,
    world,
)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

RAW_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")
WORDS_DATE = re.compile(r"\b(\d{1,2} )?(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* (\d{1,2}, )?\d{4}")


def test_1_connect_and_approve(admin, world):
    base = world["base"]
    admin.goto(base + "/admin/connectors/github", wait_until="domcontentloaded")
    form = admin.get_by_test_id("github-connect-form")
    form.get_by_label("GitHub organisation").fill("acme")
    form.get_by_label("Read-only token").fill(TOKEN)
    form.get_by_role("button", name="Connect and read my repositories").click()
    expect(admin.locator("#github-progress-line")).to_have_text(re.compile(r"22 proposals are ready"), timeout=PAGE_TIMEOUT)
    admin.get_by_role("link", name="Review what was found").click()
    admin.get_by_role("button", name="Approve all (22)").click()
    expect(admin.locator("#main-content").get_by_text("Added 22 items to your model.")).to_be_visible()


def _cards(page, base):
    answer = ask(page, base, "What breaks if Payments Service fails")
    cards = answer.locator("[data-slot='card']").filter(has=page.locator("[data-answer-row]"))
    expect(cards.first).to_be_visible()
    return answer, cards


def test_2_every_citation_lands_on_the_cited_record_with_its_source(admin, world):
    answer, cards = _cards(admin, world["base"])
    links = answer.locator("a[data-relationship-link]")
    hrefs = [links.nth(i).get_attribute("href") for i in range(links.count())]
    assert hrefs, "the answer offers no link to a relationship record, so no citation can be followed"
    for href in hrefs:
        rel_id = re.search(r"#relationship-(\d+)$", href).group(1)
        admin.goto(world["base"] + href, wait_until="domcontentloaded")
        row = admin.locator("#relationship-%s" % rel_id)
        expect(row).to_be_visible()
        assert row.evaluate("el => el.matches(':target')"), "the cited row is not marked"
        cell = "tr[id^='relationship-'] td:first-child"
        # The colour change is a short transition: wait for it to settle before comparing.
        admin.wait_for_function(
            "(sel) => { const m = document.querySelector('tr:target td:first-child'); return m && getComputedStyle(m).backgroundColor !== 'rgba(0, 0, 0, 0)' }",
            arg=cell,
        )
        marked = admin.locator("tr:target td:first-child").evaluate("el => getComputedStyle(el).backgroundColor")
        other = admin.locator("tr[id^='relationship-']:not(:target) td:first-child").first.evaluate("el => getComputedStyle(el).backgroundColor")
        assert marked != other, "the cited row looks like every other row"
        assert row.evaluate("el => { const r = el.getBoundingClientRect(); return r.top >= 0 && r.bottom <= window.innerHeight }"), (
            "the cited row is not scrolled into view"
        )
        text = row.inner_text()
        assert re.search(r"Recorded .*\d{4}|Date not recorded", text), text
        assert "Confidence" in text, text
        assert re.search(r"(Imported from|Drawn by|Origin not recorded|Sample data|\w)", text)
        assert not RAW_TIMESTAMP.search(text), text


def test_3_the_owner_reads_the_same_on_the_chat_card_the_saved_answer_and_the_list(admin, world):
    answer, cards = _cards(admin, world["base"])
    card = cards.filter(has_text="Customer Portal").first
    chat_owner = card.locator("dt:has-text('Owner') + dd").first.inner_text().strip()
    assert chat_owner and not chat_owner.startswith("No owner"), chat_owner

    admin.goto(world["base"] + "/applications/", wait_until="domcontentloaded")
    table = admin.get_by_role("region", name="Applications table")
    row = table.get_by_role("row", name=re.compile(r"^Select Customer Portal ")).first
    assert chat_owner.split(" (")[0] in row.inner_text(), (
        "the chat card says %r, the Applications list row says %r" % (chat_owner, row.inner_text())
    )
    assert "from GitHub code owners" in row.inner_text() or "from GitHub code owners" not in chat_owner


def test_4_the_saved_answer_shows_the_time_in_words(admin, world):
    admin.goto(world["base"] + "/intelligence/answers", wait_until="domcontentloaded")
    link = admin.locator("[data-answer-item] a").first
    link.click()
    admin.wait_for_load_state("domcontentloaded")
    worked_out = admin.locator("[data-computed-at]").inner_text()
    assert not RAW_TIMESTAMP.search(worked_out), "the answer shows a machine timestamp: %r" % worked_out
    assert WORDS_DATE.search(worked_out), "the answer does not show the date in words: %r" % worked_out


def test_5_indirect_connections_say_why_and_offer_the_step(admin, world):
    answer, _ = _cards(admin, world["base"])
    status = answer.locator("[data-derivation-status]")
    expect(status).to_be_visible()
    text = status.inner_text()
    if "Not worked out yet" in text:
        assert re.search(r"work(ed)? .*out|Work them out", text), text
        assert "have not been worked out" in text or "Work them out now" in text, text
