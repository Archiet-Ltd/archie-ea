"""An enterprise architect writes the "Approve supplier" decision table, links it to the "Onboard supplier"
process, tries an input, exports it as DMN XML, and a second organisation imports that file and gets the same rules.

Everything after the two organisations are seeded is done by clicking the real controls and reading the
rendered page. The exported file is checked against the bundled DMN 1.5 schema.
"""

import os
import pathlib
import re

import pytest
from lxml import etree
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

XSD = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "dmn15" / "DMN15.xsd"
WALK = os.environ.get("DMN_WALK_DIR")
LIST = "/architecture/decision-tables/"

RULES = [
    ("<1000", "-", '"auto approve"', "Small spend"),
    ("[1000..50000]", '"low","medium"', '"buyer approves"', "Standard path"),
    (">50000", "-", '"board approves"', ""),
]


def _seed_process(org_id):
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement

    app = create_app("testing")
    with app.app_context():
        process = ArchiMateElement(
            name="Onboard supplier", type="BusinessProcess", layer="business", organization_id=org_id,
        )
        db.session.add(process)
        db.session.commit()
        db.session.remove()


def _context(browser, width=1440):
    context = browser.new_context(viewport={"width": width, "height": 900})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    return context


def _shot(page, name, width):
    if not WALK:
        return
    os.makedirs(WALK, exist_ok=True)
    for theme in ("light", "dark"):
        page.evaluate("t => localStorage.setItem('theme', t)", theme)
        page.reload(wait_until="networkidle", timeout=PAGE_TIMEOUT)
        page.screenshot(path=os.path.join(WALK, "%s-%d-%s.png" % (name, width, theme)), full_page=True)
    page.evaluate("() => localStorage.setItem('theme', 'light')")
    page.reload(wait_until="networkidle", timeout=PAGE_TIMEOUT)


def _shot_open(page, name, width, opener):
    """Screenshot the page with a dialog open, in both themes (a dialog does not survive a reload)."""
    if not WALK:
        return
    os.makedirs(WALK, exist_ok=True)
    for theme in ("light", "dark"):
        page.evaluate("t => localStorage.setItem('theme', t)", theme)
        page.reload(wait_until="networkidle", timeout=PAGE_TIMEOUT)
        opener()
        page.wait_for_timeout(500)  # the dialog finishes its opening animation
        page.screenshot(path=os.path.join(WALK, "%s-%d-%s.png" % (name, width, theme)))
        page.keyboard.press("Escape")
    page.evaluate("() => localStorage.setItem('theme', 'light')")
    page.reload(wait_until="networkidle", timeout=PAGE_TIMEOUT)


def _add_column(page, kind, label, type_label):
    page.get_by_test_id("column-add").click()
    page.select_option("#column-kind", kind)
    page.fill("#column-label", label)
    page.select_option("#column-type", type_label)
    page.get_by_test_id("column-save").click()
    expect(page.get_by_test_id("dmn-%s-head" % kind).filter(has_text=label)).to_be_visible(timeout=PAGE_TIMEOUT)


def _add_rule(page, inputs, outputs, note):
    page.get_by_test_id("rule-add").click()
    for i, value in enumerate(inputs):
        page.fill("#rule-in-%d" % i, value)
    for i, value in enumerate(outputs):
        page.fill("#rule-out-%d" % i, value)
    page.fill("#rule-annotation", note)
    page.get_by_test_id("rule-save").click()


def _rule_texts(page):
    rows = page.get_by_test_id("dmn-rule")
    return [re.sub(r"\s+", " ", rows.nth(i).inner_text()).strip() for i in range(rows.count())]


