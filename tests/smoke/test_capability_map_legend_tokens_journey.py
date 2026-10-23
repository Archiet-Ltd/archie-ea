"""A business architect opens the capability map and its flow view.

The organisation has a domain, a capability under it and an application mapped
to that capability, so the flow diagram has something to draw. The page the
person opens carries no hard-coded colour and loads no d3 script until the flow
view is opened; once it is, the legend names every layer, each swatch takes its
colour from a class and not from an inline style, and the diagram is drawn.
"""

import re

import pytest

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

HEX = re.compile(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b(?![0-9a-zA-Z_-])")
D3_EXTRAS = re.compile(r"d3-(sankey|chord)", re.I)


def _seed(org_id, suffix):
    from app import create_app, db
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capabilities import BusinessCapability

    app = create_app("testing")
    with app.app_context():
        domain = BusinessCapability(name="Customer Domain %s" % suffix, level=1,
                                    business_domain="Customer", organization_id=org_id)
        db.session.add(domain)
        db.session.commit()
        capability = BusinessCapability(name="Onboarding %s" % suffix, level=2,
                                        business_domain="Customer",
                                        parent_capability_id=domain.id, organization_id=org_id)
        component = ApplicationComponent(name="Portal %s" % suffix, organization_id=org_id,
                                         lifecycle_status="operational")
        db.session.add_all([capability, component])
        db.session.commit()
        db.session.add(ApplicationCapabilityMapping(
            application_component_id=component.id, business_capability_id=capability.id,
            organization_id=org_id))
        db.session.commit()
        db.session.remove()


def test_capability_map_legend_uses_tokens_and_flow_loads_d3_only_when_opened(browser, live_server):
    org = create_fresh_org("business_architect")
    _seed(org["org_id"], org["suffix"])
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["business_architect"])
        page.goto(live_server + "/capability-map/", wait_until="load", timeout=PAGE_TIMEOUT)

        # Opening the page loads no d3 module and carries no hard-coded colour.
        script_srcs = page.eval_on_selector_all("script[src]", "els => els.map(e => e.src)")
        d3_before = [s for s in script_srcs if "d3" in s.lower()]
        assert not d3_before, "the capability map loads d3 scripts as soon as it opens: %s" % d3_before
        html = page.content()
        assert not D3_EXTRAS.search(html), "the page names a d3 extras module"
        assert not HEX.findall(html), "raw hex colours in the page: %s" % sorted(set(HEX.findall(html)))[:10]
        own_scripts = [s for s in script_srcs if "/static/js/" in s]
        assert own_scripts
        for src in own_scripts:
            body = page.request.get(src).text()
            assert not HEX.findall(body), "raw hex colours in %s: %s" % (src, sorted(set(HEX.findall(body)))[:10])

        # Open the flow view the way a person does: from the lens menu.
        page.goto(live_server + "/capability-map/?tab=flow", wait_until="load", timeout=PAGE_TIMEOUT)
        panel = page.locator("#panel-flow")
        panel.locator("svg rect").first.wait_for(state="attached", timeout=PAGE_TIMEOUT)
        assert panel.locator("svg rect").count() >= 3, "the flow diagram drew no nodes"
        assert "Portal %s" % org["suffix"] in panel.inner_text()

        swatches = panel.locator("span.w-2\\.5.h-2\\.5.rounded-sm")
        assert swatches.count() == 7, "the layer legend should name seven layers"
        for i in range(swatches.count()):
            swatch = swatches.nth(i)
            assert swatch.get_attribute("style") in (None, ""), "legend swatch %d has an inline style" % i
            assert re.search(r"\bbg-(layer-[a-z]+|primary)\b", swatch.get_attribute("class")), \
                swatch.get_attribute("class")
        colours = {swatches.nth(i).evaluate("e => getComputedStyle(e).backgroundColor") for i in range(7)}
        assert len(colours) == 7, "two legend layers share a colour: %s" % colours
        assert not HEX.findall(page.content()), "raw hex colours after opening the flow view"
        fills = panel.locator("svg rect").evaluate_all("els => els.map(e => e.getAttribute('fill'))")
        assert all(f and f.startswith("hsl(") for f in fills if f != "none"), fills
        d3_after = [s for s in page.eval_on_selector_all("script[src]", "els => els.map(e => e.src)")
                    if "d3" in s.lower()]
        assert any("d3-sankey" in s for s in d3_after)
    finally:
        context.close()
