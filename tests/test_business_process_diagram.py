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
        assert [ln.get("name") for ln in lanes][:3] == ["Service Desk Operator", "Logistics Planner", "Quality Assurance Lead"]
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


def test_saving_removes_the_links_of_lanes_that_were_unlinked_or_removed(db_session, make_org, tenant_ctx):
    from app.models import ArchiMateRelationship
    from app.modules.business_architecture import bpmn_service

    org_a, org_b = make_org("a"), make_org("b")
    with tenant_ctx(org_a.id):
        process, roles, flow = _seeded(db_session, org_a)
        other_process = _process(db_session, org_a, "Another process")
        other_flow = bpmn_service.order_to_delivery_flow([(r.id, r.name) for r in roles])
        bpmn_service.save_flow(other_process, other_flow, org_a.id)
        # a link someone drew by hand in the model, never from this drawing
        extra = _role(db_session, org_a, "Hand-linked Role")
        db_session.add(ArchiMateRelationship(source_id=extra.id, target_id=process.archimate_element_id,
                                             type="Assignment", organization_id=org_a.id))
        db_session.flush()
        edited = {**flow, "pools": [dict(p, lanes=[dict(ln) for ln in p["lanes"]]) for p in flow["pools"]]}
        edited["pools"][0]["lanes"][0]["roleId"] = None   # unlink the first lane's role
        edited["pools"][0]["lanes"].pop(2)                # remove the third lane
        edited["nodes"] = [n for n in edited["nodes"] if n["lane"] != "lane3"]
        edited["flows"] = []
        bpmn_service.save_flow(process, edited, org_a.id)
        sources = {r.source_id for r in ArchiMateRelationship.query.filter_by(
            target_id=process.archimate_element_id, type="Assignment")}
        assert sources == {roles[1].id, extra.id}
        # the other process in the same organisation keeps all three
        assert {r.source_id for r in ArchiMateRelationship.query.filter_by(
            target_id=other_process.archimate_element_id, type="Assignment")} == {r.id for r in roles}
    with tenant_ctx(org_b.id):
        assert ArchiMateRelationship.query.filter_by(target_id=process.archimate_element_id).count() == 0


def test_taking_a_sub_process_shape_off_the_drawing_detaches_its_child(db_session, make_org, tenant_ctx):
    from app.models.process_data import BusinessProcess

    org = make_org("a")
    with tenant_ctx(org.id):
        process, roles, flow = _seeded(db_session, org)
        from app.modules.business_architecture import bpmn_service
        sub = next(n for n in flow["nodes"] if n["type"] == "subprocess")
        edited = dict(flow, nodes=[n for n in flow["nodes"] if n["id"] != sub["id"]],
                      flows=[f for f in flow["flows"] if sub["id"] not in (f["source"], f["target"])])
        bpmn_service.save_flow(process, edited, org.id)
        child = db_session.get(BusinessProcess, sub["processId"])
        assert child is not None and child.name == "Pack and inspect order"
        assert child.parent_process_id is None
        assert BusinessProcess.query.filter_by(parent_process_id=process.id).count() == 0


def test_a_position_that_is_not_a_number_gives_a_plain_message(db_session, make_org, tenant_ctx):
    from app.modules.business_architecture import bpmn_service

    org = make_org("a")
    with tenant_ctx(org.id):
        process, roles, flow = _seeded(db_session, org)
        xml = bpmn_service.to_xml(flow, process.name)
        broken = xml.replace('<dc:Bounds x="80"', '<dc:Bounds x="abc"', 1)
        assert broken != xml
        with pytest.raises(bpmn_service.FlowError, match="position that is not a number"):
            bpmn_service.from_xml(broken, org.id, process)


def test_a_message_flow_that_ends_at_a_pool_imports_with_the_pool_kept(db_session, make_org, tenant_ctx):
    from app.modules.business_architecture import bpmn_service

    org = make_org("a")
    with tenant_ctx(org.id):
        process, roles, flow = _seeded(db_session, org)
        xml = bpmn_service.to_xml(flow, process.name)
        xml = xml.replace('sourceRef="order" targetRef="receive"', 'sourceRef="pool2" targetRef="receive"')
        back, notes = bpmn_service.from_xml(xml, org.id, process)
        assert [p["id"] for p in back["pools"]] == ["pool1", "pool2"]
        assert [f["id"] for f in back["flows"] if f["type"] == "message"] == ["m2"]
        assert notes == ["The message flow “Order” ends at a pool, not at a shape, so it was not drawn."]
        # a collapsed pool (no process behind it) is a participant too
        collapsed = xml.replace('</collaboration>', '<participant id="bank" name="Bank"/></collaboration>', 1)
        back, _ = bpmn_service.from_xml(collapsed, org.id, process)
        assert [p["name"] for p in back["pools"]] == ["Order to delivery", "Customer", "Bank"]


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


