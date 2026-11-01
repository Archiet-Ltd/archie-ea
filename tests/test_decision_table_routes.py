"""The decision table screens: reading, writing, linking, evaluating and exporting, across two organisations."""

import uuid

import pytest

from app import db
from app.models.archimate_core import ArchiMateElement
from app.modules.architecture.services import decision_table_service as dts

pytestmark = pytest.mark.usefixtures("db_session")


def _user(db_session, org, enterprise_role="solution_architect"):
    from app.models.user import Role, User

    user = User(
        email="dmn-%s@example.com" % uuid.uuid4().hex[:10], first_name="Dee", last_name="Em",
        organization_id=org.id, confirmed=True, enterprise_role=enterprise_role,
    )
    user.password = "correct-horse-battery"
    role = Role.query.filter_by(name="User").first()
    if role is None:
        Role.insert_roles()
        role = Role.query.filter_by(name="User").first()
    user.role = role
    db_session.add(user)
    db_session.flush()
    return user


def _seed(org):
    process = ArchiMateElement(name="Onboard supplier", type="BusinessProcess", layer="business", organization_id=org.id)
    db.session.add(process)
    d = dts.create_decision(
        "Approve supplier", "F",
        inputs=[{"label": "Annual spend", "type": "number"}, {"label": "Risk rating", "type": "string"}],
        outputs=[{"label": "Approval", "type": "string"}], org_id=org.id,
    )
    dts.save_rule(d, ["<1000", "-"], ['"auto approve"'])
    dts.save_rule(d, ["[1000..50000]", '"low","medium"'], ['"buyer approves"'])
    dts.save_rule(d, [">50000", "-"], ['"board approves"'])
    db.session.flush()
    return d, process


def _post(client, login_as, user, url, data=None, **kw):
    login_as(client, user)
    with client.session_transaction() as sess:
        sess["csrf_token"] = "t"
    return client.post(url, data=dict(data or {}, csrf_token="t"), **kw)


def test_the_table_is_drawn_with_policy_rules_and_a_double_rule(db_session, make_org, client, login_as):
    org = make_org("dmn-page")
    user = _user(db_session, org)
    d, _ = _seed(org)
    login_as(client, user)
    html = client.get("/architecture/decision-tables/%d" % d.id).get_data(as_text=True)
    assert 'data-testid="dmn-hit-policy-letter"' in html and ">F</abbr>" in html
    assert html.count('data-testid="dmn-rule"') == 3
    assert "border-l-2" in html and "overflow-x-auto" in html
    assert "&#34;auto approve&#34;" in html or "auto approve" in html


def test_evaluate_marks_the_matching_rule(db_session, make_org, client, login_as):
    org = make_org("dmn-eval")
    user = _user(db_session, org)
    d, _ = _seed(org)
    resp = _post(client, login_as, user, "/architecture/decision-tables/%d/evaluate" % d.id,
                 {"value_0": "5000", "value_1": "low"})
    html = resp.get_data(as_text=True)
    assert "Rule 2 matched." in html and "buyer approves" in html
    assert 'data-rule="2" aria-current="true"' in html and 'data-rule="1" aria-current="false"' in html


def test_linking_a_process_shows_on_both_pages(db_session, make_org, client, login_as):
    org = make_org("dmn-link")
    user = _user(db_session, org)
    d, process = _seed(org)
    resp = _post(client, login_as, user, "/architecture/decision-tables/%d/processes" % d.id,
                 {"process_id": process.id}, follow_redirects=True)
    page = resp.get_data(as_text=True)
    assert "Onboard supplier" in page and 'data-testid="process-row"' in page
    login_as(client, user)
    r = client.get("/architecture/business/BusinessProcess/%d?ae=1" % process.id)
    process_page = r.get_data(as_text=True)
    assert r.status_code == 200
    assert 'data-testid="decisions-applied"' in process_page
    assert "Approve supplier" in process_page and 'data-testid="decision-applied-link"' in process_page


def test_a_process_with_no_decision_says_so(db_session, make_org, client, login_as):
    org = make_org("dmn-none")
    user = _user(db_session, org)
    _, process = _seed(org)
    login_as(client, user)
    page = client.get("/architecture/business/BusinessProcess/%d?ae=1" % process.id).get_data(as_text=True)
    assert 'data-testid="decisions-applied-empty"' in page


