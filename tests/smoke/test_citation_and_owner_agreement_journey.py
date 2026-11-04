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
from tests.smoke.seed_estate import seed_calibration_ledger
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


VIEWPORTS = ({"width": 1440, "height": 900}, {"width": 390, "height": 844})
NAMED_SOURCE = re.compile(r"(Imported from \S|Synced by|Drawn by \S|Entered by|GitHub \S|Sample data)")


@pytest.fixture(scope="module")
def big_org(admin, world, app):
    """The approved model plus an application with hundreds of relationships around it."""
    return seed_calibration_ledger(app, world["one"]["org_id"])


def _assert_lands_on_the_cited_row(page, base, href, viewport):
    page.set_viewport_size(viewport)
    rel_id = re.search(r"relationship-(\d+)$", href).group(1)
    page.goto(base + href, wait_until="domcontentloaded")
    row = page.locator("#relationship-%s" % rel_id)
    expect(row).to_be_visible()
    assert row.evaluate("el => el.matches(':target')"), "the cited row %s is not marked (%s)" % (rel_id, href)
    try:
        page.wait_for_function(
            "() => { const m = document.querySelector('tr:target td:first-child'); return m && getComputedStyle(m).backgroundColor !== 'rgba(0, 0, 0, 0)' }",
            timeout=15000,
        )
    except Exception:
        raise AssertionError(
            "the cited row is not tinted at %s for %s; page rows: %d; row html: %s"
            % (viewport["width"], href, page.locator("tr[id^='relationship-']").count(), row.evaluate("el => el.outerHTML")[:400])
        )
    marked = page.locator("tr:target td:first-child").evaluate("el => getComputedStyle(el).backgroundColor")
    other = page.locator("tr[id^='relationship-']:not(:target) td:first-child").first.evaluate("el => getComputedStyle(el).backgroundColor")
    assert marked != other, "the cited row looks like every other row"
    assert row.evaluate("el => { const r = el.getBoundingClientRect(); return r.top >= 0 && r.bottom <= window.innerHeight }"), (
        "the cited row is not scrolled into view"
    )
    overflow = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
    assert overflow <= 0, "at %d wide the page is %dpx wider than the screen" % (viewport["width"], overflow)
    # What it connects to is on screen, not under a pinned column or off the edge.
    target_cell = row.locator("td").nth(2)
    box = target_cell.bounding_box()
    assert box and box["x"] >= 0 and box["x"] + min(box["width"], 40) <= viewport["width"], "Target is off screen: %s" % box
    cover = target_cell.evaluate(
        "el => { const r = el.getBoundingClientRect(); const e = document.elementFromPoint(r.left + 10, r.top + r.height / 2); "
        "return el.contains(e) ? null : (e ? e.tagName + '.' + e.className + ' ' + (e.innerText || '').slice(0, 60) : 'nothing') }"
    )
    assert cover is None, "at %d wide something covers the Target cell: %s" % (viewport["width"], cover)
    text = row.inner_text()
    assert NAMED_SOURCE.search(text), "the source is not a named origin: %r" % text
    assert "Origin not recorded" not in text and "Date not recorded" not in text, text
    assert WORDS_DATE.search(text), "the date is not in words: %r" % text
    assert re.search(r"Confidence: \w+", text), text
    assert not RAW_TIMESTAMP.search(text), text
    assert page.locator("th", has_text="Source").count() <= 1, "two columns are headed Source"


def test_2_every_citation_lands_on_the_cited_record_with_its_source(admin, world, big_org):
    base = world["base"]
    for question in ("What breaks if Customer Portal fails", "What breaks if Calibration Ledger fails"):
        answer = ask(admin, base, question)
        links = answer.locator("a[data-relationship-link]")
        hrefs = [links.nth(i).get_attribute("href") for i in range(links.count())]
        assert hrefs, "the answer to %r offers no link to a relationship record" % question
        if "Calibration" in question:
            assert len(hrefs) >= 6, hrefs
        try:
            for href in hrefs:
                for viewport in VIEWPORTS:
                    _assert_lands_on_the_cited_row(admin, base, href, viewport)
        finally:
            admin.set_viewport_size(VIEWPORTS[0])


