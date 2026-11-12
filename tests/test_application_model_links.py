"""An application's Technology and Vendors tabs see what the model already links to it.

Imported and connector-built models link an application to nodes and supplier
actors by association. Those links must show on the Technology and Vendors tabs
and in the completeness banner, each with one action that records it properly,
and the places that count only recorded facts say so. Two organisations
throughout: B's elements never appear on A's tabs.
"""

import uuid

import pytest

TECH = "/architecture/api/applications/%d/technology-links"
VENDOR = "/dashboard/applications/%d/vendor-link"


def _role(db_session, name):
    from app.models.user import Role

    Role.insert_roles()
    db_session.flush()
    return Role.query.filter_by(name=name).one()


def _user(db_session, org, role_name="Architect"):
    from app.models.user import User

    user = User(
        email="modellinks-%s@example.com" % uuid.uuid4().hex[:10],
        first_name="Model",
        last_name="Links",
        organization_id=org.id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    user.role = _role(db_session, role_name)
    db_session.add(user)
    db_session.flush()
    return user


def _associate(source, target, org_id):
    from app import db
    from app.models.archimate_core import ArchiMateRelationship

    rel = ArchiMateRelationship(
        type="association",
        source_id=source.id,
        target_id=target.id,
        architecture_id=source.architecture_id or target.architecture_id,
        organization_id=org_id,
    )
    db.session.add(rel)
    db.session.flush()
    return rel


def _estate(db_session, tenant_ctx, org, name, label):
    """An application with two association-linked nodes and two association-linked
    supplier actors, all in *org*."""
    from app import db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement
    from app.models.technology_layer import Node

    suffix = uuid.uuid4().hex[:6]
    with tenant_ctx(org.id):
        application = ApplicationComponent(name=name, organization_id=org.id)
        nodes = [Node(name="%s Host %d %s" % (label, i, suffix), organization_id=org.id) for i in (1, 2)]
        actors = [
            ArchiMateElement(
                name="%s Supplier %d %s" % (label, i, suffix), type="BusinessActor",
                layer="Business", organization_id=org.id)
            for i in (1, 2)
        ]
        db.session.add_all([application] + nodes + actors)
        db.session.flush()
        app_element = db.session.get(ArchiMateElement, application.archimate_element_id)
        for node in nodes:
            _associate(db.session.get(ArchiMateElement, node.archimate_element_id), app_element, org.id)
        for actor in actors:
            _associate(app_element, actor, org.id)
        db.session.flush()
        return {
            "app": application.id,
            "nodes": [n.archimate_element_id for n in nodes],
            "node_names": [n.name for n in nodes],
            "actors": [a.id for a in actors],
            "actor_names": [a.name for a in actors],
        }


@pytest.fixture
def two_orgs(app, db_session, make_org, tenant_ctx):
    org_a, org_b = make_org("modellinks-a"), make_org("modellinks-b")
    return {
        "a": org_a,
        "b": org_b,
        "user_a": _user(db_session, org_a),
        "user_b": _user(db_session, org_b),
        # The same application name in both organisations.
        "estate_a": _estate(db_session, tenant_ctx, org_a, "Shared Ledger", "A"),
        "estate_b": _estate(db_session, tenant_ctx, org_b, "Shared Ledger", "B"),
    }


def _as(app, login_as, user):
    client = app.test_client()
    login_as(client, user)
    return client


def test_association_linked_nodes_are_listed_apart_from_runs_on_links(app, login_as, two_orgs):
    a = two_orgs["estate_a"]
    client = _as(app, login_as, two_orgs["user_a"])

    body = client.get(TECH % a["app"]).get_json()
    assert body["links"] == []
    assert sorted(row["element_id"] for row in body["linked"]) == sorted(a["nodes"])
    assert {row["relationship_type"] for row in body["linked"]} == {"association"}
    assert {row["name"] for row in body["linked"]} == set(a["node_names"])


def test_recording_one_moves_it_from_linked_to_runs_on(app, login_as, two_orgs):
    a = two_orgs["estate_a"]
    client = _as(app, login_as, two_orgs["user_a"])

    created = client.post(TECH % a["app"], json={"element_id": a["nodes"][0]})
    assert created.status_code == 201, created.get_json()

    login_as(client, two_orgs["user_a"])
    body = client.get(TECH % a["app"]).get_json()
    assert [row["element_id"] for row in body["links"]] == [a["nodes"][0]]
    assert [row["element_id"] for row in body["linked"]] == [a["nodes"][1]]


def test_organisation_b_nodes_never_appear_on_organisation_a(app, login_as, two_orgs):
    a, b = two_orgs["estate_a"], two_orgs["estate_b"]
    client = _as(app, login_as, two_orgs["user_a"])
    body = client.get(TECH % a["app"]).get_json()
    assert not set(row["element_id"] for row in body["linked"]) & set(b["nodes"])
    assert not {row["name"] for row in body["linked"]} & set(b["node_names"])

    login_as(client, two_orgs["user_a"])
    assert client.get(TECH % b["app"]).status_code == 404


def test_suppliers_linked_in_the_model_are_read_for_the_vendors_tab(app, tenant_ctx, two_orgs):
    from app import db
    from app.models.application_portfolio import ApplicationComponent
    from app.services.application_model_links import linked_suppliers

    a, b = two_orgs["estate_a"], two_orgs["estate_b"]
    with tenant_ctx(two_orgs["a"].id):
        application = db.session.get(ApplicationComponent, a["app"])
        suppliers = linked_suppliers(application)
    assert sorted(row["id"] for row in suppliers) == sorted(a["actors"])
    assert not {row["name"] for row in suppliers} & set(b["actor_names"])


def test_record_as_vendor_adds_one_vendor_organisation_and_leaves_the_other_register_alone(
    app, login_as, tenant_ctx, two_orgs
):
    from app import db
    from app.models.application_portfolio import ApplicationComponent
    from app.services import vendor_register

    a, b = two_orgs["estate_a"], two_orgs["estate_b"]
    with tenant_ctx(two_orgs["b"].id):
        before_b = vendor_register.vendor_ids(two_orgs["b"].id)
    with tenant_ctx(two_orgs["a"].id):
        assert vendor_register.vendor_of(db.session.get(ApplicationComponent, a["app"])) is None

    client = _as(app, login_as, two_orgs["user_a"])
    done = client.post(VENDOR % a["app"], json={"element_id": a["actors"][0]})
    assert done.status_code == 201, done.get_json()
    assert done.get_json()["vendor"]["name"] == a["actor_names"][0]

    with tenant_ctx(two_orgs["a"].id):
        application = db.session.get(ApplicationComponent, a["app"])
        vendor_id = vendor_register.vendor_of(application)
        assert vendor_id is not None
        assert vendor_register.vendor_ids(two_orgs["a"].id) == [vendor_id]
        assert [s.id for s in vendor_register.systems_of(vendor_id, two_orgs["a"].id)] == [a["app"]]
    with tenant_ctx(two_orgs["b"].id):
        assert vendor_register.vendor_ids(two_orgs["b"].id) == before_b
        assert db.session.get(ApplicationComponent, b["app"]).vendor_organization_id is None


def test_a_supplier_that_is_not_linked_to_the_application_is_refused(app, login_as, two_orgs):
    a, b = two_orgs["estate_a"], two_orgs["estate_b"]
    client = _as(app, login_as, two_orgs["user_a"])
    assert client.post(VENDOR % a["app"], json={"element_id": a["actors"][1]}).status_code == 201
    login_as(client, two_orgs["user_a"])
    # B's actor is not A's, and A's actor is not linked to B's application.
    assert client.post(VENDOR % a["app"], json={"element_id": b["actors"][0]}).status_code == 404
    login_as(client, two_orgs["user_a"])
    assert client.post(VENDOR % b["app"], json={"element_id": a["actors"][0]}).status_code == 404


def test_a_read_only_account_cannot_record_a_vendor(app, db_session, login_as, two_orgs):
    a = two_orgs["estate_a"]
    viewer = _user(db_session, two_orgs["a"], role_name="Viewer")
    client = _as(app, login_as, viewer)
    assert client.post(VENDOR % a["app"], json={"element_id": a["actors"][0]}).status_code == 403


def test_the_banner_names_what_is_linked_and_drops_the_note_once_recorded(
    app, login_as, tenant_ctx, two_orgs
):
    from app import db
    from app.models.application_portfolio import ApplicationComponent
    from app.services.application_fact_sheet import build_fact_sheet

    a = two_orgs["estate_a"]
    with tenant_ctx(two_orgs["a"].id):
        sheet = build_fact_sheet(db.session.get(ApplicationComponent, a["app"]))
    notes = sheet["completeness"]["notes"]
    assert notes["Hosting / deployment"]["text"] == "2 technology elements linked, none recorded as runs on"
    assert notes["Hosting / deployment"]["tab"] == "technology"
    assert notes["Vendor"]["text"] == "2 suppliers linked, none recorded as vendor"
    assert notes["Vendor"]["tab"] == "vendors"
    # The score still counts only recorded facts.
    assert "Hosting / deployment" in sheet["completeness"]["missing"]

    client = _as(app, login_as, two_orgs["user_a"])
    assert client.post(TECH % a["app"], json={"element_id": a["nodes"][0]}).status_code == 201
    login_as(client, two_orgs["user_a"])
    assert client.post(VENDOR % a["app"], json={"element_id": a["actors"][0]}).status_code == 201
    with tenant_ctx(two_orgs["a"].id):
        sheet = build_fact_sheet(db.session.get(ApplicationComponent, a["app"]))
    # No ask for the link just recorded: the note now says what the score reads instead.
    hosting = sheet["completeness"]["notes"]["Hosting / deployment"]
    assert "none recorded as runs on" not in hosting["text"]
    assert "the score counts the hosting description" in hosting["text"]
    assert hosting["tab"] == "overview" and hosting["edit"] == "1"
    assert "Vendor" not in sheet["completeness"]["notes"]


def test_the_pages_show_the_links_and_say_what_is_not_counted(app, login_as, two_orgs):
    a, b = two_orgs["estate_a"], two_orgs["estate_b"]
    client = _as(app, login_as, two_orgs["user_a"])

    sheet = client.get("/applications/%d/fact-sheet" % a["app"]).get_data(as_text=True)
    assert "2 technology elements linked, none recorded as runs on" in sheet
    assert "2 suppliers linked, none recorded as vendor" in sheet
    assert "counts only facts recorded" in sheet

    login_as(client, two_orgs["user_a"])
    page = client.get("/applications/%d?tab=vendors" % a["app"]).get_data(as_text=True)
    assert "Suppliers linked in the model" in page
    for name in a["actor_names"]:
        assert name in page
    assert "Record as vendor" in page
    for name in b["actor_names"]:
        assert name not in page
    # The tab label is an enabled tab like the others.
    assert "No Vendors data yet" not in page

    login_as(client, two_orgs["user_a"])
    technology = client.get("/applications/%d?tab=technology" % a["app"]).get_data(as_text=True)
    assert "2 technology elements linked in the model, none recorded as runs on" in technology
    assert "not mapped to any technology yet" not in technology
