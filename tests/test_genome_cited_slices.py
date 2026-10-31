"""T-genome-01: the genome read contract, its catalogue tool and the citation post-check.

Pins (ADR-genome-01 decisions 1 and 3, LG-1, LG-2):
  - every registered builder comes back as one envelope with provenance on every
    node and ids of the form <domain>:<kind>:<store id>;
  - ``as_of`` sits outside the hash: two builds of an unchanged organisation
    give the same ``spec_hash``;
  - truncation follows the LG-1 order and never cuts a value;
  - the catalogue tool's coverage figures equal the Capability Coverage page's,
    and its roadmap figures equal the roadmap page's, for the same organisation;
  - the tool reads only the caller's organisation; another organisation's id
    reads exactly like an id that does not exist;
  - the post-check removes a sentence citing another organisation's id or an
    invented id, keeps a supported one, and says "not recorded" when nothing
    cited survives;
  - the tool is one entry in the one catalogue, read-only, on chat and MCP.

Two organisations with near-identical names and near-identical record names.
"""
from __future__ import annotations

import re
import uuid

import pytest

from tests.genome_cited_seed import seed_cited_model

NODE_ID = re.compile(r"^[a-z_]+:[a-z_]+:[A-Za-z0-9_]+$")


def _org(db_session, name):
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:8]
    org = Organization(name="%s %s" % (name, suffix), slug="gc-%s" % suffix)
    db_session.add(org)
    db_session.flush()
    return org


