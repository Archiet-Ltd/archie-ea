"""The count of applications in an answer equals the Applications page total (ADR-0014 Decision 9, release-rule line 3).

The portfolio answer ("which applications have no recorded owner") counts the same population the Applications page
lists. An Application element (the specialisation) and a component both sit behind their portfolio row, so adding
Application elements to the model changes neither number. Two organisations are checked so that neither count leaks.
"""

import pytest

pytestmark = pytest.mark.journey


def _portfolio(db_session, org, names, element_type):
    from app.models import ArchiMateElement
    from app.models.application_portfolio import ApplicationComponent

    for name in names:
        element = ArchiMateElement(name=name, type=element_type, layer="application", organization_id=org.id)
        db_session.add(element)
        db_session.flush()
        db_session.add(ApplicationComponent(name=name, organization_id=org.id, archimate_element_id=element.id))
    db_session.flush()


def _user(db_session, org, tag):
    from app.models.user import User

    user = User(first_name="Ada", last_name=tag, email=f"{tag}@example.com", organization_id=org.id, confirmed=True)
    db_session.add(user)
    db_session.flush()
    return user


def _answer_and_page(client, login_as, user):
    login_as(client, user)
    answer = client.get("/api/v1/intelligence/catalogue/applications_without_owner").get_json()
    page = client.get("/api/v1/applications/?per_page=1").get_json()
    answer = answer.get("data", answer)
    return answer["total"], page["data"]["pagination"]["total"]


def test_the_answer_count_equals_the_applications_page_total_in_two_organisations(
    client, login_as, db_session, make_org
):
    org_a, org_b = make_org("app-agree-a"), make_org("app-agree-b")
    _portfolio(db_session, org_a, ["Billing", "Ledger"], "Application")
    _portfolio(db_session, org_a, ["Billing API"], "ApplicationComponent")
    _portfolio(db_session, org_b, ["Other one"], "Application")
    user_a, user_b = _user(db_session, org_a, "agree-a"), _user(db_session, org_b, "agree-b")
    db_session.commit()

    answer_a, page_a = _answer_and_page(client, login_as, user_a)
    answer_b, page_b = _answer_and_page(client, login_as, user_b)

    assert (answer_a, page_a) == (3, 3)
    assert (answer_b, page_b) == (1, 1)
