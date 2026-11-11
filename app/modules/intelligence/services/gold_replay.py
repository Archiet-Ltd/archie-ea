"""The one replay runner: a gold set of questions, answered by scripted models, scored.

    python -m app.modules.intelligence.services.gold_replay --set figures

runs every question of ``tests/gold/figures`` in enforce mode under four scripted
behaviours of the model, plus one stale-key run, and exits 0 only when no true figure was lost
and no wrong figure was shown, in all of them:

  compliant    writes the references, as the contract asks.
  ignores      writes each reference's own text (true digits) instead, cites the same
               records, and does the same again after the revision.
  ignores_uncited
               writes each reference's own text (true digits), names each record as the tool
               returned it, writes no [ref:], and does the same again after the revision.
  stale_key    (one run, question 26, after a real first turn on question 3) calls the tool
               again but writes the key it used in the first turn for the count, and repeats it.
  adversarial  per question: the true count plus one, or the other organisation's value; a
               figure of another row; an unknown key; the scale written out; a sum; or a
               figure with no tool call.

The scripted models call the real tools of the real runner against two real organisations
(A, and B with the same names and different figures). A wrong figure is shown when the
delivered text holds a digit or number word outside the envelope's rendered figures and
the allowed forms (an independent re-scan below), a rendered figure that is not a true
figure of A, a rendered figure whose value is not the gold value of the record its own sentence
names (or of the organisation, when it names none), or anything on the question's forbidden
list. A true figure is lost when an
expected rendering is missing from the delivered text, in prose or in the records block.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock

BEHAVIOURS = ("compliant", "ignores", "adversarial", "ignores_uncited")
STALE_KEY = "stale_key"
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), *([os.pardir] * 4)))
_NUMBER_WORDS = (
    "zero two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen "
    "eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred thousand million billion "
    "trillion dozen"
).split()


# --------------------------------------------------------------------------- #
# The set                                                                      #
# --------------------------------------------------------------------------- #


def load_set(name: str) -> Tuple[Dict[str, Any], Any]:
    """``(questions document, fixture module)`` of the gold set *name*."""
    folder = os.path.join(ROOT, "tests", "gold", name)
    with open(os.path.join(folder, "questions.json"), encoding="utf-8") as handle:
        document = json.load(handle)
    spec = importlib.util.spec_from_file_location("gold_%s_fixture" % name, os.path.join(folder, "fixture.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return document, module


def _investments_for(variant: Dict[str, Any]) -> Dict[str, Any]:
    ranked = [
        {"capability_name": name, "priority_level": "HIGH" if score >= 60 else "MEDIUM",
         "investment_priority_score": score, "estimated_cost": None, "timeframe_months": None}
        for name, score in variant["investments"]
    ]
    return {"success": True, "result": {
        "ranked": ranked, "split": {"critical": 1, "high": 5, "medium": 5, "low": 2},
        "mapping_count": len(ranked)}, "message": "ranked"}


class Estates:
    """Organisation A and organisation B, written once, with the ids the questions name."""

    def __init__(self, fixture, create_org, create_user) -> None:
        self.fixture = fixture
        self.org: Dict[str, int] = {}
        self.user: Dict[str, int] = {}
        self.ids: Dict[str, Dict[str, Any]] = {}
        for variant in ("A", "B"):
            org_id = create_org(variant)
            self.org[variant] = org_id
            self.user[variant] = create_user(org_id, variant)
            self.ids[variant] = fixture.build_estate(org_id, variant)

    def variant_of(self, org_id) -> Optional[str]:
        return next((v for v, o in self.org.items() if o == org_id), None)


# --------------------------------------------------------------------------- #
# The scripted model                                                           #
# --------------------------------------------------------------------------- #


class ScriptedModel:
    """One behaviour of the model for one question: it looks up what the plan says, then
    answers the way its behaviour does."""

    def __init__(self, question: Dict[str, Any], behaviour: str, variant: str, ids: Dict[str, Any]) -> None:
        self.question, self.behaviour, self.variant, self.ids = question, behaviour, variant, ids
        self.issued = 0
        self.calls = 0
        self.stale_key: Optional[str] = None
        self.history_seen: List[str] = []

    def _template(self, value):
        return value[self.variant] if isinstance(value, dict) else value

    def _argument(self, value):
        if isinstance(value, str) and value.startswith("@e:"):
            return self.ids["elements"][value[3:]]
        return value

    def _lookup(self, table, spec: str) -> Dict[str, Any]:
        parts = spec.split(":")
        tool, suffix, name = parts[0], parts[1], (parts[2] if len(parts) > 2 else None)
        wanted_tool = "organisation_summary" if tool == "summary" else tool
        for entry in table.entries:
            if entry["tool"] != wanted_tool or not entry["field"].endswith(suffix):
                continue
            if name and (entry.get("record_name") or entry.get("label")) != name:
                continue
            return entry
        raise LookupError("no figure %r in this turn's table" % spec)

    def _resolve(self, template: str, table, as_text: bool) -> str:
        def ref(match):
            kind, name = match.group(1), match.group(2)
            source = self.ids["elements"] if kind == "e" else self.ids["applications"]
            return "[ref:%s]" % source[name]

        def fig(match):
            entry = self._lookup(table, match.group(1))
            if self.stale_key and match.group(1).startswith("answer:summary.total"):
                return "[fig:%s]" % self.stale_key
            return entry["rendered"] if as_text else "[fig:%s]" % entry["key"]

        def named(match):
            # Names the record as the tool returned it and writes no [ref:].
            name = match.group(2)
            before = template[:match.start()].rstrip()
            return "" if before.lower().endswith(name.lower()) else name

        if self.behaviour == "ignores_uncited":
            text = re.sub(r"\s?\{ref:([ea]):([^}]+)\}", lambda m: (" " + named(m)) if named(m) else "", template)
        else:
            text = re.sub(r"\{ref:([ea]):([^}]+)\}", ref, template)
        return re.sub(r"\{fig:([^}]+)\}", fig, text)

    def next(self, runner, messages) -> Dict[str, Any]:
        self.calls += 1
        question = self.question
        kind = (question.get("adversarial") or {}).get("kind")
        no_tool = self.behaviour == "adversarial" and kind == "no_tool_figure"
        plan = [] if no_tool else question["plan"]
        if self.issued < len(plan):
            step = plan[self.issued]
            self.issued += 1
            args = {k: self._argument(v) for k, v in step["args"].items()}
            return {"text": "", "tool_calls": [{"id": "gold-%d" % self.issued, "name": step["tool"], "arguments": args}]}
        table = runner._grounding.figure_table
        if self.behaviour == "adversarial":
            return {"text": self._resolve(self._template(question["adversarial"]["text"]), table, False),
                    "tool_calls": []}
        template = self._template(question["compliant"])
        return {"text": self._resolve(template, table, self.behaviour in ("ignores", "ignores_uncited")),
                "tool_calls": []}


# --------------------------------------------------------------------------- #
# Scoring                                                                      #
# --------------------------------------------------------------------------- #


def _strip_markers(text: str) -> str:
    return re.sub(r"\[ref:[^\]\s]+\]", " ", text)


def rescan(text: str, figures: List[Dict[str, Any]], names: List[str], question: str) -> List[str]:
    """Digits and number words left in the delivered *text* once the envelope's rendered figures,
    reference markers, links, list markers, names and the person's own typed numbers are set aside.
    Written apart from the engine's own scan on purpose."""
    chars = list(text)

    def blank(a, b):
        for i in range(a, b):
            if chars[i] != "\n":
                chars[i] = " "

    for figure in figures:
        blank(figure["start"], figure["end"])
    for pattern in (r"\[ref:[^\]\s]+\]", r"https?://\S+", r"\]\([^)]*\)", r"(?m)^\s*\d{1,3}[.)](?=\s)"):
        for match in re.finditer(pattern, "".join(chars)):
            blank(*match.span())
    from app.modules.ai_chat.services.chat_figures import names_file_entries

    for name in sorted(set(names) | set(names_file_entries()), key=len, reverse=True):
        if not re.search(r"[\d]", name):
            continue
        pattern = r"(?<![\w])" + r"\s+".join(re.escape(p) for p in name.split()) + r"(?![\w])"
        for match in re.finditer(pattern, "".join(chars), re.IGNORECASE):
            blank(*match.span())
    typed = set(re.findall(r"[$£€]?\d[\d.,]*\s?(?:mn|bn|m|k|b|%)?", question))
    left = "".join(chars)
    found = []
    for match in re.finditer(r"[$£€]?\d[\d.,]*\s?(?:mn|bn|m|k|b|%)?", left):
        span = match.group(0).strip().rstrip(".,")
        if span not in {t.strip().rstrip(".,") for t in typed}:
            found.append(span)
    for word in _NUMBER_WORDS:
        for match in re.finditer(r"(?<![\w])%ss?(?![\w])" % word, left, re.IGNORECASE):
            found.append(match.group(0))
    return found


