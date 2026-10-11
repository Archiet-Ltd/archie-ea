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
        cwd=kwargs.get("cwd", REPO_ROOT),
        timeout=kwargs.get("timeout", 60),
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
    return hashlib.sha256(text.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Generator tests
# ---------------------------------------------------------------------------


class TestGenerateApiContractSubset:
    """Tests for scripts/generate_api_contract_subset.py."""

    def test_generator_exists(self):
        """The generator script must exist."""
        script = REPO_ROOT / "scripts" / "generate_api_contract_subset.py"
        assert script.exists(), f"{script} does not exist"

    def test_generator_filters_by_group(self, tmp_path: Path):
        """Only operations whose first path segment is in PUBLISHED_GROUPS
        are kept. /capabilities must be excluded when only 'elements' is
        published."""
        contract = _minimal_contract()
        # Add a /capabilities path that should be filtered out
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
            }
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
        # Must NOT have /capabilities
        assert "/capabilities" not in subset["paths"], (
            "/capabilities should have been filtered out"
        )

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
        """Regenerating the subset from the canonical contract copy (named by
        API_CONTRACT_SOURCE) must reproduce the committed
        app/config/api_contract_subset.yaml byte for byte.

        Skipped, with the reason shown, when API_CONTRACT_SOURCE is not set
        or names a missing file.
        """
        source = os.environ.get("API_CONTRACT_SOURCE")
        if not source:
            pytest.skip(
                "API_CONTRACT_SOURCE is not set; cannot regenerate the subset "
                "from the canonical contract copy"
            )
        source_path = Path(source)
        if not source_path.exists():
            pytest.skip(f"API_CONTRACT_SOURCE names a missing file: {source}")

        out = tmp_path / "regenerated.yaml"
        result = _run(
            sys.executable,
            str(REPO_ROOT / "scripts" / "generate_api_contract_subset.py"),
            "--source", str(source_path),
            "--output", str(out),
        )
        assert result.returncode == 0, f"Generator failed:\n{result.stderr}"

        committed = REPO_ROOT / "app" / "config" / "api_contract_subset.yaml"
        assert committed.exists(), "No committed subset to compare against"
        assert out.read_bytes() == committed.read_bytes(), (
            "Regenerated subset differs from the committed subset byte for "
            "byte; regenerate it with scripts/generate_api_contract_subset.py "
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


class TestCheckInterfaceContract:
    """Tests for scripts/check_interface_contract.py."""

    def test_gate_script_exists(self):
        """The gate script must exist."""
        script = REPO_ROOT / "scripts" / "check_interface_contract.py"
        assert script.exists(), f"{script} does not exist"

    def test_gate_passes_on_real_app_with_committed_subset(self):
        """The gate must pass on the real application with the committed subset
        (both elements routes are listed and match)."""
        subset_path = REPO_ROOT / "app" / "config" / "api_contract_subset.yaml"
        if not subset_path.exists():
            pytest.skip("Committed subset not yet generated; run the generator first")

        result = _run(
            sys.executable,
            str(REPO_ROOT / "scripts" / "check_interface_contract.py"),
            "--subset", str(subset_path),
        )
        assert result.returncode == 0, (
            f"Gate failed on real app:\n{result.stdout}\n{result.stderr}"
        )

    def test_unlisted_route_fails_check_i(self, tmp_path: Path):
        """A fixture application with a token-accepting route that is not in
        the subset must make check (i) fail and name the route."""
        subset = _minimal_contract()
        # Remove /elements/{id} from the subset
        del subset["paths"]["/elements/{id}"]
        subset_path = tmp_path / "subset.yaml"
        _write_contract(subset_path, subset)

        result = _run(
            sys.executable,
            str(REPO_ROOT / "scripts" / "check_interface_contract.py"),
            "--subset", str(subset_path),
            "--fixture-app",
        )
        assert result.returncode != 0, (
            "Gate should fail when a route is missing from the subset"
        )
        assert "elements/{element_id}" in result.stdout or "GET /api/v1/elements" in result.stdout, (
            f"Missing route should be named in output:\n{result.stdout}"
        )

    def test_write_scope_against_read_only_operation_fails(self, tmp_path: Path):
        """A route tagged api:write against a read-only contract operation
        must fail check (i)."""
        subset = _minimal_contract()
        # Both operations have model:read scope (read-class)
        subset_path = tmp_path / "subset.yaml"
        _write_contract(subset_path, subset)

        result = _run(
            sys.executable,
            str(REPO_ROOT / "scripts" / "check_interface_contract.py"),
            "--subset", str(subset_path),
            "--fixture-app",
            "--fixture-route-scope", "api:write",
        )
        assert result.returncode != 0, (
            "Gate should fail when a write-scoped route has no matching "
            "non-read operation in the subset"
        )

    def test_read_scope_against_write_only_operation_fails(self, tmp_path: Path):
        """A route tagged api:read against an operation requiring a non-read
        scope must fail check (i)."""
        subset = _minimal_contract()
        # Change the security scope to a write-class scope
        for path_key in ("/elements", "/elements/{id}"):
            sec = subset["paths"][path_key]["get"].get("security", [])
            for entry in sec:
                for key, scopes in entry.items():
                    entry[key] = ["model:write"]
        subset_path = tmp_path / "subset.yaml"
        _write_contract(subset_path, subset)

        result = _run(
            sys.executable,
            str(REPO_ROOT / "scripts" / "check_interface_contract.py"),
            "--subset", str(subset_path),
            "--fixture-app",
            "--fixture-route-scope", "api:read",
        )
        assert result.returncode != 0, (
            "Gate should fail when a read-scoped route has no matching "
            "read-class operation in the subset"
        )

    def test_unbuilt_operation_counted_by_check_ii(self, tmp_path: Path):
        """A subset operation with no route is counted by check (ii) and does
        not fail the build while the count is at most the baseline."""
        subset = _minimal_contract()
        # Add an extra operation that has no route in the app
        subset["paths"]["/extra"] = {
            "get": {
                "operationId": "listExtra",
                "summary": "List extra things",
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
        }
        subset_path = tmp_path / "subset.yaml"
        _write_contract(subset_path, subset)

        # With baseline=1, should pass
        result = _run(
            sys.executable,
            str(REPO_ROOT / "scripts" / "check_interface_contract.py"),
            "--subset", str(subset_path),
            "--fixture-app",
            "--baseline", "1",
        )
        assert result.returncode == 0, (
            f"Gate should pass when unbuilt count (1) <= baseline (1):\n{result.stdout}"
        )

        # With baseline=0, should fail
        result = _run(
            sys.executable,
            str(REPO_ROOT / "scripts" / "check_interface_contract.py"),
            "--subset", str(subset_path),
            "--fixture-app",
            "--baseline", "0",
        )
        assert result.returncode != 0, (
            "Gate should fail when unbuilt count (1) > baseline (0)"
        )

    def test_gate_supports_count_flag(self, tmp_path: Path):
        """The --count flag should return a single integer on stdout."""
        subset_path = REPO_ROOT / "app" / "config" / "api_contract_subset.yaml"
        if not subset_path.exists():
            pytest.skip("Committed subset not yet generated")

        result = _run(
            sys.executable,
            str(REPO_ROOT / "scripts" / "check_interface_contract.py"),
            "--subset", str(subset_path),
            "--count",
        )
        assert result.returncode == 0
        lines = [ln for ln in result.stdout.strip().splitlines() if ln.strip()]
        assert lines, "No output from --count"
        int(lines[-1])  # Must parse as int

    def test_gate_reports_unlisted_route_reason(self, tmp_path: Path):
        """The failure output must include the route path and the reason."""
        subset = _minimal_contract()
        del subset["paths"]["/elements/{id}"]
        subset_path = tmp_path / "subset.yaml"
        _write_contract(subset_path, subset)

        result = _run(
            sys.executable,
            str(REPO_ROOT / "scripts" / "check_interface_contract.py"),
            "--subset", str(subset_path),
            "--fixture-app",
        )
        output = result.stdout + result.stderr
        assert "unlisted" in output.lower() or "missing" in output.lower() or "no matching" in output.lower(), (
            f"Output should explain why the route is unlisted:\n{output}"
        )


# ---------------------------------------------------------------------------
# Verify.py registration tests
# ---------------------------------------------------------------------------


class TestVerifyRegistration:
    """Tests that the gates are registered in scripts/verify.py."""

    def test_interface_contract_gates_registered(self):
        """Both gates must be registered in scripts/verify.py's build_gates()."""
        text = (REPO_ROOT / "scripts" / "verify.py").read_text(encoding="utf-8")
        assert 'interface-contract-unlisted' in text, (
            "Gate 'interface-contract-unlisted' not found in verify.py"
        )
        assert 'interface-contract-unimplemented' in text, (
            "Gate 'interface-contract-unimplemented' not found in verify.py"
        )

    def test_interface_contract_gates_tagged_static(self):
        """Both gates must be tagged 'static'."""
        text = (REPO_ROOT / "scripts" / "verify.py").read_text(encoding="utf-8")
        # Find the gate entries and check for static tag
        assert 'tags=["static"' in text or "'static'" in text, (
            "Gates should be tagged static"
        )


# ---------------------------------------------------------------------------
# Reuse check tests
# ---------------------------------------------------------------------------


class TestReuse:
    """No second implementation of existing mechanisms."""

    def test_single_route_extractor(self):
        """extract_route_info in app/utils/openapi_generator.py is the only
        route extractor used."""
        text = (REPO_ROOT / "scripts" / "check_interface_contract.py").read_text(
            encoding="utf-8"
        )
        assert "extract_route_info" in text, (
            "Gate must reuse extract_route_info from openapi_generator"
        )

    def test_single_marker(self):
        """_api_token_scope is the only marker used."""
        text = (REPO_ROOT / "scripts" / "check_interface_contract.py").read_text(
            encoding="utf-8"
        )
        assert "_api_token_scope" in text, (
            "Gate must reuse _api_token_scope from require_api_scope"
        )