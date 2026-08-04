<!--
=== Sync Impact Report ===
Version change: (unset) → 1.0.0
Trigger: Initial adoption (constitution was a pristine, unfilled template with all
  [PLACEHOLDER] tokens still present).

Modified principles: n/a (initial adoption — all principles are new)
  - I.  Strictly-Typed Python Foundation            (new)
  - II. Layered Separation of Concerns              (new, NON-NEGOTIABLE)
  - III.Portable Data Access & Abstraction          (new)
  - IV. Data Governance by Default                  (new, NON-NEGOTIABLE)
  - V.  Reproducible MLOps                          (new)

Added sections:
  - Core Principles (I-V)
  - Technology & Infrastructure Constraints
  - Development Workflow & Quality Gates
  - Governance

Removed sections: n/a

Follow-up TODOs / deferred placeholders: none. All template tokens resolved.
  RATIFICATION_DATE = 2026-07-30 (initial adoption)
  LAST_AMENDED_DATE = 2026-07-30 (this is the initial fill)
  CONSTITUTION_VERSION = 1.0.0 (MAJOR: initial adoption of governance)
===
-->

# Plataforma de Datos y GenAI Constitution

The bare-minimum, non-negotiable core architectural rules for the Data and
GenAI Platform. These principles govern every feature, spec, and review.

## Core Principles

### I. Strictly-Typed Python Foundation

- Python 3.11+ is the sole implementation language across all platform
  layers (Data Engineering, AI Engineering, MLOps).
- Strict static typing is MANDATORY: type hints on EVERY function and method
  signature (public and private) and on all module-level variables where the
  type is not literally obvious.
- Static type-checking (Pyright/Pylance in strict mode, or `mypy --strict`)
  MUST pass with zero errors for code to be considered mergeable.
- `Any` MUST NOT be used without an inline justification
  (`# type: ignore[...]` with rationale, or an explicit cast + comment).
- Typed I/O contracts (Pydantic v2 models or frozen `dataclasses`) are REQUIRED
  at every module and layer boundary; raw `dict` / untyped payloads MUST NOT
  cross boundaries.

### II. Layered Separation of Concerns (NON-NEGOTIABLE)

The platform is organized into three strictly isolated engineering domains.
Dependency direction is one-way and documented; no domain MAY import the
internal implementation of another.

- **Data Engineering**: ingestion pipelines and ETL/ELT (raw → curated data
  movement), schema/materialization, dataset lifecycle.
- **AI Engineering**: LLM interactions, prompt orchestration, Text-to-SQL
  logic, and semantic translation against the Semantic Layer.
- **MLOps**: model & AI-artifact lifecycle — experiment tracking,
  versioning, staged deployment, and production monitoring of AI artifacts.

Rules:

- Cross-domain communication MUST occur ONLY through explicitly declared,
  typed contracts (Protocols/Interfaces) — never via direct imports of one
  domain's internal modules into another.
- Shared contracts live in a dedicated, dependencies-downward `contracts`
  (or equivalent) location; domains depend on contracts, not on each other.
- No upward dependencies: MLOps/AI Engineering MUST NOT be imported by Data
  Engineering ingestion code; Data Engineering exposes data via contracts.

### III. Portable Data Access & Abstraction

- Local development MUST run entirely inside Docker; PostgreSQL is the ONLY
  local data store. No host-installed databases are permitted.
- All data access (reads and writes) MUST flow through a modular, abstracted
  data-access layer (Repository / Provider pattern) behind typed interfaces.
- No business, AI, or MLOps code MAY hold a direct, engine-specific dependency
  (e.g., `psycopg2`, `google-cloud-bigquery`) — implementations MUST be
  injected behind interfaces.
- The abstraction MUST allow a seamless future migration to Google BigQuery:
  swapping the PostgreSQL adapter for a BigQuery adapter MUST require NO
  change to upstream business, AI, or MLOps logic. Engine-specific SQL is
  confined to adapter implementations only.

### IV. Data Governance by Default (NON-NEGOTIABLE)

