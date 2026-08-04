"""Architecture boundary contract tests (constitution-mandated gate).

These tests enforce the constitution's separation-of-concerns and
engine-encapsulation rules WITHOUT spinning up the database:

- Principle II & III: `pandas`/`openpyxl` may be imported ONLY inside
  `src/data_engineering/eda` and `src/data_engineering/ingestion`.
- Principle III: `psycopg` may be imported ONLY inside
  `src/data_access/adapters/postgres`.
- Principle I & contracts/data_access.md: the PostgreSQL adapter satisfies
  the `runtime_checkable` Protocols (`SchemaProvider`, `DataProvider`,
  `QueryProvider`).

Reference: specs/001-data-genai-platform-baseline/contracts/data_access.md
            specs/001-data-genai-platform-baseline/contracts/ingestion.md
            .specify/memory/constitution.md
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.data_access.adapters.postgres.repository import PostgresRepository
from src.data_access.interfaces import (
    DataProvider,
    QueryProvider,
    SchemaProvider,
)

# --- Repository root for resolving source paths ---
_REPO_ROOT = Path(__file__).resolve().parents[2]


# --- Modules that MAY import the guarded libraries ---
_ALLOWED_PANDAS_DIRS = {
    _REPO_ROOT / "src" / "data_engineering" / "eda",
    _REPO_ROOT / "src" / "data_engineering" / "ingestion",
}
_ALLOWED_PSYCOPG_DIR = _REPO_ROOT / "src" / "data_access" / "adapters" / "postgres"


def _iter_python_files(root: Path) -> list[Path]:
    """Yield all `.py` files under `root`, excluding __pycache__."""
    if not root.exists():
        return []
    return [
        p
        for p in root.rglob("*.py")
        if "__pycache__" not in p.parts
    ]


def _top_level_modules(file_path: Path) -> set[str]:
    """Return the set of top-level module names imported in `file_path`."""
    try:
        tree = ast.parse(file_path.read_text(encoding="utf-8"))
    except SyntaxError:
        return set()
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None and node.level == 0:
                modules.add(node.module.split(".")[0])
    return modules


def _is_under(path: Path, allowed_dirs: set[Path]) -> bool:
    """Return True if `path` is inside any of `allowed_dirs`."""
    return any(path == d or allowed_dirs_set_contains(d, path) for d in allowed_dirs)


def allowed_dirs_set_contains(allowed_dir: Path, path: Path) -> bool:
    """Return True if `path` is inside `allowed_dir`."""
    try:
        path.relative_to(allowed_dir)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Boundary 1: pandas / openpyxl confined to data_engineering/eda|ingestion
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "guarded_module",
    ["pandas", "openpyxl"],
)
def test_pandas_openpyxl_confined_to_ingestion_modules(guarded_module: str) -> None:
    """`pandas`/`openpyxl` MUST NOT be imported outside the ingestion modules.

    Constitution Principle II & III + contracts/ingestion.md boundary rules.
    """
    src_root = _REPO_ROOT / "src"
    offenders: list[str] = []
    for py_file in _iter_python_files(src_root):
        if guarded_module not in _top_level_modules(py_file):
            continue
        if _is_under(py_file, _ALLOWED_PANDAS_DIRS):
            continue  # permitted location
        offenders.append(str(py_file.relative_to(_REPO_ROOT)))
    assert not offenders, (
        f"{guarded_module} imported outside allowed modules: {offenders}. "
        f"Only {_ALLOWED_PANDAS_DIRS} may import pandas/openpyxl."
    )


# ---------------------------------------------------------------------------
# Boundary 2: psycopg confined to data_access/adapters/postgres
# ---------------------------------------------------------------------------

def test_psycopg_confined_to_postgres_adapter() -> None:
    """`psycopg` MUST NOT be imported outside the PostgreSQL adapter.

    Constitution Principle III: engine-specific code confined to adapters.
    """
    src_root = _REPO_ROOT / "src"
    offenders: list[str] = []
    for py_file in _iter_python_files(src_root):
        if "psycopg" not in _top_level_modules(py_file):
            continue
        if _is_under(py_file, {_ALLOWED_PSYCOPG_DIR}):
            continue  # permitted location
        offenders.append(str(py_file.relative_to(_REPO_ROOT)))
    assert not offenders, (
        f"psycopg imported outside the PostgreSQL adapter: {offenders}. "
        f"Only {_ALLOWED_PSYCOPG_DIR} may import psycopg."
    )


# ---------------------------------------------------------------------------
# Boundary 3: runtime_checkable Protocol conformance
# ---------------------------------------------------------------------------

def test_postgres_repository_satisfies_protocols() -> None:
    """`PostgresRepository` satisfies the runtime_checkable Protocols.

    Constitution Principle I & contracts/data_access.md: every Protocol
    method is typed to accept/return contract models. Structural Protocol
    conformance is asserted via `isinstance` at runtime.
    """
    # `runtime_checkable` Protocols support isinstance() checks. We construct
    # without a live connection (the methods are structurally present); the
    # check only verifies method signatures exist, not DB connectivity.
    try:
        repo = PostgresRepository.__new__(PostgresRepository)
    except Exception:  # pragma: no cover - defensive
        pytest.skip("Could not construct PostgresRepository for structural check")
    assert isinstance(repo, SchemaProvider), "PostgresRepository must be a SchemaProvider"
    assert isinstance(repo, DataProvider), "PostgresRepository must be a DataProvider"
    assert isinstance(repo, QueryProvider), "PostgresRepository must satisfy QueryProvider"


def test_protocols_have_no_execute_sql_escape_hatch() -> None:
    """No shared Protocol exposes a raw `execute_sql` escape hatch.

    Constitution Principle III / research.md Part C: a generic
    `execute_sql(sql: str)` would re-couple upstream code to PG-flavored SQL
    and silently break on BigQuery. Assert it is NOT present on the Protocols.
    """
    for protocol in (SchemaProvider, DataProvider, QueryProvider):
        assert not hasattr(protocol, "execute_sql"), (
            f"{protocol.__name__} must NOT expose execute_sql (raw SQL escape hatch)"
        )