def test_another_organisation_sees_none_of_it(db_session, make_org, client, login_as):
    org_a, org_b = make_org("dmn-a"), make_org("dmn-b")
    user_a, user_b = _user(db_session, org_a), _user(db_session, org_b)
    d, process = _seed(org_a)
    login_as(client, user_b)
    listing = client.get("/architecture/decision-tables/").get_data(as_text=True)
    assert 'data-testid="decision-link"' not in listing and "No decision tables yet." in listing
    for path in ("/architecture/decision-tables/%d" % d.id, "/architecture/decision-tables/%d/export.dmn" % d.id):
        assert client.get(path).status_code == 404
    resp = _post(client, login_as, user_b, "/architecture/decision-tables/%d/delete" % d.id)
    assert resp.status_code == 404
    resp = _post(client, login_as, user_b, "/architecture/decision-tables/%d/rules" % d.id,
                 {"input": ["1", "x"], "output": ['"y"']})
    assert resp.status_code == 404
    login_as(client, user_a)
    assert 'data-testid="decision-link"' in client.get("/architecture/decision-tables/").get_data(as_text=True)


def test_a_viewer_reads_but_cannot_write(db_session, make_org, client, login_as):
    org = make_org("dmn-viewer")
    viewer = _user(db_session, org, enterprise_role="viewer")
    d, _ = _seed(org)
    login_as(client, viewer)
    page = client.get("/architecture/decision-tables/%d" % d.id)
    assert page.status_code == 200 and 'data-testid="rule-add"' not in page.get_data(as_text=True)
    assert _post(client, login_as, viewer, "/architecture/decision-tables/%d/delete" % d.id).status_code == 403
    assert _post(client, login_as, viewer, "/architecture/decision-tables/", {"name": "x"}).status_code == 403
    assert dts.get_decision(d.id, org.id) is not None


def test_export_downloads_dmn_xml(db_session, make_org, client, login_as):
    org = make_org("dmn-export")
    user = _user(db_session, org)
    d, _ = _seed(org)
    login_as(client, user)
    resp = client.get("/architecture/decision-tables/%d/export.dmn" % d.id)
    assert resp.status_code == 200 and resp.mimetype == "application/xml"
    assert resp.headers["Content-Disposition"].endswith('approve-supplier.dmn"')
    assert b"<decisionTable" in resp.data and b'hitPolicy="FIRST"' in resp.data


def test_the_model_import_screen_takes_a_dmn_file(db_session, make_org, client, login_as):
    import io

    from app.modules.architecture.services import decision_exchange_service as ex

    org_a, org_b = make_org("dmn-ia"), make_org("dmn-ib")
    user_b = _user(db_session, org_b)
    d, _ = _seed(org_a)
    xml = ex.export_dmn(d)
    login_as(client, user_b)
    with client.session_transaction() as sess:
        sess["csrf_token"] = "t"
    resp = client.post(
        "/architecture/import/oef",
        data={"csrf_token": "t", "strategy": "skip_duplicates", "oef_file": (io.BytesIO(xml.encode()), "approve.dmn")},
        content_type="multipart/form-data", headers={"Accept": "text/html"},
    )
    page = resp.get_data(as_text=True)
    assert resp.status_code == 200 and 'data-testid="open-decision-tables"' in page
    assert [x.name for x in dts.list_decisions(org_b.id)] == ["Approve supplier"]
    assert len(dts.rules_of(dts.list_decisions(org_b.id)[0])) == 3


def test_the_decision_as_a_model_element_leads_to_its_table_and_the_model_page_leads_to_the_list(
    db_session, make_org, client, login_as
):
    org = make_org("dmn-element")
    user = _user(db_session, org)
    d, process = _seed(org)
    login_as(client, user)
    page = client.get("/architecture/business/BusinessFunction/%d?ae=1" % d.id).get_data(as_text=True)
    assert 'data-testid="open-decision-table"' in page
    assert 'href="/architecture/decision-tables/%d"' % d.id in page
    other = client.get("/architecture/business/BusinessProcess/%d?ae=1" % process.id).get_data(as_text=True)
    assert 'data-testid="open-decision-table"' not in other
    dashboard = client.get("/architecture/").get_data(as_text=True)
    assert "/architecture/decision-tables/" in dashboard
