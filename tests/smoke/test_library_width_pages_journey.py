"""Seven pages use the library's width classes, and each still shows its content whole.

Goal: a person who is allowed to open the interface register, the value streams,
the spend page, the organisations list, the prompts list, the approval inbox or the
errors page sees the text in the widened cell or paragraph in full, and the page
does not scroll sideways, on a desktop and on a phone.

Each page is opened in an organisation that holds linked records (an interface
with a provider and a consumer, a value stream, a decided proposal, an error),
plus the spend page in an organisation with no contracts, which shows its
explanation instead of a table.

Played at 1440x900 and 390x844.
"""
import uuid

import pytest

from tests.smoke.conftest import PAGE_TIMEOUT
from tests.smoke.fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WIDTHS = ((1440, 900), (390, 844))

# Every element a library width class (max-w-md, max-w-lg, max-w-xl) sits on, with
# whether its content is cut and how wide the page is against the window.
MEASURE = """() => {
  const sizes = ['max-w-md', 'max-w-lg', 'max-w-xl'];
  const sites = [...document.querySelectorAll('main td, main p')]
    .filter(el => sizes.some(s => el.classList.contains(s)))
    .map(el => ({text: el.innerText.trim(), cut: el.scrollWidth > el.clientWidth + 1}));
  // Every element inside a table cell that truncates or hides overflow, wherever it sits.
  const clipped = [];
  for (const cell of document.querySelectorAll('main td, main th')) {
    for (const el of [cell, ...cell.querySelectorAll('*')]) {
      const style = getComputedStyle(el);
      const hides = style.overflowX === 'hidden' || style.textOverflow === 'ellipsis' || el.classList.contains('truncate');
      if (el.classList.contains('sr-only') || el.getBoundingClientRect().width <= 1) continue;
      if (hides && el.scrollWidth > el.clientWidth + 1) {
        clipped.push((el.innerText || el.textContent || '').trim());
      }
    }
  }
  // A table that scrolls sideways inside its own container, and the cells it hides there.
  const containers = [...document.querySelectorAll('main table')].map(table => {
    let box = table.parentElement;
    while (box && box.tagName !== 'MAIN' && getComputedStyle(box).overflowX === 'visible') box = box.parentElement;
    const scrolls = !!box && box.tagName !== 'MAIN' && box.scrollWidth > box.clientWidth + 1;
    const onPurpose = !!box && ['auto', 'scroll'].includes(getComputedStyle(box).overflowX);
    return {scrolls, onPurpose, tableWide: table.scrollWidth - (box ? box.clientWidth : window.innerWidth)};
  });
  const root = document.documentElement;
  return {sites, clipped, containers, overflow: root.scrollWidth - window.innerWidth,
          heading: (document.querySelector('main h1') || {}).innerText || '',
          bodyText: document.querySelector('main').innerText.length};
}"""


def _seed_estate(org):
    """An interface with provider and consumer, a value stream, a decided proposal
    and an error, all in the organisation, with names long enough to need room."""
    from flask import g

    from app import create_app, db

    app = create_app("testing")
    suffix = org["suffix"]
    out = {}
    with app.app_context():
        from app.models.application_portfolio import ApplicationComponent
        from app.models.archimate_core import ArchitectureModel
        from app.models.error_event import ErrorEvent
        from app.models.implementation_migration import TechnologyRoadmapInitiative
        from app.models.unified_capability import ValueStream
        from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService
        from app.modules.interface_register.services import interface_register_service as register
        from app.services.archimate_backbone import create_backbone_element

        from app.models.user import User

        org_id = org["org_id"]
        operator = db.session.get(User, org["user_ids"]["platform_admin"])
        operator.is_platform_admin = True
        operator.is_org_admin = True
        architecture = ArchitectureModel(name="Width walk architecture %s" % suffix, organization_id=org_id)
        db.session.add(architecture)
        db.session.flush()
        initiative = TechnologyRoadmapInitiative(
            name="Settlement platform renewal %s" % suffix, fiscal_year_start=2026, fiscal_year_end=2027,
            architecture_id=architecture.id)
        db.session.add(initiative)
        components = []
        for name in ("Ledger %s" % suffix, "Billing %s" % suffix):
            element = create_backbone_element(element_type="ApplicationComponent", layer="Application",
                                              name=name, organization_id=org_id)
            component = ApplicationComponent(name=name, organization_id=org_id, archimate_element_id=element.id)
            db.session.add(component)
            components.append(component)
        db.session.flush()
        with app.test_request_context("/"):
            g.current_org_id = org_id
            interface = register.create_interface(initiative.id, {
                "name": "Quarterly settlement feed %s" % suffix, "interface_type": "REST",
                "protocol": "HTTPS", "business_criticality": "High",
                "provider_component_id": str(components[0].id),
                "consumer_component_id": str(components[1].id)}, org_id)
            stream = ValueStream(organization_id=org_id, name="Order to cash %s" % suffix,
                                 code="WIDTH-%s" % uuid.uuid4().hex[:8], value_stream_type="customer_facing")
            db.session.add(stream)
            service = AIChatApprovalService(org["user_ids"]["application_manager"])
            proposal = service.create_pending_approval(
                operation_type="create", entity_type="capability", original_command="walk",
                operation_payload={"name": "Width capability %s" % suffix},
                summary="Create capability Width capability %s" % suffix, chat_session_id="walk-%s" % suffix)
            assert proposal["success"], proposal
            assert service.reject_approval(proposal["approval_id"], "Already covered")["success"]
        error = ErrorEvent(fingerprint=uuid.uuid4().hex, source="server", level="ERROR",
                           message="Width walk failure %s" % suffix, location="walk:1")
        db.session.add(error)
        db.session.commit()
        out.update(initiative_id=initiative.id, interface=interface.name, stream=stream.name,
                   summary="Create capability Width capability %s" % suffix,
                   error="Width walk failure %s" % suffix, error_id=error.id, org_name="Launch Org %s" % suffix)
        db.session.remove()
    return out


