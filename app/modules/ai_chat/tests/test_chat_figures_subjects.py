"""A subject's figure binds only to its own record; earlier turns' keys never render; the records
block restores every record a removed clause cites or names.

Tables are built the way a turn builds them (``Grounding.model_view``), in two real organisations.

Fixtures (app, db_session, make_org) come from app/modules/conftest.py.
"""

from __future__ import annotations

import re

from app.modules.ai_chat.services import chat_figures as cf
from app.modules.ai_chat.services.ai_hallucination_detector import Grounding
from app.modules.ai_chat.tests.test_answer_tool_and_citation_gate import (
    _call_answer,
    _enforce_mode,  # noqa: F401 - the module's autouse fixture, reused here
    _estate,
    _run_turn,
    _user,
)
from app.modules.ai_chat.tools import registry

IMPACT = {
    "fields": [
        {"path": "summary.total", "kind": "count",
         "reads": {"one": "{value} connection downstream", "other": "{value} connections downstream"},
         "reads_null": "no downstream connections were counted", "label": "Connections downstream",
         "concept": "demo.impact"},
    ],
    "rows": [],
    "names": ["name"],
    "subject": {"id_path": "subject.id", "name_path": "subject.name"},
}
NO_SUBJECT = {**IMPACT, "subject": "none"}
RECORDS = {
    "fields": [
        {"path": "result[].cost", "kind": "money", "currency_field": "currency",
         "reads": "an estimated cost of {value}", "reads_null": "no estimated cost recorded",
         "label": "Estimated cost", "concept": "demo.cost"},
        {"path": "result[].score", "kind": "score", "out_of": 100,
         "reads": "a priority score of {value}", "reads_null": "no priority score recorded",
         "label": "Priority score", "concept": "demo.score"},
    ],
    "rows": [{"path": "result[]", "id_field": "id", "name_field": "name"}],
    "names": ["name"],
}
TIERS = {
    "fields": [
        {"path": "tiers[].count", "kind": "count",
         "reads": {"one": "{value} investment", "other": "{value} investments"},
         "reads_null": "no investments counted", "label": "Investments", "concept": "demo.tier"},
    ],
    "rows": [{"path": "tiers[]", "label_field": "tier"}],
    "names": [],
}


def _impact(subject_id, name, total):
    return {"success": True, "subject": {"id": subject_id, "name": name}, "summary": {"total": total}}


def _records(rows):
    return {"success": True, "result": [
        {"id": i, "name": n, "cost": c, "currency": "GBP", "score": s} for i, n, c, s in rows]}


def _turn(org_id, *calls):
    """A turn that made *calls*, each ``(tool, result, declaration)``."""
    grounding = Grounding(org_id)
    for tool, result, declaration in calls:
        grounding.model_view(tool, result, declaration, org_id)
        grounding.add_tool_result(result)
    return grounding


def _key(grounding, record_id, field="total"):
    found = [e for e in grounding.figure_table.entries
             if str(e.get("record_id")) == str(record_id) and e["field"].endswith(field)]
    assert len(found) == 1, (record_id, field)
    return found[0]["key"]


def _reasons(text, grounding, org_id):
    return [v["reason"] for v in cf.violations_in(text, grounding.figure_table, org_id)]


def _two_lookups(org_id):
    return _turn(org_id,
                 ("ask_impact", _impact(31, "Orders Hub", 13), IMPACT),
                 ("ask_impact", _impact(32, "Billing", 4), IMPACT))


# --------------------------------------------------------------------------- #
# AM1 and AM2: binding                                                         #
# --------------------------------------------------------------------------- #


def test_a_subject_figure_renders_beside_its_subject(make_org):
    org_a, org_b = make_org("subj-ok-a"), make_org("subj-ok-b")
    for org, (orders, billing) in ((org_a, (13, 4)), (org_b, (21, 9))):
        grounding = _turn(org.id, ("ask_impact", _impact(31, "Orders Hub", orders), IMPACT),
                          ("ask_impact", _impact(32, "Billing", billing), IMPACT))
        key = _key(grounding, 31)
        for text in ("Orders Hub [ref:31] has [fig:%s]." % key,
                     "Orders Hub has [fig:%s], so a failure reaches them." % key,
                     "orders hub has [fig:%s]." % key):
            assert _reasons(text, grounding, org.id) == [], text
        assert cf.render("Orders Hub [ref:31] has [fig:%s]." % key, grounding.figure_table)[0] == (
            "Orders Hub [ref:31] has %d connections downstream." % orders)


