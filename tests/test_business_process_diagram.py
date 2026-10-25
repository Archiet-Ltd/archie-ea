"""A business process drawn as BPMN 2.0: stored on the process, lanes are the model's business roles.

The flow lives on BusinessProcess.bpmn_flow; export and import go through BPMN 2.0 XML and must give the
same drawing back. Every route is tested with two organisations, so one cannot read or change the other's.
"""
import uuid

import pytest
from lxml import etree

pytestmark = pytest.mark.usefixtures("db_session")

NS = {"b": "http://www.omg.org/spec/BPMN/20100524/MODEL", "di": "http://www.omg.org/spec/BPMN/20100524/DI"}


def _role(db_session, org, name):
    from app.models import ArchiMateElement

    el = ArchiMateElement(name=name, type="BusinessRole", layer="business", organization_id=org.id)
    db_session.add(el)
    db_session.flush()
    return el


def _architect(db_session, org, role="business_architect"):
    from app.models.user import User

    suffix = uuid.uuid4().hex[:8]
    user = User(email="bpmn.%s@example.com" % suffix, first_name="Bea", last_name="Analyst",
                organization_id=org.id, enterprise_role=role, confirmed=True)
    user.password = "Passw0rd!x1"
    db_session.add(user)
    db_session.flush()
    return user


def _process(db_session, org, name="Order to delivery"):
    from app.models.process_data import BusinessProcess

    p = BusinessProcess(name=name, organization_id=org.id, level=2)
    db_session.add(p)
    db_session.flush()
    return p


def _seeded(db_session, org):
    from app.modules.business_architecture import bpmn_service

    roles = [_role(db_session, org, n) for n in ("Service Desk Operator", "Logistics Planner", "Quality Assurance Lead")]
    process = _process(db_session, org)
    flow = bpmn_service.save_flow(process, bpmn_service.order_to_delivery_flow([(r.id, r.name) for r in roles]), org.id)
    return process, roles, flow


def test_xml_export_import_gives_the_same_drawing(db_session, make_org, tenant_ctx):
    from app.modules.business_architecture import bpmn_service

    org = make_org("a")
    with tenant_ctx(org.id):
        process, roles, flow = _seeded(db_session, org)
        xml = bpmn_service.to_xml(flow, process.name)
        back, notes = bpmn_service.from_xml(xml, org.id, process)
        assert back == flow
        assert notes == []
        assert bpmn_service.to_xml(back, process.name) == xml


def test_exported_xml_is_bpmn_with_every_kind_of_shape(db_session, make_org, tenant_ctx):
    from app.modules.business_architecture import bpmn_service

    org = make_org("a")
    with tenant_ctx(org.id):
        process, roles, flow = _seeded(db_session, org)
        root = etree.fromstring(bpmn_service.to_xml(flow, process.name).encode())
        found = {etree.QName(el).localname for el in root.iter() if isinstance(el.tag, str)}
        for tag in ("startEvent", "intermediateCatchEvent", "endEvent", "task", "subProcess", "exclusiveGateway",
                    "parallelGateway", "eventBasedGateway", "sequenceFlow", "messageFlow", "participant", "lane",
                    "BPMNShape", "BPMNEdge"):
            assert tag in found, tag
        lanes = root.findall(".//b:lane", NS)
        assert [l.get("name") for l in lanes][:3] == ["Service Desk Operator", "Logistics Planner", "Quality Assurance Lead"]
        gateway = root.find(".//b:exclusiveGateway", NS)
        labels = sorted(f.get("name") for f in root.findall(".//b:sequenceFlow", NS) if f.get("sourceRef") == gateway.get("id"))
        assert labels == ["No", "Yes"]


def test_a_lane_is_a_business_role_of_this_organisation_only(db_session, make_org, tenant_ctx):
    from app.modules.business_architecture import bpmn_service

    org_a, org_b = make_org("a"), make_org("b")
    with tenant_ctx(org_b.id):
        foreign = _role(db_session, org_b, "Foreign Role")
    with tenant_ctx(org_a.id):
        process = _process(db_session, org_a)
        flow = bpmn_service.empty_flow("P")
        flow["pools"][0]["lanes"][0]["roleId"] = foreign.id
        with pytest.raises(bpmn_service.FlowError, match="business role in your model"):
            bpmn_service.save_flow(process, flow, org_a.id)
        # a role that is not a BusinessRole is refused as well
        from app.models import ArchiMateElement
        actor = ArchiMateElement(name="An actor", type="BusinessActor", layer="business", organization_id=org_a.id)
        db_session.add(actor)
        db_session.flush()
        flow["pools"][0]["lanes"][0]["roleId"] = actor.id
        with pytest.raises(bpmn_service.FlowError):
            bpmn_service.save_flow(process, flow, org_a.id)


def test_saving_links_each_lane_role_to_the_process_in_the_model(db_session, make_org, tenant_ctx):
    from app.models import ArchiMateRelationship

    org = make_org("a")
    with tenant_ctx(org.id):
        process, roles, flow = _seeded(db_session, org)
        rels = ArchiMateRelationship.query.filter_by(target_id=process.archimate_element_id, type="Assignment").all()
        assert {r.source_id for r in rels} == {r.id for r in roles}


def test_a_subprocess_shape_is_a_child_process(db_session, make_org, tenant_ctx):
    from app.models.process_data import BusinessProcess

    org = make_org("a")
    with tenant_ctx(org.id):
        process, roles, flow = _seeded(db_session, org)
        sub = next(n for n in flow["nodes"] if n["type"] == "subprocess")
        child = db_session.get(BusinessProcess, sub["processId"])
        assert child.parent_process_id == process.id
        assert child.name == "Pack and inspect order"
        assert child.archimate_element_id


