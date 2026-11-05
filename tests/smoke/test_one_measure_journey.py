"""One owned count, one relationship count, one date, and a citation that lands on a phone.

The person is a startup CTO whose GitHub model has hundreds of relationships and applications of three
kinds: owned by someone, owned only by a code-owner file, and owned by nobody. They read the figures on
the dashboard, the Applications list, the application page, the answer pages and the chat card, and then
follow a citation from the chat on a phone. Written from that goal and asserting the outcome:
every screen states the same number for the same measure, every figure says what it counts, and a
citation opened at 390 and 360 wide leaves the page where it was (nothing scrolled sideways, the menu
button in view).
"""

import re
from datetime import date

import pytest
from playwright.sync_api import expect

from tests.smoke.chat_ask import ask, sign_in
from tests.smoke.conftest import PAGE_TIMEOUT
from tests.smoke.seed_estate import HUB_RECORDED, seed_calibration_ledger, seed_hub_service, seed_long_origin
from tests.smoke.test_citation_and_owner_agreement_journey import (  # noqa: F401
    test_1_connect_and_approve,
)
from tests.smoke.test_connect_github_to_cited_answer_journey import (  # noqa: F401
    TOKEN,
    admin,
    github_server,
    world,
)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

HUB_RECORDED_TEXT = "4 Mar 2026, 09:05 UTC"
ANY_DATE = re.compile(r"\b\d{1,2} (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{4}(?:, \d{2}:\d{2} UTC)?")
PADDED_OR_NUMERIC = re.compile(r"\b0\d (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{4}|\b\d{4}-\d{2}-\d{2}\b")


@pytest.fixture(scope="module")
def estate(admin, world, app):
    """The approved GitHub model, plus the large application, the hub, and one application a person owns."""
    from app import db
    from app.models.application_portfolio import ApplicationComponent

    org_id = world["one"]["org_id"]
    ledger = seed_calibration_ledger(app, org_id)
    hub = seed_hub_service(app, org_id, records=30)
    seed_long_origin(app, org_id)
    with app.app_context():
        owned = ApplicationComponent.query.filter(
            ApplicationComponent.organization_id == org_id, ApplicationComponent.name == "Customer Portal"
        ).one()
        owned.legacy_business_owner = "Pat Ownerson"
        hub_app = db.session.get(ApplicationComponent, hub["application_id"])
        hub_app.go_live_date = date(2025, 3, 4)
        db.session.commit()
        db.session.remove()
    return {"ledger": ledger, "hub": hub, "org_id": org_id}


def _text(page):
    return re.sub(r"\s+", " ", page.locator("#main-content").inner_text())


def test_2_the_most_connected_application_has_the_same_count_on_its_own_page(admin, world, estate):
    base = world["base"]
    admin.goto(base + "/dashboard/overview", wait_until="domcontentloaded")
    dash = _text(admin)
    top = re.search(r"Relationships (.+?) is the most connected, with (\d+) relationships?\.", dash)
    assert top, dash[:600]
    assert top.group(1).strip() == "Hub Service", "the most connected is %r" % top.group(1)
    printed = int(top.group(2))

    admin.goto(base + "/applications/%d" % estate["hub"]["application_id"], wait_until="domcontentloaded")
    page = admin.locator("[data-relationship-count]")
    expect(page.first).to_be_visible()
    on_page = int(page.first.inner_text().strip())
    tab = admin.locator("[role=tab], [data-tab]").filter(has_text=re.compile(r"^\s*Architecture")).first
    tab_count = int(re.search(r"(\d+)\s*$", re.sub(r"\s+", " ", tab.inner_text()).strip()).group(1))
    assert printed == estate["hub"]["records"] == on_page == tab_count, (
        "dashboard says %s, the application page says %s, its Architecture tab says %s, the records are %s"
        % (printed, on_page, tab_count, estate["hub"]["records"])
    )
    header = _text(admin)
    assert re.search(r"%d relationships" % on_page, header), "the figure does not say what it counts: %s" % header[:300]
    assert re.search(r"\b1 element", header), "the page does not say how many elements the relationships reach: %s" % header[:400]


def _dashboard_unowned(page, base):
    page.goto(base + "/dashboard/overview", wait_until="domcontentloaded")
    text = _text(page)
    sentence = re.search(r"(\d+)\s+Applications with no owner", text) or re.search(r"Applications with no owner\s*(\d+)", text)
    assert sentence, text[:700]
    return int(sentence.group(1))


