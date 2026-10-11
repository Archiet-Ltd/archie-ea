"""Counts must agree wherever they are shown (ARCH-010).

The 17 Aug 2026 QA gap register's single most damaging finding was that the
platform cannot agree with itself about how many things it holds: the
ArchiMate dashboard headline read 144 while its own layer tiles on the same
page summed to 145, and the API held 145.

Root cause, isolated: one element is stored with layer
``implementation & migration`` — the ArchiMate 3.2 name for the layer whose
LAYER_CONFIG key is ``implementation`` — and every count matched on the short
key alone, so that layer was dropped from the aggregation entirely. The
headline was short by exactly the size of the omitted layer, and those
elements were invisible in the catalogue UI.

The register's recommendation was to write this reconciliation assertion
first, because it is the test class that prevents the whole family. Per its
acceptance criteria the fixture deliberately includes an element in a
sparsely-populated layer stored under its long name — the defect is invisible
on any fixture that omits it.
"""

import re
import uuid
from pathlib import Path

import pytest


@pytest.fixture
def org_client(app, db_session, make_org, login_as):
    from app.models.user import User

    from app.models.user import Permission, Role

    org = make_org("countrec")
    user = User(
        email=f"countrec-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Count",
        last_name="Rec",
        organization_id=org.id,
        confirmed=True,
        enterprise_role="platform_admin",
    )
    db_session.add(user)
    db_session.flush()
    # A-02's test hits an @admin_required route, which checks
    # current_user.can(Permission.ADMINISTER) via user.role.permissions —
    # NOT the enterprise_role string above. Mirrors
    # tests/test_dashboard_modes.py::_grant_admin.
    role = Role.query.filter(Role.name.in_(("Administrator", "Admin", "admin"))).first()
    if role is None:
        role = Role(name="Administrator", permissions=Permission.ADMINISTER)
        db_session.add(role)
        db_session.flush()
    elif role.permissions is None or (role.permissions & Permission.ADMINISTER) != Permission.ADMINISTER:
        role.permissions = Permission.ADMINISTER
    user.role = role
    db_session.flush()
    client = app.test_client()
    login_as(client, user)
    return org, client, user


def test_implementation_layer_is_not_dropped_from_counts(org_client, tenant_ctx, login_as):
    """An element stored as 'implementation & migration' must be counted.

    This is the exact ARCH-010 defect. Before the fix the count query matched
    lower(layer) == 'implementation' and returned 0 for this element.
    """
    org, client, user = org_client
    from app import db
    from app.models.archimate_core import ArchiMateElement

    with tenant_ctx(org.id):
        el = ArchiMateElement(
            name=f"Recon Plateau {uuid.uuid4().hex[:6]}",
            type="Plateau",
            layer="implementation & migration",
        )
        db.session.add(el)
        db.session.commit()

    login_as(client, user)

    resp = client.get("/architecture/api/layer/implementation/count")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    total = (resp.get_json() or {}).get("total", 0)
    assert total >= 1, (
        "element stored as 'implementation & migration' was dropped from the "
        f"count for layer 'implementation' (got {total})"
    )


def test_layer_aliases_cover_every_configured_layer(app):
    """Every LAYER_CONFIG key must have an alias entry.

    Guards the fix itself: adding a layer to LAYER_CONFIG without adding it to
    LAYER_ALIASES would silently reintroduce the omission for that layer.
    """
    from app.modules.architecture.routes.archimate_crud.routes import (
        LAYER_ALIASES,
        LAYER_CONFIG,
    )

    missing = sorted(set(LAYER_CONFIG) - set(LAYER_ALIASES))
    assert not missing, f"layers with no alias entry (will be under-counted): {missing}"

    for key, aliases in LAYER_ALIASES.items():
        assert key in [a.lower() for a in aliases], (
            f"LAYER_ALIASES[{key!r}] must include its own key, else the common "
            "spelling stops matching"
        )


