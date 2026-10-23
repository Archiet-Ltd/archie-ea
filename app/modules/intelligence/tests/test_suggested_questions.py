"""The three questions the Ask page and the dashboard checklist offer are
written from the organisation's own services and owners, and every one leads
to an answer that can be given.

Three cases, exactly as the first-question slice requires:

* a model with dependency links offers "what breaks if <service> fails?" for
  services that really have dependents, so clicking one names them;
* a model with no links still offers three useful questions, chosen from the
  ones that need no traversal (who owns a service, which services have no
  owner, what is in a service);
* a second organisation never sees the first one's service or owner names.
"""

from __future__ import annotations


def _element(db_session, org_id, name, etype="ApplicationComponent", layer="application"):
    from app.models import ArchiMateElement

    el = ArchiMateElement(name=name, type=etype, layer=layer, organization_id=org_id)
    db_session.add(el)
    db_session.flush()
    return el


def _relationship(db_session, org_id, source, target, type_="Serving"):
    from app.models import ArchiMateRelationship

    rel = ArchiMateRelationship(
        source_id=source.id, target_id=target.id, type=type_, organization_id=org_id
    )
    db_session.add(rel)
    db_session.flush()
    return rel


def _application_component(db_session, org_id, element_id, name):
    from app.models.application_portfolio import ApplicationComponent

    comp = ApplicationComponent(
        name=name, organization_id=org_id, archimate_element_id=element_id
    )
    db_session.add(comp)
    db_session.flush()
    return comp


