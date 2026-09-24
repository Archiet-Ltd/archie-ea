"""Deploy generated solutions via Docker on the Entelim server.

This is the ONLY file that knows about the deployment backend. If we swap to
Coolify, CapRover, or Kubernetes later, only this file changes. The public API
(deploy, health_check, destroy, redeploy) stays identical.

Current backend: Docker CLI on localhost (direct, zero overhead).
"""
import logging
import os
import secrets
import shutil
import subprocess
import tempfile
from datetime import datetime

from flask import current_app

from app.extensions import db
from app.modules.codegen.models import SolutionInstance
from app.modules.codegen.services.credential_encryption import encrypt_credential

logger = logging.getLogger(__name__)

_DEPLOY_ROOT = os.path.join(tempfile.gettempdir(), "archie-deployments") if os.name == "nt" else "/opt/archie-deployments"
_BASE_PORT = 9200  # First deployed solution gets port 9200, next 9201, etc.
_MAX_PORT = 9299   # Max 100 concurrent deployments

_STATUS_MAP = {
    "running": "healthy",
    "starting": "deploying",
    "created": "deploying",
    "restarting": "deploying",
    "stopped": "stopped",
    "paused": "stopped",
    "exited": "unhealthy",
    "error": "unhealthy",
    "dead": "unhealthy",
}


_DOCKER = shutil.which("docker") or "/usr/bin/docker"


def _run(cmd: list[str], cwd: str | None = None, timeout: int = 120) -> str:
    """Run a shell command, return stdout. Raises on failure."""
    # Resolve 'docker' to absolute path so it works inside venv/gunicorn
    if cmd and cmd[0] == "docker":
        cmd = [_DOCKER] + cmd[1:]
    env = {**os.environ, "PATH": f"/usr/local/bin:/usr/bin:/bin:{os.environ.get('PATH', '')}"}
    result = subprocess.run(
        cmd, capture_output=True, text=True, cwd=cwd, timeout=timeout, env=env
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed ({' '.join(cmd[:3])}...): {result.stderr[:500]}"
        )
    return result.stdout.strip()


def _find_free_port() -> int:
    """Find the next available port in the deployment range. Works on all OS."""
    import socket
    for port in range(_BASE_PORT, _MAX_PORT + 1):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("0.0.0.0", port))
                return port
        except OSError:
            continue
    raise RuntimeError(f"No free ports in range {_BASE_PORT}-{_MAX_PORT}")


def recommend_language(elements: list[dict]) -> dict:
    """Recommend a programming language based on solution architecture elements.

    Returns {"language": str, "reason": str}.
    """
    if not elements:
        return {
            "language": "python-fastapi",
            "reason": (
                "Default recommendation: Python/FastAPI has the best template "
                "coverage (47 templates) and supports all standard CRUD patterns."
            ),
        }

    tech_stacks = []
    api_styles = []
    for el in elements:
        props = el.get("properties", {})
        if props.get("technology_stack"):
            tech_stacks.append(props["technology_stack"].lower())
        if props.get("api_style"):
            api_styles.append(props["api_style"].lower())

    salesforce_count = sum(1 for t in tech_stacks if "salesforce" in t)
    if salesforce_count > 0 and (
        not tech_stacks or salesforce_count >= len(tech_stacks) * 0.4
    ):
        return {
            "language": "salesforce-apex",
            "reason": (
                f"Your architecture references Salesforce in "
                f"{salesforce_count}/{len(elements)} elements. Salesforce Apex "
                f"is the native language for this platform."
            ),
        }

    java_count = sum(1 for t in tech_stacks if "java" in t or "spring" in t)
    if java_count > 0 and (
        not tech_stacks or java_count >= len(tech_stacks) * 0.4
    ):
        return {
            "language": "java-spring-boot",
            "reason": (
                f"Your architecture references Java/Spring in "
                f"{java_count}/{len(elements)} elements. Java Spring Boot "
                f"aligns with your enterprise technology stack."
            ),
        }

    ws_count = sum(
        1
        for a in api_styles
        if "websocket" in a or "streaming" in a or "grpc" in a
    )
    if ws_count > 0 and (not api_styles or ws_count >= len(api_styles) * 0.3):
        return {
            "language": "go-chi",
            "reason": (
                f"Your architecture has {ws_count} real-time/WebSocket "
                f"components. Go excels at concurrent connections and "
                f"streaming workloads."
            ),
        }

    return {
        "language": "python-fastapi",
        "reason": (
            "Recommended: Python/FastAPI has the best template coverage "
            "(47 templates) for REST APIs, CRUD operations, and standard "
            "web services."
        ),
    }


