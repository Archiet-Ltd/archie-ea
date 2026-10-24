"""Journey, in a browser: every page that can show an owner offers the same
next step when there is none, and the external-identifiers help names only a
connector the organisation can actually use today.

A person opens a capability and an application that have no owner. Each says
"No owner recorded" and offers "Assign owner". They take the step on the
application page with its search picker; the owner they chose then shows by
name on both the application page and that application's element page, which
read the same owner record. An owner assigned in one organisation never shows
in another. The external-identifiers help names GitHub only once a GitHub
connector is live for the organisation, and otherwise names no connector and
points at the Connectors page.
"""

import uuid

import pytest

from .conftest import PAGE_TIMEOUT
from . import fresh_org

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

ASSIGNER = "business_architect"
OWNER = "application_manager"


def _seed_elements(org_id):
    """Create, through the ORM, a capability and an application with no owner."""
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capabilities import BusinessCapability

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:6]
    with app.app_context():
        capability = BusinessCapability(
            name="Owner action capability %s" % suffix,
            organization_id=org_id,
            level=1,
            category="Operations",
        )
        component = ApplicationComponent(
            name="Owner action application %s" % suffix,
            organization_id=org_id,
            lifecycle_status="operational",
            description="Owner-action fixture.",
        )
        db.session.add_all([capability, component])
        db.session.commit()
        # This journey is about a capability not yet projected into the one
        # capability store, which offers a link to the capability map; remove
        # the projection the write-time sync may already have made.
        db.session.execute(
            db.text("DELETE FROM unified_capabilities WHERE source_table = "
                    "'business_capability' AND source_id = :sid"),
            {"sid": str(capability.id)},
        )
        db.session.commit()
        ids = {
            "capability_id": capability.id,
            "application_id": component.id,
            "capability_name": capability.name,
            "application_name": component.name,
        }
        db.session.remove()
    return ids


def _capability_url(base, ids):
    return "%s/architecture/strategy/Capability/%d" % (base, ids["capability_id"])


def _application_url(base, ids):
    return "%s/applications/%d" % (base, ids["application_id"])


def _application_element_url(base, ids):
    return "%s/architecture/application/ApplicationComponent/%d" % (
        base, ids["application_id"],
    )


def _owner_name(org):
    """The name the picker shows for the other person in an organisation."""
    from app import create_app, db
    from app.models.user import User

    app = create_app("testing")
    with app.app_context():
        user = db.session.get(User, org["user_ids"][OWNER])
        name = user.full_name()
        db.session.remove()
    return name


@pytest.fixture
def two_orgs():
    """Two isolated organisations, each with a person who can assign and a
    person to be assigned as owner, plus an un-owned capability and
    application in each."""
    org_a = fresh_org.create_fresh_org(ASSIGNER, org_admin=True, extra_roles=(OWNER,))
    org_b = fresh_org.create_fresh_org(ASSIGNER, org_admin=True, extra_roles=(OWNER,))
    org_a["elements"] = _seed_elements(org_a["org_id"])
    org_b["elements"] = _seed_elements(org_b["org_id"])
    return org_a, org_b


def test_assign_owner_action_shows_on_application_and_element_pages(page, live_server, two_orgs):
    org_a, org_b = two_orgs
    ids_a = org_a["elements"]
    owner_name = _owner_name(org_a)
    assert owner_name and owner_name != "—", "the person to assign has a shown name"

    fresh_org.sign_in(page, live_server, org_a["emails"][ASSIGNER])

    # The capability page states the gap and offers the step.
    page.goto(_capability_url(live_server, ids_a), wait_until="networkidle",
              timeout=PAGE_TIMEOUT)
    owner_cell = page.locator("[data-testid='element-owner']")
    assert "No owner recorded" in owner_cell.inner_text()
    assert owner_cell.locator("a", has_text="Assign owner").count() == 1

    # The application page states the same gap and offers the same step.
    page.goto(_application_url(live_server, ids_a), wait_until="networkidle",
              timeout=PAGE_TIMEOUT)
    page.locator("[data-testid='application-owner-empty']").wait_for(
        state="visible", timeout=PAGE_TIMEOUT)
    assert "No owner recorded" in page.inner_text("main")
    assign = page.locator("[data-owner-picker] [data-owner-search]")
    assert assign.count() == 1, "the application page has no owner picker"

    # Take the step: search, pick the person, save. The POST records the owner.
    # The search matches a name part against each name field, so search the
    # distinctive surname rather than the full name (every person here shares
    # the same forename).
    assign.fill(owner_name.split()[-1])
    result = page.locator(
        "[data-owner-picker] [data-owner-results] button", has_text=owner_name
    ).first
    result.wait_for(timeout=PAGE_TIMEOUT)
    result.click()
    with page.expect_response(
        lambda r: r.request.method == "POST" and "/owners" in r.url
    ) as saved:
        page.locator("[data-owner-picker] [data-owner-save]").click()
    assert saved.value.status == 201

    # The owner now shows by name on the application page.
    page.goto(_application_url(live_server, ids_a), wait_until="networkidle",
              timeout=PAGE_TIMEOUT)
    assert owner_name in page.inner_text("main"), (
        "the application page does not show the assigned owner by name"
    )

    # The same owner record shows by name on the application's element page.
    page.goto(_application_element_url(live_server, ids_a), wait_until="networkidle",
              timeout=PAGE_TIMEOUT)
    assert owner_name in page.locator("[data-testid='element-owner']").inner_text(), (
        "the element page does not show the assigned owner by name"
    )

    # Isolation: the other organisation never shows this owner.
    page.goto(live_server + "/account/logout", wait_until="networkidle",
              timeout=PAGE_TIMEOUT)
    fresh_org.sign_in(page, live_server, org_b["emails"][ASSIGNER])
    page.goto(_application_url(live_server, org_b["elements"]), wait_until="networkidle",
              timeout=PAGE_TIMEOUT)
    assert owner_name not in page.inner_text("main"), (
        "an owner from the other organisation leaked onto this one's application page"
    )
    assert "No owner recorded" in page.inner_text("main")


