"""A person confirms or corrects what was read from their website, and only then is it recorded.

Two real organisations read the same fixture site; what one person confirms is on their own
organisation and model and on no other.
"""

from __future__ import annotations

import pytest

from tests.test_website_crawl import _crawl, _org, fake_model, internal_hosts_allowed, other_site, site  # noqa: F401

pytestmark = pytest.mark.usefixtures("db_session")


def _person(db_session, org, label):
    import uuid

    from app.models.user import Role, User

    Role.insert_roles()
    user = User(email="%s.%s@example.test" % (label, uuid.uuid4().hex[:8]), first_name="Con", last_name=label.title(),
                organization_id=org.id, enterprise_role="solution_architect", confirmed=True)
    user.role = Role.query.filter_by(name="Architect").one()
    user.password = "Sup3r-Secret!pass"
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture
def read(make_org, db_session, site):  # noqa: F811
    """Two organisations, each with the site read into pending proposals; and a person in each."""
    from app import db
    from app.models.organization import Organization

    out = {}
    for label in ("first", "second"):
        info = _org(make_org, site.url)
        _crawl(info)
        out[label] = info.id
        out[label + "_person"] = _person(db_session, db.session.get(Organization, info.id), label).id
    return out


def _o(read, label):
    from app import db
    from app.models.organization import Organization

    db.session.expire_all()
    return db.session.get(Organization, read[label])


def _p(read, label):
    from app import db
    from app.models.user import User

    return db.session.get(User, read[label + "_person"])


def _decisions(org, accept=True, edits=None):
    from app.modules.organization.website_crawl import confirm

    facts, _completeness = confirm.proposals(org.id)
    return [{"id": f["id"], "accept": accept, "value": (edits or {}).get(f["text"], f["text"])} for f in facts], facts


def _elements(org_id, element_type):
    from app.models.archimate_core import ArchiMateElement

    return {e.name: e for e in ArchiMateElement.query.filter_by(organization_id=org_id, type=element_type).all()}


def test_nothing_is_recorded_until_the_person_confirms(read):
    from app.modules.organization.website_crawl import confirm

    org = _o(read, "first")
    facts, completeness = confirm.proposals(org.id)
    assert {f["field"] for f in facts} >= {"company_name", "product", "key_person", "compliance", "integration_partner"}
    assert completeness is not None
    assert not (org.settings or {}).get(confirm.PROFILE_KEY)
    assert _elements(org.id, "BusinessService") == {} and _elements(org.id, "BusinessActor") == {}


def test_confirmed_facts_become_profile_entries_and_model_elements_each_with_its_page(read):
    from app.modules.organization.website_crawl import confirm

    org, person = _o(read, "first"), _p(read, "first")
    decisions, facts = _decisions(org)
    result = confirm.confirm(org, person, decisions)
    assert result["confirmed"] == len(facts) and result["corrected"] == 0

    profile = org.settings[confirm.PROFILE_KEY]
    assert profile["company_name"]["value"] == "Acme Robotics Ltd"
    assert profile["company_name"]["source"] == "website"
    assert profile["company_name"]["source_url"].startswith("http://127.0.0.1")
    assert profile["company_name"]["fetched_at"] and profile["company_name"]["confirmed_by"] == person.id

    service = _elements(org.id, "BusinessService")["PickBot"]
    assert service.layer == "Business" and service.custom_properties["source_url"].endswith("/products")
    assert service.source_kind == "import" and "website" in service.source_ref
    actors = _elements(org.id, "BusinessActor")
    assert actors["Dana Whitfield"].custom_properties["role"] == "Chief Executive Officer"
    software = _elements(org.id, "SystemSoftware")
    assert "React" in software and software["React"].custom_properties["vendor"] == "React"
    assert "Comply with ISO 27001" in _elements(org.id, "Requirement")
    partners = _elements(org.id, "ApplicationComponent")
    assert partners["SAP"].custom_properties["external"] is True

    from app.models.compliance_models import ComplianceRequirement

    assert ComplianceRequirement.query.filter_by(organization_id=org.id, title="Comply with ISO 27001").count() == 1
    assert confirm.proposals(org.id)[0] == []  # nothing is left waiting


def test_a_corrected_fact_is_stored_as_corrected_with_the_person_as_its_source(read):
    from app.modules.organization.website_crawl import confirm

    org, person = _o(read, "first"), _p(read, "first")
    decisions, _facts = _decisions(org, edits={"Acme Robotics Ltd": "Acme Robotics Group", "Dana Whitfield, Chief Executive Officer": "Dana Whitfield, Founder"})
    result = confirm.confirm(org, person, decisions)
    assert result["corrected"] >= 2

    name = org.settings[confirm.PROFILE_KEY]["company_name"]
    assert name["value"] == "Acme Robotics Group" and name["corrected"] is True
    assert name["source"] == "person" and name["confirmed_by"] == person.id and name["confidence"] == 1.0
    dana = _elements(org.id, "BusinessActor")["Dana Whitfield"]
    assert dana.source_kind == "manual" and dana.source_ref == str(person.id)
    assert dana.custom_properties["role"] == "Founder"
    priya = _elements(org.id, "BusinessActor")["Priya Raman"]
    assert priya.source_kind == "import"  # a fact left alone keeps the page it was read from


