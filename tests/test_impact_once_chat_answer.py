"""The chat's answer to "What breaks if Payments service fails?" lists each affected element once."""
from tests.test_sample_company_cited import _load, _payments_id
from tests.test_sample_company_working_data import _architect

AFFECTED = 11


def _impact(app, org, element_id):
    """The answer the chat and saved-answer pages are built from (the public endpoint
    leaves the list of ways each element was reached out of its rows)."""
    from flask import g

    from app.modules.intelligence.services.answer_envelope import KIND_IMPACT, _compute

    with app.test_request_context("/"):
        g.current_org_id = org.id
        return _compute(
            KIND_IMPACT,
            {"element_id": element_id, "include_derived": True, "max_depth": 3, "direction": "downstream"},
        )


def test_sample_company_answer_has_one_row_per_affected_element_with_both_sources(app, client, login_as, make_org):
    org = make_org()
    user = _architect(org, "once-chat@example.com")
    assert _load(client, login_as, user).status_code == 200
    answer = _impact(app, org, _payments_id(org.id))
    ids = [row["element_id"] for row in answer["rows"]]
    assert len(ids) == len(set(ids)) == AFFECTED
    names = {i: answer["elements"][str(i)]["name"] for i in ids}
    cache = next(row for row in answer["rows"] if names[row["element_id"]] == "Cache layer")
    assert [rel["kind"] for rel in cache["relations"]] == ["explicit", "derived"]
    assert answer["summary"]["explicit_count"] + answer["summary"]["derived_count"] == len(ids)
