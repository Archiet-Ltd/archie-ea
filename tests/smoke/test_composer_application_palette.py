"""An Application and a component dropped in the Composer: the composition is stored and survives a reload.

ADR-0014 Decision 8, T-app-05. Drops an Application and a component from the quick-add, nests the component
inside the Application as a composition, and reloads. The composition must come back from the server, which
validated it against the relationship matrix. Set COMPOSER_SHOTS_DIR to also write the 1536-wide screenshots.
"""

import os
import re
import uuid

import pytest
from playwright.sync_api import expect

from app.models.archimate_element_types import base_type
from tests.smoke.conftest import PASSWORD

PAGE_TIMEOUT = 30000
APP = "Application"
COMP = base_type(APP)
DATA = "Alpine.$data(document.querySelector('[x-data=\"composerApp()\"]'))"


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


def _quick_add(page, name, element_type):
    page.keyboard.press("Control+k")
    input_box = page.locator("#quick-add-input")
    expect(input_box).to_be_visible(timeout=PAGE_TIMEOUT)
    input_box.fill(name)
    page.locator("[aria-label='Quick Add New Type']").select_option(element_type, timeout=PAGE_TIMEOUT)
    create_btn = page.get_by_role("button", name=re.compile("^Create$"))
    expect(create_btn).to_be_visible(timeout=PAGE_TIMEOUT)
    create_btn.click()
    page.wait_for_function(
        "n => %s.graph.getElements().some(c => c.get('elName') === n)" % DATA, arg=name, timeout=PAGE_TIMEOUT
    )


def _shot(page, label):
    out = os.environ.get("COMPOSER_SHOTS_DIR")
    if out:
        os.makedirs(out, exist_ok=True)
        page.screenshot(path=os.path.join(out, label + ".png"))


@pytest.mark.smoke
def test_application_with_a_component_is_stored_and_survives_reload(browser, live_server, seeded):
    email = seeded["emails"]["enterprise_architect"]
    suffix = uuid.uuid4().hex[:8]
    app_name, comp_name = "Palette App " + suffix, "Palette Part " + suffix

    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1536, "height": 960})
    page = context.new_page()
    _login(page, live_server, email)
    page.goto(live_server + "/archimate/composer", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.locator("nav[aria-label='Composer toolbar']")).to_be_visible(timeout=PAGE_TIMEOUT)

    # both palette entries are offered with their plain labels
    palette = page.evaluate("Object.values(ComposerRenderer.PALETTE).flat().map(i => [i.type, i.label])")
    assert [APP, "Application"] in palette and [COMP, "App Component"] in palette
    titles = page.evaluate("Object.values(ComposerRenderer.PALETTE).flat().map(i => [i.type, i.title])")
    assert [COMP, "Application component"] in titles

    _quick_add(page, app_name, APP)
    _quick_add(page, comp_name, COMP)

    drawn = page.evaluate(
        "n => { const c = %s.graph.getElements().find(e => e.get('elName') === n);"
        " return c.attr('typeLabel/text'); }" % DATA, app_name,
    )
    assert drawn == "\u00abApplication\u00bb"

    # the Application opens as a container, the component is nested in it as a composition
    page.evaluate(
        "n => { const d = %s; const cell = d.graph.getElements().find(e => e.get('elName') === n);"
        " d.ctxMenuCell = cell; d.toggleRenderingMode(); }" % DATA, app_name,
    )
    kinds = page.evaluate(
        "n => { const e = %s.graph.getElements().find(c => c.get('elName') === n);"
        " return [e.get('renderingMode'), e.get('elType')]; }" % DATA, app_name,
    )
    assert kinds == ["white_box", APP]
    with page.expect_response(lambda r: r.url.endswith("/archimate/api/relationships") and r.request.method == "POST") as posted:
        page.evaluate(
            "([a, c]) => { const d = %s; const els = d.graph.getElements();"
            " d.nestingPromptParent = els.find(e => e.get('elName') === a);"
            " d.nestingPromptChild = els.find(e => e.get('elName') === c);"
            " d.confirmNesting('composition'); }" % DATA, [app_name, comp_name],
        )
    assert posted.value.status in (200, 201), posted.value.text()
    _shot(page, "composer-application-container-light")

    page.reload(wait_until="domcontentloaded")
    expect(page.locator("nav[aria-label='Composer toolbar']")).to_be_visible(timeout=PAGE_TIMEOUT)
    stored = page.evaluate(
        """async ([a, c]) => {
            const find = async (n) => (await (await fetch('/archimate/api/elements/search?limit=5&q=' + encodeURIComponent(n))).json()).data[0];
            const [pa, pc] = [await find(a), await find(c)];
            const rels = await (await fetch('/archimate/api/relationships?source_element_id=' + pa.id
                + '&target_element_id=' + pc.id)).json();
            return {parent: pa.type, child: pc.type, rels: JSON.stringify(rels)};
        }""",
        [app_name, comp_name],
    )
    assert stored["parent"] == APP and stored["child"] == COMP
    assert "omposition" in stored["rels"], stored["rels"]
    context.close()


@pytest.mark.smoke
@pytest.mark.skipif(not os.environ.get("COMPOSER_SHOTS_DIR"), reason="screenshots are written only when asked for")
def test_screenshots_of_the_application_in_the_composer(browser, live_server, seeded):
    """Light and dark, 1536 wide: the palette and an Application holding a component."""
    email = seeded["emails"]["enterprise_architect"]
    suffix = uuid.uuid4().hex[:6]
    prefix = os.environ.get("COMPOSER_SHOTS_PREFIX", "after")
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1536, "height": 960})
    page = context.new_page()
    _login(page, live_server, email)
    page.goto(live_server + "/archimate/composer", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.locator("nav[aria-label='Composer toolbar']")).to_be_visible(timeout=PAGE_TIMEOUT)
    has_application = page.evaluate(
        "Object.values(ComposerRenderer.PALETTE).flat().some(i => i.type === 'Application')"
    )
    parent_type = APP if has_application else COMP
    _quick_add(page, "Billing " + suffix, parent_type)
    _quick_add(page, "Billing API " + suffix, COMP)
    page.evaluate(
        "n => { const d = %s; const cell = d.graph.getElements().find(e => e.get('elName') === n);"
        " d.ctxMenuCell = cell; d.toggleRenderingMode(); }" % DATA, "Billing " + suffix,
    )
    page.evaluate(
        "s => { const d = %s; const els = d.graph.getElements();"
        " const p = els.find(e => e.get('elName') === 'Billing ' + s);"
        " const c = els.find(e => e.get('elName') === 'Billing API ' + s);"
        " d.nestingPromptParent = p; d.nestingPromptChild = c; d.confirmNesting('grouping'); }" % DATA, suffix,
    )
    for scheme in ("light", "dark"):
        page.evaluate("s => document.documentElement.classList.toggle('dark', s === 'dark')", scheme)
        page.keyboard.press("Escape")
        _shot(page, "composer-%s-%s" % (prefix, scheme))
    context.close()