class DeploymentOrchestrator:
    """Deploy generated solutions via Docker or local subprocess fallback.

    Uses Docker when available (production). Falls back to running the
    generated FastAPI app directly via uvicorn subprocess when Docker is
    not installed (local development / Windows).
    """

    def __init__(self):
        self._deploy_root = current_app.config.get(
            "DEPLOY_ROOT", _DEPLOY_ROOT
        )
        self._server_ip = current_app.config.get(
            "SERVER_PUBLIC_IP", "localhost"
        )
        self._has_docker = shutil.which("docker") is not None

    def deploy(
        self,
        solution_id: int,
        language: str,
        code_files: dict[str, str],
    ) -> SolutionInstance:
        """Deploy generated code. Uses Docker if available, else uvicorn subprocess."""
        container_name = f"archie-solution-{solution_id}"
        deploy_dir = os.path.join(self._deploy_root, container_name)

        # Write code files to deploy directory (UTF-8 for cross-platform safety)
        os.makedirs(deploy_dir, exist_ok=True)
        for filepath, content in code_files.items():
            full_path = os.path.join(deploy_dir, filepath)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)

        # Ensure Dockerfile exists
        if "Dockerfile" not in code_files:
            self._write_default_dockerfile(deploy_dir, language)

        # Ensure requirements.txt exists for Python
        if language.startswith("python") and "requirements.txt" not in code_files:
            self._write_default_requirements(deploy_dir)

        # Ensure /health endpoint exists
        if "main.py" in code_files and "/health" not in code_files["main.py"]:
            self._inject_health_endpoint(deploy_dir)

        # Patch requirements.txt: ensure common missing deps are present
        self._patch_requirements(deploy_dir)

        port = _find_free_port()

        if self._has_docker:
            self._deploy_docker(container_name, deploy_dir, solution_id, port)
        else:
            self._deploy_subprocess(container_name, deploy_dir, solution_id, port)

        deployment_url = f"http://{self._server_ip}:{port}"
        db_url = f"sqlite:///solution_{solution_id}.db"

        # Create or update instance record
        existing = SolutionInstance.query.filter_by(
            solution_id=solution_id
        ).first()

        if existing:
            existing.coolify_project_id = str(port)  # Store port for reference
            existing.coolify_service_id = container_name
            existing.deployment_url = deployment_url
            existing.database_url_encrypted = encrypt_credential(db_url)
            existing.health_status = "deploying"
            existing.version = existing.version + 1
            existing.updated_at = datetime.utcnow()
            instance = existing
        else:
            instance = SolutionInstance(
                solution_id=solution_id,
                coolify_project_id=str(port),
                coolify_service_id=container_name,
                deployment_url=deployment_url,
                database_url_encrypted=encrypt_credential(db_url),
                health_status="deploying",
            )
            db.session.add(instance)

        db.session.commit()
        logger.info(
            "Deployed solution %d as %s on port %d",
            solution_id, container_name, port,
        )
        return instance

    # ── Deploy backends ─────────────────────────────────────────────────────

    def _deploy_docker(self, container_name, deploy_dir, solution_id, port):
        """Deploy via Docker (production)."""
        try:
            _run(["docker", "rm", "-f", container_name])
        except RuntimeError:
            pass

        build_cmd = ["docker", "build", "-t", container_name]
        dockerfile_path = os.path.join(deploy_dir, "Dockerfile")
        if os.path.exists(dockerfile_path):
            with open(dockerfile_path) as f:
                if "AS run" in f.read():
                    build_cmd.extend(["--target", "run"])
        build_cmd.append(".")
        _run(build_cmd, cwd=deploy_dir, timeout=300)

        # G1: generate a cryptographically random secret per deployment — never derive
        # from solution_id (sequential integer) which makes all JWT tokens forgeable.
        secret_key = secrets.token_hex(32)

        # G2: use a named Docker volume so SQLite survives container restarts and
        # redeploys. /tmp inside a container is ephemeral and destroyed on restart.
        volume_name = f"archie-sol-{solution_id}-data"
        db_url = "sqlite+aiosqlite:////app/data/solution.db"

        # G3: run Alembic migration to completion before starting the server.
        # The default CMD starts uvicorn immediately, so tables are never created
        # and the first real API call fails with "relation does not exist".
        try:
            _run([
                "docker", "run", "--rm",
                "--name", f"{container_name}-migrate",
                "-v", f"{volume_name}:/app/data",
                "-e", f"DATABASE_URL={db_url}",
                "-e", f"SECRET_KEY={secret_key}",
                "-e", "ENV=production",
                container_name,
                "alembic", "upgrade", "head",
            ], timeout=120)
        except RuntimeError as _mig_err:
            logger.warning("Alembic migration step failed (tables may already exist): %s", _mig_err)

        _run([
            "docker", "run", "-d",
            "--name", container_name,
            "-p", f"{port}:8000",
            "-v", f"{volume_name}:/app/data",
            "-e", f"DATABASE_URL={db_url}",
            "-e", f"SECRET_KEY={secret_key}",
            "-e", "ENV=production",
            "--restart", "unless-stopped",
            container_name,
        ])

    def _deploy_subprocess(self, container_name, deploy_dir, solution_id, port):
        """Deploy via uvicorn subprocess (local dev / no Docker)."""
        import sys

        # Kill any existing process on this port
        self._kill_process_on_port(port)

        # Install deps from requirements.txt
        req_path = os.path.join(deploy_dir, "requirements.txt")
        if os.path.exists(req_path):
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-q", "-r", req_path],
                cwd=deploy_dir, capture_output=True, timeout=120,
            )

        # Find the uvicorn app target
        app_target = self._find_app_target(deploy_dir)

        # Set env vars and start uvicorn as background process
        env = {
            **os.environ,
            "DATABASE_URL": f"sqlite+aiosqlite:///{os.path.join(deploy_dir, 'solution.db')}",
            "SECRET_KEY": secrets.token_hex(32),  # G1: never derive from predictable solution_id
            "ENV": "production",
            "PYTHONPATH": deploy_dir,
        }

        log_path = os.path.join(deploy_dir, "server.log")
        log_file = open(log_path, "w")

        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", app_target,
             "--host", "0.0.0.0", "--port", str(port)],
            cwd=deploy_dir, env=env,
            stdout=log_file, stderr=log_file,
        )

        # Save PID for later cleanup
        pid_path = os.path.join(deploy_dir, "server.pid")
        with open(pid_path, "w") as f:
            f.write(str(proc.pid))

        logger.info("Started uvicorn subprocess pid=%d on port %d", proc.pid, port)

    def _find_app_target(self, deploy_dir: str) -> str:
        """Find the uvicorn app target string (e.g. 'app.main:app' or 'main:app')."""
        # Check app/main.py first (generated by DeterministicCodeGenerator)
        if os.path.exists(os.path.join(deploy_dir, "app", "main.py")):
            return "app.main:app"
        if os.path.exists(os.path.join(deploy_dir, "main.py")):
            return "main:app"
        # Fallback: scan for FastAPI() instantiation
        for root, _, files in os.walk(deploy_dir):
            for fname in files:
                if fname.endswith(".py"):
                    try:
                        with open(os.path.join(root, fname)) as f:
                            if "FastAPI()" in f.read() or "FastAPI(" in f.read():
                                rel = os.path.relpath(os.path.join(root, fname), deploy_dir)
                                module = rel.replace(os.sep, ".").replace(".py", "")
                                return f"{module}:app"
                    except Exception as exc:
                        logger.debug("suppressed error in DeploymentOrchestrator._find_app_target (app/modules/codegen/services/deployment_orchestrator.py): %s", exc)
        return "main:app"

    def _kill_process_on_port(self, port: int):
        """Kill any process listening on the given port."""
        try:
            if os.name == "nt":
                output = subprocess.run(
                    ["netstat", "-ano"], capture_output=True, text=True
                ).stdout
                for line in output.splitlines():
                    if f":{port}" in line and "LISTENING" in line:
                        pid = line.strip().split()[-1]
                        subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
            else:
                output = subprocess.run(
                    ["lsof", "-ti", f":{port}"], capture_output=True, text=True
                ).stdout.strip()
                if output:
                    for pid in output.splitlines():
                        subprocess.run(["kill", "-9", pid], capture_output=True)
        except Exception as e:
            logger.warning("Could not kill process on port %d: %s", port, e)

    # ── Health check / Destroy / Redeploy ────────────────────────────────

    def health_check(self, instance_id: int) -> str:
        """Check health of a deployed instance (Docker or subprocess)."""
        instance = db.session.get(SolutionInstance, instance_id)
        if not instance:
            raise ValueError(f"No SolutionInstance with id={instance_id}")

        # Try HTTP health check first — works for both backends
        if instance.deployment_url:
            try:
                import requests as req
                resp = req.get(f"{instance.deployment_url}/health", timeout=5)
                instance.health_status = "healthy" if resp.status_code == 200 else "unhealthy"
            except Exception:
                # Check if process/container is still running
                if self._has_docker:
                    try:
                        output = _run(["docker", "inspect", "--format", "{{.State.Status}}", instance.coolify_service_id])
                        instance.health_status = _STATUS_MAP.get(output.strip().lower(), "unhealthy")
                    except RuntimeError:
                        instance.health_status = "stopped"
                else:
                    instance.health_status = "stopped" if not self._is_pid_alive(instance) else "deploying"

        instance.last_health_check = datetime.utcnow()
        db.session.commit()
        return instance.health_status

    def destroy(self, instance_id: int) -> None:
        """Stop and remove a deployed instance."""
        instance = db.session.get(SolutionInstance, instance_id)
        if not instance:
            raise ValueError(f"No SolutionInstance with id={instance_id}")

        container_name = instance.coolify_service_id
        if self._has_docker and container_name:
            try:
                _run(["docker", "rm", "-f", container_name])
            except RuntimeError:
                pass
        else:
            # Kill subprocess
            port = instance.coolify_project_id
            if port:
                self._kill_process_on_port(int(port))
            # Also try PID file
            deploy_dir = os.path.join(self._deploy_root, container_name or "")
            pid_path = os.path.join(deploy_dir, "server.pid")
            if os.path.exists(pid_path):
                try:
                    with open(pid_path) as f:
                        pid = int(f.read().strip())
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
                    else:
                        subprocess.run(["kill", "-9", str(pid)], capture_output=True)
                except Exception as exc:
                    logger.debug("suppressed error in DeploymentOrchestrator.destroy (app/modules/codegen/services/deployment_orchestrator.py): %s", exc)

        instance.health_status = "stopped"
        instance.updated_at = datetime.utcnow()
        db.session.commit()

    def redeploy(self, instance_id: int, code_files: dict[str, str]) -> SolutionInstance:
        """Redeploy an existing instance with updated code."""
        instance = db.session.get(SolutionInstance, instance_id)
        if not instance:
            raise ValueError(f"No SolutionInstance with id={instance_id}")

        # Destroy old, then deploy fresh
        self.destroy(instance_id)

        container_name = instance.coolify_service_id
        deploy_dir = os.path.join(self._deploy_root, container_name)

        for filepath, content in code_files.items():
            full_path = os.path.join(deploy_dir, filepath)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)

        port = int(instance.coolify_project_id)
        if self._has_docker:
            self._deploy_docker(container_name, deploy_dir, instance.solution_id, port)
        else:
            self._deploy_subprocess(container_name, deploy_dir, instance.solution_id, port)

        instance.health_status = "deploying"
        instance.version = instance.version + 1
        instance.updated_at = datetime.utcnow()
        db.session.commit()
        return instance

    def _is_pid_alive(self, instance) -> bool:
        """Check if the subprocess PID is still running."""
        deploy_dir = os.path.join(self._deploy_root, instance.coolify_service_id or "")
        pid_path = os.path.join(deploy_dir, "server.pid")
        if not os.path.exists(pid_path):
            return False
        try:
            with open(pid_path) as f:
                pid = int(f.read().strip())
            if os.name == "nt":
                result = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True)
                return str(pid) in result.stdout
            else:
                os.kill(pid, 0)
                return True
        except Exception:
            return False

    def _write_default_dockerfile(self, deploy_dir: str, language: str):
        """Write a default Dockerfile if none provided."""
        dockerfile = (
            "FROM python:3.12-slim\n"
            "WORKDIR /app\n"
            "COPY requirements.txt .\n"
            "RUN pip install --no-cache-dir -r requirements.txt\n"
            "COPY . .\n"
            "EXPOSE 8000\n"
            'CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]\n'
        )
        with open(os.path.join(deploy_dir, "Dockerfile"), "w") as f:
            f.write(dockerfile)

    def _write_default_requirements(self, deploy_dir: str):
        """Write default requirements.txt if none provided."""
        reqs = "fastapi==0.115.0\nuvicorn[standard]==0.30.0\n"
        req_path = os.path.join(deploy_dir, "requirements.txt")
        if not os.path.exists(req_path):
            with open(req_path, "w") as f:
                f.write(reqs)

    def _patch_requirements(self, deploy_dir: str):
        """Ensure requirements.txt includes commonly missing dependencies."""
        req_path = os.path.join(deploy_dir, "requirements.txt")
        if not os.path.exists(req_path):
            return
        with open(req_path, "r") as f:
            content = f.read()
        # Scan generated code for imports that need extra deps
        patches = []
        for root, _, files in os.walk(deploy_dir):
            for fname in files:
                if fname.endswith(".py"):
                    try:
                        with open(os.path.join(root, fname)) as pf:
                            code = pf.read()
                        if "EmailStr" in code and "email-validator" not in content:
                            patches.append("email-validator>=2.0.0")
                        if "import httpx" in code and "httpx" not in content:
                            patches.append("httpx>=0.27.0")
                    except Exception as exc:
                        logger.debug("suppressed error in DeploymentOrchestrator._patch_requirements (app/modules/codegen/services/deployment_orchestrator.py): %s", exc)
        if patches:
            with open(req_path, "a") as f:
                for dep in set(patches):
                    f.write(f"\n{dep}")
            logger.info("Patched requirements.txt with: %s", patches)

    def _inject_health_endpoint(self, deploy_dir: str):
        """Ensure main.py has a /health endpoint."""
        main_path = os.path.join(deploy_dir, "main.py")
        if os.path.exists(main_path):
            with open(main_path, "r") as f:
                content = f.read()
            if "/health" not in content:
                content += (
                    '\n\n@app.get("/health")\n'
                    "def health():\n"
                    '    return {"status": "healthy"}\n'
                )
                with open(main_path, "w") as f:
                    f.write(content)