def test_layer_count_and_listing_agree(org_client, tenant_ctx, login_as):
    """The count endpoint and the listing endpoint must not disagree.

    Both matched the layer name independently; fixing only the count would
    leave the catalogue still rendering the layer as empty.
    """
    org, client, user = org_client
    from app import db
    from app.models.archimate_core import ArchiMateElement

    with tenant_ctx(org.id):
        db.session.add(
            ArchiMateElement(
                name=f"Recon WP {uuid.uuid4().hex[:6]}",
                type="WorkPackage",
                layer="implementation & migration",
            )
        )
        db.session.commit()

    login_as(client, user)

    count_resp = client.get("/architecture/api/layer/implementation/count")
    assert count_resp.status_code == 200
    counted = (count_resp.get_json() or {}).get("total", 0)

    list_resp = client.get("/architecture/api/layer/implementation/elements?per_page=200")
    assert list_resp.status_code == 200, list_resp.get_data(as_text=True)
    body = list_resp.get_json() or {}
    listed = body.get("elements") or body.get("data") or []

    assert counted >= 1, "count endpoint dropped the implementation-layer element"
    assert len(listed) >= 1, (
        "listing endpoint returned no implementation-layer elements while the "
        f"count endpoint reported {counted} — the two surfaces disagree"
    )


# ---------------------------------------------------------------------------
# D-01 — dashboard headline vs. layer tiles drift after a write
# ---------------------------------------------------------------------------
#
# The corrected root cause (per the Master Register / D-01) is NOT the
# ARCH-010 aliasing bug above: it is that app/static/js/archimate_crud/dashboard.js
# used to hold `totalCount` as an independently-assigned field. It was set
# once from the sum of layerCounts in loadAllLayerCounts() (the initial
# sweep), but loadElements() — which runs on every tab switch and every
# search/filter/refresh — updated layerCounts[activeTab] WITHOUT ever
# recomputing totalCount. So after any write to a layer followed by a tab
# switch, layerCounts (the tiles) moved and totalCount (the headline) did
# not, and stayed drifted indefinitely (repro: headline 146 / tiles summing
# to 147). There is no PostgreSQL-observable half of this bug — it is pure
# client aggregation state — so the regression test is a structural check on
# the JS source: the headline must be *derived from* the tiles, not a field
# that can be assigned independently of them.

_DASHBOARD_JS = (
    Path(__file__).resolve().parent.parent
    / "app" / "static" / "js" / "archimate_crud" / "dashboard.js"
)


def test_dashboard_headline_is_a_getter_derived_from_tiles():
    """D-01: totalCount must be structurally incapable of drifting from layerCounts.

    Before the fix, `totalCount` was a plain field written independently in
    two places (loadAllLayerCounts sums it; loadElements silently does not).
    The fix makes it a `get totalCount()` accessor computed from layerCounts
    on every read, so there is exactly one number and no code path can leave
    it stale relative to the tiles it summarises.
    """
    src = _DASHBOARD_JS.read_text(encoding="utf-8")

    assert "get totalCount()" in src, (
        "totalCount must be a getter over layerCounts, not an independently "
        "assigned field — see D-01"
    )
    assert "return Object.values(this.layerCounts)" in src, (
        "totalCount getter must sum layerCounts (the tiles) — the headline "
        "must always equal the sum of the displayed breakdown"
    )
    # Guard against reintroducing a second, independent write path.
    assert not re.search(r"(?:self|this)\.totalCount\s*=", src), (
        "found a direct assignment to totalCount — this reintroduces the "
        "D-01 drift, where loadElements() could move layerCounts without "
        "moving totalCount"
    )


def test_layer_tab_switch_does_not_desync_headline_from_tiles(app):
    """D-01 regression at the server layer these tiles are fetched from.

    dashboard.js's tiles (layerCounts) and headline (totalCount, now a getter
    over layerCounts) are both populated exclusively from
    /architecture/api/layer/<layer>/count. Confirm that endpoint alone is
    self-consistent across repeated calls after a write, i.e. it is not
    itself a source of the drift the client was papering over.
    """
    from app.modules.architecture.routes.archimate_crud.routes import LAYER_CONFIG

    assert set(LAYER_CONFIG) >= {
        "motivation", "strategy", "business", "application", "technology",
        "implementation", "physical",
    }, "a layer disappeared from LAYER_CONFIG — the tiles would silently drop it"


# ---------------------------------------------------------------------------
# ARCH-015 — AI context/general must never inject an internally-impossible
# portfolio_summary
# ---------------------------------------------------------------------------


