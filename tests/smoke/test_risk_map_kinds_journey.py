"""An architect maps a risk to where its cause sits, and the model and the chat agree.

Goal: the risk "Cash runs out" is mapped, from the register, to the product Startup plan, the goal
Reach 20 paying customers, the driver Cash runway and the organisation's business actor. The risk page
lists all four with their types, the risk's Assessment element lists all four as connected elements
(the same detail answer the connected-assistant tool get_element returns), and the chat's "what could
hurt" finds the risk from the product. Unmapping the product removes it from every one of those.
Run at 1440 light and 390 dark. Set RISK_MAP_SHOTS to a folder to keep the screenshots.
"""

import os
import uuid
from pathlib import Path

import pytest
from playwright.sync_api import expect

from .chat_ask import ask, sign_in_as
from .conftest import PAGE_TIMEOUT

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = os.environ.get("RISK_MAP_SHOTS")
KINDS = [  # (picker option, element type, layer, name stem, type word shown)
    ("product", "Product", "Business", "Startup plan", "product"),
    ("goal", "Goal", "Motivation", "Reach 20 paying customers", "goal"),
    ("driver", "Driver", "Motivation", "Cash runway", "driver"),
    ("actor", "BusinessActor", "Business", "The organisation", "business actor"),
]


def _shot(page, name, width):
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / ("%s-%d.png" % (name, width))), full_page=True)


def _seed(org_id, suffix, title):
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement
    from app.models.risk import Risk

    with create_app("testing").app_context():
        ids = {}
        for option, element_type, layer, stem, _word in KINDS:
            element = ArchiMateElement(name="%s %s" % (stem, suffix), type=element_type, layer=layer,
                                       organization_id=org_id)
            db.session.add(element)
            db.session.flush()
            ids[option] = element.id
        risk = Risk(organization_id=org_id, title=title, likelihood=4, impact=4, owner="Chief Executive")
        db.session.add(risk)
        db.session.flush()
        from app.services.archimate_backbone import ensure_archimate_element
        mirror = ensure_archimate_element(risk)
        db.session.commit()
        out = {"risk": risk.id, "mirror": mirror.id, "elements": ids}
        db.session.remove()
        return out


def _drop(org_id, title, element_ids):
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.risk import Risk

    with create_app("testing").app_context():
        for risk in Risk.query.filter_by(organization_id=org_id, title=title).all():
            mirror = risk.archimate_element_id
            db.session.delete(risk)
            db.session.flush()
            if mirror:
                ArchiMateRelationship.query.filter_by(organization_id=org_id, source_id=mirror).delete()
                ArchiMateElement.query.filter_by(id=mirror, organization_id=org_id).delete()
        ArchiMateElement.query.filter(ArchiMateElement.id.in_(list(element_ids)),
                                      ArchiMateElement.organization_id == org_id).delete(synchronize_session=False)
        db.session.commit()
        db.session.remove()


def _connected(page, live_server, mirror):
    reply = page.request.get("%s/archimate/api/elements/%d/detail" % (live_server, mirror))
    assert reply.status == 200
    body = reply.json()
    return {c["name"] for c in body["connected_elements"]}, body["relationship_count"]


@pytest.mark.parametrize("viewport, scheme", [
    pytest.param({"width": 1440, "height": 900}, "light", id="1440-light"),
    pytest.param({"width": 390, "height": 844}, "dark", id="390-dark"),
])
def test_a_risk_mapped_to_where_its_cause_sits_shows_on_the_model_and_in_the_chat(
        browser, live_server, seeded, viewport, scheme):
    org_id = seeded["ids"]["org"]
    suffix = uuid.uuid4().hex[:5]
    title = "Cash runs out %s %s" % (scheme, suffix)
    seed = _seed(org_id, suffix, title)
    names = {option: "%s %s" % (stem, suffix) for option, _t, _l, stem, _w in KINDS}
    context = browser.new_context(viewport=viewport, color_scheme=scheme)
    page = context.new_page()
    width = viewport["width"]
    try:
        sign_in_as(page, live_server, seeded, "solution_architect")
        page.goto(live_server + "/risks/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        row = page.locator("#risk-register-table tbody tr", has_text=title)
        row.get_by_role("button", name="Row actions").click()
        page.get_by_role("menuitem", name="Map to…").click()
        detail = page.get_by_role("dialog", name="Risk detail")

        picker = detail.get_by_test_id("risk-link-picker")
        offered = picker.get_by_label("Entity type to link").locator("option").all_inner_texts()
        assert offered == ["Application", "Capability", "Solution", "Programme", "Product", "Goal",
                           "Driver", "Business actor"]
        for option, _t, _l, _stem, _word in KINDS:
            picker.get_by_label("Entity type to link").select_option(option)
            picker.get_by_label("Search entities to link").fill(names[option])
            with page.expect_response(lambda r: r.url.endswith("/links") and r.request.method == "POST") as sent:
                picker.get_by_test_id("risk-link-choice").first.click()
            assert sent.value.status == 201
        for option, _t, _l, _stem, word in KINDS:
            expect(detail).to_contain_text(names[option])
        _shot(page, "risk-map-picker-kinds", width)

        # The risk page lists all four with their types, and each link lands on a page with content.
        page.goto("%s/risks/%d" % (live_server, seed["risk"]), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        touches = page.locator("main")
        for option, _t, _l, _stem, word in KINDS:
            item = touches.locator("li", has_text=names[option])
            expect(item).to_have_count(1)
            expect(item).to_contain_text(word)
        touches.locator("li").first.scroll_into_view_if_needed()
        _shot(page, "risk-map-risk-page", width)
        if SHOTS:
            touches.locator("div", has=page.get_by_role("heading", name="What it touches")).last.screenshot(
                path=str(Path(SHOTS) / ("risk-map-what-it-touches-%d.png" % width)))
        link = touches.locator("li", has_text=names["product"]).get_by_role("link")
        link.click()
        page.wait_for_load_state("domcontentloaded")
        expect(page.locator("main")).to_contain_text(names["product"])
        page.go_back()

        # The model: the Assessment element lists all four.
        connected, count = _connected(page, live_server, seed["mirror"])
        assert connected == set(names.values()), connected
        assert count == 4

        # The chat finds the risk from the product.
        card = ask(page, live_server, "what could hurt %s" % names["product"]).locator(
            "[data-lens-row]").filter(has_text=title)
        expect(card).to_have_count(1)
        _shot(page, "risk-map-chat-answer", width)

        # Unmapping the product removes it from the model and the chat.
        page.goto(live_server + "/risks/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.locator("#risk-register-table tbody tr", has_text=title).get_by_role("button", name="Row actions").click()
        page.get_by_role("menuitem", name="Map to…").click()
        detail = page.get_by_role("dialog", name="Risk detail")
        entry = detail.locator("li", has_text=names["product"])
        with page.expect_response(lambda r: "/links/" in r.url and r.request.method == "DELETE"):
            entry.get_by_role("button", name="Unmap").click()
        page.goto("%s/risks/%d" % (live_server, seed["risk"]), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator("main").locator("li", has_text=names["product"])).to_have_count(0)
        connected, count = _connected(page, live_server, seed["mirror"])
        assert connected == {n for o, n in names.items() if o != "product"}, connected
        assert count == 3
        answer = ask(page, live_server, "what could hurt %s" % names["product"])
        expect(answer.locator("[data-lens-row]").filter(has_text=title)).to_have_count(0)
    finally:
        context.close()
        _drop(org_id, title, seed["elements"].values())
