"""Regression tests for the Health Scorecard vs. Solutions list disagreement
(bucket: arb-chart-and-solutions-data-disagreement, Task B).

Ground truth on production (verified via `flask db-query` against org 11,
2026-09-17): the org's two solutions were both created by the SAME user who
was logged in as `qa-solution-architect@example.com` — so the ownership
filter (`created_by_id=current_user.id`) does NOT hide them. Both rows are
draft, no description, empty `section_narratives`, version 1 — i.e. they are
"shell" rows excluded by the list's *default status filter*
(`solution_design_routes.py`'s `_is_shell` predicate), not by ownership.

These tests cover both narrowings the list can apply (shell-filter and
ownership-filter) because the hidden_by_default_filter / hidden_by_role_filter
fix in the route is meant to disclose either one correctly, even though only
the shell-filter case was the one actually reproduced in production.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _make_user(db_session, org, *, email=None, enterprise_role="solution_architect"):
    from app.models.user import Role, User

    role = Role.query.filter_by(name="Architect").first()
    if role is None:
        Role.insert_roles()
        role = Role.query.filter_by(name="Architect").first()

    user = User(
        email=email or f"solscorecard-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Test",
        last_name="User",
        organization_id=org.id,
        role=role,
        enterprise_role=enterprise_role,
        is_org_admin=False,
        is_platform_admin=False,
        confirmed=True,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    return user


def _make_shell_solution(db_session, org, creator, name_suffix=""):
    """A draft solution with no description/narrative/version history — the
    exact shape found in production for org 11's two hidden solutions."""
    from app.models.solution_models import Solution

    sol = Solution(
        name=f"Untitled Solution {name_suffix}{uuid.uuid4().hex[:6]}",
        status="draft",
        description=None,
        section_narratives={},
        organization_id=org.id,
        created_by_id=creator.id,
    )
    db_session.add(sol)
    db_session.flush()
    return sol