def _forbidden_hits(text: str, forbidden: List[str]) -> List[str]:
    body = _strip_markers(text)
    hits = []
    for item in forbidden:
        if re.fullmatch(r"[\d.,%$£€]+", item):
            if re.search(r"(?<![\w.,])" + re.escape(item) + r"(?![\w])", body):
                hits.append(item)
        elif item.lower() in body.lower():
            hits.append(item)
    return hits


class Run(dict):
    """The outcome of one question under one behaviour."""


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def _unit_around(text: str, position: int) -> str:
    """The table row, list item or sentence of *text* that holds *position*."""
    offset = 0
    for line in text.split("\n"):
        end = offset + len(line)
        if offset <= position <= end:
            if line.lstrip().startswith(("|", "-", "*")) or re.match(r"\s*\d{1,3}[.)]\s", line):
                return line
            cursor = offset
            for match in _SENTENCE_END.finditer(line):
                stop = offset + match.start()
                if position <= stop:
                    return text[cursor:stop]
                cursor = offset + match.end()
            return text[cursor:end]
        offset = end + 1
    return text


def named_in(unit: str, names: List[str]) -> List[str]:
    """The record names *unit* holds: whole words, ignoring case, longest name first, none inside another."""
    ordered = sorted(set(names), key=lambda n: (-len(n), n.lower()))
    if not ordered:
        return []
    pattern = re.compile(r"(?<![\w])(?:" + "|".join(r"\s+".join(re.escape(p) for p in n.split()) for n in ordered)
                         + r")(?![\w])", re.IGNORECASE)
    return [m.group(0).lower() for m in pattern.finditer(unit)]


