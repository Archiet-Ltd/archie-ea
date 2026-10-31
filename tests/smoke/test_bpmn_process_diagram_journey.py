"""A business analyst draws how an order is delivered, in a real browser.

The goal: the business architect opens the seeded order-to-delivery process, adds a task in the
logistics lane, an exclusive gateway with two labelled flows and an end event, exports BPMN 2.0 XML and
imports it back unchanged, and sees that each lane is a business role of the model. Every step is a
click on a real control, and every figure the page shows is checked against the stored drawing.

The process is seeded in an organisation with linked data (roles, a sub-process); a second
organisation proves one cannot open the other's diagram; an organisation with a process nobody has
drawn proves the empty page says why it is empty and what to do next.
"""
import json
import re
import uuid
from xml.etree import ElementTree as ET

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PASSWORD

PAGE_TIMEOUT = 30000
APP = '[data-testid="bpmn-editor"]'
ROLE_NAMES = ("Service Desk Operator", "Logistics Planner", "Quality Assurance Lead")


def _assert_signed_in_as_business_architect(email):
    """The journey's persona is the business architect (the analyst), not an administrator."""
    from app import create_app
    from app.models.user import User

    with create_app("testing").app_context():
        user = User.query.filter_by(email=email).one()
        assert user.enterprise_role == "business_architect"
        assert not user.is_platform_admin
        assert not user.is_org_admin


@pytest.fixture(scope="module")
def process_ids(seeded):
    """The seeded order-to-delivery process, a sub-process, an undrawn process and another organisation's."""
    from app import create_app, db
    from app.models import ArchiMateElement
    from app.models.organization import Organization
    from app.models.process_data import BusinessProcess
    from app.modules.business_architecture import bpmn_service

    org_id = seeded["ids"]["org"]
    suffix = uuid.uuid4().hex[:6]
    with create_app("testing").app_context():
        roles = []
        for name in ROLE_NAMES:
            el = ArchiMateElement(name="%s %s" % (name, suffix), type="BusinessRole", layer="business", organization_id=org_id)
            db.session.add(el)
            db.session.flush()
            roles.append((el.id, el.name))
        extra = ArchiMateElement(name="Compliance Reviewer %s" % suffix, type="BusinessRole", layer="business", organization_id=org_id)
        db.session.add(extra)
        main = BusinessProcess(name="Order to delivery %s" % suffix, organization_id=org_id, level=2, process_owner="Logistics Planner")
        undrawn = BusinessProcess(name="Onboard supplier %s" % suffix, organization_id=org_id, level=2)
        other = Organization(name="Other %s" % suffix, slug="other-%s" % suffix)
        db.session.add_all([main, undrawn, other])
        db.session.flush()
        flow = bpmn_service.order_to_delivery_flow(roles)
        bpmn_service.save_flow(main, flow, org_id)
        foreign = BusinessProcess(name="Foreign process %s" % suffix, organization_id=other.id, level=2)
        db.session.add(foreign)
        db.session.commit()
        child = BusinessProcess.query.filter_by(parent_process_id=main.id).one()
        return {"main": main.id, "main_name": main.name, "child": child.id, "undrawn": undrawn.id, "undrawn_name": undrawn.name,
                "foreign": foreign.id, "roles": roles, "extra_role": extra.name, "suffix": suffix, "org": org_id}


def _sign_in(browser, live_server, seeded, width=1440):
    email = seeded["emails"]["business_architect"]
    _assert_signed_in_as_business_architect(email)
    context = browser.new_context(ignore_https_errors=True, accept_downloads=True, viewport={"width": width, "height": 900})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.goto(live_server + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)
    return context, page, errors


def _flow(page):
    return page.evaluate("(sel) => JSON.parse(JSON.stringify(Alpine.$data(document.querySelector(sel)).flow))", APP)


def _canon(flow):
    """The drawing without the order the shapes were added in (the server keeps them pool by pool)."""
    return {"pools": flow["pools"], "nodes": sorted(flow["nodes"], key=lambda n: n["id"]), "flows": sorted(flow["flows"], key=lambda f: f["id"])}


