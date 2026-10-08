"""Tool catalogue contract tests and fail-closed confirmation.

Catalogue fields:
  Every registry schema carries surfaces, route, fenced and risk_class fields.
  Each field is validated for type and internal consistency.

Confirmation:
  With the approval switch OFF (auto_execute=False), every mutating tool still
  requires confirmation, closing the 17 tier="auto" mutating tools that would
  otherwise run unconfirmed.
"""

import pytest

from app.modules.ai_chat.tools.registry import TOOL_SCHEMAS, TOOL_SCHEMA_BY_NAME


# --------------------------------------------------------------------------- #
# Every tool carries surfaces, route, fenced, risk_class           #
# --------------------------------------------------------------------------- #


VALID_RISK_CLASSES = {"read", "propose", "write", "external_action"}
VALID_SURFACES = {"chat", "blueprint"}


class TestEveryToolHasNewFields:
    """Every schema in the catalogue must declare the four new fields
    explicitly. A tool that omits any of them is a contract break."""

    def test_every_tool_has_surfaces(self):
        missing = [t["name"] for t in TOOL_SCHEMAS if "surfaces" not in t]
        assert missing == [], f"tools missing 'surfaces': {missing}"

    def test_every_tool_has_route(self):
        missing = [t["name"] for t in TOOL_SCHEMAS if "route" not in t]
        assert missing == [], f"tools missing 'route': {missing}"

    def test_every_tool_has_fenced_fields(self):
        missing = [t["name"] for t in TOOL_SCHEMAS if "fenced_fields" not in t]
        assert missing == [], f"tools missing 'fenced_fields': {missing}"

    def test_every_tool_has_risk_class(self):
        missing = [t["name"] for t in TOOL_SCHEMAS if "risk_class" not in t]
        assert missing == [], f"tools missing 'risk_class': {missing}"

    def test_fields_have_correct_types(self):
        bad = []
        for t in TOOL_SCHEMAS:
            name = t["name"]
            if not isinstance(t.get("surfaces"), list):
                bad.append(f"{name}: surfaces is not a list")
            elif not all(isinstance(s, str) for s in t.get("surfaces", [])):
                bad.append(f"{name}: surfaces items are not strings")
            if not isinstance(t.get("route"), str):
                bad.append(f"{name}: route is not a string")
            if not isinstance(t.get("fenced_fields"), list):
                bad.append(f"{name}: fenced_fields is not a list")
            elif not all(isinstance(f, str) for f in t.get("fenced_fields", [])):
                bad.append(f"{name}: fenced_fields items are not strings")
            if t.get("risk_class") not in VALID_RISK_CLASSES:
                bad.append(f"{name}: risk_class '{t.get('risk_class')}' not in {VALID_RISK_CLASSES}")
        assert bad == [], "\n".join(bad)

    def test_surfaces_contain_only_valid_values(self):
        bad = []
        for t in TOOL_SCHEMAS:
            invalid = set(t.get("surfaces", [])) - VALID_SURFACES
            if invalid:
                bad.append(f"{t['name']}: invalid surfaces {invalid}")
        assert bad == [], "\n".join(bad)

    def test_surfaces_list_is_non_empty(self):
        empty = [t["name"] for t in TOOL_SCHEMAS if not t.get("surfaces")]
        assert empty == [], f"tools with empty surfaces: {empty}"

    def test_route_does_not_equal_tool_name(self):
        """Each tool's route should differ from its name — route identifies
        the wrapped service function, not repeat the tool name."""
        same = [
            t["name"] for t in TOOL_SCHEMAS
            if t.get("route") == t["name"]
        ]
        assert not same, f"tools where route == name (should be different): {same}"


