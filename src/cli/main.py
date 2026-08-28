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
from typing import TYPE_CHECKING, Any, cast

from dotenv import load_dotenv

from src.contracts.data_access import TableDef
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
# v2.0 Semantic Layer artifacts (regeneratable via generate-semantic-layer).
_SEMANTIC_SOURCE_PATH = _REPO_ROOT / "src" / "data_engineering" / "dictionary" / "semantic_source.py"
_SEMANTIC_LAYER_JSON_PATH = _REPO_ROOT / ".artifacts" / "semantic_layer.json"
_SEMANTIC_LAYER_MD_PATH = _REPO_ROOT / ".artifacts" / "semantic_layer.md"
# Expected row counts from the EDA (see research.md Part A) — the validator
# uses these to confirm the loaded warehouse matches the canonical dataset.

if TYPE_CHECKING:
    # Only needed for the `_run_ask_pipeline` return-type annotation below;
    # the real imports happen lazily inside the functions that use them so
    # the module-level import graph (and the boundary contract tests) stay
    # unaffected.
    from src.contracts.semantic_layer import SemanticViewer
    from src.contracts.text_to_sql import TextToSqlResponse

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


from typing import NoReturn


def _err(msg: str) -> NoReturn:
    """Print a clear, actionable error to stderr and exit non-zero (FR-013).

    Annotated `-> NoReturn` so static type-checkers can narrow `viewer` after
    a fail-fast call (e.g., after _err, `viewer` is guaranteed non-None).
    """
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def _info(msg: str) -> None:
    print(msg)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
def _infer_orders_table_def() -> TableDef:
    """Infer the Orders table definition from the canonical workbook."""
    if not _DEFAULT_SOURCE.exists():
        _err(f"Source workbook not found: {_DEFAULT_SOURCE}")
    tables, _ = explore_workbook(_DEFAULT_SOURCE)
    table_defs = infer_table_defs(tables)
    orders_def = next((td for td in table_defs if td.name == "Orders"), None)
    if orders_def is None:
        _err("Orders table not found in inferred schema.")
    return orders_def


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
        # Historic log of predict-sales calls (empty at bootstrap time —
        # populated as predictions are made). Created up-front so it shows
        # up in `validate`/`list_tables` immediately after bootstrap.
        from src.mlops.predictions_store import ensure_predictions_table

        ensure_predictions_table(repo)

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


def cmd_ask(
    question: str,
    viewer_id: str | None = None,
    allow_full_access: bool = False,
) -> None:
    """ask: translate a natural-language question to SQL and return results.

    Builds the Text-to-SQL pipeline (prompt → LLM → validate → execute) and
    prints the generated SQL, validation status, and result rows.
    Fail-fast (FR-013) if FORGE_API_KEY is missing or the warehouse is not running.

    v2.0 (feature 003-semantic-layer-v1):
      - Requires a `--viewer <id>` (constitution Principle IV — governance is
        non-negotiable). Without a viewer, fails fast unless `--allow-full-access`
        is passed in a local/dev environment.
      - Wires the `GovernedQueryProvider` decorator around the PG adapter so
        RLS is enforced on every executed query.
      - When the Semantic Layer artifact exists at `.artifacts/semantic_layer.json`,
        loads it and passes it to the pipeline so the prompt includes metrics.
    """
    response, viewer = _run_ask_pipeline(question, viewer_id, allow_full_access)

    # --- Print results ---
    if response.error:
        print(f"Error: {response.error}")
        sys.exit(1)

    print(f"Question: {response.question.text}")
    print(f"Viewer: {viewer_id or 'full_access_local_dev'} (regions: {list(viewer.regions)})")
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


