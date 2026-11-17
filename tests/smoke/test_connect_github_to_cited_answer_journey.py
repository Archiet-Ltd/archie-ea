"""Connect GitHub to a cited answer, in a real browser (Job 0 of the product).

The person is the administrator of a brand-new organisation. They open Connect GitHub from the
connectors page, paste a read-only token, watch the read finish, see what became of each finding
and why (nobody approves anything: the server checks and settles them), find the applications in
the inventory with their parts and owner, ask what breaks if
Payments Service fails, and see each answer name the repository and file it rests on. Another
organisation sees none of it, a Viewer is refused the connect page, and the token is in no page
and no response.

GitHub is never called. The recorded organisation of the service tests
(tests/test_github_builds_the_model.py, fixture tests/fixtures/github_acme_org.json) is served to
the server under test by the same github fixture and extend_with_job0 those tests use: a
sitecustomize on the server's import path builds them in the server process the first time it is
asked for a GitHub address. The token below is a plain placeholder.
"""

import os
import re
import uuid

import pytest
from playwright.sync_api import expect

from scripts.capture.github_fixture import SITECUSTOMIZE as _SITECUSTOMIZE
from tests.smoke.chat_ask import ask, role_of
from tests.smoke.conftest import PAGE_TIMEOUT, boot_live_server
from tests.smoke.fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

TOKEN = "test-token-not-real"
SKIP_WELCOME = "localStorage.setItem('archie_onboarding_ts', Date.now().toString());"
OWNER_EMAIL = "owner.one@example.test"
SHOTS = os.environ.get("JOB0_JOURNEY_SHOTS")


@pytest.fixture(scope="module")
def github_server(request, app, tmp_path_factory):
    """A second server whose GitHub is the recorded organisation."""
    folder = tmp_path_factory.mktemp("github_peer")
    (folder / "sitecustomize.py").write_text(_SITECUSTOMIZE)
    path = os.pathsep.join(p for p in (str(folder), os.environ.get("PYTHONPATH", "")) if p)
    return boot_live_server(
        request, None, app, extra_env={"PYTHONPATH": path, "ALLOW_EXTERNAL_API_CALLS": "true"}
    )


@pytest.fixture(scope="module")
def world(github_server, app):
    """Two fresh organisations. In the first: its administrator, a Viewer, and the person the
    code-owner file of Payments Service names."""
    from app import db
    from app.models.user import Role, User

    one = create_fresh_org("solution_architect", org_admin=True, extra_roles=("non_technical_owner",))
    two = create_fresh_org("solution_architect", org_admin=True)
    with app.app_context():
        # The code-owner file names one address, and an address belongs to one person on the whole
        # server: a holder left by an earlier run gives it up.
        for old in User.query.filter_by(email=OWNER_EMAIL).all():
            old.email = "owner.one.%s@example.test" % uuid.uuid4().hex[:8]
        db.session.flush()
        # The Community plan admits three people: the administrator, the Viewer and Payments Service's owner.
        db.session.add(User(first_name="Olive", last_name="One", email=OWNER_EMAIL, organization_id=one["org_id"], confirmed=True))
        viewer = User.query.filter_by(email=one["emails"]["non_technical_owner"]).one()
        viewer.role = Role.query.filter_by(name="Viewer").one()
        db.session.commit()
        db.session.remove()
    seen = {"html": [], "bodies": [], "answer_url": None}
    yield {"base": str(github_server), "one": one, "two": two, "seen": seen}
    remove_organisations(app, [one["org_id"], two["org_id"]])