def record_mismatches(delivered: str, figures: List[Dict[str, Any]], names: List[str],
                      truth: Dict[str, Any]) -> List[str]:
    """Rendered figures whose value is not the gold value of the record the sentence names (the
    record in brackets after the figure, in a sentence that names several), or, in a sentence that
    names no record, of the organisation. Written apart from the engine's binding on purpose."""
    wrong: List[str] = []
    for figure in figures:
        reads = figure["reads"]
        bracket = re.search(r" \(([^()]*)\)$", reads)
        phrase = reads[:bracket.start()] if bracket else reads
        if phrase in truth["org"]:
            continue
        unit = _unit_around(delivered, figure["start"])
        found = sorted(set(named_in(unit, names)))
        if bracket:
            compared = bracket.group(1).lower()
        elif len(found) == 1:
            compared = found[0]
        elif not found:
            wrong.append("no record named beside: %s" % phrase)
            continue
        else:
            wrong.append("several records named, none chosen, beside: %s" % phrase)
            continue
        if phrase not in truth["by_record"].get(compared, set()):
            wrong.append("%s is not a figure of %s" % (phrase, compared))
    return wrong


def score(question: Dict[str, Any], behaviour: str, variant: str, result: Dict[str, Any], names: List[str],
          truth: Dict[str, Any], model: ScriptedModel) -> Run:
    delivered = result.get("response") or ""
    figures = result.get("figures") or []
    expected_key = "expected_adversarial" if behaviour == "adversarial" and "expected_adversarial" in question \
        else "expected"
    expected = question[expected_key][variant]
    lost = [phrase for phrase in expected if phrase not in delivered]
    wrong: List[str] = []
    wrong += ["written: %s" % span for span in rescan(delivered, figures, names, question["ask"])]
    other = "B" if variant == "A" else "A"
    for figure in figures:
        reads = re.sub(r" \([^()]*\)$", "", figure["reads"])
        if reads not in truth[variant]["all"]:
            wrong.append(("other organisation: %s" if reads in truth[other]["all"] else "not a true figure: %s") % reads)
    wrong += record_mismatches(delivered, figures, names, truth[variant])
    wrong += ["forbidden: %s" % hit for hit in _forbidden_hits(delivered, question["forbidden"][variant])]
    check = result.get("figure_check") or {}
    return Run(
        id=question["id"], behaviour=behaviour, lost=lost, wrong=wrong, delivered=delivered,
        fallback=bool((check.get("fallback") or {}).get("clauses_removed")),
        revision=bool(check.get("revision_ran")), tool_calls=model.issued,
    )


