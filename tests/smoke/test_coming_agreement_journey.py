"""Journey: the hub, the features page's Coming soon list and the sitemap say the same thing about what is coming.

Goal: "What is coming, and can I ask for it?" A visitor reads the Coming soon list on the features page, opens the
early-access hub and opens every row of each. Whatever has a page to open is on both, under one name; whatever
is delivered is not also coming; the sitemap lists only pages that answer with their own content.

Run with the release switch off and at RELEASE_WAVE=1.
"""

from __future__ import annotations

import os
import pathlib
import re
import tempfile
from urllib.parse import urlsplit

import pytest

from .conftest import PAGE_TIMEOUT, boot_live_server

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = pathlib.Path(os.environ.get("COMING_AGREEMENT_WALK_DIR") or tempfile.gettempdir()) / "coming-agreement-walk"
SIZES = [(1440, 900, "light"), (390, 844, "dark")]


@pytest.fixture(scope="module")
def wave_server(request, app):
    return boot_live_server(request, None, app, extra_env={"RELEASE_WAVE": "1"})


def _rows(page, list_selector):
    """[(name, sentence, href or None)] for each row of a list, as a person reads it."""
    found = page.eval_on_selector_all(
        list_selector + " li",
        """els => els.map(e => {
            const a = e.querySelector('a');
            const text = (e.innerText || '').replace(/\\s+/g, ' ').trim();
            const name = a ? a.innerText.trim() : text.split(':')[0].trim();
            return [name, text.slice(name.length).replace(/^:\\s*/, ''), a ? a.getAttribute('href') : null];
        })""")
    return [(name, sentence, href) for name, sentence, href in found]


def _heading(page):
    found = page.eval_on_selector_all(
        "h1", "els => els.filter(e => !e.closest('header, footer, nav')).map(e => (e.innerText || '').trim())")
    return found[0] if found else ""


def _open(page, base, href):
    response = page.goto(base + href, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    return response.status if response is not None else 0


def _walk(browser, base, switch):
    paths = {}
    for width, height, scheme in SIZES:
        context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme)
        page = context.new_page()
        try:
            # the features page: what is delivered and what is coming
            assert _open(page, base, "/features") == 200
            page.wait_for_selector("#coming-soon", timeout=PAGE_TIMEOUT)
            coming = _rows(page, "#coming-soon + p + ul")
            assert coming, "the features page lists nothing as coming"
            available = _rows(page, "#available-now + p + ul") if page.locator("#available-now").count() else []
            delivered = page.eval_on_selector_all(
                "main p", "els => els.filter(e => e.innerText.startsWith('Delivered today by')).flatMap(e => "
                "Array.from(e.querySelectorAll('a')).map(a => a.getAttribute('href')))")
            SHOTS.mkdir(parents=True, exist_ok=True)
            page.locator("#coming-soon").scroll_into_view_if_needed()
            page.screenshot(path=str(SHOTS / ("features-coming-switch-%s-%s-%d.png" % (switch, scheme, width))), full_page=True)

            # the hub: what a visitor can open and ask for
            assert _open(page, base, "/early-access") == 200
            hub = _rows(page, '[data-testid="early-access-hub"] ul')
            page.screenshot(path=str(SHOTS / ("hub-switch-%s-%s-%d.png" % (switch, scheme, width))), full_page=True)
            sitemap = page.request.get(base + "/sitemap.xml").text()
            sitemap_paths = [urlsplit(u).path or "/" for u in re.findall(r"<loc>([^<]+)</loc>", sitemap)]

            names = [n.lower() for n, _s, _h in coming] + [n.lower() for n, _s, _h in available]
            assert len(names) == len(set(names)), "a name appears twice on the features page: %s" % sorted(
                n for n in names if names.count(n) > 1)
            hub_names = [n.lower() for n, _s, _h in hub]
            assert len(hub_names) == len(set(hub_names)), "a name appears twice on the hub"
            for name, sentence, href in coming + hub:
                assert sentence and sentence.lower() != name.lower(), "%r is its own answer" % name
                assert not sentence.lower().startswith(name.lower() + ":"), "%r repeats itself" % name
                assert sentence.lower().count("coming soon:") == 0, "%r says Coming soon twice" % name

            # delivered or coming, never both
            coming_hrefs = {h for _n, _s, h in coming if h}
            assert not coming_hrefs & set(delivered), "delivered and coming: %s" % sorted(coming_hrefs & set(delivered))
            assert not {n.lower() for n, _s, _h in available} & {n.lower() for n, _s, _h in coming}

            # open every row of the features list: the kind of page decides whether the hub lists it
            kinds = {}
            for name, sentence, href in coming:
                if href is None:
                    kinds[name] = "features-only"
                    continue
                assert _open(page, base, href) == 200, href
                assert _heading(page), "%s opens a page with no heading" % href
                path = urlsplit(page.url).path
                early = page.locator('[data-testid="early-access-page"]').count() > 0
                module = page.locator('[data-testid="waiting-list-module"]').count() > 0
                if early:
                    kinds[name] = "early-access"
                elif module and path in sitemap_paths:
                    kinds[name] = "waiting-list module"
                else:
                    kinds[name] = "features-only"
                assert early or module, "%s opens neither a waiting list nor a waiting-list page" % href
            listed = {n.lower() for n, kind in kinds.items() if kind != "features-only"}
            assert listed == set(hub_names), "hub and features list disagree: only on the hub %s; only on the features page %s" % (
                sorted(set(hub_names) - listed), sorted(listed - set(hub_names)))

            # every hub row opens a page headed with its name and saying its sentence
            for name, sentence, href in hub:
                assert _open(page, base, href) == 200, href
                assert _heading(page).lower() == name.lower(), (href, name, _heading(page))
                text = re.sub(r"\s+", " ", page.inner_text("main"))
                assert sentence in text, (href, sentence)
            if hub:
                assert page.request.get(base + "/early-access").text().count("Nothing is waiting") == 0
            paths[width] = (sorted(kinds.items()), sitemap_paths)

            # the sitemap: every address answers with its own content; a closed address answered in place is out
            for path in sitemap_paths:
                reply = page.request.get(base + path)
                assert reply.status == 200, "sitemap lists %s -> %s" % (path, reply.status)
                body = reply.text()
                assert re.search(r"<h1[^>]*>\s*\S", body), "sitemap lists %s, which has no heading" % path
                if 'data-testid="early-access-page"' in body:
                    assert path.startswith("/early-access/"), "sitemap lists %s, a closed address answered in place" % path
            assert ("/early-access" in sitemap_paths) is bool(hub), "the sitemap and the hub disagree about the hub"
            for name, kind in kinds.items():
                if kind == "waiting-list module":
                    assert any(href in sitemap_paths for n, _s, href in coming if n == name)
        finally:
            context.close()
    return paths


def test_with_the_switch_off_the_hub_the_features_list_and_the_sitemap_agree(browser, live_server):
    _walk(browser, live_server, "off")


def test_at_the_first_release_the_hub_the_features_list_and_the_sitemap_agree(browser, wave_server):
    paths = _walk(browser, wave_server, "on")
    kinds, _sitemap = paths[1440]
    assert sum(1 for _n, kind in kinds if kind == "early-access") >= 10, kinds
