# Plataforma de Datos y GenAI

A Data and GenAI Platform that connects Generative AI to a Data Warehouse.

> **Status**: Baseline (v0) — local PostgreSQL warehouse + data dictionary.
> Text-to-SQL (v1.0/1.1) and Semantic Layer + RLS (v2.0) are planned future
> milestones (see `specs/001-data-genai-platform-baseline/spec.md`).

## What this baseline delivers

- A locally-running, containerized PostgreSQL data warehouse loaded with the
  three relational Global Superstore tables (`Orders`, `Returns`, `People`).
- A comprehensive data dictionary document integrating Kaggle semantic
  descriptions with EDA-derived database types.
- Bootstrap, teardown, validate, and generate-dictionary CLI commands.
- A strictly-typed, engine-agnostic data-access layer that allows a seamless
  future migration to Google BigQuery.

## Prerequisites

- [Docker](https://www.docker.com/) (with Docker Compose) installed and running.
- [`uv`](https://docs.astral.sh/uv/) (manages Python and dependencies).
- The source file `Global Superstore Data.xlsx` in the repository root.

## Quickstart

```bash
# 1. Install Python + dependencies (uv pins Python 3.13 and creates the venv)
uv sync

# 2. Bring up the local PostgreSQL warehouse and load the data
uv run python -m src.cli.main bootstrap

# 3. Validate the environment
uv run python -m src.cli.main validate

# 4. (Re)generate the data dictionary (Phase 4)
uv run python -m src.cli.main generate-dictionary

# 5. Tear down the environment when done
uv run python -m src.cli.main teardown
```

See [`specs/001-data-genai-platform-baseline/quickstart.md`](specs/001-data-genai-platform-baseline/quickstart.md)
for the full validation guide.

### Clean-clone bootstrap (T026 / FR-016)

A fresh contributor, on a clean clone with only Docker + `uv` installed, can
reach a working, documented data warehouse with a single documented procedure:

```bash
# From a clean clone of the repository:
git clone <repo-url> && cd Plataforma_de_Datos_y_GenAI

# 1. Install Python 3.13 + all dependencies (deterministic, lockfile-pinned).
uv sync

# 2. Bring up PostgreSQL in Docker, run EDA on the .xlsx, create the schema,
#    load all three tables, and write the load manifest.
uv run python -m src.cli.main bootstrap

# 3. Confirm the environment is healthy (single pass/fail signal).
uv run python -m src.cli.main validate

# 4. Generate (or regenerate) the committed data dictionary.
uv run python -m src.cli.main generate-dictionary
```

**Expected outputs (FR-003 / SC-002):**

| Table | Row count | Kaggle label |
| --- | --- | --- |
| `Orders` | 51,290 | Transactional Logs |
| `Returns` | 2,033 | Reverse Logistics |
| `People` | 24 | Sales Governance |

A successful `validate` prints `VALIDATION PASSED` and exits 0, confirming:
container up, DB reachable, exactly three tables present, all non-empty with
the row counts above, the load manifest present with a matching source hash,
and the data dictionary file present.

To tear the environment down (removing the Docker container and, with
`--remove-volume`, the persisted data):

```bash
uv run python -m src.cli.main teardown --remove-volume
```

Re-running `bootstrap` after `teardown` (or after `bootstrap` on an already-
loaded warehouse) is idempotent and deterministic (FR-005 / SC-003): the
loader drops and recreates each table before re-loading, so the resulting
schema and row counts are identical across runs.

## Architecture

The platform follows the constitution in
[`.specify/memory/constitution.md`](.specify/memory/constitution.md):

- **Strictly-typed Python** (3.13+; Pyright/Pylance strict or `mypy --strict`).
- **Layered separation of concerns**: Data Engineering / AI Engineering / MLOps.
- **Portable data access**: engine-specific code confined to adapters; local
  dev uses Docker + PostgreSQL only.
- **Data Governance by default** (deferred to v2.0, but the data model is
  kept governance-ready).

See [`specs/001-data-genai-platform-baseline/plan.md`](specs/001-data-genai-platform-baseline/plan.md)
for the full project structure and design decisions.

## Project layout

```text
docker/                # PostgreSQL Docker Compose service
src/
├── contracts/        # Shared typed contracts (Pydantic v2 models)
├── data_engineering/ # EDA, ingestion, dictionary, validation
├── data_access/      # Engine-agnostic data-access layer + adapters
└── cli/              # CLI entrypoints
tests/                # contract / integration / unit tests
```
