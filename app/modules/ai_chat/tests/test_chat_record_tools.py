"""The chat creates and changes goals and risks of the organisation, and says what it did in plain words.

Each change is checked, reviewed and applied in the same turn. The reply starts "Done:" with the address of the
record, or "Not applied:" with a reason. A Viewer is refused plainly, and a name that two records share is asked
about before anything is written.
"""

import uuid
from datetime import date


from tests.test_change_review import PASS_ALL, FakeReviewer, _user


def _ask(user, tool, arguments, words="Please do it"):
    from app.modules.ai_chat.services.agent_runner import AgentRunner
    from app.modules.ai_chat.services.change_review import ReviewBudget
    from app.modules.ai_chat.tools.executor import ToolCall

    runner = AgentRunner(user_id=user.id, chat_session_id="records-%s" % uuid.uuid4().hex[:6])
    runner._turn_id = "turn-1"
    runner._review_budget = ReviewBudget()
    runner._drafter = ("deepseek", "deepseek-flash", words)
    approval_id = runner._queue_approval(ToolCall(id="1", name=tool, arguments=arguments))
    return approval_id, runner._verdicts.get(approval_id)


def _viewer(db_session, org_id):
    from app.models.org_role import OrgRole

    user = _user(db_session, org_id)
    db_session.add(OrgRole(organization_id=org_id, user_id=user.id, role="viewer"))
    db_session.flush()
    return user


def _goals_named(org_id, name):
    from app import db
    from app.models.motivation import Goal

    db.session.expire_all()
    return db.session.execute(db.select(Goal).where(Goal.organization_id == org_id, Goal.name == name)).scalars().all()


def _tag():
    return uuid.uuid4().hex[:6]


def test_a_goal_with_a_date_is_created_and_the_reply_links_its_page(db_session, make_org, tenant_ctx, monkeypatch):
    org = make_org("goal")
    user = _user(db_session, org.id)
    name = "Enter the German market %s" % _tag()
    FakeReviewer([PASS_ALL]).install(monkeypatch)
    with tenant_ctx(org.id):
        _id, verdict = _ask(user, "create_goal", {"name": name, "target_date": "2027-06-30"})
        assert verdict["state"] == "applied", verdict
        assert verdict["message"].startswith('Done: created Goal "%s"' % name), verdict["message"]
        assert "30 Jun 2027" in verdict["message"]
        goal = _goals_named(org.id, name)[0]
        assert goal.target_date == date(2027, 6, 30)
        assert str(goal.archimate_element_id) in verdict["record_url"]
        assert verdict["record_url"] != verdict["review_url"]


def test_changing_the_date_says_changed_and_keeps_one_goal(db_session, make_org, tenant_ctx, monkeypatch):
    org = make_org("move")
    user = _user(db_session, org.id)
    name = "Move the date %s" % _tag()
    FakeReviewer([PASS_ALL, PASS_ALL]).install(monkeypatch)
    with tenant_ctx(org.id):
        _ask(user, "create_goal", {"name": name, "target_date": "2027-06-30"})
        _id, verdict = _ask(user, "update_goal", {"goal_name": name, "target_date": "2027-09-30"})
        assert verdict["message"].startswith("Done: changed Goal"), verdict
        assert "30 Sep 2027" in verdict["message"]
        goals = _goals_named(org.id, name)
        assert len(goals) == 1 and goals[0].target_date == date(2027, 9, 30)


def test_a_risk_is_created_with_a_level_and_its_likelihood_lowered(db_session, make_org, tenant_ctx, monkeypatch):
    from app.models.risk import Risk
    from app import db

    org = make_org("risk")
    user = _user(db_session, org.id)
    title = "Payments vendor may exit %s" % _tag()
    FakeReviewer([PASS_ALL, PASS_ALL]).install(monkeypatch)
    with tenant_ctx(org.id):
        _id, made = _ask(user, "create_risk", {"title": title, "likelihood": 4, "impact": 5})
        assert made["message"].startswith('Done: created Risk "%s"' % title), made
        risk = db.session.execute(db.select(Risk).where(Risk.organization_id == org.id, Risk.title == title)
                                  ).scalar_one()
        assert made["record_url"] == "/risks/%d" % risk.id
        assert (risk.likelihood, risk.impact) == (4, 5)
        _id, changed = _ask(user, "update_risk", {"risk_title": title, "likelihood": 3})
        assert changed["message"].startswith("Done: changed Risk"), changed
        db.session.expire_all()
        assert db.session.get(Risk, risk.id).likelihood == 3


