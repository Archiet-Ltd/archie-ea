"""A startup CTO on a phone opens an application and a saved answer, and reads an impact answer that names each element once.

Goal 1: at 390 and 360 wide, light and dark, three application pages (a long name, a short
name, the most connected) and a saved answer with its permanent address scroll nothing
sideways, and every header action is fully on screen and opens its page.

Goal 2: after "Work them out now" the chat answer to "what breaks if X fails" names an
element reached by two routes (a recorded path and a worked-out one) exactly once, and
the count it states equals the number of distinct names.
"""

import re
import uuid

import pytest
from playwright.sync_api import expect

from .chat_ask import ask, role_of
from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in
from .intelligence_graph import seed_impact_graph
from .test_launch_tech_radar_journey import _new_page

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SIZES = ((390, 844), (360, 780))
THEMES = ("light", "dark")
RECOMPUTE = "/api/v1/intelligence/derivation/recompute"


def _seed_applications(org_id):
    """Three applications with an element each: a long name, a short name, the most connected."""
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:6]
    out = {}
    with app.app_context():
        def element(name):
            row = ArchiMateElement(name=name, type="ApplicationComponent", layer="application",
                                   organization_id=org_id)
            db.session.add(row)
            db.session.commit()
            return row

        def application(name):
            el = element(name)
            comp = ApplicationComponent(name=name, organization_id=org_id, archimate_element_id=el.id)
            db.session.add(comp)
            db.session.commit()
            return comp, el

        long_name = ("Enterprise Customer Calibration Certificate Reconciliation and Archive Service %s" % suffix)
        long_app, long_el = application(long_name)
        short_app, short_el = application("Ledger %s" % suffix)
        hub_app, hub_el = application("Hub Service %s" % suffix)
        for index in range(12):
            peer = element("Peer %02d %s" % (index, suffix))
            db.session.add(ArchiMateRelationship(type="Serving", source_id=hub_el.id, target_id=peer.id,
                                                 organization_id=org_id))
        db.session.add(ArchiMateRelationship(type="Serving", source_id=long_el.id, target_id=short_el.id,
                                             organization_id=org_id))
        db.session.commit()
        out = {"long": long_app.id, "short": short_app.id, "hub": hub_app.id}
        db.session.remove()
    return out


def _overflow(page):
    return page.evaluate(
        """() => ({scroll: document.documentElement.scrollWidth, inner: window.innerWidth,
                   offenders: Array.from(document.querySelectorAll('main *, [class*=inset] *'))
                     .filter(e => e.scrollWidth > e.clientWidth + 1 && getComputedStyle(e).overflowX === 'visible'
                                  && e.getBoundingClientRect().right > window.innerWidth + 1)
                     .slice(0, 5).map(e => e.tagName + '.' + String(e.className).slice(0, 60))})"""
    )


def _assert_fits(page, label):
    info = _overflow(page)
    assert info["scroll"] <= info["inner"], "%s scrolls sideways: %s" % (label, info)
    # Nothing, the header actions included, may run past the right edge.
    far = page.evaluate(
        """() => Array.from(document.querySelectorAll('main a, main button, [data-answer-link]'))
            .filter(e => e.getBoundingClientRect().width > 0 && e.getBoundingClientRect().right > window.innerWidth + 1)
            .filter(e => { for (let n = e.parentElement; n && n !== document.body; n = n.parentElement) {
                const o = getComputedStyle(n).overflowX;
                if ((o === 'auto' || o === 'scroll') && n.scrollWidth > n.clientWidth) return false; } return true; })
            .slice(0, 3).map(e => e.tagName + ' ' + (e.textContent || '').trim().slice(0, 40) + ' right=' +
                 Math.round(e.getBoundingClientRect().right) + ' in ' + (e.closest('[class]') || e).className.slice(0, 80))"""
    )
    assert not far, "%s has controls or links past the right edge (%s): %s" % (label, info["inner"], far)


def _theme(page, theme):
    page.evaluate(
        """(theme) => { document.documentElement.classList.toggle('dark', theme === 'dark');
                        document.documentElement.setAttribute('data-theme', theme); }""", theme)


def test_application_pages_and_a_saved_answer_fit_a_phone(browser, live_server):
    org = create_fresh_org("enterprise_architect")
    apps = _seed_applications(org["org_id"])
    graph = seed_impact_graph(org["org_id"])
    context, page = _new_page(browser)
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        # A saved answer: ask in the chat, then open the answer at its permanent address.
        answer = ask(page, live_server, "What breaks if %s fails" % graph["names"]["service"])
        href = answer.get_by_test_id("answer-open").first.get_attribute("href")
        assert href and href.startswith("/intelligence/answers/"), href

        for width, height in SIZES:
            page.set_viewport_size({"width": width, "height": height})
            for theme in THEMES:
                for key in ("long", "short", "hub"):
                    label = "application %s at %s %s" % (key, width, theme)
                    assert page.goto("%s/applications/%s" % (live_server, apps[key]),
                                     wait_until="domcontentloaded", timeout=PAGE_TIMEOUT).status == 200
                    _theme(page, theme)
                    composer = page.get_by_role("link", name=re.compile("Open in the Composer"))
                    traceability = page.get_by_role("link", name=re.compile("Traceability"))
                    expect(composer.first).to_be_visible(timeout=PAGE_TIMEOUT)
                    _assert_fits(page, label)
                    for link in (composer.first, traceability.first):
                        box = link.bounding_box()
                        assert box and box["x"] >= 0 and box["x"] + box["width"] <= width, (label, box)
                    # Every header action opens its page, with content.
                    for link in (composer, traceability):
                        target = link.first.get_attribute("href")
                        response = page.request.get(live_server + target)
                        assert response.status == 200 and len(response.text()) > 500, (label, target, response.status)

                label = "saved answer at %s %s" % (width, theme)
                assert page.goto(live_server + href, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT).status == 200
                _theme(page, theme)
                link = page.locator("[data-answer-link]")
                expect(link).to_be_visible(timeout=PAGE_TIMEOUT)
                expect(link).to_contain_text(href.rsplit("/", 1)[1])
                _assert_fits(page, label)
    finally:
        context.close()


def test_an_element_reached_by_two_routes_is_named_once_after_working_them_out(browser, live_server):
    org = create_fresh_org("enterprise_architect")
    graph = seed_impact_graph(org["org_id"])
    ops = graph["names"]["ops"]
    context, page = _new_page(browser)
    try:
        assert role_of(org["emails"]["enterprise_architect"]) == "enterprise_architect"
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        answer = ask(page, live_server, "What breaks if %s fails" % graph["names"]["service"])
        button = answer.get_by_role("button", name="Work them out now")
        if button.count():
            with page.expect_response(lambda r: r.url.endswith(RECOMPUTE) and r.request.method == "POST"):
                button.first.click()
            answer = page.get_by_test_id("estate-answer").nth(0)
        sentence = answer.get_by_test_id("answer-sentence")
        expect(sentence).to_be_visible(timeout=PAGE_TIMEOUT)
        text = sentence.inner_text()
        for name in (graph["names"]["gateway"], graph["names"]["portal"], ops):
            assert text.count(name) == 1, "%s named %d times: %s" % (name, text.count(name), text)
        header = answer.get_by_text(re.compile(r"\d+ connections? in your records")).first.inner_text()
        stated = int(re.match(r"\s*(\d+)", header).group(1))
        rows = answer.locator("[data-answer-row]")
        names = {rows.nth(i).get_by_role("link").first.inner_text().strip() for i in range(rows.count())}
        assert stated == len(names) == 3, (stated, names, text)
    finally:
        context.close()
