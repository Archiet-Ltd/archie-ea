"""'What if we retire X?' in the chat answers from the one impact answer, with a
citation on every fact -- never a fixed value from the frozen scenario service
(ADR-003 addendum). The portfolio health block carries no fixed value either:
the unprinted trend computation is removed with it.
"""
import re
from pathlib import Path

from tests.test_sample_company_working_data import _architect

ROOT = Path(__file__).resolve().parents[1]
CHAT_SERVICE_SOURCE = (
    ROOT / "app/modules/ai_chat/services/multi_domain_chat_service.py"
).read_text(encoding="utf-8")


def _load_sample(client, login_as, user):
    login_as(client, user)
    assert client.post(
        "/architecture/sample-company/load", headers={"Accept": "application/json"}
    ).status_code == 200


def _service():
    from app.modules.ai_chat.services.multi_domain_chat_service import (
        MultiDomainChatService,
    )

    return MultiDomainChatService(user_id=None)


# --------------------------------------------------------------------------- #
# Acceptance criterion 1: scenario_analysis_service is not imported, and the
# what-if handler answers from the one impact answer with a citation on every
# fact.
# --------------------------------------------------------------------------- #


def test_scenario_analysis_service_is_not_imported_by_the_chat_service():
    """The exact evidence the brief names: a grep for the frozen service's
    module name over this file returns nothing."""
    assert "scenario_analysis_service" not in CHAT_SERVICE_SOURCE
    assert "ScenarioAnalysisService" not in CHAT_SERVICE_SOURCE
    assert "self.scenario_analysis" not in CHAT_SERVICE_SOURCE


def test_what_if_retire_answers_from_the_impact_answer_with_a_citation_on_every_fact(
    client, login_as, make_org, tenant_ctx
):
    org = make_org("whatif")
    user = _architect(org, "whatif-a@example.com")
    _load_sample(client, login_as, user)

    with tenant_ctx(org.id):
        from app.models.application_portfolio import ApplicationComponent

        app_row = ApplicationComponent.query.filter_by(name="Payments service").first()
        assert app_row is not None, "the sample company's Payments service is the fixture every other chat journey uses"

        block = _service()._detect_and_handle_scenario(
            "What if we retire Payments service?",
            {"resolved_entities": {"applications": [{"id": app_row.id, "name": app_row.name}]}},
        )

    assert block, "the what-if question must produce a block before the model is asked"
    assert "Payments service" in block

    fact_lines = [ln for ln in block.splitlines() if ln.strip().startswith("-")]
    assert fact_lines, block
    for line in fact_lines:
        assert re.search(
            r"\((drawn|derived) from your model, as of \d{1,2} [A-Z][a-z]{2} \d{4}\)$", line
        ), "every fact in the block must carry a source and a date: %r" % line
        assert "/api/" not in line, "a person reads a record name, never an internal path: %r" % line

    # Never the frozen fixed-value scenario service's report shape.
    assert "RETIREMENT IMPACT ANALYSIS" not in block
    assert "migration cost" not in block.lower()
    assert "5-year net savings" not in block.lower()
    assert "BLAST RADIUS ANALYSIS" not in block


def test_an_impact_answer_missing_the_fact_says_so_plainly_with_a_next_step(
    make_org, tenant_ctx
):
    """No model is connected and nothing matches the name: the chat must say
    the fact is missing and what to do next, never silently answer nothing
    and never guess."""
    org = make_org("whatif-empty")

    with tenant_ctx(org.id):
        block = _service()._build_cited_impact_block({"id": 1, "name": "Sensor Data Hub"})

    assert block is not None
    assert "nothing recorded matches" in block.lower() or "nothing is recorded" in block.lower()
    assert "next step" in block.lower()
    assert "/api/v1/elements/" not in block, "a missing fact must not carry an invented citation"


# --------------------------------------------------------------------------- #
# Acceptance criterion 4 (isolation): another organisation's application name
# is not resolved.
# --------------------------------------------------------------------------- #


def test_another_organisations_application_is_not_resolved(
    client, login_as, make_org, tenant_ctx
):
    org, other = make_org("whatif-mine"), make_org("whatif-theirs")
    owner = _architect(org, "whatif-b@example.com")
    _architect(other, "whatif-c@example.com")  # the other organisation, with no sample data at all
    _load_sample(client, login_as, owner)  # only `org` gets the sample company's Payments service

    with tenant_ctx(other.id):
        from app.models.application_portfolio import ApplicationComponent

        # The entity resolution the real chat pipeline runs before the what-if
        # handler never sees the owner's application: it is tenant-scoped.
        assert ApplicationComponent.query.filter_by(name="Payments service").first() is None
        block_with_nothing_resolved = _service()._detect_and_handle_scenario(
            "What if we retire Payments service?",
            {"resolved_entities": {"applications": []}},
        )
        assert block_with_nothing_resolved is None

        # Defence in depth: even handed the name directly, the catalogue's own
        # entity matcher is organisation-scoped and resolves nothing of the
        # owner's model.
        block_by_name = _service()._build_cited_impact_block(
            {"id": 999999999, "name": "Payments service"}
        )

    assert block_by_name is not None
    assert "nothing recorded matches" in block_by_name.lower()
    assert "/api/v1/elements/" not in block_by_name


# --------------------------------------------------------------------------- #
# Acceptance criterion 2: the health block contains no fixed value; the trend
# computation is gone.
# --------------------------------------------------------------------------- #


def test_calculate_portfolio_health_has_no_trend_key(app, db_session):
    from app.services.ai_chat_extensions.advanced_analytics_service import (
        AdvancedAnalyticsService,
    )

    assert not hasattr(AdvancedAnalyticsService, "_calculate_health_trend"), (
        "the trend computation must be removed entirely, not just left unread"
    )
    health = AdvancedAnalyticsService().calculate_portfolio_health()
    assert "trend" not in health


def test_the_printed_health_block_never_reads_a_trend():
    general = CHAT_SERVICE_SOURCE.split("def _process_general_message", 1)[1].split(
        "\n    def ", 1
    )[0]
    assert "trend" not in general
    assert "_calculate_health_trend" not in CHAT_SERVICE_SOURCE


# --------------------------------------------------------------------------- #
# Acceptance criterion 3: impact_hub no longer passes analysis_types; the page
# renders unchanged.
# --------------------------------------------------------------------------- #


def test_impact_hub_route_no_longer_computes_or_passes_analysis_types():
    source = (ROOT / "app/application_mgmt/impact_routes.py").read_text(encoding="utf-8")
    assert "get_analysis_types" not in source
    assert "analysis_types" not in source


def test_impact_hub_page_still_renders(client, login_as, make_org):
    org = make_org("impact-hub")
    user = _architect(org, "impact-hub-a@example.com")
    login_as(client, user)
    response = client.get("/dashboard/impact-analysis")
    assert response.status_code == 200
