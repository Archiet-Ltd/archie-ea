"""The chat's answers about applications say only what they show, count what is in use and answer an
owner question in a sentence with its source.

Asked for the applications, the sentence names each entry's owner, and its annual cost only for a
person the one cost rule lets read it; the count is the applications list's count of applications in
use and says how many decommissioned ones it leaves out. Asked who owns an application, the chat says
who, in one sentence linking the application's page, and says nothing about capacity unless asked.
Every answer reads only the signed-in organisation's records.
"""
import re
import uuid

from app import db
from tests.test_sample_company_working_data import _architect

INVENTORY = "What applications do we run and who owns each one?"


def _ask(client, message):
    return client.post("/ai-chat/estate-answer", json={"message": message}).get_json()


def _administrator(org, email):
    from app.models.org_role import OrgRole

    user = _architect(org, email)
    user.is_org_admin = True
    db.session.commit()
    OrgRole.set_role(org.id, user.id, "org_admin", granted_by_id=user.id)
    db.session.commit()
    return user


def _application(org_id, name, *, cost=None, lifecycle=None, owner_text=None, element=False):
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement

    element_id = None
    if element:
        made = ArchiMateElement(name=name, type="ApplicationComponent", layer="application", organization_id=org_id)
        db.session.add(made)
        db.session.flush()
        element_id = made.id
    row = ApplicationComponent(
        name=name, organization_id=org_id, total_cost_of_ownership=cost, lifecycle_status=lifecycle,
        application_owner=owner_text, archimate_element_id=element_id,
    )
    db.session.add(row)
    db.session.flush()
    return row


def _person_owns(org_id, application, first, last):
    from app.models.user import User
    from app.services.element_ownership_service import set_element_owner

    person = User(
        email="%s.%s@example.com" % (first.lower(), uuid.uuid4().hex[:8]), first_name=first, last_name=last,
        organization_id=org_id, confirmed=True, enterprise_role="enterprise_architect",
    )
    db.session.add(person)
    db.session.flush()
    set_element_owner(
        element_type="application", element_id=application.id, user_id=person.id,
        organization_id=org_id, ownership_type="primary", commit=False,
    )
    return person


def _estate(org_id):
    ledger = _application(org_id, "Ledger Core", cost=12000, lifecycle="2.1 Strategic", element=True)
    _person_owns(org_id, ledger, "Nneka", "Okafor")
    _application(org_id, "Billing Hub", cost=4500, lifecycle="2.2 Tactical", owner_text="Ada Obi")
    _application(org_id, "Old Archive", cost=900, lifecycle="5. Decommissioned", owner_text="Tunde Bello")
    db.session.commit()
    return ledger


def _sentence(html):
    match = re.search(r'data-testid="catalogue-sentence">([^<]*)<', html)
    assert match, html[:600]
    return match.group(1)


def test_the_count_is_the_applications_in_use_and_names_the_decommissioned_it_leaves_out(client, login_as, make_org):
    org = make_org()
    user = _architect(org, "apps-count-%s@example.com" % uuid.uuid4().hex[:6])
    _estate(org.id)
    login_as(client, user)
    body = _ask(client, INVENTORY)
    assert body["entry_id"] == "applications_and_owners"
    sentence = _sentence(body["html"])
    assert sentence.startswith("2 applications in use; 1 decommissioned not counted."), sentence
    assert body["html"].count("data-catalogue-record") == 2
    assert "Old Archive" not in body["html"]
    # The applications list counts the same two in use and the same one decommissioned.
    listing = client.get("/applications/").get_data(as_text=True)
    assert re.search(r'data-testid="total-count-badge">2 shown', listing), "the list does not show 2 in use"
    assert re.search(r'data-testid="decom-count-label">1<', listing)


def test_a_person_without_spend_authority_is_never_promised_a_cost(client, login_as, make_org):
    from app.decorators.requires_role import may_see_spend_pages

    org = make_org()
    user = _architect(org, "apps-nospend-%s@example.com" % uuid.uuid4().hex[:6])
    assert not may_see_spend_pages(user)
    _estate(org.id)
    login_as(client, user)
    html = _ask(client, INVENTORY)["html"]
    sentence = _sentence(html)
    assert sentence.endswith("Each is listed with its accountable owner."), sentence
    assert "cost" not in html.lower() and "12,000" not in html and "4,500" not in html
    assert "Owner: Nneka Okafor" in html and "Owner: Ada Obi" in html