# --------------------------------------------------------------------------- #
# Running                                                                      #
# --------------------------------------------------------------------------- #


def _names_of(estates: Estates, variant: str) -> List[str]:
    ids = estates.ids[variant]
    return list(ids["elements"]) + list(ids["applications"]) + [
        name for name, _ in estates.fixture.VARIANTS[variant]["investments"]]


def _truth_phrases(app, estates: Estates, variant: str) -> Dict[str, Any]:
    """Every figure the real tools give for *variant*, read without any model: all the phrases
    (``all``), the phrases of each record or row label (``by_record``, keyed by lower-case name) and
    the phrases that belong to no record (``org``)."""
    from flask import g

    from app.modules.ai_chat.services.ai_hallucination_detector import Grounding
    from app.modules.ai_chat.tools.executor import ToolCall, ToolExecutor
    from app.modules.ai_chat.tools.registry import TOOL_SCHEMA_BY_NAME

    org_id, user_id = estates.org[variant], estates.user[variant]
    phrases: set = set()
    with app.test_request_context("/"):
        g.current_org_id = org_id
        grounding = Grounding(org_id)
        calls = [("answer", {"question": "impact", "element_id": eid}) for eid in estates.ids[variant]["elements"].values()]
        calls += [("find_applications", {"limit": 50}), ("find_applications", {"ownership": "unowned", "limit": 50}),
                  ("find_applications", {"lifecycle_status": "retired", "limit": 50}),
                  ("find_applications", {"name_contains": "Billing", "limit": 50}),
                  ("get_investment_priorities", {})]
        for name, arguments in calls:
            out = ToolExecutor(user_id).execute(ToolCall(id="truth", name=name, arguments=arguments))
            grounding.model_view(name, out, TOOL_SCHEMA_BY_NAME[name]["figures"], org_id)
        context = _summary_context(app, org_id)
        from app.modules.ai_chat.tools.registry import ORGANISATION_SUMMARY_FIGURES

        grounding.figure_table.declare_summary(context, ORGANISATION_SUMMARY_FIGURES, org_id)
        by_record: Dict[str, set] = {}
        org: set = set()
        for entry in grounding.figure_table.entries:
            phrases.add(entry["rendered"])
            owner = entry.get("record_name") or entry.get("label")
            if owner:
                by_record.setdefault(owner.lower(), set()).add(entry["rendered"])
            else:
                org.add(entry["rendered"])
    return {"all": phrases, "by_record": by_record, "org": org}


def _summary_context(app, org_id) -> Dict[str, Any]:
    from flask import g

    from app.modules.ai_chat.services.multi_domain_chat_service import MultiDomainChatService

    g.current_org_id = org_id
    return MultiDomainChatService().get_domain_context("general", {}).get("context", {})


def _gold_history(question: Dict[str, Any], estates: Estates, variant: str):
    if not question.get("history"):
        return None
    expected = estates.fixture.EXPECTED[variant]
    return [{"role": h["role"], "content": re.sub(
        r"\{expected:(\w+)\}", lambda m: str(expected[m.group(1)]), h["content"])} for h in question["history"]]


