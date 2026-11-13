"""The Vendors page counts the suppliers the model links to systems, beside the vendors recorded.

Two organisations throughout: B's suppliers never count on A's page. A system's count on the
page is the count on its own Vendors tab, and recording a supplier from the page moves it into
the recorded vendors on both.
"""

import re
import uuid

import pytest

from tests.test_application_model_links import VENDOR, _as, _associate, _user

PAGE = "/applications/vendors"


def _estate(tenant_ctx, org, name, label, actors=3):
    from app import db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement

    suffix = uuid.uuid4().hex[:6]
    with tenant_ctx(org.id):
        application = ApplicationComponent(name=name, organization_id=org.id, lifecycle_status="operational")
        people = [
            ArchiMateElement(name="%s Supplier %d %s" % (label, i, suffix), type="Business Actor",
                             layer="Business", organization_id=org.id)
            for i in range(actors)
        ]
        db.session.add_all([application] + people)
        db.session.flush()
        element = db.session.get(ArchiMateElement, application.archimate_element_id)
        for person in people:
            _associate(element, person, org.id)
        db.session.flush()
        return {"app": application.id, "name": name, "actors": [p.id for p in people],
                "actor_names": [p.name for p in people]}


@pytest.fixture
def two_orgs(app, db_session, make_org, tenant_ctx):
    org_a, org_b = make_org("portfolio-a"), make_org("portfolio-b")
    return {
        "a": org_a,
        "user_a": _user(db_session, org_a),
        "estate_a": _estate(tenant_ctx, org_a, "Ledger %s" % uuid.uuid4().hex[:5], "A"),
        "estate_b": _estate(tenant_ctx, org_b, "Ledger %s" % uuid.uuid4().hex[:5], "B", actors=2),
    }


def _text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def test_linked_suppliers_are_counted_not_reported_as_none(app, login_as, two_orgs):
    a, b = two_orgs["estate_a"], two_orgs["estate_b"]
    client = _as(app, login_as, two_orgs["user_a"])
    html = client.get(PAGE).get_data(as_text=True)
    text = _text(html)
    assert "3 suppliers linked in the model to 1 system, none recorded as the vendor" in text
    assert "name a vendor yet" not in text
    for name in a["actor_names"]:
        assert name in html
    assert a["name"] in html
    assert "Record as vendor" in html
    # Organisation B's suppliers and system never reach A's page.
    for name in b["actor_names"] + [b["name"]]:
        assert name not in html


def test_the_page_and_the_vendors_tab_state_one_count(app, login_as, two_orgs):
    a = two_orgs["estate_a"]
    client = _as(app, login_as, two_orgs["user_a"])
    page = _text(client.get(PAGE).get_data(as_text=True))
    assert "3 on its Vendors tab" in page
    login_as(client, two_orgs["user_a"])
    tab = client.get("/applications/%d?tab=vendors" % a["app"]).get_data(as_text=True)
    assert tab.count('data-testid="supplier-linked-row"') == 3


def test_recording_from_the_page_agrees_with_the_tab(app, login_as, two_orgs):
    a = two_orgs["estate_a"]
    client = _as(app, login_as, two_orgs["user_a"])
    assert client.post(VENDOR % a["app"], json={"element_id": a["actors"][0]}).status_code == 201
    login_as(client, two_orgs["user_a"])
    page = _text(client.get(PAGE).get_data(as_text=True))
    assert "2 suppliers linked in the model to 1 system, none recorded as the vendor" in page
    assert "3 on its Vendors tab, %s recorded" % a["actor_names"][0] in page
    login_as(client, two_orgs["user_a"])
    tab = client.get("/applications/%d?tab=vendors" % a["app"]).get_data(as_text=True)
    assert tab.count('data-testid="supplier-linked-row"') == 2
    assert tab.count('data-testid="vendor-organisation-row"') == 1


def test_a_read_only_account_is_not_offered_the_record_step(app, db_session, login_as, two_orgs):
    viewer = _user(db_session, two_orgs["a"], role_name="Viewer")
    client = _as(app, login_as, viewer)
    html = client.get(PAGE).get_data(as_text=True)
    assert two_orgs["estate_a"]["actor_names"][0] in html
    assert "Record as vendor" not in html


def test_there_is_one_type_normaliser():
    from app.modules.architecture.services import application_technology_links as tech
    from app.services import application_model_links as links

    assert not hasattr(tech, "_normalised_type")
    assert tech.normalised_type is links.normalised_type
    assert links.normalised_type("Business Actor") == links.normalised_type("business_actor") == "businessactor"
