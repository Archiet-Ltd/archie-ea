"""A CTO finds the assistant, the evidence page and the application pages telling one story.

The person: a CTO whose estate came in partly from GitHub. The goals, one test each:
the assistant never contradicts its own answer, imported repositories are not passed
off as applications, the evidence page reads as sentences, an application's page agrees
with its own connections, the ownership page and the overview count applications the
same way and say why, and an action about a work package opens that package.

Every test runs in an organisation seeded with linked records, beside a second
organisation holding records of its own that must never appear.
"""

import datetime
import re
import uuid

import pytest
from playwright.sync_api import expect

from .chat_ask import ask, open_chat
from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed(org, tag, *, import_names, wp_name, extra_apps=0):
    """One organisation: four owned applications, one decommissioned, imports, a late work package."""
    from app import create_app, db
    from app.connectors import github
    from app.models.application_owner import ApplicationOwner
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.audit_log import AuditLog
    from app.models.enterprise_intelligence import OrganizationUnit
    from app.models.unified_work_package import UnifiedWorkPackage

    app = create_app("testing")
    out = {}
    with app.app_context():
        org_id, owner_id = org["org_id"], org["user_ids"]["cto"]

        def application(name, status="planning", owned=True, domain=None):
            row = ApplicationComponent(name=name, organization_id=org_id, lifecycle_status=status,
                                       business_domain=domain)
            db.session.add(row)
            db.session.commit()
            if owned:
                db.session.add(ApplicationOwner(user_id=owner_id, application_id=row.id,
                                                organization_id=org_id, ownership_type="primary"))
                db.session.commit()
            return row

        ledger = application("Calibration Ledger %s" % tag)
        hub = application("Orders Hub %s" % tag)
        billing = application("Billing Core %s" % tag)
        application("Pay Gateway %s" % tag)
        for n in range(extra_apps):
            application("Extra System %s %d" % (tag, n))
        application("Legacy Fax %s" % tag, status="5. DECOMMISSIONED")

        # A business unit holding one live and one decommissioned owned application, and an
        # owned application in a domain that matches no unit.
        db.session.add(OrganizationUnit(name="Operations %s" % tag, code="OPS-%s" % tag,
                                        organization_id=org_id))
        db.session.commit()
        application("Ops Live %s" % tag, domain="Operations %s" % tag)
        application("Ops Retired %s" % tag, status="5. DECOMMISSIONED", domain="Operations %s" % tag)
        application("Marketing Site %s" % tag, domain="Marketing")

        api = ArchiMateElement(name="Calibration Ledger API %s" % tag, type="ApplicationInterface",
                               layer="application", organization_id=org_id)
        db.session.add(api)
        db.session.commit()
        for target in (hub, billing):
            db.session.add(ArchiMateRelationship(type="Serving", source_id=ledger.archimate_element_id,
                                                 target_id=target.archimate_element_id, organization_id=org_id))
        db.session.add(ArchiMateRelationship(type="Composition", source_id=ledger.archimate_element_id,
                                             target_id=api.id, organization_id=org_id))
        db.session.commit()

        # Repositories the connector brought in: approved as applications, nobody has promoted them.
        imported = []
        for name in import_names:
            row = application(name, owned=False)
            github._finish_application(
                org_id, row.id,
                {"repository": "acme/%s" % name.lower().replace(" ", "-"), "commit": "abc123",
                 "evidence_file": "Dockerfile", "owners": []},
                owner_id)
            db.session.commit()
            imported.append(row.id)

        db.session.add(UnifiedWorkPackage(
            name=wp_name, organization_id=org_id, status="in_progress", progress_percentage=40,
            priority="high", start_date=datetime.datetime(2026, 1, 5), end_date=datetime.datetime(2026, 3, 1),
            owner_id=owner_id))
        db.session.commit()
        wp = UnifiedWorkPackage.query.filter_by(organization_id=org_id, name=wp_name).one()

        # What the change history holds: a connector sync that wrote a property with its source,
        # and a row that recorded who made a record and nothing else.
        element = db.session.get(ArchiMateElement, ledger.archimate_element_id)
        db.session.add(AuditLog(
            organization_id=org_id, action="update", table_name="archimate_elements", record_id=element.id,
            old_value={"acm_properties": {"github_archived": {"value": True, "source": "github"}}},
            new_value={"acm_properties": {"github_archived": {"value": False, "source": "github"}}},
            extra_json={"source": "connector", "label": element.name}))
        db.session.add(AuditLog(
            organization_id=org_id, action="create", table_name="application_component", record_id=hub.id,
            old_value=None, new_value={}, extra_json={"source": "import", "label": hub.name}))
        db.session.commit()
        out.update(ledger=ledger.id, hub=hub.id, wp=wp.id, wp_name=wp_name, imported=imported,
                   tag=tag, imports=list(import_names))
        db.session.remove()
    return out


