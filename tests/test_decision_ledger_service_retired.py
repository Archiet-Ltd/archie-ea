"""The decision-ledger service and ARB workflow service are retired (ADR 0012):
they must no longer exist or be importable. Neither had a live caller -- see
the ADR for the grep evidence -- and the service class left a second
writable, unfenced, unpaired register alongside the canonical
architecture_decisions store this brief consolidates into.
"""

import importlib

import pytest


def test_decision_ledger_service_module_is_gone():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("app.services.decision_ledger")


def test_arb_workflow_module_is_gone():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("app.services.arb_workflow")


def test_decision_ledger_model_still_imports_cleanly_without_the_service():
    """The DB model (app.models.decision_ledger.DecisionLedger) is a
    different class from the retired service of the same name and stays
    live, fenced by TenantMixin -- only its docstring's cross-reference to
    the retired service was updated.
    """
    import app.models.decision_ledger as decision_ledger_model

    assert hasattr(decision_ledger_model, "DecisionLedger")
    from app.models.mixins.core import TenantMixin
    assert issubclass(decision_ledger_model.DecisionLedger, TenantMixin)


def test_live_arb_workflow_service_is_unaffected():
    """app.services.arb_workflow_service's ARBWorkflowService (a same-named
    but different, live class used by app/routes/enterprise_api.py) does not
    import the retired app.services.arb_workflow module and still imports
    cleanly.
    """
    import app.services.arb_workflow_service as live_service

    assert hasattr(live_service, "ARBWorkflowService")