def test_a_fact_left_unticked_is_not_recorded(read):
    from app.modules.organization.website_crawl import confirm

    org, person = _o(read, "first"), _p(read, "first")
    decisions, facts = _decisions(org)
    for decision, fact in zip(decisions, facts):
        decision["accept"] = fact["field"] != "key_person"
    result = confirm.confirm(org, person, decisions)
    assert result["left_out"] == sum(1 for f in facts if f["field"] == "key_person")
    assert _elements(org.id, "BusinessActor") == {}
    assert "key_person" not in org.settings[confirm.PROFILE_KEY]


def test_what_one_organisation_confirms_is_not_on_the_other(read):
    from app.modules.organization.website_crawl import confirm

    first, second = _o(read, "first"), _o(read, "second")
    decisions, _facts = _decisions(first)
    confirm.confirm(first, _p(read, "first"), decisions)
    assert _elements(second.id, "BusinessService") == {}
    assert not (second.settings or {}).get(confirm.PROFILE_KEY)
    assert confirm.proposals(second.id)[0], "the other organisation's proposals still wait"
    # a decision naming the other organisation's proposal changes nothing
    stray = [{"id": f["id"], "accept": True, "value": f["text"]} for f in confirm.proposals(second.id)[0]]
    confirm.confirm(first, _p(read, "first"), stray)
    assert _elements(first.id, "BusinessService").keys() == {"PickBot"}
    assert confirm.proposals(second.id)[0]


def test_the_asked_answers_are_kept_and_unknown_ones_refused(read):
    from app.modules.organization.website_crawl import confirm

    org = _o(read, "first")
    confirm.save_answers(org, "11-50", "CTO", "audit")
    assert confirm.asked(org.settings) == {"size_band": "11-50", "role": "CTO", "goal": "audit"}
    with pytest.raises(ValueError):
        confirm.save_answers(org, "huge", None, None)
    with pytest.raises(ValueError):
        confirm.save_answers(org, None, None, "world domination")
    assert confirm.asked(_o(read, "second").settings) == {"size_band": None, "role": None, "goal": None}


def test_the_confirm_route_records_for_the_signed_in_organisation_only(read, client, login_as):
    from app.models.org_role import OrgRole
    from app.modules.organization.website_crawl import confirm

    first, second = _o(read, "first"), _o(read, "second")
    for org, person in ((first, _p(read, "first")), (second, _p(read, "second"))):
        OrgRole.set_role(org.id, person.id, "org_admin", granted_by_id=person.id)
    decisions, _facts = _decisions(first)
    login_as(client, _p(read, "first"))
    reply = client.post("/account/welcome/profile", json={"size_band": "11-50", "role": "CTO", "goal": "audit", "facts": decisions})
    assert reply.status_code == 200, reply.get_data(as_text=True)
    assert reply.get_json()["confirmed"] == len(decisions)
    assert confirm.asked(first.settings)["goal"] == "audit"
    assert confirm.asked(second.settings)["goal"] is None and _elements(second.id, "BusinessService") == {}
    refused = client.post("/account/welcome/profile", json={"size_band": "huge"})
    assert refused.status_code == 400


def test_the_confirmed_company_name_becomes_the_organisations_name_and_only_its_own(read):
    from app import db
    from app.modules.organization import public_signals
    from app.modules.organization.website_crawl import confirm

    first, second, person = _o(read, "first"), _o(read, "second"), _p(read, "first")
    first.name = "Casey Startup's Workspace"
    second.name = "Robin Startup's Workspace"
    db.session.commit()
    assert public_signals.company_name(first) == ""  # the stand-in names no company

    confirm.confirm(first, person, _decisions(first, edits={"Acme Robotics Ltd": "Acme Robotics Group"})[0])
    db.session.expire_all()
    assert _o(read, "first").name == "Acme Robotics Group"
    assert public_signals.company_name(_o(read, "first")) == "Acme Robotics Group"
    assert _o(read, "second").name == "Robin Startup's Workspace"  # the other organisation is untouched


def test_a_company_name_left_unticked_leaves_the_organisations_name_alone(read):
    from app import db
    from app.modules.organization.website_crawl import confirm

    org, person = _o(read, "first"), _p(read, "first")
    org.name = "Casey Startup's Workspace"
    db.session.commit()
    decisions, facts = _decisions(org)
    for decision, fact in zip(decisions, facts):
        decision["accept"] = fact["field"] != "company_name"
    confirm.confirm(org, person, decisions)
    db.session.expire_all()
    assert _o(read, "first").name == "Casey Startup's Workspace"