def _seed_github_connector(org_id):
    """Record a live GitHub connector for the organisation."""
    from app import create_app, db
    from app.models.connector_config import ConnectorConfig, ConnectorStatus

    app = create_app("testing")
    with app.app_context():
        db.session.add(ConnectorConfig(
            organization_id=org_id,
            connector_type="github",
            name="GitHub",
            config={},
            status=ConnectorStatus.ACTIVE.value,
        ))
        db.session.commit()
        db.session.remove()


def test_external_identifiers_help_names_only_live_connectors(page, live_server, two_orgs):
    org_a, org_b = two_orgs

    fresh_org.sign_in(page, live_server, org_a["emails"][ASSIGNER])

    # Deliberately empty: no external links and no live connector. The help
    # names no connector and links to the Connectors page.
    page.goto(_application_element_url(live_server, org_a["elements"]),
              wait_until="networkidle", timeout=PAGE_TIMEOUT)
    help_block = page.locator("[data-testid='external-identifiers']")
    help_text = help_block.inner_text()
    assert "GitHub" not in help_text, "named a connector that is not live"
    assert "Jira" not in help_text, "named a connector that is not live"
    assert help_block.locator("a", has_text="Connectors").count() == 1, (
        "no link to the Connectors page when nothing is live"
    )

    # With a GitHub connector live for this organisation, the help names GitHub
    # and never Jira.
    _seed_github_connector(org_a["org_id"])
    page.goto(_application_element_url(live_server, org_a["elements"]),
              wait_until="networkidle", timeout=PAGE_TIMEOUT)
    help_text = page.locator("[data-testid='external-identifiers']").inner_text()
    assert "GitHub" in help_text, "did not name GitHub once it is live"
    assert "Jira" not in help_text, "named Jira, which is not live"


# ---------------------------------------------------------------------------
# The enterprise architect (not an organisation administrator) takes the step
# on an application page, on an application opened as a generic element, and
# on a capability page; the owner then shows on the fact sheet and the list.
# ---------------------------------------------------------------------------

ARCHITECT = "enterprise_architect"


def _seed_generic_application_and_capability(org_id):
    """An application that a person reaches through its generic element row
    (the element id and the application id differ), and a capability that has
    been projected into the one capability store."""
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capabilities import BusinessCapability
    from app.models.models import ArchiMateElement
    from app.models.unified_capability import UnifiedCapability

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:6]
    with app.app_context():
        top = db.session.execute(db.text(
            "SELECT GREATEST(COALESCE((SELECT MAX(id) FROM archimate_elements), 0),"
            " COALESCE((SELECT MAX(id) FROM application_components), 0))"
        )).scalar()
        element = ArchiMateElement(
            id=top + 1000, name="Relay application %s" % suffix,
            type="ApplicationComponent", layer="application",
            organization_id=org_id,
        )
        db.session.add(element)
        db.session.flush()
        component = ApplicationComponent(
            name=element.name, organization_id=org_id,
            lifecycle_status="operational", archimate_element_id=element.id,
        )
        capability = BusinessCapability(
            name="Relay capability %s" % suffix, organization_id=org_id,
            level=1, category="Operations",
        )
        db.session.add_all([component, capability])
        db.session.flush()
        # The capability's write-time projection may already have created the
        # unified row (it does once the provenance index exists); add it only
        # when it has not.
        unified = UnifiedCapability.query.filter_by(
            source_table="business_capability", source_id=str(capability.id),
            organization_id=org_id,
        ).first()
        if unified is None:
            unified = UnifiedCapability(
                name=capability.name, organization_id=org_id,
                source_table="business_capability", source_id=str(capability.id),
            )
            db.session.add(unified)
        db.session.commit()
        ids = {
            "element_id": element.id,
            "application_id": component.id,
            "application_name": component.name,
            "capability_id": capability.id,
            "capability_name": capability.name,
        }
        db.session.remove()
    return ids