def test_a_viewer_is_told_plainly_and_no_goal_is_written(db_session, make_org, tenant_ctx, monkeypatch):
    org = make_org("viewer")
    viewer = _viewer(db_session, org.id)
    name = "Cut costs %s" % _tag()
    reviewer = FakeReviewer([PASS_ALL]).install(monkeypatch)
    with tenant_ctx(org.id):
        _id, verdict = _ask(viewer, "create_goal", {"name": name})
        assert verdict["state"] == "declined"
        assert verdict["message"] == (
            "Not applied: Your role in %s is Viewer. Viewers can read records but not change them. An "
            "administrator can change your role on the members page." % org.name), verdict["message"]
        assert _goals_named(org.id, name) == []
    assert reviewer.review_prompts == []


def test_a_name_two_goals_share_is_asked_about_and_nothing_is_written(db_session, make_org, tenant_ctx):
    from app.modules.ai_chat.services import change_review
    from app.services import goal_store

    org = make_org("two")
    name = "Grow revenue %s" % _tag()
    with tenant_ctx(org.id):
        goal_store.create_goal(org.id, name)
        goal_store.create_goal(org.id, name)
        for tool, args in (("update_goal", {"goal_name": name, "target_date": "2027-01-01"}),
                           ("create_goal", {"name": name})):
            question = change_review.preflight(tool, args, org.id)
            assert question and "There are 2 goals called" in question and name in question
        assert len(_goals_named(org.id, name)) == 2


def test_org_b_cannot_name_org_a_goal(db_session, make_org, tenant_ctx, monkeypatch):
    from app.modules.ai_chat.services import change_review
    from app.services import goal_store

    org_a, org_b = make_org("a"), make_org("b")
    user_b = _user(db_session, org_b.id)
    name = "Grow revenue %s" % _tag()
    with tenant_ctx(org_a.id):
        goal_store.create_goal(org_a.id, name, target_date="2027-06-30")
    FakeReviewer([PASS_ALL]).install(monkeypatch)
    with tenant_ctx(org_b.id):
        _id, verdict = _ask(user_b, "update_goal", {"goal_name": name, "target_date": "2030-01-01"})
        assert verdict["state"] == "declined"
        assert verdict["message"] == 'Not applied: There is no goal named "%s" in this workspace.' % name
        assert change_review.preflight("update_goal", {"goal_name": name}, org_b.id) is None
    with tenant_ctx(org_a.id):
        assert _goals_named(org_a.id, name)[0].target_date == date(2027, 6, 30)


def test_resolvers_filter_by_organisation_without_request_context(app, db_session, make_org):
    """The chat's worker has no request: the organisation is passed, and another one's rows never resolve."""
    from flask import g

    from app.models.archimate_core import ArchiMateElement
    from app.models.risk import Risk
    from app.modules.ai_chat.tools.resolver import EntityResolver
    from app.services import goal_store

    org_a, org_b = make_org("ra"), make_org("rb")
    tag = _tag()
    goal_store.create_goal(org_a.id, "Resolver goal %s" % tag)
    db_session.add(Risk(title="Resolver risk %s" % tag, likelihood=2, impact=2, organization_id=org_a.id))
    db_session.add(ArchiMateElement(name="Resolver thing %s" % tag, type="Node", layer="Technology",
                                    organization_id=org_a.id))
    db_session.flush()
    with app.app_context():
        assert not hasattr(g, "current_org_id") or g.current_org_id is None
        for resolve, name in ((EntityResolver.resolve_goal, "Resolver goal %s" % tag),
                              (EntityResolver.resolve_risk, "Resolver risk %s" % tag),
                              (EntityResolver.resolve_archimate_element, "Resolver thing %s" % tag)):
            assert resolve(name, org_a.id)["resolved"] is True, name
            other = resolve(name, org_b.id)
            assert other["resolved"] is False and other["candidates"] == [], name
            assert resolve(name)["resolved"] is False, "no organisation resolves nothing: %s" % name


