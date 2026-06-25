from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

import pytest
from fastapi import APIRouter

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOTS = (
    REPO_ROOT / "Axiom",
    REPO_ROOT / "frontend" / "src",
    REPO_ROOT / "tests",
)
DUPLICATE_SCAN_ROOTS = (
    REPO_ROOT / "Axiom" / "routers",
    REPO_ROOT / "Axiom" / "strategies" / "builtin",
)
def _module_names(package_name: str) -> list[str]:
    package = importlib.import_module(package_name)
    return sorted(
        module.name
        for module in pkgutil.iter_modules(package.__path__)
        if not module.name.startswith("_")
    )


def _scanned_source_files() -> list[Path]:
    paths: list[Path] = []
    for root in DUPLICATE_SCAN_ROOTS:
        paths.extend(sorted(root.glob("*.py")))
    return paths


@pytest.mark.parametrize("module_name", _module_names("axiom.routers"))
def test_router_modules_import_and_expose_router(module_name: str, AXIOM_db):
    module = importlib.import_module(f"axiom.routers.{module_name}")

    assert isinstance(module.router, APIRouter)


@pytest.mark.parametrize("module_name", _module_names("axiom.strategies.builtin"))
def test_builtin_strategy_modules_import(module_name: str):
    importlib.import_module(f"axiom.strategies.builtin.{module_name}")


@pytest.mark.parametrize("source_file", _scanned_source_files(), ids=lambda path: path.name)
def test_router_and_builtin_files_are_not_duplicated_end_to_end(source_file: Path):
    lines = source_file.read_text(encoding="utf-8").splitlines()

    if len(lines) < 20 or len(lines) % 2 != 0:
        return

    midpoint = len(lines) // 2
    assert lines[:midpoint] != lines[midpoint:], f"{source_file} is duplicated end to end"


@pytest.mark.parametrize("root", SOURCE_ROOTS, ids=lambda path: path.name)
def test_source_tree_has_no_unresolved_merge_markers(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in {".py", ".ts", ".svelte", ".md"}:
            continue

        for line_number, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1):
            stripped = line.strip()
            has_conflict_marker = (
                stripped.startswith("<<<<<<<")
                or stripped == "======="
                or stripped.startswith(">>>>>>>")
            )
            assert not has_conflict_marker, (
                f"{path}:{line_number} contains unresolved merge marker {stripped!r}"
            )
