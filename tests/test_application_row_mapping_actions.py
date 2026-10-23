"""Mapping an application from its row on the applications list.

The unified mapping modal works from a target (a capability, a process, a vendor
product, an element) to the applications that use it. From an application row the
person picks the target first and the application is already ticked on the next
step. Each of the four row actions must therefore:

  1. list its targets (the modal's first step),
  2. list the organisation's applications for the chosen target (second step),
  3. save the mapping the way the modal sends it, and
  4. show the saved mapping on the application's own detail page.

Two organisations throughout: another organisation's application is never
listed for, or mapped by, this organisation's user.
"""

import pathlib
import uuid

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _user(db_session, org, enterprise_role="enterprise_architect"):
    from app.models.user import Role, User

    Role.insert_roles()
    db_session.flush()
    user = User(
        email="rowmap-%s@example.com" % uuid.uuid4().hex[:10],
        first_name="Row",
        last_name="Map",
        organization_id=org.id,
        confirmed=True,
        enterprise_role=enterprise_role,
    )
    user.role = Role.query.filter_by(name="Architect").one()
    db_session.add(user)
    db_session.flush()
    return user


def _estate(db_session, tenant_ctx, org, label):
    """One application and one target of each kind, all in *org*."""
    from app import db
    from app.models.apqc_process import APQCProcess
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement
    from app.models.business_capabilities import BusinessCapability
    from app.models.vendor.vendor_organization import VendorOrganization, VendorProduct

    suffix = uuid.uuid4().hex[:6]
    with tenant_ctx(org.id):
        application = ApplicationComponent(name="%s Ledger %s" % (label, suffix), organization_id=org.id)
        capability = BusinessCapability(name="%s Billing %s" % (label, suffix), organization_id=org.id)
        element = ArchiMateElement(
            name="%s Service %s" % (label, suffix),
            type="BusinessProcess",
            layer="Business",
            organization_id=org.id,
        )
        process = APQCProcess(process_code="9.%s" % suffix, process_name="%s Invoice process %s" % (label, suffix))
        vendor = VendorOrganization(name="%s Vendor %s" % (label, suffix))
        db.session.add_all([application, capability, element, process, vendor])
        db.session.flush()
        product = VendorProduct(vendor_organization_id=vendor.id, name="%s Product %s" % (label, suffix))
        db.session.add(product)
        db.session.flush()
        return {
            "app": application.id,
            "app_name": application.name,
            "capability": capability.id,
            "capability_name": capability.name,
            "element": element.id,
            "element_name": element.name,
            "process": process.id,
            "process_name": process.process_name,
            "product": product.id,
            "product_name": product.name,
        }


@pytest.fixture
def two_orgs(app, db_session, make_org, tenant_ctx):
    org_a, org_b = make_org("rowmap-a"), make_org("rowmap-b")
    return {
        "user_a": _user(db_session, org_a),
        "user_b": _user(db_session, org_b),
        "viewer_a": _user(db_session, org_a, enterprise_role="non_technical_owner"),
        "estate_a": _estate(db_session, tenant_ctx, org_a, "A"),
        "estate_b": _estate(db_session, tenant_ctx, org_b, "B"),
    }


def _as(app, login_as, user):
    client = app.test_client()
    login_as(client, user)
    return client


def _application_ids(payload):
    return {int(a["id"]) for a in payload["applications"]}


def _detail_page(client, app_id):
    response = client.get("/applications/%d" % app_id)
    assert response.status_code == 200
    return response.get_data(as_text=True)


# The first step of the modal: the targets, as the modal asks for them.
TARGET_LISTS = {
    "capability": "/capability-map/api/unified-capabilities",
    "apqc": "/api/apqc/tree",
    "vendor": "/dashboard/api/vendors/products",
    "archimate": "/solutions/api/archimate-all-elements",
}


def _contains_name(response, name):
    return name in response.get_data(as_text=True)


@pytest.mark.parametrize(
    "context,name_key",
    [
        ("capability", "capability_name"),
        ("vendor", "product_name"),
        ("archimate", "element_name"),
    ],
)
def test_first_step_lists_the_targets(app, login_as, two_orgs, context, name_key):
    client = _as(app, login_as, two_orgs["user_a"])
    response = client.get(TARGET_LISTS[context])
    assert response.status_code == 200
    assert _contains_name(response, two_orgs["estate_a"][name_key])


def test_first_step_lists_the_processes(app, login_as, two_orgs):
    client = _as(app, login_as, two_orgs["user_a"])
    response = client.get(TARGET_LISTS["apqc"])
    assert response.status_code == 200
    assert response.get_json()["success"] is True


# The second step: the organisation's applications for the chosen target.
def _second_step_url(context, estate):
    if context == "capability":
        return "/capability-map/api/capability/%d/applications" % estate["capability"]
    if context == "apqc":
        return "/api/apqc/process/%d/applications" % estate["process"]
    if context == "vendor":
        return "/api/vendors/%d/applications" % estate["product"]
    return "/applications/api/list"