class TestRiskClassConsistency:
    """risk_class must be consistent with the mutates flag and the tier."""

    def test_write_tools_mutate(self):
        """Every risk_class='write' tool must declare mutates=True."""
        bad = [
            t["name"] for t in TOOL_SCHEMAS
            if t.get("risk_class") == "write" and t.get("mutates") is not True
        ]
        assert bad == [], f"write-classified tools not flagged mutating: {bad}"

    def test_read_tools_do_not_mutate(self):
        """Every risk_class='read' tool must declare mutates=False."""
        bad = [
            t["name"] for t in TOOL_SCHEMAS
            if t.get("risk_class") == "read" and t.get("mutates") is not False
        ]
        assert bad == [], f"read-classified tools flagged mutating: {bad}"

    def test_propose_tools_do_not_mutate(self):
        """risk_class='propose' tools queue changes but don't directly write."""
        bad = [
            t["name"] for t in TOOL_SCHEMAS
            if t.get("risk_class") == "propose" and t.get("mutates") is not False
        ]
        assert bad == [], f"propose-classified tools flagged mutating: {bad}"

    def test_external_action_tools_mutate(self):
        """risk_class='external_action' tools trigger side effects."""
        bad = [
            t["name"] for t in TOOL_SCHEMAS
            if t.get("risk_class") == "external_action" and t.get("mutates") is not True
        ]
        assert bad == [], f"external_action tools not flagged mutating: {bad}"

    def test_external_action_tools_always_approve_tier(self):
        """risk_class='external_action' tools must be tier='approve'."""
        bad = [
            t["name"] for t in TOOL_SCHEMAS
            if t.get("risk_class") == "external_action" and t.get("tier") != "approve"
        ]
        assert bad == [], f"external_action tools not tier='approve': {bad}"


class TestFencedFieldsConsistency:
    """fenced_fields lists result fields holding untrusted/LLM-generated text."""

    def test_fenced_fields_tools_are_not_write(self):
        """Tools with fenced_fields generate content (LLM-produced), not direct writes."""
        for t in TOOL_SCHEMAS:
            if t.get("fenced_fields") and t.get("risk_class") == "write":
                # generate_blueprint_narrative has fenced_fields AND write — the narrative
                # is LLM-generated (fenced) but overwrites section text (write).
                # This is the known exception.
                if t["name"] != "generate_blueprint_narrative":
                    pytest.fail(f"{t['name']}: fenced_fields={t['fenced_fields']} but risk_class='write'")

    def test_fenced_fields_is_always_a_list(self):
        bad = [t["name"] for t in TOOL_SCHEMAS if not isinstance(t.get("fenced_fields"), list)]
        assert bad == [], f"tools with non-list fenced_fields: {bad}"


# --------------------------------------------------------------------------- #
# Fail-closed confirmation                                          #
# --------------------------------------------------------------------------- #