def _user(db_session, org_id, first, last):
    from app.models.user import User

    import uuid

    user = User(
        email=f"sq-{uuid.uuid4().hex[:10]}@example.com",
        first_name=first,
        last_name=last,
        organization_id=org_id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    db_session.add(user)
    db_session.flush()
    return user


def _suggest(app, org_id):
    from flask import g

    from app.modules.intelligence.services.suggested_questions import suggested_questions

    with app.test_request_context("/"):
        g.current_org_id = org_id
        return suggested_questions(org_id)


def test_a_model_with_links_names_services_that_have_dependents(app, db_session, make_org):
    org = make_org("sq-links")
    payments = _element(db_session, org.id, "Payments Service")
    billing = _element(db_session, org.id, "Billing Api")
    portal = _element(db_session, org.id, "Customer Portal")
    _relationship(db_session, org.id, payments, billing)
    _relationship(db_session, org.id, payments, portal)
    db_session.commit()

    result = _suggest(app, org.id)

    assert result["has_links"] is True
    questions = result["questions"]
    assert len(questions) == 3
    # The first question names a real service that really has dependents.
    first = questions[0]
    assert first["element_id"] == payments.id
    assert first["element_name"] == "Payments Service"
    assert first["kind"] == "impact"
    assert "Payments Service" in first["text"]
    assert first["answerable"] is True
    # The link opens the Ask page centred on that element.
    assert first["href"].endswith("element=%s" % payments.id)
    assert "/intelligence/ask" in first["href"]
    # Nothing is shown as its own answer: a service never cites itself.
    for question in questions:
        assert question["element_id"] is not None
        assert question["text"].strip()


def test_a_model_with_no_links_still_offers_three_answerable_questions(app, db_session, make_org):
    org = make_org("sq-nolinks")
    # Services, but nothing depends on anything: no relationships at all.
    _element(db_session, org.id, "Quiet Service")
    _element(db_session, org.id, "Lonely Api")
    _element(db_session, org.id, "Orphan Worker")
    db_session.commit()

    result = _suggest(app, org.id)

    assert result["has_links"] is False
    questions = result["questions"]
    assert len(questions) == 3
    names = {q["element_name"] for q in questions if q["element_name"]}
    # The questions use the organisation's own service names.
    assert names <= {"Quiet Service", "Lonely Api", "Orphan Worker"}
    assert names, "no-links questions still name the person's own services"
    # Every question is one that needs no dependency traversal to answer, and
    # is marked answerable so the screen never offers a dead end.
    for question in questions:
        assert question["kind"] in {"accountability", "data"}
        assert question["answerable"] is True
        assert question["text"].strip()
        assert "/intelligence/ask" in question["href"]


def test_an_empty_model_offers_nothing_rather_than_an_empty_list_of_names(app, db_session, make_org):
    org = make_org("sq-empty")
    db_session.commit()

    result = _suggest(app, org.id)

    # No elements at all: there are no real names to write a question from, so
    # the caller is told the model is empty rather than handed invented names.
    assert result["has_links"] is False
    assert result["questions"] == []
    assert result["empty"] is True


def test_a_second_organisation_never_sees_the_first_ones_names(app, db_session, make_org):
    org_a = make_org("sq-iso-a")
    org_b = make_org("sq-iso-b")

    a_pay = _element(db_session, org_a.id, "Acme Payments")
    a_dep = _element(db_session, org_a.id, "Acme Ledger")
    _relationship(db_session, org_a.id, a_pay, a_dep)
    owner_a = _user(db_session, org_a.id, "Ada", "Secret")
    from app.models.application_owner import ApplicationOwner

    comp_a = _application_component(db_session, org_a.id, a_pay.id, "Acme Payments")
    db_session.add(ApplicationOwner(
        application_id=comp_a.id, user_id=owner_a.id, organization_id=org_a.id,
        ownership_type="primary",
    ))

    b_svc = _element(db_session, org_b.id, "Globex Billing")
    b_dep = _element(db_session, org_b.id, "Globex Invoices")
    _relationship(db_session, org_b.id, b_svc, b_dep)
    db_session.commit()

    import json

    result_b = _suggest(app, org_b.id)
    text_b = json.dumps(result_b, default=str)
    assert "Acme" not in text_b
    assert "Ada" not in text_b and "Secret" not in text_b
    # Organisation B sees only its own service.
    assert any(q["element_name"] == "Globex Billing" for q in result_b["questions"])

    result_a = _suggest(app, org_a.id)
    text_a = json.dumps(result_a, default=str)
    assert "Globex" not in text_a


# --- the route that serves the questions to a signed-in person --------------


def _route_user(db_session, org_id):
    from app.models.user import User

    import uuid

    user = User(
        email=f"sqr-{uuid.uuid4().hex[:10]}@example.com",
        first_name="Sqr",
        last_name="Tester",
        organization_id=org_id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    db_session.add(user)
    db_session.flush()
    return user


def test_route_serves_the_three_questions_for_a_signed_in_person(
    app, db_session, make_org, client, login_as
):
    org = make_org("sq-route")
    user = _route_user(db_session, org.id)
    pay = _element(db_session, org.id, "Routed Payments")
    dep = _element(db_session, org.id, "Routed Ledger")
    _relationship(db_session, org.id, pay, dep)
    db_session.commit()

    login_as(client, user)
    resp = client.get("/api/v1/intelligence/suggested-questions")
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["has_links"] is True
    assert len(data["questions"]) >= 1
    first = data["questions"][0]
    assert first["element_name"] == "Routed Payments"
    assert first["kind"] == "impact"
    assert first["href"].endswith("element=%s" % pay.id)


def test_route_is_not_served_to_an_anonymous_visitor(app, client):
    resp = client.get("/api/v1/intelligence/suggested-questions")
    assert resp.status_code in (301, 302, 401)


def test_route_never_shows_another_organisations_names(
    app, db_session, make_org, client, login_as
):
    org_a = make_org("sq-route-a")
    org_b = make_org("sq-route-b")
    user_b = _route_user(db_session, org_b.id)
    a_svc = _element(db_session, org_a.id, "Secret Alpha Service")
    a_dep = _element(db_session, org_a.id, "Secret Alpha Ledger")
    _relationship(db_session, org_a.id, a_svc, a_dep)
    _element(db_session, org_b.id, "Bravo Billing")
    db_session.commit()

    login_as(client, user_b)
    resp = client.get("/api/v1/intelligence/suggested-questions")
    assert resp.status_code == 200
    assert "Secret Alpha" not in resp.get_data(as_text=True)