def remove_organisations(app, org_ids):
    """Leave nothing behind: every row of the two organisations this journey made, and the
    organisations themselves, and nothing that belongs to anyone else. A table that still
    holds a row another table points at is tried again once the others are done."""
    from sqlalchemy import text

    from app import db

    with app.app_context():
        # A row another connection still holds (a server process left by an earlier killed run, a
        # transaction left open) must fail this clean-up in seconds, with the reason, not stall the
        # run for as long as the holder lives.
        db.session.execute(text("SET lock_timeout = '30s'"))
        db.session.commit()
        # Tables that hold rows of an organisation, by the column that says whose they are.
        tables = [
            row[0]
            for row in db.session.execute(
                text(
                    "SELECT c.table_name FROM information_schema.columns c "
                    "JOIN information_schema.tables t USING (table_schema, table_name) "
                    "WHERE c.table_schema = 'public' AND t.table_type = 'BASE TABLE' "
                    "AND c.column_name = 'organization_id' AND c.table_name <> 'organizations'"
                )
            )
        ]
        db.session.rollback()
        ids = {"ids": list(org_ids)}
        # Rows that point at an organisation's rows through no organisation column of their own.
        # The server may still be writing one when the clean-up starts (an answer being recorded, a
        # background pass finishing), so these run again before the organisations go.
        keyed = (
            "DELETE FROM sync_logs WHERE connector_id IN (SELECT id FROM connector_configs WHERE organization_id = ANY(:ids))",
            # The chat messages carry no organisation column, so the threads (which do) cannot go
            # until the messages that point at them have.
            "DELETE FROM conversation_messages WHERE thread_id IN "
            "(SELECT id FROM conversation_threads WHERE organization_id = ANY(:ids))",
            "DELETE FROM ai_chat_approval_audit_log WHERE approval_id IN "
            "(SELECT id FROM ai_chat_crud_approvals WHERE organization_id = ANY(:ids))",
            # An import maps applications to capabilities; the mapping carries no organisation column.
            "DELETE FROM unified_application_capability_mapping WHERE application_component_id IN "
            "(SELECT id FROM application_components WHERE organization_id = ANY(:ids))",
            "DELETE FROM application_capability_coverage WHERE application_component_id IN "
            "(SELECT id FROM application_components WHERE organization_id = ANY(:ids))",
            "DELETE FROM saved_diagram_elements WHERE diagram_id IN "
            "(SELECT id FROM saved_diagrams WHERE organization_id = ANY(:ids))",
            # Mapping a capability is audited against the person who did it, with no organisation.
            "DELETE FROM soc2_audit_log WHERE user_id IN (SELECT id FROM users WHERE organization_id = ANY(:ids))",
        )

        def clear_keyed_rows():
            for statement in keyed:
                with db.session.begin_nested():
                    db.session.execute(text(statement), ids)

        clear_keyed_rows()
        # Append-only tables refuse every delete; the switch that stops their triggers firing
        # lasts for this one transaction (the same way tests/_outbox_cleanup.py does it).
        db.session.execute(text("SET LOCAL session_replication_role = replica"))
        for name in ("transformation_outbox_events",):
            db.session.execute(text('DELETE FROM "%s" WHERE organization_id = ANY(:ids)' % name), ids)
        db.session.execute(text("SET LOCAL session_replication_role = DEFAULT"))
        db.session.commit()
        # The evidence tables are append-only and this journey never writes to them.
        evidence = ("arb_submission_evidence_snapshots", "workbench_artifact_evidence")
        for name in evidence:
            assert not db.session.execute(
                text('SELECT count(*) FROM "%s" WHERE organization_id = ANY(:ids)' % name), ids
            ).scalar_one(), "the journey wrote evidence rows it cannot remove"
        pending = [name for name in tables if name not in evidence]
        for _ in range(8):
            clear_keyed_rows()
            still = []
            for name in pending:
                try:
                    with db.session.begin_nested():
                        db.session.execute(
                            text('DELETE FROM "%s" WHERE organization_id = ANY(:ids)' % name.replace('"', "")),
                            {"ids": list(org_ids)},
                        )
                except Exception:
                    still.append(name)
            db.session.commit()
            if not still or still == pending:
                pending = still
                break
            pending = still
        db.session.execute(text("DELETE FROM organizations WHERE id = ANY(:ids)"), ids)
        db.session.commit()
        left = db.session.execute(text("SELECT count(*) FROM organizations WHERE id = ANY(:ids)"), ids).scalar_one()
        db.session.execute(text("RESET lock_timeout"))
        db.session.commit()
        db.session.remove()
        assert left == 0 and not pending, "the journey left rows behind in: %s" % pending