def _open_rail(page):
    """The shapes list starts closed on a screen this narrow; open it from the toolbar, as a person would."""
    if not page.get_by_test_id("bpmn-rail").is_visible():
        page.get_by_test_id("bpmn-rail-toggle").click()
    expect(page.get_by_test_id("bpmn-rail")).to_be_visible()


def _saved(page):
    expect(page.get_by_test_id("status-save")).to_contain_text("Saved", timeout=PAGE_TIMEOUT)
    page.wait_for_function("(sel) => !Alpine.$data(document.querySelector(sel)).dirty", arg=APP, timeout=PAGE_TIMEOUT)


def _counts(page):
    text = page.get_by_test_id("status-counts").inner_text()
    m = re.match(r"(\d+) steps?, (\d+) flows?", text)
    assert m, text
    return int(m.group(1)), int(m.group(2))


def _open_diagram(page, live_server, process_id):
    page.goto("%s/business-architecture/processes/%d/diagram" % (live_server, process_id), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("bpmn-editor")).to_be_visible(timeout=PAGE_TIMEOUT)
    page.wait_for_selector("svg [data-lane-id]", timeout=PAGE_TIMEOUT)


def _centre(page, selector):
    box = page.locator(selector).first.bounding_box()
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


def _click_shape(page, node_id):
    x, y = _centre(page, 'svg [data-node-id="%s"]' % node_id)
    page.mouse.click(x, y)