def _drop_error(error_id):
    from app import create_app, db
    from app.models.error_event import ErrorEvent

    app = create_app("testing")
    with app.app_context():
        ErrorEvent.query.filter_by(id=error_id).delete()  # tenant-scoping-ok: removes the one row this test made, by key
        db.session.commit()


def _open(browser, base, email, path, size):
    context = browser.new_context(ignore_https_errors=True, viewport={"width": size[0], "height": size[1]})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    sign_in(page, base, email)
    response = page.goto(base + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert response.status == 200, "%s answered %s" % (path, response.status)
    page.wait_for_selector("main h1", timeout=PAGE_TIMEOUT)
    return context, page


def _assert_whole(path, size, measured, expect_text):
    where = "%s at %d wide" % (path, size[0])
    assert measured["overflow"] <= 0, "%s scrolls sideways by %spx" % (where, measured["overflow"])
    assert measured["heading"].strip(), "%s has no heading" % where
    assert measured["sites"], "%s shows no element on a library width class" % where
    cut = [s["text"] for s in measured["sites"] if s["cut"]]
    assert not cut, "%s cuts its content: %s" % (where, cut)
    clipped = [t for t in measured["clipped"] if t]
    assert not clipped, "%s cuts text inside a cell: %s" % (where, clipped)
    for box in measured["containers"]:
        assert not box["scrolls"] or box["onPurpose"], "%s has a table that scrolls sideways by accident" % where
    shown = " ".join(s["text"] for s in measured["sites"])
    assert expect_text in shown, "%s does not show %r in the widened cell; it shows %r" % (where, expect_text, shown)


@pytest.mark.parametrize("size", WIDTHS, ids=lambda s: "%dpx" % s[0])
def test_widened_pages_show_their_content_whole(browser, live_server, size):
    org = create_fresh_org("enterprise_architect", extra_roles=("platform_admin", "application_manager"))
    seeded = _seed_estate(org)
    try:
        email = org["emails"]
        cases = (
            (email["enterprise_architect"], "/interface-register/", "Settlement platform renewal"),
            (email["enterprise_architect"], "/interface-register/?initiative_id=%d" % seeded["initiative_id"], seeded["interface"]),
            (email["enterprise_architect"], "/value-streams/", seeded["stream"]),
            (email["application_manager"], "/ai-chat/approvals/inbox", seeded["summary"]),
            (email["platform_admin"], "/admin/organizations", seeded["org_name"]),
            (email["platform_admin"], "/admin/errors", seeded["error"]),
            (email["platform_admin"], "/admin/solution-prompts", ""),
        )
        for address, path, expect_text in cases:
            context, page = _open(browser, live_server, address, path, size)
            try:
                _assert_whole(path, size, page.evaluate(MEASURE), expect_text)
            finally:
                context.close()
    finally:
        _drop_error(seeded["error_id"])


@pytest.mark.parametrize("size", WIDTHS, ids=lambda s: "%dpx" % s[0])
def test_spend_page_with_no_contracts_explains_itself_whole(browser, live_server, size):
    org = create_fresh_org("procurement")
    context, page = _open(browser, live_server, org["emails"]["procurement"], "/procurement/spend", size)
    try:
        _assert_whole("/procurement/spend", size, page.evaluate(MEASURE), "Add your vendor contracts")
    finally:
        context.close()
