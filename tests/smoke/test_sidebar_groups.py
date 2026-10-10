"""The sidebar groups its links under the same question-shaped headings for every role, in a real
browser: the Ask link first, the headings in the approved order, every group open, and a heading
collapses its group and opens it again.
"""

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

APPROVED_ORDER = [
    "Getting started", "What we do", "What supports it", "Goals and changes",
    "What if we change it", "Build and model", "Admin",
]


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    try:
        page.click("#submit", no_wait_after=True)
    except TypeError:
        page.locator("#submit").click()
    try:
        page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)
    except Exception:
        pass
    assert "/account/login" not in page.url, "could not sign in as %s" % email


@pytest.mark.parametrize("persona", ["solution_architect", "cto"])
def test_the_sidebar_shows_the_approved_headings_and_a_collapsed_group_opens(page, live_server, seeded, persona):
    _login(page, live_server, seeded["emails"][persona])
    page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    nav = page.locator("#sidebar-nav")
    expect(nav).to_be_visible()

    # Ask is the first link, before any titled group.
    first_link = nav.locator("[data-sidebar-group] a").first
    expect(first_link).to_have_attribute("href", "/intelligence/ask")

    # Only approved headings, in the approved order.
    # The heading text is upper-cased by CSS, and the browser reports it that way.
    lowered = {title.lower(): title for title in APPROVED_ORDER}
    headings = [
        lowered.get(t.strip().lower(), t.strip())
        for t in nav.locator("[data-sidebar-group] [role=heading]").all_inner_texts()
    ]
    assert headings, "no group headings rendered"
    assert all(h in APPROVED_ORDER for h in headings), headings
    assert [APPROVED_ORDER.index(h) for h in headings] == sorted(APPROVED_ORDER.index(h) for h in headings)

    # Every group starts open (the headings organise the list, they do not hide it), and a
    # heading collapses its group by hand and opens it again.
    buttons = nav.locator("[data-sidebar-group] button[aria-expanded]")
    for button in buttons.all():
        expect(button).to_have_attribute("aria-expanded", "true")
    key = buttons.first.locator("xpath=ancestor::div[@data-sidebar-group][1]").get_attribute("data-sidebar-group")
    # Pin the group by its key: "the first button" is a lazy locator and would re-resolve.
    group = nav.locator("[data-sidebar-group='%s']" % key)
    heading = group.locator("button")
    link = group.locator("a").first
    expect(link).to_be_visible()
    heading.click()
    expect(heading).to_have_attribute("aria-expanded", "false")
    expect(link).to_be_hidden()
    heading.click()
    expect(heading).to_have_attribute("aria-expanded", "true")
    expect(link).to_be_visible()