def test_a_subject_figure_beside_another_record_is_unbound(make_org):
    org = make_org("subj-other")
    grounding = _two_lookups(org.id)
    orders = _key(grounding, 31)
    for text in ("Billing [ref:32] has [fig:%s]." % orders,
                 "Billing has [fig:%s]." % orders):
        assert _reasons(text, grounding, org.id) == [cf.REASON_UNBOUND], text


def test_a_subject_figure_in_a_sentence_naming_its_subject_and_another_record_is_unbound(make_org):
    org = make_org("subj-both")
    grounding = _two_lookups(org.id)
    orders = _key(grounding, 31)
    for text in ("Orders Hub [ref:31] and Billing [ref:32] have [fig:%s]." % orders,
                 "Orders Hub and Billing have [fig:%s]." % orders,
                 "Orders Hub [ref:31] has [fig:%s] and Billing has more." % orders):
        assert _reasons(text, grounding, org.id) == [cf.REASON_UNBOUND], text


def test_a_subject_figure_in_a_sentence_naming_no_record_is_unbound(make_org):
    org = make_org("subj-none-named")
    grounding = _two_lookups(org.id)
    orders = _key(grounding, 31)
    assert _reasons("A failure there reaches [fig:%s]." % orders, grounding, org.id) == [cf.REASON_UNBOUND]
    # A name that only sits inside a longer record's name is that record, not the subject.
    longer = _turn(org.id, ("ask_impact", _impact(31, "Orders Hub", 13), IMPACT),
                   ("ask_impact", _impact(33, "Orders Hub Legacy", 2), IMPACT))
    assert _reasons("Orders Hub Legacy has [fig:%s]." % _key(longer, 31), longer, org.id) == [cf.REASON_UNBOUND]


def test_the_revision_line_for_a_subject_figure_names_its_subject(make_org):
    org = make_org("subj-line")
    grounding = _two_lookups(org.id)
    orders = _key(grounding, 31)
    violations = cf.violations_in("Billing [ref:32] has [fig:%s]." % orders, grounding.figure_table, org.id)
    message = cf.revision_message(violations, grounding.figure_table, "q", 2)
    assert ("[fig:%s] belongs to Orders Hub [ref:31]. Put it in a sentence that cites or names that "
            "record and no other record." % orders) in message.split("\n")


def test_a_figure_without_a_subject_binds_to_any_sentence(make_org):
    org_a, org_b = make_org("subj-free-a"), make_org("subj-free-b")
    grounding = _turn(org_a.id, ("find_things", _impact(None, "x", 7), NO_SUBJECT),
                      ("ask_impact", _impact(31, "Orders Hub", 13), IMPACT))
    free = [e for e in grounding.figure_table.entries if e["tool"] == "find_things"][0]["key"]
    for text in ("It has [fig:%s]." % free, "Orders Hub [ref:31] has [fig:%s]." % free,
                 "Billing and Orders Hub have [fig:%s]." % free):
        assert _reasons(text, grounding, org_a.id) == [], text
    # The organisation summary's figures bind anywhere too, and never in another organisation.
    summary = Grounding(org_a.id)
    summary.figure_table.declare_summary({"portfolio_summary": {"total_applications": 20}},
                                         registry.ORGANISATION_SUMMARY_FIGURES, org_a.id)
    key = summary.figure_table.summary_entries()[0]["key"]
    assert _reasons("Orders Hub [ref:31] sits in an estate of [fig:%s]." % key, summary, org_a.id) == []
    assert _reasons("It has [fig:%s]." % key, summary, org_b.id) == [cf.REASON_OTHER_ORG]


def test_a_tool_with_top_level_figures_declares_its_subject_or_none():
    for schema in registry.TOOL_SCHEMAS:
        declaration = schema["figures"]
        top = [f for f in declaration.get("fields", []) if "[]" not in f["path"]]
        if top:
            subject = declaration.get("subject")
            assert subject == "none" or (isinstance(subject, dict) and (
                subject.get("map") or (subject.get("id_path") and subject.get("name_path")))), schema["name"]
    assert registry.ORGANISATION_SUMMARY_FIGURES["subject"] == "none"
    undeclared = {"fields": [dict(IMPACT["fields"][0])], "rows": [], "names": []}
    assert registry.figure_declaration_problems(undeclared)
    assert not registry.figure_declaration_problems({**undeclared, "subject": "none"})
    assert registry.figure_declaration_problems({**undeclared, "subject": "somebody"})


