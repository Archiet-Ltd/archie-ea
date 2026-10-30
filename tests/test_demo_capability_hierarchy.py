"""The demonstration organisation's capabilities nest: level-1 capabilities carry
level-2 children, and those carry level-3 children, so the capability map and the
hierarchy show containment rather than a flat list."""

import os

import pytest

pytestmark = [pytest.mark.journey]


@pytest.fixture(scope="module")
def demo_org_id(app):
    from app.commands.seed_demo_company import seed_demo_company
    from app.models.user import User

    os.environ["DEMO_USER_PASSWORD"] = "Sm0ke-Test-Pass-1"
    try:
        with app.app_context():
            seed_demo_company()
            return User.query.filter_by(email="demo@lantern-quay.example.com").one().organization_id
    finally:
        del os.environ["DEMO_USER_PASSWORD"]


def _caps(org_id):
    from app.models.unified_capability import UnifiedCapability

    return UnifiedCapability.query.filter(UnifiedCapability.organization_id == org_id).all()


def test_demo_hierarchy_has_level_two_and_level_three(app, demo_org_id):
    with app.app_context():
        by_level = {}
        for cap in _caps(demo_org_id):
            by_level.setdefault(cap.level, []).append(cap)
        assert len(by_level.get(2, [])) > 0, by_level.keys()
        assert len(by_level.get(3, [])) > 0, by_level.keys()


def test_every_child_sits_under_a_shallower_parent_in_the_same_domain(app, demo_org_id):
    with app.app_context():
        caps = _caps(demo_org_id)
        by_id = {c.id: c for c in caps}
        nested = [c for c in caps if c.parent_capability_id]
        assert len(nested) >= 20
        for child in nested:
            parent = by_id[child.parent_capability_id]
            assert parent.level < child.level, (parent.code, child.code)
            assert parent.domain_id == child.domain_id, (parent.code, child.code)


def test_business_capability_twins_carry_the_same_parents(app, demo_org_id):
    with app.app_context():
        from app.models.business_capabilities import BusinessCapability

        rows = BusinessCapability.query.filter(BusinessCapability.organization_id == demo_org_id).all()
        by_id = {r.id: r for r in rows}
        nested = [r for r in rows if r.parent_capability_id]
        assert len(nested) >= 20
        for child in nested:
            assert by_id[child.parent_capability_id].level < child.level


def test_seeding_again_creates_no_second_copy(app, demo_org_id):
    from app.commands.seed_demo_company import seed_demo_company

    with app.app_context():
        before = len(_caps(demo_org_id))
        os.environ["DEMO_USER_PASSWORD"] = "Sm0ke-Test-Pass-1"
        try:
            seed_demo_company()
        finally:
            del os.environ["DEMO_USER_PASSWORD"]
        assert len(_caps(demo_org_id)) == before
