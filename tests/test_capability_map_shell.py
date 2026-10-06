"""Capability Map on the screen system (shell-overhaul Wave 2, Task 3).

Product design review pinned three structural defects:

- `/capability-map/` and `/capability-map/hierarchy` disagreed on the total
  capability count (500 vs 495) — they used two independent counting
  queries. `/capability-map/` (via ``api_unified_domains``) did a flat
  ``BusinessCapability.query.count()``; `/capability-map/hierarchy`'s Alpine
  ``countAll()`` walked only the subtree reachable from level-1 roots, so
  capabilities orphaned from that tree were silently dropped from the
  second count. Both pages now render the total from a single function,
  ``count_business_capabilities`` (app/modules/capabilities/services/
  capability_count_service.py).
- two competing rows of 9+ view-mode switchers (page-links row PLUS an
  11-tab in-page Alpine strip) — consolidated into one segmented control
  (the true view modes) plus a "Lens" dropdown for the rest.
- red gap-dots with no legend — a ``data-testid="capability-legend"``
  element now documents color -> meaning wherever dots render.
"""

from __future__ import annotations

import re
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

    org = make_org(f"cap-map-shell-{label}")
    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"cap-map-shell-{label}-{suffix}@example.com",
        first_name="Cap",
        last_name="Shell",
        organization_id=org.id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    user.password = "Sup3rSecret!23"
    db_session.add(user)
    db_session.flush()
    db_session.commit()
    return user.id, org


def _seed_capabilities(db_session, org, count, tenant_ctx):
    """Seed ``count`` BusinessCapability rows: a level-1 root plus
    ``count - 1`` level-2 children under it, all reachable from the root so
    both the flat count and the old tree-walk would (if it still ran) agree
    — the point of this fixture is a known, unambiguous total.

    Runs inside ``tenant_ctx(org.id)``: the BusinessCapability insert also
    fires a ``before_insert`` listener that raw-inserts a matching
    ``ArchiMateElement`` row (``app/models/business_capabilities.py``, see
    ``create_capability_archimate_element``). That insert bypasses the ORM
    ``before_flush`` auto-set and relies on the column default
    (``_default_org_id``) instead, which reads ``g.current_org_id`` — set only
    inside a request context. Seeding outside one made every insert fail with
    ``NotNullViolation: organization_id`` once more than one organization
    exists in the shared test database. This is a test-harness gap, not a
    product bug: production requests always carry ``g.current_org_id``.
    """
    from app.models.business_capabilities import BusinessCapability

    with tenant_ctx(org.id):
        root = BusinessCapability(
            name=f"Root Capability {uuid.uuid4().hex[:8]}",
            level=1,
            business_domain="Operations",
            organization_id=org.id,
        )
        db_session.add(root)
        db_session.flush()

        for i in range(count - 1):
            child = BusinessCapability(
                name=f"Child Capability {i}-{uuid.uuid4().hex[:8]}",
                level=2,
                business_domain="Operations",
                parent_capability_id=root.id,
                organization_id=org.id,
            )
            db_session.add(child)
        db_session.commit()