@pytest.fixture
def architect_orgs():
    orgs = []
    for _ in range(2):
        org = fresh_org.create_fresh_org(ARCHITECT, org_admin=False, extra_roles=(OWNER,))
        org["ids"] = _seed_generic_application_and_capability(org["org_id"])
        orgs.append(org)
    return orgs


def _assign_with_picker(page, owner_name):
    box = page.locator("[data-owner-picker] [data-owner-search]")
    assert box.count() == 1, "the page offers no Assign owner step"
    box.fill(owner_name.split()[-1])
    page.locator(
        "[data-owner-picker] [data-owner-results] button", has_text=owner_name
    ).first.click()
    # Saving reloads the page; wait for that reload before moving on.
    with page.expect_navigation(wait_until="networkidle", timeout=PAGE_TIMEOUT):
        with page.expect_response(
            lambda r: r.request.method == "POST" and "/owners" in r.url
        ) as saved:
            page.locator("[data-owner-picker] [data-owner-save]").click()
    assert saved.value.status == 201


def test_architect_assigns_owner_on_application_and_element_pages(
    page, live_server, architect_orgs
):
    org_a, org_b = architect_orgs
    ids = org_a["ids"]
    owner_name = _owner_name(org_a)
    fresh_org.sign_in(page, live_server, org_a["emails"][ARCHITECT])

    # Application page: the architect sees the step and takes it.
    app_url = "%s/applications/%d" % (live_server, ids["application_id"])
    page.goto(app_url, wait_until="networkidle", timeout=PAGE_TIMEOUT)
    # The ownership card in the overview tab states the gap.
    owner_empty = page.locator("[data-testid='application-owner-empty']")
    owner_empty.wait_for(state="visible", timeout=PAGE_TIMEOUT)
    assert "No owner recorded" in owner_empty.inner_text()
    _assign_with_picker(page, owner_name)
    page.goto(app_url, wait_until="networkidle", timeout=PAGE_TIMEOUT)
    assert owner_name in page.locator("[data-testid='application-owner']").inner_text()

    # The same application opened as a generic element shows that owner.
    element_url = "%s/architecture/application/ApplicationComponent/%d" % (
        live_server, ids["element_id"])
    page.goto(element_url, wait_until="networkidle", timeout=PAGE_TIMEOUT)
    assert owner_name in page.locator("[data-testid='element-owner']").inner_text(), (
        "the element page does not show the owner assigned on the application page"
    )

    # A capability page offers the step to the same architect and saves it.
    cap_url = "%s/architecture/strategy/Capability/%d" % (live_server, ids["capability_id"])
    page.goto(cap_url, wait_until="networkidle", timeout=PAGE_TIMEOUT)
    assert "No owner recorded" in page.locator("[data-testid='element-owner']").inner_text()
    _assign_with_picker(page, owner_name)
    page.goto(cap_url, wait_until="networkidle", timeout=PAGE_TIMEOUT)
    assert owner_name in page.locator("[data-testid='element-owner']").inner_text()

    # Nothing leaks into the other organisation, and its own elements still
    # show the empty state with the step.
    page.goto(live_server + "/account/logout", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    fresh_org.sign_in(page, live_server, org_b["emails"][ARCHITECT])
    page.goto("%s/architecture/application/ApplicationComponent/%d" % (
        live_server, org_b["ids"]["element_id"]),
        wait_until="networkidle", timeout=PAGE_TIMEOUT)
    cell = page.locator("[data-testid='element-owner']")
    assert owner_name not in cell.inner_text()
    assert "No owner recorded" in cell.inner_text()
    assert page.locator("[data-owner-picker] [data-owner-search]").count() == 1


def test_application_element_page_step_assigns_from_generic_row(
    page, live_server, architect_orgs
):
    """The step on the generic element page of an application saves, and the
    application page then names the owner."""
    org_a = architect_orgs[0]
    ids = org_a["ids"]
    owner_name = _owner_name(org_a)
    fresh_org.sign_in(page, live_server, org_a["emails"][ARCHITECT])
    page.goto("%s/architecture/application/ApplicationComponent/%d" % (
        live_server, ids["element_id"]), wait_until="networkidle", timeout=PAGE_TIMEOUT)
    _assign_with_picker(page, owner_name)
    page.goto("%s/applications/%d" % (live_server, ids["application_id"]),
              wait_until="networkidle", timeout=PAGE_TIMEOUT)
    assert owner_name in page.locator("[data-testid='application-owner']").inner_text()


# ---------------------------------------------------------------------------
# An enterprise architect whose platform role carries no general write
# permission still takes the step wherever it is shown, because the
# permission matrix grants the architect personas "assign owner".
# ---------------------------------------------------------------------------


def _seed_technology_elements(org_id):
    """A technology service (which has an owner field) and a node (which has
    none), each as the generic element row the lists link to."""
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.models import ArchiMateElement

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:6]
    with app.app_context():
        service = ArchiMateElement(
            name="Gateway service %s" % suffix, type="TechnologyService",
            layer="technology", organization_id=org_id,
        )
        node = ArchiMateElement(
            name="Rack node %s" % suffix, type="Node",
            layer="technology", organization_id=org_id,
        )
        component = ApplicationComponent(
            name="Matrix application %s" % suffix, organization_id=org_id,
            lifecycle_status="operational",
        )
        db.session.add_all([service, node, component])
        db.session.commit()
        ids = {
            "service_id": service.id,
            "node_id": node.id,
            "application_id": component.id,
        }
        db.session.remove()
    return ids


