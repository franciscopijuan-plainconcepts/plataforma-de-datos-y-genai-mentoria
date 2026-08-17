"""CLI entrypoints — bootstrap, teardown, validate, generate-dictionary.

Implements the four baseline commands per the quickstart.md guide:
- bootstrap: fail-fast checks (Docker, port, source file) -> docker compose up
  -> wait for PG healthcheck -> EDA -> create tables -> load rows -> write manifest.
- teardown: stop + remove container (optionally volume) -> no orphaned resources.
- validate: single pass/fail signal (container up, DB reachable, 3 tables,
  row counts, manifest present).
- generate-dictionary: deferred to Phase 4 (US2).

Constitution Principle III: the CLI depends only on the data-access
Protocols + contract models; engine-specific code stays in the adapter.

Reference: specs/001-data-genai-platform-baseline/quickstart.md
            specs/001-data-genai-platform-baseline/spec.md (FR-001..FR-016)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from src.contracts.ingestion import SchemaInferenceResult
from src.data_access.adapters.postgres.connection import PostgresConfig
from src.data_access.adapters.postgres.repository import PostgresRepository
from src.data_engineering.dictionary.generator import generate_dictionary
from src.data_engineering.dictionary.render import render_markdown
from src.data_engineering.eda.explorer import explore_workbook
from src.data_engineering.eda.schema_inferrer import infer_table_defs
from src.data_engineering.ingestion.loader import load_workbook
from src.data_engineering.ingestion.manifest import (
    build_manifest,
    sha256_of_file,
    write_manifest,
)

# --- Repository root + file paths ---
_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE_FILE = _REPO_ROOT / "docker" / "docker-compose.yml"
_DEFAULT_SOURCE = _REPO_ROOT / "Global Superstore Data.xlsx"
_MANIFEST_PATH = _REPO_ROOT / ".artifacts" / "load_manifest.json"
_DICTIONARY_PATH = _REPO_ROOT / "data_dictionary.md"
# Expected row counts from the EDA (see research.md Part A) — the validator
# uses these to confirm the loaded warehouse matches the canonical dataset.

# Load .env from the repository root so FORGE_API_KEY and POSTGRES_* are
# available without manually sourcing .env. Safe if .env doesn't exist.
load_dotenv(_REPO_ROOT / ".env")
_EXPECTED_ROW_COUNTS = {
    "Orders": 51290,
    "Returns": 2033,
    "People": 24,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_env() -> dict[str, str]:
    """Load environment config (POSTGRES_* vars). Secrets come from env/.env."""
    return {
        "host": os.environ.get("POSTGRES_HOST", "localhost"),
        "port": os.environ.get("POSTGRES_PORT", "5432"),
        "db": os.environ.get("POSTGRES_DB", "global_superstore"),
        "user": os.environ.get("POSTGRES_USER", "plataforma"),
        "password": os.environ.get("POSTGRES_PASSWORD", "plataforma_dev"),
    }


def _docker_available() -> bool:
    """Fail-fast check (FR-013): is Docker installed and responsive?"""
    try:
        result = subprocess.run(
            ["docker", "info"], capture_output=True, text=True, check=False, timeout=10
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _port_in_use(port: int) -> bool:
    """Check if a local port is already in use (FR-013 fail-fast)."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("localhost", port)) == 0


def _compose_up() -> None:
    """Start the PostgreSQL container via docker compose."""
    subprocess.run(
        ["docker", "compose", "-f", str(_COMPOSE_FILE), "up", "-d"],
        check=True,
    )


def _compose_down(remove_volume: bool) -> None:
    """Stop and remove the container (and optionally the volume). FR-007."""
    cmd = ["docker", "compose", "-f", str(_COMPOSE_FILE), "down"]
    if remove_volume:
        cmd.append("-v")
    subprocess.run(cmd, check=True)


