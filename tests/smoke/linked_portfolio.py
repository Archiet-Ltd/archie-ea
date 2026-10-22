"""A small, linked portfolio for the journeys that read figures across screens.

Six applications with owners, vendors and annual costs (two have no owner),
dependencies between them, one mapped capability and one solution in phase C
so the overview is in data mode and the health score is measured, and four
risks: one linked to one application, one linked to two, one linked twice
(through a link and through its element field) and one linked to nothing.

``seed_linked_portfolio`` is called once for the organisation under test and
once for a second organisation: the second holds an application with the same
name as the first one's billing application and its own risk, so a journey can
prove that nothing crosses.
"""


# key, name suffix, owner, vendor, annual cost
APPLICATIONS = (
    ("billing", "Billing", "Priya Raman", "Chargebee", 120000),
    ("ledger", "Ledger", "Marcus Webb", "Oracle", 80000),
    ("portal", "Portal", "Ines Duarte", "Contentful", 45000),
    ("gateway", "Gateway", "Marcus Webb", "Kong", 30000),
    ("reports", "Reports", None, "Tableau", 15000),
    ("crm", "Legacy CRM", None, "Siebel", 9000),
)


def seed_linked_portfolio(org_id, noun, *, with_risks=True, applications=APPLICATIONS,
                          include_capability=True):
    """Create the portfolio; return ids, names and the figures a person should see."""
    from app import create_app, db
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.business_capabilities import BusinessCapability
    from app.models.risk import Risk
    from app.models.risk_entity_link import RiskEntityLink
    from app.models.solution_models import Solution
    from app.services.application_cost_accessor import set_annual_cost

    app = create_app("testing")
    out = {"noun": noun, "org": org_id, "apps": {}, "risks": {}}
    with app.app_context():
        for key, label, owner, vendor, cost in applications:
            component = ApplicationComponent(
                name="%s %s" % (noun, label), organization_id=org_id, business_owner=owner,
                vendor_name=vendor, lifecycle_status="active",
                business_criticality="high" if key in ("billing", "ledger") else "medium",
                component_type="Application",
            )
            db.session.add(component)
            db.session.commit()
            # The cost goes in through the one accessor, as the import and edit screens do.
            set_annual_cost(component, cost)
            db.session.commit()
            out["apps"][key] = {
                "id": component.id, "name": component.name, "owner": owner, "vendor": vendor,
                "cost": cost, "cost_text": "{:,.0f}".format(cost),
                "element": component.archimate_element_id,
            }

        for source, target in (("billing", "ledger"), ("billing", "gateway"), ("portal", "billing")):
            if source in out["apps"] and target in out["apps"]:
                db.session.add(ArchiMateRelationship(
                    type="Serving", source_id=out["apps"][source]["element"],
                    target_id=out["apps"][target]["element"], organization_id=org_id))
        db.session.commit()

        if include_capability:
            element = ArchiMateElement(name="%s Take payment" % noun, type="Capability",
                                       layer="Strategy", organization_id=org_id)
            db.session.add(element)
            db.session.commit()
            capability = BusinessCapability(name=element.name, level=1, organization_id=org_id,
                                            archimate_element_id=element.id)
            db.session.add(capability)
            db.session.commit()
            db.session.add(ApplicationCapabilityMapping(
                application_component_id=out["apps"][applications[0][0]]["id"],
                business_capability_id=capability.id, organization_id=org_id))
            db.session.add(Solution(name="%s Payments modernisation" % noun, organization_id=org_id,
                                    governance_status="draft", adm_phase="C", has_acm_domains=True))
            db.session.commit()
            out["capability"] = capability.name

        if with_risks:
            def risk(key, title, likelihood, impact, *app_keys, element_of=None):
                row = Risk(title="%s %s" % (noun, title), likelihood=likelihood, impact=impact,
                           organization_id=org_id, owner="Risk office")
                db.session.add(row)
                db.session.commit()
                for app_key in app_keys:
                    db.session.add(RiskEntityLink(
                        organization_id=org_id, risk_id=row.id, entity_type="application",
                        entity_id=out["apps"][app_key]["id"]))
                if element_of:
                    row.archimate_element_id = out["apps"][element_of]["element"]
                db.session.commit()
                out["risks"][key] = {"id": row.id, "title": row.title, "apps": list(app_keys)}

            risk("billing", "Billing outage", 4, 4, "billing")
            risk("ledger", "Ledger vendor lock-in", 3, 4, "ledger", "gateway")
            risk("gateway", "Gateway certificate expiry", 2, 3, "gateway", element_of="gateway")
            risk("loose", "Reporting debt", 3, 3)
        db.session.remove()
    return out


def seed_other_organisation(org_id, noun):
    """A second organisation: an application named like the first one's billing
    application, a different owner and cost, and a risk linked to it."""
    other = seed_linked_portfolio(
        org_id, noun, applications=(("billing", "Billing", "Zed Outsider", "Competitor Ltd", 999999),
                                    ("ledger", "Ledger", "Zed Outsider", "Competitor Ltd", 888888),
                                    ("gateway", "Gateway", "Zed Outsider", "Competitor Ltd", 777777)),
        with_risks=False, include_capability=False)
    from app import create_app, db
    from app.models.risk import Risk
    from app.models.risk_entity_link import RiskEntityLink

    app = create_app("testing")
    with app.app_context():
        row = Risk(title="%s Outsider secret risk" % noun, likelihood=5, impact=5,
                   organization_id=org_id, owner="Zed Outsider")
        db.session.add(row)
        db.session.commit()
        db.session.add(RiskEntityLink(organization_id=org_id, risk_id=row.id, entity_type="application",
                                      entity_id=other["apps"]["billing"]["id"]))
        db.session.commit()
        other["risks"]["secret"] = {"id": row.id, "title": row.title}
        db.session.remove()
    return other


import pytest  # noqa: E402


@pytest.fixture(scope="module")
def linked_world(live_server):
    """Two organisations: the one a person works in, with the linked portfolio and
    two signed-in users (an enterprise architect and a CTO), and a second one
    holding look-alike records that must never show."""
    import uuid

    from tests.smoke.fresh_org import create_fresh_org

    noun = "Harbour%s" % uuid.uuid4().hex[:5]
    mine = create_fresh_org("enterprise_architect", extra_roles=("cto",))
    other = create_fresh_org("enterprise_architect")
    return {
        "noun": noun,
        "org": mine,
        "mine": seed_linked_portfolio(mine["org_id"], noun),
        "other": seed_other_organisation(other["org_id"], noun),
        "other_org": other,
    }
