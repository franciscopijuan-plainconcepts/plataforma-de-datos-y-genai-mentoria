# Plataforma de Datos y GenAI

A Data and GenAI Platform that connects Generative AI to a Data Warehouse.

> **Status**: v2.0 — Baseline + Text-to-SQL + Semantic Layer (with RLS governance).
> The warehouse, data dictionary, NL→SQL pipeline, and governed Semantic Layer
> are all delivered. See `specs/` for the feature_specs and
> `README_STATUS.md` for the current roadmap (milestones M0–M3 delivered).

## What the platform delivers

### Baseline (M0 / v0)

- A locally-running, containerized PostgreSQL data warehouse loaded with the
  three relational Global Superstore tables (`Orders`, `Returns`, `People`).
- A comprehensive data dictionary document integrating Kaggle semantic
  descriptions with EDA-derived database types.
- Bootstrap, teardown, validate, and generate-dictionary CLI commands.
- A strictly-typed, engine-agnostic data-access layer that allows a seamless
  future migration to Google BigQuery.

### Text-to-SQL (M1+M2 / v1.0 + v1.1)

- Natural-language → SQL pipeline (`ask` command) over the `Orders` table:
  prompt → LLM (Forge proxy) → validated SQL → executed → typed result rows.
- Structured logging (`evaluate` command, sanity-check over ~10 questions).
- Strong SQL validator: SELECT-only, no forbidden keywords, Orders-only columns.

### Semantic Layer (M3 / v2.0)

- A governed Semantic Layer (`SemanticLayerDocument` artifact regeneratable
  via `generate-semantic-layer`) that declares 8 metrics (incl. net sales,
  return rate, net profit), 11 dimensions, and cross-table relationships.
- Row-Level Security (RLS) enforced at the Semantic Layer boundary per
  constitution Principle IV (NON-NEGOTIABLE). No LLM-generated SQL can bypass
  `WHERE "Region" IN (viewer.regions)` filtering.
- Viewer-based governance: configure `viewers.yaml` (optional, only for escape
  hatches like `admin_dev`), then `ask --viewer alice` scopes results to
  Alice's regions. By default, `ask --viewer <person>` resolves a real person
  from the `People` table (e.g., `marilene_rousseau`, `Marilène Rousseau`,
  or `Marilene Rousseau`) and uses that person's region — no YAML needed for
  real-person logins.
- Prompt enrichment: when the Semantic Layer is loaded, the prompt includes
  metrics + dimensions + Returns-join notes so the LLM distinguishes
  gross_sales from net_sales.

## Team Handoff Docs

- [README_SPECKIT.md](README_SPECKIT.md): how Spec Kit was used in this repo,
  folder-by-folder explanation of OpenSpec artifacts, and a continuation
  playbook for future features.
- [README_STATUS.md](README_STATUS.md): current project status, delivered scope,
  risks, roadmap milestones, and next recommended execution steps.

## Prerequisites

- [Docker](https://www.docker.com/) (with Docker Compose) installed and running.
- [`uv`](https://docs.astral.sh/uv/) (manages Python and dependencies).
- The source file `Global Superstore Data.xlsx` in the repository root.
- A `.env` file with `FORGE_API_KEY` set (copy `.env.example` and fill it).
- For Semantic Layer RLS: real-person viewers resolve directly from the
  `People` table (e.g., `--viewer marilene_rousseau`); no YAML needed.
  A `viewers.yaml` file is only required for escape hatches like `admin_dev`
  (copy `viewers.example.yaml` if you need them).

## Quickstart

```bash
# 1. Install Python + dependencies (uv pins Python 3.13 and creates the venv)
uv sync

# 2. Bring up the local PostgreSQL warehouse and load the data
uv run python -m src.cli.main bootstrap

# 3. Validate the environment
uv run python -m src.cli.main validate

# 4. (Re)generate the data dictionary
uv run python -m src.cli.main generate-dictionary

# 5. Generate the Semantic Layer artifact (metrics, dimensions, RLS metadata)
uv run python -m src.cli.main generate-semantic-layer

# 6. Ask a natural-language question (RLS-scoped by the active viewer)
#    Login as a real person (resolved from the People table — no YAML needed):
uv run python -m src.cli.main ask --viewer marilene_rousseau "What is the total sales amount?"
#    Or escape-hatch in local/dev (no RLS):
# uv run python -m src.cli.main ask --allow-full-access "What is the total sales amount?"

# 7. Tear down the environment when done
uv run python -m src.cli.main teardown
```

See [`specs/001-data-genai-platform-baseline/quickstart.md`](specs/001-data-genai-platform-baseline/quickstart.md)
for the baseline validation guide and
[`specs/003-semantic-layer-v1/quickstart.md`](specs/003-semantic-layer-v1/quickstart.md)
for the Semantic Layer RLS validation guide.

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