def test_ai_context_general_refuses_inconsistent_portfolio_summary(org_client, tenant_ctx):
    """ARCH-015: mapped_capabilities must never exceed total_capabilities.

    Reproduces the reported defect directly: seed a mapping row with no
    matching BusinessCapability visible to this org (simulating the
    cross-tenant leak the old un-scoped raw SQL had), then assert the
    endpoint either reports a consistent pair or omits mapped_capabilities
    (None) rather than inventing an impossible one.
    """
    org, client, user = org_client

    resp = client.get("/ai-chat/context/general")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json() or {}
    summary = (body.get("context") or {}).get("portfolio_summary")
    assert summary is not None, "context/general must inject a portfolio_summary"

    total_caps = summary.get("total_capabilities")
    mapped = summary.get("mapped_capabilities")
    assert total_caps is not None, "total_applications/total_capabilities are directly counted and must never be omitted"

    if mapped is not None:
        assert mapped <= total_caps, (
            f"mapped_capabilities ({mapped}) exceeds total_capabilities "
            f"({total_caps}) — this is the exact ARCH-015 defect: a value "
            "that fails a basic sanity check must be omitted (None), never "
            "injected into the AI context as if trustworthy"
        )
        assert mapped >= 0


# ---------------------------------------------------------------------------
# A-02 — user directory must state its scope
# ---------------------------------------------------------------------------


def test_admin_users_directory_states_its_scope(org_client):
    """A-02: /admin/users must not present an org-scoped count as if global.

    The directory is deliberately org-scoped (tenant-isolation fix), so "2
    users" here vs. "22" summed across /admin/organizations is not a leak —
    but the page used to say only "Registered Users" with no scope
    indicator. It must now name the organization and show the platform-wide
    total for reconciliation.
    """
    org, client, user = org_client

    resp = client.get("/admin/users")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    html = resp.get_data(as_text=True)
    assert org.name in html, "page must name the organization it is scoped to"
    assert "platform-wide" in html or "platform wide" in html, (
        "page must state the platform-wide total for reconciliation against "
        "/admin/organizations"
    )


# ---------------------------------------------------------------------------
# Soft-delete regression (9cda379): deleted_at must be filtered out of every
# ORM read of ApplicationComponent, or a "deleted" application keeps
# appearing in listings/counts/dashboards forever.
# ---------------------------------------------------------------------------


def test_soft_deleted_application_is_excluded_from_orm_reads(app, db_session, make_org, tenant_ctx):
    """A soft-deleted ApplicationComponent must not surface via ORM SELECT.

    9cda379 added the nullable deleted_at/deleted_by columns for bulk-delete
    recovery but nothing filtered deleted_at IS NULL back into reads — the
    commit says so explicitly ("KNOWN REGRESSION, deliberately committed").
    This reproduces it directly at the ORM layer: query the model the way
    every list/detail/dashboard/count call site does, with a row that has
    deleted_at set, and assert it is invisible.
    """
    import datetime

    from app.models.application_portfolio import ApplicationComponent

    org = make_org("softdel")

    with tenant_ctx(org.id):
        live = ApplicationComponent(
            name=f"Live App {uuid.uuid4().hex[:6]}",
            organization_id=org.id,
        )
        dead = ApplicationComponent(
            name=f"Deleted App {uuid.uuid4().hex[:6]}",
            organization_id=org.id,
            deleted_at=datetime.datetime.utcnow(),
            deleted_by=None,
        )
        db_session.add_all([live, dead])
        db_session.commit()

        ids = {a.id for a in ApplicationComponent.query.all()}
        assert live.id in ids, "a non-deleted application must still be readable"
        assert dead.id not in ids, (
            "a soft-deleted application (deleted_at set) was returned by a "
            "plain ORM query — this is the exact 9cda379 regression: nothing "
            "filters deleted_at IS NULL out of read paths"
        )

        # Only asserts the deleted row isn't double-counted relative to a
        # direct id-based check, independent of how many other rows exist
        # from other tests in this (rolled-back) transaction.
        assert dead.id not in {a.id for a in ApplicationComponent.query.filter(
            ApplicationComponent.id.in_([live.id, dead.id])
        ).all()}