def _run_ask_pipeline(
    question: str,
    viewer_id: str | None,
    allow_full_access: bool,
) -> tuple["TextToSqlResponse", "SemanticViewer"]:
    """Shared setup for `ask` and `chart`: build + run the Text-to-SQL pipeline.

    Factored out of `cmd_ask` so `cmd_chart` (v3.1 NL->chart) can reuse the
    exact same governed, semantically-enriched pipeline instead of duplicating
    ~150 lines of viewer resolution + prompt-context building.
    """
    from src.ai_engineering.llm_client import LlmClient
    from src.ai_engineering.pipeline import TextToSqlPipeline
    from src.contracts.semantic_layer import SemanticViewer
    from src.contracts.text_to_sql import LlmConfig, NLQuestion, TextToSqlResponse
    from src.data_engineering.semantic_layer.builder import SemanticLayerBuilder
    from src.data_engineering.semantic_layer.governed_provider import (
        build_governed_provider,
    )
    from src.data_engineering.semantic_layer.registry import ViewerRegistry
    from src.data_engineering.semantic_layer.resolver import SemanticQueryResolver

    # --- Fail-fast checks (FR-013) ---
    try:
        llm_config = LlmConfig.from_env()
    except ValueError as exc:
        _err(str(exc))

    if not _docker_available():
        _err("Docker is not running. Run `bootstrap` first to start the warehouse.")

    config = PostgresConfig.from_env()

    # --- Resolve the viewer (governance context) ---
    # v2.0 "login as a person" model: try the People table first (the canonical
    # governance mapping per constitution Principle IV), then fall back to the
    # static `viewers.yaml` registry for escape hatches like `admin_dev`.
    import os

    is_local_dev = os.environ.get("ENV", "").strip().lower() in {"local", "dev", "test"}

    viewer: SemanticViewer | None = None
    if viewer_id is not None:
        from src.data_engineering.semantic_layer.person_resolver import (
            PeopleViewerResolver,
        )

        # The People resolver needs an UNGOVERNED provider so it can read the
        # full People table without being scoped by RLS (People IS the mapping,
        # not subject to it). We use the raw PostgresRepository here, and build
        # the GovernedQueryProvider below for the actual ask pipeline.
        with PostgresRepository(config=config) as people_repo:
            people_resolver = PeopleViewerResolver(
                query_provider=people_repo,
                is_local_dev=is_local_dev,
            )
            try:
                viewer = people_resolver.resolve(viewer_id)
            except Exception as exc:
                _err(
                    f"Could not read the People table to resolve viewer "
                    f"{viewer_id!r}: {exc}. Is the warehouse running?"
                )

        if viewer is not None:
            _info(
                f"Logged in as person {viewer_id!r}: "
                f"region={viewer.regions!r} (resolved from People table)"
            )
        else:
            # People did not match → fall back to the static viewers.yaml registry.
            try:
                viewer = ViewerRegistry().get_viewer(viewer_id)
                _info(
                    f"Loaded viewer {viewer_id!r} from viewers.yaml: "
                    f"regions={list(viewer.regions)} "
                    f"allows_full_access={viewer.allows_full_access}"
                )
            except FileNotFoundError as exc:
                _err(str(exc))
            except ValueError as exc:
                # No match in People nor viewers.yaml — list both sources.
                try:
                    available_people = people_resolver.list_available()
                except Exception:
                    available_people = []
                _err(
                    f"Viewer {viewer_id!r} not found. "
                    f"People available: {available_people}. "
                    f"Configure custom viewers in viewers.yaml (see "
                    "viewers.example.yaml)."
                )
    elif allow_full_access:
        # `--allow-full-access` without --viewer: build an ad-hoc full-access viewer.
        # Only effective in local/dev (the registry enforces is_local_dev gating).
        import os

        env = os.environ.get("ENV", "").strip().lower()
        if env not in {"local", "dev", "test"}:
            _err(
                "--allow-full-access without --viewer is only honored in local/dev/test "
                f"(got ENV={env!r}). Set a real viewer via --viewer <id>."
            )
        viewer = SemanticViewer(
            viewer_id="full_access_local_dev",
            regions=[],
            allows_full_access=True,
            is_local_dev=True,
        )
        _info("WARNING: running in full-access mode (no RLS). Only for local/dev.")
    else:
        _err(
            "Governance is non-negotiable (constitution Principle IV). "
            "Provide --viewer <id> (see viewers.example.yaml) or, in local/dev, "
            "--allow-full-access."
        )

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

    # --- Optional: load the Semantic Layer artifact for prompt enrichment (US3) ---
    semantic_doc = None
    if _SEMANTIC_LAYER_JSON_PATH.exists():
        try:
            import json

            payload = json.loads(_SEMANTIC_LAYER_JSON_PATH.read_text())
            from src.contracts.semantic_layer import SemanticLayerDocument

            # `generated_at` is excluded from the canonical JSON; supply a
            # placeholder so the model validates (it's not used at runtime).
            payload.setdefault("generated_at", datetime.now(timezone.utc).isoformat())
            semantic_doc = SemanticLayerDocument.model_validate(payload)
            _info(f"Loaded Semantic Layer artifact from {_SEMANTIC_LAYER_JSON_PATH}")
        except Exception as exc:
            _info(
                f"WARNING: could not load Semantic Layer artifact ({exc}); "
                "continuing without semantic enrichment."
            )
            semantic_doc = None
    else:
        # Build on the fly if the artifact doesn't exist (best-effort).
        try:
            builder = SemanticLayerBuilder()
            semantic_doc = builder.build(
                dictionary=document,
                semantic_source_sha256=sha256_of_file(_SEMANTIC_SOURCE_PATH),
                source_sha256=source_sha,
            )
        except Exception as exc:
            _info(f"WARNING: could not build Semantic Layer on the fly ({exc}).")

    llm_client = LlmClient(llm_config)

    # --- Run the pipeline with a GovernedQueryProvider (RLS enforced) ---
    with PostgresRepository(config=config) as repo:
        query_provider = build_governed_provider(
            delegate=repo,
            resolver=SemanticQueryResolver(),
            viewer=viewer,
            table_def=orders_def,
        )
        pipeline = TextToSqlPipeline(
            dictionary=document,
            table_def=orders_def,
            llm_client=llm_client,
            query_provider=query_provider,
            llm_config=llm_config,
            semantic_layer=semantic_doc,
            viewer=viewer,
        )
        response = pipeline.run(NLQuestion(text=question))

    return response, viewer