def test_every_record_tool_declares_its_permission_pair_and_kind():
    from app.core.auth.permissions import ACTIONS, OBJECT_TYPES
    from app.modules.ai_chat.tools.registry import TOOL_SCHEMA_BY_NAME

    expected = {"create_goal": ("create", "goal"), "update_goal": ("edit", "goal"),
                "create_risk": ("create", "risk"), "update_risk": ("edit", "risk")}
    for name, (action, kind) in expected.items():
        schema = TOOL_SCHEMA_BY_NAME[name]
        assert schema["record_kind"] == kind and schema["mutates"] is True
        assert schema["permission"][0] == action and schema["permission"][0] in ACTIONS
        assert schema["permission"][1] in OBJECT_TYPES
    assert "solution_id" in TOOL_SCHEMA_BY_NAME["create_goal"]["parameters"]["properties"]
    assert "owner" not in TOOL_SCHEMA_BY_NAME["update_risk"]["description"]


def test_the_review_reads_goal_and_risk_targets_from_the_organisation(db_session, make_org, tenant_ctx):
    from app.modules.ai_chat.services.change_review import _TOOL_TARGETS
    from app.services import goal_store

    org = make_org("targets")
    name = "Target goal %s" % _tag()
    with tenant_ctx(org.id):
        goal_store.create_goal(org.id, name, target_date="2027-06-30")
        target = _TOOL_TARGETS["update_goal"]({"goal_name": name, "target_date": "2027-09-30"}, org.id)
        assert target.ok and target.before["target_date"] == "2027-06-30"
        assert _TOOL_TARGETS["update_goal"]({"goal_name": "No such goal"}, org.id).ok is False
        assert _TOOL_TARGETS["create_goal"]({"name": name}, org.id).value_reason.startswith("A goal called")
        assert _TOOL_TARGETS["create_goal"]({"name": "x", "target_date": "soon"}, org.id).value_reason
        assert _TOOL_TARGETS["update_risk"]({"risk_title": "No such risk"}, org.id).ok is False


def test_a_failed_save_says_nothing_was_changed(db_session, make_org, tenant_ctx, monkeypatch):
    from app.modules.ai_chat.tools.executor import ToolCall, ToolExecutor
    from app.services import goal_store

    org = make_org("fail")
    user = _user(db_session, org.id)

    def boom(*args, **kwargs):
        raise RuntimeError("database says no: secret detail")

    monkeypatch.setattr(goal_store, "create_goal", boom)
    with tenant_ctx(org.id):
        result = ToolExecutor(user.id).execute(ToolCall(id="9", name="create_goal", arguments={"name": "Anything"}))
    assert result == {"success": False, "error": "The goal could not be saved, so nothing was changed."}


def _solution(db_session, org_id, name):
    from app.models.solution_models import Solution

    solution = Solution(name=name, organization_id=org_id)
    db_session.add(solution)
    db_session.flush()
    return solution


def test_a_goal_asked_for_on_a_solution_lands_on_that_solution(db_session, make_org, tenant_ctx, monkeypatch):
    from app import db
    from app.models.solution_architect_models import SolutionGoal, SolutionProblemDefinition

    org = make_org("sg")
    user = _user(db_session, org.id)
    solution = _solution(db_session, org.id, "Payments rebuild %s" % _tag())
    name = "Cut settlement time %s" % _tag()
    FakeReviewer([PASS_ALL]).install(monkeypatch)
    with tenant_ctx(org.id):
        _id, verdict = _ask(user, "create_goal", {"name": name, "solution_id": solution.id})
        assert verdict["state"] == "applied", verdict
        assert 'on solution "%s"' % solution.name in verdict["message"], verdict["message"]
        assert verdict["record_url"] and verdict["record_url"] != verdict["review_url"]
        db.session.expire_all()
        row = db.session.execute(db.select(SolutionGoal).where(SolutionGoal.name == name)).scalar_one()
        problem = db.session.get(SolutionProblemDefinition, row.problem_id)
        assert problem.session.name == "Agent session — solution %d" % solution.id
        assert _goals_named(org.id, name) == [], "it is the solution's goal, not an organisation goal"