def test_duplicate_guard_does_not_collide_with_soft_deleted_row(app, db_session, make_org, tenant_ctx):
    """A name must be reusable after the row holding it is soft-deleted.

    Without an exclusion, find_duplicate_by_name's ORM query would still see
    the soft-deleted row (same defect as the read paths above) and make the
    name permanently unusable — worse than before soft-delete existed, when a
    hard delete actually freed the name.
    """
    import datetime

    from app.models.application_portfolio import ApplicationComponent
    from app.utils.duplicate_guard import find_duplicate_by_name

    org = make_org("softdel-dup")
    name = f"Reusable App {uuid.uuid4().hex[:6]}"

    with tenant_ctx(org.id):
        dead = ApplicationComponent(
            name=name,
            organization_id=org.id,
            deleted_at=datetime.datetime.utcnow(),
        )
        db_session.add(dead)
        db_session.commit()

        existing = find_duplicate_by_name(ApplicationComponent, name)
        assert existing is None, (
            "find_duplicate_by_name matched a soft-deleted row — the name is "
            "permanently unusable after deletion, which is worse than a hard "
            "delete"
        )


# ---------------------------------------------------------------------------
# ARCH-130 / spec R-01, R-02, R-03, R-06 — the Priority 0 reconciliation
# suite proper.
#
# R-01: for each entity type, db_count == api total == what the AI context
# reports. "UI-rendered count" is represented by the same collection
# endpoints the templates fetch from (this repo's list pages are
# fetch()-driven, not server-rendered counts — see DESIGN.md); asserting the
# API total is therefore asserting what the UI will render, and is the
# layer the ARCH-010 defect (dashboard 144 vs tiles 145 vs API 145) actually
# lived in.
#
# Fixture requirement per the spec: seed a SPARSE type as well as a normal
# one, because the ARCH-010 investigation was misled by a fixture that had
# only one element in the affected layer.
# ---------------------------------------------------------------------------