# --------------------------------------------------------------------------- #
# AM3: earlier turns                                                           #
# --------------------------------------------------------------------------- #


def _turn_one(app, monkeypatch, user, root):
    """Turn one: the model looks the root up and writes the reference. Returns the result and the
    key it used."""
    used = []

    def says(messages):
        blob = " ".join(str(m.get("content")) for m in messages)
        used.append(re.findall(r"""['"]total['"]: \{['"]fig['"]: ['"](F\d+)['"]""", blob)[-1])
        return {"text": "%s [ref:%d] has [fig:%s]." % (root.name, root.id, used[-1])}

    result, _events, _runner, _seen = _run_turn(
        app, monkeypatch, [_call_answer(root.id), says], user=user, message="What breaks if %s fails?" % root.name)
    return result, used[-1]


def test_earlier_answers_reach_the_model_as_delivered_text(app, db_session, make_org, monkeypatch):
    org = make_org("am3-text")
    user = _user(db_session, org.id)
    root, _mid, _leaf = _estate(db_session, org.id, "Turn")
    first, _key_used = _turn_one(app, monkeypatch, user, root)
    delivered = first["response"]
    assert "[fig:" not in delivered and "connections downstream" in delivered

    history = [{"role": "user", "content": "What breaks if Turn Root fails?"},
               {"role": "assistant", "content": delivered}]
    _result, _e, _r, seen = _run_turn(
        app, monkeypatch, [{"text": "Nothing more to add."}], user=user, message="Anything else?", history=history)
    sent = seen[0]
    assert sent[0] == {"role": "user", "content": "What breaks if Turn Root fails?"}
    assert sent[1] == {"role": "assistant", "content": delivered}, "the text the person read, as delivered"
    assert "[fig:" not in " ".join(str(m["content"]) for m in sent[:2])


def test_keys_start_above_every_key_in_the_model_input(app, db_session, make_org, monkeypatch):
    org = make_org("am3-start")
    user = _user(db_session, org.id)
    root, _mid, _leaf = _estate(db_session, org.id, "Turn")
    assert cf.key_floor(None, "hello") == 0, "no key anywhere: the first key is F1"
    assert cf.key_floor([{"role": "user", "content": "copy [fig:F7]"},
                         {"role": "user", "content": 'tool said {"fig": "F12", "reads": "x"}'}], "") == 12
    assert cf.key_floor([], "my own [fig:F40] message") == 40, "the person's own message counts"
    assert cf.key_floor([{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}], "") == (
        cf.KEYS_PER_TURN), "an answer delivered as text holds no key, so its turn is counted instead"

    def first_key(history, message):
        _r, _e, runner, _seen = _run_turn(
            app, monkeypatch, [_call_answer(root.id), {"text": "Nothing to add."}], user=user, message=message,
            history=history)
        return min(int(e["key"][1:]) for e in runner._grounding.figure_table.entries)

    assert first_key(None, "What breaks if Turn Root fails?") == 1
    history = [{"role": "user", "content": "earlier [fig:F7]"},
               {"role": "assistant", "content": "Earlier answer with the tool saying 'fig': 'F12'"}]
    assert first_key(history, "What breaks if Turn Root fails?") > 12


def test_a_key_from_an_earlier_turn_is_unknown_and_never_renders(app, db_session, make_org, monkeypatch):
    org_a, org_b = make_org("am3-stale-a"), make_org("am3-stale-b")
    user = _user(db_session, org_a.id)
    root, _mid, _leaf = _estate(db_session, org_a.id, "Turn")
    other_root, _m, _l = _estate(db_session, org_b.id, "Other")
    first, old_key = _turn_one(app, monkeypatch, user, root)
    history = [{"role": "user", "content": "What breaks if Turn Root fails?"},
               {"role": "assistant", "content": first["response"]}]
    stale = {"text": "Turn Root [ref:%d] has [fig:%s]." % (root.id, old_key)}

    result, _e, runner, seen = _run_turn(
        app, monkeypatch, [_call_answer(root.id), stale, stale], user=user,
        message="Why is that so high?", history=history)

    assert len(seen) == 3, "one revision"
    assert "[fig:%s] does not exist in this turn." % old_key in seen[2][-1]["content"]
    assert "[fig:" not in result["response"]
    assert result["response"].startswith(cf.PLAIN_LINE)
    new_phrase = [f for f in result["figures"]][0]["reads"]
    assert new_phrase.endswith("connections downstream") and "Turn Root [ref:%d]" % root.id in result["response"]
    assert all(f["key"] != old_key for f in result["figures"])
    assert not any("Other" in str(m) for call in seen for m in call), "the other organisation's records never appear"