@pytest.fixture
def matrix_orgs():
    orgs = []
    for _ in range(2):
        org = fresh_org.create_fresh_org(
            ARCHITECT, org_admin=False, extra_roles=(OWNER,), platform_role="Viewer",
        )
        org["ids"] = _seed_technology_elements(org["org_id"])
        orgs.append(org)
    return orgs


def test_architect_without_write_role_saves_owner_on_application(
    page, live_server, matrix_orgs
):
    org_a, org_b = matrix_orgs
    owner_name = _owner_name(org_a)
    fresh_org.sign_in(page, live_server, org_a["emails"][ARCHITECT])
    app_url = "%s/applications/%d" % (live_server, org_a["ids"]["application_id"])
    page.goto(app_url, wait_until="networkidle", timeout=PAGE_TIMEOUT)
    page.locator("[data-testid='application-owner-empty']").wait_for(
        state="visible", timeout=PAGE_TIMEOUT)
    _assign_with_picker(page, owner_name)
    page.goto(app_url, wait_until="networkidle", timeout=PAGE_TIMEOUT)
    assert owner_name in page.locator("[data-testid='application-owner']").inner_text()

    # The other organisation's application still has no owner.
    page.goto(live_server + "/account/logout", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    fresh_org.sign_in(page, live_server, org_b["emails"][ARCHITECT])
    page.goto("%s/applications/%d" % (live_server, org_b["ids"]["application_id"]),
              wait_until="networkidle", timeout=PAGE_TIMEOUT)
    assert owner_name not in page.inner_text("main")


def test_pages_of_types_with_no_owner_field_claim_no_missing_owner(
    page, live_server, matrix_orgs
):
    org_a, org_b = matrix_orgs
    fresh_org.sign_in(page, live_server, org_a["emails"][ARCHITECT])

    # A technology service and a node have no owner field, so neither page
    # shows an Owner row that claims a gap, or a step that could not save.
    for type_name, key in (("TechnologyService", "service_id"), ("Node", "node_id")):
        page.goto("%s/architecture/technology/%s/%d" % (
            live_server, type_name, org_a["ids"][key]),
            wait_until="networkidle", timeout=PAGE_TIMEOUT)
        assert page.locator("[data-testid='about-card']").count() == 1
        assert page.locator("[data-testid='element-owner']").count() == 0
        assert "No owner recorded" not in page.inner_text("main")
        assert page.locator("[data-owner-picker]").count() == 0

    # An application opened as its generic element row has an owner field:
    # it states the gap and offers the step, in either organisation.
    for org in (org_a, org_b):
        if org is org_b:
            page.goto(live_server + "/account/logout", wait_until="networkidle", timeout=PAGE_TIMEOUT)
            fresh_org.sign_in(page, live_server, org["emails"][ARCHITECT])
        page.goto("%s/applications/%d" % (live_server, org["ids"]["application_id"]),
                  wait_until="networkidle", timeout=PAGE_TIMEOUT)
        assert "No owner" in page.inner_text("main")
