"""Reads the SysML v2 system model: parses the textual files with the open-source Pilot Implementation
parser and returns the parsed elements as Python objects the checks can read.

The parser is a Java program. It is not part of the repository: run scripts/sysml/install_parser.sh once, or
point SYSML_HOME at a folder that holds `jdk*/`, `kernel/sysml/` (the Pilot Implementation release) and
nothing else is needed. The compiled helper (Validate.java in this folder) is built on first use.
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MODEL_DIR = REPO / "model"
VOCAB = "EntelimVocabulary::"
# Files in the order the parser must read them (a file may import only from earlier ones). as-built is generated.
AUTHORED_DIRS = ("library", "requirements", "architecture")


class ParserMissing(RuntimeError):
    pass


def sysml_home() -> Path:
    return Path(os.environ.get("SYSML_HOME") or Path.home() / ".cache" / "entelim-sysml")


def _tools():
    home = sysml_home()
    javas = sorted(glob.glob(str(home / "jdk*" / "bin" / "java")))
    jars = sorted(glob.glob(str(home / "kernel" / "sysml" / "*-all.jar")))
    lib = home / "kernel" / "sysml" / "sysml.library"
    if not javas or not jars or not lib.is_dir():
        raise ParserMissing(
            f"SysML parser not found under {home}. Run scripts/sysml/install_parser.sh (free, about 480 MB), "
            "or set SYSML_HOME to a folder that holds jdk*/ and kernel/sysml/."
        )
    return Path(javas[0]), Path(jars[0]), lib


def _helper_classes(java: Path, jar: Path) -> Path:
    """Compile Validate.java next to the parser the first time it is needed."""
    out = sysml_home() / "classes"
    src = Path(__file__).with_name("Validate.java")
    cls = out / "Validate.class"
    if cls.exists() and cls.stat().st_mtime >= src.stat().st_mtime:
        return out
    out.mkdir(parents=True, exist_ok=True)
    javac = java.with_name("javac")
    r = subprocess.run([str(javac), "-cp", str(jar), "-d", str(out), str(src)], capture_output=True, text=True)
    if r.returncode:
        raise ParserMissing("could not compile Validate.java: " + r.stderr[-500:])
    return out


def model_files(root: Path = MODEL_DIR, as_built: Path | None = None) -> list[Path]:
    """Authored files, then the generated as-built files (from `as_built`, default root/as-built)."""
    files: list[Path] = []
    for d in AUTHORED_DIRS:
        files += sorted((root / d).glob("*.sysml"))
    files += sorted((as_built or root / "as-built").glob("*.sysml"))
    return files


def parse(files: list[Path], dump: Path | None = None) -> tuple[int, str]:
    """Parse and validate the files in order. Returns (exit code, the parser's output ending in its result line)."""
    java, jar, lib = _tools()
    classes = _helper_classes(java, jar)
    cmd = [str(java), "-cp", f"{classes}{os.pathsep}{jar}", "Validate"]
    if dump:
        cmd += ["--dump", str(dump)]
    cmd += [str(lib)] + [str(f) for f in files]
    r = subprocess.run(cmd, capture_output=True, text=True)
    lines = [ln for ln in (r.stdout + r.stderr).splitlines() if not ln.startswith(("Reading ", "log4j"))]
    return r.returncode, "\n".join(lines)


@dataclass
class Model:
    elements: dict = field(default_factory=dict)
    children: dict = field(default_factory=dict)
    elements_list: list = field(default_factory=list)

    def attr(self, qn: str, name: str):
        """Value of the attribute `name` set on element `qn` (a redefinition such as `:>> reqId = "x"`)."""
        for c in self.children.get(qn, []):
            sup = (c.get("supers") or c.get("refs") or [""])[0]
            if sup.rsplit("::", 1)[-1] == name and "value" in c:
                return c["value"]
        return None

    def of_type(self, type_qn: str, kind: str | None = None):
        for e in self.elements.values():
            if type_qn in (e.get("types") or []) and (kind is None or e["kind"] == kind):
                yield e

    # ---- the constructs the checks read -------------------------------------------------------------
    def requirements(self):
        out = {}
        for e in self.of_type(VOCAB + "EntelimRequirement", "RequirementUsage"):
            out[e["qn"]] = {
                "qn": e["qn"],
                "id": self.attr(e["qn"], "reqId"),
                "text": " ".join((e.get("doc") or "").split()),
                "parent": self.attr(e["qn"], "parent") or "",
                "ruleLine": self.attr(e["qn"], "ruleLine") or "",
                "job": self.attr(e["qn"], "job") or "",
                "ownerRole": self.attr(e["qn"], "ownerRole") or "",
                "rationale": self.attr(e["qn"], "rationale") or "",
                "asked": self.attr(e["qn"], "asked") or "",
                "method": self.method(e["qn"]),
                "file": e.get("file"),
            }
        return out

    def method(self, qn: str):
        for c in self.children.get(qn, []):
            sup = (c.get("supers") or c.get("refs") or [""])[0]
            if sup.endswith("::method"):
                return c.get("value") or self._enum_ref(c)
        return None

    @staticmethod
    def _enum_ref(c):
        return None

    def satisfies(self):
        """[(requirement qn, satisfying part qn)]"""
        return [(e["satisfied"], e["by"]) for e in self.elements_list if e["kind"] == "SatisfyRequirementUsage"]

    def parts(self):
        out = {}
        for e in self.elements.values():
            if e["kind"] == "PartUsage" and any(t.startswith(VOCAB) for t in e.get("types", [])):
                out[e["qn"]] = {
                    "qn": e["qn"],
                    "name": e["name"],
                    "type": e["types"][0].split("::")[-1],
                    "sourcePath": self.attr(e["qn"], "sourcePath") or self.attr(e["qn"], "template") or "",
                    "ports": {
                        p["name"]: {k: self.attr(p["qn"], k) for k in ("rule", "methods", "endpoint", "target")}
                        for p in self.children.get(e["qn"], [])
                        if p["kind"] == "PortUsage"
                    },
                }
        return out

    def allocations(self):
        return [tuple(e["ends"]) for e in self.elements_list if e["kind"] == "AllocationUsage" and len(e.get("ends", [])) == 2]

    def interfaces(self):
        return [e for e in self.elements_list if e["kind"] == "InterfaceUsage"]

    def functions(self):
        return {e["qn"]: e for e in self.elements_list if e["kind"] == "ActionUsage" and VOCAB + "ProductFunction" in e.get("types", [])}

    def constraints(self):
        return {
            e["qn"]: {"qn": e["qn"], "canonicalSource": self.attr(e["qn"], "canonicalSource"),
                      "column": self.attr(e["qn"], "columnName"), "doc": e.get("doc", "")}
            for e in self.of_type(VOCAB + "OneSource", "ConstraintUsage")
        }

    def items(self):
        return {e["qn"]: e for e in self.of_type(VOCAB + "StoredRecord", "ItemUsage")}

    def cases(self):
        out = {}
        for e in self.elements_list:
            if e["kind"] != "VerificationCaseUsage":
                continue
            verifies = []
            for c in self.elements_list:
                if c["kind"] == "RequirementUsage" and c["qn"].startswith(e["qn"] + "::obj::"):
                    verifies += [r for r in c.get("refs", []) if r]
            out[e["qn"]] = {
                "qn": e["qn"],
                "testFile": self.attr(e["qn"], "testFile"),
                "testName": self.attr(e["qn"], "testName"),
                "verifies": sorted(set(verifies)),
            }
        return out


def load(dump: Path) -> Model:
    m = Model()
    for ln in dump.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        e = json.loads(ln)
        m.elements_list.append(e)
        if e.get("qn"):
            m.elements[e["qn"]] = e
        if e.get("owner"):
            m.children.setdefault(e["owner"], []).append(e)
    return m


def parse_model(root: Path = MODEL_DIR, as_built: Path | None = None) -> tuple[Model, str]:
    """Parse every model file; stop if the parser reports an error. Returns the model and the parser's result line."""
    files = model_files(root, as_built)
    with tempfile.TemporaryDirectory() as td:
        dump = Path(td) / "model.jsonl"
        code, out = parse(files, dump)
        if code:
            print(out, file=sys.stderr)
            raise SystemExit("The SysML files do not parse: fix the errors above.")
        return load(dump), out.splitlines()[-1]
