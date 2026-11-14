"""Organisation B has a segment with the same name on its own canvas. Organisation A's architect never sees it
on the canvas page, in the JSON, in the chat's answer or through get_canvas, and asking for B's canvas id is
answered as not found."""

import pytest

from .chat_ask import ask, sign_in_as
from .conftest import PAGE_TIMEOUT, _seed_standard_org
from .test_canvas_one_store_journey import QUESTION, SEGMENTS, chat_names, page_segments, seed_canvas

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def test_another_organisations_segment_never_reaches_this_one(browser, live_server, seeded, request, ai_protocol_stub):
    other = _seed_standard_org(request, ai_protocol_stub)
    a = seed_canvas(seeded["ids"]["org"], canvas_name="Our model")
    b = seed_canvas(other["ids"]["org"], canvas_name="Their model", first="Regional logistics firms", legacy="Their agencies")
    # B also holds a segment A does not have, so a leak cannot hide behind the shared name.
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement
    from app.models.business_model import BusinessModelCanvas
    from app.modules.business_model_canvas import model_service as ms

    app = create_app("testing")
    with app.app_context():
        canvas = BusinessModelCanvas.query.filter_by(id=b["canvas"], organization_id=other["ids"]["org"]).one()
        extra = ArchiMateElement(name="Only they serve this", type="BusinessActor", layer="business",
                                 organization_id=other["ids"]["org"])
        db.session.add(extra)
        db.session.flush()
        ms.add_entry(canvas, "customer_segments", element_id=extra.id)

    context = browser.new_context(ignore_https_errors=True)
    context.set_default_timeout(PAGE_TIMEOUT)
    context.set_default_navigation_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        sign_in_as(page, live_server, seeded, "business_architect")
        page.goto("%s/business-model/%d" % (live_server, a["canvas"]), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert page_segments(page) == SEGMENTS
        assert "Only they serve this" not in page.content()

        data = page.request.get("%s/business-model/api/%d" % (live_server, a["canvas"]))
        assert data.status == 200
        assert "Only they serve this" not in data.text() and "Their" not in data.text()

        card = ask(page, live_server, QUESTION)
        assert chat_names(card, "Our model") == SEGMENTS
        assert "Their model" not in card.inner_text() and "Only they serve this" not in card.inner_text()

        assert page.request.get("%s/business-model/api/%d" % (live_server, b["canvas"])).status == 404
        response = page.goto("%s/business-model/%d" % (live_server, b["canvas"]), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert response.status == 404
        assert "Only they serve this" not in page.content()
    finally:
        context.close()