Data Governance is an inherent property of the platform, not an add-on.

- Access control is enforced at the **Semantic Layer** — never at the
  application query layer or inside Text-to-SQL output.
  - **Role-Based Access Control (RBAC)**: every dataset, table, and column
    MUST declare required roles; access requests MUST be resolved against
    identity/roles before any data is returned.
  - **Row-Level Security (RLS)**: tenant/user scoping MUST be applied by the
    Semantic Layer and enforced as close to the data as possible (PostgreSQL
    RLS policies locally, equivalent BigQuery policies on migration).
- No LLM-generated SQL (Text-to-SQL) MAY bypass Semantic Layer RBAC/RLS
  resolution. Generated SQL MUST be rewritten/filtered by the Semantic Layer
  or executed only against governance-enforced views.
- PII handling, audit logging of access events, and data lineage MUST be wired
  into the Semantic Layer from the first feature, not retrofitted later.

### V. Reproducible MLOps

- Every model, prompt, and AI artifact MUST be versioned and traceable to
  its source data and code commit.
- Experiment tracking (parameters, metrics, artifacts) is MANDATORY for any
  model training, fine-tuning, or prompt-evaluation run.
- Models/prompts MUST be promoted through staged environments
  (dev → staging → prod) with explicit approval gates; no direct-to-prod
  deployment.
- Production inference MUST be observable: input/output (subject to
  governance), latency, and drift signals logged for every call.

## Technology & Infrastructure Constraints

- **Approved runtime**: Python 3.11+, strict static type-checking
  (Pyright/Pylance strict or `mypy --strict`).
- **Local environment**: Docker Compose only; PostgreSQL is the sole local
  data store.
- **Target cloud data warehouse (future)**: Google BigQuery. The data-access
  layer MUST remain engine-agnostic until migration is initiated.
- **Data contracts** between layers MUST be Pydantic v2 models or frozen
  `dataclasses`.
- **Semantic Layer** is the single authoritative boundary for RBAC/RLS,
  audit logging, and lineage.
- **Secrets**: never in source. Configuration via environment variables /
  secret manager; `.env` is gitignored.
- **Dependency management**: lockfile-based and reproducible
  (e.g., `uv.lock` or `poetry.lock`) with pinned versions.
- **No engine-specific code** outside the data-access adapter implementations.

## Development Workflow & Quality Gates

- Type-checking MUST pass (`mypy --strict` or Pyright strict) on every commit
  and PR; CI failing on type errors blocks merge.
- Every cross-layer and cross-domain boundary MUST have contract tests.
- Any new Text-to-SQL or LLM feature MUST ship with a Semantic-Layer
  RBAC/RLS enforcement test proving governance is applied before data is
  returned.
- Integration tests run against the Dockerized PostgreSQL instance (no mocked
  data stores for governance/enforcement paths).
- Code review MUST verify: (a) no engine-specific leakage outside the
  data-access layer, (b) no cross-domain internal imports, (c) typing
  present on all public APIs, (d) governance enforcement tested.
- A feature is NOT "done" until typing, governance enforcement, and contract
  tests pass.

## Governance

- This Constitution is the highest-authority governance document for the
  Plataforma de Datos y GenAI; it supersedes other practices where they
  conflict.
- Amendments REQUIRE: a written proposal, documented rationale, an impact
  assessment on existing code/contracts, and approval before merge.
- **Versioning follows Semantic Versioning**:
  - MAJOR: principle removal or backward-incompatible redefinition.
  - MINOR: new principle/section added or materially expanded guidance.
  - PATCH: clarifications, wording, typo fixes, non-semantic refinements.
- Every PR/review MUST verify constitutional compliance; unaddressed
  violations MUST be called out in review and block merge.
- Complexity MUST be justified against these principles; favor simplicity
  (YAGNI) within the constraints above.
- Runtime development guidance lives in this file
  (`.specify/memory/constitution.md`); feature-level detail lives in feature
  specs under `.specify/`.

**Version**: 1.0.0 | **Ratified**: 2026-07-30 | **Last Amended**: 2026-07-30
