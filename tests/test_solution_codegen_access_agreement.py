"""Blueprint visibility and Code Workbench authorization must agree."""

from types import SimpleNamespace

from app.modules.codegen.routes._helpers import _check_access
from app.modules.solutions_strategic.v2.routes.solution_design_routes import (
    _check_solution_access,
)


def _user(user_id, email, *, admin=False, platform_admin=False):
    return SimpleNamespace(
        id=user_id,
        email=email,
        is_authenticated=True,
        is_admin=lambda: admin,
        is_platform_admin=platform_admin,
    )


def _solution(**overrides):
    values = {
        "created_by_id": 10,
        "solution_owner": "owner@example.com",
        "business_sponsor": None,
        "technical_lead": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_non_stakeholder_is_not_admitted_by_bound_is_admin_method():
    outsider = _user(20, "outsider@example.com")

    assert _check_solution_access(_solution(), outsider) is False
    assert _check_access(_solution(), outsider) is False


def test_blueprint_and_codegen_admit_creator_stakeholder_and_platform_admin():
    solution = _solution()
    users = [
        _user(10, "creator@example.com"),
        _user(20, "OWNER@example.com"),
        _user(30, "platform@example.com", platform_admin=True),
    ]

    for user in users:
        assert _check_solution_access(solution, user) is True
        assert _check_access(solution, user) is True


def test_global_admin_flag_alone_fails_closed_when_the_richer_check_cannot_run(monkeypatch):
    """R2-6 (PR 428 round 3): both helpers used to DEGRADE TO THE LEGACY
    GLOBAL ``is_admin()`` ANSWER (``return True``) whenever the richer,
    organisation-aware check could not be made -- the solution has no
    ``organization_id``, the user object has none either (exactly this
    fixture's bare duck-typed shape), or ``rbac_service.is_org_admin``
    itself raised. That is fail OPEN: a holder of the global ADMINISTER
    flag with no relationship to this solution's organisation at all was
    admitted the moment either input was missing or the lookup errored --
    reproduced here with neither the solution nor the user carrying
    ``organization_id`` (so the lookup is never attempted) and, separately,
    by making ``rbac_service.is_org_admin`` raise outright. Both must now
    deny, not admit.
    """
    stranger = _user(99, "stranger@example.com", admin=True)
    solution_without_org = _solution()  # no organization_id, same as the fixture above
    assert not hasattr(solution_without_org, "organization_id")
    assert not hasattr(stranger, "organization_id")

    assert _check_solution_access(solution_without_org, stranger) is False
    assert _check_access(solution_without_org, stranger) is False

    # Same admin, but now the solution DOES carry an organization_id the
    # richer check can be attempted against -- and rbac_service.is_org_admin
    # raises instead of answering. Must still deny, never fall back to the
    # bare global flag. Both helpers do their
    # ``from app.services.rbac_service import rbac_service`` inside the
    # function body (not a module-level import), so the one real
    # ``rbac_service`` singleton's method is what needs patching.
    from app.services.rbac_service import rbac_service as real_rbac_service

    def _raise(*args, **kwargs):
        raise RuntimeError("simulated rbac_service outage")

    monkeypatch.setattr(real_rbac_service, "is_org_admin", _raise)
    org_scoped_stranger = _user(99, "stranger@example.com", admin=True)
    org_scoped_stranger.organization_id = 12345
    solution_with_org = _solution(organization_id=999)

    assert _check_solution_access(solution_with_org, org_scoped_stranger) is False
    assert _check_access(solution_with_org, org_scoped_stranger) is False
