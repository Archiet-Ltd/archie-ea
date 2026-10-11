"""Sidebar counts must be the signed-in tenant's, and must not leak between tenants.

Found by driving a real browser: signed in as a business architect of the SG
Tadley demo tenant (14 applications), the sidebar read "Applications (38)" —
the total across every organisation, 24 of which belong to a different customer.
"All Elements (434)" against that tenant's 71, likewise.

Two independent defects produced it, and each is enough on its own:

1. ``db.session.query(db.func.count(Model.id))`` is a COLUMN query, not an
   entity query, and ``with_loader_criteria`` — the whole basis of tenant
   isolation in this codebase — only applies to entity queries. So the counts
   were never filtered, in any request, for any tenant.
2. ``_nav_counts_cache`` was a single module-level dict with no tenant in its
   key. Even with correct filtering, the first organisation to load a page
   would populate it and every other organisation would be served that
   organisation's numbers for the next five minutes.
"""

import pytest

from app._bootstrap.context_processors import compute_nav_counts


@pytest.fixture
def two_tenants(app, db_session, make_org):
    """Two orgs with deliberately different application counts."""
    from app.models.application_portfolio import ApplicationComponent

    # Built with NO request context: organization_id is explicit, so TenantMixin's
    # before_flush has nothing to fill in, and this avoids mutating g.current_org_id
    # across a single flush — doing that silently lost the first org's row.
    small = make_org("nav-small")
    large = make_org("nav-large")
    db_session.add(ApplicationComponent(name="Solo App", organization_id=small.id))
    for i in range(3):
        db_session.add(
            ApplicationComponent(name=f"Big App {i}", organization_id=large.id)
        )
    db_session.commit()
    return small, large


def test_counts_are_scoped_to_the_signed_in_tenant(app, db_session, two_tenants):
    from app.models.application_portfolio import ApplicationComponent

    small, large = two_tenants

    actual = {
        oid: db_session.query(ApplicationComponent)
        .filter(ApplicationComponent.organization_id == oid)
        .count()
        for oid in (small.id, large.id)
    }
    assert actual == {small.id: 1, large.id: 3}, f"fixture precondition failed: {actual}"

    assert compute_nav_counts(small.id)["applications"] == 1, (
        "sidebar application count is not scoped to the signed-in organisation"
    )
    assert compute_nav_counts(large.id)["applications"] == 3


def test_counts_do_not_leak_through_the_cache(app, db_session, two_tenants):
    """The decisive one: ask as tenant A, then as tenant B, back-to-back."""
    small, large = two_tenants

    first = compute_nav_counts(small.id)["applications"]
    second = compute_nav_counts(large.id)["applications"]
    third = compute_nav_counts(small.id)["applications"]

    assert (first, second, third) == (1, 3, 1), (
        f"cache served one tenant's counts to another: got {first}, {second}, {third}"
    )


def test_no_tenant_context_does_not_poison_a_tenant_entry(app, db_session, two_tenants):
    """An unauthenticated render must not overwrite a tenant's cached counts."""
    small, _large = two_tenants

    compute_nav_counts(small.id)
    compute_nav_counts(None)  # e.g. the login page
    assert compute_nav_counts(small.id)["applications"] == 1


def test_unknown_organisation_never_returns_global_tenant_counts(app, db_session, make_org):
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement
    from app.models.business_capabilities import BusinessCapability

    before = {
        "applications": db_session.query(ApplicationComponent).count(),
        "elements": db_session.query(ArchiMateElement).count(),
        "capabilities": db_session.query(BusinessCapability).count(),
    }

    first = make_org("nav-unknown-a")
    second = make_org("nav-unknown-b")
    db_session.add_all([
        ApplicationComponent(name="First app", organization_id=first.id),
        ApplicationComponent(name="Second app", organization_id=second.id),
        ArchiMateElement(
            name="First element",
            type="ApplicationComponent",
            organization_id=first.id,
        ),
        ArchiMateElement(
            name="Second element",
            type="ApplicationComponent",
            organization_id=second.id,
        ),
        BusinessCapability(name="First capability", organization_id=first.id),
        BusinessCapability(name="Second capability", organization_id=second.id),
    ])
    db_session.commit()

    global_counts = {
        "applications": db_session.query(ApplicationComponent).count(),
        "elements": db_session.query(ArchiMateElement).count(),
        "capabilities": db_session.query(BusinessCapability).count(),
    }
    assert global_counts["applications"] > before["applications"]
    assert global_counts["elements"] > before["elements"]
    assert global_counts["capabilities"] > before["capabilities"]

    counts = compute_nav_counts(None)

    assert counts["applications"] == 0
    assert counts["elements"] == 0
    assert counts["capabilities"] == 0
    assert {
        "applications": counts["applications"],
        "elements": counts["elements"],
        "capabilities": counts["capabilities"],
    } != global_counts, "unknown organisation leaked another tenant's totals"


def test_a_record_created_after_a_read_is_counted_on_the_next_read(
    app, db_session, two_tenants
):
    """No cross-request cache: the next page load after a create counts it.

    The counts were held for five minutes per worker process, so the pages that
    decide "is anything modelled yet" kept saying nothing for that long after
    the first application was added.
    """
    from app.models.application_portfolio import ApplicationComponent

    small, _large = two_tenants
    with app.test_request_context("/"):
        assert compute_nav_counts(small.id)["applications"] == 1
    db_session.add(ApplicationComponent(name="Second App", organization_id=small.id))
    db_session.commit()
    with app.test_request_context("/"):
        assert compute_nav_counts(small.id)["applications"] == 2, (
            "a count read before the create was served again after it"
        )
    assert compute_nav_counts(small.id)["applications"] == 2
