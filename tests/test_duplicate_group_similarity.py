"""A duplicate group shows the similarity its own pairs measured.

The list and the group page read the one stored figure, the threshold is
applied to that same pair score, and a run only clears its own organisation's
earlier groups."""

import contextlib
import uuid
from unittest import mock

import pytest

SERVICE = (
    "app.modules.duplicate_detection.services.unified_duplicate_detection_service."
    "UnifiedDuplicateDetectionService._calculate_name_description_similarity"
)


def _user(db_session, org, role_name="Architect"):
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(
        email=f"dg-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Test",
        last_name=role_name,
        organization_id=org.id,
        role=Role.query.filter_by(name=role_name).first(),
        is_org_admin=False,
        is_platform_admin=False,
        confirmed=True,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    return user


def _apps(db_session, org, *names):
    from app.models.application_portfolio import ApplicationComponent

    rows = [
        ApplicationComponent(name=n, organization_id=org.id, lifecycle_status="operational")
        for n in names
    ]
    db_session.add_all(rows)
    db_session.flush()
    return rows


def _run_detection(tenant_ctx, org, threshold, score=None, scores=None, strategy="fast"):
    """Run detection in the organisation, with the pair measure fixed.

    strategy="fast" calls run_detection (with its hybrid strategy);
    strategy="hybrid" calls run_detection_hybrid."""
    from app.modules.duplicate_detection.services.unified_duplicate_detection_service import (
        UnifiedDuplicateDetectionService,
    )

    service = UnifiedDuplicateDetectionService()
    with tenant_ctx(org.id):
        if scores is not None:
            patch = mock.patch(SERVICE, side_effect=list(scores))
        elif score is not None:
            patch = mock.patch(SERVICE, return_value=score)
        else:
            patch = contextlib.nullcontext()
        with patch:
            if strategy == "hybrid":
                return service.run_detection_hybrid(similarity_threshold=threshold)
            return service.run_detection(similarity_threshold=threshold, strategy="hybrid")


def _groups_of(org):
    """The groups whose member applications belong to the organisation."""
    from app import db
    from app.models.unified_duplicate_detection import UnifiedDuplicateGroup

    ids = [
        r[0]
        for r in db.session.execute(
            db.text(
                "SELECT DISTINCT m.group_id FROM unified_group_members m "
                "JOIN application_components ac ON ac.id = m.application_id "
                "WHERE ac.organization_id = :org"
            ),
            {"org": org.id},
        )
    ]
    if not ids:
        return []
    return UnifiedDuplicateGroup.query.filter(UnifiedDuplicateGroup.id.in_(ids)).all()


@pytest.fixture
def world(db_session, make_org):
    own, other = make_org("dgs-own"), make_org("dgs-other")
    return {"own": own, "other": other}


def test_group_keeps_the_score_its_pair_measured_not_the_run_threshold(db_session, tenant_ctx, world):
    _apps(db_session, world["own"], "Ledger Core", "Ledger Core Copy")
    result = _run_detection(tenant_ctx, world["own"], 0.50, score=0.53)
    assert result["success"], result
    groups = _groups_of(world["own"])
    assert len(groups) == 1
    assert groups[0].similarity_score == pytest.approx(0.53)
    assert groups[0].similarity_threshold == pytest.approx(0.50)
    assert groups[0].match_details == {"pair_count": 1}


def test_pair_below_the_threshold_forms_no_group(db_session, tenant_ctx, world):
    _apps(db_session, world["own"], "Ledger Core", "Ledger Core Copy")
    result = _run_detection(tenant_ctx, world["own"], 0.55, score=0.53)
    assert result["success"], result
    assert result["groups_found"] == 0
    assert _groups_of(world["own"]) == []


def test_group_of_several_pairs_stores_their_mean(db_session, tenant_ctx, world):
    _apps(db_session, world["own"], "Ledger Core", "Ledger Core Copy", "Ledger Core Two")
    # first app against the second and third; the second against the third is never asked
    _run_detection(tenant_ctx, world["own"], 0.50, scores=[0.60, 0.70])
    (group,) = _groups_of(world["own"])
    assert group.similarity_score == pytest.approx(0.65)
    assert group.match_details == {"pair_count": 2}


def test_hybrid_run_stores_the_pair_mean_and_exact_groups_score_one(db_session, tenant_ctx, world):
    _apps(db_session, world["own"], "Ledger Core", "Ledger Core Copy")
    _run_detection(tenant_ctx, world["own"], 0.50, score=0.53, strategy="hybrid")
    (group,) = _groups_of(world["own"])
    assert group.similarity_score == pytest.approx(0.53)
    _apps(db_session, world["own"], "Twin", "Twin")
    _run_detection(tenant_ctx, world["own"], 0.99, score=0.0, strategy="hybrid")
    exact = [g for g in _groups_of(world["own"]) if g.duplicate_type == "exact"]
    assert exact and all(g.similarity_score == pytest.approx(1.0) for g in exact)


def test_a_run_leaves_another_organisations_groups_alone(db_session, tenant_ctx, world):
    _apps(db_session, world["own"], "Ledger Core", "Ledger Core Copy")
    _apps(db_session, world["other"], "Payroll Hub", "Payroll Hub Copy")
    _run_detection(tenant_ctx, world["other"], 0.50, score=0.62)
    (before,) = _groups_of(world["other"])
    before_id, before_score = before.id, before.similarity_score

    _run_detection(tenant_ctx, world["own"], 0.50, score=0.53)
    _run_detection(tenant_ctx, world["own"], 0.50, score=0.58)

    (after,) = _groups_of(world["other"])
    assert (after.id, after.similarity_score) == (before_id, before_score)
    (own,) = _groups_of(world["own"])
    assert own.similarity_score == pytest.approx(0.58)


def test_list_and_group_page_show_the_same_figure_and_say_how_it_was_worked_out(
    app, db_session, tenant_ctx, login_as, client, world
):
    _apps(db_session, world["own"], "Ledger Core", "Ledger Core Copy")
    user = _user(db_session, world["own"])
    _run_detection(tenant_ctx, world["own"], 0.50, score=0.53)
    (group,) = _groups_of(world["own"])
    db_session.commit()

    login_as(client, user)
    listing = client.get("/duplicate-detection/simple/groups").get_json()
    row = next(g for g in listing["groups"] if g["id"] == group.id)
    assert row["overall_similarity"] == pytest.approx(0.53)
    assert row["similarity_basis"] == "Average of 1 pair"

    login_as(client, user)
    page = client.get(f"/duplicate-detection/simple/group/{group.id}")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "53%" in html and "50%" not in html.split("Overall Similarity")[1][:400]
    assert "Average of 1 pair" in html
    assert "Aggregate match score" not in html


def test_an_older_exact_group_holding_zero_reads_as_a_full_name_match(
    app, db_session, tenant_ctx, login_as, client, world
):
    """Exact groups made before the score was stored hold the 0.0 default."""
    _apps(db_session, world["own"], "Ledger Core", "Ledger Core Copy")
    user = _user(db_session, world["own"])
    _run_detection(tenant_ctx, world["own"], 0.50, score=0.53)
    (group,) = _groups_of(world["own"])
    group.duplicate_type, group.similarity_score = "exact", 0.0
    db_session.commit()

    login_as(client, user)
    row = next(g for g in client.get("/duplicate-detection/simple/groups").get_json()["groups"]
               if g["id"] == group.id)
    assert row["overall_similarity"] == pytest.approx(1.0)
    assert row["similarity_basis"] == "Names match exactly"

    login_as(client, user)
    html = client.get(f"/duplicate-detection/simple/group/{group.id}").get_data(as_text=True)
    card = html.split("Overall Similarity")[1][:500]
    assert "100%" in card and "Names match exactly" in card
    assert "0%" not in card.replace("100%", "") and "Not scored" not in html

    login_as(client, user)
    body = client.get("/duplicate-detection/simple/api/statistics").get_json()
    assert (body["total_groups"], body["high_priority_groups"]) == (1, 1)


class _Named:
    def __init__(self, name, description=""):
        self.name, self.description = name, description


@pytest.mark.parametrize(
    "first,second,threshold,groups",
    [
        ("Monelytics", "Monelytics (usage, quotas, plans, billing)", 0.55, True),
        ("Ifiok", "Ifiok", 0.55, True),
        ("Billing, metering, quotas and entitlements", "Subscription, Billing and Plans", 0.55, False),
        ("Billing, metering, quotas and entitlements", "Subscription, Billing and Plans", 0.45, True),
        ("Ledger Core", "Payroll Hub", 0.45, False),
    ],
)
def test_measured_similarity_of_near_duplicate_names(app, first, second, threshold, groups):
    """The measure as it stands: which near-duplicate names reach a threshold."""
    from app.modules.duplicate_detection.services.unified_duplicate_detection_service import (
        UnifiedDuplicateDetectionService,
    )

    score = UnifiedDuplicateDetectionService()._calculate_name_description_similarity(
        _Named(first), _Named(second)
    )
    assert (score >= threshold) is groups, score


def _dashboard(client, login_as, user):
    login_as(client, user)
    body = client.get("/duplicate-detection/simple/api/statistics").get_json()
    return body["total_groups"], body["high_priority_groups"]


def test_dashboard_figures_count_only_the_signed_in_organisations_groups(
    app, db_session, tenant_ctx, login_as, client, world
):
    _apps(db_session, world["own"], "Ledger Core", "Ledger Core Copy")
    _apps(db_session, world["other"], "Payroll Hub", "Payroll Hub Copy")
    own_user = _user(db_session, world["own"])
    other_user = _user(db_session, world["other"])
    _run_detection(tenant_ctx, world["own"], 0.50, score=0.53)
    _run_detection(tenant_ctx, world["other"], 0.50, score=0.90)
    db_session.commit()

    assert len(_groups_of(world["other"])) == 1
    assert _dashboard(client, login_as, own_user) == (1, 0)
    assert _dashboard(client, login_as, other_user) == (1, 1)

    login_as(client, own_user)
    listing = client.get("/duplicate-detection/simple/groups").get_json()
    shown = {g["id"] for g in listing["groups"]}
    assert shown == {g.id for g in _groups_of(world["own"])}


def test_a_run_clears_groups_left_with_no_live_members_and_spares_other_organisations(
    app, db_session, tenant_ctx, world
):
    from app import db
    from app.models.unified_duplicate_detection import UnifiedDuplicateGroup

    own_apps = _apps(db_session, world["own"], "Ledger Core", "Ledger Core Copy")
    _apps(db_session, world["other"], "Payroll Hub", "Payroll Hub Copy")
    _run_detection(tenant_ctx, world["own"], 0.50, score=0.53)
    _run_detection(tenant_ctx, world["other"], 0.50, score=0.62)
    (other_group,) = _groups_of(world["other"])
    (own_group,) = _groups_of(world["own"])
    other_id, own_id = other_group.id, own_group.id

    # both of the group's applications are deleted: the group has no live member
    for row in own_apps:
        db_session.delete(row)
    db_session.flush()
    assert db.session.execute(
        db.text("SELECT COUNT(*) FROM unified_group_members WHERE group_id = :g"), {"g": own_id}
    ).scalar() == 0

    _apps(db_session, world["own"], "Rota Tool", "Rota Tool Copy")
    _run_detection(tenant_ctx, world["own"], 0.50, score=0.58)

    assert UnifiedDuplicateGroup.query.filter_by(id=own_id).count() == 0
    assert UnifiedDuplicateGroup.query.filter_by(id=other_id).count() == 1
    assert [g.similarity_score for g in _groups_of(world["own"])] == [pytest.approx(0.58)]



def test_last_detection_is_the_signed_in_organisations_own_run(
    app, db_session, tenant_ctx, login_as, client, world
):
    from app.models.unified_duplicate_detection import UnifiedDetectionRun

    _apps(db_session, world["own"], "Ledger Core", "Ledger Core Copy")
    _apps(db_session, world["other"], "Payroll Hub", "Payroll Hub Copy")
    own_user = _user(db_session, world["own"])
    other_user = _user(db_session, world["other"])
    _run_detection(tenant_ctx, world["own"], 0.50, score=0.53)
    _run_detection(tenant_ctx, world["other"], 0.50, score=0.90)
    # the other organisation runs last, so an unscoped "latest" would be theirs
    db_session.commit()
    own_run = UnifiedDetectionRun.query.filter_by(organization_id=world["own"].id).one()
    other_run = UnifiedDetectionRun.query.filter_by(organization_id=world["other"].id).one()
    assert own_run.id != other_run.id

    for user, expected, foreign in ((own_user, own_run, other_run), (other_user, other_run, own_run)):
        login_as(client, user)
        body = client.get("/duplicate-detection/simple/api/statistics").get_json()
        assert body["latest_run"]["id"] == expected.id
        assert body["latest_run"]["id"] != foreign.id


def test_an_organisation_that_never_ran_detection_has_no_last_detection(
    app, db_session, tenant_ctx, login_as, client, world
):
    _apps(db_session, world["other"], "Payroll Hub", "Payroll Hub Copy")
    _run_detection(tenant_ctx, world["other"], 0.50, score=0.90)
    quiet_user = _user(db_session, world["own"])
    db_session.commit()
    login_as(client, quiet_user)
    body = client.get("/duplicate-detection/simple/api/statistics").get_json()
    assert body["latest_run"] is None


def _two_organisations_with_runs(db_session, tenant_ctx, world):
    _apps(db_session, world["own"], "Ledger Core", "Ledger Core Copy")
    _apps(db_session, world["other"], "Payroll Hub", "Payroll Hub Copy")
    _run_detection(tenant_ctx, world["own"], 0.50, score=0.53)
    _run_detection(tenant_ctx, world["other"], 0.50, score=0.90)
    db_session.commit()


def test_reads_with_no_active_organisation_return_nothing(db_session, tenant_ctx, world):
    from app.modules.duplicate_detection.services.unified_duplicate_detection_service import (
        count_organisation_groups,
    )
    from app.modules.duplicate_detection.services.unified_duplicate_service import (
        UnifiedDuplicateService,
    )

    _two_organisations_with_runs(db_session, tenant_ctx, world)
    with tenant_ctx(None):
        assert UnifiedDuplicateService.get_runs() == []
        stats = UnifiedDuplicateService.get_statistics()
        assert stats["latest_run"] is None
        assert stats["total_runs"] == 0
        assert (stats["total_groups"], stats["pending_groups"]) == (0, 0)
        assert (stats["resolved_groups"], stats["ignored_groups"]) == (0, 0)
        assert stats["total_potential_savings"] == 0
        assert count_organisation_groups() == (0, 0)
    with tenant_ctx(world["own"].id):
        run_id = UnifiedDuplicateService.get_runs()[0]["id"]
    with tenant_ctx(None):
        assert UnifiedDuplicateService.get_run(run_id) is None


def test_statistics_count_only_the_organisations_own_groups_and_agree_with_the_list(
    db_session, tenant_ctx, login_as, client, world
):
    from app.modules.duplicate_detection.services.unified_duplicate_service import (
        UnifiedDuplicateService,
    )

    _two_organisations_with_runs(db_session, tenant_ctx, world)
    for org in (world["own"], world["other"]):
        with tenant_ctx(org.id):
            stats = UnifiedDuplicateService.get_statistics()
            assert stats["total_groups"] == len(_groups_of(org)) == 1
            assert stats["pending_groups"] == 1
            assert stats["resolved_groups"] == 0
            assert stats["total_runs"] == 1
            assert stats["latest_run"]["id"] == UnifiedDuplicateService.get_runs()[0]["id"]

    own_user = _user(db_session, world["own"])
    db_session.commit()
    login_as(client, own_user)
    body = client.get("/duplicate-detection/simple/api/statistics").get_json()
    listing = client.get("/duplicate-detection/simple/groups").get_json()
    assert body["total_groups"] == len(listing["groups"]) == 1
