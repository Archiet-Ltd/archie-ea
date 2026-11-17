"""The solution record: one page, seven tabs, the one score, and role-based actions.

Served through the test client against a fresh organisation. What a person
sees in the browser is proved by tests/smoke/test_solution_record_journey.py.
"""
import uuid

import pytest

from app.modules.solutions_strategic.v2.services import solution_record_service as svc


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


def _user(db_session, org, role, label):
    from app.models.user import User
    user = User(
        email=f"{label}-{uuid.uuid4().hex[:8]}@example.com", first_name=label.title(),
        last_name="Person", organization_id=org.id, confirmed=True, enterprise_role=role,
    )
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture
def org_with_solution(app, db_session, make_org):
    from app.models.solution_models import Solution
    org = make_org("record")
    author = _user(db_session, org, "solution_architect", "author")
    member = _user(db_session, org, "arb_member", "member")
    solution = Solution(
        name=f"Record Fixture {uuid.uuid4().hex[:6]}", organization_id=org.id,
        created_by_id=author.id, description="Orders are keyed twice because two systems hold them.",
        solution_owner="Ivo Reed", technical_lead="Head of Engineering", governance_status="draft",
    )
    db_session.add(solution)
    db_session.flush()
    return app.test_client(), org, author, member, solution, db_session


def test_tab_names_and_old_names_land_on_a_tab():
    assert svc.tab_for(None) == "overview"
    assert svc.tab_for("Design") == "design"
    assert svc.tab_for("governance") == "review"
    assert svc.tab_for("conformance") == "review"
    assert svc.tab_for("completeness") == "overview"
    assert svc.tab_for("nonsense") == "overview"


def test_every_open_check_links_to_the_tab_that_fixes_it(org_with_solution):
    client, org, author, member, solution, session = org_with_solution
    _login(client, author.id)
    ctx = svc.build(solution, tab="overview", user=author)
    for check in ctx["score"]["checks"]:
        tab = svc.FIX_TABS[check["key"]]
        expected = "/solutions/%d/edit" % solution.id if tab == "edit" else svc.record_url(solution.id, tab)
        assert check["fix_href"] == expected
    assert ctx["score"]["label"].endswith("of 10")
    # the next step is the first open check
    first_open = next(c for c in ctx["score"]["checks"] if not c["passed"])
    assert ctx["next_step"]["label"] == first_open["label"]
    assert ctx["next_step"]["href"] == first_open["fix_href"]


def test_record_renders_every_tab_with_content_or_a_next_step(org_with_solution):
    client, org, author, member, solution, session = org_with_solution
    _login(client, author.id)
    for tab in ("overview", "design", "requirements", "options", "risks", "review", "delivery"):
        resp = client.get(f"/solutions/{solution.id}?tab={tab}")
        assert resp.status_code == 200, (tab, resp.status_code)
        html = resp.get_data(as_text=True)
        assert f'data-tab="{tab}"' in html
        assert "bp-governance-panel" not in html  # the fifteen-section page is gone
    html = client.get(f"/solutions/{solution.id}").get_data(as_text=True)
    assert "Orders are keyed twice" in html
    assert 'data-testid="record-score"' in html
    assert 'data-testid="record-next-step"' in html
    assert 'data-testid="record-edit"' in html
    assert 'data-testid="record-submit"' in html  # the author may submit a draft
    assert 'data-testid="record-decide"' not in html  # and never decides
    assert 'data-testid="record-ask"' in html and "/ai-chat?q=" in html
    design = client.get(f"/solutions/{solution.id}?tab=design").get_data(as_text=True)
    assert 'data-testid="design-empty"' in design and "Link elements" in design


def test_old_addresses_land_on_the_matching_tab(org_with_solution):
    client, org, author, member, solution, session = org_with_solution
    _login(client, author.id)
    for path, tab in (("/review-packet", "review"), ("/conformance", "review"), ("/completeness", "overview")):
        resp = client.get(f"/solutions/{solution.id}{path}")
        assert resp.status_code == 302, (path, resp.status_code)
        assert resp.headers["Location"].endswith(f"/solutions/{solution.id}?tab={tab}"), (path, resp.headers["Location"])


def test_a_board_member_sees_decide_on_an_open_review_and_the_author_does_not(org_with_solution):
    client, org, author, member, solution, session = org_with_solution
    from app.models.architecture_review_board import ARBReviewItem
    solution.governance_status = "arb_review"
    review = ARBReviewItem(
        review_number=f"REV-T-{uuid.uuid4().hex[:6]}", title=solution.name, review_type="solution_design",
        solution_id=solution.id, organization_id=org.id, status="submitted", submitter_id=author.id,
    )
    session.add(review)
    session.flush()

    member_actions = svc.actions_for(solution, member, review)
    assert member_actions["can_decide"] is True
    assert member_actions["decide_href"].endswith(f"/arb/reviews/{review.id}")
    assert member_actions["can_submit"] is False

    author_actions = svc.actions_for(solution, author, review)
    assert author_actions["can_decide"] is False
    assert author_actions["is_author"] is True

    _login(client, member.id)
    html = client.get(f"/solutions/{solution.id}?tab=review").get_data(as_text=True)
    assert 'data-testid="review-decide"' in html
    assert 'data-testid="review-history"' in html and review.review_number in html


def test_another_organisation_never_opens_the_record(app, db_session, make_org, org_with_solution):
    client, org, author, member, solution, session = org_with_solution
    other_org = make_org("other")
    outsider = _user(db_session, other_org, "solution_architect", "outsider")
    _login(client, outsider.id)
    resp = client.get(f"/solutions/{solution.id}")
    assert resp.status_code in (403, 404)
    resp = client.get(f"/solutions/{solution.id}?tab=review")
    assert resp.status_code in (403, 404)