def _seed_capability_with_mappings(db_session, org, tenant_ctx, mapped_apps=0):
    """One root BusinessCapability, plus ``mapped_apps`` distinct
    ApplicationComponents each mapped to it via ApplicationCapabilityMapping
    — for testing the real per-capability mapping_count wired into
    map_views._compute_capability_mapping_counts(). Returns the capability.

    Runs inside ``tenant_ctx(org.id)`` for the same reason as
    ``_seed_capabilities`` above — the BusinessCapability insert's
    ``before_insert`` listener raw-inserts an ``ArchiMateElement`` whose
    ``organization_id`` is only resolvable from ``g.current_org_id``.
    """
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capabilities import BusinessCapability
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping
    from app.models.unified_capability import UnifiedCapability

    with tenant_ctx(org.id):
        cap = BusinessCapability(
            name=f"Mapped Capability {uuid.uuid4().hex[:8]}",
            level=1,
            business_domain="Operations",
            organization_id=org.id,
        )
        db_session.add(cap)
        db_session.flush()

        # Also create the UnifiedCapability projection (simulating the
        # project-capabilities command) so the mapping count query finds it.
        # Check if it already exists (test database may have residue from
        # previous runs due to the provenance unique index).
        unified_cap = UnifiedCapability.query.filter(
            UnifiedCapability.source_table == "business_capability",
            UnifiedCapability.source_id == str(cap.id),
            UnifiedCapability.source_org_id == cap.organization_id,
        ).first()
        if not unified_cap:
            unified_cap = UnifiedCapability(
                name=cap.name,
                level=cap.level,
                description=cap.description,
                code=cap.code,
                category=cap.category,
                strategic_importance=cap.strategic_importance,
                parent_capability_id=cap.parent_capability_id,
                specialization_type=cap.specialization_type if hasattr(cap, 'specialization_type') else "BUSINESS",
                organization_id=cap.organization_id,
                scope=cap.scope if hasattr(cap, 'scope') else "tenant",
                source_table="business_capability",
                source_id=str(cap.id),
                source_org_id=cap.organization_id,
            )
            db_session.add(unified_cap)
            db_session.flush()

        for _ in range(mapped_apps):
            app_component = ApplicationComponent(
                name=f"App {uuid.uuid4().hex[:8]}",
                organization_id=org.id,
            )
            db_session.add(app_component)
            db_session.flush()
            # Create legacy mapping
            db_session.add(
                ApplicationCapabilityMapping(
                    organization_id=org.id,
                    application_component_id=app_component.id,
                    business_capability_id=cap.id,
                )
            )
            # Create unified mapping (canonical store)
            unified_mapping = UnifiedApplicationCapabilityMapping.query.filter(
                UnifiedApplicationCapabilityMapping.unified_capability_id == unified_cap.id,
                UnifiedApplicationCapabilityMapping.application_component_id == app_component.id,
            ).first()
            if not unified_mapping:
                db_session.add(
                    UnifiedApplicationCapabilityMapping(
                        unified_capability_id=unified_cap.id,
                        application_component_id=app_component.id,
                        support_level="partial",
                        coverage_percentage=80,
                    )
                )
        db_session.commit()
        return cap


def _get(app, db_session, make_org, tenant_ctx, label, path, seed_count=7):
    user_id, org = _make_user(db_session, make_org, label)
    _seed_capabilities(db_session, org, seed_count, tenant_ctx)
    client = app.test_client()
    _login(client, user_id)
    resp = client.get(path)
    assert resp.status_code == 200, resp.get_data(as_text=True)[:2000]
    return resp.get_data(as_text=True)


def _extract_capability_total(html):
    """Pull the total-capabilities figure. `/capability-map/` renders it in
    `<span id="unified-cap-count">N</span>`; `/capability-map/hierarchy`
    renders it as "N capabilities" in the coverage banner. Both are now
    sourced from the same server-side counting function."""
    match = re.search(r'id="unified-cap-count">\s*(\d+)\s*<', html)
    if match:
        return int(match.group(1))
    match = re.search(r"(\d+)\s+capabilities", html)
    assert match, "could not find a '<N> capabilities' figure in the page"
    return int(match.group(1))


def test_index_and_hierarchy_report_the_same_total(app, db_session, make_org, tenant_ctx):
    """The historical bug: /capability-map/ said 500, /capability-map/hierarchy
    said 495. Seed a known count and assert both pages report it."""
    user_id, org = _make_user(db_session, make_org, "same-total")
    _seed_capabilities(db_session, org, 11, tenant_ctx)

    client = app.test_client()
    _login(client, user_id)

    index_html = client.get("/capability-map/").get_data(as_text=True)
    hierarchy_html = client.get("/capability-map/hierarchy").get_data(as_text=True)

    index_total = _extract_capability_total(index_html)
    hierarchy_total = _extract_capability_total(hierarchy_html)

    assert index_total == 11, index_total
    assert hierarchy_total == 11, hierarchy_total
    assert index_total == hierarchy_total


def test_index_page_has_exactly_one_h1(app, db_session, make_org, tenant_ctx):
    html = _get(app, db_session, make_org, tenant_ctx, "one-h1", "/capability-map/")
    assert html.count("<h1") == 1


