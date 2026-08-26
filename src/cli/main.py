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
from typing import TYPE_CHECKING, Any

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

if TYPE_CHECKING:
    # Avoid top-level import cycle: MetabaseSession is used only in type hints
    # of private helpers that are invoked after the runtime import inside the
    # commands (e.g., `from src.contracts.metabase import MetabaseSession`).
    from src.contracts.metabase import MetabaseSession

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

# Load .env from the repository root so FORGE_API_KEY, POSTGRES_*, METABASE_*,
# and ENV are all available without manually sourcing .env. `override=True`
# ensures .env values take precedence over any pre-existing environment vars
# (so ENV=local in .env is respected even if the shell doesn't set it).
load_dotenv(_REPO_ROOT / ".env", override=True)
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


def cmd_ask(
    question: str,
    viewer_id: str | None = None,
    allow_full_access: bool = False,
    metabase_enabled: bool = True,
    session_id: str | None = None,
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

    v2.1 (feature 004-metabase-integration):
      - When `metabase_enabled=True` and Metabase is reachable, wires a
        `on_query_complete` callback that calls MetabaseClient.send_governed_query
        to create a card in the 'Chat Sessions' collection with the ALREADY-
        GOVERNED SQL (Principle IV preserved by design). Best-effort: failures
        are logged as warnings and do NOT break the pipeline (FR-013).
      - `--no-metabase` flag disables this integration (SC-006).
      - `--session <id>` routes the generated card into a 'Session: <id>'
        dashboard within the 'Chat Sessions' collection (US3).
    """
    from src.ai_engineering.llm_client import LlmClient
    from src.ai_engineering.pipeline import TextToSqlPipeline
    from src.contracts.semantic_layer import SemanticViewer
    from src.contracts.text_to_sql import LlmConfig, NLQuestion
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

    # --- v2.1: optional MetabaseClient (best-effort integration) ---
    metabase_client = None
    if metabase_enabled:
        try:
            from src.ai_engineering.metabase_client import MetabaseClient
            from src.contracts.metabase import MetabaseConfig

            mb_config = MetabaseConfig.from_env()
            metabase_client = MetabaseClient(mb_config)
            # Verify Metabase is reachable (best-effort probe).
            metabase_client.get_health()
        except Exception as exc:
            _info(
                f"Metabase integration disabled (best-effort): {exc}"
            )
            metabase_client = None

    # Build the on_query_complete callback (or None if Metabase is disabled).
    metabase_state = _load_metabase_state() if metabase_client is not None else None
    collection_id = metabase_state.collection_id if metabase_state is not None else None
    db_id = metabase_state.metabase_db_id if metabase_state is not None else 1

    def on_query_complete(response: object, viewer_arg: object) -> None:
        # The callback is invoked by the pipeline at the end of a successful run.
        # Best-effort: never raises (errors are logged inside send_governed_query).
        if metabase_client is None:
            return
        # Cast viewer to its real type for the type checker.
        v = viewer_arg if isinstance(viewer_arg, SemanticViewer) else None
        card = metabase_client.send_governed_query(
            response=response,  # type: ignore[arg-type]
            viewer=v,
            session_id=session_id,
            collection_id=collection_id,
            db_id=db_id,
        )
        if card is not None:
            _info(f"Metabase card created: id={card.id} name={card.name!r}")

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
            on_query_complete=on_query_complete,
        )
        response = pipeline.run(NLQuestion(text=question))

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


# ---------------------------------------------------------------------------
# v2.1 Metabase integration (feature 004)
# ---------------------------------------------------------------------------

_METABASE_STATE_PATH = _REPO_ROOT / ".artifacts" / "metabase_state.json"


def _wait_for_metabase_health(timeout_s: int = 120) -> bool:
    """Poll `GET /api/health` until 200 OK or timeout. Returns True on ready."""
    import urllib.request

    deadline = time.time() + timeout_s
    url = f"{os.environ.get('METABASE_HOST', 'http://localhost:3000')}/api/health"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as _:
                return True
        except Exception:
            time.sleep(2)
    return False


def _compose_metabase(action_args: list[str]) -> None:
    """Run `docker compose -f <compose> <action_args...> metabase`.

    The action args are a separate list so that "up -d" is passed as two args
    (["up", "-d"]) rather than a single concatenated token.
    """
    cmd = ["docker", "compose", "-f", str(_COMPOSE_FILE), *action_args, "metabase"]
    subprocess.run(cmd, check=True)


def _save_metabase_state(state: MetabaseSession) -> None:
    """Persist the MetabaseSession to .artifacts/metabase_state.json."""
    _METABASE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _METABASE_STATE_PATH.write_text(
        state.model_dump_json(indent=2), encoding="utf-8"
    )


def _load_metabase_state() -> MetabaseSession | None:
    """Load the persisted MetabaseSession, or None if not yet set up."""
    if not _METABASE_STATE_PATH.exists():
        return None
    from src.contracts.metabase import MetabaseSession

    try:
        return MetabaseSession.model_validate_json(
            _METABASE_STATE_PATH.read_text(encoding="utf-8")
        )
    except Exception:
        return None


def cmd_metabase_setup() -> None:
    """metabase setup: bring up the Metabase container + automate the initial setup.

    Idempotent: if Metabase is already configured (setup-token is null),
    skips admin user creation. Persists state to .artifacts/metabase_state.json
    for use by subsequent commands.

    Requires Docker, the warehouse (PostgreSQL) running, and METABASE_*
    env vars.
    """
    from src.ai_engineering.metabase_client import MetabaseClient
    from src.contracts.metabase import MetabaseConfig, MetabaseSession
    from src.data_access.adapters.postgres.roles import (
        ensure_metabase_readonly_role,
    )

    if not _docker_available():
        _err("Docker is not running. Run `bootstrap` first to start the warehouse.")

    # 1. Bring up the Metabase container.
    _info("Starting Metabase container (this may take a minute on first pull)...")
    try:
        _compose_metabase(["up", "-d"])
    except subprocess.CalledProcessError as exc:
        _err(
            f"docker compose up failed for metabase: "
            f"{exc.stderr if exc.stderr else exc}"
        )

    # 2. Wait for healthcheck.
    _info("Waiting for Metabase healthcheck...")
    if not _wait_for_metabase_health():
        _err("Metabase did not become healthy within the timeout.")

    # 3. Load config (env vars).
    try:
        mb_config = MetabaseConfig.from_env()
    except ValueError as exc:
        _err(str(exc))

    client = MetabaseClient(mb_config)

    # 4. Create the metabase_readonly PG role (defense-in-depth; Principle IV).
    _info("Ensuring metabase_readonly PG role with SELECT grants...")
    config = PostgresConfig.from_env()
    try:
        with PostgresRepository(config=config) as repo:
            ensure_metabase_readonly_role(
                repo._conn, mb_config.admin_password
            )
    except Exception as exc:
        _err(f"Failed to create metabase_readonly role: {exc}")

    # 5. Auto-setup admin user (idempotent via is_setup_complete check).
    _info("Checking Metabase setup status...")
    if client.is_setup_complete():
        _info("  Metabase already configured (admin user present); skipping setup_initial().")
    else:
        _info("  Running setup_initial() to create the admin user...")
        client.setup_initial(mb_config.admin_email, mb_config.admin_password)

    # 6. Create the database connection to PostgreSQL (metabase_readonly role).
    _info("Creating Metabase → PostgreSQL database connection...")
    db_id = client.create_db_connection(
        pg_host=os.environ.get("POSTGRES_HOST", "localhost"),
        pg_port=int(os.environ.get("POSTGRES_PORT", "5432")),
        pg_database=os.environ.get("POSTGRES_DB", "global_superstore"),
        pg_user="metabase_readonly",
        pg_password=mb_config.admin_password,  # reuse local-only password
        db_name=mb_config.db_name,
    )
    if db_id is None:
        _err("Failed to create Metabase database connection.")

    # 7. Create the "Chat Sessions" collection.
    _info('Creating Metabase "Chat Sessions" collection...')
    collection = client.get_or_create_collection(mb_config.collection_name)
    if collection is None:
        _err('Failed to create Metabase collection "Chat Sessions".')

    # 8. Persist state.
    version = client.get_version()
    state = MetabaseSession(
        configured_at=datetime.now(timezone.utc),
        admin_email=mb_config.admin_email,
        metabase_db_id=db_id or 0,
        collection_id=collection.id if collection else 0,
        metabase_version=version,
    )
    _save_metabase_state(state)

    # 9. Print summary.
    _info("Metabase setup complete.")
    print("")
    print("Metabase summary")
    print(f"  URL              : {mb_config.host}")
    print(f"  Admin email      : {mb_config.admin_email}")
    print(f"  Metabase version : {version}")
    print(f"  DB connection id : {state.metabase_db_id} (PostgreSQL via metabase_readonly)")
    print(f"  Collection id    : {state.collection_id} ({mb_config.collection_name!r})")
    print(f"  State persisted  : {_METABASE_STATE_PATH}")
    print("")
    print(f"Open {mb_config.host} and login with the admin credentials.")


def cmd_metabase_status() -> None:
    """metabase status: print a readable summary of the Metabase instance."""
    from src.ai_engineering.metabase_client import MetabaseClient
    from src.contracts.metabase import MetabaseConfig

    try:
        mb_config = MetabaseConfig.from_env()
    except ValueError as exc:
        _err(str(exc))
    client = MetabaseClient(mb_config)

    health = client.get_health()
    version = client.get_version()
    state = _load_metabase_state()
    cards_count: int = 0
    if state is not None:
        cards = client.list_cards_in_collection(state.collection_id)
        cards_count = len(cards)

    print("Metabase status")
    print(f"  URL              : {mb_config.host}")
    print(f"  Version          : {version}")
    print(f"  Health           : {health}")
    print(f"  Admin email      : {state.admin_email if state else 'not set up'}")
    print(f"  DB connection id : {state.metabase_db_id if state else 'n/a'}")
    print(f"  Collection id    : {state.collection_id if state else 'n/a'}")
    print(f"  Cards in 'Chat Sessions': {cards_count}")


def cmd_metabase_teardown(remove_volume: bool = False) -> None:
    """metabase teardown: stop and remove the Metabase container (+ optional volume)."""
    if not _docker_available():
        _err("Docker is not running; nothing to tear down for Metabase.")

    _info("Stopping Metabase container...")
    try:
        _compose_metabase(["stop"])
        _compose_metabase(["rm", "-f"])
    except subprocess.CalledProcessError as exc:
        _err(f"Metabase teardown failed: {exc}")

    if remove_volume:
        result = subprocess.run(
            ["docker", "volume", "rm", "plataforma_metabase_data"],
            capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            _info(f"Note: could not remove volume (may already be gone): {result.stderr.strip()}")

    _info("Metabase teardown complete. PG warehouse remains running.")


def cmd_metabase_reset_cards() -> None:
    """metabase reset-cards: delete every card in the 'Chat Sessions' collection."""
    from src.ai_engineering.metabase_client import MetabaseClient
    from src.contracts.metabase import MetabaseConfig

    state = _load_metabase_state()
    if state is None:
        _err("Metabase state not found; run `metabase setup` first.")

    try:
        mb_config = MetabaseConfig.from_env()
    except ValueError as exc:
        _err(str(exc))
    client = MetabaseClient(mb_config)

    cards = client.list_cards_in_collection(state.collection_id)
    if not cards:
        _info("No cards to delete in 'Chat Sessions' collection.")
        return

    deleted = 0
    for card in cards:
        if client.delete_card(card.id):
            deleted += 1

    _info(f"Deleted {deleted}/{len(cards)} cards from 'Chat Sessions' (admin user + DB connection intact).")


def cmd_metabase_cards() -> None:
    """metabase cards: list all cards in the 'Chat Sessions' collection."""
    from src.ai_engineering.metabase_client import MetabaseClient
    from src.contracts.metabase import MetabaseConfig

    state = _load_metabase_state()
    if state is None:
        _err("Metabase state not found; run `metabase setup` first.")

    try:
        mb_config = MetabaseConfig.from_env()
    except ValueError as exc:
        _err(str(exc))
    client = MetabaseClient(mb_config)

    cards = client.list_cards_in_collection(state.collection_id)
    if not cards:
        print("No cards found in 'Chat Sessions' collection.")
        return

    print(f"Cards in 'Chat Sessions' (collection_id={state.collection_id}):")
    for card in cards:
        desc = card.description or ""
        # Extract viewer_id from description if present
        viewer_id = "unknown"
        if "viewer_id=" in desc:
            viewer_id = desc.split("viewer_id=")[1].split()[0]
        print(f"  id={card.id:>4}  name={card.name!r}  display={card.display}  viewer={viewer_id}")


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
    "metabase": None,  # v2.1 — subcommands dispatched in main()
    "ask": None,  # has special arg handling in main()
    "evaluate": None,  # has special arg handling in main()
}


def main(argv: list[str] | None = None) -> None:
    """CLI entrypoint: `python -m src.cli <command>`."""
    args = argv if argv is not None else sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print("Usage: python -m src.cli.main {bootstrap|teardown|validate|generate-dictionary|generate-semantic-layer|ask|evaluate} [args]")
        print("Commands:")
        print("  bootstrap [--source PATH]        Bring up PG, load data, write manifest")
        print("  teardown [--remove-volume]        Stop & remove container (and optionally volume)")
        print("  validate                          Single pass/fail health check")
        print("  generate-dictionary [--source P]  Generate data_dictionary.md from the schema")
        print("  generate-semantic-layer            Generate .artifacts/semantic_layer.{json,md}")
        print("  metabase <setup|...>                v2.1 Metabase integration (`metabase setup` to bootstrap)")
        print("  ask <question> [--viewer <id>] [--no-metabase] [--session <id>]")
        print("                                      Translate a natural-language question to SQL")
        print("                                      (--viewer <id> resolves a person from the")
        print("                                       People table, or a custom viewer from")
        print("                                       viewers.yaml; --no-metabase skips Metabase;")
        print("                                       --session <id> groups cards under a dashboard)")
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
    if command == "generate-semantic-layer":
        cmd_generate_semantic_layer()
        return
    if command == "metabase":
        # Subcommands: setup | status | teardown | reset-cards.
        # T011 US1: metabase setup. T029-T031 US4: the others.
        if not rest:
            _err("Usage: python -m src.cli.main metabase <setup|status|teardown|reset-cards|cards>")
        sub = rest[0]
        sub_rest = rest[1:]
        # T011 / T012-T014 (US1)
        if sub == "setup":
            cmd_metabase_setup()
            return
        # The rest are implemented in Phase 6 (US4); fail-fast until then.
        if sub in {"status", "teardown", "reset-cards"}:
            if sub == "status":
                cmd_metabase_status()
                return
            if sub == "teardown":
                remove_vol = "--remove-volume" in sub_rest
                cmd_metabase_teardown(remove_volume=remove_vol)
                return
            if sub == "reset-cards":
                cmd_metabase_reset_cards()
                return
        if sub == "cards":
            cmd_metabase_cards()
            return
        _err(f"Unknown metabase subcommand: {sub!r}. Use setup|status|teardown|reset-cards|cards.")
        return
    if command == "ask":
        # Parse v2.0/v2.1 flags: --viewer <id>, --allow-full-access,
        # --no-metabase, --session <id>.
        # Any remaining non-flag token is part of the question (joined).
        viewer_id: str | None = None
        allow_full = False
        metabase_enabled = True
        session_id: str | None = None
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
            if tok == "--no-metabase":
                metabase_enabled = False
                i += 1
                continue
            if tok == "--session" and i + 1 < len(rest):
                session_id = rest[i + 1]
                i += 2
                continue
            question_tokens.append(tok)
            i += 1
        if not question_tokens:
            _err("Usage: python -m src.cli.main ask <question> [--viewer <id>] [--no-metabase] [--session <id>]")
        question_text = " ".join(question_tokens)
        cmd_ask(
            question_text,
            viewer_id=viewer_id,
            allow_full_access=allow_full,
            metabase_enabled=metabase_enabled,
            session_id=session_id,
        )
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