@pytest.fixture(scope="module")
def admin(browser, world):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.add_init_script(SKIP_WELCOME)
    context.set_default_timeout(PAGE_TIMEOUT)
    context.set_default_navigation_timeout(PAGE_TIMEOUT)
    page = context.new_page()

    def keep(response):
        try:
            if response.url.startswith(world["base"]):
                world["seen"]["bodies"].append((response.url, response.body().decode("utf-8", "replace")))
        except Exception:
            pass

    page.on("response", keep)
    sign_in(page, world["base"], world["one"]["emails"]["solution_architect"])
    yield page
    context.close()


def shoot(page, name, reload=True):
    """The screen at the two widths people use, kept for the person who reviews it."""
    if not SHOTS:
        return
    os.makedirs(SHOTS, exist_ok=True)
    for width, height in ((1440, 900), (390, 844)):
        page.set_viewport_size({"width": width, "height": height})
        if reload:  # the page as it opens at this width, not as it was resized
            page.reload(wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle")
        page.screenshot(path=os.path.join(SHOTS, "%s-%d.png" % (name, width)), full_page=True)
    page.set_viewport_size({"width": 1440, "height": 900})


def remember(world, page):
    world["seen"]["html"].append((page.url, page.content()))


# --------------------------------------------------------------------------------------------- #
# The journey, in order                                                                          #
# --------------------------------------------------------------------------------------------- #


def test_1_connect_github_is_a_link_on_the_connectors_page_and_lands_on_content(admin, world):
    admin.goto(world["base"] + "/admin/connectors", wait_until="domcontentloaded")
    link = admin.get_by_test_id("github-connect-link").get_by_role("link", name="Connect GitHub")
    expect(link).to_be_visible()
    link.click()
    admin.wait_for_url("**/admin/connectors/github")
    expect(admin.get_by_role("heading", name="Connect GitHub")).to_be_visible()
    form = admin.get_by_test_id("github-connect-form")
    expect(form.get_by_label("GitHub organisation")).to_be_visible()
    expect(form.get_by_label("Read-only token")).to_be_visible()
    expect(form.get_by_role("button", name="Connect and read my repositories")).to_be_visible()
    expect(admin.get_by_text("Paste a read-only token.", exact=False)).to_be_visible()
    remember(world, admin)
    shoot(admin, "1-connect")


def test_2_the_administrator_pastes_a_token_and_submits(admin, world):
    form = admin.get_by_test_id("github-connect-form")
    form.get_by_label("GitHub organisation").fill("acme")
    form.get_by_label("Read-only token").fill(TOKEN)
    form.get_by_role("button", name="Connect and read my repositories").click()
    expect(admin.locator("#main-content").get_by_text("Connected. Reading your repositories now", exact=False)).to_be_visible()
    expect(admin.get_by_test_id("github-progress")).to_be_visible()
    remember(world, admin)


def test_3_the_status_moves_to_done_with_counts(admin, world):
    line = admin.locator("#github-progress-line")
    expect(line).to_have_text(re.compile(r"Read 16 repositories\. 23 findings were checked: 23 added to your model\."), timeout=PAGE_TIMEOUT)
    # The read settled every finding on the server: the applications are in the model already, nothing waits.
    expect(admin.locator("#github-count-applications")).to_have_text("8")
    world["seen"]["checked"] = 23
    remember(world, admin)
    shoot(admin, "3-status")


def test_4_the_record_shows_what_became_of_each_finding_with_a_reason_and_a_source(admin, world, app):
    admin.get_by_role("link", name="See what was checked").click()
    admin.wait_for_url("**/ai-chat/approvals/inbox**")
    # Nothing waits for a person: the page is the record of what was settled, with no Approve or Reject.
    expect(admin.get_by_role("button", name=re.compile(r"Approve|Reject"))).to_have_count(0)
    expect(admin.get_by_role("link", name=re.compile(r"Approve|Reject"))).to_have_count(0)
    expect(admin.get_by_test_id("approval-waiting")).to_have_count(0)
    checked = world["seen"]["checked"]
    rows = admin.get_by_test_id("approval-record-row")
    expect(rows).to_have_count(checked)
    for index in range(checked):
        row = rows.nth(index)
        outcome = row.locator("[data-testid^='approval-outcome-']").inner_text().strip()
        assert outcome in ("Applied", "Linked"), outcome
        assert len(row.locator("[data-testid^='approval-reason-']").inner_text().strip()) > 20
        assert row.locator("[data-testid^='approval-verdict-']").inner_text().strip()
        href = row.locator("[data-testid^='approval-source-link-']").get_attribute("href")
        assert re.match(r"https://github\.com/acme/[A-Za-z0-9._-]+(/blob/[0-9a-f]{40}/.+)?$", href), href
    # What the record and the model hold as the owner of three things the answer names later: the
    # code-owner facts the read recorded on the elements.
    from app import db
    from app.models.archimate_core import ArchiMateElement

    with app.app_context():
        shown = {}
        for name in ("Billing Api", "Customer Portal", "Payments Worker"):
            element = ArchiMateElement.query.filter_by(organization_id=world["one"]["org_id"], name=name).first()
            props = (element.acm_properties or {}) if element else {}
            owners = (props.get("github_code_owners") or {}).get("value")
            shown[name] = ", ".join(owners) if owners else (props.get("github_owning_team") or {}).get("value") or "No owner recorded"
        db.session.remove()
    world["review_owner"] = shown
    remember(world, admin)
    shoot(admin, "4-record")

    # On a phone the record still reads, with no sideways scroll.
    admin.set_viewport_size({"width": 390, "height": 844})
    try:
        admin.reload(wait_until="domcontentloaded")
        overflow = admin.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert overflow <= 0, "the record page scrolls sideways by %spx at 390 wide" % overflow
        expect(admin.get_by_test_id("approval-record-row").first).to_be_visible()
    finally:
        admin.set_viewport_size({"width": 1440, "height": 900})


def test_5_nothing_waits_and_the_applications_are_already_in_the_inventory(admin, world):
    admin.goto(world["base"] + "/admin/connectors/github", wait_until="domcontentloaded")
    expect(admin.get_by_role("button", name=re.compile(r"Approve all"))).to_have_count(0)
    expect(admin.locator("#github-progress-line")).to_contain_text("findings were checked")
    admin.goto(world["base"] + "/applications/", wait_until="domcontentloaded")
    table = admin.get_by_role("region", name="Applications table")
    for name in ("Payments Service", "Customer Portal", "Billing Api", "Inventory Service", "Orders Worker", "Mobile App", "Auth", "Gateway"):
        expect(table.get_by_role("row", name=re.compile(r"^Select %s " % name))).to_have_count(1)
    remember(world, admin)


def test_6_the_applications_are_listed_and_payments_service_shows_its_parts_and_owner(admin, world):
    admin.goto(world["base"] + "/applications/", wait_until="domcontentloaded")
    table = admin.get_by_role("region", name="Applications table")
    for name in ("Payments Service", "Customer Portal", "Billing Api", "Inventory Service", "Orders Worker", "Mobile App", "Auth", "Gateway"):
        expect(table.get_by_role("row", name=re.compile(r"^Select %s " % name))).to_have_count(1)
    rows = table.locator("tbody tr[data-testid^='app-row-']")  # the footer row of each application is not an application
    assert rows.count() == 8
    # Cells are found by the column header a person reads, not by position: the table gained and
    # lost columns and a positional index silently pointed at the wrong one.
    headers = [h.strip().lower() for h in table.locator("thead th").all_inner_texts()]
    for needed in ("type", "archimate", "capability"):
        assert needed in headers, "the applications table has no %r column: %r" % (needed, headers)
    kind, linked, capability = (headers.index(n) for n in ("type", "archimate", "capability"))
    for row in rows.all():
        # every application shows what it is and why anything unknown is unknown: no bare dash or dot
        cells = row.locator("td")
        assert cells.nth(kind).inner_text().strip() == "Application", row.inner_text()
        assert cells.nth(linked).inner_text().strip() in ("Linked", "Not linked"), "the AM dot has no stated meaning: " + row.inner_text()
        assert cells.nth(capability).inner_text().strip() not in ("", "—", "-", "Not mapped"), "the capability says nothing: " + row.inner_text()
        assert cells.nth(capability).inner_text().strip() == "No capability mapped yet: map it", "the capability cell names no next step: " + row.inner_text()
        for cell in (cells.nth(kind), cells.nth(capability)):
            assert cell.inner_text().strip() not in ("—", "-", "–")
    expect(table.get_by_role("button", name="No capability mapped yet: map it").first).to_be_visible()
    shoot(admin, "6-applications")
    # The fact sheet is in the row's menu (the Record ring links to its missing fields): open the menu, as a person does.
    table.get_by_role("row", name=re.compile(r"^Select Payments Service ")).get_by_role("button", name="Actions for Payments Service").click()
    admin.get_by_role("menuitem", name=re.compile("Fact sheet")).click()
    admin.wait_for_load_state("domcontentloaded")
    expect(admin.get_by_role("heading", name="Payments Service").first).to_be_visible()
    expect(admin.get_by_text("Payments Worker").first).to_be_visible()
    expect(admin.get_by_text("Olive One").first).to_be_visible()
    remember(world, admin)
    shoot(admin, "6-application")


CAPABILITY = "Take card payments"


def test_6a_an_editor_maps_a_capability_from_the_cell_and_it_stays_after_a_reload(admin, world, app):
    from app import db
    from app.models.business_capabilities import BusinessCapability

    with app.app_context():
        capability = BusinessCapability(
            name=CAPABILITY, code="JRN-" + uuid.uuid4().hex[:8], organization_id=world["one"]["org_id"], level=1
        )
        db.session.add(capability)
        db.session.commit()
        db.session.remove()
    admin.goto(world["base"] + "/applications/", wait_until="domcontentloaded")
    table = admin.get_by_role("region", name="Applications table")
    row = table.get_by_role("row", name=re.compile(r"^Select Payments Service "))
    row.get_by_role("button", name="No capability mapped yet: map it").click()
    dialog = admin.locator("#unified-mapping-modal")
    expect(dialog).to_be_visible()
    # The dialog is about this application's capabilities, not about applications of a capability.
    expect(dialog.locator("#unified-modal-target-name")).to_have_text("Payments Service")
    expect(dialog).to_contain_text("Map Capabilities to:")
    dialog.locator("#unified-targets-list").get_by_text(CAPABILITY).first.click()
    expect(dialog.get_by_role("button", name="Save mappings").locator("visible=true")).to_contain_text("1 selected")
    dialog.get_by_role("button", name="Save mappings").locator("visible=true").click()
    expect(dialog).to_be_hidden()
    admin.goto(world["base"] + "/applications/", wait_until="domcontentloaded")
    table = admin.get_by_role("region", name="Applications table")
    row = table.get_by_role("row", name=re.compile(r"^Select Payments Service "))
    expect(row.locator("[data-testid^='app-capability-']")).to_have_text(CAPABILITY)
    expect(row).not_to_contain_text("map it")
    assert table.get_by_role("button", name="No capability mapped yet: map it").count() == 7  # the others still say what to do
    remember(world, admin)
    shoot(admin, "6a-capability-mapped")


def test_6b_a_viewer_sees_plain_text_and_no_control_in_the_capability_cell(browser, world):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.add_init_script(SKIP_WELCOME)
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        sign_in(page, world["base"], world["one"]["emails"]["non_technical_owner"])
        page.goto(world["base"] + "/applications/", wait_until="domcontentloaded")
        table = page.get_by_role("region", name="Applications table")
        expect(table.get_by_role("button", name="No capability mapped yet: map it")).to_have_count(0)
        expect(table.get_by_text("No capability mapped yet", exact=True).first).to_be_visible()
        expect(table.get_by_text(CAPABILITY, exact=True).first).to_be_visible()
    finally:
        context.close()


def ask_what_breaks_if_payments_service_fails(page, base):
    """In the AI chat, the way a person does: type the question and press Enter. Returns the
    connection cards of the answer."""
    answer = ask(page, base, "What breaks if Payments Service fails")
    cards = answer.locator("[data-slot='card']").filter(has=page.locator("[data-answer-row]"))
    expect(cards.first).to_be_visible()
    return cards


def test_7_asking_what_breaks_names_the_dependents(admin, world):
    assert role_of(world["one"]["emails"]["solution_architect"]) == "solution_architect"  # the persona, not an administrator
    rows = ask_what_breaks_if_payments_service_fails(admin, world["base"])
    for name in ("Billing Api", "Customer Portal", "Payments Worker"):
        expect(rows.filter(has_text=name).first).to_be_visible()
    remember(world, admin)
    shoot(admin, "7-answer", reload=False)


def test_7a_the_saved_answer_names_them_at_its_own_address(admin, world):
    admin.goto(world["base"] + "/intelligence/answers", wait_until="domcontentloaded")
    admin.wait_for_function(
        "() => { const el = document.querySelector('[x-data=\"answersSurface()\"]'); return !!(el && el._x_dataStack); }"
    )
    admin.locator("#answers-picker-input").press_sequentially("Payments Service", delay=15)
    admin.locator("#answers-picker-listbox [role=option]", has_text="Payments Service").first.click()
    admin.locator("#answers-ask-button").click()
    admin.wait_for_url("**/intelligence/answers/*")
    world["seen"]["answer_url"] = admin.url
    rows = admin.locator("[data-answer-rows]")
    for name in ("Billing Api", "Customer Portal", "Payments Worker"):
        expect(rows).to_contain_text(name)
    remember(world, admin)


def shoot_whole_answer(page, path):
    """The whole page in one picture. The page scrolls inside its own frame, so the window is made
    as tall as the longest scrolling thing for the moment of the capture, then put back."""
    size = page.viewport_size
    height = page.evaluate(
        "Math.max(document.documentElement.scrollHeight, ...Array.from(document.querySelectorAll('*'))"
        ".filter(e => e.scrollHeight > e.clientHeight + 50 && /auto|scroll/.test(getComputedStyle(e).overflowY))"
        ".map(e => e.getBoundingClientRect().top + e.scrollHeight))"
    )
    page.set_viewport_size({"width": size["width"], "height": min(max(int(height), size["height"]), 8000)})
    page.wait_for_timeout(300)
    page.screenshot(path=path, full_page=True)
    page.set_viewport_size(size)


def shoot_on_a_phone(browser, world, name):
    """The Ask answer as it opens on a phone: a second person's window at 390 wide, not a resized one."""
    context = browser.new_context(viewport={"width": 390, "height": 844})
    context.add_init_script(SKIP_WELCOME)
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        sign_in(page, world["base"], world["one"]["emails"]["solution_architect"])
        cards = ask_what_breaks_if_payments_service_fails(page, world["base"])
        page.wait_for_timeout(1200)  # the smooth scroll to the answer has settled
        box = cards.first.bounding_box()
        assert box and box["x"] >= 0 and box["x"] + box["width"] <= 390, "the answer overflows a phone: %s" % box
        composer = page.locator("#user-input").bounding_box()
        assert composer and composer["y"] + composer["height"] <= 844, "the question box is off screen: %s" % composer
        if SHOTS:
            os.makedirs(SHOTS, exist_ok=True)
            shoot_whole_answer(page, os.path.join(SHOTS, "%s-390.png" % name))
    finally:
        context.close()


def test_7b_each_dependent_is_cited_with_its_repository_and_file(admin, world, browser):
    rows = ask_what_breaks_if_payments_service_fails(admin, world["base"])
    billing = rows.filter(has_text="Billing Api").first
    expect(billing.locator("[data-citation]").first).to_be_visible()
    text = billing.inner_text()
    assert "acme/billing-api" in text and "pom.xml" in text, text
    assert re.search(r"@ [0-9a-f]{7}(?![0-9a-f])", text), "no short commit in: " + text
    portal = rows.filter(has_text="Customer Portal").first.inner_text()
    assert "acme/customer-portal" in portal and "package.json" in portal, portal
    # Two screens, one owner: the answer shows what the review showed, not "Not recorded".
    for name, owner in world["review_owner"].items():
        row_text = rows.filter(has_text=name).first.inner_text()
        if owner.startswith("No owner"):  # nothing was found, so nothing may be invented
            assert "from GitHub code owners" not in row_text, row_text
            continue
        assert owner in row_text, "review showed %r as the owner of %s, the answer shows: %s" % (owner, name, row_text)
        assert "Owner\nNot recorded" not in row_text, row_text
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        shoot_whole_answer(admin, os.path.join(SHOTS, "7b-answer-cited-1440.png"))
    shoot_on_a_phone(browser, world, "7b-answer-cited")


def test_7c_a_citation_link_opens_on_content(admin, world):
    rows = ask_what_breaks_if_payments_service_fails(admin, world["base"])
    link = rows.filter(has_text="Billing Api").first.get_by_role("link", name="View file on GitHub").first
    expect(link).to_be_visible()
    href = link.get_attribute("href")
    # GitHub is not reachable from here: the address must be exactly the file at the commit.
    assert re.fullmatch(r"https://github\.com/acme/billing-api/blob/[0-9a-f]{40}/pom\.xml", href), href
    assert link.get_attribute("target") == "_blank" and "noopener" in link.get_attribute("rel") and "noreferrer" in link.get_attribute("rel")
    portal = rows.filter(has_text="Customer Portal").first.get_by_role("link", name="View file on GitHub").first
    assert re.fullmatch(r"https://github\.com/acme/customer-portal/blob/[0-9a-f]{40}/package\.json", portal.get_attribute("href"))
    # The citation's short commit is the start of the commit in the address.
    short = re.search(r"@ ([0-9a-f]{7})", rows.filter(has_text="Billing Api").first.inner_text()).group(1)
    assert "/blob/" + short in href
    # Inside the product, the same row leads to the element in the Composer, which draws it.
    inside = rows.filter(has_text="Billing Api").first.get_by_role("link", name="Billing Api", exact=True)
    inside.click()
    admin.wait_for_load_state("domcontentloaded")
    assert "/archimate/composer?element=" in admin.url
    expect(admin.locator("#composer-focus-title")).to_contain_text("Billing Api", timeout=15000)


def test_7e_a_citation_link_still_opens_in_a_new_tab_after_the_connections_are_worked_out(admin, world):
    answer = ask(admin, world["base"], "What breaks if Payments Service fails")
    work_out = answer.get_by_role("button", name="Work them out now")
    expect(work_out).to_be_visible()
    # Working out a whole organisation's connections takes minutes on a loaded machine and is not what is
    # under test: the request is answered at once, and the question is then asked again for real.
    admin.route("**/api/v1/intelligence/derivation/recompute", lambda route: route.fulfill(status=200, content_type="application/json", body="{}"))
    answer.locator("[data-answer-cards]").evaluate("el => el.setAttribute('data-first-draw', '')")
    work_out.click()
    # The answer is drawn again in place of the first one; its citations are the same links.
    chat_address = admin.url
    expect(admin.locator("[data-first-draw]")).to_have_count(0)
    again = admin.get_by_test_id("estate-answer").locator("[data-answer-cards]").first
    cards = again.locator("[data-slot='card']").filter(has=admin.locator("[data-answer-row]"))
    link = cards.filter(has_text="Billing Api").first.get_by_role("link", name="View file on GitHub").first
    expect(link).to_be_visible()
    assert re.fullmatch(r"https://github\.com/acme/billing-api/blob/[0-9a-f]{40}/pom\.xml", link.get_attribute("href"))
    assert link.get_attribute("target") == "_blank", "the citation would take the person out of the conversation"
    rel = link.get_attribute("rel") or ""
    assert "noopener" in rel and "noreferrer" in rel, rel
    assert admin.url == chat_address
    admin.unroute("**/api/v1/intelligence/derivation/recompute")


def test_7d_the_saved_answer_cites_each_dependent_with_a_link(admin, world):
    admin.goto(world["seen"]["answer_url"], wait_until="domcontentloaded")
    rows = admin.locator("[data-answer-rows] tbody tr")
    billing = rows.filter(has_text="Billing Api")
    expect(billing).to_contain_text("acme/billing-api")
    expect(billing).to_contain_text("pom.xml")
    href = billing.get_by_role("link", name="View file on GitHub").first.get_attribute("href")
    assert re.fullmatch(r"https://github\.com/acme/billing-api/blob/[0-9a-f]{40}/pom\.xml", href), href
    expect(rows.filter(has_text="Customer Portal").first).to_contain_text("acme/customer-portal")
    remember(world, admin)
    shoot(admin, "7d-saved-answer")


def test_8_another_organisation_sees_none_of_it(browser, world):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.add_init_script(SKIP_WELCOME)
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        sign_in(page, world["base"], world["two"]["emails"]["solution_architect"])
        page.goto(world["base"] + "/admin/connectors/github", wait_until="domcontentloaded")
        expect(page.get_by_role("button", name="Connect and read my repositories")).to_be_visible()
        expect(page.get_by_test_id("github-progress")).to_have_count(0)
        page.goto(world["base"] + "/admin/connectors/github/review", wait_until="domcontentloaded")
        expect(page.locator("#main-content").get_by_text("Nothing has been checked yet", exact=False)).to_be_visible()
        page.goto(world["base"] + "/applications/", wait_until="domcontentloaded")
        for name in ("Payments Service", "Customer Portal", "Billing Api"):
            expect(page.get_by_text(name)).to_have_count(0)
        page.goto(world["base"] + "/intelligence/answers", wait_until="domcontentloaded")
        expect(page.locator("[data-answer-item]")).to_have_count(0)
        response = page.goto(world["seen"]["answer_url"], wait_until="domcontentloaded")
        assert response.status == 404
        assert "Payments Service" not in page.content()
    finally:
        context.close()


def test_9_the_token_is_in_no_page_and_no_response(world):
    seen = world["seen"]
    assert seen["html"] and seen["bodies"], "nothing was collected"
    for url, html in seen["html"]:
        assert TOKEN not in html, "the token is in the page at %s" % url
    for url, body in seen["bodies"]:
        assert TOKEN not in body, "the token is in the response from %s" % url


def test_10_a_viewer_is_refused_the_connect_page(browser, world):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.add_init_script(SKIP_WELCOME)
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        sign_in(page, world["base"], world["one"]["emails"]["non_technical_owner"])
        for path in ("/admin/connectors/github", "/admin/connectors/github/review"):
            response = page.goto(world["base"] + path, wait_until="domcontentloaded")
            assert response.status == 403, "%s answered %s" % (path, response.status)
            assert page.get_by_label("Read-only token").count() == 0
            assert "Payments Service" not in page.content()
    finally:
        context.close()