@pytest.fixture(scope="module")
def estate(live_server):
    tag = uuid.uuid4().hex[:6]
    org = create_fresh_org("cto", org_admin=False)
    other = create_fresh_org("cto", org_admin=False)
    mine = _seed(org, tag, import_names=["Archie Ea %s" % tag, "Entelim Status %s" % tag],
                 wp_name="Deploy mobile field app %s" % tag)
    theirs = _seed(other, tag + "x", import_names=["Rival Repo %s" % tag],
                   wp_name="Rival rollout %s" % tag, extra_apps=3)
    return {"org": org, "mine": mine, "theirs": theirs, "email": org["emails"]["cto"], "tag": tag}


@pytest.fixture()
def cto(page, live_server, estate):
    sign_in(page, live_server, estate["email"])
    return page


def test_assistant_never_contradicts_its_own_answer(cto, live_server, estate):
    """Ask what breaks, then come back to the conversation: an answer from the records
    is never followed by a line saying nothing was read."""
    tag = estate["tag"]
    answer = ask(cto, live_server, "What breaks if Calibration Ledger %s fails?" % tag)
    assert "Orders Hub" in answer.inner_text()
    cto.reload(wait_until="domcontentloaded")
    restored = cto.locator("#messages-container")
    expect(restored.get_by_text("Answer from your records").first).to_be_visible(timeout=PAGE_TIMEOUT)
    text = restored.inner_text()
    assert "No portfolio data was read" not in text, text[:400]
    assert re.search(r"[Aa]nswered from your (organisation's )?records", text), text[:600]
    # Nothing was stored to open, so the line is not a control that opens an empty panel.
    assert restored.locator(".js-evidence-toggle").filter(has_text="Answered from your").count() == 0