def cmd_chart(
    question: str,
    viewer_id: str | None = None,
    allow_full_access: bool = False,
) -> None:
    """chart: translate a natural-language request into a saved PNG chart.

    v3.1: reuses the governed Text-to-SQL pipeline (`ask`) to fetch data,
    then asks the LLM (via `src.ai_engineering.nl_chart`) to choose a chart
    type + axis mapping from the returned columns, and renders it with
    `src.reporting.chart_renderer` (matplotlib, Agg backend) to
    `.artifacts/charts/`.
    """
    from src.ai_engineering.llm_client import LlmClient
    from src.ai_engineering.nl_chart import ChartSpecAssistant
    from src.contracts.text_to_sql import LlmConfig, NLQuestion
    from src.reporting.chart_renderer import ChartRenderError, render_chart

    response, viewer = _run_ask_pipeline(question, viewer_id, allow_full_access)

    if response.error:
        _err(f"Text-to-SQL step failed: {response.error}")
    if not response.validation.accepted:
        _err(f"Generated SQL was rejected: {response.validation.reason}")
    if response.query_result is None or response.query_result.error:
        err_msg = response.query_result.error if response.query_result else "no result"
        _err(f"Query execution failed: {err_msg}")

    query_result = response.query_result
    if not query_result.rows:
        _err("The query returned 0 rows; nothing to chart.")

    columns = sorted(query_result.rows[0].data.keys())
    _info(f"Query returned {query_result.row_count} row(s) with columns: {columns}")

    llm_config = LlmConfig.from_env()
    llm_client = LlmClient(llm_config)
    assistant = ChartSpecAssistant(llm_client=llm_client)
    parse_result = assistant.parse(NLQuestion(text=question), columns)

    if parse_result.spec is None:
        _err(f"Could not derive a chart specification: {parse_result.error}")

    print(
        f"Chart spec: type={parse_result.spec.chart_type} "
        f"x={parse_result.spec.x_field} y={parse_result.spec.y_field} "
        f"aggregation={parse_result.spec.aggregation} title={parse_result.spec.title!r}"
    )

    try:
        chart_result = render_chart(query_result, parse_result.spec)
    except ChartRenderError as exc:
        _err(str(exc))

    print(f"Chart saved to: {chart_result.image_path}")
    print(f"Data points plotted: {chart_result.point_count}")


