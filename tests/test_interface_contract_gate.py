"""Tests for the interface contract gate (PR 2).

Every new test must fail on main (no gate exists) and pass on this branch.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(*args: str, **kwargs) -> subprocess.CompletedProcess:
    """Run a command and return the CompletedProcess.

    ``env_extra`` (optional) is merged into the subprocess environment.
    """
    env = {
        **os.environ,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONPATH": str(REPO_ROOT),
        "FLASK_CONFIG": "testing",
        **kwargs.get("env_extra", {}),
    }
    return subprocess.run(
        list(args),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=kwargs.get("cwd", REPO_ROOT),
        timeout=kwargs.get("timeout", 600),
        env=env,
    )


def _minimal_contract(**overrides) -> dict:
    """Return a minimal valid OpenAPI contract dict with an /elements path."""
    doc = {
        "openapi": "3.1.0",
        "info": {"title": "Test contract", "version": "1.0.0"},
        "paths": {
            "/elements": {
                "get": {
                    "operationId": "listElements",
                    "summary": "List elements",
                    "tags": ["Model"],
                    "parameters": [],
                    "responses": {
                        "200": {
                            "description": "Success",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object"}
                                }
                            },
                        }
                    },
                    "security": [{"entelimOAuth": ["model:read"]}],
                }
            },
            "/elements/{id}": {
                "get": {
                    "operationId": "getElement",
                    "summary": "Get one element",
                    "tags": ["Model"],
                    "parameters": [
                        {
                            "name": "id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "integer"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Success",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object"}
                                }
                            },
                        }
                    },
                    "security": [{"entelimOAuth": ["model:read"]}],
                }
            },
        },
        "components": {
            "schemas": {
                "Element": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "name": {"type": "string"},
                    },
                }
            }
        },
    }
    doc.update(overrides)
    return doc


def _write_contract(path: Path, doc: dict) -> str:
    """Write *doc* as YAML to *path* and return its sha256 hex digest."""
    text = yaml.dump(doc, default_flow_style=False, sort_keys=False)
    path.write_text(text, encoding="utf-8")
    # Hash the bytes on disk: write_text translates newlines on Windows.
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# Generator tests
# ---------------------------------------------------------------------------


class TestGenerateApiContractSubset:
    """Tests for scripts/generate_api_contract_subset.py."""

    def test_generator_exists(self):
        """The generator script must exist."""
        script = REPO_ROOT / "scripts" / "generate_api_contract_subset.py"
        assert script.exists(), f"{script} does not exist"

    def test_generator_filters_by_operation_id(self, tmp_path: Path):
        """Operations are selected by operationId (PUBLISHED_OPERATIONS),
        not by whole path group: listCapabilities is kept because it is
        listed, while createCapability on the same path is not, so a slice
        publishes exactly the operations it builds."""
        contract = _minimal_contract()
        # listCapabilities is published; createCapability on the same path is not
        contract["paths"]["/capabilities"] = {
            "get": {
                "operationId": "listCapabilities",
                "summary": "List capabilities",
                "tags": ["Strategy"],
                "parameters": [],
                "responses": {
                    "200": {
                        "description": "Success",
                        "content": {
                            "application/json": {
                                "schema": {"type": "object"}
                            }
                        },
                    }
                },
                "security": [{"entelimOAuth": ["strategy:read"]}],
            },
            "post": {
                "operationId": "createCapability",
                "summary": "Create a capability",
                "tags": ["Strategy"],
                "parameters": [],
                "responses": {
                    "201": {
                        "description": "Created",
                        "content": {
                            "application/json": {
                                "schema": {"type": "object"}
                            }
                        },
                    }
                },
                "security": [{"entelimOAuth": ["strategy:write"]}],
            },
        }
        src = tmp_path / "source.yaml"
        _write_contract(src, contract)

        out = tmp_path / "subset.yaml"
        result = _run(
            sys.executable,
            str(REPO_ROOT / "scripts" / "generate_api_contract_subset.py"),
            "--source", str(src),
            "--output", str(out),
        )
        assert result.returncode == 0, f"Generator failed:\n{result.stderr}"

        with open(out, encoding="utf-8") as f:
            subset = yaml.safe_load(f)

        # Must have /elements and /elements/{id}
        assert "paths" in subset
        assert "/elements" in subset["paths"], "Missing /elements path"
        assert "/elements/{id}" in subset["paths"], "Missing /elements/{id} path"
        # /capabilities is kept, but only for the listed operation
        assert "/capabilities" in subset["paths"], "Missing /capabilities path"
        kept = subset["paths"]["/capabilities"]
        assert "get" in kept, "listCapabilities should be published"
        assert "post" not in kept, "createCapability should not be published"

    def test_generator_follows_ref_transitively(self, tmp_path: Path):
        """A response that references a schema that references another schema
        must include both schemas in the subset."""
        contract = _minimal_contract()
        # Add a chain: ElementDetail -> Element -> BaseElement
        contract["paths"]["/elements/{id}"]["get"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"] = {"$ref": "#/components/schemas/ElementDetail"}
        contract["components"]["schemas"]["ElementDetail"] = {
            "type": "object",
            "properties": {
                "element": {"$ref": "#/components/schemas/Element"},
                "extra": {"type": "string"},
            },
        }
        contract["components"]["schemas"]["BaseElement"] = {
            "type": "object",
            "properties": {"id": {"type": "integer"}},
        }
        contract["components"]["schemas"]["Element"]["allOf"] = [
            {"$ref": "#/components/schemas/BaseElement"}
        ]

        src = tmp_path / "source.yaml"
        _write_contract(src, contract)

        out = tmp_path / "subset.yaml"
        result = _run(
            sys.executable,
            str(REPO_ROOT / "scripts" / "generate_api_contract_subset.py"),
            "--source", str(src),
            "--output", str(out),
        )
        assert result.returncode == 0, f"Generator failed:\n{result.stderr}"

        with open(out, encoding="utf-8") as f:
            subset = yaml.safe_load(f)

        schemas = subset.get("components", {}).get("schemas", {})
        assert "ElementDetail" in schemas, "Missing ElementDetail schema"
        assert "Element" in schemas, "Missing Element schema"
        assert "BaseElement" in schemas, "Missing BaseElement schema (transitive ref)"

    def test_generator_is_deterministic(self, tmp_path: Path):
        """Two runs with the same input produce byte-identical output."""
        contract = _minimal_contract()
        src = tmp_path / "source.yaml"
        _write_contract(src, contract)

        out1 = tmp_path / "subset1.yaml"
        out2 = tmp_path / "subset2.yaml"

        result1 = _run(
            sys.executable,
            str(REPO_ROOT / "scripts" / "generate_api_contract_subset.py"),
            "--source", str(src),
            "--output", str(out1),
        )
        assert result1.returncode == 0

        result2 = _run(
            sys.executable,
            str(REPO_ROOT / "scripts" / "generate_api_contract_subset.py"),
            "--source", str(src),
            "--output", str(out2),
        )
        assert result2.returncode == 0

        assert out1.read_bytes() == out2.read_bytes(), (
            "Two runs produced different output"
        )

    def test_generator_deterministic_across_hash_seeds(self, tmp_path: Path):
        """The generator must be byte-identical no matter what PYTHONHASHSEED
        the subprocess was started with.

        Regression: the generator emitted component sets in set-iteration
        order, which Python's string hashing (seeded per process) reorders,
        so two identical invocations under different seeds produced different
        files. The fixture is deliberately branching (a hub schema
        referencing 30 schemas, each referencing a leaf) so the pending-ref
        collection holds many entries whose pop order varies by hash seed.
        """
        contract = _minimal_contract()
        names = [f"Schema{i:02d}" for i in range(30)]
        leaves = [f"Leaf{i:02d}" for i in range(30)]
        for name in names:
            contract["components"]["schemas"][name] = {
                "type": "object",
                "properties": {"value": {"type": "string"}},
            }
        # Hub schema referencing every SchemaNN
        contract["components"]["schemas"]["Schema00"]["properties"] = {
            "value": {"type": "string"},
            **{
                name: {"$ref": f"#/components/schemas/{name}"}
                for name in names
            },
        }
        # Each SchemaNN references a leaf, so every branch has depth
        for name, leaf in zip(names, leaves):
            contract["components"]["schemas"][name]["properties"]["leaf"] = {
                "$ref": f"#/components/schemas/{leaf}"
            }
            contract["components"]["schemas"][leaf] = {
                "type": "object",
                "properties": {"x": {"type": "string"}},
            }
        for path_key in ("/elements", "/elements/{id}"):
            contract["paths"][path_key]["get"]["responses"]["200"]["content"][
                "application/json"
            ]["schema"] = {"$ref": "#/components/schemas/Schema00"}
        src = tmp_path / "source.yaml"
        _write_contract(src, contract)

        out1 = tmp_path / "seed1.yaml"
        out2 = tmp_path / "seed2.yaml"
        result1 = _run(
            sys.executable,
            str(REPO_ROOT / "scripts" / "generate_api_contract_subset.py"),
            "--source", str(src),
            "--output", str(out1),
            env_extra={"PYTHONHASHSEED": "1"},
        )
        assert result1.returncode == 0, f"Generator failed:\n{result1.stderr}"
        result2 = _run(
            sys.executable,
            str(REPO_ROOT / "scripts" / "generate_api_contract_subset.py"),
            "--source", str(src),
            "--output", str(out2),
            env_extra={"PYTHONHASHSEED": "2"},
        )
        assert result2.returncode == 0, f"Generator failed:\n{result2.stderr}"

        assert out1.read_bytes() == out2.read_bytes(), (
            "Generator output differs under PYTHONHASHSEED=1 vs PYTHONHASHSEED=2"
        )

    def test_regenerated_subset_matches_committed(self, tmp_path: Path):
        """Regenerating the subset must reproduce the committed
        app/config/api_contract_subset.yaml.

        The canonical contract copy lives outside this repository, so CI has
        no API_CONTRACT_SOURCE. This test therefore always runs: with the
        variable set it regenerates from the canonical copy and compares byte
        for byte; without it, it regenerates from the committed subset itself
        and proves the generator is a fixed point on its own output (same
        operations, same components, same body hash), so a generator change
        that alters the committed content is caught either way. Only the
        two header lines naming the source file and its sha256 may differ.
        """
        committed = REPO_ROOT / "app" / "config" / "api_contract_subset.yaml"
        assert committed.exists(), "No committed subset to compare against"
        source = os.environ.get("API_CONTRACT_SOURCE")
        from_canonical = bool(source)
        source_path = Path(source) if source else committed
        assert source_path.exists(), (
            f"API_CONTRACT_SOURCE names a missing file: {source_path}"
        )

        out = tmp_path / "regenerated.yaml"
        result = _run(
            sys.executable,
            str(REPO_ROOT / "scripts" / "generate_api_contract_subset.py"),
            "--source", str(source_path),
            "--output", str(out),
        )
        assert result.returncode == 0, f"Generator failed:\n{result.stderr}"

        if from_canonical:
            assert out.read_bytes() == committed.read_bytes(), (
                "Regenerated subset differs from the committed subset byte for "
                "byte; regenerate it with scripts/generate_api_contract_subset.py "
                "and commit the result"
            )
            return

        def body(path: Path) -> list[str]:
            return [
                ln for ln in path.read_text(encoding="utf-8").splitlines()
                if not ln.startswith(("# Source:", "# SHA256:"))
            ]

        assert body(out) == body(committed), (
            "The generator does not reproduce the committed subset from "
            "itself; regenerate it with scripts/generate_api_contract_subset.py "
            "and commit the result"
        )

    def test_committed_subset_header_has_64_hex_sha256(self):
        """The committed subset header must record the source sha256 as 64
        hex characters so a drift check can compare it against the live
        contract copy."""
        subset_path = REPO_ROOT / "app" / "config" / "api_contract_subset.yaml"
        if not subset_path.exists():
            pytest.skip("Committed subset not present")

        lines = subset_path.read_text(encoding="utf-8").splitlines()
        sha_lines = [ln for ln in lines if ln.startswith("# SHA256:")]
        assert sha_lines, "Committed subset header has no '# SHA256:' line"
        sha = sha_lines[0].split(":", 1)[1].strip()
        assert len(sha) == 64, f"sha256 line is {len(sha)} characters, expected 64"
        assert all(c in "0123456789abcdef" for c in sha.lower()), (
            f"sha256 line is not hex: {sha!r}"
        )

    def test_generator_records_source_sha256(self, tmp_path: Path):
        """The output file header must contain the source file name and sha256."""
        contract = _minimal_contract()
        src = tmp_path / "source.yaml"
        sha = _write_contract(src, contract)

        out = tmp_path / "subset.yaml"
        result = _run(
            sys.executable,
            str(REPO_ROOT / "scripts" / "generate_api_contract_subset.py"),
            "--source", str(src),
            "--output", str(out),
        )
        assert result.returncode == 0

        header = out.read_text(encoding="utf-8").splitlines()[:5]
        header_text = "\n".join(header)
        assert "source.yaml" in header_text, "Missing source file name in header"
        assert sha in header_text, "Missing source sha256 in header"

    def test_generated_subset_parses_and_refs_resolve(self, tmp_path: Path):
        """The generated subset must be valid YAML and every $ref must resolve
        inside the subset itself."""
        contract = _minimal_contract()
        # Add a response ref
        contract["components"]["responses"] = {
            "NotFound": {
                "description": "Not found",
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/Error"}
                    }
                },
            }
        }
        contract["components"]["schemas"]["Error"] = {
            "type": "object",
            "properties": {"message": {"type": "string"}},
        }
        contract["paths"]["/elements/{id}"]["get"]["responses"]["404"] = {
            "$ref": "#/components/responses/NotFound"
        }

        src = tmp_path / "source.yaml"
        _write_contract(src, contract)

        out = tmp_path / "subset.yaml"
        result = _run(
            sys.executable,
            str(REPO_ROOT / "scripts" / "generate_api_contract_subset.py"),
            "--source", str(src),
            "--output", str(out),
        )
        assert result.returncode == 0, f"Generator failed:\n{result.stderr}"

        # Parse the subset
        with open(out, encoding="utf-8") as f:
            subset = yaml.safe_load(f)

        assert subset is not None, "Subset is not valid YAML"
        assert "paths" in subset, "Subset has no paths"

        # Check every $ref resolves inside the subset
        def _check_refs(obj, path=""):
            if isinstance(obj, dict):
                if "$ref" in obj:
                    ref = obj["$ref"]
                    assert ref.startswith("#/"), f"External ref: {ref}"
                    parts = ref[2:].split("/")
                    current = subset
                    for part in parts:
                        assert isinstance(current, dict), (
                            f"Ref {ref} at {path} cannot resolve: {part} not found"
                        )
                        current = current.get(part)
                        assert current is not None, (
                            f"Ref {ref} at {path} cannot resolve: {part} missing"
                        )
                for k, v in obj.items():
                    _check_refs(v, f"{path}.{k}")
            elif isinstance(obj, list):
                for i, item in enumerate(obj):
                    _check_refs(item, f"{path}[{i}]")

        _check_refs(subset)

    def test_generator_handles_parameters_ref(self, tmp_path: Path):
        """Parameters referenced via $ref must be included."""
        contract = _minimal_contract()
        contract["components"]["parameters"] = {
            "Id": {
                "name": "id",
                "in": "path",
                "required": True,
                "schema": {"type": "integer"},
            }
        }
        contract["paths"]["/elements/{id}"]["parameters"] = [
            {"$ref": "#/components/parameters/Id"}
        ]

        src = tmp_path / "source.yaml"
        _write_contract(src, contract)

        out = tmp_path / "subset.yaml"
        result = _run(
            sys.executable,
            str(REPO_ROOT / "scripts" / "generate_api_contract_subset.py"),
            "--source", str(src),
            "--output", str(out),
        )
        assert result.returncode == 0, f"Generator failed:\n{result.stderr}"

        with open(out, encoding="utf-8") as f:
            subset = yaml.safe_load(f)

        params = subset.get("components", {}).get("parameters", {})
        assert "Id" in params, "Missing Id parameter"


# ---------------------------------------------------------------------------
# Gate tests
# ---------------------------------------------------------------------------


GATE_SCRIPT = REPO_ROOT / "scripts" / "check_interface_contract.py"


def _gate_module():
    """Import the gate script as a module (scripts/ is not a package)."""
    scripts_dir = str(REPO_ROOT / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import check_interface_contract

    return check_interface_contract


def _app_with_routes(routes):
    """A small application whose routes use the real ``require_api_scope``.

    ``routes`` is a list of (rule, methods, scope-or-None); None means a plain
    route that does not accept a token.
    """
    from flask import Flask

    from app.modules.oauth_provider.scopes import require_api_scope

    app = Flask(__name__)
    for index, (rule, methods, scope) in enumerate(routes):

        def view(**kwargs):
            return {}

        if scope is not None:
            view = require_api_scope(scope)(view)
        app.add_url_rule(rule, endpoint=f"route_{index}", view_func=view, methods=methods)
    return app


ELEMENT_ROUTES = [
    ("/api/v1/elements", ["GET"], "api:read"),
    ("/api/v1/elements/<int:element_id>", ["GET"], "api:read"),
]

STRATEGY_ROUTES = [
    ("/api/v1/capabilities", ["GET"], "api:read"),
    ("/api/v1/capabilities/<int:capability_id>", ["GET"], "api:read"),
    ("/api/v1/value-streams", ["GET"], "api:read"),
    ("/api/v1/value-streams/<int:value_stream_id>", ["GET"], "api:read"),
]

MOTIVATION_ROUTES = [
    ("/api/v1/drivers", ["GET"], "api:read"),
    ("/api/v1/drivers/<int:driver_id>", ["GET"], "api:read"),
    ("/api/v1/goals", ["GET"], "api:read"),
    ("/api/v1/goals/<int:goal_id>", ["GET"], "api:read"),
]

BUSINESS_ROUTES = [
    ("/api/v1/business-actors", ["GET"], "api:read"),
    ("/api/v1/business-actors/<int:actor_id>", ["GET"], "api:read"),
    ("/api/v1/business-processes", ["GET"], "api:read"),
    ("/api/v1/business-processes/<int:process_id>", ["GET"], "api:read"),
]

ALL_ROUTES = ELEMENT_ROUTES + STRATEGY_ROUTES + MOTIVATION_ROUTES + BUSINESS_ROUTES


def _six_operation_contract() -> dict:
    """The committed subset's fourteen operations: the two element reads, the
    four strategy reads, the four motivation reads, and the four business
    reads this slice builds.
    The gate matches routes to operations by path and method, so the fixture
    contract must list every token-accepting route the real application registers."""
    contract = _minimal_contract()
    contract["paths"]["/capabilities"] = {
        "get": {
            "operationId": "listCapabilities",
            "summary": "List Capability records",
            "tags": ["Strategy"],
            "parameters": [],
            "responses": {"200": {"description": "Success"}},
            "security": [{"entelimOAuth": ["model:read"]}],
        }
    }
    contract["paths"]["/capabilities/{id}"] = {
        "get": {
            "operationId": "getCapability",
            "summary": "Get a Capability",
            "tags": ["Strategy"],
            "parameters": [],
            "responses": {"200": {"description": "Success"}},
            "security": [{"entelimOAuth": ["model:read"]}],
        }
    }
    contract["paths"]["/value-streams"] = {
        "get": {
            "operationId": "listValueStreams",
            "summary": "List Value Stream records",
            "tags": ["Strategy"],
            "parameters": [],
            "responses": {"200": {"description": "Success"}},
            "security": [{"entelimOAuth": ["model:read"]}],
        }
    }
    contract["paths"]["/value-streams/{id}"] = {
        "get": {
            "operationId": "getValueStream",
            "summary": "Get a Value Stream",
            "tags": ["Strategy"],
            "parameters": [],
            "responses": {"200": {"description": "Success"}},
            "security": [{"entelimOAuth": ["model:read"]}],
        }
    }
    contract["paths"]["/drivers"] = {
        "get": {
            "operationId": "listDrivers",
            "summary": "List Driver records",
            "tags": ["Motivation"],
            "parameters": [],
            "responses": {"200": {"description": "Success"}},
            "security": [{"entelimOAuth": ["model:read"]}],
        }
    }
    contract["paths"]["/drivers/{id}"] = {
        "get": {
            "operationId": "getDriver",
            "summary": "Get a Driver",
            "tags": ["Motivation"],
            "parameters": [],
            "responses": {"200": {"description": "Success"}},
            "security": [{"entelimOAuth": ["model:read"]}],
        }
    }
    contract["paths"]["/goals"] = {
        "get": {
            "operationId": "listGoals",
            "summary": "List Goal records",
            "tags": ["Motivation"],
            "parameters": [],
            "responses": {"200": {"description": "Success"}},
            "security": [{"entelimOAuth": ["model:read"]}],
        }
    }
    contract["paths"]["/goals/{id}"] = {
        "get": {
            "operationId": "getGoal",
            "summary": "Get a Goal",
            "tags": ["Motivation"],
            "parameters": [],
            "responses": {"200": {"description": "Success"}},
            "security": [{"entelimOAuth": ["model:read"]}],
        }
    }
    contract["paths"]["/business-actors"] = {
        "get": {
            "operationId": "listBusinessActors",
            "summary": "List Business Actor records",
            "tags": ["Business"],
            "parameters": [],
            "responses": {"200": {"description": "Success"}},
            "security": [{"entelimOAuth": ["model:read"]}],
        }
    }
    contract["paths"]["/business-actors/{id}"] = {
        "get": {
            "operationId": "getBusinessActor",
            "summary": "Get a Business Actor",
            "tags": ["Business"],
            "parameters": [],
            "responses": {"200": {"description": "Success"}},
            "security": [{"entelimOAuth": ["model:read"]}],
        }
    }
    contract["paths"]["/business-processes"] = {
        "get": {
            "operationId": "listBusinessProcesses",
            "summary": "List Business Process records",
            "tags": ["Business"],
            "parameters": [],
            "responses": {"200": {"description": "Success"}},
            "security": [{"entelimOAuth": ["model:read"]}],
        }
    }
    contract["paths"]["/business-processes/{id}"] = {
        "get": {
            "operationId": "getBusinessProcess",
            "summary": "Get a Business Process",
            "tags": ["Business"],
            "parameters": [],
            "responses": {"200": {"description": "Success"}},
            "security": [{"entelimOAuth": ["model:read"]}],
        }
    }
    return contract


class TestCheckInterfaceContract:
    """Tests for scripts/check_interface_contract.py."""

    def test_matching_routes_pass_with_nothing_unbuilt(self):
        """Positive control: the two element routes match the subset."""
        gate = _gate_module()
        app = _app_with_routes(ELEMENT_ROUTES)
        passes, violations = gate.check_unlisted_routes(app, _minimal_contract())
        assert violations == []
        assert passes is True
        assert gate.count_unbuilt_operations(app, _minimal_contract()) == 0

    def test_gate_passes_on_real_app_with_committed_subset(self):
        """The gate passes on the real application with the committed subset
        (both elements routes are listed and match)."""
        subset_path = REPO_ROOT / "app" / "config" / "api_contract_subset.yaml"
        assert subset_path.exists(), "the committed subset is missing"

        result = _run(sys.executable, str(GATE_SCRIPT), "--subset", str(subset_path))
        assert result.returncode == 0, (
            f"Gate failed on real app:\n{result.stdout}\n{result.stderr}"
        )
        assert "PASS: interface-contract" in result.stdout

    def test_unlisted_route_fails_check_i_and_is_named(self):
        """A token-accepting route that is not in the subset fails check (i)
        and the failure names the method, the path and the reason."""
        gate = _gate_module()
        app = _app_with_routes(
            ELEMENT_ROUTES + [("/api/v1/widgets", ["GET"], "api:read")]
        )
        passes, violations = gate.check_unlisted_routes(app, _minimal_contract())
        assert passes is False
        assert len(violations) == 1
        assert "GET /api/v1/widgets" in violations[0]
        assert "no matching operation" in violations[0]

    def test_route_removed_from_subset_is_reported(self):
        """Removing an operation from the subset leaves its route unlisted."""
        gate = _gate_module()
        subset = _minimal_contract()
        del subset["paths"]["/elements/{id}"]
        passes, violations = gate.check_unlisted_routes(
            _app_with_routes(ELEMENT_ROUTES), subset
        )
        assert passes is False
        assert len(violations) == 1
        assert "/api/v1/elements/{element_id}" in violations[0]

    def test_routes_without_the_marker_or_outside_api_v1_are_not_checked(self):
        """A session-only route and a marked route outside /api/v1 do not
        accept a token on /api/v1, so the contract does not list them."""
        gate = _gate_module()
        app = _app_with_routes(
            ELEMENT_ROUTES
            + [
                ("/api/v1/session-only", ["GET"], None),
                ("/internal/tokenised", ["GET"], "api:read"),
            ]
        )
        passes, violations = gate.check_unlisted_routes(app, _minimal_contract())
        assert (passes, violations) == (True, [])
        assert gate.count_unbuilt_operations(app, _minimal_contract()) == 0

    def test_write_scope_against_read_only_operation_fails(self):
        """A route tagged api:write against a read-only operation fails (i)."""
        gate = _gate_module()
        app = _app_with_routes(
            [
                ("/api/v1/elements", ["GET"], "api:write"),
                ("/api/v1/elements/<int:element_id>", ["GET"], "api:read"),
            ]
        )
        passes, violations = gate.check_unlisted_routes(app, _minimal_contract())
        assert passes is False
        assert len(violations) == 1
        assert "GET /api/v1/elements " in violations[0]
        assert "only read-class scopes" in violations[0]

    def test_read_scope_against_write_operation_fails(self):
        """A route tagged api:read against an operation that needs a non-read
        scope fails (i)."""
        gate = _gate_module()
        subset = _minimal_contract()
        subset["paths"]["/elements"]["get"]["security"] = [{"entelimOAuth": ["model:write"]}]
        passes, violations = gate.check_unlisted_routes(
            _app_with_routes(ELEMENT_ROUTES), subset
        )
        assert passes is False
        assert len(violations) == 1
        assert "GET /api/v1/elements " in violations[0]
        assert "requires non-read scopes" in violations[0]

    def test_operation_without_its_own_security_inherits_the_document_one(self):
        """An operation with no security of its own takes the document-level
        requirement, so a write-scoped route is not matched by a document that
        asks for a read scope."""
        gate = _gate_module()
        subset = _minimal_contract(security=[{"entelimOAuth": ["model:read"]}])
        del subset["paths"]["/elements"]["get"]["security"]
        del subset["paths"]["/elements/{id}"]["get"]["security"]
        assert gate.check_unlisted_routes(_app_with_routes(ELEMENT_ROUTES), subset) == (True, [])
        write_app = _app_with_routes([("/api/v1/elements", ["GET"], "api:write")])
        passes, violations = gate.check_unlisted_routes(write_app, subset)
        assert passes is False
        assert "only read-class scopes" in violations[0]

    def test_trailing_slash_and_parameter_name_do_not_hide_a_match(self):
        """/elements/ in the route table matches /elements in the contract and
        a differently named path parameter still matches."""
        gate = _gate_module()
        app = _app_with_routes(
            [
                ("/api/v1/elements/", ["GET"], "api:read"),
                ("/api/v1/elements/<int:other_name>", ["GET"], "api:read"),
            ]
        )
        assert gate.check_unlisted_routes(app, _minimal_contract()) == (True, [])

    def test_unbuilt_operation_counted_by_check_ii(self, tmp_path: Path):
        """A subset operation with no route is counted by check (ii) and does
        not fail while the count is at most the baseline."""
        gate = _gate_module()
        subset = _six_operation_contract()
        subset["paths"]["/extra"] = {
            "get": {
                "operationId": "listExtra",
                "summary": "List extra things",
                "tags": ["Model"],
                "responses": {"200": {"description": "Success"}},
                "security": [{"entelimOAuth": ["model:read"]}],
            }
        }
        # The probe app must register every route the subset lists, or the
        # listed-but-unbuilt operations would be counted too.
        assert gate.count_unbuilt_operations(_app_with_routes(ALL_ROUTES), subset) == 1

        subset_path = tmp_path / "subset.yaml"
        _write_contract(subset_path, subset)
        at_baseline = _run(
            sys.executable, str(GATE_SCRIPT), "--subset", str(subset_path), "--baseline", "1"
        )
        assert at_baseline.returncode == 0, at_baseline.stdout + at_baseline.stderr
        assert "1 unbuilt operation(s) within baseline 1" in at_baseline.stdout

        above_baseline = _run(
            sys.executable, str(GATE_SCRIPT), "--subset", str(subset_path), "--baseline", "0"
        )
        assert above_baseline.returncode == 1
        assert "FAIL: interface-contract-unimplemented" in above_baseline.stdout
        assert "1 unbuilt operation(s) > baseline 0" in above_baseline.stdout

    def test_gate_supports_count_flag(self):
        """--count prints the unbuilt count as the last stdout line: 0 for the
        committed subset, whose operations are all built."""
        subset_path = REPO_ROOT / "app" / "config" / "api_contract_subset.yaml"
        result = _run(sys.executable, str(GATE_SCRIPT), "--subset", str(subset_path), "--count")
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip().splitlines()[-1] == "0"


# ---------------------------------------------------------------------------
# Verify.py registration tests
# ---------------------------------------------------------------------------


class TestVerifyRegistration:
    """The gates are registered in scripts/verify.py with the right kind."""

    def test_interface_contract_gates_registered_static(self):
        scripts_dir = str(REPO_ROOT / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        import verify

        baseline = json.loads((REPO_ROOT / "verification_baseline.json").read_text(encoding="utf-8"))
        gates = {g.name: g for g in verify.build_gates(baseline["ratchets"])}
        assert gates["interface-contract-unlisted"].kind == "zero"
        assert gates["interface-contract-unimplemented"].kind == "ratchet"
        for name in ("interface-contract-unlisted", "interface-contract-unimplemented"):
            assert gates[name].tags == ["static"]
            assert gates[name].needs_db is False

    def test_unimplemented_baseline_is_the_true_count(self):
        baseline = json.loads((REPO_ROOT / "verification_baseline.json").read_text(encoding="utf-8"))
        assert baseline["ratchets"]["interface_contract_unimplemented"] == 0


# ---------------------------------------------------------------------------
# Review round: zero gate independent of the ratchet, /api/v10, body hash
# ---------------------------------------------------------------------------

EXTRA_OPERATION = {
    "get": {
        "operationId": "listExtra",
        "summary": "List extra things",
        "tags": ["Model"],
        "responses": {"200": {"description": "Success"}},
        "security": [{"entelimOAuth": ["model:read"]}],
    }
}


def _verify_module():
    scripts_dir = str(REPO_ROOT / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import verify

    return verify


def _gates_for(verify, monkeypatch, subset_path: Path, ratchet: int) -> dict:
    """Run both interface-contract gates from verify.build_gates against a
    subset, with the given ratchet baseline."""
    monkeypatch.chdir(REPO_ROOT)
    monkeypatch.setattr(verify, "INTERFACE_CONTRACT_SUBSET", subset_path)
    monkeypatch.setattr(verify, "_INTERFACE_CONTRACT_REPORTS", {})
    gates = {g.name: g for g in verify.build_gates({"interface_contract_unimplemented": ratchet})}
    return {
        name: gates[name].runner()
        for name in ("interface-contract-unlisted", "interface-contract-unimplemented")
    }


class TestZeroGateIsIndependentOfTheRatchet:
    def test_unbuilt_operation_within_baseline_passes_both_gates(self, tmp_path, monkeypatch):
        """One unbuilt operation with the ratchet at 1: the zero gate PASSES
        (it used to fail with '[1 > 0]') and the ratchet gate passes."""
        verify = _verify_module()
        subset = _six_operation_contract()
        subset["paths"]["/extra"] = EXTRA_OPERATION
        subset_path = tmp_path / "subset.yaml"
        _write_contract(subset_path, subset)

        results = _gates_for(verify, monkeypatch, subset_path, ratchet=1)
        assert results["interface-contract-unlisted"].status == verify.PASS
        assert results["interface-contract-unimplemented"].status == verify.PASS
        assert results["interface-contract-unimplemented"].measured == 1

    def test_route_outside_the_subset_fails_the_zero_gate_with_exact_count(self, tmp_path, monkeypatch):
        """A real route missing from the subset fails the zero gate, and the
        measured count is the number of violations (one), not one more for the
        header line."""
        verify = _verify_module()
        subset = _six_operation_contract()
        del subset["paths"]["/elements/{id}"]
        subset_path = tmp_path / "subset.yaml"
        _write_contract(subset_path, subset)

        results = _gates_for(verify, monkeypatch, subset_path, ratchet=0)
        unlisted = results["interface-contract-unlisted"]
        assert unlisted.status == verify.FAIL
        assert unlisted.measured == 1
        assert "GET /api/v1/elements/{element_id}" in unlisted.detail
        assert "FAIL:" not in unlisted.detail
        assert results["interface-contract-unimplemented"].status == verify.PASS

    def test_unlisted_only_flag_skips_the_ratchet(self, tmp_path):
        subset = _six_operation_contract()
        subset["paths"]["/extra"] = EXTRA_OPERATION
        subset_path = tmp_path / "subset.yaml"
        _write_contract(subset_path, subset)
        result = _run(
            sys.executable, str(GATE_SCRIPT), "--subset", str(subset_path), "--unlisted-only"
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "PASS: interface-contract-unlisted" in result.stdout


def test_api_v10_is_not_under_api_v1():
    """/api/v10 is another version: a marked route there is not checked
    against the v1 contract, and the prefix strip does not eat '/api/v1'."""
    gate = _gate_module()
    app = _app_with_routes(ELEMENT_ROUTES + [("/api/v10/things", ["GET"], "api:read")])
    assert gate.check_unlisted_routes(app, _minimal_contract()) == (True, [])
    assert gate._under_api_v1("/api/v1") and gate._under_api_v1("/api/v1/elements")
    assert not gate._under_api_v1("/api/v10") and not gate._under_api_v1("/api/v10/things")
    assert gate._normalize_path("/api/v10/things") == "/api/v10/things"


def _body_sha_check(text: str) -> tuple[str, str]:
    """Return (recorded, recomputed) Body-SHA256 for a subset file's text."""
    lines = text.replace("\r\n", "\n").split("\n")
    recorded = next(ln for ln in lines if ln.startswith("# Body-SHA256:")).split(":", 1)[1].strip()
    body = "\n".join(ln for ln in lines if ln.startswith("#") is False or False)
    # The body is everything after the leading comment header.
    index = 0
    while lines[index].startswith("#"):
        index += 1
    body = "\n".join(lines[index:])
    return recorded, hashlib.sha256(body.encode("utf-8")).hexdigest()


class TestCommittedSubsetBodyHash:
    def test_committed_subset_body_matches_its_recorded_hash(self):
        """No source file needed: a hand edit of the committed subset body
        changes its hash and fails here, in CI."""
        text = (REPO_ROOT / "app" / "config" / "api_contract_subset.yaml").read_text(encoding="utf-8")
        recorded, recomputed = _body_sha_check(text)
        assert recorded == recomputed, "the committed subset was edited by hand; regenerate it"

    def test_a_hand_edit_changes_the_hash(self):
        text = (REPO_ROOT / "app" / "config" / "api_contract_subset.yaml").read_text(encoding="utf-8")
        recorded, recomputed = _body_sha_check(text.replace("listElements", "listElementz", 1))
        assert recorded != recomputed