def _wait_for_pg(config: PostgresConfig, timeout_s: int = 60) -> bool:
    """Wait until PostgreSQL is accepting connections. Returns True if healthy."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            from src.data_access.adapters.postgres.connection import connect

            c = connect(config)
            c.close()
            return True
        except Exception:
            time.sleep(2)
    return False


def _err(msg: str) -> None:
    """Print a clear, actionable error to stderr and exit non-zero (FR-013)."""
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def _info(msg: str) -> None:
    print(msg)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_bootstrap(source_file: str | None = None) -> None:
    """bootstrap: bring up PostgreSQL, run EDA, create schema, load data, write manifest."""
    src_path = Path(source_file) if source_file else _DEFAULT_SOURCE

    # --- Fail-fast checks (FR-013) ---
    if not _docker_available():
        _err("Docker is not installed or the daemon is not running. " "Start Docker before running bootstrap.")
    env = _load_env()
    try:
        port = int(env["port"])
    except ValueError:
        _err(f"POSTGRES_PORT={env['port']!r} is not a valid integer.")
    if not src_path.exists():
        _err(f"Source workbook not found: {src_path}")

    _info(f"Starting PostgreSQL container (port {port})...")
    try:
        _compose_up()
    except subprocess.CalledProcessError as exc:
        _err(f"docker compose up failed: {exc.stderr if exc.stderr else exc}")

    _info("Waiting for PostgreSQL to become healthy...")
    config = PostgresConfig.from_env()
    if not _wait_for_pg(config):
        _err("PostgreSQL did not become healthy within the timeout.")

    # --- EDA + schema + load ---
    _info(f"Exploring workbook: {src_path}")
    tables, shared = explore_workbook(src_path)
    source_sha = sha256_of_file(src_path)
    inference = SchemaInferenceResult(
        source_file=str(src_path),
        source_sha256=source_sha,
        tables=tables,
        shared_columns=shared,
        inferred_at=datetime.now(timezone.utc),
    )

    _info("Creating tables and loading data...")
    # Load directly via the data-access layer. Per FR-015, any validation
    # error (bad row, constraint violation) raises immediately — no retry
    # that could mask data-quality issues.
    with PostgresRepository(config=config) as repo:
        load_results = load_workbook(src_path, repo, repo)

    # --- Manifest provenance ---
    manifest = build_manifest(inference, load_results)
    written = write_manifest(manifest, _MANIFEST_PATH)
    _info(f"Wrote load manifest to {written}")

    _info("Bootstrap complete. Run `validate` to confirm.")


def cmd_teardown(remove_volume: bool | None = None) -> None:
    """teardown: stop and remove the container (and optionally volume). FR-007/SC-006."""
    env = _load_env()
    if remove_volume is None:
        remove_volume = os.environ.get("TEARDOWN_REMOVE_VOLUME", "false").lower() == "true"
    _info(f"Stopping PostgreSQL container (remove_volume={remove_volume})...")
    try:
        _compose_down(remove_volume=remove_volume)
    except subprocess.CalledProcessError as exc:
        _err(f"docker compose down failed: {exc.stderr if exc.stderr else exc}")
    _info("Teardown complete. No orphaned resources should remain.")


def cmd_validate() -> None:
    """validate: single pass/fail signal. FR-014 / SC-007."""
    env = _load_env()
    checks: dict[str, bool] = {}

    # 1. Container up (Docker check).
    if not _docker_available():
        _err("Docker is not running. Cannot validate.")
    # Confirm the container is up by attempting to query it.
    config = PostgresConfig.from_env()
    try:
        with PostgresRepository(config=config) as repo:
            # 2. DB reachable (implicit — connection succeeded).
            checks["database_reachable"] = True
            # 3. Tables present.
            tables = repo.list_tables()
            for expected in _EXPECTED_ROW_COUNTS:
                checks[f"table_{expected}_present"] = expected in tables
                # 4. Row counts.
                if expected in tables:
                    n = repo.count_rows(expected)
                    checks[f"table_{expected}_rowcount"] = (
                        n == _EXPECTED_ROW_COUNTS[expected]
                    )
    except Exception as exc:
        _err(f"Validation failed (database not reachable): {exc}")

    # 5. Manifest present + source hash matches + per-table row-count provenance.
    checks["manifest_present"] = _MANIFEST_PATH.exists()
    if _MANIFEST_PATH.exists():
        try:
            manifest_data: dict[str, Any] = json.loads(_MANIFEST_PATH.read_text())
            expected_sha = sha256_of_file(_DEFAULT_SOURCE)
            checks["manifest_source_hash_matches"] = (
                manifest_data.get("source_sha256") == expected_sha
            )
            # Per-table provenance: rows_loaded in the manifest MUST match the
            # EDA-derived canonical row counts (FR-014 / SC-007).
            per_table = manifest_data.get("per_table", [])
            manifest_counts: dict[str, int] = {
                entry.get("table_name", ""): int(entry.get("row_count", -1))
                for entry in per_table
            }
            for table_name, expected_count in _EXPECTED_ROW_COUNTS.items():
                recorded = manifest_counts.get(table_name)
                checks[f"manifest_{table_name}_rowcount"] = (
                    recorded is not None and recorded == expected_count
                )
        except Exception:
            checks["manifest_source_hash_matches"] = False
            for table_name in _EXPECTED_ROW_COUNTS:
                checks[f"manifest_{table_name}_rowcount"] = False

    # 6. Data dictionary file present (FR-014 / SC-007).
    checks["data_dictionary_present"] = _DICTIONARY_PATH.exists()

    # --- Report ---
    all_pass = all(checks.values())
    _info("Validation results:")
    for name, ok in checks.items():
        status = "PASS" if ok else "FAIL"
        _info(f"  [{status}] {name}")
    if all_pass:
        _info("VALIDATION PASSED")
        sys.exit(0)
    else:
        _err("VALIDATION FAILED — see checks above.")


def cmd_generate_dictionary(source_file: str | None = None) -> None:
    """generate-dictionary: render the committed `data_dictionary.md` artifact.

    Consumes the EDA schema inference + curated Kaggle semantics, produces a
    `DataDictionaryDocument`, renders it to Markdown, and writes it to the
    repository root. Regeneratable on a fresh machine from the loaded schema
    (FR-012). Covers 100% of tables and columns (FR-008/FR-009), documents
    cross-table relationships (FR-011), and attaches EDA-derived data-quality
    notes.
    """
    src_path = Path(source_file) if source_file else _DEFAULT_SOURCE
    if not src_path.exists():
        _err(f"Source workbook not found: {src_path}")

    _info(f"Exploring workbook for dictionary generation: {src_path}")
    tables, shared = explore_workbook(src_path)
    source_sha = sha256_of_file(src_path)
    inference = SchemaInferenceResult(
        source_file=str(src_path),
        source_sha256=source_sha,
        tables=tables,
        shared_columns=shared,
        inferred_at=datetime.now(timezone.utc),
    )

    _info("Inferring schema and generating dictionary document...")
    table_defs = infer_table_defs(tables)
    document = generate_dictionary(inference, table_defs)

    _info("Rendering Markdown...")
    markdown = render_markdown(document)

    _DICTIONARY_PATH.write_text(markdown)
    _info(f"Wrote data dictionary to {_DICTIONARY_PATH}")
    _info("Done.")


def cmd_ask(question: str) -> None:
    """ask: translate a natural-language question to SQL and return results.

    Builds the Text-to-SQL pipeline (prompt → LLM → validate → execute) and
    prints the generated SQL, validation status, and result rows.
    Fail-fast (FR-013) if FORGE_API_KEY is missing or the warehouse is not running.
    """
    from src.ai_engineering.llm_client import LlmClient
    from src.ai_engineering.pipeline import TextToSqlPipeline
    from src.contracts.text_to_sql import LlmConfig, NLQuestion

    # --- Fail-fast checks (FR-013) ---
    try:
        llm_config = LlmConfig.from_env()
    except ValueError as exc:
        _err(str(exc))

    if not _docker_available():
        _err("Docker is not running. Run `bootstrap` first to start the warehouse.")

    config = PostgresConfig.from_env()

    # --- Build the pipeline ---
    src_path = _DEFAULT_SOURCE
    if not src_path.exists():
        _err(f"Source workbook not found: {src_path}")

    _info("Exploring workbook for semantic context...")
    tables, shared = explore_workbook(src_path)
    source_sha = sha256_of_file(src_path)
    inference = SchemaInferenceResult(
        source_file=str(src_path),
        source_sha256=source_sha,
        tables=tables,
        shared_columns=shared,
        inferred_at=datetime.now(timezone.utc),
    )
    table_defs = infer_table_defs(tables)
    orders_def = next(
        (td for td in table_defs if td.name.lower() == "orders"), None
    )
    if orders_def is None:
        _err("Orders table not found in inferred schema.")

    document = generate_dictionary(inference, table_defs)

    llm_client = LlmClient(llm_config)

    # --- Run the pipeline ---
    with PostgresRepository(config=config) as repo:
        pipeline = TextToSqlPipeline(
            dictionary=document,
            table_def=orders_def,  # type: ignore[arg-type]  # confirmed non-None above
            llm_client=llm_client,
            query_provider=repo,
            llm_config=llm_config,
        )
        response = pipeline.run(NLQuestion(text=question))

    # --- Print results ---
    if response.error:
        print(f"Error: {response.error}")
        sys.exit(1)

    print(f"Question: {response.question.text}")
    print(f"Generated SQL: {response.generated_sql.sql}")
    print(f"Validation: {'ACCEPTED' if response.validation.accepted else 'REJECTED'}")
    if response.validation.reason:
        print(f"  Reason: {response.validation.reason}")

    if response.query_result is not None:
        if response.query_result.error:
            print(f"Execution error: {response.query_result.error}")
            print(f"  SQL: {response.query_result.sql}")
        else:
            print(f"Rows ({response.query_result.row_count}):")
            for row in response.query_result.rows:
                print(f"  {row.data}")
            print(f"Latency: {response.query_result.latency_ms}ms")


def cmd_evaluate() -> None:
    """evaluate: run the sanity-check evaluation (v1.1).

    Runs ~10 sample questions through the full Text-to-SQL pipeline and prints
    a simple pass/fail summary (FR-017/SC-002). Requires Docker PG + FORGE_API_KEY.
    """
    from src.ai_engineering.evaluation import run_evaluation
    from src.ai_engineering.llm_client import LlmClient
    from src.ai_engineering.pipeline import TextToSqlPipeline
    from src.contracts.text_to_sql import LlmConfig

    # --- Fail-fast checks (FR-013) ---
    try:
        llm_config = LlmConfig.from_env()
    except ValueError as exc:
        _err(str(exc))

    if not _docker_available():
        _err("Docker is not running. Run `bootstrap` first to start the warehouse.")

    config = PostgresConfig.from_env()

    # --- Build the pipeline (same as cmd_ask) ---
    src_path = _DEFAULT_SOURCE
    if not src_path.exists():
        _err(f"Source workbook not found: {src_path}")

    _info("Exploring workbook for semantic context...")
    tables, shared = explore_workbook(src_path)
    source_sha = sha256_of_file(src_path)
    inference = SchemaInferenceResult(
        source_file=str(src_path),
        source_sha256=source_sha,
        tables=tables,
        shared_columns=shared,
        inferred_at=datetime.now(timezone.utc),
    )
    table_defs = infer_table_defs(tables)
    orders_def = next(
        (td for td in table_defs if td.name.lower() == "orders"), None
    )
    if orders_def is None:
        _err("Orders table not found in inferred schema.")

    document = generate_dictionary(inference, table_defs)
    llm_client = LlmClient(llm_config)

    sample_path = _REPO_ROOT / "specs" / "002-text-to-sql-v1" / "sample_questions.json"
    if not sample_path.exists():
        _err(f"Sample questions file not found: {sample_path}")

    _info("Running sanity-check evaluation...")
    with PostgresRepository(config=config) as repo:
        pipeline = TextToSqlPipeline(
            dictionary=document,
            table_def=orders_def,  # type: ignore[arg-type]  # confirmed non-None above
            llm_client=llm_client,
            query_provider=repo,
            llm_config=llm_config,
        )
        summary = run_evaluation(pipeline, sample_path)

    print(summary)


_COMMANDS = {
    "bootstrap": cmd_bootstrap,
    "teardown": cmd_teardown,
    "validate": cmd_validate,
    "generate-dictionary": None,  # has special arg handling in main()
    "ask": None,  # has special arg handling in main()
    "evaluate": None,  # has special arg handling in main()
}


def main(argv: list[str] | None = None) -> None:
    """CLI entrypoint: `python -m src.cli <command>`."""
    args = argv if argv is not None else sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print("Usage: python -m src.cli.main {bootstrap|teardown|validate|generate-dictionary} [args]")
        print("Commands:")
        print("  bootstrap [--source PATH]        Bring up PG, load data, write manifest")
        print("  teardown [--remove-volume]        Stop & remove container (and optionally volume)")
        print("  validate                          Single pass/fail health check")
        print("  generate-dictionary [--source P]  Generate data_dictionary.md from the schema")
        print("  ask <question>                     Translate a natural-language question to SQL")
        print("  evaluate                           Run sanity-check evaluation (v1.1)")
        sys.exit(0 if args else 1)

    command = args[0]
    rest = args[1:]
    if command == "generate-dictionary":
        dict_source: str | None = None
        for i, a in enumerate(rest):
            if a in ("--source", "-s") and i + 1 < len(rest):
                dict_source = rest[i + 1]
        cmd_generate_dictionary(source_file=dict_source)
        return
    if command == "ask":
        if not rest:
            _err("Usage: python -m src.cli.main ask <question>")
        # Join remaining args into a single question string.
        question_text = " ".join(rest)
        cmd_ask(question_text)
        return
    if command == "evaluate":
        cmd_evaluate()
        return
    handler = _COMMANDS.get(command)
    if handler is None:
        _err(f"Unknown command: {command!r}. Use bootstrap|teardown|validate.")

    # Parse simple per-command args.
    if command == "bootstrap":
        source: str | None = None
        for i, a in enumerate(rest):
            if a in ("--source", "-s") and i + 1 < len(rest):
                source = rest[i + 1]
        cmd_bootstrap(source_file=source)
    elif command == "teardown":
        remove_vol = ("--remove-volume" in rest) or ("-v" in rest)
        cmd_teardown(remove_volume=remove_vol)
    else:  # validate
        cmd_validate()


if __name__ == "__main__":
    main()
