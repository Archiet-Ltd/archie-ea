"""Model freshness: the share of one organisation's model confirmed or synchronised in 90 days.

Seeds two organisations with applications, owners, reviewed elements, undated
elements and relationships, then checks the figures ``measure_freshness``
returns, and that the model health page shows the same figure and the stalest
owners. Organisation B's rows never reach organisation A's numbers or page.

Uses the shared fixtures in tests/conftest.py (db_session rolls everything back).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from bs4 import BeautifulSoup

pytestmark = pytest.mark.usefixtures("db_session")

NOW = datetime(2026, 9, 1, 12, 0, 0)


def _user(db_session, org, label):
    from app.models.user import User

    user = User(
        email=f"fresh-{label}-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Fresh",
        last_name=label,
        organization_id=org.id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    db_session.add(user)
    db_session.flush()
    return user


def _app(db_session, org, name, *, saved, owner=None, synced=None):
    """An application (its element is created with it) saved at ``saved``."""
    from app.models.application_owner import ApplicationOwner
    from app.models.application_portfolio import ApplicationComponent

    component = ApplicationComponent(name=f"{name} {uuid.uuid4().hex[:6]}", organization_id=org.id)
    db_session.add(component)
    db_session.flush()
    # A bulk update, so the column's on-update default does not replace the date.
    db_session.query(ApplicationComponent).filter(ApplicationComponent.id == component.id).update(
        {"updated_at": saved, "last_sync_from_abacus": synced}, synchronize_session=False
    )
    if owner is not None:
        db_session.add(
            ApplicationOwner(
                application_id=component.id,
                user_id=owner.id,
                organization_id=org.id,
                ownership_type="primary",
            )
        )
    db_session.flush()
    return component


def _element(db_session, org, name, layer, reviewed=None):
    from app.models.archimate_core import ArchiMateElement

    element = ArchiMateElement(
        name=f"{name} {uuid.uuid4().hex[:6]}",
        type="BusinessProcess",
        layer=layer,
        organization_id=org.id,
        last_reviewed_date=reviewed,
    )
    db_session.add(element)
    db_session.flush()
    return element


def _relationship(db_session, org, source, target, updated):
    from app.models.archimate_core import ArchiMateRelationship

    rel = ArchiMateRelationship(
        type="serving", source_id=source.id, target_id=target.id, organization_id=org.id
    )
    db_session.add(rel)
    db_session.flush()
    db_session.query(ArchiMateRelationship).filter(ArchiMateRelationship.id == rel.id).update(
        {"updated_at": updated}, synchronize_session=False
    )
    db_session.flush()
    return rel


@pytest.fixture
def two_orgs(db_session, make_org):
    from app.services.billing_plans import set_contract_plan

    org_a, org_b = make_org("fresh-a"), make_org("fresh-b")
    for org in (org_a, org_b):
        set_contract_plan(org, "enterprise", None)  # more people than Community admits
    db_session.flush()
    a_recent = _user(db_session, org_a, "Recent")
    a_stale = _user(db_session, org_a, "Stalest")
    a_middle = _user(db_session, org_a, "Middle")
    b_owner = _user(db_session, org_b, "OtherOrg")

    # Organisation A.
    _app(db_session, org_a, "Fresh app", saved=NOW - timedelta(days=10), owner=a_recent)
    _app(db_session, org_a, "Old app", saved=NOW - timedelta(days=400), owner=a_stale)
    # Saved long ago but synchronised last week: the sync date counts.
    _app(db_session, org_a, "Synced app", saved=NOW - timedelta(days=300),
         synced=NOW - timedelta(days=7), owner=a_middle)
    _app(db_session, org_a, "Middle app", saved=NOW - timedelta(days=120), owner=a_middle)
    reviewed = _element(db_session, org_a, "Reviewed process", "Business", reviewed=NOW - timedelta(days=5))
    undated = _element(db_session, org_a, "Undated process", "Business")
    _relationship(db_session, org_a, reviewed, undated, NOW - timedelta(days=3))
    _relationship(db_session, org_a, undated, reviewed, NOW - timedelta(days=200))

    # Organisation B: a much staler owner who must never appear for A.
    _app(db_session, org_b, "Other app", saved=NOW - timedelta(days=900), owner=b_owner)
    _element(db_session, org_b, "Other process", "Business", reviewed=NOW - timedelta(days=1))
    _element(db_session, org_b, "Other undated", "Strategy")

    return {"a": org_a, "b": org_b, "a_stale": a_stale, "a_middle": a_middle,
            "a_recent": a_recent, "b_owner": b_owner}


def test_freshness_counts_only_this_organisations_elements(db_session, two_orgs):
    from app.modules.genome.services.freshness import measure_freshness

    report = measure_freshness(two_orgs["a"].id, db_session, now=NOW)

    # Four application elements, one reviewed element, one undated element.
    assert report["elements"] == {
        "total": 6, "fresh": 3, "stale": 2, "not_recorded": 1,
        "share": pytest.approx(3 / 6), "oldest": (NOW - timedelta(days=400)).isoformat(),
    }
    layers = {row["layer"]: row for row in report["by_layer"]}
    assert set(layers) == {"application", "business"}
    assert (layers["application"]["total"], layers["application"]["fresh"]) == (4, 2)
    assert (layers["business"]["fresh"], layers["business"]["not_recorded"]) == (1, 1)
    assert report["relationships"]["total"] == 2
    assert report["relationships"]["fresh"] == 1


def test_owners_and_the_stalest_owner_list(db_session, two_orgs):
    from app.modules.genome.services.freshness import measure_freshness

    report = measure_freshness(two_orgs["a"].id, db_session, now=NOW)

    owners = {row["user_id"]: row for row in report["by_owner"]}
    assert set(owners) == {two_orgs["a_recent"].id, two_orgs["a_stale"].id, two_orgs["a_middle"].id}
    assert two_orgs["b_owner"].id not in owners
    middle = owners[two_orgs["a_middle"].id]
    assert (middle["total"], middle["fresh"], middle["stale"]) == (2, 1, 1)
    # The two elements with no application record have no owner record.
    assert report["no_owner"]["total"] == 2

    stalest = [row["user_id"] for row in report["stalest_owners"]]
    assert stalest == [two_orgs["a_stale"].id, two_orgs["a_middle"].id]


def test_other_organisation_sees_only_its_own_figures(db_session, two_orgs):
    from app.modules.genome.services.freshness import measure_freshness

    report = measure_freshness(two_orgs["b"].id, db_session, now=NOW)

    assert report["elements"]["total"] == 3
    assert report["elements"]["fresh"] == 1
    assert [row["user_id"] for row in report["stalest_owners"]] == [two_orgs["b_owner"].id]
    assert report["relationships"]["total"] == 0
    assert report["relationships"]["share"] is None


def test_no_recorded_dates_gives_no_figure_rather_than_zero(db_session, make_org):
    from app.modules.genome.services.freshness import measure_freshness

    org = make_org("fresh-undated")
    _element(db_session, org, "Undated one", "Business")
    _element(db_session, org, "Undated two", "Motivation")

    report = measure_freshness(org.id, db_session, now=NOW)

    assert report["elements"]["total"] == 2
    assert report["elements"]["not_recorded"] == 2
    assert report["elements"]["share"] is None
    assert report["stalest_owners"] == []


def _freshness_section(response):
    assert response.status_code == 200, response.get_data(as_text=True)[:1500]
    soup = BeautifulSoup(response.get_data(as_text=True), "html.parser")
    section = soup.find(id="model-freshness")
    assert section is not None, "the model health page has no freshness section"
    return section


def test_model_health_page_shows_freshness_and_stalest_owners(client, login_as, two_orgs):
    login_as(client, two_orgs["a_recent"])
    section = _freshness_section(client.get("/genome/model-health/"))

    # Measured now, so figures differ from the fixed-date tests; the page must
    # carry a percentage figure and the stalest owner first.
    share = section.find(attrs={"data-testid": "freshness-share"}).get_text(strip=True)
    assert share.endswith("%")
    stalest = section.find(attrs={"data-testid": "stalest-owners"})
    assert stalest is not None
    names = [li.get_text(" ", strip=True) for li in stalest.find_all("li")]
    assert names and names[0].startswith("Fresh Stalest")
    text = section.get_text(" ", strip=True)
    assert "OtherOrg" not in text


def test_model_health_page_for_other_organisation_hides_first_organisations_owners(
    client, login_as, two_orgs
):
    login_as(client, two_orgs["b_owner"])
    section = _freshness_section(client.get("/genome/model-health/"))

    text = section.get_text(" ", strip=True)
    assert "Fresh OtherOrg" in text
    for label in ("Stalest", "Middle", "Recent"):
        assert f"Fresh {label}" not in text
