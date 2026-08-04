# Quickstart: Data and GenAI Platform – Baseline

**Feature**: 001-data-genai-platform-baseline
**Date**: 2026-07-30
**Related**: [spec.md](./spec.md) · [plan.md](./plan.md) · [research.md](./research.md) · [data-model.md](./data-model.md) · [contracts/](./contracts/) · [.env.example](../../.env.example)

> Runnable validation guide proving the baseline works end-to-end. Covers prerequisites, setup, validation commands, and expected outcomes. Implementation detail lives in `tasks.md` (Phase 2); this is a validation/run guide only.

## Prerequisites

- **Docker** (with Docker Compose) installed and running. Verify: `docker --version` and `docker compose version` both succeed.
- **`uv`** installed (the project's package manager; also manages the Python version). Verify: `uv --version`.
- **Git** (for provenance — the load manifest records the git commit).
- **Source file**: `Global Superstore Data.xlsx` present in the repository root.

> The baseline does NOT require a host-installed PostgreSQL or Python — `uv` pins Python, and PostgreSQL runs in Docker. See constitution Principle III.

## Setup (one-time bootstrap from a clean clone)

```bash
# 1. Install Python deps (uv pins Python 3.11+ and creates the venv)
uv sync

# 2. Bring up the local PostgreSQL warehouse in Docker
uv run python -m src.cli bootstrap
```

**Expected outcome**: A Docker container running PostgreSQL is up; the EDA script runs over `Global Superstore Data.xlsx`; the inferred schema is materialized; all three tables (`Orders`, `Returns`, `People`) are created and populated; the load artifact manifest is written; the data dictionary is generated.

**Expected timing**: Under 5 minutes on a standard developer laptop (spec SC-001).

> If `bootstrap` fails: check the typed error message (FR-013). Common fail-fast causes: Docker not running, port `5432` (or configured port) in use, source `.xlsx` missing/corrupt, EDA发现自己的类型不匹配. The system leaves the warehouse in a known (unloaded or rolled-back) state — never a partial load (FR-015).

## Validation (proving the baseline works)

### A. Environment + data load

Run the single validation command:

```bash
uv run python -m src.cli validate
```

**Expected outcome** (spec FR-014 / SC-007): a single pass/fail signal that confirms:

| Check | Expected |
|---|---|
| Container up | PostgreSQL container running |
| Database reachable | Connection succeeds with configured credentials |
| Three tables present | `Orders`, `Returns`, `People` exist in the dedicated schema |
| All three non-empty | `Orders` = 51,290 rows; `Returns` = 2,033 rows; `People` = 24 rows (EDA-derived counts) |
| Dictionary file present | `data_dictionary.md` exists and is non-empty |
| Load manifest present | `LoadArtifactManifest` JSON/YAML exists; `source_sha256` matches the `.xlsx`; `rows_loaded` per table matches EDA counts |

Exit code `0` = all checks pass (spec SC-007: validation completes within 30 seconds).

### B. Queryable warehouse (manual spot-check)

Connect to the warehouse and confirm the tables are queryable:

```bash
# Connect via psql in the running container
docker compose -f docker/docker-compose.yml exec postgres psql -U <user> -d <db>

# Inside psql, run:
SELECT count(*) FROM orders;   -- expect: 51290
SELECT count(*) FROM returns;  -- expect: 2033
SELECT count(*) FROM people;   -- expect: 24
```

**Expected outcome** (spec FR-003 / SC-002): each table returns the non-zero row count above.

### C. Data dictionary readable

Open `data_dictionary.md` and confirm (spec FR-008/FR-009/FR-010/FR-011 / SC-004/SC-005):

- Three sections exist: `Orders` (Transactional Logs), `Returns` (Reverse Logistics), `People` (Sales Governance).
- Every column has: name, business description, type, nullable, key flag, allowed values (for enum-like), data-quality notes.
- Cross-table relationships are documented: `Returns.Order ID → Orders.Order ID`; `People.Region → Orders.Region`; `People.Region → Returns.Region`.
- A previously-onboarded stakeholder can find any column (e.g., `Discount`, `Profit`, `Region`) within 30 seconds (SC-005).

### D. Reproducibility (spec SC-003)

```bash
# 1. Tear down the environment
uv run python -m src.cli teardown

# 2. Re-bootstrap from the same source
uv run python -m src.cli bootstrap
uv run python -m src.cli validate
```

**Expected outcome**: identical schema and row counts across all three tables (deterministic). The `source_sha256` in the new manifest MUST match the previous run.

## Teardown

```bash
uv run python -m src.cli teardown
```

**Expected outcome** (spec FR-007 / SC-006): the PostgreSQL container stops and is removed; persisted data is removed (when configured); no orphaned Docker resources remain. Verify with `docker ps` (no `postgres` container for this project) and `docker volume ls`.

## What This Proves

- **Story 1 (P1)**: A queryable local PostgreSQL warehouse with the three Global Superstore tables — validated via checks A and B.
- **Story 2 (P1)**: A comprehensive data dictionary integrating Kaggle semantics with EDA-derived types — validated via check C.
- **Story 3 (P2)**: Deterministic reproduction from a clean clone — validated via check D and the `uv sync` bootstrap.

## Out of Scope for this Quickstart

- Text-to-SQL queries on `Orders` — v1.0/1.1 scope.
- Semantic Layer / RBAC / RLS on `Returns`/`People` — v2.0 scope.
- Dashboard UI, cloud deployment, model training — out of scope for the baseline.

See [spec.md § Scope Summary](./spec.md) for the full roadmap context.
