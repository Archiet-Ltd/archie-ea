"""An application mapped to the nodes and system software it runs on.

Each link is a real ArchiMate realization relationship (technology element ->
application element), so the impact answer walks from the node to the
application with no change of its own. Two organisations throughout: a link
written in A is never listed, removed or reachable from B.
"""

import uuid

import pytest

BASE = "/architecture/api/applications/%d/technology-links"


def _role(db_session, name):
    from app.models.user import Role

    Role.insert_roles()
    db_session.flush()
    return Role.query.filter_by(name=name).one()


def _user(db_session, org, role_name="Architect", enterprise_role="enterprise_architect"):
    from app.models.user import User

    user = User(
        email="techlinks-%s@example.com" % uuid.uuid4().hex[:10],
        first_name="Tech",
        last_name="Links",
        organization_id=org.id,
        confirmed=True,
        enterprise_role=enterprise_role,
    )
    user.role = _role(db_session, role_name)
    db_session.add(user)
    db_session.flush()
    return user


def _estate(db_session, tenant_ctx, org, label):
    """One application, one node, one piece of system software and one element
    that is neither, all in *org*."""
    from app import db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement
    from app.models.technology_layer import Node, SystemSoftware

    with tenant_ctx(org.id):
        application = ApplicationComponent(
            name="%s Ledger %s" % (label, uuid.uuid4().hex[:6]), organization_id=org.id
        )
        node = Node(name="%s Host %s" % (label, uuid.uuid4().hex[:6]), organization_id=org.id)
        software = SystemSoftware(
            name="%s Postgres %s" % (label, uuid.uuid4().hex[:6]), organization_id=org.id
        )
        other = ArchiMateElement(
            name="%s Goal %s" % (label, uuid.uuid4().hex[:6]),
            type="Goal",
            layer="Motivation",
            organization_id=org.id,
        )
        db.session.add_all([application, node, software, other])
        db.session.flush()
        assert application.archimate_element_id and node.archimate_element_id
        return {
            "app": application.id,
            "app_element": application.archimate_element_id,
            "node": node.archimate_element_id,
            "node_name": node.name,
            "software": software.archimate_element_id,
            "software_name": software.name,
            "other": other.id,
        }


@pytest.fixture
def two_orgs(app, db_session, make_org, tenant_ctx):
    org_a, org_b = make_org("techlinks-a"), make_org("techlinks-b")
    return {
        "a": org_a,
        "b": org_b,
        "user_a": _user(db_session, org_a),
        "user_b": _user(db_session, org_b),
        "estate_a": _estate(db_session, tenant_ctx, org_a, "A"),
        "estate_b": _estate(db_session, tenant_ctx, org_b, "B"),
    }


def _as(app, login_as, user):
    client = app.test_client()
    login_as(client, user)
    return client


def test_adding_a_node_writes_a_realization_relationship_and_lists_it(app, login_as, two_orgs):
    from app import db
    from app.models.archimate_core import ArchiMateRelationship

    estate = two_orgs["estate_a"]
    client = _as(app, login_as, two_orgs["user_a"])

    before = client.get(BASE % estate["app"])
    assert before.status_code == 200
    assert before.get_json()["links"] == []

    created = client.post(BASE % estate["app"], json={"element_id": estate["node"]})
    assert created.status_code == 201, created.get_json()
    link = created.get_json()["link"]
    assert link["name"] == estate["node_name"]

    rel = db.session.execute(
        db.select(ArchiMateRelationship).where(ArchiMateRelationship.id == link["relationship_id"])
    ).scalar_one()
    assert (rel.type, rel.source_id, rel.target_id) == (
        "realization", estate["node"], estate["app_element"])
    assert rel.organization_id == two_orgs["a"].id

    login_as(client, two_orgs["user_a"])
    listed = client.get(BASE % estate["app"]).get_json()["links"]
    assert [row["element_id"] for row in listed] == [estate["node"]]