class TestFailClosedConfirmation:
    """With the approval switch OFF (auto_execute=False), every mutating tool
    must require confirmation regardless of its tier.

    The 17 hand-written tools with tier='auto' and mutates=True are the ones
    most at risk of running unconfirmed — this test pins each one.
    """

    # The 17 hand-written tools with tier='auto' and mutates=True that
    # would run unconfirmed if _should_queue only checked tier.
    MUTATING_AUTO_TOOLS = sorted([
        name for name, schema in TOOL_SCHEMA_BY_NAME.items()
        if schema.get("mutates") is True
        and schema.get("tier") == "auto"
    ])

    def test_seventeen_mutating_auto_tools_exist(self):
        """Assert exactly 17 hand-written tier='auto' mutating tools exist, not
        counting the 54 generated element tools which are all tier='approve'."""
        assert len(self.MUTATING_AUTO_TOOLS) == 17, (
            f"Expected 17 mutating auto tools, got {len(self.MUTATING_AUTO_TOOLS)}: "
            f"{self.MUTATING_AUTO_TOOLS}"
        )

    @pytest.mark.parametrize("name", MUTATING_AUTO_TOOLS)
    def test_mutating_auto_tool_queues_when_auto_execute_off(self, name):
        """Each of the 17 tools queues for confirmation when auto_execute=False,
        using the same _should_queue logic AgentRunner relies on."""
        from app.modules.ai_chat.services.agent_runner import AgentRunner

        schema = TOOL_SCHEMA_BY_NAME[name]
        assert AgentRunner._should_queue(schema, auto_execute=False) is True, (
            f"{name} must queue when auto_execute=False"
        )

    @pytest.mark.parametrize("name", MUTATING_AUTO_TOOLS)
    def test_mutating_auto_tool_queues_even_when_auto_execute_on(self, name):
        """Each of the 17 tools queues for confirmation even when auto_execute=True,
        because _should_queue returns True for any tool with mutates=True or
        risk_class in {'write', 'external_action'} regardless of auto_execute.

        This is the confirmation guard: with the approval switch off, every mutating
        tool still requires confirmation, closing the 17 tier='auto' mutating
        tools that would otherwise run unconfirmed.
        """
        from app.modules.ai_chat.services.agent_runner import AgentRunner

        schema = TOOL_SCHEMA_BY_NAME[name]
        assert AgentRunner._should_queue(schema, auto_execute=True) is True, (
            f"{name} must queue even when auto_execute=True"
        )

    def test_approve_tier_tools_always_queue(self):
        """Tools with tier='approve' always queue regardless of auto_execute."""
        from app.modules.ai_chat.services.agent_runner import AgentRunner

        approve_tools = [
            t for t in TOOL_SCHEMAS if t.get("tier") == "approve"
        ]
        for t in approve_tools:
            assert AgentRunner._should_queue(t, auto_execute=True) is True, (
                f"{t['name']}: approve-tier tool must queue even with auto_execute on"
            )
            assert AgentRunner._should_queue(t, auto_execute=False) is True, (
                f"{t['name']}: approve-tier tool must queue with auto_execute off"
            )

    def test_read_tools_never_queue(self):
        """Read-tools must never queue regardless of auto_execute."""
        from app.modules.ai_chat.services.agent_runner import AgentRunner

        read_tools = [
            t for t in TOOL_SCHEMAS if t.get("risk_class") == "read"
        ]
        for t in read_tools:
            assert AgentRunner._should_queue(t, auto_execute=True) is False, (
                f"{t['name']}: read tool must not queue with auto_execute on"
            )
            assert AgentRunner._should_queue(t, auto_execute=False) is False, (
                f"{t['name']}: read tool must not queue with auto_execute off"
            )


