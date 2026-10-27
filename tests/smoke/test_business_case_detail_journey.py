"""A business owner reads a business case on its detail page.

The detail screen (app/modules/business_case/routes.py :: detail) is the page a
business owner is handed a link to: it carries the case title and description,
the financial summary (capex / opex / 3yr TCO / annual benefit / ROI) pulled
from the linked capability, strategic initiative and solution, and the names of
those three links. A unit test can prove aggregate_financials() computes the
right figures; only a real browser proves the page the owner opens actually
shows them, and that every link on it lands on a page with content rather than
a 404 or an empty shell.

This journey therefore:
  * seeds a business case in one organisation, linked to a strategic
    initiative, a capability (with a cost allocation and its projected unified
    row) and a solution carrying the exact financial shape the aggregate unit
    test fixes, and pulls those figures onto the case;
  * signs in as that organisation's own business owner (a non-administrator —
    the test asserts the signed-in persona is not an org or platform admin —
    reaching the page by its direct URL, as the owner would from a shared link);
  * asserts the page shows the title, the description, each financial figure
    exactly as stored (same numbers as the aggregate test, formatted as the
    field holds them), and the linked initiative / capability / solution names;
  * walks every link on the page and asserts each lands on HTTP-200 content
    with a heading — the "no broken link" loop — never a refusal or blank page;
  * seeds a SECOND organisation with its own business case and proves the first
    owner cannot reach it: the other tenant's row is never found or named.

Follows tests/smoke/test_business_case_journey.py for the login and live-server
pattern (real sign-in so the session cookie flows as the browser sends it).
"""

import re
import uuid

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PASSWORD

PAGE_TIMEOUT = 30000


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


def _make_owner(db, org, suffix):
    """A confirmed, password-holding business owner in *org* — not an admin."""
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(
        email=f"bizcase-owner-{suffix}@example.com",
        first_name="Case",
        last_name="Owner",
        organization_id=org.id,
        enterprise_role="business_architect",
        confirmed=True,
    )
    user.role = Role.query.filter_by(name="Architect").one()
    user.is_platform_admin = False
    user.password = PASSWORD
    db.session.add(user)
    db.session.flush()
    return user


def _seed_financed_case(db, org, user, suffix):
    """A business case linked to an initiative, a capability (+ cost allocation
    and projected unified row) and a solution, with the figures pulled onto it.

    Returns (business_case, {capex, opex_annual, tco_3yr, financial_benefit_annual,
    roi_percentage}) where each value is the string the detail form field holds,
    so the journey asserts exactly what the page shows.
    """
    import datetime
    from decimal import Decimal

    from app.models.business_capabilities import BusinessCapability
    from app.models.business_case import BusinessCase
    from app.models.cost_intelligence import CapabilityCostAllocation
    from app.models.solution_models import Solution
    from app.models.strategic import StrategicInitiative
    from app.models.unified_capability import UnifiedCapability
    from app.modules.business_case import service

    initiative = StrategicInitiative(
        name=f"CRM Consolidation Programme {suffix}",
        organization_id=org.id,
        budget_allocated=100000.0,
        budget_spent=40000.0,
        business_value_score=8,
    )
    db.session.add(initiative)

    capability = BusinessCapability(name=f"Customer Management {suffix}")
    capability.organization_id = org.id
    db.session.add(capability)
    db.session.flush()

    db.session.add(CapabilityCostAllocation(
        capability_id=capability.id,
        organization_id=org.id,
        fiscal_year=2026,
        period_start_date=datetime.date(2026, 1, 1),
        period_end_date=datetime.date(2026, 12, 31),
        software_licensing_cost=Decimal("10000.00"),
        infrastructure_hosting_cost=Decimal("5000.00"),
        business_value_generated=Decimal("50000.00"),
    ))

    unified = UnifiedCapability.query.filter_by(
        source_table="business_capability", source_id=str(capability.id)
    ).first()
    if not unified:
        unified = UnifiedCapability(
            name=capability.name,
            source_table="business_capability",
            source_id=str(capability.id),
            organization_id=org.id,
        )
        db.session.add(unified)
        db.session.flush()
    unified.annual_cost = 20000.0
    unified.roi_percentage = 15.0

    solution = Solution(
        name=f"Unified CRM Platform {suffix}",
        organization_id=org.id,
        estimated_cost=Decimal("30000.00"),
        actual_cost=Decimal("35000.00"),
        roi_percentage=25.0,
    )
    db.session.add(solution)
    db.session.flush()

    business_case = BusinessCase(
        title=f"Consolidate the regional CRM estate {suffix}",
        description="One platform to replace the diverged regional CRM instances.",
        organization_id=org.id,
        capability_id=capability.id,
        strategic_initiative_id=initiative.id,
        solution_id=solution.id,
        created_by_id=user.id,
    )
    db.session.add(business_case)
    db.session.commit()

    # Pull the figures from the links, exactly as the page's own
    # "Pull financials from links" action does, and commit them.
    service.refresh_financials_from_links(business_case)
    db.session.refresh(business_case)

    figures = {
        "capex": str(business_case.capex),
        "opex_annual": str(business_case.opex_annual),
        "tco_3yr": str(business_case.tco_3yr),
        "financial_benefit_annual": str(business_case.financial_benefit_annual),
        "roi_percentage": str(business_case.roi_percentage),
    }
    links = {
        "initiative": initiative.name,
        "capability": capability.name,
        "solution": solution.name,
    }
    return business_case, figures, links