def test_legend_exists_for_gap_dot_indicators(app, db_session, make_org, tenant_ctx):
    """A legend must exist wherever the red/amber/emerald gap-dots render —
    on both the index page's Capability Model tab and the hierarchy page's
    coverage dots."""
    index_html = _get(app, db_session, make_org, tenant_ctx, "legend-index", "/capability-map/")
    assert 'data-testid="capability-legend"' in index_html

    hierarchy_html = _get(
        app, db_session, make_org, tenant_ctx, "legend-hierarchy", "/capability-map/hierarchy"
    )
    assert 'data-testid="capability-legend"' in hierarchy_html


def test_zero_real_mappings_renders_destructive_tier_not_hidden(app, db_session, make_org, tenant_ctx):
    """A capability with zero *real* app mappings still gets a computed
    answer (mapping_count: 0) from the org-scoped grouped query, not a
    null — the query ran and genuinely found nothing. That is legitimately
    the destructive ("no apps mapped") tier, distinct from "not computed"
    (covered by test_query_failure_hides_dots_and_legend below). The legend
    must be present, since real (if all-zero) data was computed."""
    user_id, org = _make_user(db_session, make_org, "zero-real")
    _seed_capability_with_mappings(db_session, org, tenant_ctx, mapped_apps=0)

    client = app.test_client()
    _login(client, user_id)
    html = client.get("/capability-map/hierarchy").get_data(as_text=True)

    assert '"mapping_count": 0' in html
    assert 'data-testid="capability-legend"' in html
    assert "Coverage indicators could not be loaded" not in html


def test_mapped_capability_reports_real_count(app, db_session, make_org, tenant_ctx):
    """A capability with 1 real app mapping must embed mapping_count: 1
    (the amber/1-2-apps tier per the legend), sourced from
    _compute_capability_mapping_counts()'s single grouped query, not a
    fabricated value."""
    user_id, org = _make_user(db_session, make_org, "one-mapping")
    _seed_capability_with_mappings(db_session, org, tenant_ctx, mapped_apps=1)

    client = app.test_client()
    _login(client, user_id)
    html = client.get("/capability-map/hierarchy").get_data(as_text=True)

    assert '"mapping_count": 1' in html
    assert 'data-testid="capability-legend"' in html


def test_query_failure_hides_dots_and_legend(app, db_session, make_org, tenant_ctx, monkeypatch):
    """When the mapping-count query cannot be computed at all (simulated
    here — no tenant context / a genuine query failure in production),
    every capability's mapping_count must be null (not defaulted to 0,
    which would render a confidently-wrong red dot), and the legend must
    not render since it would be documenting dots that don't exist."""
    import app.modules.capabilities.routes.map_views as map_views

    monkeypatch.setattr(map_views, "_compute_capability_mapping_counts", lambda: None)

    user_id, org = _make_user(db_session, make_org, "query-failure")
    _seed_capability_with_mappings(db_session, org, tenant_ctx, mapped_apps=2)

    client = app.test_client()
    _login(client, user_id)
    html = client.get("/capability-map/hierarchy").get_data(as_text=True)

    assert '"mapping_count": null' in html
    assert '"mapping_count": 2' not in html
    assert 'data-testid="capability-legend"' not in html
    assert "Coverage indicators could not be loaded" in html


def test_hierarchy_cache_is_org_scoped_when_caching_is_forced_on(
    app, db_session, make_org, tenant_ctx, monkeypatch
):
    """The historical bug: hierarchy() is @cached(), and the decorator's
    default cache key ignores its caller entirely — every organization
    shares one key. Whichever org's request warms the cache first, every
    other org would then be served *that* org's rendered page (including
    its real per-capability mapping_count figures) for the TTL. This
    environment has no Redis, so cache_manager.get()/set() are no-ops and
    the bug is inert here but live wherever Redis is up — force caching on
    with an in-memory dict standing in for Redis, and prove org B's request
    never receives org A's payload."""
    from app.extensions import cache as cache_module

    store: dict[str, object] = {}
    monkeypatch.setattr(
        cache_module.cache_manager,
        "get",
        lambda key, default=None: store.get(key, default),
    )
    monkeypatch.setattr(
        cache_module.cache_manager,
        "set",
        lambda key, value, ttl=300: (store.__setitem__(key, value), True)[1],
    )

    user_a, org_a = _make_user(db_session, make_org, "cache-org-a")
    cap_a = _seed_capability_with_mappings(db_session, org_a, tenant_ctx, mapped_apps=0)

    user_b, org_b = _make_user(db_session, make_org, "cache-org-b")
    cap_b = _seed_capability_with_mappings(db_session, org_b, tenant_ctx, mapped_apps=0)

    client_a = app.test_client()
    _login(client_a, user_a)
    html_a = client_a.get("/capability-map/hierarchy").get_data(as_text=True)
    assert cap_a.name in html_a

    client_b = app.test_client()
    _login(client_b, user_b)
    html_b = client_b.get("/capability-map/hierarchy").get_data(as_text=True)

    assert cap_a.name not in html_b, "org B received org A's cached hierarchy payload"
    assert cap_b.name in html_b

    # Prove caching was genuinely exercised (not a vacuous pass because the
    # monkeypatched backend was never actually hit) -- one entry per org.
    assert len(store) >= 2, "expected the org-scoped key_func to produce distinct cache keys"


