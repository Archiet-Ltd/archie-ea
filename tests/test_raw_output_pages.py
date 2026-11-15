"""Two pages must read as answers, never as code.

The consolidation opportunities page printed the savings forecast as a Python
dictionary, and the Chief Architect Workbench printed database predicates as
body text. These tests render both pages and pin that neither leaks raw output,
and that the counts on the workbench still equal the records of the signed-in
organisation (and only that organisation).
"""

import re

import pytest

from app.models.organization import Organization
from app.models.user import User

OPPORTUNITIES = "/enterprise/consolidation/opportunities"
WORKBENCH = "/solutions/architect-synthesis"


def _org(db_session, slug):
    org = Organization(name=f"Raw output {slug}", slug=f"raw-output-{slug}")
    db_session.add(org)
    db_session.flush()
    return org


def _user(db_session, org):
    user = User(
        email=f"raw-output-{org.id}@example.com",
        first_name="Chief",
        last_name="Architect",
        organization_id=org.id,
        enterprise_role="enterprise_architect",
        confirmed=True,
    )
    user.password = "TestPass123!"
    db_session.add(user)
    db_session.flush()
    return user


def _visible(html):
    """The page content from its heading on, without script blocks.

    The shared layout carries script and Alpine object literals
    (``{'Content-Type': ...}``) that are code by design, so the "no raw
    dictionary" check starts where the page's own content starts.
    """
    start = html.find("<h1")
    body = html[start:] if start >= 0 else html
    return re.sub(r"<script.*?</script>", "", body, flags=re.DOTALL | re.IGNORECASE)


def _forecast_area(html):
    """Heading to the opportunities table: where the savings estimate is shown."""
    body = _visible(html)
    end = body.find("<table")
    return body[:end] if end > 0 else body


def _row_counts(body, label):
    """(value, denominator) the workbench answer gives for a labelled row."""
    for lens in body["enterprise"]["lenses"]:
        for row in lens["missing"]:
            if row["label"] == label:
                return row["value"], row["of"]
    raise AssertionError(f"row {label!r} not found with a count")


# -- consolidation opportunities ------------------------------------------------


def test_opportunities_page_states_empty_forecast_in_words(
    db_session, client, login_as, tenant_ctx
):
    org = _org(db_session, "opp-empty")
    with tenant_ctx(org.id):
        user = _user(db_session, org)
    login_as(client, user)

    response = client.get(OPPORTUNITIES)

    assert response.status_code == 200
    html = _forecast_area(response.get_data(as_text=True))
    assert "{'" not in html
    assert "forecast_by_year" not in html
    assert "No savings estimated yet: no opportunity has been included" in html


def test_opportunities_page_shows_forecast_years_as_figures(
    db_session, client, login_as, tenant_ctx, monkeypatch
):
    from app.routes import unified_low_priority_routes as routes

    monkeypatch.setattr(
        routes.consolidation_service,
        "get_savings_forecast",
        lambda: {
            "forecast_by_year": {2027: 12000.0, 2028: 48000.0},
            "opportunities_included": 2,
        },
    )
    org = _org(db_session, "opp-years")
    with tenant_ctx(org.id):
        user = _user(db_session, org)
    login_as(client, user)

    html = _forecast_area(client.get(OPPORTUNITIES).get_data(as_text=True))

    assert "{'" not in html
    assert "forecast_by_year" not in html
    assert "FY 2027" in html and "FY 2028" in html
    assert "12,000" in html and "48,000" in html
    assert "From 2 opportunities" in html
    assert "No savings estimated yet" not in html


# -- architect synthesis --------------------------------------------------------


def _seed(db_session, tenant_ctx, org, *, capabilities, applications):
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capabilities import BusinessCapability

    with tenant_ctx(org.id):
        for index in range(capabilities):
            db_session.add(
                BusinessCapability(
                    name=f"Cap {org.id}-{index}",
                    business_owner="Owner" if index == 0 else ("" if index == 1 else None),
                )
            )
        for index in range(applications):
            db_session.add(ApplicationComponent(name=f"App {org.id}-{index}"))
        db_session.flush()


def test_synthesis_counts_equal_the_organisation_records(
    db_session, client, login_as, tenant_ctx
):
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capabilities import BusinessCapability

    org_a = _org(db_session, "syn-a")
    org_b = _org(db_session, "syn-b")
    _seed(db_session, tenant_ctx, org_a, capabilities=3, applications=4)
    _seed(db_session, tenant_ctx, org_b, capabilities=7, applications=9)
    with tenant_ctx(org_a.id):
        user = _user(db_session, org_a)
        apps_in_list = ApplicationComponent.query.count()
        caps_total = BusinessCapability.query.count()
        caps_without_owner = BusinessCapability.query.filter(
            (BusinessCapability.business_owner.is_(None))
            | (BusinessCapability.business_owner == "")
        ).count()
    login_as(client, user)

    body = client.get(WORKBENCH + "/api").get_json()

    # The signed-in organisation's own records, not the other organisation's.
    assert (apps_in_list, caps_total, caps_without_owner) == (4, 3, 2)
    assert _row_counts(body, "Capabilities with no business owner") == (
        caps_without_owner,
        caps_total,
    )
    assert _row_counts(body, "Applications with no end-of-life date") == (
        apps_in_list,
        apps_in_list,
    )


def test_plain_source_never_returns_a_predicate():
    from app.modules.solutions_strategic.v2.services.enterprise_posture_service import (
        plain_source,
    )

    assert plain_source("Widget.business_owner IS NULL") == "No business owner recorded"
    assert "IS NULL" not in plain_source("Widget.review_date IS NULL")
    assert "Widget." not in plain_source("Widget.review_date")