@pytest.mark.smoke
@pytest.mark.journey
def test_business_case_detail_shows_the_case_its_figures_and_links(
    app, browser, live_server
):
    """The owner opens the detail page and sees the case, its financial summary,
    and its links; every link lands on content; the other tenant's case is
    unreachable."""
    from app import db
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:8]
    other_suffix = uuid.uuid4().hex[:8]

    with app.app_context():
        org = Organization(
            name=f"BizCase Owner Org {suffix}", slug=f"bizcase-owner-org-{suffix}"
        )
        db.session.add(org)
        db.session.flush()
        owner = _make_owner(db, org, suffix)
        owner_email = owner.email
        # The persona the detail page serves is a business owner, not an
        # administrator: prove that before driving the browser as them.
        assert owner.is_platform_admin is False
        assert owner.is_org_admin is False
        assert owner.enterprise_role == "business_architect"
        business_case, figures, links = _seed_financed_case(db, org, owner, suffix)
        case_id = business_case.id
        case_title = business_case.title
        case_description = business_case.description

        # A SECOND organisation with its own business case and owner — the first
        # owner must never be able to read, see or name this tenant's row.
        other_org = Organization(
            name=f"Rival Org {other_suffix}", slug=f"rival-org-{other_suffix}"
        )
        db.session.add(other_org)
        db.session.flush()
        other_owner = _make_owner(db, other_org, other_suffix)
        from app.models.business_case import BusinessCase

        other_case = BusinessCase(
            title=f"Rival confidential case {other_suffix}",
            description="This belongs to another tenant and must never leak.",
            organization_id=other_org.id,
            created_by_id=other_owner.id,
        )
        db.session.add(other_case)
        db.session.commit()
        other_case_id = other_case.id
        other_case_title = other_case.title

    base = str(live_server)
    detail_path = f"/business-case/{case_id}"
    detail_url = base + detail_path

    context = browser.new_context(ignore_https_errors=True)
    page = context.new_page()
    page.on("dialog", lambda d: d.accept())
    try:
        _login(page, base, owner_email)

        # ---- The page shows the case ----------------------------------------
        page.goto(detail_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        heading = page.get_by_role("heading", name=re.compile(re.escape(case_title)))
        expect(heading.first).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_text(case_description, exact=False).first).to_be_visible(
            timeout=PAGE_TIMEOUT
        )

        # ---- The financial summary figures (same numbers as the unit test) --
        for name, value in figures.items():
            field = page.get_by_test_id(f"bc-input-{name}")
            expect(field).to_have_value(value, timeout=PAGE_TIMEOUT)

        # ---- The linked initiative / capability / solution names ------------
        # Each link renders as a native <select> whose currently-selected
        # option (bound by x-model to the stored link id) names the entity.
        for select_id, name in (
            ("#bc-link-capability", links["capability"]),
            ("#bc-link-initiative", links["initiative"]),
            ("#bc-link-solution", links["solution"]),
        ):
            select = page.locator(select_id)
            expect(select).to_be_visible(timeout=PAGE_TIMEOUT)
            # The entity's name is an option on the control...
            expect(select.locator("option", has_text=name)).to_have_count(
                1, timeout=PAGE_TIMEOUT
            )
            # ...and it is the one currently selected (the active link).
            expect(select.locator("option:checked")).to_have_text(
                re.compile(re.escape(name)), timeout=PAGE_TIMEOUT
            )

        # ---- Tenant isolation: the other org's case is unreachable ----------
        # Checked before the link walk (which may visit the sign-out link and
        # end the session): navigate as the signed-in owner to the OTHER
        # tenant's case. The route must refuse it (404) and never render its
        # title or any of its content.
        other_response = page.goto(
            base + f"/business-case/{other_case_id}",
            wait_until="domcontentloaded",
            timeout=PAGE_TIMEOUT,
        )
        assert other_response is not None and other_response.status == 404, (
            "cross-tenant read returned "
            f"{other_response.status if other_response else 'no response'}, not 404"
        )
        expect(
            page.get_by_text("Business case not found", exact=False).first
        ).to_be_visible(timeout=PAGE_TIMEOUT)
        assert other_case_title not in page.content(), (
            "the other tenant's case title leaked into the response"
        )

        # ---- Every link on the page lands on content (no broken link) -------
        page.goto(detail_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(heading.first).to_be_visible(timeout=PAGE_TIMEOUT)
        # Sign-out (and other session-ending actions) are not broken links —
        # following one would simply end the session, so they are excluded.
        skip_fragments = ("/account/logout", "/logout", "/account/signout")
        hrefs = []
        for link in page.locator("a[href]").all():
            href = link.get_attribute("href")
            if not href:
                continue
            if href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue
            abs_href = href if href.startswith("http") else base + href
            if not abs_href.startswith(base):
                continue  # only follow links within this app
            if any(frag in abs_href for frag in skip_fragments):
                continue
            hrefs.append(abs_href)

        assert hrefs, "detail page has no in-app links to check"
        seen = set()
        for target in hrefs:
            if target in seen:
                continue
            seen.add(target)
            resp = page.request.get(target)
            assert resp.status == 200, f"{target} returned {resp.status}"
            headers = resp.headers
            # A file download (e.g. the export link) is a real, working link,
            # but it is not an HTML page to render a heading on — a 200 with
            # the bytes is the content assertion it needs.
            is_download = "attachment" in headers.get("content-disposition", "")
            is_html = "text/html" in headers.get("content-type", "")
            if is_download or not is_html:
                continue
            dest = context.new_page()
            dest.goto(target, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            expect(dest.get_by_role("heading").first).to_be_visible(
                timeout=PAGE_TIMEOUT
            )
            dest.close()
    finally:
        context.close()
