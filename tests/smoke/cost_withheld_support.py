"""Shared set-up for the journeys that check who may read a money figure.

Each organisation made here has a finance person (the cost rule allows) and a solution architect (the cost
rule withholds from), with the same kinds of record carrying distinct amounts, so a figure on a page can be
traced to the organisation that owns it.
"""
import os
import pathlib
import tempfile
import uuid

from .fresh_org import create_fresh_org, sign_in

HELD_BACK = "Cost figures are withheld for your role."
SHOTS = pathlib.Path(os.environ.get("SMOKE_SHOT_DIR") or tempfile.mkdtemp(prefix="cost-withheld-"))


def figures(base):
    """Amounts for one organisation, all different from every other organisation's (``base`` 1, 2, ...)."""
    return dict(
        savings=61000 + base * 1234, investment=15000 + base * 877, tco=188000 + base * 451,
        benefit_projected=55000 + base * 555, benefit_actual=22000 + base * 222,
        entry_savings=33000 + base * 333, entry_migration=44000 + base * 444,
        group_savings=88000 + base * 888, run_savings=77000 + base * 777,
        licence=48000 + base * 210, infrastructure=3000 + base * 175, development=91000 + base * 340,
        maintenance=27000 + base * 655, app_tco=207000 + base * 1005,
        vendor_tco=310000 + base * 1111, vendor_licence=120000 + base * 2222, vendor_support=45000 + base * 3333,
    )


def digits(amount):
    """The figure as a page prints it with thousands separators, and as a reply carries it."""
    return ("{:,.0f}".format(amount), str(int(amount)))


def seed_organisation(base):
    """One organisation with a finance person and a solution architect, and linked rationalisation records."""
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.application_rationalization import (
        ApplicationRationalizationScore,
        RationalizationBenefitsTracker,
    )
    from app.models.consolidation_list import ConsolidationListEntry
    from app.models.unified_duplicate_detection import (
        UnifiedDetectionRun,
        UnifiedDuplicateGroup,
        unified_group_members,
    )

    fig = figures(base)
    org = create_fresh_org("finance", extra_roles=("solution_architect",))
    tag = uuid.uuid4().hex[:6]
    org["figures"] = fig
    org["tag"] = tag
    with create_app("testing").app_context():
        apps = []
        for i, stem in enumerate(("Ledgerline", "Ledgerline Books")):
            row = ApplicationComponent(
                name="%s %s" % (stem, tag), organization_id=org["org_id"], lifecycle_status="operational",
                description="Holds the ledger.", license_cost_annual=fig["licence"] + i,
                infrastructure_cost_monthly=fig["infrastructure"], development_cost_annual=fig["development"],
                maintenance_cost=fig["maintenance"], total_cost_of_ownership=fig["app_tco"] + i)
            db.session.add(row)
            apps.append(row)
        db.session.flush()
        for row in apps:
            db.session.add(ApplicationRationalizationScore(
                application_component_id=row.id, organization_id=org["org_id"], disposition_action="retain",
                rationalization_action="ELIMINATE", overall_health_score=72, priority="medium",
                technical_health_score=70, business_value_score=64, cost_efficiency_score=58, vendor_risk_score=66,
                action_rationale="Keeps the ledger running.", estimated_annual_savings=fig["savings"],
                estimated_investment_needed=fig["investment"], total_cost_of_ownership=fig["tco"]))
            db.session.add(ConsolidationListEntry(
                application_id=row.id, estimated_savings=fig["entry_savings"], migration_cost=fig["entry_migration"],
                status="identified", recommended_action="decommission", source_group_name="Ledger twins " + tag))
        db.session.flush()
        db.session.add(RationalizationBenefitsTracker(
            application_id=apps[0].id, projected_annual_savings=fig["benefit_projected"],
            actual_annual_savings=fig["benefit_actual"]))
        run = UnifiedDetectionRun(run_name="Ledger run " + tag, strategy="fast", status="completed",
                                  estimated_savings=fig["run_savings"], groups_found=1,
                                  applications_analyzed=2)
        db.session.add(run)
        db.session.flush()
        group = UnifiedDuplicateGroup(name="Ledger twins " + tag, similarity_score=0.93,
                                      estimated_savings=fig["group_savings"], detection_run_id=run.id,
                                      status="pending", duplicate_type="fuzzy")
        db.session.add(group)
        db.session.flush()
        for row in apps:
            db.session.execute(unified_group_members.insert().values(group_id=group.id, application_id=row.id))
        db.session.commit()
        org["app_ids"] = [row.id for row in apps]
        org["app_names"] = [row.name for row in apps]
        org["group_id"] = group.id
        org["group_name"] = group.name
        org["run_id"] = run.id
    org["admin_email"] = "launch.admin.%s@example.com" % org["suffix"]
    return org


def open_page(browser, live_server, email, width, scheme):
    """A signed-in page at the given width and colour scheme."""
    context = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    page = context.new_page()
    sign_in(page, live_server, email)
    return context, page


def shot(page, name, width, scheme):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / ("%s-%s-%d.png" % (name, scheme, width))), full_page=True)


def shown_text(page):
    """What a person can read on the page now: the main area's rendered text (hidden parts are left out)."""
    return page.locator("main").inner_text()


def wait_until_said(page, timeout=60000):
    """Wait until the sentence is on the page where a person can read it (a part that loads late is awaited)."""
    page.wait_for_function("(t) => document.querySelector('main').innerText.includes(t)", arg=HELD_BACK,
                           timeout=timeout)


def holds_none_of(text, amounts):
    """The amounts, each as printed and as carried in a reply, that appear in ``text``."""
    found = []
    for amount in amounts:
        for form in digits(amount):
            if form in text:
                found.append(form)
    return found
