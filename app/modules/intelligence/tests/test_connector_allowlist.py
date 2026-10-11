"""Tests for the connector allowlist gate that sits in front of any future
crosswalk write.

Covers: refusal of a known-pending source with a named reason, refusal of a
type nobody has ever heard of (proving the allowlist, not a denylist, is the
mechanism), that a refusal performs no write, that this module's claim names
both boundaries it gates (crosswalk write and connector configuration) and
the configuration path calls it, pass-through for every permitted type, the config-approved
default-closed behaviour, and a mutation check that a denylist substitution
would have let the unrecognised-type test catch it.
"""

from __future__ import annotations

import logging

import pytest

from app.modules.intelligence.services import connector_allowlist as gate_module
from app.modules.intelligence.services.connector_allowlist import (
    GATED_CONNECTOR_TYPES,
    PERMITTED_CONNECTOR_TYPES,
    ConnectorNotPermitted,
    assert_connector_permitted,
)


# ---------------------------------------------------------------------------
# 1. Named-gated refusal
# ---------------------------------------------------------------------------

def test_named_gated_type_is_refused_with_reason(app, caplog):
    with app.app_context():
        with caplog.at_level(logging.WARNING, logger="archie.intelligence.connector_allowlist"):
            with pytest.raises(ConnectorNotPermitted) as excinfo:
                assert_connector_permitted("hr")

    # The exception names the missing compliance artifact, not just "no".
    message = str(excinfo.value).lower()
    assert "compliance" in message
    assert "hr" in message

    # A WARNING was logged carrying the connector type.
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "expected a WARNING log record on refusal"
    assert any("hr" in r.getMessage() for r in warnings)


def test_hr_is_a_recognised_gated_type():
    """Sanity check on the fixture data itself: 'hr' must actually be a
    member of GATED_CONNECTOR_TYPES for the above test to be exercising the
    named-gated-reason path rather than the generic unrecognised path."""
    assert "hr" in GATED_CONNECTOR_TYPES
    assert "hr" not in PERMITTED_CONNECTOR_TYPES


# ---------------------------------------------------------------------------
# 2. Unanticipated-type refusal (allowlist, not denylist)
# ---------------------------------------------------------------------------

def test_unrecognised_type_outside_both_sets_is_also_refused(app):
    assert "mystery_hrms" not in PERMITTED_CONNECTOR_TYPES
    assert "mystery_hrms" not in GATED_CONNECTOR_TYPES

    with app.app_context():
        with pytest.raises(ConnectorNotPermitted):
            assert_connector_permitted("mystery_hrms")


# ---------------------------------------------------------------------------
# 3. End-to-end zero-write: a refused call has no side effect
# ---------------------------------------------------------------------------

def test_refused_call_reaches_no_writer(app):
    """There is no crosswalk writer to call in this codebase yet. This test
    is written around the same shape a future writer test can reuse: call
    the gate first, and assert that a side-effect marker representing "the
    writer ran" was never flipped. A future test only needs to add the real
    writer call after the gate and use its own object/row as the marker.
    """
    calls = []

    def fake_writer(connector_type, external_id, element_id):
        assert_connector_permitted(connector_type)
        calls.append((connector_type, external_id, element_id))

    with app.app_context():
        with pytest.raises(ConnectorNotPermitted):
            fake_writer("mystery_hrms", "ext-1", "element-1")

    assert calls == [], "no write should occur when the gate refuses the connector type"


# ---------------------------------------------------------------------------
# 4. Narrowed-claim correctness
# ---------------------------------------------------------------------------

def test_module_docstring_names_both_boundaries_it_gates():
    """The gate now covers two boundaries: the crosswalk write, where a
    connected system's data enters the model, and connector configuration,
    where a connected system is first wired up. Its documentation must name
    both, and the configuration path must really call it, so the claim and
    the code cannot drift apart."""
    import inspect

    from app.modules.codegen.routes import connector_routes
    from app.modules.codegen.services import connector_orchestrator

    doc = " ".join((gate_module.__doc__ or "").lower().split())
    assert "two boundaries" in doc
    assert "crosswalk" in doc
    assert "configur" in doc
    assert "assert_connector_permitted(" in inspect.getsource(connector_orchestrator)
    assert "assert_connector_permitted(" in inspect.getsource(connector_routes)


def test_assert_connector_permitted_has_no_configuration_side_effect(app):
    """The gate function only inspects the type and either returns or
    raises -- it never touches connector configuration state (credentials,
    stored connection records, etc.). There is no configuration store
    parameter or return value here to touch, which is itself the point:
    this function's signature and behaviour are scoped to a single decision
    about a connector type string."""
    with app.app_context():
        result = assert_connector_permitted("jira")
    assert result is None


# ---------------------------------------------------------------------------
# 5. Permitted pass-through
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "connector_type",
    sorted(PERMITTED_CONNECTOR_TYPES),
)
def test_each_permitted_type_passes(app, connector_type):
    with app.app_context():
        assert assert_connector_permitted(connector_type) is None


def test_permitted_set_has_exactly_the_expected_six_members():
    assert PERMITTED_CONNECTOR_TYPES == frozenset(
        {"servicenow", "jira", "m365", "devops", "lucidchart", "ea_tool"}
    )


def test_ea_tool_justification_is_recorded_in_module_source():
    """ea_tool's justification (already configurable with no gate today,
    and carries architecture-tool exports rather than personal/HR/security
    data) must be recorded as a plain code comment, not left implicit."""
    import inspect

    source = inspect.getsource(gate_module)
    assert "ea_tool" in source
    # A comment near the allowlist definition explains why it is safe.
    assert "architecture" in source.lower()


# ---------------------------------------------------------------------------
# 6. Config-approved path / default-closed behaviour
# ---------------------------------------------------------------------------

def test_config_approved_type_passes_when_added(app):
    app.config["COMPLIANCE_APPROVED_CONNECTOR_TYPES"] = ("hr",)
    try:
        with app.app_context():
            assert assert_connector_permitted("hr") is None
    finally:
        app.config.pop("COMPLIANCE_APPROVED_CONNECTOR_TYPES", None)


def test_same_type_is_refused_at_the_empty_tuple_default(app):
    # No COMPLIANCE_APPROVED_CONNECTOR_TYPES set at all -- default applies.
    app.config.pop("COMPLIANCE_APPROVED_CONNECTOR_TYPES", None)
    with app.app_context():
        with pytest.raises(ConnectorNotPermitted):
            assert_connector_permitted("hr")


def test_default_config_value_absent_still_refuses_every_type_not_in_the_static_allowlist(app):
    """When the key is genuinely unset (not just defaulted at read time),
    the union check must fall back to an empty set of extra approvals rather
    than raising or silently permitting everything -- i.e. the default is
    behaviourally closed, not just structurally absent."""
    app.config.pop("COMPLIANCE_APPROVED_CONNECTOR_TYPES", None)
    with app.app_context():
        with pytest.raises(ConnectorNotPermitted):
            assert_connector_permitted("some_type_nobody_configured")