def test_a_goal_cannot_be_put_on_another_organisations_solution(db_session, make_org, tenant_ctx, monkeypatch):
    from app import db
    from app.models.solution_architect_models import SolutionGoal

    org_a, org_b = make_org("sa"), make_org("sb")
    user_b = _user(db_session, org_b.id)
    solution_a = _solution(db_session, org_a.id, "Org A programme %s" % _tag())
    name = "Sneaky goal %s" % _tag()
    FakeReviewer([PASS_ALL]).install(monkeypatch)
    with tenant_ctx(org_b.id):
        _id, verdict = _ask(user_b, "create_goal", {"name": name, "solution_id": solution_a.id})
        assert verdict["state"] == "declined", verdict
        assert db.session.execute(db.select(SolutionGoal).where(SolutionGoal.name == name)).first() is None


def test_a_risk_asked_for_on_a_solution_keeps_the_solution(db_session, make_org, tenant_ctx, monkeypatch):
    from app import db
    from app.models.risk import Risk

    org = make_org("sr")
    user = _user(db_session, org.id)
    solution = _solution(db_session, org.id, "Risk programme %s" % _tag())
    title = "Vendor may exit %s" % _tag()
    FakeReviewer([PASS_ALL]).install(monkeypatch)
    with tenant_ctx(org.id):
        _id, verdict = _ask(user, "create_risk", {"title": title, "likelihood": 3, "impact": 4,
                                                  "solution_id": solution.id})
        assert verdict["state"] == "applied", verdict
        db.session.expire_all()
        risk = db.session.execute(db.select(Risk).where(Risk.organization_id == org.id, Risk.title == title)).scalar_one()
        assert risk.solution_id == solution.id


def test_the_role_check_is_listed_once(db_session, make_org, tenant_ctx, monkeypatch):
    org = make_org("once")
    user = _user(db_session, org.id)
    FakeReviewer([PASS_ALL]).install(monkeypatch)
    with tenant_ctx(org.id):
        approval_id, verdict = _ask(user, "create_goal", {"name": "Listed once %s" % _tag()})
        assert verdict["state"] == "applied", verdict
        from app import db
        from app.models.ai_chat_crud_approval import AIChatCRUDApproval
        from app.modules.ai_chat.services import change_review

        row = db.session.get(AIChatCRUDApproval, approval_id)
        names = [c["name"] for c in change_review._state(row)["checks"]]
        assert names.count("The person who asked may make it") == 1, names


def test_a_percent_or_underscore_in_a_name_matches_only_itself(db_session, make_org):
    from app.models.risk import Risk
    from app.modules.ai_chat.tools.resolver import EntityResolver
    from app.services import goal_store

    org = make_org("like")
    tag = _tag()
    goal_store.create_goal(org.id, "Grow 100%% %s" % tag)
    goal_store.create_goal(org.id, "Grow 1000 now %s" % tag)
    db_session.add(Risk(title="Lost_data %s" % tag, likelihood=2, impact=2, organization_id=org.id))
    db_session.add(Risk(title="LostXdata %s" % tag, likelihood=2, impact=2, organization_id=org.id))
    db_session.flush()
    assert EntityResolver.resolve_goal("Grow 100%% %s" % tag, org.id)["resolved"] is True
    assert EntityResolver.resolve_goal("Grow 100%", org.id)["resolved"] is True  # only one name contains "100%"
    assert EntityResolver.resolve_risk("Lost_data %s" % tag, org.id)["resolved"] is True
    assert EntityResolver.resolve_risk("Lost_data", org.id)["resolved"] is True  # "LostXdata" is not matched