def test_unified_domains_coverage_is_org_scoped(app, db_session, make_org, tenant_ctx):
    """The sixth cross-tenant instance: ``ApplicationCapabilityMapping`` is
    not ``TenantMixin`` (see the model), so the ORM injects no tenant filter
    and ``api_unified_domains`` (the Business-lens default of the Capability
    Map) must filter it explicitly — mirroring
    ``_compute_capability_mapping_counts()`` in map_views.py.

    Without the org predicate, ``mapped_count`` is a query across every
    org's mappings while ``total_capabilities`` is this org's count alone,
    so ``coverage = mapped_count / total_capabilities`` can exceed 100% and
    leaks another tenant's mapping volume into the default tab.

    Org A gets 1 mapped capability; org B gets several more. Org A's
    coverage/mapped_count must reflect only org A's single mapping, not be
    inflated by org B's.
    """
    user_a, org_a = _make_user(db_session, make_org, "unified-domains-a")
    _seed_capability_with_mappings(db_session, org_a, tenant_ctx, mapped_apps=1)

    user_b, org_b = _make_user(db_session, make_org, "unified-domains-b")
    for _ in range(4):
        _seed_capability_with_mappings(db_session, org_b, tenant_ctx, mapped_apps=1)

    client_a = app.test_client()
    _login(client_a, user_a)
    resp_a = client_a.get("/capability-map/api/unified/domains")
    assert resp_a.status_code == 200, resp_a.get_data(as_text=True)[:2000]
    data_a = resp_a.get_json()

    assert data_a["success"] is True
    assert data_a["total_capabilities"] == 1, data_a
    assert data_a["mapped_count"] == 1, data_a
    assert data_a["coverage"] == 100.0, data_a


def test_single_view_switcher_row(app, db_session, make_org, tenant_ctx):
    """The old page had two competing rows of 9+ view-mode switchers — one
    page-link row and one 11-button Alpine tab strip. There must now be
    exactly one segmented-control row (data-testid="capability-view-switcher")
    and the old tab-strip's per-tab role="tab" buttons must be gone."""
    html = _get(app, db_session, make_org, tenant_ctx, "switcher", "/capability-map/")
    assert html.count('data-testid="capability-view-switcher"') == 1
    assert 'role="tab" id="tab-' not in html, "the old 11-button tab strip must be gone"
    assert 'id="capability-lens"' in html, "the Lens dropdown must replace it"