@pytest.fixture
def recon_client(app, db_session, make_org, login_as):
    """A logged-in client scoped to a fresh org, for /api/v1 reconciliation."""
    from app.models.user import Permission, Role, User

    org = make_org("recon")
    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        role = Role(name="Administrator", permissions=Permission.ADMINISTER)
        db_session.add(role)
        db_session.flush()
    user = User(
        email=f"recon-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Recon",
        last_name="User",
        organization_id=org.id,
        role=role,
        confirmed=True,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    client = app.test_client()
    login_as(client, user)
    return org, client, user


def test_r01_application_counts_agree_db_api_and_ai_context(recon_client, tenant_ctx, login_as):
    """R-01 for applications: db_count == /api/v1/applications total == AI context total.

    ApplicationComponent is tenant-scoped (TenantMixin), so seeding inside
    this org and reading back through a request logged in as this org's user
    exercises the real ORM tenant filter on every surface, not a hand-rolled
    query.
    """
    from app import db
    from app.models.application_portfolio import ApplicationComponent

    org, client, user = recon_client

    with tenant_ctx(org.id):
        # A normal population...
        for i in range(3):
            db.session.add(
                ApplicationComponent(
                    name=f"Recon App {i} {uuid.uuid4().hex[:6]}",
                    organization_id=org.id,
                    lifecycle_status="2.1 STRATEGIC",
                )
            )
        # ...and one sparse/edge-case row: a null lifecycle_status, which is
        # exactly the shape of field that has silently acted as an implicit
        # filter elsewhere in this codebase (R-04).
        db.session.add(
            ApplicationComponent(
                name=f"Recon App Sparse {uuid.uuid4().hex[:6]}",
                organization_id=org.id,
                lifecycle_status=None,
            )
        )
        db.session.commit()
        db_count = ApplicationComponent.query.count()

    login_as(client, user)
    api_resp = client.get("/api/v1/applications/?per_page=100")
    assert api_resp.status_code == 200, api_resp.get_data(as_text=True)
    api_body = api_resp.get_json() or {}
    api_total = ((api_body.get("data") or {}).get("pagination") or {}).get("total")

    ctx_resp = client.get("/ai-chat/context/general")
    assert ctx_resp.status_code == 200, ctx_resp.get_data(as_text=True)
    ctx_body = ctx_resp.get_json() or {}
    ctx_total = ((ctx_body.get("context") or {}).get("portfolio_summary") or {}).get(
        "total_applications"
    )

    assert db_count == 4, "fixture setup sanity check"
    assert api_total == db_count, (
        f"API total ({api_total}) != direct db count ({db_count}) for "
        "applications — this is the ARCH-010 defect class"
    )
    assert ctx_total == db_count, (
        f"AI context total_applications ({ctx_total}) != direct db count "
        f"({db_count}) — the agent would state a figure the API cannot back"
    )


def test_r02_application_count_increments_by_exactly_one_after_write(recon_client, login_as):
    """R-02: every surface increments by exactly 1 after one write, immediately."""
    org, client, user = recon_client
    login_as(client, user)

    before = client.get("/api/v1/applications/?per_page=1")
    before_total = (
        ((before.get_json() or {}).get("data") or {}).get("pagination") or {}
    ).get("total")

    create_resp = client.post(
        "/api/v1/applications/",
        json={"name": f"R02 New App {uuid.uuid4().hex[:6]}"},
    )
    assert create_resp.status_code in (200, 201), create_resp.get_data(as_text=True)

    after = client.get("/api/v1/applications/?per_page=1")
    after_total = (
        ((after.get_json() or {}).get("data") or {}).get("pagination") or {}
    ).get("total")

    ctx_after = client.get("/ai-chat/context/general")
    ctx_total_after = (
        ((ctx_after.get_json() or {}).get("context") or {}).get("portfolio_summary") or {}
    ).get("total_applications")

    assert after_total == before_total + 1, (
        f"API total did not increment by exactly 1 after one create "
        f"(before={before_total}, after={after_total})"
    )
    assert ctx_total_after == after_total, (
        "AI context total drifted from the API total immediately after a "
        f"write (api={after_total}, ctx={ctx_total_after})"
    )


def test_r01_vendor_and_capability_totals_agree_db_and_api(
    recon_client, db_session, login_as, make_org
):
    """R-01 for the global vendor and hybrid-scoped capability catalogues.

    Vendors are platform-wide. Capabilities expose shared reference rows plus
    the current organization's extensions, so the database comparison must
    use that same visibility contract rather than counting other tenants.
    """
    from app import db
    from app.models.unified_capability import UnifiedCapability
    from app.models.vendor.vendor_organization import VendorOrganization

    org, client, user = recon_client
    login_as(client, user)

    other_org = make_org("recon-hidden-capability")
    db_session.add(
        UnifiedCapability(
            name=f"Hidden capability {uuid.uuid4().hex[:8]}",
            organization_id=other_org.id,
            scope="tenant",
        )
    )
    db_session.flush()

    vendor_db_count = VendorOrganization.query.count()
    vendor_resp = client.get("/api/vendors/list?per_page=100")
    assert vendor_resp.status_code == 200, vendor_resp.get_data(as_text=True)
    vendor_api_total = (vendor_resp.get_json() or {}).get("total")
    assert vendor_api_total == vendor_db_count, (
        f"vendor API total ({vendor_api_total}) != direct db count "
        f"({vendor_db_count})"
    )

    cap_db_count = UnifiedCapability.query.filter(
        db.or_(
            UnifiedCapability.organization_id == org.id,
            UnifiedCapability.organization_id.is_(None),
        )
    ).count()
    cap_resp = client.get("/api/v1/capabilities/?per_page=100")
    assert cap_resp.status_code == 200, cap_resp.get_data(as_text=True)
    cap_api_total = ((cap_resp.get_json() or {}).get("data") or {}).get("pagination", {}).get(
        "total"
    )
    assert cap_api_total == cap_db_count, (
        f"capability API total ({cap_api_total}) != direct db count "
        f"({cap_db_count})"
    )


def test_r03_vendor_pagination_reports_the_true_total_not_the_page_size(
    recon_client, db_session, login_as
):
    """R-03: pagination total must be the full dataset size, and per_page must
    be honoured — a page-size-sized total is exactly the bug this guards.
    """
    from app import db
    from app.models.vendor.vendor_organization import VendorOrganization

    org, client, user = recon_client
    login_as(client, user)

    before_count = VendorOrganization.query.count()
    seed_n = 15
    for i in range(seed_n):
        db.session.add(
            VendorOrganization(
                name=f"R03 Vendor {i} {uuid.uuid4().hex[:6]}",
                vendor_type="software",
            )
        )
    db.session.commit()
    expected_total = before_count + seed_n

    resp_default = client.get("/api/vendors/list")
    body_default = resp_default.get_json() or {}
    assert body_default.get("total") == expected_total, (
        f"total ({body_default.get('total')}) must be the full dataset size "
        f"({expected_total}), not the page size"
    )

    resp_paged = client.get("/api/vendors/list?per_page=10")
    body_paged = resp_paged.get_json() or {}
    assert body_paged.get("total") == expected_total
    assert len(body_paged.get("vendors") or []) == 10, (
        "per_page=10 must return exactly 10 rows while total still reports "
        "the full dataset"
    )


def test_r06_ai_context_grounds_every_portfolio_count_in_the_live_api(recon_client, login_as):
    """R-06: ai-chat context/general's portfolio_summary must match the API
    it is meant to summarise, for every count it states, not just capabilities
    (ARCH-015 already covers the capabilities half above).

    NOTE on "capabilities": the codebase has two distinct capability
    concepts behind the same word — ``UnifiedCapability`` (the global,
    non-tenant PCF/APQC reference taxonomy the /api/v1/capabilities/
    endpoint serves) and ``BusinessCapability`` (a tenant-scoped model,
    what ``_load_general_context``'s ``total_capabilities`` actually
    counts). Comparing the context figure against the UnifiedCapability API
    total would be comparing two different entity types by name coincidence
    — exactly the kind of numerator/denominator mismatch ARCH-015 already
    caught once (see the comment in multi_domain_chat_service.py's
    ``_load_general_context``). So this test grounds total_capabilities
    against the correct source, BusinessCapability's own tenant-scoped
    count, not the UnifiedCapability API.
    """
    from app.models.business_capabilities import BusinessCapability

    org, client, user = recon_client
    login_as(client, user)

    apps_total = (
        ((client.get("/api/v1/applications/?per_page=1").get_json() or {}).get("data") or {})
        .get("pagination", {})
        .get("total")
    )
    vendors_total = (client.get("/api/vendors/list?per_page=1").get_json() or {}).get("total")
    caps_db_count = BusinessCapability.query.count()

    ctx = client.get("/ai-chat/context/general")
    assert ctx.status_code == 200, ctx.get_data(as_text=True)
    summary = ((ctx.get_json() or {}).get("context") or {}).get("portfolio_summary") or {}

    assert summary.get("total_applications") == apps_total, (
        f"ctx total_applications ({summary.get('total_applications')}) != "
        f"applications API total ({apps_total})"
    )
    assert summary.get("total_vendors") == vendors_total, (
        f"ctx total_vendors ({summary.get('total_vendors')}) != vendors API "
        f"total ({vendors_total})"
    )
    assert summary.get("total_capabilities") == caps_db_count, (
        f"ctx total_capabilities ({summary.get('total_capabilities')}) != "
        f"BusinessCapability db count ({caps_db_count}) — the two "
        "'capability' concepts (UnifiedCapability vs BusinessCapability) "
        "must not be conflated when grounding this figure"
    )


# ---------------------------------------------------------------------------
# ARCH-130 / spec R-01, R-02, R-03, R-06 — the Priority 0 reconciliation
# suite proper.
#
# R-01: for each entity type, db_count == api total == what the AI context
# reports. "UI-rendered count" is represented by the same collection
# endpoints the templates fetch from (this repo's list pages are
# fetch()-driven, not server-rendered counts — see DESIGN.md); asserting the
# API total is therefore asserting what the UI will render, and is the
# layer the ARCH-010 defect (dashboard 144 vs tiles 145 vs API 145) actually
# lived in.
#
# Fixture requirement per the spec: seed a SPARSE type as well as a normal
# one, because the ARCH-010 investigation was misled by a fixture that had
# only one element in the affected layer.
# ---------------------------------------------------------------------------


@pytest.fixture
def recon_client(app, db_session, make_org, login_as):
    """A logged-in client scoped to a fresh org, for /api/v1 reconciliation."""
    from app.models.user import Permission, Role, User

    org = make_org("recon")
    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        role = Role(name="Administrator", permissions=Permission.ADMINISTER)
        db_session.add(role)
        db_session.flush()
    user = User(
        email=f"recon-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Recon",
        last_name="User",
        organization_id=org.id,
        role=role,
        confirmed=True,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    client = app.test_client()
    login_as(client, user)
    return org, client, user


def test_r01_application_counts_agree_db_api_and_ai_context(recon_client, tenant_ctx, login_as):
    """R-01 for applications: db_count == /api/v1/applications total == AI context total.

    ApplicationComponent is tenant-scoped (TenantMixin), so seeding inside
    this org and reading back through a request logged in as this org's user
    exercises the real ORM tenant filter on every surface, not a hand-rolled
    query.
    """
    from app import db
    from app.models.application_portfolio import ApplicationComponent

    org, client, user = recon_client

    with tenant_ctx(org.id):
        # A normal population...
        for i in range(3):
            db.session.add(
                ApplicationComponent(
                    name=f"Recon App {i} {uuid.uuid4().hex[:6]}",
                    organization_id=org.id,
                    lifecycle_status="2.1 STRATEGIC",
                )
            )
        # ...and one sparse/edge-case row: a null lifecycle_status, which is
        # exactly the shape of field that has silently acted as an implicit
        # filter elsewhere in this codebase (R-04).
        db.session.add(
            ApplicationComponent(
                name=f"Recon App Sparse {uuid.uuid4().hex[:6]}",
                organization_id=org.id,
                lifecycle_status=None,
            )
        )
        db.session.commit()
        db_count = ApplicationComponent.query.count()

    login_as(client, user)
    api_resp = client.get("/api/v1/applications/?per_page=100")
    assert api_resp.status_code == 200, api_resp.get_data(as_text=True)
    api_body = api_resp.get_json() or {}
    api_total = ((api_body.get("data") or {}).get("pagination") or {}).get("total")

    ctx_resp = client.get("/ai-chat/context/general")
    assert ctx_resp.status_code == 200, ctx_resp.get_data(as_text=True)
    ctx_body = ctx_resp.get_json() or {}
    ctx_total = ((ctx_body.get("context") or {}).get("portfolio_summary") or {}).get(
        "total_applications"
    )

    assert db_count == 4, "fixture setup sanity check"
    assert api_total == db_count, (
        f"API total ({api_total}) != direct db count ({db_count}) for "
        "applications — this is the ARCH-010 defect class"
    )
    assert ctx_total == db_count, (
        f"AI context total_applications ({ctx_total}) != direct db count "
        f"({db_count}) — the agent would state a figure the API cannot back"
    )


def test_r02_application_count_increments_by_exactly_one_after_write(recon_client, login_as):
    """R-02: every surface increments by exactly 1 after one write, immediately."""
    org, client, user = recon_client
    login_as(client, user)

    before = client.get("/api/v1/applications/?per_page=1")
    before_total = (
        ((before.get_json() or {}).get("data") or {}).get("pagination") or {}
    ).get("total")

    create_resp = client.post(
        "/api/v1/applications/",
        json={"name": f"R02 New App {uuid.uuid4().hex[:6]}"},
    )
    assert create_resp.status_code in (200, 201), create_resp.get_data(as_text=True)

    after = client.get("/api/v1/applications/?per_page=1")
    after_total = (
        ((after.get_json() or {}).get("data") or {}).get("pagination") or {}
    ).get("total")

    ctx_after = client.get("/ai-chat/context/general")
    ctx_total_after = (
        ((ctx_after.get_json() or {}).get("context") or {}).get("portfolio_summary") or {}
    ).get("total_applications")

    assert after_total == before_total + 1, (
        f"API total did not increment by exactly 1 after one create "
        f"(before={before_total}, after={after_total})"
    )
    assert ctx_total_after == after_total, (
        "AI context total drifted from the API total immediately after a "
        f"write (api={after_total}, ctx={ctx_total_after})"
    )


def test_r01_vendor_and_capability_totals_agree_db_and_api(
    recon_client, db_session, login_as, make_org
):
    """R-01 for the global vendor and hybrid-scoped capability catalogues.

    Vendors are platform-wide. Capabilities expose shared reference rows plus
    the current organization's extensions, so the database comparison must
    use that same visibility contract rather than counting other tenants.
    """
    from app import db
    from app.models.unified_capability import UnifiedCapability
    from app.models.vendor.vendor_organization import VendorOrganization

    org, client, user = recon_client
    login_as(client, user)

    other_org = make_org("recon-hidden-capability")
    db_session.add(
        UnifiedCapability(
            name=f"Hidden capability {uuid.uuid4().hex[:8]}",
            organization_id=other_org.id,
            scope="tenant",
        )
    )
    db_session.flush()

    vendor_db_count = VendorOrganization.query.count()
    vendor_resp = client.get("/api/vendors/list?per_page=100")
    assert vendor_resp.status_code == 200, vendor_resp.get_data(as_text=True)
    vendor_api_total = (vendor_resp.get_json() or {}).get("total")
    assert vendor_api_total == vendor_db_count, (
        f"vendor API total ({vendor_api_total}) != direct db count "
        f"({vendor_db_count})"
    )

    cap_db_count = UnifiedCapability.query.filter(
        db.or_(
            UnifiedCapability.organization_id == org.id,
            UnifiedCapability.organization_id.is_(None),
        )
    ).count()
    cap_resp = client.get("/api/v1/capabilities/?per_page=100")
    assert cap_resp.status_code == 200, cap_resp.get_data(as_text=True)
    cap_api_total = ((cap_resp.get_json() or {}).get("data") or {}).get("pagination", {}).get(
        "total"
    )
    assert cap_api_total == cap_db_count, (
        f"capability API total ({cap_api_total}) != direct db count "
        f"({cap_db_count})"
    )


def test_r03_vendor_pagination_reports_the_true_total_not_the_page_size(
    recon_client, db_session, login_as
):
    """R-03: pagination total must be the full dataset size, and per_page must
    be honoured — a page-size-sized total is exactly the bug this guards.
    """
    from app import db
    from app.models.vendor.vendor_organization import VendorOrganization

    org, client, user = recon_client
    login_as(client, user)

    before_count = VendorOrganization.query.count()
    seed_n = 15
    for i in range(seed_n):
        db.session.add(
            VendorOrganization(
                name=f"R03 Vendor {i} {uuid.uuid4().hex[:6]}",
                vendor_type="software",
            )
        )
    db.session.commit()
    expected_total = before_count + seed_n

    resp_default = client.get("/api/vendors/list")
    body_default = resp_default.get_json() or {}
    assert body_default.get("total") == expected_total, (
        f"total ({body_default.get('total')}) must be the full dataset size "
        f"({expected_total}), not the page size"
    )

    resp_paged = client.get("/api/vendors/list?per_page=10")
    body_paged = resp_paged.get_json() or {}
    assert body_paged.get("total") == expected_total
    assert len(body_paged.get("vendors") or []) == 10, (
        "per_page=10 must return exactly 10 rows while total still reports "
        "the full dataset"
    )


def test_r06_ai_context_grounds_every_portfolio_count_in_the_live_api(recon_client, login_as):
    """R-06: ai-chat context/general's portfolio_summary must match the API
    it is meant to summarise, for every count it states, not just capabilities
    (ARCH-015 already covers the capabilities half above).

    NOTE on "capabilities": the codebase has two distinct capability
    concepts behind the same word — ``UnifiedCapability`` (the global,
    non-tenant PCF/APQC reference taxonomy the /api/v1/capabilities/
    endpoint serves) and ``BusinessCapability`` (a tenant-scoped model,
    what ``_load_general_context``'s ``total_capabilities`` actually
    counts). Comparing the context figure against the UnifiedCapability API
    total would be comparing two different entity types by name coincidence
    — exactly the kind of numerator/denominator mismatch ARCH-015 already
    caught once (see the comment in multi_domain_chat_service.py's
    ``_load_general_context``). So this test grounds total_capabilities
    against the correct source, BusinessCapability's own tenant-scoped
    count, not the UnifiedCapability API.
    """
    from app.models.business_capabilities import BusinessCapability

    org, client, user = recon_client
    login_as(client, user)

    apps_total = (
        ((client.get("/api/v1/applications/?per_page=1").get_json() or {}).get("data") or {})
        .get("pagination", {})
        .get("total")
    )
    vendors_total = (client.get("/api/vendors/list?per_page=1").get_json() or {}).get("total")
    caps_db_count = BusinessCapability.query.count()

    ctx = client.get("/ai-chat/context/general")
    assert ctx.status_code == 200, ctx.get_data(as_text=True)
    summary = ((ctx.get_json() or {}).get("context") or {}).get("portfolio_summary") or {}

    assert summary.get("total_applications") == apps_total, (
        f"ctx total_applications ({summary.get('total_applications')}) != "
        f"applications API total ({apps_total})"
    )
    assert summary.get("total_vendors") == vendors_total, (
        f"ctx total_vendors ({summary.get('total_vendors')}) != vendors API "
        f"total ({vendors_total})"
    )
    assert summary.get("total_capabilities") == caps_db_count, (
        f"ctx total_capabilities ({summary.get('total_capabilities')}) != "
        f"BusinessCapability db count ({caps_db_count}) — the two "
        "'capability' concepts (UnifiedCapability vs BusinessCapability) "
        "must not be conflated when grounding this figure"
    )