def _answer(app, estates: Estates, question: Dict[str, Any], model: ScriptedModel, history, variant: str):
    """One turn of the real runner with *model* scripted; returns ``(result, runner, messages seen)``."""
    from flask import g

    from app.modules.ai_chat.services.agent_runner import AgentRunner

    seen: List[List[Dict[str, Any]]] = []

    def fake(self, provider, model_name, api_key, system_prompt, messages, tools, stream=False,
             base_url=None, _model=model):
        seen.append([dict(m) for m in messages])
        reply = _model.next(self, messages)
        raw = MagicMock()
        raw.content = []
        return {"text": reply["text"], "tool_calls": reply["tool_calls"], "raw": raw}

    AgentRunner._call_llm = fake
    runner = AgentRunner(user_id=estates.user[variant])
    with app.test_request_context("/"):
        g.current_org_id = estates.org[variant]
        result = runner.run(user_message=question["ask"], domain="general", context={}, history=history)
    return result, runner, seen


def run_set(app, estates: Estates, document: Dict[str, Any], behaviours=BEHAVIOURS, only=None,
            stale_key_run: bool = True) -> List[Run]:
    """Run each question of *document* under each behaviour, as organisation A's person, then the
    one stale-key run (a real turn on question 3, then question 26 with the first turn's key)."""
    from app.modules.ai_chat.services.agent_runner import AgentRunner
    from app.modules.ai_chat.services.llm_service_impl import LLMService
    from app.modules.ai_chat.tools.executor import ToolExecutor

    variant = "A"
    names = _names_of(estates, variant)
    runs: List[Run] = []
    saved = (AgentRunner._gate_mode, AgentRunner._call_llm, LLMService._get_configured_provider,
             LLMService._get_all_api_keys, ToolExecutor._tool_get_investment_priorities)
    AgentRunner._gate_mode = staticmethod(lambda: "enforce")
    LLMService._get_configured_provider = staticmethod(lambda: ("anthropic", "replay-model"))
    LLMService._get_all_api_keys = staticmethod(lambda provider: ["gold-key"])
    # The investment scorer reads a shared catalogue that no organisation owns, so the replay
    # gives the tool the scorer's own output shape for the organisation asking.
    ToolExecutor._tool_get_investment_priorities = lambda self, args: _investments_for(
        estates.fixture.VARIANTS[estates.variant_of(self._get_organization_id())])
    try:
        truth = {v: _truth_phrases(app, estates, v) for v in ("A", "B")}
        by_id = {q["id"]: q for q in document["questions"]}
        for question in document["questions"]:
            if only and question["id"] not in only:
                continue
            for behaviour in behaviours:
                model = ScriptedModel(question, behaviour, variant, estates.ids[variant])
                result, runner, _seen = _answer(app, estates, question, model,
                                                _gold_history(question, estates, variant), variant)
                run = score(question, behaviour, variant, result, names, truth, model)
                run["withheld"] = [c.get("unsupported") for c in runner._withheld_claims]
                runs.append(run)
        if stale_key_run and 3 in by_id and 26 in by_id and (not only or 26 in only):
            runs.append(_stale_key(app, estates, by_id[3], by_id[26], names, truth, variant))
    finally:
        (AgentRunner._gate_mode, AgentRunner._call_llm, LLMService._get_configured_provider,
         LLMService._get_all_api_keys, ToolExecutor._tool_get_investment_priorities) = saved
    return runs


