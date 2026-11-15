"""A Contributor keeps a goal and a risk current by asking the chat, and a Viewer is told they cannot.

Organisation A holds the goal "Grow revenue" related to the capability "Payments", a Contributor and a Viewer;
organisation B holds its own "Grow revenue". The server is set up as production is: DeepSeek only, as a stub on
a loopback port, drafting with deepseek-flash and reviewing with deepseek-v4-pro.

The Contributor asks for a goal with a target date, moves the date, records a risk with a likelihood and impact,
and lowers its likelihood. Each reply starts "Done:" and its link opens the record showing the change. The Viewer
asks for a goal and is told, in words, that their role does not allow it; no goal is written. A request naming a
goal that two share is answered with one question and writes nothing.
"""
import json
import os
import pathlib
import re
import uuid

import pytest
from playwright.sync_api import expect

from tests.deepseek_stub import KEY, DeepSeekStub

from .conftest import PAGE_TIMEOUT, PASSWORD, boot_live_server

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

OTHERS = ("OPENAI", "ANTHROPIC", "AZURE", "GEMINI", "GOOGLE", "NVIDIA", "OPENROUTER", "HUGGINGFACE", "CLAUDE")
CHAT_REPLY = "I asked for that change; the result is shown below."


def _respond(model, text, has_tools):
    if model == "deepseek-v4-pro" and not has_tools:
        return json.dumps({"criteria": [
            {"id": n, "passed": True, "reason": "Criterion %d holds for this change." % n} for n in range(1, 7)
        ]})
    return None


@pytest.fixture(scope="module")
def stub():
    s = DeepSeekStub(reply=CHAT_REPLY, respond=_respond)
    yield s
    s.close()


@pytest.fixture(scope="module")
def deepseek_server(request, app, stub):
    env = {name + "_API_KEY": "" for name in OTHERS}
    env.update(
        LLM_PROVIDER_ORDER="deepseek", DEEPSEEK_API_KEY=KEY, DEEPSEEK_API_BASE=stub.base_url,
        LLM_SPEND_CEILING_USD_DEEPSEEK="62", NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost",
    )
    return boot_live_server(request, None, app, extra_env=env)


def _world():
    """Organisation A (a Contributor, a Viewer, "Grow revenue" related to "Payments") and organisation B."""
    from app import create_app, db

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:8]
    with app.app_context():
        from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
        from app.models.org_role import OrgRole
        from app.models.organization import Organization
        from app.models.user import Role, User
        from app.services import goal_store
        from app.services.billing_plans import set_contract_plan

        Role.insert_roles()
        architect = Role.query.filter_by(name="Architect").one()

        def organisation(label):
            org = Organization(name="%s %s" % (label, suffix), slug="%s-%s" % (label.lower(), suffix))
            db.session.add(org)
            db.session.flush()
            set_contract_plan(org, "enterprise", None)
            return org

        def person(org, first, role_in_org):
            user = User(email="%s-%s@example.com" % (first.lower(), suffix), first_name=first, last_name="Tester",
                        organization_id=org.id, enterprise_role="enterprise_architect", confirmed=True)
            user.role = architect
            user.password = PASSWORD
            db.session.add(user)
            db.session.flush()
            db.session.add(OrgRole(organization_id=org.id, user_id=user.id, role=role_in_org))
            return user

        org_a, org_b = organisation("Alpha"), organisation("Beta")
        dana, vic = person(org_a, "Dana", "architect"), person(org_a, "Vic", "viewer")
        person(org_b, "Bea", "architect")
        grow_a = goal_store.create_goal(org_a.id, "Grow revenue")
        goal_store.create_goal(org_b.id, "Grow revenue", target_date="2030-01-01")
        payments = ArchiMateElement(name="Payments", type="Capability", layer="Strategy", organization_id=org_a.id)
        db.session.add(payments)
        db.session.flush()
        db.session.add(ArchiMateRelationship(type="Association", source_id=grow_a.archimate_element_id,
                                             target_id=payments.id, organization_id=org_a.id))
        db.session.commit()
        out = {"org_a": org_a.id, "org_b": org_b.id, "org_a_name": org_a.name, "dana": dana.email,
               "vic": vic.email, "grow_element": grow_a.archimate_element_id}
        db.session.remove()
        return out


