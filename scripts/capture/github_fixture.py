"""A server whose GitHub is the recorded organisation, for the browser journeys and the clip recorder.

The text below is a ``sitecustomize`` module: put it in a folder on the server's ``PYTHONPATH`` and every
request the server makes to api.github.com is answered from the recorded organisation of the service tests
(tests/test_github_builds_the_model.py), so nothing leaves the machine.
"""

# Runs inside the server process, before the application: every request to api.github.com is
# answered from the recorded organisation by the fixtures the service tests use; anything else
# goes where it would have gone.
SITECUSTOMIZE = '''
def _install():
    import requests

    original = requests.Session.request
    state = {}

    def dispatch(self, method=None, url=None, *args, **kwargs):
        if isinstance(url, str) and url.startswith("https://api.github.com/"):
            if "fake" not in state:
                from types import SimpleNamespace

                import pytest

                from tests import test_github_builds_the_model as job0
                from tests import test_github_connector as connector

                recorded = connector.github._get_wrapped_function()(SimpleNamespace(config={}), pytest.MonkeyPatch())
                job0.extend_with_job0(recorded.bundle)
                state["fake"] = requests.Session.request
                requests.Session.request = dispatch
            return state["fake"](self, method, url, *args, **kwargs)
        return original(self, method, url, *args, **kwargs)

    requests.Session.request = dispatch


_install()
'''
