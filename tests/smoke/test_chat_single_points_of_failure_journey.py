"""Asked what breaks if the most connected application fails, the chat names applications only,
ranked by how many things depend on them; asked about single points of failure with no type, it
lists every type and says which each is.

The person: an enterprise architect in an organisation whose data object has the most dependents,
an application the second most, with a service and a second application behind them. Another
organisation has a hub of its own that must never appear.

The goal, at 1440 light and at 390 dark:
- "What breaks if our most connected application fails?" lists applications only, the most depended-on
  first, each with its dependents count and a source that opens a page with content;
- "What are our single points of failure?" still lists every type, the data object first, each row
  naming its type;
- the other organisation sees only its own names.
"""
import os

import pytest
from playwright.sync_api import expect

from tests.deepseek_stub import KEY, DeepSeekStub

from .chat_ask import ask, sign_in
from .conftest import PAGE_TIMEOUT, boot_live_server
from .fresh_org import create_fresh_org

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK = os.environ.get("CHAT_ANSWER_WALK_DIR")
TYPED_QUESTION = "What breaks if our most connected application fails?"
UNTYPED_QUESTION = "What are our single points of failure?"
OTHER_PROVIDERS = ("OPENAI", "ANTHROPIC", "AZURE", "GEMINI", "GOOGLE", "NVIDIA", "OPENROUTER", "HUGGINGFACE", "CLAUDE")
DATA_OBJECT = "Customer Ledger"
APPLICATIONS = ("Payments Core", "Statement Runner")
SERVICE = "Quote Service"
FOREIGN = "Harbour Hub"


@pytest.fixture(scope="module")
def stub():
    s = DeepSeekStub(reply="Nothing in your records is called that.")
    yield s
    s.close()


@pytest.fixture(scope="module")
def server(request, app, stub):
    env = {name + "_API_KEY": "" for name in OTHER_PROVIDERS}
    env.update(
        LLM_PROVIDER_ORDER="deepseek", DEEPSEEK_API_KEY=KEY, DEEPSEEK_API_BASE=stub.base_url,
        LLM_SPEND_CEILING_USD_DEEPSEEK="62", NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost",
    )
    return boot_live_server(request, None, app, extra_env=env)


def _seed(org_id, other_org_id):
    """Dependents: data object 4, Payments Core 3, Quote Service 2, Statement Runner 2."""
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.organization import Organization
    from app.services.billing_plans import set_contract_plan

    app = create_app("testing")
    with app.app_context():
        set_contract_plan(db.session.get(Organization, org_id), "enterprise", None)

        def element(name, kind, org):
            e = ArchiMateElement(name=name, type=kind, layer="application", organization_id=org)
            db.session.add(e)
            db.session.flush()
            return e

        def depend(callers, target, org):
            for caller in callers:
                db.session.add(ArchiMateRelationship(
                    type="Serving", source_id=caller.id, target_id=target.id, organization_id=org,
                ))

        ledger = element(DATA_OBJECT, "DataObject", org_id)
        payments = element(APPLICATIONS[0], "ApplicationComponent", org_id)
        runner = element(APPLICATIONS[1], "ApplicationComponent", org_id)
        quote = element(SERVICE, "ApplicationService", org_id)
        callers = [element("Caller %d" % i, "ApplicationComponent", org_id) for i in range(4)]
        depend(callers, ledger, org_id)
        depend(callers[:3], payments, org_id)
        depend(callers[:2], quote, org_id)
        depend(callers[:2], runner, org_id)
        foreign = element(FOREIGN, "ApplicationComponent", other_org_id)
        depend([element("Harbour Caller %d" % i, "ApplicationComponent", other_org_id) for i in range(2)], foreign, other_org_id)
        db.session.commit()
        db.session.remove()


@pytest.fixture(scope="module")
def organisation(server):
    made = create_fresh_org("enterprise_architect", org_admin=True)
    other = create_fresh_org("enterprise_architect", org_admin=True)
    made["other"] = other
    _seed(made["org_id"], other["org_id"])
    return made