def test_an_administrator_reads_each_owner_and_annual_cost_the_sentence_names(client, login_as, make_org):
    from app.decorators.requires_role import may_see_spend_pages

    org = make_org()
    admin = _administrator(org, "apps-admin-%s@example.com" % uuid.uuid4().hex[:6])
    assert may_see_spend_pages(admin)
    _estate(org.id)
    login_as(client, admin)
    html = _ask(client, INVENTORY)["html"]
    assert _sentence(html).endswith("Each is listed with its accountable owner and annual cost."), html[:600]
    details = re.findall(r"data-catalogue-detail>([^<]*)<", html)
    assert len(details) == 2, details
    assert any("Owner: Nneka Okafor" in d and "12,000" in d for d in details), details
    assert any("Owner: Ada Obi" in d and "4,500" in d for d in details), details


def test_an_owner_question_is_one_sentence_with_its_source_and_no_unrelated_line(client, login_as, make_org):
    org = make_org()
    user = _architect(org, "apps-owner-%s@example.com" % uuid.uuid4().hex[:6])
    ledger = _estate(org.id)
    login_as(client, user)
    html = _ask(client, "Who owns Ledger Core?")["html"]
    assert re.search(
        r'Ledger Core is owned by Nneka Okafor, recorded on <a [^>]*href="/applications/%d"[^>]*>the Ledger Core '
        r"application page</a>\." % ledger.id,
        html,
    ), html[:800]
    assert "not yet connected" not in html
    # Asked about capacity, the answer says that data is not connected.
    html = _ask(client, "Who owns Ledger Core, and can they take on more?")["html"]
    assert "Capacity and availability data is not yet connected." in html


def test_each_organisation_hears_only_its_own_applications_and_owners(client, login_as, make_org):
    org, other = make_org(), make_org()
    mine = _architect(org, "apps-iso-a-%s@example.com" % uuid.uuid4().hex[:6])
    theirs = _architect(other, "apps-iso-b-%s@example.com" % uuid.uuid4().hex[:6])
    _estate(org.id)
    _application(other.id, "Harbour Payroll", cost=777, lifecycle="2.1 Strategic", owner_text="Zed Foreign")
    db.session.commit()

    login_as(client, theirs)
    html = _ask(client, INVENTORY)["html"]
    assert _sentence(html).startswith("1 application in use."), html[:600]
    assert "Harbour Payroll" in html and "Ledger Core" not in html and "Nneka Okafor" not in html
    owner = _ask(client, "Who owns Ledger Core?")
    assert "Nneka Okafor" not in (owner.get("html") or "")

    login_as(client, mine)
    html = _ask(client, INVENTORY)["html"]
    assert _sentence(html).startswith("2 applications in use; 1 decommissioned not counted."), html[:600]
    assert "Harbour Payroll" not in html and "Zed Foreign" not in html


def _github_import(org_id, name):
    """A repository the GitHub connector brought in that nobody has promoted: no lifecycle, no owner."""
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement

    made = ArchiMateElement(
        name=name, type="ApplicationComponent", layer="application", organization_id=org_id, source_kind="connector"
    )
    db.session.add(made)
    db.session.flush()
    row = ApplicationComponent(
        name=name, organization_id=org_id, component_type="repository", archimate_element_id=made.id
    )
    db.session.add(row)
    db.session.flush()
    return row


def test_an_unpromoted_github_import_is_never_called_decommissioned(client, login_as, make_org):
    from app.modules.dashboard.services.overview_figures import left_out_applications

    org, other = make_org(), make_org()
    mine = _architect(org, "apps-import-a-%s@example.com" % uuid.uuid4().hex[:6])
    theirs = _architect(other, "apps-import-b-%s@example.com" % uuid.uuid4().hex[:6])
    _estate(org.id)
    _github_import(org.id, "payments-service")
    for name in ("web-shop", "batch-jobs", "infra-scripts"):
        _github_import(other.id, name)
    db.session.commit()

    login_as(client, mine)
    html = _ask(client, INVENTORY)["html"]
    sentence = _sentence(html)
    assert sentence.startswith("2 applications in use; 1 decommissioned not counted."), sentence
    assert "payments-service" not in html
    # The chat's decommissioned figure is the Overview's left-out figure for the same organisation.
    assert left_out_applications(org.id)["decommissioned"] == 1

    # An organisation with only imports has nothing decommissioned to report.
    login_as(client, theirs)
    other_html = _ask(client, INVENTORY)["html"]
    assert "decommissioned" not in other_html.lower(), other_html[:600]
    assert "web-shop" not in other_html
    assert left_out_applications(other.id)["decommissioned"] == 0