def cmd_predict_sales_nl(question: str, environment: str) -> None:
    """predict-sales-nl: extract a prediction request from NL text and run it.

    v3.1: uses `src.ai_engineering.nl_predict` (LLM JSON extraction, grounded
    with the categorical vocabularies observed by the *promoted* model in
    `environment`) to build a typed `PredictionInput`, then calls the exact
    same `src.mlops.inference.predict_sales` used by the `predict-sales` CLI
    command — the LLM never touches the model directly, it only fills in the
    typed contract that the model already knows how to consume.
    """
    from src.ai_engineering.llm_client import LlmClient
    from src.ai_engineering.nl_predict import PredictSalesAssistant
    from src.contracts.text_to_sql import LlmConfig, NLQuestion
    from src.mlops.inference import predict_sales
    from src.mlops.registry import ArtifactRegistry, NoActiveModelError, RegistryError

    try:
        llm_config = LlmConfig.from_env()
    except ValueError as exc:
        _err(str(exc))

    registry = ArtifactRegistry()
    active_entry = registry.resolve_active_run(cast("Any", environment))
    if active_entry is None:
        _err(
            f"No active model promoted in environment {environment!r}. "
            "Run train-sales-model + promote-sales-model first."
        )

    model = registry.load_model(active_entry)
    known_categories = cast(
        "dict[str, list[str]]",
        getattr(model, "_mlops_categorical_vocabularies", {}),
    )

    llm_client = LlmClient(llm_config)
    assistant = PredictSalesAssistant(llm_client=llm_client, known_categories=known_categories)
    parse_result = assistant.parse(NLQuestion(text=question))

    if parse_result.prediction_input is None:
        print("Could not extract a complete prediction request from the question.")
        if parse_result.missing_fields:
            print(f"Missing fields: {parse_result.missing_fields}")
        if parse_result.clarification:
            print(f"Assistant: {parse_result.clarification}")
        sys.exit(1)

    print(f"Parsed prediction input: {parse_result.prediction_input.model_dump(mode='json')}")

    repo: PostgresRepository | None
    try:
        repo = PostgresRepository(config=PostgresConfig.from_env())
    except Exception:
        repo = None

    try:
        result = predict_sales(registry, cast("Any", environment), parse_result.prediction_input, repo)
    except (NoActiveModelError, RegistryError) as exc:
        _err(str(exc))
    except Exception as exc:
        _err(f"predict-sales-nl failed: {exc}")
    finally:
        if repo is not None:
            repo.close()

    print(f"Predicted Sales: {result.predicted_sales}")
    print(
        f"Model: {result.model_name} (run_id={result.run_id}, environment={result.environment})"
    )
    print(f"used_fallback_encoding: {str(result.used_fallback_encoding).lower()}")
    print(f"latency_ms: {result.latency_ms}")


def cmd_train_sales_model() -> None:
    """train-sales-model: train, compare, and persist both sales models."""
    from src.mlops.registry import ArtifactRegistry, RegistryError
    from src.mlops.training import train_sales_models

    if not _docker_available():
        _err("Docker is not running. Run `bootstrap` first to start the warehouse.")

    orders_def = _infer_orders_table_def()
    config = PostgresConfig.from_env()
    registry = ArtifactRegistry()

    try:
        with PostgresRepository(config=config) as repo:
            linear_entry, catboost_entry = train_sales_models(repo, orders_def, registry)
    except (RegistryError, ValueError) as exc:
        _err(str(exc))
    except Exception as exc:
        _err(f"train-sales-model failed: {exc}")

    linear_metadata = registry.read_run_metadata(linear_entry)
    catboost_metadata = registry.read_run_metadata(catboost_entry)

    print("Model comparison (same test set, same split):")
    print(f"{'model':<20} {'rmse':>12} {'mae':>12} {'r2':>12} {'training_ms':>12}")
    print(f"{'-' * 20} {'-' * 12} {'-' * 12} {'-' * 12} {'-' * 12}")
    print(
        f"{linear_entry.model_name:<20} "
        f"{linear_entry.metrics.rmse:>12.4f} "
        f"{linear_entry.metrics.mae:>12.4f} "
        f"{linear_entry.metrics.r2:>12.4f} "
        f"{linear_metadata.training_duration_ms:>12}"
    )
    print(
        f"{catboost_entry.model_name:<20} "
        f"{catboost_entry.metrics.rmse:>12.4f} "
        f"{catboost_entry.metrics.mae:>12.4f} "
        f"{catboost_entry.metrics.r2:>12.4f} "
        f"{catboost_metadata.training_duration_ms:>12}"
    )

    better = linear_entry if linear_entry.metrics.rmse <= catboost_entry.metrics.rmse else catboost_entry
    print(f"Better RMSE: {better.model_name} (run_id={better.run_id})")
    print("")
    print("Persisted runs:")
    print(f"  {linear_entry.model_name} -> .artifacts/mlops/models/{linear_entry.model_name}/{linear_entry.run_id}/")
    print(f"  {catboost_entry.model_name} -> .artifacts/mlops/models/{catboost_entry.model_name}/{catboost_entry.run_id}/")


