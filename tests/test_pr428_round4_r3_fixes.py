"""Red/green reproductions for PR 428 round 4 (R3-1, R3-5).

R3-1: the same always-truthy "read .is_admin without calling it" bug class
survives in ``getattr(current_user, "is_admin", False)`` form on two write
routes -- ARB solution withdraw and ARB decision reopen -- exploitable by
*any* signed-in member, admin-anywhere or not, which is why both
reproductions below use a plain Viewer/User-role member rather than the
home-org-admin-switched-into-a-victim-org attacker the rest of this PR's
sweep uses: the point is that no admin standing anywhere is needed.
Withdraw is a JSON API (403 on refusal); reopen is a flash/redirect form
handler that returns 302 on both the refused and the pre-fix allowed path,
so its pass/fail signal is the unchanged decision row, not the status code.

R3-5: ``app/decorators/requires_role.py`` always appended the literal
string ``"platform_admin"`` to the allowed-roles list and compared it
against the raw, uncomputed ``enterprise_role`` persona column -- which
defaults to ``"platform_admin"`` for every legacy account -- rather than
judging genuine platform authority via ``is_platform_admin()``. Reproduced
against the exact route the review named,
``GET /compliance/data-subject-requests``.

R3-2/R3-3/R3-4 have no standalone reproduction here: R3-2 is a sweep-fixture
change proven by the mutation test described in the round-4 build report
(scratch-reverted and restored, not a committed test); R3-3 and R3-4 are
structural (decorator placement / single predicate), covered by the
existing sweep and the ``is-admin-called``/reuse checks rather than a new
behavioural test.
"""

from __future__ import annotations

import uuid

import pytest


def _suffix():
    return uuid.uuid4().hex[:10]


def _plain_org_member(db_session, org, *, role_name="User", enterprise_role=None):
    """A user with no admin standing anywhere: no ``is_platform_admin``
    flag, no ``Administrator`` Role, and (when *enterprise_role* is left
    ``None``) no explicit ``enterprise_role`` override -- picking up this
    column's real database default, the same as a never-reassigned legacy
    account, which is the exact shape R3-5's reproduction needs.
    """
    from app.models.user import Role, User

    role = Role.query.filter_by(name=role_name).first()
    if role is None:
        pytest.skip(f"no {role_name!r} role seeded in this database")

    kwargs = dict(
        email=f"r3-plain-{_suffix()}@example.test",
        first_name="Plain",
        last_name="Member",
        organization_id=org.id,
        confirmed=True,
        role=role,
    )
    if enterprise_role is not None:
        kwargs["enterprise_role"] = enterprise_role
    user = User(**kwargs)
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    assert user.is_admin() is False, "fixture setup must NOT grant any admin standing"
    return user


def test_r3_1_arb_withdraw_refuses_a_non_admin_viewer(
    app, db_session, make_org, client, login_as
):
    """The review's own reproduction: a Viewer, not the solution's owner,
    POSTs .../withdraw. ``getattr(current_user, "is_admin", False)`` (no
    call) used to return the bound method -- always truthy -- so this
    returned 200 and the row went to ``governance_status="withdrawn"``.
    Must now return 403 and leave the row untouched.
    """
    from app.models.solution_models import Solution
    from app.models.user import User

    org = make_org("r3-1-withdraw-org")

    owner = User(
        email=f"r3-1-owner-{_suffix()}@example.test",
        first_name="Solution",
        last_name="Owner",
        organization_id=org.id,
        confirmed=True,
    )
    owner.password = uuid.uuid4().hex
    db_session.add(owner)
    db_session.flush()

    viewer = _plain_org_member(db_session, org, role_name="Viewer")

    solution = Solution(
        name="R3-1 withdraw probe solution",
        organization_id=org.id,
        created_by_id=owner.id,
        governance_status="submitted",
    )
    db_session.add(solution)
    db_session.flush()

    login_as(client, viewer)
    response = client.post(
        f"/api/arb-workflow/solutions/{solution.id}/withdraw",
        json={"reason": "probe"},
    )

    assert response.status_code == 403, (
        f"expected 403, got {response.status_code}: {response.get_data(as_text=True)}"
    )
    db_session.refresh(solution)
    assert solution.governance_status == "submitted", (
        "the attacker's withdraw must not have been applied"
    )