def _stale_key(app, estates: Estates, first: Dict[str, Any], follow_up: Dict[str, Any], names: List[str],
               truth: Dict[str, Any], variant: str) -> Run:
    """Question 3 answered with references; then question 26, in which the model calls the tool
    again and writes the key it used in the first turn, and writes it again after the revision."""
    one = ScriptedModel(first, "compliant", variant, estates.ids[variant])
    result_one, runner_one, _ = _answer(app, estates, first, one, None, variant)
    old = next(e["key"] for e in runner_one._grounding.figure_table.entries
               if e["tool"] == "answer" and e["field"].endswith("summary.total"))
    two = ScriptedModel(follow_up, "compliant", variant, estates.ids[variant])
    two.stale_key = old
    history = [{"role": "user", "content": first["ask"]},
               {"role": "assistant", "content": result_one["response"]}]
    result, runner, seen = _answer(app, estates, follow_up, two, history, variant)
    run = score(follow_up, STALE_KEY, variant, result, names, truth, two)
    sent = " ".join(str(m.get("content")) for call in seen for m in call[:2])
    if "[fig:" in sent:
        run["wrong"].append("history sent to the model held a key: %s" % sent[:80])
    if any(f["key"] == old for f in result.get("figures") or []):
        run["wrong"].append("the first turn's key %s rendered in the second turn" % old)
    if "[fig:" in (result.get("response") or ""):
        run["wrong"].append("a raw key reached the person")
    run["stale_key"] = old
    run["withheld"] = [c.get("unsupported") for c in runner._withheld_claims]
    return run


def summarise(runs: List[Run]) -> Dict[str, Any]:
    """Counts per behaviour: runs, true figures lost, wrong figures shown, fallback and revision rates."""
    out: Dict[str, Any] = {}
    for behaviour in BEHAVIOURS + (STALE_KEY,):
        mine = [r for r in runs if r["behaviour"] == behaviour]
        if not mine:
            continue
        out[behaviour] = {
            "runs": len(mine),
            "true_figures_lost": sum(len(r["lost"]) for r in mine),
            "wrong_figures_shown": sum(len(r["wrong"]) for r in mine),
            "fallback_rate": round(sum(r["fallback"] for r in mine) / len(mine), 3),
            "revision_rate": round(sum(r["revision"] for r in mine) / len(mine), 3),
        }
    return out


# --------------------------------------------------------------------------- #
# The judge                                                                    #
# --------------------------------------------------------------------------- #

JUDGE_ITEMS = {
    "A": "Every figure reads as what it is, for the record it sits with.",
    "B": "No quantity is stated in words without a figure (one, all, none, most, twice, half).",
    "C": "The answer answers the question.",
    "D": "It reads naturally: no doubled words and no stranded fragment.",
}


def judge_prompt(question: str, delivered: str) -> str:
    items = "\n".join("%s. %s" % (key, text) for key, text in JUDGE_ITEMS.items())
    return (
        "You score one answer from an enterprise architecture assistant. Reply with only a JSON object "
        'whose keys are "A", "B", "C" and "D" and whose values are true when the item holds.\n'
        "Question: %s\nAnswer:\n%s\nItems:\n%s" % (question, delivered, items)
    )


def judge_answers(runs: List[Run], questions: Dict[int, str], ask_model) -> Dict[str, Any]:
    """Score each delivered answer on items A to D with *ask_model(prompt) -> text*; the failures per
    item and the share that passes. Items A and B must have no failure; C and D must reach 90%."""
    failures = {key: 0 for key in JUDGE_ITEMS}
    judged = 0
    for run in runs:
        reply = ask_model(judge_prompt(questions[run["id"]], run["delivered"]))
        try:
            verdict = json.loads(reply[reply.index("{"):reply.rindex("}") + 1])
        except ValueError:
            verdict = {}
        judged += 1
        for key in JUDGE_ITEMS:
            if verdict.get(key) is not True:
                failures[key] += 1
    share = {key: (1 - failures[key] / judged) if judged else None for key in JUDGE_ITEMS}
    ok = bool(judged) and failures["A"] == 0 and failures["B"] == 0 and share["C"] >= 0.9 and share["D"] >= 0.9
    return {"judged": judged, "failures": failures, "pass_share": share, "ok": ok}


def router_ask_model(answering_provider: str = ""):
    """An *ask_model* that goes through the existing model router, on a provider family other than
    the one that answered. Raises when no other provider is configured."""
    from app.modules.ai_chat.services.llm_model_router import LLMModelRouter, TaskComplexity
    from app.modules.ai_chat.services.llm_service_impl import LLMService

    router = LLMModelRouter()
    provider, model = router.select_model("gap_analysis", TaskComplexity.SIMPLE)
    if provider == answering_provider:
        raise RuntimeError("the judge needs a provider family other than the answering model's")

    def ask(prompt: str) -> str:
        text, _ = LLMService._call_llm_with_failover(prompt=prompt, model=model, provider=provider)
        return text

    return ask