# --------------------------------------------------------------------------- #
# Per-tool contract tests — every tool's exact field values                    #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "name,expected",
    [
        ("build_architecture_plan", {"surfaces": ["chat"], "route": "OrchestrationPlannerService.build_plan", "fenced_fields": [], "risk_class": "read"}),
        ("bulk_update_application_status", {"surfaces": ["chat", "blueprint"], "route": "ApplicationComponent.lifecycle_status", "fenced_fields": [], "risk_class": "write"}),
        ("create_adr", {"surfaces": ["chat", "blueprint"], "route": "ADRService.create_adr", "fenced_fields": [], "risk_class": "write"}),
        ("create_archimate_element", {"surfaces": ["chat", "blueprint"], "route": "ArchiMateElement", "fenced_fields": [], "risk_class": "write"}),
        ("create_archimate_relationship", {"surfaces": ["chat", "blueprint"], "route": "ArchiMateInferenceEngine.graph.get_or_create_relationship", "fenced_fields": [], "risk_class": "write"}),
        ("create_constraint", {"surfaces": ["chat", "blueprint"], "route": "SolutionConstraint", "fenced_fields": [], "risk_class": "write"}),
        ("create_contract", {"surfaces": ["chat"], "route": "VendorContract", "fenced_fields": [], "risk_class": "write"}),
        ("create_driver", {"surfaces": ["chat", "blueprint"], "route": "SolutionDriver", "fenced_fields": [], "risk_class": "write"}),
        ("create_goal", {"surfaces": ["chat", "blueprint"], "route": "SolutionGoal", "fenced_fields": [], "risk_class": "write"}),
        ("create_option", {"surfaces": ["chat", "blueprint"], "route": "SolutionRecommendation", "fenced_fields": [], "risk_class": "write"}),
        ("create_programme", {"surfaces": ["chat", "blueprint"], "route": "ProgrammeSetupService.create_business_first_programme", "fenced_fields": [], "risk_class": "write"}),
        ("create_requirement", {"surfaces": ["chat", "blueprint"], "route": "SolutionRequirement", "fenced_fields": [], "risk_class": "write"}),
        ("create_risk", {"surfaces": ["chat", "blueprint"], "route": "SolutionRisk", "fenced_fields": [], "risk_class": "write"}),
        ("create_solution", {"surfaces": ["chat", "blueprint"], "route": "Solution", "fenced_fields": [], "risk_class": "write"}),
        ("create_vendor", {"surfaces": ["chat"], "route": "AIDataInteractionService.create_vendor", "fenced_fields": [], "risk_class": "write"}),
        ("diagnose_chain", {"surfaces": ["chat"], "route": "ArchiMateInferenceEngine.diagnose", "fenced_fields": [], "risk_class": "read"}),
        ("explain_element", {"surfaces": ["chat"], "route": "ArchiMateInferenceEngine.explain", "fenced_fields": [], "risk_class": "read"}),
        ("extract_contract_from_document", {"surfaces": ["chat"], "route": "ContractExtractionService.extract_contract_terms", "fenced_fields": ["terms", "parties", "dates", "obligations"], "risk_class": "external_action"}),
        ("find_applications", {"surfaces": ["chat"], "route": "ApplicationComponent.query", "fenced_fields": [], "risk_class": "read"}),
        ("find_applications_by_capability", {"surfaces": ["chat", "blueprint"], "route": "ApplicationCapabilityMapping.query", "fenced_fields": [], "risk_class": "read"}),
        ("find_technical_capabilities", {"surfaces": ["chat"], "route": "TechnicalCapability.query", "fenced_fields": [], "risk_class": "read"}),
        ("generate_blueprint_narrative", {"surfaces": ["chat", "blueprint"], "route": "generate_section_narrative", "fenced_fields": ["narrative"], "risk_class": "write"}),
        ("get_arb_status", {"surfaces": ["chat"], "route": "ARBReviewItem.query", "fenced_fields": [], "risk_class": "read"}),
        ("get_completeness_score", {"surfaces": ["chat", "blueprint"], "route": "BlueprintCompletenessService.score_all", "fenced_fields": [], "risk_class": "read"}),
        ("get_executive_dashboard", {"surfaces": ["chat"], "route": "ExecutiveDashboardService.get_executive_summary", "fenced_fields": [], "risk_class": "read"}),
        ("get_investment_priorities", {"surfaces": ["chat"], "route": "InvestmentPrioritizationService", "fenced_fields": [], "risk_class": "read"}),
        ("get_solution_summary", {"surfaces": ["chat", "blueprint"], "route": "Solution.query", "fenced_fields": [], "risk_class": "read"}),
        ("infer_schema", {"surfaces": ["chat", "blueprint"], "route": "SchemaInferenceService.infer", "fenced_fields": ["candidates"], "risk_class": "read"}),
        ("link_application_to_capability", {"surfaces": ["chat", "blueprint"], "route": "ApplicationCapabilityMapping", "fenced_fields": [], "risk_class": "write"}),
        ("link_application_to_solution", {"surfaces": ["chat", "blueprint"], "route": "Solution.applications", "fenced_fields": [], "risk_class": "write"}),
        ("link_capability_to_solution", {"surfaces": ["chat", "blueprint"], "route": "SolutionCapabilityMapping", "fenced_fields": [], "risk_class": "write"}),
        ("link_vendor_product", {"surfaces": ["chat", "blueprint"], "route": "Solution.vendor_products", "fenced_fields": [], "risk_class": "write"}),
        ("mark_option_recommended", {"surfaces": ["chat", "blueprint"], "route": "SolutionRecommendation.is_recommended", "fenced_fields": [], "risk_class": "write"}),
        ("merge_capabilities", {"surfaces": ["chat", "blueprint"], "route": "BusinessCapability.merge", "fenced_fields": [], "risk_class": "write"}),
        ("poll_infrastructure", {"surfaces": ["chat"], "route": "InfrastructurePollingService.poll_infrastructure", "fenced_fields": [], "risk_class": "external_action"}),
        ("propose_genome_patch", {"surfaces": ["chat"], "route": "genome.patch.proposer.propose_genome_patch", "fenced_fields": [], "risk_class": "propose"}),
        ("propose_rationalization", {"surfaces": ["chat"], "route": "RationalizationProposalService.generate_proposals", "fenced_fields": [], "risk_class": "read"}),
        ("query_capability_gaps", {"surfaces": ["chat"], "route": "BusinessCapability.current_maturity_level", "fenced_fields": [], "risk_class": "read"}),
        ("record_capability_maturity", {"surfaces": ["chat", "blueprint"], "route": "BusinessCapability.maturity_levels", "fenced_fields": [], "risk_class": "write"}),
        ("run_inference_engine", {"surfaces": ["chat", "blueprint"], "route": "ArchiMateInferenceEngine.repair", "fenced_fields": [], "risk_class": "write"}),
        ("score_rationalization", {"surfaces": ["chat", "blueprint"], "route": "RationalizationScoringService.calculate_app_score", "fenced_fields": [], "risk_class": "write"}),
        ("search_archimate_elements", {"surfaces": ["chat", "blueprint"], "route": "ArchiMateElement.query", "fenced_fields": [], "risk_class": "read"}),
        ("search_capabilities_by_problem", {"surfaces": ["chat"], "route": "VectorEmbeddingService.embed_text", "fenced_fields": [], "risk_class": "read"}),
        ("simulate_impact", {"surfaces": ["chat"], "route": "ArchiMateInferenceEngine.simulate_change_impact", "fenced_fields": [], "risk_class": "read"}),
        ("submit_for_arb_review", {"surfaces": ["chat", "blueprint"], "route": "TypedARBSubmissionAdapter.submit_solution_for_actor", "fenced_fields": [], "risk_class": "write"}),
        ("update_application_status", {"surfaces": ["chat", "blueprint"], "route": "ApplicationComponent.deployment_status", "fenced_fields": [], "risk_class": "write"}),
        ("update_solution_fields", {"surfaces": ["chat", "blueprint"], "route": "Solution.fields", "fenced_fields": [], "risk_class": "write"}),
        ("update_solution_phase", {"surfaces": ["chat", "blueprint"], "route": "Solution.adm_phase", "fenced_fields": [], "risk_class": "write"}),
        ("upsert_license", {"surfaces": ["chat"], "route": "LicenseEntitlement", "fenced_fields": [], "risk_class": "write"}),
        ("validate_sap_clean_core", {"surfaces": ["chat", "blueprint"], "route": "SAPCleanCoreService.validate", "fenced_fields": [], "risk_class": "read"}),
        ("verify_codegen", {"surfaces": ["chat", "blueprint"], "route": "CodegenVerifierService.verify_solution", "fenced_fields": [], "risk_class": "read"}),
    ],
)
def test_hand_written_tool_contract(name, expected):
    """Every hand-written tool's surfaces, route, fenced and risk_class match
    the pinned values."""
    schema = TOOL_SCHEMA_BY_NAME.get(name)
    assert schema is not None, f"{name} is not registered"
    for field, expected_value in expected.items():
        assert schema[field] == expected_value, (
            f"{name}.{field}: expected {expected_value!r}, got {schema[field]!r}"
        )


def test_generated_element_tools_have_contract():
    """Every generated element tool has the expected field values."""
    from app.modules.ai_chat.tools.archimate_specs import ELEMENT_SPECS

    # Get element tool schemas by iterating TOOL_SCHEMAS and filtering for
    # archimate_layer (which only generated element tools carry).
    element_tools = {s["name"]: s for s in TOOL_SCHEMAS if "archimate_layer" in s}

    assert len(element_tools) == len(ELEMENT_SPECS), (
        f"Expected {len(ELEMENT_SPECS)} element tools, got {len(element_tools)}"
    )

    for name, schema in sorted(element_tools.items()):
        assert schema["surfaces"] == ["chat", "blueprint"], (
            f"{name}.surfaces: expected ['chat', 'blueprint']"
        )
        assert schema["route"] == "ArchiMateElement", (
            f"{name}.route: expected 'ArchiMateElement'"
        )
        assert schema["fenced_fields"] == [], f"{name}.fenced_fields must be []"
        assert schema["risk_class"] == "write", (
            f"{name}.risk_class must be 'write'"
        )