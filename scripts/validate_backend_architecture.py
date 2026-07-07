#!/usr/bin/env python3
"""Validate the documented backend module architecture."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
MODULES_DIR = BACKEND / "modules"
DEPENDENCIES_FILE = BACKEND / "module-dependencies.json"
ADR_FILE = ROOT / "docs" / "adr" / "0001-backend-modular-architecture.md"
SHARED_TYPES_FILE = BACKEND / "shared" / "types.md"

REQUIRED_MODULES = [
    "identity",
    "traveler-profiles",
    "flight-search",
    "flight-booking",
    "taxi-booking",
    "payments",
    "notifications",
    "audit-events",
    "provider-adapters",
]

REQUIRED_MODULE_PHRASES = {
    "README.md": ["## Boundary", "## Responsibilities", "## Does not own", "## Allowed dependencies", "## Public interface"],
    "service-interface.md": ["interface"],
}


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def load_dependencies() -> dict[str, list[str]]:
    if not DEPENDENCIES_FILE.exists():
        fail(f"missing {DEPENDENCIES_FILE.relative_to(ROOT)}")
    try:
        data = json.loads(DEPENDENCIES_FILE.read_text())
    except json.JSONDecodeError as exc:
        fail(f"invalid dependency JSON: {exc}")
    if not isinstance(data, dict):
        fail("module dependency graph must be an object")
    return data


def validate_module_artifacts(dependencies: dict[str, list[str]]) -> None:
    dependency_modules = set(dependencies)
    required_modules = set(REQUIRED_MODULES)
    if dependency_modules != required_modules:
        missing = sorted(required_modules - dependency_modules)
        extra = sorted(dependency_modules - required_modules)
        fail(f"dependency modules mismatch; missing={missing}, extra={extra}")

    for module in REQUIRED_MODULES:
        module_dir = MODULES_DIR / module
        if not module_dir.is_dir():
            fail(f"missing module directory {module_dir.relative_to(ROOT)}")
        for filename, phrases in REQUIRED_MODULE_PHRASES.items():
            path = module_dir / filename
            if not path.exists():
                fail(f"missing {path.relative_to(ROOT)}")
            content = path.read_text()
            for phrase in phrases:
                if phrase not in content:
                    fail(f"{path.relative_to(ROOT)} must include {phrase!r}")

    provider_dtos = MODULES_DIR / "provider-adapters" / "dtos.md"
    if not provider_dtos.exists():
        fail("provider DTO catalog must be isolated in provider-adapters/dtos.md")

    if not SHARED_TYPES_FILE.exists():
        fail("missing shared provider-neutral types file")

    if not ADR_FILE.exists():
        fail("missing backend architecture ADR")


def validate_dependency_targets(dependencies: dict[str, list[str]]) -> None:
    known = set(dependencies)
    for module, targets in dependencies.items():
        if not isinstance(targets, list):
            fail(f"dependencies for {module} must be a list")
        for target in targets:
            if target not in known:
                fail(f"{module} depends on unknown module {target}")
            if target == module:
                fail(f"{module} cannot depend on itself")


def validate_acyclic(dependencies: dict[str, list[str]]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []

    def visit(module: str) -> None:
        if module in visited:
            return
        if module in visiting:
            cycle = stack[stack.index(module):] + [module]
            fail("circular dependency detected: " + " -> ".join(cycle))
        visiting.add(module)
        stack.append(module)
        for target in dependencies[module]:
            visit(target)
        stack.pop()
        visiting.remove(module)
        visited.add(module)

    for module in dependencies:
        visit(module)


def validate_provider_dto_isolation() -> None:
    product_modules = [module for module in REQUIRED_MODULES if module != "provider-adapters"]
    forbidden_type_patterns = ["Dto {", "DTO {", "Dto extends", "DTO extends"]
    for module in product_modules:
        for path in (MODULES_DIR / module).glob("*.md"):
            content = path.read_text()
            for pattern in forbidden_type_patterns:
                if pattern in content:
                    fail(f"provider DTO type leaked into {path.relative_to(ROOT)}")

    provider_dto_catalog = MODULES_DIR / "provider-adapters" / "dtos.md"
    if "Dto" not in provider_dto_catalog.read_text():
        fail("provider adapter DTO catalog must contain provider DTO definitions")


def main() -> None:
    dependencies = load_dependencies()
    validate_module_artifacts(dependencies)
    validate_dependency_targets(dependencies)
    validate_acyclic(dependencies)
    validate_provider_dto_isolation()
    print("Backend architecture validation passed")


if __name__ == "__main__":
    main()