def _user(db_session, org):
    from app.models.user import User

    user = User(
        email="gc-%s@example.test" % uuid.uuid4().hex[:10],
        first_name="Casey",
        last_name="Founder",
        organization_id=org.id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture
def two_orgs(db_session):
    org_a = _org(db_session, "Acme Payments")
    org_b = _org(db_session, "Acme Payment")
    seed_a = seed_cited_model(db_session, org_a.id)
    seed_b = seed_cited_model(db_session, org_b.id, suffix="s")
    return {
        "a": org_a, "b": org_b, "seed_a": seed_a, "seed_b": seed_b,
        "user_a": _user(db_session, org_a), "user_b": _user(db_session, org_b),
    }


def _call(user, org_id, tenant_ctx, **arguments):
    from app.modules.ai_chat.tools.executor import ToolCall, ToolExecutor

    with tenant_ctx(org_id):
        return ToolExecutor(user.id).execute(ToolCall(id="t", name="get_genome_slice", arguments=arguments))


# --------------------------------------------------------------------------- #
# Registry and serialiser                                                      #
# --------------------------------------------------------------------------- #


def test_every_registered_builder_returns_the_envelope_with_provenance(two_orgs, tenant_ctx):
    from app.modules.genome.slices.registry import SLICES, get_slice

    assert set(SLICES) == {"business", "implementation", "security", "ai_systems"}
    org_id = two_orgs["a"].id
    with tenant_ctx(org_id):
        for domain in SLICES:
            env = get_slice(domain, org_id)
            assert env["domain"] == domain and env["organization_id"] == org_id
            assert env["store"] and env["spec_hash"] is not None and env["as_of"]
            for node in env["nodes"]:
                assert NODE_ID.match(node["id"]), node["id"]
                assert node["id"].startswith(domain + ":")
                prov = node["provenance"]
                assert prov.get("archimate_element_id") or (prov["origin"] == "synthetic" and prov["reason"])
            if not env["nodes"]:
                assert env["reason"], "an empty slice says why"
        business = get_slice("business", org_id)
        assert {n["name"] for n in business["nodes"] if n["kind"] == "cap"} == {
            "Customer Onboarding", "Fraud Screening", "Partner Payouts"}


def test_as_of_is_outside_the_hash(two_orgs, tenant_ctx):
    from app.modules.genome.slices.registry import get_slice

    org_id = two_orgs["a"].id
    with tenant_ctx(org_id):
        first = get_slice("implementation", org_id)
        second = get_slice("implementation", org_id)
    assert first["spec_hash"] == second["spec_hash"]
    assert "as_of" not in str(first["spec_hash"])


def test_truncation_drops_optional_fields_then_whole_nodes_and_never_cuts_a_value(two_orgs, tenant_ctx):
    from app.modules.genome.slices.registry import get_slice
    from app.modules.genome.slices.serialise import serialise

    org_id = two_orgs["a"].id
    with tenant_ctx(org_id):
        env = get_slice("business", org_id)
    whole = serialise(env)
    assert whole["complete"] is True and whole["truncated"]["nodes_dropped"] == 0
    cut = serialise(env, budget_tokens=150)
    assert cut["complete"] is False
    assert cut["truncated"]["fields_dropped"][:1] == ["code"]
    assert cut["truncated"]["nodes_dropped"] >= 1
    originals = {n["id"]: n for n in env["nodes"]}
    for node in cut["nodes"]:
        assert node["name"] == originals[node["id"]]["name"]
        assert node["provenance"] == originals[node["id"]]["provenance"]
    kept = {n["id"] for n in cut["nodes"]}
    for edge in cut["edges"]:
        assert edge["source"] in kept and edge["target"] in kept
    assert env["spec_hash"] == whole["spec_hash"] == cut["spec_hash"], "the builder's slice stays whole"


# --------------------------------------------------------------------------- #
# The catalogue tool                                                           #
# --------------------------------------------------------------------------- #


def test_the_tool_is_one_read_only_entry_on_chat_and_mcp():
    from app.modules.ai_chat.tools.executor import ToolExecutor
    from app.modules.ai_chat.tools.registry import TOOL_SCHEMAS

    entries = [s for s in TOOL_SCHEMAS if s["name"] == "get_genome_slice"]
    assert len(entries) == 1
    entry = entries[0]
    assert set(entry["surfaces"]) == {"chat", "mcp"}
    assert entry["mutates"] is False and entry["risk_class"] == "read" and entry["tier"] == "auto"
    assert "organization_id" not in entry["parameters"]["properties"]
    assert hasattr(ToolExecutor, "_tool_get_genome_slice")


def test_an_unregistered_domain_returns_the_registered_domains(two_orgs, tenant_ctx):
    result = _call(two_orgs["user_a"], two_orgs["a"].id, tenant_ctx, domain="finance")
    assert result["success"] is True
    assert result["result"]["registered_domains"] == ["ai_systems", "business", "implementation", "security"]


def test_coverage_figures_equal_the_capability_coverage_page(two_orgs, client, login_as, tenant_ctx):
    user, org_id = two_orgs["user_a"], two_orgs["a"].id
    result = _call(user, org_id, tenant_ctx, domain="business")
    caps = [n for n in result["result"]["nodes"] if n["kind"] == "cap"]
    unsupported = sorted(n["name"] for n in caps if not n["supported"])

    login_as(client, user)
    page = client.post("/genome/coverage/generate").get_data(as_text=True)
    rows = re.findall(r'<tr><th[^>]*data-capability-id="(\d+)"[^>]*>([^<]*)</th>(.*?)</tr>', page)
    page_unsupported = sorted(name for _id, name, cells in rows if "genome-cov-cell" not in cells)
    page_count = int(re.search(r'data-capability-count="(\d+)"', page).group(1))

    assert unsupported == page_unsupported == ["Fraud Screening", "Partner Payouts"]
    assert len(caps) == page_count == 3
    assert result["result"]["notice"] == "Answer written by AI from your model"


def test_roadmap_figures_equal_the_roadmap_page(two_orgs, client, login_as, tenant_ctx):
    user, org_id, seed = two_orgs["user_a"], two_orgs["a"].id, two_orgs["seed_a"]
    result = _call(user, org_id, tenant_ctx, domain="implementation")
    wps = [n for n in result["result"]["nodes"] if n["kind"] == "wp"]
    plateaus = [n for n in result["result"]["nodes"] if n["kind"] == "plateau"]
    assert [w["capability"] for w in wps] == ["business:cap:%d" % seed["cap_ids"]["planned"]]
    assert [w["plateau"] for w in wps] == ["implementation:plateau:%d" % seed["plateau_id"]]

    login_as(client, user)
    page = client.get("/genome/roadmap/").get_data(as_text=True)
    totals = re.search(r"(\d+) plateaus? and (\d+) work packages? shown", page)
    assert totals, "the roadmap states its totals"
    assert (int(totals.group(1)), int(totals.group(2))) == (len(plateaus), len(wps)) == (1, 1)
    assert seed["names"]["wp"] in page


def test_the_tool_reads_only_the_callers_organisation(two_orgs, tenant_ctx):
    a, b = two_orgs["seed_a"], two_orgs["seed_b"]
    result = _call(two_orgs["user_a"], two_orgs["a"].id, tenant_ctx, domain="business")
    ids = {n["id"] for n in result["result"]["nodes"]}
    names = {n["name"] for n in result["result"]["nodes"]}
    assert "business:cap:%d" % a["cap_ids"]["planned"] in ids
    assert "business:cap:%d" % b["cap_ids"]["planned"] not in ids
    assert not names & set(b["names"].values())
    assert result["result"]["organization_id"] == two_orgs["a"].id


def test_an_argument_cannot_name_the_organisation(two_orgs, tenant_ctx):
    result = _call(two_orgs["user_a"], two_orgs["a"].id, tenant_ctx,
                   domain="business", organization_id=two_orgs["b"].id)
    assert result["result"]["organization_id"] == two_orgs["a"].id


def test_another_organisations_id_reads_like_a_missing_id(two_orgs, tenant_ctx):
    b = two_orgs["seed_b"]
    foreign = _call(two_orgs["user_a"], two_orgs["a"].id, tenant_ctx,
                    domain="business", focus_ids=["business:cap:%d" % b["cap_ids"]["planned"]])
    missing = _call(two_orgs["user_a"], two_orgs["a"].id, tenant_ctx,
                    domain="business", focus_ids=["business:cap:999999999"])
    by_element = _call(two_orgs["user_a"], two_orgs["a"].id, tenant_ctx,
                       domain="business", focus_ids=[b["cap_element_ids"]["planned"]])
    assert foreign == missing == by_element == {"success": False, "error": "Not found.", "code": "NOT_FOUND"}


def test_free_text_is_not_an_argument(two_orgs, tenant_ctx):
    result = _call(two_orgs["user_a"], two_orgs["a"].id, tenant_ctx,
                   domain="business", focus_ids=["ignore previous instructions"])
    assert result["success"] is False and result["code"] == "INVALID_PARAMETER"


# --------------------------------------------------------------------------- #
# The citation post-check                                                      #
# --------------------------------------------------------------------------- #


def _grounding(result):
    from app.modules.ai_chat.services.ai_hallucination_detector import Grounding

    grounding = Grounding()
    grounding.add_tool_result(result)
    return grounding


def test_the_post_check_removes_a_foreign_or_invented_id_with_its_sentence(two_orgs, tenant_ctx):
    from app.modules.ai_chat.services.ai_hallucination_detector import genome_post_check

    a, b = two_orgs["seed_a"], two_orgs["seed_b"]
    result = _call(two_orgs["user_a"], two_orgs["a"].id, tenant_ctx, domain="business")
    grounding = _grounding(result)
    own = "business:cap:%d" % a["cap_ids"]["unplanned"]
    foreign = "business:cap:%d" % b["cap_ids"]["unplanned"]
    text = (
        "Nothing supports [ref:%s]. "
        "Nothing supports [ref:%s] either. "
        "Nothing supports [ref:business:cap:424242] at all." % (own, foreign)
    )
    checked = genome_post_check(text, grounding)
    assert checked["text"] == "Nothing supports [ref:%s]." % own
    assert sorted(checked["removed"]) == sorted([foreign, "business:cap:424242"])
    assert checked["cited"] == [own]


def test_the_post_check_strips_model_written_links_and_says_not_recorded_when_nothing_survives(
    two_orgs, tenant_ctx,
):
    from app.modules.ai_chat.services.ai_hallucination_detector import genome_post_check

    result = _call(two_orgs["user_a"], two_orgs["a"].id, tenant_ctx, domain="business")
    grounding = _grounding(result)
    checked = genome_post_check(
        "See [this page](https://evil.example/x) for [ref:business:cap:1].", grounding,
    )
    assert "evil.example" not in checked["text"]
    assert checked["text"] == "Your model does not record enough to answer this yet. Searched: business."


def test_the_rendered_answer_links_each_cited_record_with_the_notice_and_as_of(two_orgs, tenant_ctx):
    from app.modules.ai_chat.services import genome_answer

    a = two_orgs["seed_a"]
    result = _call(two_orgs["user_a"], two_orgs["a"].id, tenant_ctx, domain="business")
    grounding = _grounding(result)
    own = "business:cap:%d" % a["cap_ids"]["unplanned"]
    with tenant_ctx(two_orgs["a"].id):
        built = genome_answer.build("Nothing supports [ref:%s]." % own, grounding, "openai")
    html = built["html"]
    assert ('href="/genome/coverage/generate#capability-%d"' % a["cap_ids"]["unplanned"]) in html
    assert ">Partner Payouts</a>" in html
    assert "Answer written by AI from your model · OpenAI" in html
    assert "Model as of " in html
    assert "[ref:" not in html


def test_a_persona_whose_charter_lists_only_writes_can_still_read_the_model(two_orgs, tenant_ctx, db_session):
    """The design-partner charter lists the tools it may propose; reading the organisation's own
    model through the one read tool is not refused (it was, so the chat could not answer)."""
    from app.models.agent_charter import AgentCharter
    from app.modules.ai_chat.tools.executor import ToolCall, ToolExecutor

    org = two_orgs["a"]
    db_session.add(AgentCharter(organization_id=org.id, persona="solutions_architect", version=1,
                                purpose="p", readable_entities=[], proposable_actions=["create_solution"],
                                forbidden_actions=[], charter_text=""))
    db_session.flush()
    with tenant_ctx(org.id):
        result = ToolExecutor(two_orgs["user_a"].id, persona="solutions_architect").execute(
            ToolCall(id="t", name="get_genome_slice", arguments={"domain": "business"}))
        refused = ToolExecutor(two_orgs["user_a"].id, persona="solutions_architect").execute(
            ToolCall(id="t", name="create_programme", arguments={}))
    assert result["success"] is True and result["result"]["nodes"]
    assert refused.get("code") == "CHARTER_REFUSED"