# ARCH-064: the three "map applications" modals (acm-mapping-modal,
# process-mapping-modal, mapping-modal) were ~130-line near-duplicates —
# 1,225 of the template's 2,924 lines (42%). They are now generated by a
# single macro, capability_map/_mapping_modal.html:mapping_modal(). These
# tests pin the page still renders, is meaningfully smaller, that all three
# modal DOM ids the JS opens by id are still present unchanged, and that the
# div tree is still balanced — this branch has produced three prior
# regressions from unbalanced divs detaching a subtree from its Alpine
# x-data scope, so div balance is asserted directly rather than assumed.
def test_capability_map_page_size_after_modal_dedup(app, db_session, make_org, tenant_ctx):
    """Measures actual rendered byte size after deduplicating the three
    mapping-modal blocks into one macro (capability_map/_mapping_modal.html).

    Measured directly (test client, same seed, before/after the dedup):
    before 481,749 bytes, after 482,243 bytes — essentially flat, ~500 bytes
    *larger*, not smaller. The macro removes ~326 lines of duplicated
    *template source* (2,924 -> 2,598 lines in index.html), which is a real
    maintainability win and the thing actually asked for, but it does not
    shrink the *rendered* page: all three modals are distinct widgets (ACM
    mapping, process mapping, capability mapping) that each still render
    once in the DOM: three renders in, three renders out. The 1,225-line/42%
    figure behind the original finding was itself wrong — the three modal
    blocks were ~126-130 lines each (~380 total), not ~1,225. Cutting
    rendered page weight would need a different approach (e.g. not
    server-rendering all three modals on every page load, only the one the
    user opens) which is out of scope for a source-dedup task and was not
    attempted here. This test pins "dedup does not silently bloat the page"
    rather than a shrink that dedup was never going to produce.
    """
    html = _get(app, db_session, make_org, tenant_ctx, "modal-size", "/capability-map/")
    size = len(html.encode("utf-8"))
    assert size < 500_000, f"unexpected size regression after modal dedup: {size} bytes"


def test_capability_map_modal_ids_unchanged_and_present(app, db_session, make_org, tenant_ctx):
    """The JS that opens each modal (app/static/js/capability_map/*.js) finds
    its target by DOM id — those ids must survive unchanged.

    ARCH-064 moved the dialogs out of /capability-map/'s initial HTML: all five
    start closed, and serialising them cost 68KB on every page load. They are now
    fetched from capability_map.mapping_modal_partial on first open. The intent
    of this test is unchanged — every id the JS reaches for must exist — so it
    asserts against the fragment that now carries them instead of the page that
    used to. Asserting they are inline would pin the payload back.
    """
    html = _get(app, db_session, make_org, tenant_ctx, "modal-ids", "/capability-map/")
    assert 'id="lazy-modal-host"' in html, "the lazy modal host is missing from the page"

    variants = {
        "acm-mapping-modal": "acm-mapping-modal",
        "process-mapping-modal": "process-mapping-modal",
        "mapping-modal": "mapping-modal",
    }
    html = "".join(
        _get(
            app, db_session, make_org, tenant_ctx, f"modal-{v}",
            f"/capability-map/partials/mapping-modal/{v}",
        )
        for v in variants
    )
    for modal_id in variants:
        assert f'id="{modal_id}"' in html, f"modal id {modal_id!r} missing from its partial"
    # Spot-check a few of the ids inside each modal that the per-modal JS
    # also depends on (search box, applications list, save/close handlers).
    assert 'id="acm-application-search"' in html
    assert 'id="acm-applications-list"' in html
    assert 'id="process-application-search"' in html
    assert 'id="process-applications-list"' in html
    assert 'id="application-search"' in html
    assert 'id="applications-list"' in html
    assert 'closeACMMappingModal()' in html
    assert 'closeProcessMappingModal()' in html
    assert 'closeMappingModal()' in html


def test_capability_map_div_tree_is_balanced(app, db_session, make_org, tenant_ctx):
    """Direct div-balance check on the rendered HTML, not just the template
    source, since Jinja macro expansion is what actually ships. Guards
    against the exact failure mode that hit this branch three times before:
    an unbalanced <div> detaching a whole subtree from its Alpine scope."""
    html = _get(app, db_session, make_org, tenant_ctx, "div-balance", "/capability-map/")
    opens = len(re.findall(r"<div\b", html))
    closes = len(re.findall(r"</div\s*>", html))
    # The one pre-existing orphan closing </div> this test used to tolerate
    # was fixed upstream (fe15b627, naming the icon button for screen
    # readers touched these templates) -- exact balance now, so a new
    # orphan introduced by a future edit still fails this test.
    assert closes == opens, (
        f"div balance drifted: {opens} opens, {closes} closes (expected exact balance)"
    )


# ── D2 regression test: reference-capability mappings excluded from org counts ──