def test_3_the_owned_and_no_owner_counts_are_identical_on_every_screen(admin, world, estate, app):
    from app import db
    from app.models.application_portfolio import ApplicationComponent
    from app.modules.ai_chat.tools.executor import ToolExecutor
    from app.models.user import User
    from app.services.element_ownership_service import application_is_owned

    base = world["base"]
    with app.app_context():
        apps = ApplicationComponent.query.filter_by(organization_id=estate["org_id"]).all()
        names = {a.id: a.name for a in apps}
        truth = {a.id for a in apps if not application_is_owned(a)}
        text_owned = {a.id for a in apps if (a.legacy_business_owner or a.legacy_application_owner or '').strip()}
        active = [a for a in apps if (a.lifecycle_status or "").lower() != "5. decommissioned"]
        assert len(truth) >= 3 and len(truth) < len(apps), "the organisation must hold owned and unowned applications"
        user = User.query.filter_by(email=world["one"]["emails"]["solution_architect"]).one()
        fields = ToolExecutor(user.id)._application_owner_fields(apps)
        chat_tool = {i for i, f in fields.items() if not f["has_owner"]}
        db.session.remove()
    assert chat_tool == truth, "the chat tool counts %s, the one rule counts %s" % (sorted(names[i] for i in chat_tool), sorted(names[i] for i in truth))
    # The application page's completeness line asks for an owner record (pinned by test_application_owner_writer), so
    # it may also miss an owner for an application owned by legacy text alone; it must never call an
    # application owned that the one rule calls unowned, a code-owner-only application above all.
    sheet = set()
    for app_id in names:
        admin.goto(base + "/applications/%d" % app_id, wait_until="domcontentloaded")
        missing = re.search(r"Missing: ([^.]*?)\. Fill", _text(admin))
        if missing and "Owner" in missing.group(1).split(", "):
            sheet.add(app_id)
    assert truth <= sheet, "the application pages call %s owned, the one rule says they are not" % sorted(names[i] for i in truth - sheet)
    assert sheet - truth <= text_owned, "the application pages miss an owner for %s, which the one rule calls owned" % sorted(names[i] for i in sheet - truth - text_owned)

    dashboard = _dashboard_unowned(admin, base)
    admin.goto(base + "/applications/?per_page=100", wait_until="domcontentloaded")
    page_text = _text(admin)
    headline = re.search(r"(\d+) of (\d+) have no owner", page_text)
    assert headline, page_text[:600]
    listed = int(headline.group(1))
    admin.get_by_test_id("dq-no-owner").click()
    admin.wait_for_load_state("domcontentloaded")
    table = admin.get_by_role("region", name="Applications table")
    behind = table.get_by_role("row", name=re.compile(r"^Select (?!all)")).all()
    rows = len(behind)
    row_names = [r.inner_text().split("\n")[1:3] for r in behind]

    answer = ask(admin, base, "Which applications have no owner")
    answer_text = re.sub(r"\s+", " ", answer.inner_text())
    ask_count = re.search(r"(\d+) in your records", answer_text)
    assert ask_count, answer_text[:500]
    asked = int(ask_count.group(1))

    figures = {
        "the one rule": len(truth),
        "dashboard": dashboard,
        "Applications list headline": listed,
        "rows behind the headline link": rows,
        "Ask answer": asked,
    }
    assert len(set(figures.values())) == 1, "the no-owner count differs between screens: %s; rows: %s; unowned: %s" % (figures, row_names, sorted(names[i] for i in truth))
    assert len(active) - len(truth) >= 1


def test_4_a_citation_opened_on_a_phone_leaves_the_page_where_it_was(admin, world, estate):
    base = world["base"]
    try:
        for question in ("What breaks if Customer Portal fails", "What breaks if Calibration Ledger fails"):
            admin.set_viewport_size({"width": 1440, "height": 900})
            answer = ask(admin, base, question)
            links = answer.locator("a[data-relationship-link]")
            hrefs = [links.nth(i).get_attribute("href") for i in range(links.count())]
            assert hrefs, question
            for width, height in ((390, 844), (360, 780)):
                admin.set_viewport_size({"width": width, "height": height})
                for href in hrefs:
                    admin.goto(base + href, wait_until="domcontentloaded")
                    rel_id = re.search(r"relationship-(\d+)$", href).group(1)
                    expect(admin.locator("#relationship-%s" % rel_id)).to_be_visible()
                    admin.wait_for_timeout(400)
                    state = admin.evaluate(
                        """() => {
                          const out = [];
                          for (const el of document.querySelectorAll('*')) {
                            if (el.scrollLeft > 0) out.push(el.tagName + '.' + String(el.className).slice(0, 40) + ' scrollLeft=' + el.scrollLeft);
                          }
                          const btn = document.querySelector('[data-sidebar-trigger], button[aria-label*="idebar" i], button[aria-label*="enu" i]');
                          const r = btn ? btn.getBoundingClientRect() : null;
                          const main = document.querySelector('.cn-sidebar-inset');
                          return {scrolled: out, button: r ? [Math.round(r.left), Math.round(r.right)] : null,
                                  main: main ? main.scrollWidth - main.clientWidth : null};
                        }"""
                    )
                    assert not state["scrolled"], "at %d wide opening %s scrolled sideways: %s" % (width, href, state["scrolled"])
                    assert state["button"] and state["button"][0] >= 0 and state["button"][1] <= width, (
                        "at %d wide the menu button is off screen: %s" % (width, state["button"])
                    )
                    assert state["main"] is not None and state["main"] <= 0, (
                        "at %d wide the page container is %dpx wider than the screen on %s" % (width, state["main"], href)
                    )
    finally:
        admin.set_viewport_size({"width": 1440, "height": 900})