def test_message_and_sequence_flows_keep_to_their_own_kind(db_session, make_org, tenant_ctx):
    from app.modules.business_architecture import bpmn_service

    org = make_org("a")
    with tenant_ctx(org.id):
        process, roles, flow = _seeded(db_session, org)
        bad = dict(flow["flows"][0], type="message")
        with pytest.raises(bpmn_service.FlowError, match="joins two pools"):
            bpmn_service.normalise(dict(flow, flows=[bad]), org.id, process)
        crossing = {"id": "x1", "type": "sequence", "source": "order", "target": "receive", "label": ""}
        with pytest.raises(bpmn_service.FlowError, match="inside one pool"):
            bpmn_service.normalise(dict(flow, flows=[crossing]), org.id, process)


def test_import_names_what_it_cannot_draw_and_what_is_not_linked(db_session, make_org, tenant_ctx):
    from app.modules.business_architecture import bpmn_service

    org = make_org("a")
    with tenant_ctx(org.id):
        process, roles, flow = _seeded(db_session, org)
        xml = bpmn_service.to_xml(flow, process.name)
        with pytest.raises(bpmn_service.FlowError, match="not BPMN"):
            bpmn_service.from_xml("<html/>", org.id, process)
        with pytest.raises(bpmn_service.FlowError, match="not BPMN 2.0 XML the editor can read"):
            bpmn_service.from_xml("not xml", org.id, process)
        inclusive = xml.replace("exclusiveGateway", "inclusiveGateway")
        with pytest.raises(bpmn_service.FlowError, match="inclusiveGateway"):
            bpmn_service.from_xml(inclusive, org.id, process)
        renamed = xml.replace('name="Logistics Planner" entelim:roleId="%d"' % roles[1].id, 'name="Dispatcher"')
        back, notes = bpmn_service.from_xml(renamed, org.id, process)
        assert notes == ["The lane “Dispatcher” is not linked to a business role in your model."]
        assert back["pools"][0]["lanes"][1]["roleId"] is None


def test_xml_with_an_entity_is_refused(db_session, make_org, tenant_ctx):
    from app.modules.business_architecture import bpmn_service

    org = make_org("a")
    with tenant_ctx(org.id):
        evil = ('<?xml version="1.0"?><!DOCTYPE d [<!ENTITY e "boom">]>'
                '<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL" name="&e;"/>')
        with pytest.raises(bpmn_service.FlowError):
            bpmn_service.from_xml(evil, org.id)


# ── over HTTP, with two organisations ─────────────────────────────────────────────────────────────

def test_one_organisation_cannot_read_or_change_anothers_diagram(db_session, make_org, client, login_as):
    org_a, org_b = make_org("a"), make_org("b")
    process_a, roles_a, flow_a = _seeded(db_session, org_a)
    user_b = _architect(db_session, org_b)
    login_as(client, user_b)
    base = "/business-architecture/processes/%d" % process_a.id
    assert client.get(base + "/flow").status_code == 404
    assert client.get(base + "/diagram").status_code == 404
    assert client.get(base + "/bpmn.xml").status_code == 404
    assert client.put(base + "/flow", json=flow_a).status_code == 404
    login_as(client, user_b)
    assert client.post(base + "/bpmn", data={}).status_code == 404
    login_as(client, user_b)
    page = client.get("/business-architecture/processes").get_data(as_text=True)
    assert "Order to delivery" not in page


def test_the_architect_saves_exports_and_imports_over_http(db_session, make_org, client, login_as):
    import io

    org = make_org("a")
    process, roles, flow = _seeded(db_session, org)
    user = _architect(db_session, org)
    assert user.enterprise_role == "business_architect" and not user.is_platform_admin and not user.is_org_admin
    base = "/business-architecture/processes/%d" % process.id
    login_as(client, user)
    saved = client.get(base + "/flow").get_json()
    assert saved == flow
    saved["flows"] = [f for f in saved["flows"] if f["id"] != "f16"]
    login_as(client, user)
    r = client.put(base + "/flow", json=saved)
    assert r.status_code == 200 and len(r.get_json()["flows"]) == len(flow["flows"]) - 1
    login_as(client, user)
    exported = client.get(base + "/bpmn.xml")
    assert exported.status_code == 200 and "attachment" in exported.headers["Content-Disposition"]
    login_as(client, user)
    r = client.post(base + "/bpmn", data={"file": (io.BytesIO(exported.data), "p.bpmn")}, content_type="multipart/form-data")
    assert r.status_code == 200 and r.get_json()["flow"] == saved
    login_as(client, user)
    bad = client.post(base + "/bpmn", data={"file": (io.BytesIO(b"nope"), "p.bpmn")}, content_type="multipart/form-data")
    assert bad.status_code == 400 and "BPMN" in bad.get_json()["error"]


def test_a_person_without_edit_rights_can_read_but_not_change(db_session, make_org, client, login_as):
    org = make_org("a")
    process, roles, flow = _seeded(db_session, org)
    from app.models.org_role import OrgRole

    viewer = _architect(db_session, org, role="procurement")
    OrgRole.set_role(org.id, viewer.id, "viewer")
    assert not viewer.is_platform_admin and not viewer.is_org_admin
    login_as(client, viewer)
    base = "/business-architecture/processes/%d" % process.id
    assert client.get(base + "/flow").status_code == 200
    login_as(client, viewer)
    assert client.put(base + "/flow", json=flow).status_code == 403
