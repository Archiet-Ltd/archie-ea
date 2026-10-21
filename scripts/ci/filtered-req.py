#!/usr/bin/env python3
"""Filter requirements.txt to drop the heavy packages the CI test jobs do not need.

Excludes: torch, sentence-transformers, transformers, Flask-RQ, flasgger,
raygun4py, jsmin. Torch, triton and the CUDA libraries arrive only through
sentence-transformers, so dropping it keeps several GB per job off the runners'
disks; the application imports each of these lazily or behind a guard.

Usage: python scripts/ci/filtered-req.py requirements.txt [output.txt]

The script reads requirements.txt, removes lines whose package name matches an
excluded name (case-sensitive), and writes the result to stdout or
an output file. This replaces the ad-hoc grep -viE pattern previously repeated
in each CI job.
"""

import sys

EXCLUDED_PREFIXES = (
    "torch",
    "sentence-transformers",
    "transformers",
    "Flask-RQ",
    "flasgger",
    "raygun4py",
    "jsmin",
)


def filter_requirements(input_path: str, output_path: str | None = None) -> str:
    """Return requirements.txt content with excluded ML packages removed.

    Lines starting (after stripping leading whitespace) with one of the EXCLUDED_PREFIXES
    are dropped. Blank lines and comments are preserved.
    """
    excluded = set(EXCLUDED_PREFIXES)
    kept: list[str] = []

    with open(input_path, "r") as fh:
        lines = fh.readlines()

    for line in lines:
        stripped = line.lstrip()
        # Skip the package name: everything before the first whitespace or version spec
        package_name = stripped.split()[0] if stripped else ""
        # Remove trailing commas, version pins, comments for matching
        pkg_for_match = package_name.split(",")[0].split(">=")[0].split("<=")[0].split("~=")[0].split("!=")[0]
        if pkg_for_match in excluded:
            continue
        kept.append(line)

    result = "".join(kept)
    if output_path:
        with open(output_path, "w") as fh:
            fh.write(result)
    return result


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python scripts/ci/filtered-req.py requirements.txt [output.txt]", file=sys.stderr)
        return 1

    input_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else None

    result = filter_requirements(input_path, output_path)
    if not output_path:
        print(result, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())