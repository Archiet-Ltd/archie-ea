"""What a question the records cannot answer offers as its next step, and to whom.

Adding the one missing thing is offered to whoever may add it. Bringing a whole
model in (connecting GitHub, loading the sample company) is offered only where
there is no model yet: never beside a typo in an organisation that already holds
its systems.
"""
from types import SimpleNamespace

import pytest

from app.models.archimate_core import ArchiMateElement
from app.modules.ai_chat.routes import estate_answer_routes as routes


def _answer(**over):
    base = {"records": [], "complete": False, "named_not_found": None}
    base.update(over)
    return base


@pytest.fixture
def person(monkeypatch):
    """A signed-in person who may add an application, connect GitHub and load the sample."""
    monkeypatch.setattr(routes, "current_user", SimpleNamespace(id=1))
    from app.core.auth import permissions
    from app.utils import role_access

    state = SimpleNamespace(may_add=True)
    monkeypatch.setattr(permissions, "can", lambda user, action, thing: state.may_add)
    monkeypatch.setattr(role_access, "may_connect_github", lambda user, org_id: True)
    monkeypatch.setattr(role_access, "may_load_sample_company", lambda user: True)
    return state


def _hold_an_element(db_session, org):
    db_session.add(
        ArchiMateElement(name="Payments service", type="ApplicationComponent", layer="application", organization_id=org.id)
    )
    db_session.flush()


def test_a_name_that_is_missing_from_an_empty_organisation_offers_add_it_and_the_whole_model_ways_in(
    person, make_org, tenant_ctx
):
    org = make_org("empty")
    with tenant_ctx(org.id):
        steps = routes._empty_steps(_answer(named_not_found="Zebrafoo"), org.id)
    assert steps == {"kind": "add_application", "name": "Zebrafoo", "connect": True, "sample": True}


def test_a_name_that_is_missing_from_a_populated_organisation_offers_add_it_only(
    person, make_org, tenant_ctx, db_session
):
    org, other = make_org("full"), make_org("other")
    _hold_an_element(db_session, org)
    with tenant_ctx(org.id):
        steps = routes._empty_steps(_answer(named_not_found="Zebrafoo"), org.id)
    assert steps == {"kind": "add_application", "name": "Zebrafoo", "connect": False, "sample": False}
    # another organisation's systems do not make this one populated
    with tenant_ctx(other.id):
        steps = routes._empty_steps(_answer(named_not_found="Zebrafoo"), other.id)
    assert steps["connect"] is True and steps["sample"] is True


def test_a_name_that_is_missing_and_may_not_be_added_offers_nothing_in_a_populated_organisation(
    person, make_org, tenant_ctx, db_session
):
    org = make_org("full")
    _hold_an_element(db_session, org)
    person.may_add = False
    with tenant_ctx(org.id):
        assert routes._empty_steps(_answer(named_not_found="Zebrafoo"), org.id) is None


def test_a_name_that_is_missing_and_may_not_be_added_still_offers_the_ways_in_to_an_empty_organisation(
    person, make_org, tenant_ctx
):
    org = make_org("empty")
    person.may_add = False
    with tenant_ctx(org.id):
        steps = routes._empty_steps(_answer(named_not_found="Zebrafoo"), org.id)
    assert steps == {"kind": "connect_or_sample", "connect": True, "sample": True}


def test_a_question_with_no_name_offers_the_ways_in_only_to_an_empty_organisation(
    person, make_org, tenant_ctx, db_session
):
    empty, full = make_org("empty"), make_org("full")
    _hold_an_element(db_session, full)
    with tenant_ctx(empty.id):
        assert routes._empty_steps(_answer(), empty.id) == {"kind": "connect_or_sample", "connect": True, "sample": True}
    with tenant_ctx(full.id):
        assert routes._empty_steps(_answer(), full.id) is None


def test_an_answer_with_records_or_a_finished_finding_offers_nothing(person, make_org, tenant_ctx):
    org = make_org("empty")
    with tenant_ctx(org.id):
        assert routes._empty_steps(_answer(records=[{"name": "x"}]), org.id) is None
        assert routes._empty_steps(_answer(complete=True), org.id) is None
