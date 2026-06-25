"""Guardrail: the `ta` library must never be imported anywhere in the codebase.

History: the `ta` package (https://github.com/bukosabino/ta) was used by ~104
custom strategy files that were silently dead code because `ta` was never an
installed dependency. The registry skipped them with warnings, the DB accepted
strategies pointing at their TYPE_NAMEs, and the backtest engine fell through
to a zero-signal path, producing fake "successful" backtests for months.

After the cleanup those files were deleted and `ta` was removed from
`pyproject.toml` and `requirements-ci.txt`. This test is the tripwire that
stops anyone — human or LLM — from reintroducing the dependency.

If this test fails, the fix is NOT to install `ta`. The fix is to rewrite the
offending file using native pandas / numpy, or using a helper from a future
`Axiom/strategies/indicators.py` module if one exists.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# Directories to scan for forbidden imports.
SCAN_DIRS = (
    REPO_ROOT / "Axiom",
    REPO_ROOT / "scripts",
    REPO_ROOT / "tests",
)

# Paths excluded from the scan:
#   - this file (it references "ta" in prose and docstrings)
#   - the `_broken/` quarantine (files there are already excluded from
#     discovery and kept only for archaeology; see its README)
EXCLUDED = {
    Path(__file__).resolve(),
    REPO_ROOT / "Axiom" / "strategies" / "custom" / "_broken",
}


def _is_excluded(path: Path) -> bool:
    path = path.resolve()
    for ex in EXCLUDED:
        if path == ex:
            return True
        try:
            path.relative_to(ex)
        except ValueError:
            continue
        return True
    return False


def _file_imports_ta(path: Path) -> bool:
    """Return True iff the file contains an `import ta` or `from ta...` at
    any level (module or function body).

    Uses AST so string literals mentioning "ta" don't produce false positives.
    """
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return False
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        # Files with syntax errors can't be imported, so they can't exercise
        # `ta`. Skip them — test_no_ta_imports is not a lint suite.
        return False

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = (alias.name or "").split(".")[0]
                if root == "ta":
                    return True
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root == "ta":
                return True
    return False


def _git_tracked_files() -> set[Path] | None:
    """Return the set of git-tracked *.py paths, or None if git is unavailable.

    The ban targets the committed codebase. `Axiom/strategies/custom/` is
    gitignored (only __init__.py is tracked) and the app generates throwaway
    strategies there at runtime — some import unavailable libs and are simply
    skipped by the registry. Scanning those untracked artifacts turned this
    tripwire into a local-clutter false positive, so we restrict the scan to
    tracked files: exactly the code that can actually reintroduce the dep.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "ls-files", "*.py"],
            capture_output=True,
            text=True,
            check=True,
        )
    except Exception:
        return None
    return {(REPO_ROOT / line).resolve() for line in out.stdout.splitlines() if line}


def _iter_python_files():
    tracked = _git_tracked_files()
    for base in SCAN_DIRS:
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            if _is_excluded(path):
                continue
            if tracked is not None and path.resolve() not in tracked:
                continue
            yield path


def test_no_file_imports_the_ta_library():
    offenders = sorted(p for p in _iter_python_files() if _file_imports_ta(p))
    if offenders:
        rels = [str(p.relative_to(REPO_ROOT)) for p in offenders]
        pytest.fail(
            "The `ta` library is banned from this codebase. See the docstring "
            "of tests/test_no_ta_imports.py for the history and the correct "
            "fix (rewrite with native pandas/numpy, never reinstall ta).\n\n"
            "Offending files:\n  - " + "\n  - ".join(rels)
        )


def _extract_pyproject_dependencies(text: str) -> list[str]:
    """Parse the `[project] dependencies = [...]` array out of pyproject.toml
    without a TOML parser (stdlib `tomllib` is 3.11+ but avoiding it keeps
    this test resilient across environments). Returns the list of dep
    strings (each like `"ta>=0.11.0"` with quotes stripped).
    """
    import re

    deps: list[str] = []
    in_project = False
    in_deps = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_project = stripped == "[project]"
            in_deps = False
            continue
        if not in_project:
            continue
        if re.match(r"^dependencies\s*=\s*\[", stripped):
            in_deps = True
            # handle same-line dep like `dependencies = ["foo"]`
            rest = stripped.split("[", 1)[1]
            if rest.endswith("]"):
                in_deps = False
                rest = rest[:-1]
            for item in rest.split(","):
                item = item.strip().strip('"').strip("'")
                if item:
                    deps.append(item)
            continue
        if in_deps:
            if stripped == "]":
                in_deps = False
                continue
            item = stripped.rstrip(",").strip().strip('"').strip("'")
            if item and not item.startswith("#"):
                deps.append(item)
    return deps


def test_ta_not_in_project_dependencies():
    """`ta` must not appear in pyproject.toml `dependencies` or requirements-ci.txt.

    Deliberately does NOT scan the `[tool.ruff...]` banned-api config, which
    necessarily names `"ta"` to ban it.
    """
    import re

    # pyproject.toml — parse the dependencies list structurally
    pyproject = REPO_ROOT / "pyproject.toml"
    if pyproject.exists():
        text = pyproject.read_text(encoding="utf-8", errors="replace")
        deps = _extract_pyproject_dependencies(text)
        for dep in deps:
            name = re.split(r"[\s<>=!~\[]", dep, maxsplit=1)[0].strip().lower()
            if name == "ta":
                pytest.fail(
                    f"pyproject.toml declares the banned `ta` library as a "
                    f"project dependency:\n  {dep}\n"
                    "See tests/test_no_ta_imports.py docstring for why."
                )

    # requirements-ci.txt — one dep per line
    req_file = REPO_ROOT / "requirements-ci.txt"
    if req_file.exists():
        text = req_file.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            name = re.split(r"[\s<>=!~\[]", stripped, maxsplit=1)[0].strip().lower()
            if name == "ta":
                pytest.fail(
                    f"requirements-ci.txt:{lineno} declares the banned "
                    f"`ta` library:\n  {line}\n"
                    "See tests/test_no_ta_imports.py docstring for why."
                )