@pytest.mark.smoke
def test_analyst_draws_the_delivery_flow_exports_and_imports_it_back(browser, live_server, seeded, process_ids, tmp_path):
    context, page, errors = _sign_in(browser, live_server, seeded)
    try:
        # The analyst finds the process from the list, through the Diagram view, not from a URL.
        page.goto(live_server + "/business-architecture/processes", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        row = page.get_by_test_id("process-row").filter(has_text=process_ids["main_name"]).first  # its sub-process row names it as its parent
        expect(row).to_contain_text("18 steps")
        page.get_by_role("link", name="Diagram", exact=True).click()
        page.wait_for_url(re.compile(r"view=diagram"), timeout=PAGE_TIMEOUT)
        card = page.get_by_test_id("process-diagram-cards").locator("div", has_text=process_ids["main_name"]).last
        expect(card).to_contain_text("lanes")
        page.get_by_test_id("process-diagram-cards").get_by_role("link", name=process_ids["main_name"]).click()
        page.wait_for_selector("svg [data-lane-id]", timeout=PAGE_TIMEOUT)

        # It opens filled: figures agree between the status line, the finder and the stored drawing.
        before = _flow(page)
        assert (len(before["nodes"]), len(before["flows"])) == (18, 19)
        assert _counts(page) == (18, 19)
        _open_rail(page)
        page.get_by_test_id("rail-tab-view").click()
        expect(page.get_by_test_id("finder-count")).to_have_text("18")
        expect(page.get_by_test_id("finder-list")).to_contain_text("Reserve replacement stock")
        expect(page.get_by_test_id("bpmn-legend")).to_contain_text("Message flow")
        # Every kind of shape is on the canvas.
        for kind in ("start", "intermediate", "end", "task", "subprocess", "xor", "and", "event"):
            assert page.locator('svg [data-node-type="%s"]' % kind).count() >= 1, kind
        assert page.locator('svg [data-flow-type="message"]').count() == 2
        assert page.locator("svg [data-lane-id]").count() == 4
        page.screenshot(path=str(tmp_path / "opened.png"))

        # A lane is a business role of the model: the lane panel says so, and names the role.
        lane = process_ids["roles"][1]
        page.locator('svg [data-lane-id="lane2"]').click()
        expect(page.get_by_test_id("role-linked")).to_contain_text(lane[1])
        assert page.locator('svg [data-lane-id="lane2"]').get_attribute("data-lane-role") == str(lane[0])

        # Add a task in the logistics lane (the lane is selected), and name it.
        page.get_by_test_id("rail-tab-add").click()
        page.get_by_test_id("palette-task").click()
        label = page.get_by_test_id("props-label")
        expect(label).to_be_focused()
        label.fill("Confirm delivery slot")
        task = _flow(page)["nodes"][-1]
        assert task["label"] == "Confirm delivery slot" and task["lane"] == "lane2" and task["type"] == "task"
        # An exclusive gateway and an end event in the same lane.
        page.get_by_test_id("palette-xor").click()
        page.get_by_test_id("props-label").fill("Slot free?")
        gateway = _flow(page)["nodes"][-1]
        assert gateway["type"] == "xor" and gateway["lane"] == "lane2"
        page.get_by_test_id("palette-end").click()
        page.get_by_test_id("props-label").fill("Slot booked")
        end = _flow(page)["nodes"][-1]
        assert end["type"] == "end" and end["lane"] == "lane2"
        # Join them: task to gateway, then the gateway to the end event (labelled) and back to the task (labelled).
        page.get_by_test_id("tool-connect").click()
        for a, b, text in ((task["id"], gateway["id"], None), (gateway["id"], end["id"], "Yes"), (gateway["id"], task["id"], "No, try the next slot")):
            _click_shape(page, a)
            _click_shape(page, b)
            if text:
                page.get_by_test_id("props-flow-label").fill(text)
        page.get_by_test_id("tool-select").click()
        drawn = _flow(page)
        assert len(drawn["nodes"]) == 21 and len(drawn["flows"]) == 22
        out_of_gateway = {f["label"] for f in drawn["flows"] if f["source"] == gateway["id"]}
        assert out_of_gateway == {"Yes", "No, try the next slot"}
        assert _counts(page) == (21, 22)
        expect(page.get_by_test_id("finder-count")).to_have_text("21")
        _saved(page)

        # It persisted: after a reload the same drawing, and the same counts, are there.
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("svg [data-lane-id]", timeout=PAGE_TIMEOUT)
        assert _canon(_flow(page)) == _canon(drawn)
        assert _counts(page) == (21, 22)

        # Export BPMN 2.0 XML: a real file with the lanes, the gateway and its two labelled flows.
        with page.expect_download() as info:
            page.get_by_test_id("bpmn-export").click()
        first = tmp_path / "order-to-delivery.bpmn"
        info.value.save_as(str(first))
        text = first.read_text()
        root = ET.fromstring(text)
        ns = {"b": "http://www.omg.org/spec/BPMN/20100524/MODEL"}
        assert [ln.get("name") for ln in root.findall(".//b:lane", ns)][:3] == [r[1] for r in process_ids["roles"]]
        gw = [g for g in root.findall(".//b:exclusiveGateway", ns) if g.get("name") == "Slot free?"][0]
        assert {f.get("name") for f in root.findall(".//b:sequenceFlow", ns) if f.get("sourceRef") == gw.get("id")} == {"Yes", "No, try the next slot"}
        assert len(root.findall(".//b:messageFlow", ns)) == 2

        # Import it back: nothing changes, and exporting again gives the same file.
        page.get_by_test_id("bpmn-import-open").click()
        page.get_by_test_id("bpmn-import-file").set_input_files(str(first))
        page.get_by_test_id("bpmn-import-submit").click()
        expect(page.get_by_test_id("bpmn-import-dialog")).to_be_hidden(timeout=PAGE_TIMEOUT)
        assert _canon(_flow(page)) == _canon(drawn)
        assert _counts(page) == (21, 22)
        with page.expect_download() as info:
            page.get_by_test_id("bpmn-export").click()
        second = tmp_path / "again.bpmn"
        info.value.save_as(str(second))
        assert second.read_text() == text

        assert errors == []
    finally:
        context.close()


@pytest.mark.smoke
def test_a_new_lane_takes_its_role_from_the_model_and_a_subprocess_opens_its_own_diagram(browser, live_server, seeded, process_ids):
    context, page, errors = _sign_in(browser, live_server, seeded)
    try:
        _open_diagram(page, live_server, process_ids["main"])
        steps_before = _counts(page)[0]
        # Add a lane, then link it to a business role by searching the model.
        page.get_by_test_id("add-lane").click()
        expect(page.get_by_test_id("role-unlinked")).to_contain_text("not linked to a business role")
        page.get_by_test_id("role-search").fill("Compliance Reviewer %s" % process_ids["suffix"])
        role = page.get_by_test_id("role-results").get_by_role("button", name=process_ids["extra_role"])
        role.click()
        expect(page.get_by_test_id("role-linked")).to_contain_text(process_ids["extra_role"])
        _saved(page)
        stored = _flow(page)["pools"][0]["lanes"][-1]
        assert stored["name"] == process_ids["extra_role"] and stored["roleId"]
        # The model records it: the role is assigned to the process in ArchiMate.
        from app import create_app
        from app.models import ArchiMateRelationship
        from app.models.process_data import BusinessProcess

        with create_app("testing").app_context():
            process = BusinessProcess.query.filter_by(id=process_ids["main"], organization_id=process_ids["org"]).one()
            assigned = {r.source_id for r in ArchiMateRelationship.query.filter_by(
                target_id=process.archimate_element_id, type="Assignment", organization_id=process_ids["org"])}
            assert stored["roleId"] in assigned
        # A lane with steps in it cannot be removed; the page says why.
        page.locator('svg [data-lane-id="lane2"]').click()
        page.get_by_test_id("props-delete-lane").click()
        expect(page.get_by_test_id("diagram-status-line")).to_contain_text("Move or delete the shapes in this lane first")
        # The sub-process shape is a process of its own: select it, open it, and land on content.
        _click_shape(page, "pack")
        expect(page.get_by_test_id("props-subprocess")).to_contain_text("its own diagram")
        page.get_by_test_id("open-subprocess").click()
        page.wait_for_url(re.compile(r"/processes/%d/diagram" % process_ids["child"]), timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("diagram-title")).to_have_text("Pack and inspect order")
        expect(page.get_by_test_id("bpmn-empty")).to_contain_text("Nothing is drawn for this process yet")
        expect(page.get_by_test_id("bpmn-empty")).to_contain_text("Add a start event")
        # The way back lands on the parent's drawing, with content.
        page.get_by_test_id("bpmn-parent").click()
        page.wait_for_url(re.compile(r"/processes/%d/diagram" % process_ids["main"]), timeout=PAGE_TIMEOUT)
        page.wait_for_selector("svg [data-node-id]", timeout=PAGE_TIMEOUT)
        assert _counts(page)[0] == steps_before
        # The child's own diagram can be drawn: a start event is added and survives a reload.
        page.goto("%s/business-architecture/processes/%d/diagram" % (live_server, process_ids["child"]), wait_until="domcontentloaded")
        _open_rail(page)
        page.get_by_test_id("palette-start").click()
        _saved(page)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("svg [data-node-id]", timeout=PAGE_TIMEOUT)
        assert _counts(page) == (1, 0)
        assert errors == []
    finally:
        context.close()


@pytest.mark.smoke
def test_another_organisation_cannot_open_the_diagram_and_an_undrawn_process_says_what_to_do(browser, live_server, seeded, process_ids):
    context, page, errors = _sign_in(browser, live_server, seeded)
    try:
        response = page.goto("%s/business-architecture/processes/%d/diagram" % (live_server, process_ids["foreign"]), wait_until="domcontentloaded")
        assert response.status == 404
        listing = page.goto(live_server + "/business-architecture/processes", wait_until="domcontentloaded")
        assert "Foreign process" not in page.content()
        assert listing.status == 200
        expect(page.get_by_test_id("process-row").filter(has_text=process_ids["undrawn_name"])).to_contain_text("Not drawn")
        _open_diagram(page, live_server, process_ids["undrawn"])
        expect(page.get_by_test_id("bpmn-empty")).to_contain_text("Add a start event")
        expect(page.get_by_test_id("finder-empty")).to_be_hidden()
        _open_rail(page)
        page.get_by_test_id("rail-tab-view").click()
        expect(page.get_by_test_id("finder-empty")).to_contain_text("No steps are drawn yet")
        page.get_by_role("button", name="Open the shapes").click()
        expect(page.get_by_test_id("bpmn-palette")).to_be_visible()
        assert errors == []
    finally:
        context.close()


@pytest.mark.smoke
def test_the_diagram_fits_a_phone_without_hiding_the_job(browser, live_server, seeded, process_ids):
    context, page, errors = _sign_in(browser, live_server, seeded, width=390)
    try:
        _open_diagram(page, live_server, process_ids["main"])
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        # The shapes list starts closed on a phone and opens from the toolbar.
        expect(page.get_by_test_id("bpmn-rail")).to_be_hidden()
        page.get_by_test_id("bpmn-rail-toggle").click()
        expect(page.get_by_test_id("palette-task")).to_be_visible()
        page.get_by_test_id("bpmn-rail-toggle").click()
        expect(page.get_by_test_id("bpmn-canvas")).to_be_visible()
        box = page.get_by_test_id("bpmn-canvas").bounding_box()
        assert box["width"] >= 300 and box["height"] >= 300
        # Export stays reachable.
        expect(page.get_by_test_id("bpmn-export")).to_be_visible()
        assert errors == []
    finally:
        context.close()


def _drawing_offset(page):
    """Where the drawing sits in the canvas: the transform JointJS puts on its layer group."""
    return page.evaluate("document.querySelector('[data-testid=bpmn-canvas] svg .joint-layers, [data-testid=bpmn-canvas] svg .joint-viewport').getAttribute('transform')")


@pytest.mark.smoke
def test_space_and_drag_pans_the_drawing_as_the_status_line_promises(browser, live_server, seeded, process_ids):
    """The status line says "Space + drag to pan". Holding Space and dragging the canvas moves the drawing, with the
    Select tool still active, and letting go of Space returns to selecting: the next click picks a shape."""
    context, page, errors = _sign_in(browser, live_server, seeded)
    try:
        _open_diagram(page, live_server, process_ids["main"])
        expect(page.get_by_test_id("diagram-status-line")).to_contain_text("Space")
        page.get_by_test_id("tool-select").click()
        before = _drawing_offset(page)
        box = page.get_by_test_id("bpmn-canvas").bounding_box()
        x, y = box["x"] + box["width"] - 30, box["y"] + box["height"] - 30
        page.mouse.move(x, y)  # the pointer is over the canvas, as a person holding Space to pan has it
        page.keyboard.down("Space")
        page.mouse.down()
        page.mouse.move(x - 150, y - 80, steps=10)
        page.mouse.up()
        page.keyboard.up("Space")
        assert _drawing_offset(page) != before, "Space + drag did not move the drawing"
        # Space over the canvas did not press the focused Select button either: the tool is still Select.
        assert page.evaluate("(sel) => Alpine.$data(document.querySelector(sel)).tool", APP) == "select"
        # Space released: a plain click selects again, and the canvas did not scroll the page.
        _click_shape(page, "receive")
        expect(page.get_by_test_id("status-selection")).to_contain_text("Selected:")
        assert page.evaluate("window.scrollY") == 0
        # The shortcuts help says the same thing as the status line.
        page.get_by_role("button", name="Keyboard Shortcuts").click()
        expect(page.get_by_role("dialog")).to_contain_text("Space + drag")
        page.keyboard.press("Escape")
        assert errors == []
    finally:
        context.close()


@pytest.mark.smoke
def test_the_process_list_is_a_table_whose_every_row_opens_its_diagram(browser, live_server, seeded, process_ids):
    """The List view is a real table (column headers a screen reader announces), each process names its owner and
    whether it is drawn, and Open diagram lands on that process's diagram."""
    context, page, errors = _sign_in(browser, live_server, seeded)
    try:
        page.goto(live_server + "/business-architecture/processes", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        table = page.get_by_test_id("processes-table")
        expect(table).to_be_visible()
        for heading in ("Process", "Part of", "Owner", "Diagram"):
            expect(table.get_by_role("columnheader", name=heading)).to_be_visible()
        row = page.get_by_test_id("process-row").filter(has_text=process_ids["main_name"]).first
        expect(row).to_contain_text("Logistics Planner")
        expect(row).to_contain_text("steps")
        undrawn = page.get_by_test_id("process-row").filter(has_text=process_ids["undrawn_name"])
        expect(undrawn).to_contain_text("Not drawn")
        expect(undrawn).to_contain_text("No owner recorded")
        row.get_by_role("link", name="Open diagram").click()
        page.wait_for_url(re.compile(r"/business-architecture/processes/%d/diagram$" % process_ids["main"]), timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("diagram-title")).to_have_text(process_ids["main_name"])
        assert errors == []
    finally:
        context.close()
