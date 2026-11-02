"""A visitor picks their role on /roles, and a signed-in person checks the record on the history pages.

The visitor sees a card for every role page, Startup founders first and Scale-up CTOs second; the
page's structured data lists the same pages in the same order, and every card opens a page with
content. A signed-in architect presses each button of the two history pages and reads the result.

Played at 1440x900 and 390x844. Screenshots go under the seat's walk directory.
"""
import json
import re
from pathlib import Path

import pytest

from tests.smoke.conftest import PAGE_TIMEOUT
from tests.smoke.fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK = Path("/home/ubuntu/verify/seats/linered03c/walk")
VIEWPORTS = (("1440", 1440, 900), ("390", 390, 844))


def _card_hrefs(page):
    return page.eval_on_selector_all("[data-role-card]", "els => els.map(e => e.getAttribute('href'))")


def _ld_urls(page):
    raw = page.eval_on_selector_all('script[type="application/ld+json"]', "els => els.map(e => e.textContent)")
    assert len(raw) == 1
    graph = json.loads(raw[0])["@graph"]
    collection = next(n for n in graph if n["@type"] == "CollectionPage")
    return [i["url"].removeprefix("https://entelim.org") for i in collection["mainEntity"]["itemListElement"]]


@pytest.mark.parametrize("name,width,height", VIEWPORTS)
def test_visitor_sees_a_card_for_every_role_page_and_each_opens(browser, live_server, name, width, height):
    WALK.mkdir(parents=True, exist_ok=True)
    context = browser.new_context(viewport={"width": width, "height": height})
    page = context.new_page()
    try:
        page.goto(live_server + "/roles", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_selector("[data-role-card]", timeout=PAGE_TIMEOUT)
        cards = _card_hrefs(page)
        assert cards[:2] == ["/roles/startup-founder", "/roles/scale-up-cto-and-vp-engineering"]
        assert _ld_urls(page) == cards
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        first_titles = page.eval_on_selector_all("[data-role-card]", "els => els.slice(0, 2).map(e => e.innerText)")
        assert first_titles[0].startswith("Startup founders") and first_titles[1].startswith("Scale-up CTOs")
        page.screenshot(path=str(WALK / f"roles-{name}.png"), full_page=True)

        for href in cards:
            page.goto(live_server + "/roles", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            page.locator(f'[data-role-card][href="{href}"]').click()
            page.wait_for_url(lambda url, h=href: url.endswith(h), timeout=PAGE_TIMEOUT)
            assert page.locator("h1").first.inner_text().strip(), f"{href} has no heading"
            assert len(page.locator("main, body").first.inner_text().split()) > 300, f"{href} has little content"
    finally:
        context.close()


@pytest.mark.parametrize("name,width,height", VIEWPORTS)
def test_architect_presses_every_history_button_and_reads_the_result(browser, live_server, name, width, height):
    WALK.mkdir(parents=True, exist_ok=True)
    email = create_fresh_org("solution_architect", org_admin=True)["emails"]["solution_architect"]
    context = browser.new_context(viewport={"width": width, "height": height})
    page = context.new_page()
    try:
        sign_in(page, live_server, email)
        for path, button, heading in (
            ("/intelligence/history/changes", "Show changes", "Evidence"),
            ("/intelligence/history/changes", "Check the record is intact", "Evidence"),
            ("/intelligence/history/as-of", "Show the model", "Model as of"),
        ):
            page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            page.locator(f'form[action="{path}"]').get_by_role("button", name=button).click()
            page.wait_for_load_state("domcontentloaded")
            assert path in page.url
            assert any(h.startswith(heading) for h in page.locator("h1").all_inner_texts())
            body = page.locator("main").first.inner_text()
            assert "Something went wrong" not in body
            if button.startswith("Check"):
                assert re.search(r"Intact: \d+ entries checked|No sealed entries to check yet", body), body[:300]
            else:
                assert len(body.split()) > 15
            page.screenshot(path=str(WALK / f"history-{button.split()[0].lower()}-{path.rsplit('/', 1)[1]}-{name}.png"))
    finally:
        context.close()