# --------------------------------------------------------------------------- #
# AM6 and AM7: the records block                                               #
# --------------------------------------------------------------------------- #


def _block(grounding, removed, shown=(), tools_called=1):
    return cf.records_block(grounding.figure_table, removed, shown, tools_called)[0]


def _headings(block):
    return [re.match(r"- (.+?\[ref:\d+\]|[^:]+):", line).group(1) for line in _lines(block)]


def _lines(block):
    return block.split("\n")[1:]


INVESTMENTS = _records([(12, "ERP Upgrade", 470000, 80), (13, "Field Service Management", 110000, 60),
                        (14, "Phase 21 Rollout", 90000, 45)])


def test_the_records_block_holds_a_record_a_removed_clause_names_without_citing(make_org):
    org = make_org("blk-named")
    grounding = _turn(org.id, ("find_investments", INVESTMENTS, RECORDS))
    block = _block(grounding, ["Field Service Management has an annual cost of £110,000."])
    assert _lines(block) == ["- Field Service Management [ref:13]: an estimated cost of £110,000; "
                             "a priority score of 60 out of 100"]


def test_a_record_named_only_in_kept_text_gets_no_line(make_org):
    org = make_org("blk-kept")
    grounding = _turn(org.id, ("find_investments", INVESTMENTS, RECORDS))
    kept = "Field Service Management is the cheapest of the three."
    block = _block(grounding, ["ERP Upgrade [ref:12] costs 5."])
    assert kept not in block and _headings(block) == ["ERP Upgrade [ref:12]"]


def test_a_name_inside_a_longer_name_adds_no_line_of_its_own(make_org):
    org = make_org("blk-longer")
    rows = _records([(20, "Orders Hub", 100, 50), (21, "Orders Hub Legacy", 200, 40)])
    grounding = _turn(org.id, ("find_investments", rows, RECORDS))
    block = _block(grounding, ["Orders Hub Legacy costs a lot."])
    assert _headings(block) == ["Orders Hub Legacy [ref:21]"]


def test_names_that_are_not_records_add_no_record_line(make_org):
    org = make_org("blk-notrecords")
    rows = {"success": True, "result": [{"id": 12, "name": "ERP Upgrade", "cost": 5, "currency": "GBP", "score": 8,
                                         "layer": "Application layer", "category": "Finance", "tier": "Critical"}]}
    grounding = _turn(org.id, ("find_investments", rows, RECORDS))
    block = _block(grounding, ["The Application layer, the Finance category, the Critical tier and Acme Industries "
                               "have many."])
    assert "[ref:" not in block, "a layer, a category, a tier label and the organisation's own name are not records"


def test_a_row_without_a_record_gets_a_line_when_a_removed_clause_holds_its_label(make_org):
    org = make_org("blk-label")
    tiers = {"success": True, "tiers": [{"tier": "Critical", "count": 1}, {"tier": "High", "count": 5}]}
    grounding = _turn(org.id, ("tiers", tiers, TIERS))
    block = _block(grounding, ["The critical tier holds several."])
    assert _lines(block) == ["- Critical: 1 investment"]


def test_a_record_cited_and_named_gets_one_line(make_org):
    org = make_org("blk-both")
    grounding = _turn(org.id, ("find_investments", INVESTMENTS, RECORDS))
    block = _block(grounding, ["ERP Upgrade [ref:12] has an annual cost of £470,000.", "ERP Upgrade is big."])
    assert _headings(block) == ["ERP Upgrade [ref:12]"]


def test_two_records_with_one_name_each_get_their_own_line(make_org):
    org = make_org("blk-twins")
    rows = _records([(31, "Billing", 10, 1), (32, "Billing", 20, 2)])
    grounding = _turn(org.id, ("find_investments", rows, RECORDS))
    block = _block(grounding, ["Billing costs something."])
    assert _headings(block) == ["Billing [ref:31]", "Billing [ref:32]"]
    cited = _block(grounding, ["Billing [ref:32] costs something."])
    assert _headings(cited) == ["Billing [ref:32]"]


