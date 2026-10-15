"""entity_in_active_org / require_entity_in_active_org fail closed.

The automatic tenant filter is off when no active organisation is known, so a
guard that relies on it alone answers for every organisation in that case. The
strict helpers name the active organisation and refuse when it is unknown.
"""

from __future__ import annotations

import pytest
from werkzeug.exceptions import NotFound

pytestmark = pytest.mark.usefixtures("db_session")


@pytest.fixture
def two_solutions(db_session, make_org):
    from app.models.solution_models import Solution

    org_a = make_org("guard-a")
    org_b = make_org("guard-b")
    sol_a = Solution(name="Guard A", organization_id=org_a.id)
    sol_b = Solution(name="Guard B", organization_id=org_b.id)
    db_session.add_all([sol_a, sol_b])
    db_session.flush()
    return org_a.id, sol_a.id, sol_b.id


def test_own_organisation_row_is_found(two_solutions, tenant_ctx):
    from app.models.solution_models import Solution
    from app.utils.route_guards import entity_in_active_org

    org_a, sol_a, _ = two_solutions
    with tenant_ctx(org_a):
        assert entity_in_active_org(Solution, sol_a) is True


def test_foreign_organisation_row_is_refused(two_solutions, tenant_ctx):
    from app.models.solution_models import Solution
    from app.utils.route_guards import entity_in_active_org

    org_a, _, sol_b = two_solutions
    with tenant_ctx(org_a):
        assert entity_in_active_org(Solution, sol_b) is False


def test_unknown_organisation_refuses_even_an_existing_row(two_solutions, tenant_ctx):
    from app.models.solution_models import Solution
    from app.utils.route_guards import entity_in_active_org

    _, sol_a, sol_b = two_solutions
    with tenant_ctx(None):
        assert entity_in_active_org(Solution, sol_a) is False
        assert entity_in_active_org(Solution, sol_b) is False


def test_missing_and_none_ids_are_refused(two_solutions, tenant_ctx):
    from app.models.solution_models import Solution
    from app.utils.route_guards import entity_in_active_org

    org_a, _, _ = two_solutions
    with tenant_ctx(org_a):
        assert entity_in_active_org(Solution, 999999999) is False
        assert entity_in_active_org(Solution, None) is False


def test_require_returns_row_or_404(two_solutions, tenant_ctx):
    from app.models.solution_models import Solution
    from app.utils.route_guards import require_entity_in_active_org

    org_a, sol_a, sol_b = two_solutions
    with tenant_ctx(org_a):
        assert require_entity_in_active_org(Solution, sol_a).id == sol_a
        with pytest.raises(NotFound):
            require_entity_in_active_org(Solution, sol_b)
    with tenant_ctx(None):
        with pytest.raises(NotFound):
            require_entity_in_active_org(Solution, sol_a)