class TestHealthScorecardSolutionsListAgreement:
    """Both surfaces must agree on how many solutions EXIST; only the list's
    rendered ROWS may legitimately differ, and when they do the page must say
    so rather than claim zero exist."""

    def test_scorecard_and_list_disclosed_total_agree_when_shell_filtered(
        self, app, db_session, login_as, client
    ):
        from app.models.organization import Organization

        org = Organization(name=f"Scorecard Org {uuid.uuid4().hex[:8]}", slug=f"sc-{uuid.uuid4().hex[:8]}")
        db_session.add(org)
        db_session.flush()

        owner = _make_user(db_session, org, email=f"owner-{uuid.uuid4().hex[:6]}@example.com")
        _make_shell_solution(db_session, org, owner, "a")
        _make_shell_solution(db_session, org, owner, "b")
        db_session.commit()

        with app.app_context():
            login_as(client, owner)
            list_resp = client.get("/solutions/")
            assert list_resp.status_code == 200
            body = list_resp.get_data(as_text=True)

            # The two solutions exist and are owned by the viewer, but both are
            # shells excluded by the default filter — rendered rows must be 0,
            # and the page must disclose the 2 that are hidden rather than
            # claim none exist.
            assert "Get started by creating your first solution" not in body
            # R2-4 (round-3 refuter fix): the `or` with a prose string meant
            # this assertion passed even if the testid itself were removed
            # (the surviving prose string alone would satisfy it). Assert the
            # testid specifically.
            assert 'data-testid="solutions-hidden-disclosure"' in body

            health_resp = client.get("/dashboard/health")
            assert health_resp.status_code == 200
            health_body = health_resp.get_data(as_text=True)

        from app.models.solution_models import Solution

        ground_truth = Solution.query.filter_by(organization_id=org.id).count()
        assert ground_truth == 2
        # Scorecard tile renders the raw tenant-wide count. Assert on the
        # actual rendered tile value (metrics_card.html's value_testid), not
        # a loose substring match against "2" that would also match a date,
        # an id, or any other stray digit on the page.
        import re

        match = re.search(
            r'data-testid="health-total-solutions"[^>]*>([^<]*)</span>',
            health_body,
        )
        assert match is not None, "health-total-solutions tile not found in scorecard"
        assert match.group(1).strip() == "2"

    def test_status_all_bypasses_default_filter(self, app, db_session, login_as, client):
        from app.models.organization import Organization

        org = Organization(name=f"StatusAll Org {uuid.uuid4().hex[:8]}", slug=f"sa-{uuid.uuid4().hex[:8]}")
        db_session.add(org)
        db_session.flush()

        owner = _make_user(db_session, org, email=f"owner2-{uuid.uuid4().hex[:6]}@example.com")
        _make_shell_solution(db_session, org, owner, "c")
        db_session.commit()

        with app.app_context():
            login_as(client, owner)

            # The literal bug: `?status=all` used to fall through to a
            # `WHERE status = 'all'` predicate that matches zero rows ever.
            default_resp = client.get("/solutions/")
            all_resp = client.get("/solutions/?status=all")
            assert default_resp.status_code == 200
            assert all_resp.status_code == 200

        from app.models.solution_models import Solution

        assert Solution.query.filter_by(organization_id=org.id).count() == 1
        assert "Untitled Solution c" in all_resp.get_data(as_text=True)

    def test_ownership_filter_discloses_hidden_count_without_widening_visibility(
        self, app, db_session, login_as, client
    ):
        """A Solution Architect who did not create any of the org's solutions
        must see a disclosed count of what exists, but must NOT be handed the
        rows themselves — the ownership filter is legitimate authorisation,
        not a display bug, and must not be silently widened."""
        from app.models.organization import Organization
        from app.models.solution_models import Solution

        org = Organization(name=f"Ownership Org {uuid.uuid4().hex[:8]}", slug=f"own-{uuid.uuid4().hex[:8]}")
        db_session.add(org)
        db_session.flush()

        creator = _make_user(db_session, org, email=f"creator-{uuid.uuid4().hex[:6]}@example.com")
        viewer = _make_user(db_session, org, email=f"viewer-{uuid.uuid4().hex[:6]}@example.com")

        # A real (non-shell) solution so shell-filtering is not the confound.
        real_sol = Solution(
            name=f"Customer 360 {uuid.uuid4().hex[:6]}",
            status="in_progress",
            description="A" * 200,
            organization_id=org.id,
            created_by_id=creator.id,
        )
        db_session.add(real_sol)
        db_session.commit()

        with app.app_context():
            login_as(client, viewer)
            resp = client.get("/solutions/")
            assert resp.status_code == 200
            body = resp.get_data(as_text=True)

            # Never leak the other person's row into the viewer's rendered rows.
            assert real_sol.name not in body
            # But the org has 1 solution, and the viewer must be told so rather
            # than "no solutions found".
            assert "Get started by creating your first solution" not in body
            # R2-4 (round-3 refuter fix): the test's own name claims it
            # verifies the hidden count is disclosed, but previously only
            # asserted absence of the CTA -- which also passes on a blank or
            # broken page. Assert the disclosure actually rendered with the
            # correct figure (1 solution hidden by ownership).
            assert 'data-testid="solutions-hidden-disclosure"' in body
            import re

            heading_match = re.search(
                r'data-testid="solutions-empty-state-heading"[^>]*>([^<]*)<', body
            )
            assert heading_match is not None, "empty-state heading not found"
            assert "Showing 0 of 1" in heading_match.group(1), (
                f"expected the disclosed total to reflect the org's 1 solution, got: {heading_match.group(1)!r}"
            )

    def test_neither_surface_counts_another_orgs_solutions(self, app, db_session, login_as, client):
        from app.models.organization import Organization
        from app.models.solution_models import Solution

        org_a = Organization(name=f"TenantA {uuid.uuid4().hex[:8]}", slug=f"ta-{uuid.uuid4().hex[:8]}")
        org_b = Organization(name=f"TenantB {uuid.uuid4().hex[:8]}", slug=f"tb-{uuid.uuid4().hex[:8]}")
        db_session.add_all([org_a, org_b])
        db_session.flush()

        user_a = _make_user(db_session, org_a, email=f"a-{uuid.uuid4().hex[:6]}@example.com")
        user_b = _make_user(db_session, org_b, email=f"b-{uuid.uuid4().hex[:6]}@example.com")

        _make_shell_solution(db_session, org_a, user_a, "onlyA")
        for _ in range(3):
            sol = Solution(
                name=f"Org B Solution {uuid.uuid4().hex[:6]}",
                status="planned",
                description="B" * 100,
                organization_id=org_b.id,
                created_by_id=user_b.id,
            )
            db_session.add(sol)
        db_session.commit()

        with app.app_context():
            login_as(client, user_a)
            health_resp = client.get("/dashboard/health")
            list_resp = client.get("/solutions/")
            assert health_resp.status_code == 200
            assert list_resp.status_code == 200
            health_body = health_resp.get_data(as_text=True)
            list_body = list_resp.get_data(as_text=True)

        # org_a's own scorecard/list must never reflect org_b's 3 rows.
        assert "Org B Solution" not in list_body
        assert "Org B Solution" not in health_body
        assert Solution.query.filter_by(organization_id=org_a.id).count() == 1

        import re

        match = re.search(
            r'data-testid="health-total-solutions"[^>]*>([^<]*)</span>',
            health_body,
        )
        assert match is not None, "health-total-solutions tile not found in scorecard"
        assert match.group(1).strip() == "1", (
            "org_a's scorecard must report org_a's own solution count (1), "
            "not org_b's 3 rows leaking across the tenant boundary"
        )

    def test_scorecard_excludes_soft_deleted_solutions_like_the_list_does(
        self, app, db_session, login_as, client
    ):
        """R2-5 (round-3 refuter fix): the Health Scorecard's total-solutions
        tile must exclude "[DELETED] ..." soft-deleted rows, same as the
        Solutions list's org_total -- otherwise the two screens disagree on
        the same underlying question in the same session."""
        from app.models.organization import Organization
        from app.models.solution_models import Solution

        org = Organization(name=f"Deleted Org {uuid.uuid4().hex[:8]}", slug=f"del-{uuid.uuid4().hex[:8]}")
        db_session.add(org)
        db_session.flush()

        owner = _make_user(db_session, org, email=f"delowner-{uuid.uuid4().hex[:6]}@example.com")
        for i in range(2):
            sol = Solution(
                name=f"Live Solution {i} {uuid.uuid4().hex[:6]}",
                status="in_progress",
                description="L" * 100,
                organization_id=org.id,
                created_by_id=owner.id,
            )
            db_session.add(sol)
        deleted_sol = Solution(
            name=f"[DELETED] Legacy CRM {uuid.uuid4().hex[:6]}",
            status="archived",
            description="D" * 100,
            organization_id=org.id,
            created_by_id=owner.id,
        )
        db_session.add(deleted_sol)
        db_session.commit()

        # Ground truth: 3 rows physically exist, 2 are "live".
        assert Solution.query.filter_by(organization_id=org.id).count() == 3

        with app.app_context():
            login_as(client, owner)
            health_resp = client.get("/dashboard/health")
            assert health_resp.status_code == 200
            health_body = health_resp.get_data(as_text=True)

        import re

        match = re.search(
            r'data-testid="health-total-solutions"[^>]*>([^<]*)</span>',
            health_body,
        )
        assert match is not None, "health-total-solutions tile not found in scorecard"
        assert match.group(1).strip() == "2", (
            f"scorecard must exclude the soft-deleted row like the solutions "
            f"list does, got: {match.group(1)!r}"
        )

    def test_cto_tile_excludes_soft_deleted_solutions_too(
        self, app, db_session, login_as, client
    ):
        """D5 (round-4 refuter finding): the CTO persona tile's own
        total_solutions (dashboard_views.py ~line 287, /dashboard/overview)
        did not exclude "[DELETED] ..." rows, unlike the /dashboard/health
        tile fixed in round 3 -- a THIRD surface answering "how many
        solutions" with its own, uniquely-inflated count."""
        from app.models.organization import Organization
        from app.models.solution_models import Solution
        from app.models.solution_models import Solution as SolutionModel
        from sqlalchemy import not_, or_

        org = Organization(name=f"CTO Tile Org {uuid.uuid4().hex[:8]}", slug=f"ctot-{uuid.uuid4().hex[:8]}")
        db_session.add(org)
        db_session.flush()

        owner = _make_user(db_session, org, email=f"ctoowner-{uuid.uuid4().hex[:6]}@example.com")
        for i in range(2):
            sol = Solution(
                name=f"CTO Live Solution {i} {uuid.uuid4().hex[:6]}",
                status="in_progress",
                description="L" * 100,
                organization_id=org.id,
                created_by_id=owner.id,
            )
            db_session.add(sol)
        deleted_sol = Solution(
            name=f"[DELETED] CTO Legacy {uuid.uuid4().hex[:6]}",
            status="archived",
            description="D" * 100,
            organization_id=org.id,
            created_by_id=owner.id,
        )
        db_session.add(deleted_sol)
        db_session.commit()

        with app.app_context():
            # Exercise the exact query shape dashboard_views.py's CTO block
            # builds (same _test_name_patterns + [DELETED]% exclusion), scoped
            # to this org, since the persona_metrics dict isn't independently
            # exposed with a stable testid in the rendered overview page.
            _test_name_patterns = [
                "J1-AutoTest-%", "New Solution%", "J1 Bootstrap%",
                "J1 Test%", "J1 Regression%",
                "J1-Debug%", "J1-Test-%",
                "%E2E Test%", "%Journey Test%", "AI Test%",
                "Minimal Test%", "QA Test%", "Driver Test%",
                "Blueprint Test%", "%JDD Test%", "%Gap2 Persistence%",
                "Post-Deploy%", "%Smoke Test%", "% Test Solution%",
                "% Test Solution", "%Audit Test%", "%Forensic Audit%",
                "%Verification Test%", "%Uniformity Verification%",
                "% Test Programme%", "%PESTLE News Analyser%",
                "MDM Test%", "Create an architecture for%",
            ]
            solutions = SolutionModel.query.filter(
                SolutionModel.organization_id == org.id,
                SolutionModel.name.isnot(None),
                not_(SolutionModel.name.like("[DELETED]%")),
                not_(or_(*[SolutionModel.name.like(p) for p in _test_name_patterns])),
            ).all()
            assert len(solutions) == 2, (
                f"D5: CTO tile total_solutions must exclude soft-deleted rows, "
                f"got {len(solutions)}"
            )

    def test_privileged_user_bu_filter_with_zero_matches_discloses(
        self, app, db_session, login_as, client
    ):
        """R2-1 (round-3 refuter fix): a privileged user (_can_see_all=True,
        e.g. business_architect) scoped to a business unit whose domain
        matches none of the org's solutions must NOT see the false
        "create your first solution" empty state -- the org has solutions,
        just none in that BU. The BU domain filter is applied to every user
        regardless of `_can_see_all`, so its disclosure must be too."""
        from app.models.business_layer import BusinessActor
        from app.models.organization import Organization
        from app.models.solution_models import Solution

        org = Organization(name=f"BuPriv Org {uuid.uuid4().hex[:8]}", slug=f"bp-{uuid.uuid4().hex[:8]}")
        db_session.add(org)
        db_session.flush()

        finance_bu = BusinessActor(name=f"Finance-{uuid.uuid4().hex[:6]}", organization_id=org.id)
        db_session.add(finance_bu)
        db_session.flush()

        viewer = _make_user(
            db_session, org, email=f"privbu-{uuid.uuid4().hex[:6]}@example.com",
            enterprise_role="business_architect",
        )
        viewer.business_unit_id = finance_bu.id
        db_session.add(viewer)

        other_creator = _make_user(db_session, org, email=f"hrcreator-{uuid.uuid4().hex[:6]}@example.com")
        for i in range(3):
            sol = Solution(
                name=f"HR Solution {i} {uuid.uuid4().hex[:6]}",
                status="in_progress",
                description="H" * 100,
                business_domain="HR",
                organization_id=org.id,
                created_by_id=other_creator.id,
            )
            db_session.add(sol)
        db_session.commit()

        with app.app_context():
            login_as(client, viewer)
            resp = client.get("/solutions/")
            assert resp.status_code == 200
            body = resp.get_data(as_text=True)

            assert "Get started by creating your first solution" not in body, (
                "R2-1: a privileged user's BU domain filter matching zero rows "
                "must not fall back to the false first-run empty state -- the "
                "org has 3 solutions, just none in this viewer's BU"
            )
            assert 'data-testid="solutions-hidden-disclosure"' in body

            # D1 (round-4 refuter finding): the heading must show the
            # ORG-WIDE total (matching the Health Scorecard's grain), not a
            # BU-scoped subtotal presented as if it were the org total --
            # "Showing 0 of 0" next to "3 solutions exist" was a self-
            # contradiction. org_total is org-wide (3) unconditionally now.
            import re
            heading_match = re.search(
                r'data-testid="solutions-empty-state-heading"[^>]*>\s*([^<]*)\s*<',
                body,
            )
            assert heading_match is not None
            assert "Showing 0 of 3" in heading_match.group(1), (
                f"expected the org-wide total (3) in the heading, got: {heading_match.group(1)!r}"
            )

            # D7 (round-4 refuter finding): tighten the count assertion to the
            # specific disclosure testid rather than any stray '3' in the page
            # (pagination/stat cards can also contain a bare '3').
            reason_match = re.search(
                r'data-testid="solutions-role-filter-reason"[^>]*>(.*?)</p>',
                body,
                re.DOTALL,
            )
            assert reason_match is not None, "expected the role/BU-filter reason to be disclosed"
            reason_text = reason_match.group(1)
            assert "3" in reason_text, f"expected the hidden count (3) in the role-filter reason, got: {reason_text!r}"
            assert "business unit" in reason_text

            # D1: the escape action row must not be empty for a BU-scoped user.
            assert 'data-testid="solutions-view-all-bu"' in body, (
                "expected a 'View all business units' escape action in the BU empty state"
            )

    def test_search_filter_zero_results_attributed_to_search_not_ownership(
        self, app, db_session, login_as, client
    ):
        """R2-2 (round-3 refuter fix): a non-privileged owner of 2 solutions
        who searches for a term matching neither must see the zero result
        attributed to the search, not to ownership/role filtering -- their
        own rows were never hidden by permissions, only by the search term."""
        from app.models.organization import Organization
        from app.models.solution_models import Solution

        org = Organization(name=f"Search Org {uuid.uuid4().hex[:8]}", slug=f"srch-{uuid.uuid4().hex[:8]}")
        db_session.add(org)
        db_session.flush()

        owner = _make_user(db_session, org, email=f"searchowner-{uuid.uuid4().hex[:6]}@example.com")
        for i in range(2):
            sol = Solution(
                name=f"Customer Portal {i} {uuid.uuid4().hex[:6]}",
                status="in_progress",
                description="C" * 100,
                organization_id=org.id,
                created_by_id=owner.id,
            )
            db_session.add(sol)
        db_session.commit()

        with app.app_context():
            login_as(client, owner)
            resp = client.get("/solutions/?search=zzz_no_match_zzz")
            assert resp.status_code == 200
            body = resp.get_data(as_text=True)

            assert "Get started by creating your first solution" not in body
            # Round 4: the per-cause testid was unified into
            # "solutions-filter-reason" (covers search/status/domain/type/
            # dates/worklist-bucket alike, see D2/D3) rather than a
            # search-specific one.
            assert 'data-testid="solutions-filter-reason"' in body, (
                "R2-2: a zero-result search must be attributed to the active "
                "filter(s), not silently rendered as a generic/ownership empty state"
            )
            assert 'search &#34;zzz_no_match_zzz&#34;' in body or 'search "zzz_no_match_zzz"' in body, (
                "expected the search term to be named in the filter-reason message"
            )
            # The owner's own 2 rows must NOT be misattributed as hidden by
            # role/ownership/BU -- they were only excluded by the search term,
            # so hidden_by_role_filter must be 0 and its reason must not render.
            assert (
                'data-testid="solutions-role-filter-reason"' not in body
            ), "R2-2: the owner's own rows must not be misattributed to role/ownership when a search term is the true cause"
            assert 'data-testid="solutions-clear-filters"' in body

    def test_domain_filter_zero_matches_discloses_not_ownership(
        self, app, db_session, login_as, client
    ):
        """D2 (round-4 refuter finding): the domain/type/date filters were not
        accounted for by any disclosure block at all -- a user filtering by a
        domain none of their own solutions has saw the wrong cause (the
        default-shell message) or the false first-run CTA. The unified
        `hidden_by_filters` figure must cover this without a dedicated
        per-filter-type block."""
        from app.models.organization import Organization
        from app.models.solution_models import Solution

        org = Organization(name=f"Domain Org {uuid.uuid4().hex[:8]}", slug=f"dm-{uuid.uuid4().hex[:8]}")
        db_session.add(org)
        db_session.flush()

        owner = _make_user(db_session, org, email=f"domainowner-{uuid.uuid4().hex[:6]}@example.com")
        for i in range(5):
            sol = Solution(
                name=f"Logistics Solution {i} {uuid.uuid4().hex[:6]}",
                status="in_progress",
                description="L" * 100,
                business_domain="Logistics",
                organization_id=org.id,
                created_by_id=owner.id,
            )
            db_session.add(sol)
        db_session.commit()

        with app.app_context():
            login_as(client, owner)
            resp = client.get("/solutions/?domain=Finance")
            assert resp.status_code == 200
            body = resp.get_data(as_text=True)

            assert "Get started by creating your first solution" not in body, (
                "D2: a domain filter matching none of the owner's 5 solutions "
                "must not fall back to the false first-run empty state"
            )
            assert 'data-testid="solutions-filter-reason"' in body
            # Jinja autoescapes quotes to &#34; in text nodes, so match the
            # words rather than the literal quote characters.
            assert "domain" in body and "Finance" in body
            assert 'data-testid="solutions-role-filter-reason"' not in body, (
                "D2: the owner's own rows must not be misattributed to "
                "ownership/role when a domain filter is the true cause"
            )

    def test_worklist_bucket_zero_matches_discloses_not_first_run(
        self, app, db_session, login_as, client
    ):
        """D3 (round-4 refuter finding): worklist-bucket filtering
        (?status=needs_setup etc, reachable by clicking the stat cards) still
        produced the false "create your first solution" CTA because bucket
        filtering happens Python-side, after status_filter is blanked, and
        wasn't counted by any disclosure block. `pagination.total` (the
        `_ManualPagination` branch) now feeds `hidden_by_filters` directly, so
        this is covered without a bucket-specific block."""
        from app.models.organization import Organization
        from app.models.solution_models import Solution

        org = Organization(name=f"Worklist Org {uuid.uuid4().hex[:8]}", slug=f"wl-{uuid.uuid4().hex[:8]}")
        db_session.add(org)
        db_session.flush()

        owner = _make_user(db_session, org, email=f"worklistowner-{uuid.uuid4().hex[:6]}@example.com")
        # A solution that is fully "ready for review" -- well past needs_setup.
        sol = Solution(
            name=f"Mature Solution {uuid.uuid4().hex[:6]}",
            status="in_progress",
            description="M" * 500,
            business_domain="Finance",
            organization_id=org.id,
            created_by_id=owner.id,
            maturity_current=90,
        )
        db_session.add(sol)
        db_session.commit()

        with app.app_context():
            login_as(client, owner)
            # This fixture solution has no capabilities linked, so the
            # worklist classifier (solution_design_routes.py ~line 538) puts
            # it in "needs_setup" regardless of description/maturity --
            # "ready_for_review" is therefore the bucket with 0 matches.
            resp = client.get("/solutions/?status=ready_for_review")
            assert resp.status_code == 200
            body = resp.get_data(as_text=True)

            assert "Get started by creating your first solution" not in body, (
                "D3: clicking a worklist stat card showing count 0 must not "
                "produce the false first-run empty state -- the owner has 1 "
                "solution, just not in this bucket"
            )
            assert 'data-testid="solutions-filter-reason"' in body
            assert "worklist" in body and "Ready for Review" in body

    def test_status_all_bypasses_crashing_url_for_splat(self, app, db_session, login_as, client):
        """D4 (round-4 refuter finding, security-adjacent): the previous
        `clear_filters_url` computation splatted raw `request.args` into
        `url_for(**...)`, which reserves keywords like `endpoint`/`_scheme`/
        `_method`. A malformed/crawler query string must never 500 or
        silently fall back to the false empty state via the blanket except."""
        from app.models.organization import Organization

        org = Organization(name=f"D4 Org {uuid.uuid4().hex[:8]}", slug=f"d4-{uuid.uuid4().hex[:8]}")
        db_session.add(org)
        db_session.flush()
        owner = _make_user(db_session, org, email=f"d4owner-{uuid.uuid4().hex[:6]}@example.com")
        db_session.commit()

        with app.app_context():
            login_as(client, owner)
            for bad_qs in ("?endpoint=x", "?_scheme=https", "?_method=POST", "?_anchor=x", "?_external=1"):
                resp = client.get(f"/solutions/{bad_qs}")
                assert resp.status_code == 200, f"{bad_qs} must not 500"
                body = resp.get_data(as_text=True)
                assert 'data-testid="solutions-load-error"' not in body, (
                    f"{bad_qs} must not fall through to the load-error/blanket-except path"
                )

    def test_duplicate_query_params_do_not_break_page(self, app, db_session, login_as, client):
        """D8 (round-4 refuter finding, minor): repeated query params of the
        same key must not 500 the page. The unified clear_filters_url no
        longer round-trips request.args at all, sidestepping this class
        entirely."""
        from app.models.organization import Organization

        org = Organization(name=f"D8 Org {uuid.uuid4().hex[:8]}", slug=f"d8-{uuid.uuid4().hex[:8]}")
        db_session.add(org)
        db_session.flush()
        owner = _make_user(db_session, org, email=f"d8owner-{uuid.uuid4().hex[:6]}@example.com")
        db_session.commit()

        with app.app_context():
            login_as(client, owner)
            resp = client.get("/solutions/?domain=A&domain=B")
            assert resp.status_code == 200

    def test_clear_all_filters_link_clears_domain_and_type_too(
        self, app, db_session, login_as, client
    ):
        """D6 (round-4 refuter finding): the "clear filters" action must
        clear ALL active filters (search, status, domain, type, dates, bu),
        not just search/status/page, and must be reachable from an empty
        state produced by a non-search filter."""
        from app.models.organization import Organization
        from app.models.solution_models import Solution

        org = Organization(name=f"D6 Org {uuid.uuid4().hex[:8]}", slug=f"d6-{uuid.uuid4().hex[:8]}")
        db_session.add(org)
        db_session.flush()
        owner = _make_user(db_session, org, email=f"d6owner-{uuid.uuid4().hex[:6]}@example.com")
        sol = Solution(
            name=f"D6 Solution {uuid.uuid4().hex[:6]}",
            status="in_progress",
            description="D" * 100,
            business_domain="Logistics",
            organization_id=org.id,
            created_by_id=owner.id,
        )
        db_session.add(sol)
        db_session.commit()

        with app.app_context():
            login_as(client, owner)
            resp = client.get("/solutions/?domain=Finance&type=custom")
            assert resp.status_code == 200
            body = resp.get_data(as_text=True)
            assert 'data-testid="solutions-clear-filters"' in body

            import re

            match = re.search(r'href="([^"]*)"[^>]*data-testid="solutions-clear-filters"', body)
            assert match is not None, "could not locate the clear-filters link href"
            clear_href = match.group(1)
            assert "domain" not in clear_href and "type" not in clear_href, (
                f"clear-filters link must drop every active filter, got: {clear_href!r}"
            )
