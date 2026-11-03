"""The risk assessment reads the register: the same risks, counted the same way, reached through
the capabilities they are linked to. Two organisations never mix."""
import pytest

from app.models.application_capability import ApplicationCapabilityMapping
from app.models.application_portfolio import ApplicationComponent
from app.models.business_capabilities import BusinessCapability
from app.models.risk import Risk, RiskStatus
from app.models.risk_entity_link import RiskEntityLink
from app.models.unified_capability import UnifiedCapability
from app.services import risk_service


def _capability(db_session, org, name):
    row = BusinessCapability(name=name, level=1, organization_id=org.id)
    db_session.add(row)
    db_session.flush()
    return row


def _risk(db_session, org, title, likelihood, impact, status=RiskStatus.OPEN, **fields):
    row = Risk(organization_id=org.id, title=title, likelihood=likelihood, impact=impact, status=status, **fields)
    db_session.add(row)
    db_session.flush()
    return row


def _link(db_session, org, risk, kind, entity_id):
    db_session.add(RiskEntityLink(organization_id=org.id, risk_id=risk.id, entity_type=kind, entity_id=entity_id))
    db_session.flush()


def test_counts_match_the_registers_open_risks_and_stay_inside_the_organisation(
        app, db_session, make_org, tenant_ctx):
    one, two = make_org("assess-count-one"), make_org("assess-count-two")
    with tenant_ctx(one.id):
        _risk(db_session, one, "Open critical", 5, 5)
        _risk(db_session, one, "Mitigated critical", 5, 5, RiskStatus.MITIGATED)
        _risk(db_session, one, "Open high", 3, 3)
        _risk(db_session, one, "Open medium", 2, 3)
        _risk(db_session, one, "Open low", 1, 1)
        counts = risk_service.level_counts()
        register_critical = [
            r for r in Risk.query.all() if r.status == RiskStatus.OPEN and r.risk_level == "critical"
        ]
    assert counts == {"critical": 1, "high": 1, "medium": 1, "low": 1, "open": 4}
    assert counts["critical"] == len(register_critical)
    with tenant_ctx(two.id):
        _risk(db_session, two, "Their critical", 5, 5)
        _risk(db_session, two, "Their other critical", 4, 4)
        assert risk_service.level_counts()["critical"] == 2
    with tenant_ctx(one.id):
        assert risk_service.level_counts()["critical"] == 1


def test_a_risk_reaches_a_capability_directly_and_through_an_application(
        app, db_session, make_org, tenant_ctx):
    org, rival = make_org("assess-reach-one"), make_org("assess-reach-two")
    with tenant_ctx(org.id):
        direct_cap = _capability(db_session, org, "Direct capability")
        mapped_cap = _capability(db_session, org, "Mapped capability")
        billing = ApplicationComponent(name="Billing", organization_id=org.id)
        db_session.add(billing)
        db_session.flush()
        db_session.add(ApplicationCapabilityMapping(
            application_component_id=billing.id, business_capability_id=mapped_cap.id,
            organization_id=org.id))
        direct = _risk(db_session, org, "Direct risk", 5, 5)
        via_app = _risk(db_session, org, "Via application", 4, 4)
        loose = _risk(db_session, org, "Loose risk", 1, 1)
        _link(db_session, org, direct, "capability", direct_cap.id)
        _link(db_session, org, via_app, "application", billing.id)
        rows = {r["title"]: r for r in risk_service.risk_assessment()["rows"]}
        assert [c["name"] for c in rows["Direct risk"]["capabilities"]] == ["Direct capability"]
        assert [c["name"] for c in rows["Via application"]["capabilities"]] == ["Mapped capability"]
        assert rows["Loose risk"]["capabilities"] == []
        assert loose.id == rows["Loose risk"]["risk_id"]
    with tenant_ctx(rival.id):
        # Another organisation's risks and capabilities are not in this organisation's answer.
        assert risk_service.risk_assessment()["rows"] == []


def test_a_capability_owner_comes_from_the_owner_record(app, db_session, make_org, tenant_ctx):
    from app.models.user import User
    from app.services.capability_ownership_service import set_capability_owner

    org = make_org("assess-owner")
    with tenant_ctx(org.id):
        owned = _capability(db_session, org, "Owned capability")
        unowned = _capability(db_session, org, "Unowned capability")
        person = User(email="assess-owner@example.com", first_name="Mina", last_name="Reed",
                      organization_id=org.id, confirmed=True)
        db_session.add(person)
        db_session.flush()
        projected = UnifiedCapability.query.filter_by(
            organization_id=org.id, source_table="business_capability", source_id=str(owned.id)).one()
        set_capability_owner(capability_id=projected.id, user_id=person.id, organization_id=org.id)
        first = _risk(db_session, org, "On owned", 5, 5)
        second = _risk(db_session, org, "On unowned", 5, 4)
        _link(db_session, org, first, "capability", owned.id)
        _link(db_session, org, second, "capability", unowned.id)
        rows = {r["title"]: r for r in risk_service.risk_assessment()["rows"]}
        assert rows["On owned"]["capabilities"][0]["owner"] == "Mina Reed"
        assert rows["On unowned"]["capabilities"][0]["owner"] is None


def test_a_risk_can_be_linked_to_a_capability_of_its_own_organisation_only(
        app, db_session, make_org, tenant_ctx):
    org, rival = make_org("assess-link-one"), make_org("assess-link-two")
    with tenant_ctx(rival.id):
        theirs = _capability(db_session, rival, "Their capability")
    with tenant_ctx(org.id):
        mine = _capability(db_session, org, "My capability")
        risk = _risk(db_session, org, "Linkable", 3, 3)
        link = risk_service.add_risk_link(risk.id, "capability", mine.id)
        assert (link.entity_type, link.entity_id) == ("capability", mine.id)
        with pytest.raises(ValueError):
            risk_service.add_risk_link(risk.id, "capability", theirs.id)


def test_the_demo_risks_each_have_their_own_description_and_a_link(app, db_session, make_org, tenant_ctx):
    from app.commands.seed_demo_company import _RISK_RECORDS, _complete_risk_records

    org = make_org("assess-seed")
    with tenant_ctx(org.id):
        for name in {capability for _, capability in _RISK_RECORDS.values()}:
            _capability(db_session, org, name)
        for title in _RISK_RECORDS:
            _risk(db_session, org, title, 3, 3, description="Same words", mitigation_plan="Same words")
        _complete_risk_records(org.id)
        for risk in Risk.query.all():
            assert risk.description and risk.description != risk.mitigation_plan, risk.title
            assert RiskEntityLink.query.filter_by(risk_id=risk.id).count() >= 1, risk.title
        # Run again: nothing is written twice and an edited description is kept.
        edited = Risk.query.first()
        edited.description = "An architect wrote this."
        db_session.flush()
        assert _complete_risk_records(org.id) == 0
        assert Risk.query.get(edited.id).description == "An architect wrote this."
        assert len({r.description for r in Risk.query.filter(Risk.id != edited.id)}) == len(_RISK_RECORDS) - 1
