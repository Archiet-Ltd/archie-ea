"""The dashboard's route to the one definition of an owned application.

The rule lives in the owner store; this only hands it to the v2 dashboard, the
same pass-through the other v2 services use for shared services.
"""
import importlib

_ownership = importlib.import_module("app.services.element_ownership_service")


def application_is_owned_expression(model=None):
    return _ownership.application_is_owned_expression(model)
