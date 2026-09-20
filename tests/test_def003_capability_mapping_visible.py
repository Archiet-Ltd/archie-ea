"""DEF-003 follow-up: a capability mapped through the "Map Capability" modal
persisted correctly (UnifiedApplicationCapabilityMapping, the canonical store
per ADR-0008) but never appeared on the application detail page, because the
page's "Mapped Capabilities" table only ever read the legacy
ApplicationCapabilityMapping table. Confirmed live on production: POST
/dashboard/applications/<id>/capability-mappings succeeded, the row existed
in the database, and the "Mapped Capabilities" section still showed 0 after
a reload.

The original DEF-003 crash (a raw BusinessCapability.id posted into a FK
that targets unified_capabilities.id) is already fixed elsewhere
(detail_layer_routes.py's application_capability_mapping_create resolves
through UnifiedCapability.source_id) - this file is the newly-discovered
display-side half of the same underlying dual-store defect.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _login(client, user_id):
    from tests._session_test_helpers import mint_test_sid
    _sid = mint_test_sid(user_id)
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True
        if _sid:
            sess["_sid"] = _sid

    from flask import g, has_app_context

    if not has_app_context():
        return
    for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
        if hasattr(g, cached):
            delattr(g, cached)


def _make_user(db_session, make_org, label):
    from app.models.user import User

    org = make_org(f"def003-{label}")
    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"def003-{label}-{suffix}@example.com",
        first_name="DEF003",
        last_name="Test",
        organization_id=org.id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    user.password = "Sup3rSecret!23"
    db_session.add(user)
    db_session.flush()
    db_session.commit()
    return user.id, org


def _unified_capability_id(db_session, business_cap, org, **fields):
    """Id of the unified_capabilities row that stands for ``business_cap``.

    Where the provenance index exists, creating the business capability already
    projects its unified row in the same flush, and a second row with the same
    provenance violates the unique index. Use that row; only on a database
    without the index does nothing project it, and then it is created here.
    """
    from sqlalchemy import text

    from app.models.unified_capability import UnifiedCapability

    projected = db_session.execute(
        text(
            "SELECT id FROM unified_capabilities "
            "WHERE source_table = 'business_capability' AND source_id = :sid"
        ),
        {"sid": str(business_cap.id)},
    ).scalar()
    if projected is not None:
        return projected

    unified_cap = UnifiedCapability(
        name=business_cap.name,
        code=f"BC-{business_cap.id}",
        level=1,
        scope="tenant",
        organization_id=org.id,
        source_table="business_capability",
        source_id=str(business_cap.id),
        **fields,
    )
    db_session.add(unified_cap)
    db_session.flush()
    return unified_cap.id


def test_mapping_created_only_in_unified_store_is_visible_on_detail_page(
    app, db_session, make_org
):
    """The exact production repro: a UnifiedApplicationCapabilityMapping row
    with no matching legacy ApplicationCapabilityMapping row must still
    render in the 'Mapped Capabilities' table."""
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capability import BusinessCapability
    from app.models.unified_application_capability_mapping import (
        UnifiedApplicationCapabilityMapping,
    )

    user_id, org = _make_user(db_session, make_org, "visible")

    app_obj = ApplicationComponent(
        name=f"DEF003 App {uuid.uuid4().hex[:8]}",
        organization_id=org.id,
    )
    db_session.add(app_obj)
    db_session.flush()

    business_cap = BusinessCapability(
        name="Unified-Only Test Capability",
        organization_id=org.id,
        level=1,
    )
    db_session.add(business_cap)
    db_session.flush()

    unified_capability_id = _unified_capability_id(
        db_session, business_cap, org, category="Test Category"
    )

    mapping = UnifiedApplicationCapabilityMapping(
        unified_capability_id=unified_capability_id,
        application_component_id=app_obj.id,
        support_level="primary",
        coverage_percentage=75,
        maturity_level=3,
        is_strategic=True,
    )
    db_session.add(mapping)
    db_session.commit()

    client = app.test_client()
    _login(client, user_id)
    resp = client.get(f"/applications/{app_obj.id}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "Unified-Only Test Capability" in html
    assert "0 capabilities linked" not in html


def test_legacy_and_unified_mappings_for_the_same_capability_do_not_duplicate(
    app, db_session, make_org
):
    """A capability already linked via the legacy Abacus-synced table must
    not also render a second row if a UnifiedApplicationCapabilityMapping
    row exists for the same underlying BusinessCapability."""
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capability import BusinessCapability
    from app.models.unified_application_capability_mapping import (
        UnifiedApplicationCapabilityMapping,
    )

    user_id, org = _make_user(db_session, make_org, "dedup")

    app_obj = ApplicationComponent(
        name=f"DEF003 Dedup App {uuid.uuid4().hex[:8]}",
        organization_id=org.id,
    )
    db_session.add(app_obj)
    db_session.flush()

    business_cap = BusinessCapability(
        name="Dually-Mapped Test Capability",
        organization_id=org.id,
        level=1,
    )
    db_session.add(business_cap)
    db_session.flush()

    legacy_mapping = ApplicationCapabilityMapping(
        organization_id=org.id,
        application_component_id=app_obj.id,
        business_capability_id=business_cap.id,
        support_level="primary",
    )
    db_session.add(legacy_mapping)

    unified_capability_id = _unified_capability_id(db_session, business_cap, org)

    unified_mapping = UnifiedApplicationCapabilityMapping(
        unified_capability_id=unified_capability_id,
        application_component_id=app_obj.id,
        support_level="primary",
    )
    db_session.add(unified_mapping)
    db_session.commit()

    client = app.test_client()
    _login(client, user_id)
    resp = client.get(f"/applications/{app_obj.id}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    # The summary stat card ("Mapped Capabilities" / count) is the direct,
    # unambiguous proof: it comes straight from `capabilities | length` in
    # the template, so a duplicate row in cap_table would inflate it to 2.
    stat_idx = html.index('title="Enterprise capabilities linked to this application"')
    stat_card = html[max(0, stat_idx - 400):stat_idx]
    assert ">1<" in stat_card, stat_card

    # And the actual table heading (<h3>Mapped Capabilities</h3>, which comes
    # after the summary stat card on the page) must show the capability name
    # exactly once, not once per underlying store.
    heading_idx = html.index("Mapped Capabilities", stat_idx)
    table_html = html[heading_idx:heading_idx + 4000]
    assert table_html.count("Dually-Mapped Test Capability") == 1, table_html