def test_5_the_application_page_figure_equals_the_architecture_tab(admin, world, estate):
    base = world["base"]
    for app_id, label in ((estate["ledger"]["application_id"], "Calibration Ledger"), (estate["hub"]["application_id"], "Hub Service")):
        admin.goto(base + "/applications/%d" % app_id, wait_until="domcontentloaded")
        figure = int(admin.locator("[data-relationship-count]").first.inner_text().strip())
        tab = admin.locator("[role=tab], [data-tab]").filter(has_text=re.compile(r"^\s*Architecture")).first
        tab_count = int(re.search(r"(\d+)\s*$", re.sub(r"\s+", " ", tab.inner_text()).strip()).group(1))
        deps = admin.locator("[role=tab], [data-tab]").filter(has_text=re.compile(r"^\s*Dependencies")).first
        deps_count = int(re.search(r"(\d+)\s*$", re.sub(r"\s+", " ", deps.inner_text()).strip()).group(1))
        assert figure == tab_count == deps_count, "%s: page says %s relationships, Architecture %s, Dependencies %s" % (label, figure, tab_count, deps_count)
        text = _text(admin)
        assert re.search(r"%d relationships? to \d+ elements?|%d relationships? · \d+ elements?" % (figure, figure), text), (
            "%s: the figure does not name both what it counts and the elements it reaches: %s" % (label, text[:300])
        )
        # the elements figure is the one the chat card and the saved answer call "direct"
        elements = int(admin.locator("[data-direct-count]").first.inner_text().strip())
        answer = ask(admin, base, "What breaks if %s fails" % label)
        chat = answer.locator("[data-testid='answer-direct-count'] [data-direct-count]")
        assert int(chat.first.inner_text()) == elements, "%s: the page reaches %s elements, the chat card says %s direct" % (label, elements, chat.first.inner_text())


def test_6_the_same_recorded_date_reads_the_same_on_the_answer_the_chat_card_and_the_application(admin, world, estate):
    base = world["base"]
    answer = ask(admin, base, "What breaks if Hub Service fails")
    card_text = re.sub(r"\s+", " ", answer.inner_text())
    assert "Recorded " + HUB_RECORDED_TEXT in card_text, "the chat card does not read %r: %s" % (HUB_RECORDED_TEXT, card_text[:600])
    # the citation the card offers lands on a page that reads the recorded moment the same way
    href = answer.locator("a[data-relationship-link]").first.get_attribute("href")
    admin.goto(base + href, wait_until="domcontentloaded")
    landed = _text(admin)
    assert "Recorded " + HUB_RECORDED_TEXT in landed, "the cited row does not read %r: %s" % (HUB_RECORDED_TEXT, landed[:500])
    # the saved answer and the list of answers state the moment it was worked out in the same words, with the zone
    admin.goto(base + "/intelligence/answers/element/%d" % estate["hub"]["element_id"], wait_until="domcontentloaded")
    saved = _text(admin)
    computed = admin.locator("[data-computed-at]").inner_text().strip()
    assert re.fullmatch(r"\d{1,2} \w{3} \d{4}, \d{2}:\d{2} UTC", computed), "the answer's time is %r" % computed
    admin.goto(base + "/intelligence/answers", wait_until="domcontentloaded")
    listed = _text(admin)
    assert computed in listed, "the answers list does not read %r: %s" % (computed, listed[:400])
    # the application page reads its dates the same way: unpadded day, month in words, no raw numbers
    admin.goto(base + "/applications/%d" % estate["hub"]["application_id"], wait_until="domcontentloaded")
    on_app = _text(admin)
    assert "4 Mar 2025" in on_app, "the go-live date is not read as the one formatter reads it: %s" % on_app[:600]
    pages = {"chat card": card_text, "cited row": landed, "answer page": saved, "answers list": listed, "application page": on_app}
    for name, text in pages.items():
        assert not PADDED_OR_NUMERIC.search(text), "%s shows a padded or numeric date: %s" % (name, PADDED_OR_NUMERIC.search(text).group(0))
        for m in re.finditer(r"\b\d{1,2} (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{4}, \d{2}:\d{2}(?! UTC)", text):
            raise AssertionError("%s shows a time with no zone: %s" % (name, m.group(0)))


def test_7_a_second_organisation_sees_none_of_it(browser, world, estate):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        sign_in(page, world["base"], world["two"]["emails"]["solution_architect"])
        ids = estate["hub"]["ids"][:3] + estate["ledger"]["cited_ids"][:3]
        urls = ["/architecture/relationships?relationship=%d#relationship-%d" % (i, i) for i in ids]
        urls += ["/applications/?per_page=100", "/dashboard/overview", "/intelligence/answers"]
        for url in urls:
            page.goto(world["base"] + url, wait_until="domcontentloaded")
            text = page.locator("body").inner_text()
            for secret in ("Hub Service", "Hub Peer", "Calibration Ledger", "Ledger Reports", "acme/hub.yaml", "Pat Ownerson", "acme/web-team", "Filler"):
                assert secret not in text, "%s shows %r of another organisation" % (url, secret)
            assert not page.locator("tr[id^='relationship-']").count(), "%s lists another organisation's relationships" % url
    finally:
        context.close()