def test_records_block_lines_follow_the_order_they_first_appear(make_org):
    org = make_org("blk-order")
    grounding = _turn(org.id, ("find_investments", INVESTMENTS, RECORDS))
    block = _block(grounding, ["Phase 21 Rollout costs more than Field Service Management.", "ERP Upgrade is high."])
    assert _headings(block) == [
        "Phase 21 Rollout [ref:14]", "Field Service Management [ref:13]", "ERP Upgrade [ref:12]"]


def test_a_named_records_figures_already_shown_are_left_out(make_org):
    org = make_org("blk-shown")
    grounding = _turn(org.id, ("find_investments", INVESTMENTS, RECORDS))
    keys = {e["field"]: e["key"] for e in grounding.figure_table.entries if e["record_id"] == 13}
    block = _block(grounding, ["Field Service Management is dear."], shown={keys["result[].cost"]})
    assert _lines(block) == ["- Field Service Management [ref:13]: a priority score of 60 out of 100"]
    assert _block(grounding, ["Field Service Management is dear."], shown=set(keys.values())) == ""


def test_a_name_only_the_other_organisation_holds_adds_no_line(make_org):
    org_a, org_b = make_org("blk-other-a"), make_org("blk-other-b")
    grounding_a = _turn(org_a.id, ("find_investments", INVESTMENTS, RECORDS))
    _turn(org_b.id, ("find_investments", _records([(77, "Zeta Hub", 5, 5)]), RECORDS))
    block = _block(grounding_a, ["Zeta Hub costs 5."])
    assert "Zeta" not in block and "[ref:77]" not in block


def _with_summary(org_id, *calls):
    grounding = _turn(org_id, *calls)
    grounding.figure_table.declare_summary(
        {"portfolio_summary": {"total_applications": 20, "total_capabilities": 45}},
        registry.ORGANISATION_SUMMARY_FIGURES, org_id)
    return grounding


def test_a_removed_clause_naming_no_record_shows_the_organisation_summary_in_a_tool_turn(make_org):
    org = make_org("blk-org-summary")
    grounding = _with_summary(org.id, ("find_investments", INVESTMENTS, RECORDS))
    block = _block(grounding, ["You have 999 applications."], tools_called=1)
    assert "- Your organisation: 20 applications; 45 capabilities" in block
    assert "[ref:" not in block


def test_a_removed_clause_naming_a_record_adds_no_organisation_summary_line(make_org):
    org = make_org("blk-org-none")
    grounding = _with_summary(org.id, ("find_investments", INVESTMENTS, RECORDS))
    block = _block(grounding, ["ERP Upgrade has an annual cost of £999."], tools_called=1)
    assert "Your organisation" not in block and "- ERP Upgrade [ref:12]" in block
    labelled = _with_summary(org.id, ("tiers", {"success": True, "tiers": [{"tier": "High", "count": 5}]}, TIERS))
    assert "Your organisation" not in _block(labelled, ["High is crowded."]), "a row label counts as a record"
    assert "- Your organisation: 20 applications" in _block(grounding, ["Roughly 70."], tools_called=0)


# --------------------------------------------------------------------------- #
# A record named without its marker (the commonest draft that breaks the contract) #
# --------------------------------------------------------------------------- #


def test_a_sentence_naming_a_record_without_its_marker_is_taken_out_and_its_figures_come_back(
    app, db_session, make_org, monkeypatch
):
    org_a, org_b = make_org("unmarked-a"), make_org("unmarked-b")
    user = _user(db_session, org_a.id)
    root, _mid, _leaf = _estate(db_session, org_a.id, "Turn")
    _estate(db_session, org_b.id, "Turn")
    draft = {"text": "Turn Root sits under the whole chain. It matters."}

    result, _e, runner, seen = _run_turn(
        app, monkeypatch, [_call_answer(root.id), draft], user=user, message="What breaks if Turn Root fails?")

    assert len(seen) == 2, "no revision: the draft holds no figure violation"
    assert not result["response"].startswith("Not recorded"), "the answer is not refused whole"
    assert "Turn Root sits under" not in result["response"]
    assert "It matters." in result["response"]
    assert "- Turn Root [ref:%d]: 2 connections downstream" % root.id in result["response"]
    assert result["response"].count(cf.PLAIN_LINE) == 1