def test_r3_1_arb_reopen_refuses_a_non_admin_member(
    app, db_session, make_org, client, login_as
):
    """The review's own reproduction: a non-decision-maker member POSTs
    .../reopen. ``getattr(current_user, "is_admin", False) or
    getattr(current_user, "role", "") == "admin"`` (no call on the first
    branch) used to return 302 and erase the recorded decision
    (``decision=None``, ``status="under_review"``). Must now refuse with
    403/redirect-denied and leave the decision intact.
    """
    from app.models.architecture_review_board import ARBReviewItem
    from app.models.user import User

    org = make_org("r3-1-reopen-org")

    decider = User(
        email=f"r3-1-decider-{_suffix()}@example.test",
        first_name="Decision",
        last_name="Maker",
        organization_id=org.id,
        confirmed=True,
    )
    decider.password = uuid.uuid4().hex
    db_session.add(decider)
    db_session.flush()

    member = _plain_org_member(db_session, org, role_name="User")

    review = ARBReviewItem(
        organization_id=org.id,
        review_number=f"R3-1-{_suffix()}",
        title="R3-1 reopen probe review",
        review_type="design_review",
        submitter_id=decider.id,
        status="approved",
        decision="approved",
        decided_by_id=decider.id,
    )
    db_session.add(review)
    db_session.flush()

    login_as(client, member)
    response = client.post(
        f"/arb/reviews/{review.id}/reopen",
        data={"reopen_reason": "probe"},
        follow_redirects=False,
    )

    # This route is a flash-message/redirect form handler, not a JSON API --
    # it redirects to review_detail on BOTH the refused and the (pre-fix)
    # allowed path, so a 302 status code alone proves nothing (the review's
    # own reproduction measured 302 on the vulnerable code too). The actual
    # pass/fail signal is whether the recorded decision survived.
    assert response.status_code == 302
    db_session.refresh(review)
    assert review.decision == "approved", "the recorded decision must not have been erased"
    assert review.status == "approved"


def test_r3_5_data_subject_requests_refuses_a_default_persona_viewer(
    app, db_session, make_org, client, login_as
):
    """The review's own reproduction: a Viewer-role user whose
    ``enterprise_role`` was never reassigned from its database default
    (which is the literal string ``"platform_admin"`` -- a legacy
    backward-compatibility label, not real platform authority) used to
    reach every route ``requires_role`` guards, GDPR data-subject-request
    routes included, with 200. Must now get 403.
    """
    org = make_org("r3-5-dsr-org")
    viewer = _plain_org_member(db_session, org, role_name="Viewer")
    assert viewer.enterprise_role == "platform_admin", (
        "fixture setup must leave enterprise_role at its real database "
        f"default, got {viewer.enterprise_role!r}"
    )

    login_as(client, viewer)
    response = client.get("/compliance/data-subject-requests")

    assert response.status_code == 403, (
        f"expected 403, got {response.status_code}: {response.get_data(as_text=True)}"
    )


def test_r3_5_may_handle_data_subject_requests_matches_the_route_guard(db_session, make_org):
    """The sidebar/directory helper must agree with the route guard it
    exists to mirror -- otherwise a default-persona user would still see a
    link to a page ``requires_role`` now correctly refuses them.
    """
    from app.decorators.requires_role import may_handle_data_subject_requests

    org = make_org("r3-5-dsr-helper-org")
    viewer = _plain_org_member(db_session, org, role_name="Viewer")
    assert viewer.enterprise_role == "platform_admin"

    assert may_handle_data_subject_requests(viewer) is False


def test_r3_3_prompt_writes_require_platform_admin_not_just_active_org_admin(
    app, db_session, make_org, client, login_as
):
    """R3-3: an org's own Administrator (active-org admin, not a platform
    admin) must still be refused the two prompt-write routes -- the write
    half of the merge conflict review-pr430-v3.md flagged between this PR
    (``_require_admin``, active-org) and the split PR (``platform_admin_
    required`` on the writes). Settled by keeping both: active-org is not
    enough on its own for a table with no organisation column.
    """
    from app.models.user import Role, User

    org = make_org(f"r3-3-prompt-org-{uuid.uuid4().hex[:8]}")
    admin_role = Role.query.filter_by(name="Administrator").first()
    if admin_role is None:
        pytest.skip("no Administrator role seeded in this database")

    org_admin = User(
        email=f"r3-3-org-admin-{_suffix()}@example.test",
        first_name="Org",
        last_name="Admin",
        organization_id=org.id,
        confirmed=True,
        role=admin_role,
    )
    org_admin.password = uuid.uuid4().hex
    db_session.add(org_admin)
    db_session.flush()
    assert org_admin.is_platform_admin is False

    login_as(client, org_admin)

    read_response = client.get("/ai-chat/admin/prompts/data")
    assert read_response.status_code == 200, (
        "an active-org admin must still be able to read persona prompts "
        f"(R2-2/R3-3 unchanged): got {read_response.status_code}"
    )

    write_response = client.post(
        "/ai-chat/admin/prompts/enterprise_architect/update",
        json={"system_prompt": "org-admin probe override"},
    )
    assert write_response.status_code == 403, (
        f"expected 403, got {write_response.status_code}: "
        f"{write_response.get_data(as_text=True)}"
    )
