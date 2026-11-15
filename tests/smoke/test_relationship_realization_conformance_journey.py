"""An architect checking a drawn model sees realizations ArchiMate 3.2 permits accepted.

The goal: "import my diagram and see which relationships are not allowed". The architect signs in, opens
the diagram import page, chooses a drawing in which a capability realizes an outcome and a work package
realizes an application component, presses Preview, and reads the conformance line: both relationships are
counted as permitted and none as not permitted.

Run at 1440 wide in the light scheme and at 390 wide in the dark scheme.
"""

import json
import os
import tempfile
from pathlib import Path

import pytest

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK = Path(os.environ.get("REALIZATION_WALK_DIR") or tempfile.mkdtemp(prefix="realization-walk-"))
VIEWS = [("light", 1440, 900), ("dark", 390, 844)]


def _shape(shape_id, stereotype, name, x):
    return {
        "id": shape_id,
        "class": "GenericRectangle",
        "textAreas": [{"label": "Text", "text": "«%s»\n%s" % (stereotype, name)}],
        "boundingBox": {"x": x, "y": 0, "w": 100, "h": 100},
    }


def _realization(line_id, source, target):
    return {
        "id": line_id,
        "endpoint1": {"connectedTo": source, "style": "None"},
        "endpoint2": {"connectedTo": target, "style": "Hollow Triangle"},
        "stroke": {"style": "dotted"},
    }


@pytest.fixture(scope="module")
def diagram(tmp_path_factory):
    payload = {
        "title": "Realizations",
        "pages": [{"id": "p1", "title": "Page 1", "items": {
            "shapes": [
                _shape("cap", "Capability", "Order Fulfilment", 0),
                _shape("out", "Outcome", "Orders Delivered On Time", 300),
                _shape("wp", "WorkPackage", "Rebuild Order Portal", 600),
                _shape("app", "ApplicationComponent", "Order Portal", 900),
            ],
            "lines": [_realization("l1", "cap", "out"), _realization("l2", "wp", "app")],
        }}],
    }
    path = tmp_path_factory.mktemp("diagram") / "realizations.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def architect(live_server):
    return create_fresh_org("enterprise_architect")["emails"]["enterprise_architect"]


@pytest.fixture(params=VIEWS, ids=lambda v: "%s-%d" % (v[0], v[1]))
def page(request, browser, live_server, architect):
    scheme, width, height = request.param
    context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme)
    page = context.new_page()
    sign_in(page, live_server, architect)
    page._view = "%s-%d" % (scheme, width)
    yield page
    context.close()


def test_permitted_realizations_are_counted_as_conforming(page, live_server, diagram):
    response = page.goto(live_server + "/archimate/import/diagram", wait_until="domcontentloaded",
                         timeout=PAGE_TIMEOUT)
    assert response.status == 200
    page.set_input_files("#diagram-file", str(diagram))
    page.get_by_role("button", name="Preview").click()
    line = page.get_by_text("ArchiMate 3.2 conformance:").locator("xpath=..")
    line.wait_for(timeout=PAGE_TIMEOUT)
    WALK.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(WALK / ("conformance-%s.png" % page._view)), full_page=True)
    text = " ".join(line.inner_text().split())
    assert "2 permitted, 0 not permitted" in text, text