def test_write_link_try_export_and_import_into_a_second_organisation(browser, live_server, tmp_path):
    org_a = create_fresh_org("enterprise_architect")
    org_b = create_fresh_org("enterprise_architect")
    _seed_process(org_a["org_id"])
    email_a = org_a["emails"]["enterprise_architect"]
    email_b = org_b["emails"]["enterprise_architect"]

    context = _context(browser)
    page = context.new_page()
    sign_in(page, live_server, email_a)

    # First use: the list is empty and says what to do.
    page.goto(live_server + LIST, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_text("No decision tables yet.")).to_be_visible(timeout=PAGE_TIMEOUT)
    _shot(page, "decisions-empty", 1440)

    # Create the decision with the First hit policy.
    page.get_by_role("button", name="New decision").click()
    page.fill("#decision-name", "Approve supplier")
    page.select_option("#decision-policy", "F")
    page.get_by_test_id("decision-create-submit").click()
    page.wait_for_url(re.compile(r"/architecture/decision-tables/\d+$"), timeout=PAGE_TIMEOUT)
    decision_url = page.url
    expect(page.get_by_role("heading", name="Approve supplier", level=1)).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("dmn-hit-policy-letter")).to_have_text("F")
    expect(page.get_by_test_id("decision-columns-empty")).to_be_visible()

    # Two inputs and one output, then three rules.
    _add_column(page, "input", "Annual spend", "number")
    _add_column(page, "input", "Risk rating", "string")
    _add_column(page, "output", "Approval", "string")
    expect(page.get_by_test_id("decision-rules-empty")).to_be_visible()
    for inputs_and_outputs in RULES:
        _add_rule(page, list(inputs_and_outputs[:2]), [inputs_and_outputs[2]], inputs_and_outputs[3])
        expect(page.get_by_test_id("dmn-rule")).to_have_count(RULES.index(inputs_and_outputs) + 1, timeout=PAGE_TIMEOUT)
    rows = _rule_texts(page)
    assert rows[1].startswith("2 [1000..50000]") and '"buyer approves"' in rows[1] and "Standard path" in rows[1]
    # DMN's form: hit policy top left, a double rule between inputs and outputs, rule numbers first.
    expect(page.get_by_test_id("dmn-double-rule")).to_have_class(re.compile("border-l-2"))
    assert page.get_by_test_id("dmn-rule-number").first.inner_text().strip() == "1"
    assert page.locator('[data-testid="dmn-hit-policy-letter"]').get_attribute("title").startswith("First")
    _shot(page, "decision-table", 1440)

    # The rule editor refuses an entry it cannot read, saying why.
    page.get_by_test_id("rule-add").click()
    page.fill("#rule-in-0", "<abc")
    page.fill("#rule-out-0", '"x"')
    page.get_by_test_id("rule-save").click()
    expect(page.get_by_text('"abc" is not a number.').first).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("dmn-rule")).to_have_count(3)

    # A range on a text column is refused in plain words, and nothing is stored.
    page.get_by_test_id("rule-add").click()
    page.fill("#rule-in-0", "<1000")
    page.fill("#rule-in-1", "[1..10]")
    page.fill("#rule-out-0", '"x"')
    page.get_by_test_id("rule-save").click()
    expect(page.get_by_text('"[1..10]" needs a number column.').first).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("dmn-rule")).to_have_count(3)

    # Editing a rule opens the same dialog filled with that rule; the change persists.
    page.get_by_role("button", name="Edit rule 3").click()
    expect(page.locator("#rule-in-0")).to_have_value(">50000")
    expect(page.get_by_role("heading", name="Edit rule 3")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator("#rule-dialog")).to_be_hidden()
    _shot_open(page, "rule-editor", 1440, lambda: page.get_by_role("button", name="Edit rule 3").click())
    page.get_by_role("button", name="Edit rule 3").click()
    page.fill("#rule-annotation", "Over the limit")
    page.get_by_test_id("rule-save").click()
    expect(page.get_by_test_id("dmn-rule").nth(2)).to_contain_text("Over the limit", timeout=PAGE_TIMEOUT)
    rows = _rule_texts(page)

    # Try an input: rule 2 matches.
    page.fill("#value-0", "5000")
    page.fill("#value-1", "low")
    page.get_by_test_id("evaluate-submit").click()
    expect(page.get_by_test_id("evaluate-message")).to_have_text("Rule 2 matched.", timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("evaluate-output")).to_contain_text("Approval: buyer approves")
    expect(page.locator('[data-rule="2"]')).to_have_attribute("aria-current", "true")
    expect(page.locator('[data-rule="1"]')).to_have_attribute("aria-current", "false")
    page.get_by_test_id("evaluate-card").scroll_into_view_if_needed()
    _shot(page, "decision-evaluated", 1440)

    # Link it to the process; the decision page lists the process.
    expect(page.get_by_test_id("process-empty")).to_be_visible()
    page.select_option("#process-id", label="Onboard supplier")
    page.get_by_test_id("process-link-submit").click()
    expect(page.get_by_test_id("process-row")).to_have_count(1, timeout=PAGE_TIMEOUT)

    # From the decision to the process: the process page lists the decision and links back.
    page.get_by_test_id("process-link").click()
    page.wait_for_url(re.compile(r"/architecture/business/BusinessProcess/\d+"), timeout=PAGE_TIMEOUT)
    expect(page.get_by_role("heading", name="Onboard supplier", level=1)).to_be_visible(timeout=PAGE_TIMEOUT)
    applied = page.get_by_test_id("decisions-applied")
    expect(applied).to_contain_text("Approve supplier")
    expect(applied).to_contain_text("First hit policy, 3 rules")
    applied.scroll_into_view_if_needed()
    _shot(page, "process-decisions-applied", 1440)
    # The decision is also an element of the model; its own page leads to the table.
    process_url = page.url
    page.get_by_test_id("answer-line").get_by_role("link", name="Approve supplier").click()
    expect(page.get_by_test_id("open-decision-table")).to_be_visible(timeout=PAGE_TIMEOUT)
    page.get_by_test_id("open-decision-table").click()
    page.wait_for_url(decision_url, timeout=PAGE_TIMEOUT)
    page.goto(process_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.get_by_test_id("decision-applied-link").click()
    page.wait_for_url(decision_url, timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("dmn-rule")).to_have_count(3)
    expect(page.get_by_test_id("process-row")).to_contain_text("Onboard supplier")

    # The list now shows it with its process.
    page.goto(live_server + LIST, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    row = page.get_by_test_id("decision-row").filter(has_text="Approve supplier")
    expect(row).to_contain_text("First")
    expect(row).to_contain_text("Onboard supplier")
    _shot(page, "decisions-list", 1440)

    # Persisted: reload the decision and the rules are all there.
    page.goto(decision_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert len(_rule_texts(page)) == 3

    # Export the file Camunda Modeler opens; it validates against the DMN 1.5 schema.
    with page.expect_download() as download:
        page.get_by_test_id("decision-export").click()
    saved = tmp_path / "approve-supplier.dmn"
    download.value.save_as(str(saved))
    document = etree.parse(str(saved))
    etree.XMLSchema(etree.parse(str(XSD))).assertValid(document)
    assert document.getroot().find(".//{*}decisionTable").get("hitPolicy") == "FIRST"
    assert len(document.getroot().findall(".//{*}rule")) == 3

    # The phone: the table keeps its shape and scrolls sideways inside its own box.
    narrow = _context(browser, 390)
    phone = narrow.new_page()
    sign_in(phone, live_server, email_a)
    phone.goto(decision_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(phone.get_by_test_id("dmn-rule")).to_have_count(3, timeout=PAGE_TIMEOUT)
    sizes = phone.evaluate(
        """() => { const box = document.querySelector('[data-testid="decision-scroll"]');
                   return {box: box.scrollWidth, shown: box.clientWidth, page: document.documentElement.scrollWidth,
                           view: window.innerWidth, cols: document.querySelectorAll('[data-testid="dmn-rule"]:first-child td').length}; }"""
    )
    assert sizes["box"] > sizes["shown"], "the table should scroll sideways on a phone: %s" % sizes
    assert sizes["page"] <= sizes["view"] + 1, "the page itself must not scroll sideways: %s" % sizes
    assert sizes["cols"] >= 5, "the table stacked instead of keeping its columns: %s" % sizes
    _shot(phone, "decision-table", 390)
    _shot_open(phone, "rule-editor", 390, lambda: phone.get_by_test_id("rule-add").click())
    phone.get_by_test_id("rule-add").click()
    expect(phone.locator("#rule-dialog")).to_be_visible()
    phone.wait_for_timeout(500)
    box = phone.locator("#rule-dialog").bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 391, "the rule dialog overflows a phone: %s" % box
    narrow.close()

    # Another organisation: it sees nothing of the first, and cannot open it.
    other = _context(browser)
    page_b = other.new_page()
    sign_in(page_b, live_server, email_b)
    page_b.goto(live_server + LIST, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page_b.get_by_text("No decision tables yet.")).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(page_b.get_by_test_id("decision-row")).to_have_count(0)
    assert page_b.goto(decision_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT).status == 404

    # It imports the exported file on the model import screen and gets the same rules.
    page_b.goto(live_server + "/architecture/import/oef", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page_b.set_input_files("#oef_file", str(saved))
    page_b.get_by_test_id("btn-preview-import").click()
    expect(page_b.get_by_test_id("preview-new")).to_have_text("1", timeout=PAGE_TIMEOUT)
    expect(page_b.get_by_test_id("preview-decision-reason")).to_contain_text("3 rules")
    page_b.get_by_test_id("btn-import-model").click()
    expect(page_b.get_by_test_id("result-elements-created")).to_have_text("1", timeout=PAGE_TIMEOUT)
    _shot(page_b, "import-dmn-result", 1440)
    page_b.get_by_test_id("open-decision-tables").click()
    page_b.get_by_test_id("decision-link").filter(has_text="Approve supplier").click()
    expect(page_b.get_by_test_id("dmn-rule")).to_have_count(3, timeout=PAGE_TIMEOUT)
    assert _rule_texts(page_b) == rows
    expect(page_b.get_by_test_id("dmn-hit-policy-letter")).to_have_text("F")
    page_b.fill("#value-0", "5000")
    page_b.fill("#value-1", "low")
    page_b.get_by_test_id("evaluate-submit").click()
    expect(page_b.get_by_test_id("evaluate-message")).to_have_text("Rule 2 matched.", timeout=PAGE_TIMEOUT)
    # Its link list is its own: the process of the first organisation is not there.
    expect(page_b.get_by_test_id("process-none-to-link")).to_be_visible()

    # The first organisation still has exactly one decision.
    page.goto(live_server + LIST, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("decision-row")).to_have_count(1)
    other.close()
    context.close()