def _goals(org_id, name):
    from app import create_app, db

    app = create_app("testing")
    with app.app_context():
        from app.models.motivation import Goal

        rows = [(g.id, g.archimate_element_id, g.target_date.isoformat() if g.target_date else None)
                for g in db.session.query(Goal).execution_options(include_all_tenants=True)
                .filter(Goal.organization_id == org_id, Goal.name == name).order_by(Goal.id)]
        db.session.remove()
        return rows


def _add_second_goal(org_id, name):
    from app import create_app, db

    app = create_app("testing")
    with app.app_context():
        from app.services import goal_store

        goal_store.create_goal(org_id, name)
        db.session.remove()


@pytest.fixture
def shots(tmp_path):
    folder = pathlib.Path(os.environ.get("SMOKE_SCREENSHOT_DIR") or tmp_path)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _shot(page, folder, name):
    page.screenshot(path=str(pathlib.Path(folder) / ("%s-%d.png" % (name, page.viewport_size["width"]))),
                    full_page=True)


def _sign_in(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


def _settled_count(page):
    """The number of messages on screen once the saved conversation has finished loading."""
    replies = page.locator("#messages-container .message-content")
    seen = -1
    for _ in range(40):
        now = replies.count()
        if now == seen:
            return now
        seen = now
        page.wait_for_timeout(1500)
    return seen


def _ask(page, stub, call, words):
    """Send one request to the chat; the stub's drafting model answers with `call`. Returns the new reply."""
    box = page.locator("#user-input")
    expect(box).to_be_visible(timeout=PAGE_TIMEOUT)
    replies = page.locator("#messages-container .message-content")
    before = _settled_count(page)
    stub.tool_script = [call] if call else []
    box.fill(words)
    box.press("Enter")
    expect(replies).to_have_count(before + 2, timeout=PAGE_TIMEOUT * 2)  # the person's words, then the reply
    reply = replies.last
    expect(reply).not_to_have_text("", timeout=PAGE_TIMEOUT)
    return reply


def _open_link(page, reply, label, url_pattern):
    with page.expect_navigation(timeout=PAGE_TIMEOUT):
        reply.get_by_role("link", name=label).click()
    page.wait_for_load_state("domcontentloaded")
    assert re.search(url_pattern, page.url), page.url


def _shows(page, label, value):
    """The risk page's fact `label` reads `value of 5`."""
    return re.search(r"%s\s+%d of 5" % (label, value), page.locator("body").inner_text(), re.I) is not None


def _back_to_chat(page, base):
    page.goto(base + "/ai-chat", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)


@pytest.mark.parametrize("width,scheme", [(1440, "light"), (390, "dark")])
def test_a_contributor_keeps_a_goal_and_a_risk_current_and_a_viewer_is_told_they_cannot(
    browser, deepseek_server, stub, shots, width, scheme
):
    base = deepseek_server
    world = _world()
    goal_name, risk_title = "Enter the German market", "Payments vendor may exit"
    context = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme)
    context.set_default_timeout(PAGE_TIMEOUT)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    page = context.new_page()
    try:
        # Step 1: Dana adds a goal with a target date; the reply links the goal's page, which shows the date.
        _sign_in(page, base, world["dana"])
        _back_to_chat(page, base)
        reply = _ask(page, stub, ("create_goal", {"name": goal_name, "target_date": "2027-06-30"}),
                     "Add a goal Enter the German market with a target date of 30 June 2027")
        expect(reply).to_contain_text('Done: created Goal "%s"' % goal_name)
        assert reply.inner_text().strip().startswith('Done: created Goal "%s"' % goal_name), reply.inner_text()
        assert "Applied:" not in reply.inner_text()
        _shot(page, shots, "step1-reply")
        made = _goals(world["org_a"], goal_name)
        assert len(made) == 1 and made[0][2] == "2027-06-30", made
        _open_link(page, reply, "Open it", r"/architecture/motivation/Goal/%d" % made[0][1])
        expect(page.get_by_test_id("goal-facts")).to_contain_text("Target date")
        expect(page.get_by_test_id("property-target-date")).to_have_text("30 Jun 2027")
        expect(page.get_by_text(goal_name).locator("visible=true").first).to_be_visible()
        _shot(page, shots, "step1-goal-page")

        # Step 2: the date moves; the same page shows the new date.
        _back_to_chat(page, base)
        reply = _ask(page, stub, ("update_goal", {"goal_name": goal_name, "target_date": "2027-09-30"}),
                     "Move its target date to 30 September 2027")
        assert reply.inner_text().strip().startswith("Done: changed Goal"), reply.inner_text()
        _shot(page, shots, "step2-reply")
        assert [g[2] for g in _goals(world["org_a"], goal_name)] == ["2027-09-30"]
        _open_link(page, reply, "Open it", r"/architecture/motivation/Goal/%d" % made[0][1])
        expect(page.get_by_test_id("property-target-date")).to_have_text("30 Sep 2027")
        assert "30 Jun 2027" not in page.get_by_test_id("goal-facts").inner_text()
        _shot(page, shots, "step2-goal-page")

        # Step 3: a risk with a likelihood and an impact; the link opens it.
        _back_to_chat(page, base)
        reply = _ask(page, stub, ("create_risk", {"title": risk_title, "likelihood": 4, "impact": 5}),
                     "Record a risk that our payments vendor may exit, likelihood 4, impact 5")
        assert reply.inner_text().strip().startswith('Done: created Risk "%s"' % risk_title), reply.inner_text()
        _shot(page, shots, "step3-reply")
        _open_link(page, reply, "Open it", r"/risks/\d+")
        risk_url = page.url
        expect(page.locator("body")).to_contain_text(risk_title)
        assert _shows(page, "Likelihood", 4), "likelihood 4 not shown"
        assert _shows(page, "Impact", 5), "impact 5 not shown"
        expect(page.get_by_test_id("risk-score")).to_have_text("Inherent 20 of 25")
        _shot(page, shots, "step3-risk-page")

        # Step 4: the likelihood is lowered; the risk page shows 3.
        _back_to_chat(page, base)
        reply = _ask(page, stub, ("update_risk", {"risk_title": risk_title, "likelihood": 3}),
                     "Lower that risk's likelihood to 3")
        assert reply.inner_text().strip().startswith("Done: changed Risk"), reply.inner_text()
        _shot(page, shots, "step4-reply")
        _open_link(page, reply, "Open it", re.escape(risk_url.split("//", 1)[1].split("/", 1)[1]))
        assert _shows(page, "Likelihood", 3), "likelihood 3 not shown"
        expect(page.get_by_test_id("risk-score")).to_have_text("Inherent 15 of 25")
        _shot(page, shots, "step4-risk-page")

        # Step 8: two goals share a name, so the chat asks one question and writes nothing.
        _add_second_goal(world["org_a"], "Grow revenue")
        _back_to_chat(page, base)
        reply = _ask(page, stub, ("create_goal", {"name": "Grow revenue"}), "Add a goal Grow revenue")
        text = reply.inner_text()
        assert "There are 2 goals called 'Grow revenue'" in text and "Which one do you mean?" in text, text
        assert len(_goals(world["org_a"], "Grow revenue")) == 2
        _shot(page, shots, "step8-question")

        # Organisation B's "Grow revenue" is untouched by all of it.
        assert [g[2] for g in _goals(world["org_b"], "Grow revenue")] == ["2030-01-01"]
        page.context.clear_cookies()

        # Step 7: Vic, a Viewer, is told plainly and no goal is written.
        _sign_in(page, base, world["vic"])
        _back_to_chat(page, base)
        reply = _ask(page, stub, ("create_goal", {"name": "Cut costs"}), "Add a goal Cut costs")
        expected = ("Not applied: Your role in %s is Viewer. Viewers can read records but not change them. "
                    "An administrator can change your role on the members page." % world["org_a_name"])
        assert reply.inner_text().strip().startswith(expected), reply.inner_text()
        assert _goals(world["org_a"], "Cut costs") == []
        _shot(page, shots, "step7-viewer-refused")
    finally:
        context.close()