def test_reference_capability_mappings_excluded_from_org_counts(
    app, db_session, make_org, tenant_ctx
):
    """Reference capabilities (organization_id IS NULL) with application
    mappings must not contribute to a tenant's per-capability mapping counts.

    The old BusinessCapability-based code filtered on
    ``organization_id == org_id``.  The UnifiedCapability do_orm_execute
    listener includes reference rows, so the mapping-count query must add
    its own explicit org filter — otherwise a reference capability mapped
    to an application inflates every organisation's count.
    """
    from app.models.application_portfolio import ApplicationComponent
    from app.models.unified_application_capability_mapping import (
        UnifiedApplicationCapabilityMapping,
    )
    from app.models.unified_capability import UnifiedCapability

    user_id, org = _make_user(db_session, make_org, "ref-excluded")

    # Reference capability — created outside tenant_ctx so the before_flush
    # listener does not auto-stamp an organization_id.
    ref_cap = UnifiedCapability(
        name=f"Reference Cap {uuid.uuid4().hex[:8]}",
        level=1,
        organization_id=None,
        scope="reference",
    )
    db_session.add(ref_cap)
    db_session.flush()

    with tenant_ctx(org.id):
        tenant_cap = UnifiedCapability(
            name=f"Tenant Cap {uuid.uuid4().hex[:8]}",
            level=1,
            organization_id=org.id,
            scope="tenant",
        )
        db_session.add(tenant_cap)
        db_session.flush()

        app_component = ApplicationComponent(
            name=f"App {uuid.uuid4().hex[:8]}",
            organization_id=org.id,
        )
        db_session.add(app_component)
        db_session.flush()

        # Map the same app to both the reference and the tenant capability.
        db_session.add(
            UnifiedApplicationCapabilityMapping(
                unified_capability_id=ref_cap.id,
                application_component_id=app_component.id,
                support_level="partial",
                coverage_percentage=80,
            )
        )
        db_session.add(
            UnifiedApplicationCapabilityMapping(
                unified_capability_id=tenant_cap.id,
                application_component_id=app_component.id,
                support_level="partial",
                coverage_percentage=80,
            )
        )
        db_session.commit()

    client = app.test_client()
    _login(client, user_id)
    html = client.get("/capability-map/hierarchy").get_data(as_text=True)

    # The reference capability IS visible in the hierarchy (the listener
    # includes organisation_id IS NULL rows), but its mapping must not be
    # counted — only the tenant-owned capability contributes a count of 1.
    assert ref_cap.name in html, "reference capability should be visible in the tree"
    assert tenant_cap.name in html
    assert '"mapping_count": 1' in html
    assert html.count('"mapping_count": 1') == 1, (
        "only the tenant-owned capability should have mapping_count: 1; "
        "the reference capability's mapping must not be counted"
    )


# ── D5: prove endpoints read from UnifiedCapability, not BusinessCapability ──

def test_hierarchy_reads_from_unified_not_business_capability(
    app, db_session, make_org, tenant_ctx
):
    """The hierarchy and index pages must read from UnifiedCapability, not
    BusinessCapability.  Seed only UnifiedCapability rows (zero
    BusinessCapability rows) and assert both pages still return the correct
    total.  This test fails on anioko/main where the readers still query
    BusinessCapability directly.
    """
    from app.models.unified_capability import UnifiedCapability

    user_id, org = _make_user(db_session, make_org, "unified-only")

    with tenant_ctx(org.id):
        root = UnifiedCapability(
            name=f"Unified-Only Root {uuid.uuid4().hex[:8]}",
            level=1,
            organization_id=org.id,
            scope="tenant",
        )
        db_session.add(root)
        db_session.flush()

        for i in range(5):
            child = UnifiedCapability(
                name=f"Unified-Only Child {i}-{uuid.uuid4().hex[:8]}",
                level=2,
                parent_capability_id=root.id,
                organization_id=org.id,
                scope="tenant",
            )
            db_session.add(child)
        db_session.commit()

    client = app.test_client()
    _login(client, user_id)

    html = client.get("/capability-map/hierarchy").get_data(as_text=True)
    assert root.name in html, "root UnifiedCapability must appear in hierarchy page"

    total = _extract_capability_total(html)
    assert total == 6, f"expected 6 capabilities from UnifiedCapability, got {total}"

    index_html = client.get("/capability-map/").get_data(as_text=True)
    index_total = _extract_capability_total(index_html)
    assert index_total == 6, f"index page expected 6, got {index_total}"