def test_system_software_is_accepted_and_other_elements_are_refused(app, login_as, two_orgs):
    estate = two_orgs["estate_a"]
    client = _as(app, login_as, two_orgs["user_a"])

    ok = client.post(BASE % estate["app"], json={"element_id": estate["software"]})
    assert ok.status_code == 201
    assert ok.get_json()["link"]["type_label"] == "System software"

    login_as(client, two_orgs["user_a"])
    refused = client.post(BASE % estate["app"], json={"element_id": estate["other"]})
    assert refused.status_code == 400
    assert refused.get_json()["error"] == "Choose a node or system software."

    login_as(client, two_orgs["user_a"])
    again = client.post(BASE % estate["app"], json={"element_id": estate["software"]})
    assert again.status_code == 409


def test_organisation_b_never_sees_or_touches_organisation_a_links(app, login_as, two_orgs):
    a, b = two_orgs["estate_a"], two_orgs["estate_b"]
    client_a = _as(app, login_as, two_orgs["user_a"])
    rel_id = client_a.post(BASE % a["app"], json={"element_id": a["node"]}).get_json()["link"][
        "relationship_id"
    ]

    client_b = _as(app, login_as, two_orgs["user_b"])
    assert client_b.get(BASE % a["app"]).status_code == 404

    login_as(client_b, two_orgs["user_b"])
    assert client_b.get(BASE % b["app"]).get_json()["links"] == []

    # B cannot map A's node onto B's own application, nor write onto A's.
    login_as(client_b, two_orgs["user_b"])
    assert client_b.post(BASE % b["app"], json={"element_id": a["node"]}).status_code == 404
    login_as(client_b, two_orgs["user_b"])
    assert client_b.post(BASE % a["app"], json={"element_id": b["node"]}).status_code == 404

    # B cannot remove A's link through either application.
    login_as(client_b, two_orgs["user_b"])
    assert client_b.delete((BASE % a["app"]) + "/%d" % rel_id).status_code == 404
    login_as(client_b, two_orgs["user_b"])
    assert client_b.delete((BASE % b["app"]) + "/%d" % rel_id).status_code == 404

    login_as(client_a, two_orgs["user_a"])
    assert [r["relationship_id"] for r in client_a.get(BASE % a["app"]).get_json()["links"]] == [
        rel_id
    ]


def test_removing_a_link_takes_it_off_the_list(app, login_as, two_orgs):
    estate = two_orgs["estate_a"]
    client = _as(app, login_as, two_orgs["user_a"])
    rel_id = client.post(BASE % estate["app"], json={"element_id": estate["node"]}).get_json()[
        "link"]["relationship_id"]

    login_as(client, two_orgs["user_a"])
    assert client.delete((BASE % estate["app"]) + "/%d" % rel_id).status_code == 200
    login_as(client, two_orgs["user_a"])
    assert client.get(BASE % estate["app"]).get_json()["links"] == []


def test_a_read_only_account_cannot_write_a_link(app, db_session, login_as, two_orgs):
    estate = two_orgs["estate_a"]
    viewer = _user(db_session, two_orgs["a"], role_name="Viewer")
    client = _as(app, login_as, viewer)
    assert client.post(BASE % estate["app"], json={"element_id": estate["node"]}).status_code == 403


def test_the_impact_answer_puts_the_application_in_the_node_blast_radius(
    app, login_as, tenant_ctx, two_orgs
):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    a = two_orgs["estate_a"]
    client = _as(app, login_as, two_orgs["user_a"])
    assert client.post(BASE % a["app"], json={"element_id": a["node"]}).status_code == 201

    with tenant_ctx(two_orgs["a"].id):
        answer = IntelligenceQueryService.cross_layer_impact(a["node"], include_derived=False)
    reached = {int(k) for k in answer["elements"]}
    assert a["app_element"] in reached

    with tenant_ctx(two_orgs["b"].id):
        foreign = IntelligenceQueryService.cross_layer_impact(a["node"], include_derived=False)
    assert foreign["rows"] == []
