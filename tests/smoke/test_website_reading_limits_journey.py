"""A founder who presses Save on the company website again and again starts one reading, not many.

The person: a startup CTO signing up. The goal: give the company website and have it read. What they need:
saving the same address several times at once starts exactly one reading and says so; a different address
is read once the first reading is done; the fourth reading of the day is refused with a plain sentence and
the way forward. Run at 1440 and 390 wide. Set READING_LIMIT_SHOTS to save screenshots.
"""
import os
import time

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PAGE_TIMEOUT
from tests.smoke.test_signup_brings_the_company_in_journey import (  # noqa: F401  (these are fixtures)
    new_page,
    people,
    provider,
    server,
    sign_up,
)
from tests.test_website_crawl import ABOUT, CONTACT, HOME, PRODUCTS, ROBOTS, _html, _Site

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = os.environ.get("READING_LIMIT_SHOTS")
WIDTHS = [1440, 390]
LIMIT_SENTENCE = "You can read a website three times a day. Try again tomorrow, or confirm what we found."


def _company_site():
    pages = {
        "/": _html(HOME % {"linkedin": "https://www.linkedin.com/company/acme-robotics", "other": "http://127.0.0.1:9"}),
        "/about": _html(ABOUT), "/products": _html(PRODUCTS), "/contact": _html(CONTACT),
        "/robots.txt": (200, "text/plain", ROBOTS),
    }
    return _Site(pages)


@pytest.fixture(scope="module")
def sites():
    made = [_company_site() for _ in range(4)]
    yield made
    for site in made:
        site.stop()


def shoot(page, name, width):
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        page.screenshot(path=os.path.join(SHOTS, "%s-%d.png" % (name, width)), full_page=True)


def _give(page, url):
    page.get_by_label("Website address").fill(url)
    page.get_by_label("You may read this website").check()
    page.get_by_role("button", name="Save website").click()


def _readings(app, email):
    from app.models.job import Job
    from app.models.user import User

    with app.app_context():
        org_id = User.query.filter_by(email=email).one().organization_id
        return [(job.id, job.status) for job in Job.query.filter(Job.task == "website_profile_crawl").order_by(Job.id)
                if (job.payload or {}).get("organization_id") == org_id]


def _wait_until_read(app, email, count):
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        jobs = _readings(app, email)
        if len(jobs) == count and all(status in ("completed", "failed") for _id, status in jobs):
            return jobs
        time.sleep(1)
    raise AssertionError("the reading never finished: %s" % (_readings(app, email),))


@pytest.mark.parametrize("width", WIDTHS, ids=[str(w) for w in WIDTHS])
def test_saving_the_website_again_and_again_starts_one_reading_and_the_fourth_of_the_day_is_refused(
        browser, server, people, app, sites, width):
    context, page = new_page(browser, width)
    page.set_viewport_size({"width": width, "height": 900 if width > 500 else 844})
    try:
        email, _started = sign_up(page, str(server), people)
        first, second, third, fourth = (site.url for site in sites)

        # Five saves of the same address sent together (a double click, a retry, a script) start one reading.
        page.get_by_label("Website address").fill(first)
        page.get_by_label("You may read this website").check()
        page.evaluate("""([url]) => Promise.all([1, 2, 3, 4, 5].map(() =>
            window.Platform.fetch.post(document.querySelector('[data-url-website]').getAttribute('data-url-website'),
                {url: url, consent: true}, {silent: true}).catch(() => null)))""", [first])
        assert len(_readings(app, email)) == 1, _readings(app, email)
        # A further press of the real button is told why nothing new was started.
        page.get_by_role("button", name="Save website").click()
        expect(page.get_by_test_id("signup-status-website")).to_contain_text("already under way", timeout=PAGE_TIMEOUT)
        assert len(_readings(app, email)) == 1
        shoot(page, "one-reading", width)
        _wait_until_read(app, email, 1)

        # The same address once it has been read: not read again, and the person is told so.
        _give(page, first)
        expect(page.get_by_test_id("signup-status-website")).to_contain_text("We read this address within the last day", timeout=PAGE_TIMEOUT)
        assert len(_readings(app, email)) == 1
        shoot(page, "read-today", width)

        # A different address is a second reading, then a third.
        _give(page, second)
        expect(page.get_by_test_id("signup-status-website")).not_to_contain_text("within the last day", timeout=PAGE_TIMEOUT)
        _wait_until_read(app, email, 2)
        _give(page, third)
        _wait_until_read(app, email, 3)

        # The fourth reading of the day is refused with the sentence and starts nothing.
        _give(page, fourth)
        line = page.locator('[data-say="website"]')
        expect(line).to_have_text(LIMIT_SENTENCE, timeout=PAGE_TIMEOUT)
        assert len(_readings(app, email)) == 3
        shoot(page, "limit", width)

        # What was read is still there to confirm: the way forward the sentence offers lands on facts.
        page.get_by_test_id("signup-continue").click()
        page.wait_for_url("**/account/welcome?step=3")
        expect(page.locator("[data-fact]").first).to_be_visible(timeout=PAGE_TIMEOUT)
    finally:
        context.close()