def _shot(page, tmp_path, name):
    folder = WALK or str(tmp_path)
    os.makedirs(folder, exist_ok=True)
    page.screenshot(path=os.path.join(folder, name))


def _new_page(browser, viewport, scheme):
    context = browser.new_context(viewport=viewport, color_scheme=scheme)
    context.set_default_timeout(PAGE_TIMEOUT)
    return context, context.new_page()


CASES = (
    ("desktop-light", {"width": 1440, "height": 900}, "light"),
    ("mobile-dark", {"width": 390, "height": 844}, "dark"),
)


@pytest.mark.parametrize("label,viewport,scheme", CASES, ids=[c[0] for c in CASES])
def test_the_most_connected_application_question_lists_applications_only_with_their_source(
    browser, server, organisation, tmp_path, label, viewport, scheme
):
    context, page = _new_page(browser, viewport, scheme)
    try:
        sign_in(page, server, organisation["emails"]["enterprise_architect"])
        answer = ask(page, server, TYPED_QUESTION)
        text = answer.inner_text()
        for name in APPLICATIONS:
            assert name in text, text
        assert DATA_OBJECT not in text and SERVICE not in text and FOREIGN not in text, text
        assert text.index(APPLICATIONS[0]) < text.index(APPLICATIONS[1]), "the most depended-on application comes first"
        assert "3" in text.split(APPLICATIONS[0], 1)[1].split(APPLICATIONS[1], 1)[0], text
        _shot(page, tmp_path, "chat-most-connected-application-%s.png" % label)

        # The first row's source opens a page that shows that application.
        link = answer.get_by_role("link", name=APPLICATIONS[0]).first
        page.goto(server + link.get_attribute("href"), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator("body")).to_contain_text(APPLICATIONS[0])
    finally:
        context.close()


@pytest.mark.parametrize("label,viewport,scheme", CASES, ids=[c[0] for c in CASES])
def test_a_question_naming_no_type_lists_every_type_and_says_which_each_is(
    browser, server, organisation, tmp_path, label, viewport, scheme
):
    context, page = _new_page(browser, viewport, scheme)
    try:
        sign_in(page, server, organisation["emails"]["enterprise_architect"])
        answer = ask(page, server, UNTYPED_QUESTION)
        text = answer.inner_text()
        for name in (DATA_OBJECT,) + APPLICATIONS + (SERVICE,):
            assert name in text, text
        assert FOREIGN not in text
        assert text.index(DATA_OBJECT) < text.index(APPLICATIONS[0]), "the data object has the most dependents"
        kinds = {DATA_OBJECT: "Data", APPLICATIONS[0]: "Application", APPLICATIONS[1]: "Application", SERVICE: "Service"}
        records = answer.locator("[data-catalogue-record]")
        assert records.count() == len(kinds), text
        for index in range(records.count()):
            record = records.nth(index)
            name = record.get_by_test_id("catalogue-open").inner_text().strip()
            expect(record.locator("[data-catalogue-type]")).to_have_text(kinds[name])
        _shot(page, tmp_path, "chat-single-points-of-failure-%s.png" % label)
    finally:
        context.close()


def test_another_organisation_sees_only_its_own_hub(browser, server, organisation, tmp_path):
    other = organisation["other"]
    context, page = _new_page(browser, {"width": 1440, "height": 900}, "light")
    try:
        sign_in(page, server, other["emails"]["enterprise_architect"])
        for question in (TYPED_QUESTION, UNTYPED_QUESTION):
            answer = ask(page, server, question)
            text = answer.inner_text()
            assert FOREIGN in text, text
            for name in (DATA_OBJECT, SERVICE) + APPLICATIONS:
                assert name not in text, text
        _shot(page, tmp_path, "chat-single-points-of-failure-other-organisation.png")
    finally:
        context.close()