@pytest.mark.parametrize("context", ["capability", "apqc", "vendor", "archimate"])
def test_second_step_lists_this_organisations_application_only(app, login_as, two_orgs, context):
    client = _as(app, login_as, two_orgs["user_a"])
    response = client.get(_second_step_url(context, two_orgs["estate_a"]))
    assert response.status_code == 200, response.get_data(as_text=True)[:300]
    ids = _application_ids(response.get_json())
    assert two_orgs["estate_a"]["app"] in ids
    assert two_orgs["estate_b"]["app"] not in ids


# Saving the mapping the way the modal sends it, then reading it on the detail page.
def _modal_body(context, estate):
    application = {"application_id": estate["app"], "mapping": {}}
    if context == "capability":
        return "/capability-map/api/mappings", {"capability_id": estate["capability"], "applications": [application], "context": context}
    if context == "apqc":
        return "/api/apqc/process-mappings", {"process_id": estate["process"], "applications": [application], "context": context}
    if context == "vendor":
        return "/capability-map/api/vendors/application-mappings", {"vendor_product_id": estate["product"], "applications": [application], "context": context}
    return "/capability-map/api/save-archimate-mappings", {"element_id": estate["element"], "applications": [application], "context": context}


SHOWN_ON_DETAIL = {
    "capability": "capability_name",
    "apqc": "process_name",
    "vendor": "product_name",
    "archimate": "element_name",
}


@pytest.mark.parametrize("context", ["capability", "apqc", "vendor", "archimate"])
def test_a_saved_mapping_appears_on_the_application_detail_page(app, login_as, two_orgs, context):
    estate = two_orgs["estate_a"]
    name = estate[SHOWN_ON_DETAIL[context]]
    client = _as(app, login_as, two_orgs["user_a"])
    # Some names already appear in a pick list on the page; a saved mapping adds one more.
    shown_before = _detail_page(client, estate["app"]).count(name)

    url, body = _modal_body(context, estate)
    saved = client.post(url, json=body)
    assert saved.status_code == 200, saved.get_data(as_text=True)[:300]
    assert saved.get_json().get("success", True) is not False

    assert _detail_page(client, estate["app"]).count(name) > shown_before


@pytest.mark.parametrize("context", ["capability", "apqc", "vendor", "archimate"])
def test_another_organisations_application_is_not_mapped(app, login_as, two_orgs, context):
    """User B names organisation A's application: nothing is written for it."""
    estate_a, estate_b = two_orgs["estate_a"], two_orgs["estate_b"]
    name = estate_b[SHOWN_ON_DETAIL[context]]
    owner = _as(app, login_as, two_orgs["user_a"])
    shown_before = _detail_page(owner, estate_a["app"]).count(name)

    mixed = dict(estate_b, app=estate_a["app"])
    client = _as(app, login_as, two_orgs["user_b"])
    url, body = _modal_body(context, mixed)
    client.post(url, json=body)

    owner = _as(app, login_as, two_orgs["user_a"])
    assert _detail_page(owner, estate_a["app"]).count(name) == shown_before


# The application list page: the row action.
def test_a_viewer_is_refused_before_the_dialog_opens(app, login_as, two_orgs):
    estate = two_orgs["estate_a"]
    viewer = _as(app, login_as, two_orgs["viewer_a"])
    html = viewer.get("/applications/").get_data(as_text=True)
    assert 'data-app-map-id="%d"' % estate["app"] in html
    assert 'data-can-map="false"' in html
    assert 'data-can-map="true"' not in html

    editor = _as(app, login_as, two_orgs["user_a"])
    html = editor.get("/applications/").get_data(as_text=True)
    assert 'data-can-map="true"' in html
    assert 'data-can-map="false"' not in html


def test_the_row_action_starts_from_the_target_not_the_application_id():
    source = (ROOT / "app/static/js/applications/list.js").read_text()
    start = source.index("openMappingForApp(id, name, mappingType)")
    action = source[start : source.index("// Fallback: navigate to the mapping page", start)]
    assert "openUnifiedMappingModalDiscovery" in action
    assert "ids: [Number(id)]" in action
    assert "Your role can view mappings but cannot change them." in action
    assert action.index("cannot change them") < action.index("openUnifiedMappingModalDiscovery({")


def test_the_modal_still_opens_on_a_capability_with_that_capability_as_target():
    """The capability page's own flow is unchanged: target first, applications next."""
    source = (ROOT / "app/static/js/components/unified_mapping_modal.js").read_text()
    assert "UnifiedMappingModal.targetId = String(targetId);" in source
    assert "UnifiedMappingModal.apiEndpoint + '/capability/' + id + '/applications'" in source
    assert "url = '/api/apqc/process/' + id + '/applications';" in source
    assert "url = '/api/vendors/' + id + '/applications';" in source


def test_the_modal_ticks_the_row_application_and_saves_vendor_links_at_the_live_address():
    source = (ROOT / "app/static/js/components/unified_mapping_modal.js").read_text()
    assert "preIds.map(String).indexOf(String(app.id))" in source
    assert "url = '/capability-map/api/vendors/application-mappings';" in source
