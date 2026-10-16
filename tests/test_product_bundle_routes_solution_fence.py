"""Bundle routes answer a foreign solution exactly as they answer an unknown bundle.

run, download, preview, push-to-github, synthesize and stop-preview look a code
bundle up by solution id + bundle id on a table with no organisation column.
The fence used to hold only because a later Solution.query.get raised and the
route answered 422, which told a caller the foreign bundle exists. Each route
now proves the solution is the active organisation's first and returns the same
404 as an unknown bundle, before the sandbox, the git service or the file
download is reached.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.modules.solutions_product.services.product_spec_bundle import (
    build_product_spec_bundle as _REAL_BUILD_SPEC,
)

pytestmark = pytest.mark.usefixtures("db_session")


def _user(db_session, org_id, label):
    from werkzeug.security import generate_password_hash

    from app.models.user import User

    u = User(
        email=f"pbf-{label}-{uuid.uuid4().hex[:8]}@example.test",
        first_name="Pbf", last_name=label, confirmed=True,
        organization_id=org_id,
        password_hash=generate_password_hash("x"),
    )
    db_session.add(u)
    db_session.flush()
    return u


@pytest.fixture
def scene(db_session, make_org):
    from app.models.solution_models import Solution
    from app.modules.solutions_product.models import SolutionCodeBundle

    org_a = make_org("pbf-a")
    org_b = make_org("pbf-b")
    user_a = _user(db_session, org_a.id, "a")
    sol_a = Solution(name="Own Solution", organization_id=org_a.id)
    sol_b = Solution(name="Foreign Solution", organization_id=org_b.id)
    db_session.add_all([sol_a, sol_b])
    db_session.flush()
    tag = uuid.uuid4().hex[:10]
    bundle_a = SolutionCodeBundle(solution_id=sol_a.id, bundle_id=f"own-{tag}", language="python-fastapi")
    bundle_b = SolutionCodeBundle(solution_id=sol_b.id, bundle_id=f"foreign-{tag}", language="python-fastapi")
    db_session.add_all([bundle_a, bundle_b])
    db_session.flush()
    ids = {"user_a": user_a.id, "sol_a": sol_a.id, "sol_b": sol_b.id,
           "bundle_a": bundle_a.bundle_id, "bundle_b": bundle_b.bundle_id}
    db_session.commit()
    return ids


@pytest.fixture
def effects(monkeypatch):
    """Stub every effect a bundle route can reach and record each call."""
    from app.modules.solutions_product.routes import product_routes
    from app.modules.solutions_product.services import (
        business_logic_synthesizer,
        deterministic_code_generator,
        git_integration_service,
        product_spec_bundle,
        sandbox_runner,
    )

    calls = []
    fake_code_bundle = SimpleNamespace(files=[])

    monkeypatch.setattr(product_spec_bundle, "build_product_spec_bundle",
                        lambda solution_id: calls.append(("spec", solution_id)) or object())
    monkeypatch.setattr(deterministic_code_generator.DeterministicCodeGenerator, "generate",
                        lambda self, bundle: fake_code_bundle)

    def fake_run(self, bundle):
        calls.append(("run",))
        return SimpleNamespace(status="passed", test_summary={}, failures=[],
                               duration_seconds=0.1, build_log="")

    monkeypatch.setattr(sandbox_runner.SandboxRunner, "run", fake_run)
    monkeypatch.setattr(sandbox_runner.SandboxRunner, "preview",
                        lambda self, bundle: calls.append(("preview",)) or {"status": "failed"})
    monkeypatch.setattr(sandbox_runner.SandboxRunner, "stop_preview",
                        lambda self, bundle_id: calls.append(("stop_preview", bundle_id)))
    monkeypatch.setattr(git_integration_service.GitIntegrationService, "create_repo_and_push",
                        lambda self, *a, **k: calls.append(("push",)) or {"success": True})
    monkeypatch.setattr(business_logic_synthesizer.BusinessLogicSynthesizer, "synthesize_all",
                        lambda self, solution_id: calls.append(("synthesize",)) or
                        {"handlers_generated": 0, "handlers_failed": 0, "total": 0})
    monkeypatch.setattr(product_routes, "send_file",
                        lambda *a, **k: calls.append(("send_file",)) or ("zip", 200))
    return calls


ROUTES = [
    ("post", "run", False),
    ("get", "download", False),
    ("post", "preview", True),
    ("post", "push-to-github", False),
    ("post", "synthesize", False),
    ("delete", "preview", True),
]
IDS = [f"{m}-{p}" for m, p, _ in ROUTES]


def _call(client, login_as, scene, method, solution_id, bundle_id, suffix):
    from app import db

    db.session.remove()
    login_as(client, scene["user_a"])
    db.session.remove()
    kwargs = {"json": {}} if method == "post" else {}
    resp = getattr(client, method)(
        f"/api/solutions/{solution_id}/product/{bundle_id}/{suffix}", **kwargs
    )
    db.session.remove()
    return resp


@pytest.mark.parametrize("method,suffix,_no_detail", ROUTES, ids=IDS)
def test_foreign_solution_and_bundle_is_the_unknown_bundle_404(
    scene, client, login_as, effects, method, suffix, _no_detail
):
    foreign = _call(client, login_as, scene, method, scene["sol_b"], scene["bundle_b"], suffix)
    unknown = _call(client, login_as, scene, method, scene["sol_a"], "no-such-bundle", suffix)
    assert foreign.status_code == 404, foreign.get_data(as_text=True)
    assert unknown.status_code == 404
    # Same answer shape: only the ids echoed in the detail text differ.
    assert set(foreign.get_json()) == set(unknown.get_json())
    assert foreign.get_json()["error"] == unknown.get_json()["error"] == "Bundle not found"
    assert effects == []


@pytest.mark.parametrize("method,suffix,_no_detail", ROUTES, ids=IDS)
def test_unknown_organisation_is_404_and_reaches_nothing(
    scene, client, login_as, effects, monkeypatch, method, suffix, _no_detail
):
    import app.middleware.tenant_context as tc

    monkeypatch.setattr(tc, "current_org_id", lambda: None)
    resp = _call(client, login_as, scene, method, scene["sol_a"], scene["bundle_a"], suffix)
    assert resp.status_code == 404, resp.get_data(as_text=True)
    assert effects == []


@pytest.mark.parametrize("method,suffix,_no_detail", ROUTES, ids=IDS)
def test_own_solution_and_bundle_still_reaches_the_service(
    scene, client, login_as, effects, method, suffix, _no_detail
):
    resp = _call(client, login_as, scene, method, scene["sol_a"], scene["bundle_a"], suffix)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert effects, "the own-organisation call never reached the service it fronts"


@pytest.mark.parametrize("method,suffix", [("post", "run"), ("get", "download"),
                                           ("post", "preview"), ("post", "push-to-github")])
def test_foreign_bundle_with_the_real_spec_builder_is_404_not_422(
    scene, client, login_as, effects, monkeypatch, method, suffix
):
    """Before the fence the answer was 422 (the spec builder could not see the
    foreign solution), which told the caller the foreign bundle exists."""
    from app.modules.solutions_product.services import product_spec_bundle

    monkeypatch.setattr(product_spec_bundle, "build_product_spec_bundle", _REAL_BUILD_SPEC)
    resp = _call(client, login_as, scene, method, scene["sol_b"], scene["bundle_b"], suffix)
    assert resp.status_code == 404, resp.get_data(as_text=True)
    assert effects == []