# --------------------------------------------------------------------------- #
# Command line                                                                 #
# --------------------------------------------------------------------------- #


def _create_org(label: str) -> int:
    import uuid

    from app import db
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:8]
    org = Organization(name="Gold %s %s" % (label, suffix), slug="gold-%s-%s" % (label.lower(), suffix))
    db.session.add(org)
    db.session.commit()
    return org.id


def _create_user(org_id: int, label: str) -> int:
    import uuid

    from app import db
    from app.models.user import User

    user = User(email="gold.%s.%s@example.com" % (label.lower(), uuid.uuid4().hex[:8]), first_name="Gold",
                last_name=label, organization_id=org_id, confirmed=True, enterprise_role="enterprise_architect")
    db.session.add(user)
    db.session.commit()
    return user.id


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--set", default="figures")
    parser.add_argument("--only", type=int, nargs="*", default=None, help="question ids")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--judge", action="store_true", help="also score each delivered answer on items A to D")
    parser.add_argument("--answering-provider", default="", help="the provider family that answered")
    args = parser.parse_args(argv)

    from app import create_app, db

    app = create_app("testing")
    document, fixture = load_set(args.set)
    with app.app_context(), _rolled_back(db):
        estates = Estates(fixture, _create_org, _create_user)
        runs = run_set(app, estates, document, only=set(args.only) if args.only else None)
    summary = summarise(runs)
    failed = [r for r in runs if r["lost"] or r["wrong"]]
    judged = None
    if args.judge:
        judged = judge_answers(runs, {q["id"]: q["ask"] for q in document["questions"]},
                               router_ask_model(args.answering_provider))
        print("judge: %s" % json.dumps(judged))
        if not judged["ok"]:
            failed = failed or [Run(id=0, behaviour="judge", lost=[], wrong=["judge items failed"], delivered="")]
    if args.json:
        print(json.dumps({"summary": summary, "failed": failed,
                          "fell_back": [[r["id"], r["behaviour"]] for r in runs if r["fallback"]]},
                         indent=1, default=str))
    else:
        for behaviour, row in summary.items():
            print("%-12s runs=%d true_figures_lost=%d wrong_figures_shown=%d fallback_rate=%s revision_rate=%s" % (
                behaviour, row["runs"], row["true_figures_lost"], row["wrong_figures_shown"],
                row["fallback_rate"], row["revision_rate"]))
        for run in failed:
            print("FAIL q%s %s lost=%s wrong=%s" % (run["id"], run["behaviour"], run["lost"], run["wrong"]))
        print("gold set %s: %d runs, %d failed" % (args.set, len(runs), len(failed)))
    return 1 if failed else 0


@contextlib.contextmanager
def _rolled_back(db):
    """Everything the run writes (its two organisations and what is theirs) goes into one outer
    transaction that is rolled back when the run ends, pass or fail. A commit inside the run is a
    savepoint release, so no row, including the append-only change ledger, is left in the database."""
    factory = db.session.session_factory
    original_class, original_options = factory.class_, dict(factory.kw)
    connections = {}
    with contextlib.ExitStack() as resources:
        for engine in dict.fromkeys(db.engines.values()):
            connection = resources.enter_context(engine.connect())
            resources.callback(connection.begin().rollback)
            connections[engine] = connection

        class RollbackSession(original_class):
            def get_bind(self, mapper=None, clause=None, bind=None, **kwargs):
                resolved = super().get_bind(mapper=mapper, clause=clause, bind=bind, **kwargs)
                return connections.get(resolved, resolved)

        try:
            db.session.remove()
            factory.class_ = RollbackSession
            db.session.configure(join_transaction_mode="create_savepoint")
            yield
        finally:
            try:
                db.session.remove()
            finally:
                factory.class_ = original_class
                factory.kw.clear()
                factory.kw.update(original_options)


if __name__ == "__main__":
    sys.exit(main())