def test_a_sub_process_drawn_again_keeps_its_one_child(db_session, make_org, tenant_ctx):
    from app.models.process_data import BusinessProcess
    from app.modules.business_architecture import bpmn_service

    org = make_org("a")
    with tenant_ctx(org.id):
        process, roles, flow = _seeded(db_session, org)
        again = bpmn_service.order_to_delivery_flow([(r.id, r.name) for r in roles])  # the same drawing, no child ids
        saved = bpmn_service.save_flow(process, again, org.id)
        assert BusinessProcess.query.filter_by(parent_process_id=process.id).count() == 1
        assert next(n for n in saved["nodes"] if n["type"] == "subprocess")["processId"] == next(
            n for n in flow["nodes"] if n["type"] == "subprocess")["processId"]


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


@pytest.mark.parametrize("bad", ["nan", "inf", "-inf", "1e400", "250000"])
def test_bounds_that_are_not_sane_numbers_are_refused_naming_the_shape(db_session, make_org, tenant_ctx, bad):
    from app.modules.business_architecture import bpmn_service

    org = make_org("a")
    with tenant_ctx(org.id):
        process, roles, flow = _seeded(db_session, org)
        xml = bpmn_service.to_xml(flow, process.name)
        broken = xml.replace('<dc:Bounds x="80"', '<dc:Bounds x="%s"' % bad, 1)
        assert broken != xml
        with pytest.raises(bpmn_service.FlowError, match="start"):
            bpmn_service.from_xml(broken, org.id, process)


def test_the_import_route_answers_400_not_500_for_bad_bounds(db_session, make_org, client, login_as):
    import io

    org = make_org("a")
    process, roles, flow = _seeded(db_session, org)
    user = _architect(db_session, org)
    from app.modules.business_architecture import bpmn_service

    xml = bpmn_service.to_xml(flow, process.name)
    for bad in ("nan", "inf", "1e400"):
        broken = xml.replace('<dc:Bounds x="80"', '<dc:Bounds x="%s"' % bad, 1).encode()
        login_as(client, user)
        r = client.post("/business-architecture/processes/%d/bpmn" % process.id,
                        data={"file": (io.BytesIO(broken), "p.bpmn")}, content_type="multipart/form-data")
        assert r.status_code == 400, bad
        assert "start" in r.get_json()["error"]


def test_a_message_flow_to_a_missing_element_is_named_and_the_rest_imports(db_session, make_org, tenant_ctx):
    from app.modules.business_architecture import bpmn_service

    org = make_org("a")
    with tenant_ctx(org.id):
        process, roles, flow = _seeded(db_session, org)
        xml = bpmn_service.to_xml(flow, process.name)
        xml = xml.replace('sourceRef="order" targetRef="receive"', 'sourceRef="order" targetRef="ghost"')
        back, notes = bpmn_service.from_xml(xml, org.id, process)
        assert [f["id"] for f in back["flows"] if f["type"] == "message"] == ["m2"]
        assert len(back["nodes"]) == len(flow["nodes"])
        assert notes == ["The message flow “Order” ends at a shape that is not in the file, so it was not drawn."]


def test_a_role_link_made_by_hand_survives_removing_a_lane_drawn_later(db_session, make_org, tenant_ctx):
    from app.models import ArchiMateRelationship
    from app.modules.business_architecture import bpmn_service

    org = make_org("a")
    with tenant_ctx(org.id):
        process, roles, flow = _seeded(db_session, org)
        late = _role(db_session, org, "Drawn Later")
        db_session.add(ArchiMateRelationship(source_id=late.id, target_id=process.archimate_element_id,
                                             type="Assignment", organization_id=org.id))
        db_session.flush()
        bigger = {**flow, "pools": [dict(p, lanes=[dict(ln) for ln in p["lanes"]]) for p in flow["pools"]]}
        bigger["pools"][0]["lanes"].append({"id": "lane9", "name": "Drawn Later", "roleId": late.id})
        saved = bpmn_service.save_flow(process, bigger, org.id)
        smaller = {**saved, "pools": [dict(p, lanes=[dict(ln) for ln in p["lanes"] if ln["id"] != "lane9"]) for p in saved["pools"]]}
        bpmn_service.save_flow(process, smaller, org.id)
        sources = {r.source_id for r in ArchiMateRelationship.query.filter_by(
            target_id=process.archimate_element_id, type="Assignment")}
        assert late.id in sources and {r.id for r in roles} <= sources


def test_a_parent_link_made_by_hand_survives_taking_the_shape_off(db_session, make_org, tenant_ctx):
    from app.models.process_data import BusinessProcess
    from app.modules.business_architecture import bpmn_service

    org = make_org("a")
    with tenant_ctx(org.id):
        process, roles, flow = _seeded(db_session, org)
        mine = _process(db_session, org, "Hand attached")
        mine.parent_process_id = process.id
        db_session.flush()
        sub = next(n for n in flow["nodes"] if n["type"] == "subprocess")
        pool = flow["nodes"][0]
        shape = dict(sub, id="handsub", label="Hand attached", processId=mine.id, x=sub["x"] + 140)
        saved = bpmn_service.save_flow(process, {**flow, "nodes": flow["nodes"] + [shape]}, org.id)
        assert any(n["id"] == "handsub" and n["processId"] == mine.id for n in saved["nodes"])
        bpmn_service.save_flow(process, {**saved, "nodes": [n for n in saved["nodes"] if n["id"] != "handsub"]}, org.id)
        assert db_session.get(BusinessProcess, mine.id).parent_process_id == process.id