def cmd_promote_sales_model(run_id: str, environment: str, force: bool = False) -> None:
    """promote-sales-model: promote a persisted run into an environment."""
    from src.mlops.registry import ArtifactRegistry, PromotionGateError, RegistryError, UnknownRunIdError

    registry = ArtifactRegistry()
    try:
        record = registry.promote(
            run_id=run_id,
            environment=cast("Any", environment),
            force_bypass_staging_gate=force,
        )
    except (PromotionGateError, RegistryError, UnknownRunIdError) as exc:
        _err(str(exc))

    if record.bypassed_staging_gate:
        _info(
            f"GOVERNANCE BYPASS: promoted run_id={run_id} directly to prod with --force."
        )
    print(
        f"Promoted run_id={record.run_id} to env={record.environment} "
        f"at {record.promoted_at.isoformat()} bypassed_staging_gate={str(record.bypassed_staging_gate).lower()}"
    )


def cmd_predict_sales(environment: str, input_payload: dict[str, str]) -> None:
    """predict-sales: run inference with the model promoted in an environment."""
    from pydantic import ValidationError

    from src.contracts.mlops import PredictionInput
    from src.mlops.inference import predict_sales
    from src.mlops.registry import ArtifactRegistry, NoActiveModelError, RegistryError

    try:
        prediction_input = PredictionInput.model_validate(input_payload)
    except ValidationError as exc:
        _err(f"Invalid predict-sales input: {exc}")

    registry = ArtifactRegistry()
    # Best-effort: persist the prediction into the `Predictions` SQL table
    # when PostgreSQL is reachable. If it isn't (e.g. offline/unit-test
    # usage), fall back to inference without SQL persistence — the JSONL
    # append-log in src/mlops/inference.py still records the call either way.
    repo: PostgresRepository | None
    try:
        repo = PostgresRepository(config=PostgresConfig.from_env())
    except Exception:
        repo = None

    try:
        result = predict_sales(registry, cast("Any", environment), prediction_input, repo)
    except (NoActiveModelError, RegistryError) as exc:
        _err(str(exc))
    except Exception as exc:
        _err(f"predict-sales failed: {exc}")
    finally:
        if repo is not None:
            repo.close()

    print(f"Predicted Sales: {result.predicted_sales}")
    print(
        f"Model: {result.model_name} (run_id={result.run_id}, environment={result.environment})"
    )
    print(f"used_fallback_encoding: {str(result.used_fallback_encoding).lower()}")
    print(f"latency_ms: {result.latency_ms}")


def cmd_generate_semantic_layer() -> None:
    """generate-semantic-layer: build and persist the Semantic Layer v2.0 artifact.

    Produces two artifacts in `.artifacts/`:
      - `semantic_layer.json`  (canonical, deterministic JSON — FR-007/SC-005)
      - `semantic_layer.md`    (human-readable Markdown — FR-008)

    The builder constructs the `SemanticLayerDocument` from the existing
    `DataDictionaryDocument` (regenerated from the source workbook) and the
    canonical metric definitions (`metrics.py`). No DB connection required.
    Fail-fast (FR-013) if the source workbook is missing or the builder
    detects an invalid reference (FR-006).

    Prints a one-block summary: tables, metrics, dimensions, relationships.
    """
    from src.data_engineering.semantic_layer.builder import SemanticLayerBuilder
    from src.data_engineering.semantic_layer.render import (
        render_json,
        render_markdown,
    )

    src_path = _DEFAULT_SOURCE
    if not src_path.exists():
        _err(f"Source workbook not found: {src_path}")

    if not _SEMANTIC_SOURCE_PATH.exists():
        _err(f"Semantic source not found: {_SEMANTIC_SOURCE_PATH}")

    _info(f"Exploring workbook for semantic context: {src_path}")
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
    document = generate_dictionary(inference, table_defs)

    semantic_source_sha = sha256_of_file(_SEMANTIC_SOURCE_PATH)
    _info("Building Semantic Layer document...")
    builder = SemanticLayerBuilder()
    semantic_doc = builder.build(
        dictionary=document,
        semantic_source_sha256=semantic_source_sha,
        source_sha256=source_sha,
    )

    _SEMANTIC_LAYER_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    json_str = render_json(semantic_doc)
    _SEMANTIC_LAYER_JSON_PATH.write_text(json_str, encoding="utf-8")
    _info(f"Wrote Semantic Layer JSON to {_SEMANTIC_LAYER_JSON_PATH}")

    md_str = render_markdown(semantic_doc)
    _SEMANTIC_LAYER_MD_PATH.write_text(md_str, encoding="utf-8")
    _info(f"Wrote Semantic Layer Markdown to {_SEMANTIC_LAYER_MD_PATH}")

    # Summary block.
    print("")
    print("Semantic Layer summary")
    print(f"  Version           : {semantic_doc.version}")
    print(f"  Tables            : {len(semantic_doc.tables)}")
    print(f"  Metrics           : {len(semantic_doc.metrics)}")
    print(f"  Dimensions        : {len(semantic_doc.dimensions)}")
    print(f"  Relationships     : {len(semantic_doc.relationships)}")
    print(f"  Assumptions       : {len(semantic_doc.assumptions)}")
    print(f"  Source SHA-256    : {semantic_doc.source_sha256[:16]}...")
    print(f"  JSON deterministic: True (no generated_at field in JSON)")


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
            table_def=orders_def,
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
    "generate-semantic-layer": None,  # v2.0 — has special arg handling in main()
    "ask": None,  # has special arg handling in main()
    "evaluate": None,  # has special arg handling in main()
    "train-sales-model": None,
    "promote-sales-model": None,
    "predict-sales": None,
}


