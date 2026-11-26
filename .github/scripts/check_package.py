"""Check a built wheel against pyproject.toml.

Verifies that the wheel contains only the package and its metadata, no
development or local files, and that its name, version, Python requirement and
console script match pyproject.toml.

Usage: python check_package.py pyproject.toml dist/
"""

import sys
import tomllib
import zipfile
from email.parser import Parser
from pathlib import Path

PACKAGE = "network_diagnostic"
# Modules that must be present: the package markers, both entry points and the service layer.
REQUIRED_FILES = (
    "network_diagnostic/__init__.py",
    "network_diagnostic/__main__.py",
    "network_diagnostic/diagnostic_service.py",
    "network_diagnostic/cli/main.py",
    "network_diagnostic/core/command_runner.py",
)
# Name parts that must never be packaged (agent instructions, local settings, plans, secrets).
FORBIDDEN_PARTS = (
    "agents.md", "claude", ".codex", ".mcp", ".env", ".git", "docs", "tests", "docker",
    "requirements", ".coverage", "htmlcov", "__pycache__", ".pyc",
)


def main(argv: list[str]) -> int:
    pyproject_path = Path(argv[0])
    wheels = sorted(Path(argv[1]).glob("*.whl"))
    if len(wheels) != 1:
        print(f"ERROR: expected exactly one wheel in {argv[1]}, found {len(wheels)}")
        return 1

    project = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))["project"]
    dist_info = f"{project['name'].replace('-', '_')}-{project['version']}.dist-info/"
    errors = []

    with zipfile.ZipFile(wheels[0]) as wheel:
        names = wheel.namelist()
        metadata = Parser().parsestr(wheel.read(dist_info + "METADATA").decode("utf-8"))
        entry_points = wheel.read(dist_info + "entry_points.txt").decode("utf-8")

    print(f"Wheel: {wheels[0].name} ({len(names)} files)")
    for name in names:
        print(f"  {name}")
        if not (name.startswith(PACKAGE + "/") or name.startswith(dist_info)):
            errors.append(f"unexpected file outside the package: {name}")
        lowered = name.lower()
        for part in FORBIDDEN_PARTS:
            if part in lowered:
                errors.append(f"forbidden file in wheel: {name}")
                break

    for required in REQUIRED_FILES:
        if required not in names:
            errors.append(f"missing package file: {required}")

    expected_metadata = {
        "Name": project["name"],
        "Version": project["version"],
        "Requires-Python": project["requires-python"],
    }
    for field, expected in expected_metadata.items():
        if metadata.get(field) != expected:
            errors.append(f"metadata {field} is {metadata.get(field)!r}, expected {expected!r}")

    for script, target in project["scripts"].items():
        if f"{script} = {target}" not in entry_points:
            errors.append(f"console script missing from entry_points.txt: {script} = {target}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("Wheel contents and metadata match pyproject.toml.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
