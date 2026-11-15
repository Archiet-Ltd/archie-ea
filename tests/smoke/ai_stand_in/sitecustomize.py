"""Test-only: answers the options analysis with a fixed reply, so a live journey has options to read.

A journey puts this directory first on the server's PYTHONPATH. Python runs this file when the server starts;
it waits for the options analysis module to be imported and replaces the one AI call with a fixed reply that
carries amounts in its text. Nothing in the application knows about it.
"""
import copy
import importlib.abc
import importlib.machinery
import sys

TARGET = "app.modules.architecture.services.options_analysis_service"

REPLY = {
    "options": [{
        "course_of_action": "Buy", "approach_name": "Buy a ledger", "description": "Buy one",
        "cost_estimate": "£250,000", "pros": ["Licence about £40,000 a year"],
        "scores": {"weighted_total": 70},
    }],
    "recommendation": "BUY", "summary": "A fixed test reply",
}


def _fixed_reply(self, requirement, context):
    return copy.deepcopy(REPLY)


class _Loader(importlib.abc.Loader):
    def __init__(self, inner):
        self._inner = inner

    def create_module(self, spec):
        return self._inner.create_module(spec)

    def exec_module(self, module):
        self._inner.exec_module(module)
        module.OptionsAnalysisService.analyze_requirement_options = _fixed_reply


class _Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        if name != TARGET:
            return None
        spec = importlib.machinery.PathFinder.find_spec(name, path)
        if spec is not None and spec.loader is not None:
            spec.loader = _Loader(spec.loader)
        return spec


sys.meta_path.insert(0, _Finder())