def main(argv: list[str] | None = None) -> None:
    """CLI entrypoint: `python -m src.cli <command>`."""
    args = argv if argv is not None else sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print("Usage: python -m src.cli.main {bootstrap|teardown|validate|generate-dictionary|generate-semantic-layer|ask|chart|evaluate|train-sales-model|promote-sales-model|predict-sales|predict-sales-nl} [args]")
        print("Commands:")
        print("  bootstrap [--source PATH]        Bring up PG, load data, write manifest")
        print("  teardown [--remove-volume]        Stop & remove container (and optionally volume)")
        print("  validate                          Single pass/fail health check")
        print("  generate-dictionary [--source P]  Generate data_dictionary.md from the schema")
        print("  generate-semantic-layer            Generate .artifacts/semantic_layer.{json,md}")
        print("  ask <question> [--viewer <id>]     Translate a natural-language question to SQL")
        print("                                      (--viewer <id> resolves a person from the")
        print("                                       People table, or a custom viewer from")
        print("                                       viewers.yaml)")
        print("  chart <question> [--viewer <id>]   Translate a NL request into a saved PNG chart")
        print("                                      (runs ask, then LLM picks chart type/axes,")
        print("                                       renders to .artifacts/charts/)")
        print("  evaluate                           Run sanity-check evaluation (v1.1)")
        print("  train-sales-model                  Train, compare, and persist both sales models")
        print("  promote-sales-model --run-id ID --env {dev,staging,prod} [--force]")
        print("                                      Promote a persisted run to an environment")
        print("  predict-sales --env ENV --ship-mode ... --order-date YYYY-MM-DD")
        print("                                      Predict sales using the promoted model")
        print("  predict-sales-nl <question> --env ENV")
        print("                                      LLM extracts the prediction inputs from a")
        print("                                       natural-language question, then predicts")
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
    if command == "generate-semantic-layer":
        cmd_generate_semantic_layer()
        return
    if command == "ask":
        # Parse v2.0 flags: --viewer <id> and --allow-full-access.
        # Any remaining non-flag token is part of the question (joined).
        viewer_id: str | None = None
        allow_full = False
        question_tokens: list[str] = []
        i = 0
        while i < len(rest):
            tok = rest[i]
            if tok in ("--viewer", "-v") and i + 1 < len(rest):
                viewer_id = rest[i + 1]
                i += 2
                continue
            if tok == "--allow-full-access":
                allow_full = True
                i += 1
                continue
            question_tokens.append(tok)
            i += 1
        if not question_tokens:
            _err("Usage: python -m src.cli.main ask <question> [--viewer <id>]")
        question_text = " ".join(question_tokens)
        cmd_ask(question_text, viewer_id=viewer_id, allow_full_access=allow_full)
        return
    if command == "chart":
        # Same flag grammar as `ask`: --viewer <id>, --allow-full-access.
        chart_viewer_id: str | None = None
        chart_allow_full = False
        chart_question_tokens: list[str] = []
        i = 0
        while i < len(rest):
            tok = rest[i]
            if tok in ("--viewer", "-v") and i + 1 < len(rest):
                chart_viewer_id = rest[i + 1]
                i += 2
                continue
            if tok == "--allow-full-access":
                chart_allow_full = True
                i += 1
                continue
            chart_question_tokens.append(tok)
            i += 1
        if not chart_question_tokens:
            _err("Usage: python -m src.cli.main chart <question> [--viewer <id>]")
        cmd_chart(
            " ".join(chart_question_tokens),
            viewer_id=chart_viewer_id,
            allow_full_access=chart_allow_full,
        )
        return
    if command == "predict-sales-nl":
        nl_environment: str | None = None
        nl_question_tokens: list[str] = []
        i = 0
        while i < len(rest):
            tok = rest[i]
            if tok == "--env" and i + 1 < len(rest):
                nl_environment = rest[i + 1]
                i += 2
                continue
            nl_question_tokens.append(tok)
            i += 1
        if not nl_question_tokens or nl_environment is None:
            _err("Usage: python -m src.cli.main predict-sales-nl <question> --env <dev|staging|prod>")
        if nl_environment not in {"dev", "staging", "prod"}:
            _err(f"Invalid environment: {nl_environment!r}. Use dev|staging|prod.")
        cmd_predict_sales_nl(" ".join(nl_question_tokens), nl_environment)
        return
    if command == "evaluate":
        cmd_evaluate()
        return
    if command == "train-sales-model":
        cmd_train_sales_model()
        return
    if command == "promote-sales-model":
        run_id: str | None = None
        environment: str | None = None
        force = False
        i = 0
        while i < len(rest):
            tok = rest[i]
            if tok == "--run-id" and i + 1 < len(rest):
                run_id = rest[i + 1]
                i += 2
                continue
            if tok == "--env" and i + 1 < len(rest):
                environment = rest[i + 1]
                i += 2
                continue
            if tok == "--force":
                force = True
                i += 1
                continue
            _err("Usage: python -m src.cli.main promote-sales-model --run-id <id> --env <dev|staging|prod> [--force]")
        if run_id is None or environment is None:
            _err("Usage: python -m src.cli.main promote-sales-model --run-id <id> --env <dev|staging|prod> [--force]")
        if environment not in {"dev", "staging", "prod"}:
            _err(f"Invalid environment: {environment!r}. Use dev|staging|prod.")
        cmd_promote_sales_model(run_id, environment, force=force)
        return
    if command == "predict-sales":
        predict_environment: str | None = None
        payload: dict[str, str] = {}
        expected_flags = {
            "--ship-mode": "ship_mode",
            "--segment": "segment",
            "--region": "region",
            "--market": "market",
            "--product-id": "product_id",
            "--sub-category": "sub_category",
            "--category": "category",
            "--quantity": "quantity",
            "--discount": "discount",
            "--order-date": "order_date",
        }
        i = 0
        while i < len(rest):
            tok = rest[i]
            if tok == "--env" and i + 1 < len(rest):
                predict_environment = rest[i + 1]
                i += 2
                continue
            field_name = expected_flags.get(tok)
            if field_name is not None and i + 1 < len(rest):
                payload[field_name] = rest[i + 1]
                i += 2
                continue
            _err("Usage: python -m src.cli.main predict-sales --env <dev|staging|prod> --ship-mode ... --order-date YYYY-MM-DD")
        if predict_environment is None:
            _err("predict-sales requires --env <dev|staging|prod>.")
        if predict_environment not in {"dev", "staging", "prod"}:
            _err(f"Invalid environment: {predict_environment!r}. Use dev|staging|prod.")
        missing = [name for name in expected_flags.values() if name not in payload]
        if missing:
            _err(f"predict-sales is missing required flags for: {missing}")
        cmd_predict_sales(predict_environment, payload)
        return
    handler = _COMMANDS.get(command)
    if handler is None:
        _err(f"Unknown command: {command!r}. Use bootstrap|teardown|validate|generate-dictionary|generate-semantic-layer|ask|chart|evaluate|train-sales-model|promote-sales-model|predict-sales|predict-sales-nl.")

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