def test_imported_repositories_are_not_offered_as_applications(cto, live_server, estate):
    """The list names an unpromoted import as a repository, and the assistant's suggested
    questions leave it out."""
    tag = estate["tag"]
    cto.goto(live_server + "/applications/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    row = cto.locator("tr, [data-testid='application-row'], li").filter(has_text="Archie Ea %s" % tag).first
    expect(row).to_be_visible(timeout=PAGE_TIMEOUT)
    assert re.search(r"repository", row.inner_text(), re.I), row.inner_text()
    assert "Rival Repo" not in cto.locator("main").inner_text()

    open_chat(cto, live_server)
    cards = cto.locator("#portfolio-briefing")
    expect(cards).to_contain_text("What breaks if", timeout=PAGE_TIMEOUT)
    suggested = cards.inner_text()
    for name in estate["mine"]["imports"]:
        assert name not in suggested, suggested
    assert "Rival" not in suggested


def test_evidence_page_reads_as_sentences(cto, live_server, estate):
    """A property written by a connector reads "Field: before -> after", and a row with no
    values says what happened and who or what did it."""
    cto.goto(live_server + "/intelligence/history/changes", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    rows = cto.locator("[data-testid='change-row']")
    expect(rows.first).to_be_visible(timeout=PAGE_TIMEOUT)
    text = cto.locator("main").inner_text()
    assert "{" not in text and "'value'" not in text, text[:600]
    sync = rows.filter(has_text="Calibration Ledger").first.inner_text()
    assert "Github archived: yes → no" in sync, sync
    bare = rows.filter(has_text="Orders Hub").first.inner_text()
    assert "No field values were recorded" not in bare, bare
    assert re.search(r"Created .*Orders Hub", bare), bare
    assert re.search(r"Import", bare), bare
    assert not re.search(r"(^|\n)—(\n|$)", bare), bare
    assert "Rival" not in text


def test_application_page_agrees_with_its_connections(cto, live_server, estate):
    """The page of an application whose API is among its connections counts that interface,
    and an unrecorded deployment status says why with a next step."""
    cto.goto(live_server + "/applications/%d" % estate["mine"]["ledger"], wait_until="domcontentloaded",
             timeout=PAGE_TIMEOUT)
    assert "Calibration Ledger API" in cto.content()
    card = cto.locator("text=Interfaces Linked").first.locator("xpath=ancestor::*[self::div][2]")
    assert re.search(r"\b1\b", card.inner_text()), card.inner_text()
    status = cto.locator("text=Deployment Status").first.locator("xpath=ancestor::*[self::div][2]").inner_text()
    assert re.search(r"not recorded", status, re.I), status
    assert re.search(r"edit|set|add|record", status, re.I), status


def _overview_count(page, live_server):
    page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.locator("main")).to_contain_text("Applications", timeout=PAGE_TIMEOUT)
    figure = re.search(r"(\d+)\s*\n\s*Applications\b", page.locator("main").inner_text())
    assert figure, page.locator("main").inner_text()[:600]
    return int(figure.group(1))


def _coverage_rows(page, live_server):
    page.goto(live_server + "/applications/ownership-coverage", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    rows = {}
    for tr in page.locator("main tbody tr").all():
        cells = [c.strip() for c in tr.inner_text().split("\t")]
        if len(cells) == 4:
            rows[cells[0]] = cells[1:]
    return rows


def test_ownership_page_and_overview_count_the_same_applications(cto, live_server, estate):
    """Both count applications through one function and the ownership page says a
    decommissioned application is left out. A second organisation's applications never count."""
    cto.goto(live_server + "/applications/ownership-coverage", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    main = cto.locator("main").inner_text()
    # Six owned live applications; the two imports nobody owns and the two decommissioned
    # ones are not counted, and the page says so.
    m = re.search(r"Total applications\s*\n\s*(\d+)", main)
    assert m, main[:400]
    total = int(m.group(1))
    assert total == 6, main[:600]
    assert re.search(r"decommissioned", main, re.I), main[:600]
    assert total == _overview_count(cto, live_server)
    assert "Rival" not in main


def test_a_unit_with_a_decommissioned_owned_application_never_reads_above_full(cto, live_server, estate):
    """The unit holds one live and one decommissioned owned application: it reads 1 of 1, 100%."""
    rows = _coverage_rows(cto, live_server)
    unit = rows["Operations %s" % estate["tag"]]
    assert unit[0] == "1" and unit[1] == "1" and unit[2] == "100%", unit
    for name, (apps, owned, pct) in rows.items():
        if apps.isdigit():
            assert int(owned) <= int(apps), (name, apps, owned)
            assert int(pct.rstrip("%")) <= 100, (name, pct)


def test_applications_outside_every_unit_are_named_and_the_totals_reconcile(cto, live_server, estate):
    """Applications whose domain matches no unit show on their own row, so the rows add up to the total."""
    rows = _coverage_rows(cto, live_server)
    outside = rows["No business unit"]
    assert outside[0] == "5", outside  # four with no domain and one in Marketing
    assert sum(int(r[0]) for r in rows.values() if r[0].isdigit()) == 6
    assert "Marketing" in cto.locator("main").inner_text()


def test_an_unpromoted_import_reads_repository_on_the_evidence_page(cto, live_server, estate):
    """An import nobody owns is a repository on the evidence page too, and is in no count."""
    cto.goto(live_server + "/intelligence/history/changes", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    text = cto.locator("main").inner_text()
    imported = cto.locator("[data-testid='change-row']").filter(has_text="Archie Ea %s" % estate["tag"])
    assert imported.count(), text[:600]
    shown = " ".join(imported.all_inner_texts())
    assert "Repository" in shown, shown
    assert not re.search(r"ApplicationComponent\s*→\s*Application\b", shown), shown
    assert "Competitive advantage: False" not in text
    cto.goto(live_server + "/applications/ownership-coverage", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert "Archie Ea" not in cto.locator("main").inner_text()


def test_work_package_action_opens_the_package(cto, live_server, estate):
    """"Finish <package>" lands on that package, not the whole list."""
    cto.goto(live_server + "/strategic/impact-analysis", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    link = cto.get_by_test_id("priority-action-link").filter(has_text="Deploy mobile field app").first
    expect(link).to_be_visible(timeout=PAGE_TIMEOUT)
    assert "Finish Deploy mobile field app" in link.inner_text()
    link.click()
    cto.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)
    assert re.search(r"/work-packages/%d\b" % estate["mine"]["wp"], cto.url), cto.url
    expect(cto.locator("main")).to_contain_text("Deploy mobile field app %s" % estate["tag"], timeout=PAGE_TIMEOUT)
    assert "Rival rollout" not in cto.locator("main").inner_text()