def test_3_the_owner_reads_the_same_on_the_chat_card_the_saved_answer_the_application_and_the_list(admin, world):
    base = world["base"]
    answer, cards = _cards(admin, base)
    card = cards.filter(has_text="Customer Portal").first
    chat_owner = card.locator("dt:has-text('Owner') + dd").first.inner_text().strip()
    assert chat_owner and not chat_owner.startswith("No owner"), chat_owner
    owner_name = chat_owner.split(" (")[0]

    admin.goto(base + "/applications/", wait_until="domcontentloaded")
    table = admin.get_by_role("region", name="Applications table")
    row = table.get_by_role("row", name=re.compile(r"^Select Customer Portal ")).first
    row_text = row.inner_text()
    assert owner_name in row_text, "the chat card says %r, the Applications list row says %r" % (chat_owner, row_text)
    assert "from GitHub code owners" in row_text, row_text
    # The evidence footer names where a derived owner came from; it is not "entered by hand".
    footer = row.locator("[data-result-footer], [data-evidence-footer]")
    footer_text = row_text + " " + (footer.first.inner_text() if footer.count() else "")
    assert "GitHub code owners" in footer_text and "entered by hand" not in footer_text.lower(), footer_text

    # The headline counts applications with neither a person nor a code owner: exactly the rows saying so.
    page_text = admin.locator("#main-content").inner_text()
    headline = re.search(r"(\d+) of (\d+) have no owner", page_text)
    rows = table.get_by_role("row").all()
    unowned_rows = [
        r for r in table.get_by_role("row", name=re.compile(r"^Select ")).all() if re.search(r"No owner", r.inner_text())
    ]
    assert (int(headline.group(1)) if headline else 0) == len(unowned_rows), (
        "the headline says %s, the list shows %d rows with no owner: %s" % (headline and headline.group(0), len(unowned_rows), [r.inner_text()[:90] for r in unowned_rows])
    )

    # The application page names the same owner.
    href = row.get_by_role("link", name="Customer Portal").first.get_attribute("href")
    admin.goto(base + href, wait_until="domcontentloaded")
    app_text = admin.locator("#main-content").inner_text()
    assert owner_name in app_text, "the application page does not name %r: %r" % (owner_name, app_text[:400])
    assert "No owner recorded" not in app_text and "Missing: Owner" not in app_text, app_text[:600]

    # The saved answer names the same owner.
    admin.goto(base + "/intelligence/answers", wait_until="domcontentloaded")
    admin.locator("[data-answer-item] a").first.click()
    admin.wait_for_load_state("domcontentloaded")
    saved = admin.locator("#main-content").inner_text()
    if "Customer Portal" in saved and "Owner" in saved:
        assert "No owner recorded" not in saved.split("Customer Portal", 1)[1][:600] or owner_name in saved


def test_4_the_saved_answer_shows_the_time_in_words(admin, world):
    admin.goto(world["base"] + "/intelligence/answers", wait_until="domcontentloaded")
    link = admin.locator("[data-answer-item] a").first
    link.click()
    admin.wait_for_load_state("domcontentloaded")
    worked_out = admin.locator("[data-computed-at]").inner_text()
    assert not RAW_TIMESTAMP.search(worked_out), "the answer shows a machine timestamp: %r" % worked_out
    assert WORDS_DATE.search(worked_out), "the answer does not show the date in words: %r" % worked_out


def test_5_indirect_connections_say_why_and_offer_the_step(admin, world, big_org):
    base = world["base"]
    answer = ask(admin, base, "What breaks if Calibration Ledger fails")
    status = answer.locator("[data-derivation-status]")
    expect(status).to_be_visible()
    text = status.inner_text()
    assert "Not worked out yet" in text, "a model never worked out should say so: %r" % text
    assert re.search(r"Work them out now", text) or answer.get_by_role("button", name=re.compile("Work them out now")).count(), text
    before = answer.inner_text()
    assert re.search(r"\d+ direct", before), before
    answer.get_by_role("button", name=re.compile("Work them out now")).first.click()
    admin.wait_for_function(
        "() => !document.querySelector('[data-derivation-status]') || !document.querySelector('[data-derivation-status]').innerText.includes('Not worked out yet')",
        timeout=PAGE_TIMEOUT,
    )
    after = admin.get_by_test_id("estate-answer").last.inner_text()
    assert "Not worked out yet" not in after, after
    assert re.search(r"\d+ direct", after), after
    assert "Ledger Reports" in after and "Ledger Dashboard" in after, "the indirect connection is not named: %r" % after
