# Feature Specification: Data and GenAI Platform – Baseline

**Feature Branch**: `001-data-genai-platform-baseline`

**Created**: 2026-07-30

**Status**: Draft

**Input**: User description: "Create the baseline specification for a Data and GenAI Platform that connects Generative AI to a Data Warehouse. The data source is a relational version of the Global Superstore Dataset consisting of three tables: Orders, Returns, and People. The immediate scope for this week is to deploy a local PostgreSQL database via Docker containing these three tables and to generate a comprehensive data dictionary document. Subsequent milestones include: Version 1.0/1.1 will implement Text-to-SQL capabilities on the 'Orders' table to translate natural language into SQL for data retrieval and dashboarding. Version 2.0 will introduce a Semantic Layer and Data Governance, using the 'Returns' table to model business logic (e.g., net vs. gross sales) and the 'People' table to implement Row-Level Security (RLS), ensuring users can only query data corresponding to their assigned regions."

## Scope Summary

This specification establishes the **baseline (v0)** of the Plataforma de Datos y GenAI: a local, containerized PostgreSQL data warehouse containing a relational version of the Global Superstore Dataset (three tables: `Orders`, `Returns`, `People`) accompanied by a comprehensive data dictionary.

It deliberately defers the higher-milestone capabilities below and scopes them out of this baseline so the data foundation is delivered first:

- **Deferred to v1.0 / v1.1 (Text-to-SQL on `Orders`)**: a natural-language interface that translates user questions into SQL queries for data retrieval and dashboarding.
- **Deferred to v2.0 (Semantic Layer & Data Governance)**: business-logic modeling on `Returns` (net vs gross sales) and Row-Level Security on `People` (regional scoping) at the Semantic Layer.

These deferred milestones are captured here as context — to frame the data model the baseline must support — and will be specified in detail in their own feature specs.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Provision and inspect the local Data Warehouse (Priority: P1)

A Data Engineer or AI Engineer needs a fully running, containerized PostgreSQL data warehouse on their local machine, containing the three Global Superstore tables loaded with the provided dataset, so they can query and explore the data immediately without any manual installation steps.

**Why this priority**: This is the foundational deliverable of the baseline. Every subsequent milestone (Text-to-SQL, Semantic Layer, Governance) depends on a populated, queryable warehouse. Without this, no value can be demonstrated.

**Independent Test**: Can be fully verified by running a single command that brings up the local environment, connecting to the database, and confirming each of the three tables exists and is non-empty via a simple query or a provided validation script. At the end, a user can run `SELECT count(*) FROM orders; / returns; / people;` and get non-zero row counts, delivering "a queryable local warehouse".

**Acceptance Scenarios**:

1. **Given** a developer's machine with Docker installed, **When** they run the provided bootstrap command, **Then** a PostgreSQL database is running in a container and reachable on the documented local port within a few minutes.
2. **Given** the PostgreSQL container is running, **When** a user connects with a SQL client using the documented credentials, **Then** they can access a dedicated database/schema containing exactly the `Orders`, `Returns`, and `People` tables.
3. **Given** the container is running and tables exist, **When** the user queries each table, **Then** each table returns non-zero row counts reflecting the loaded Global Superstore Dataset.
4. **Given** the running environment is no longer needed, **When** the user runs the provided teardown command, **Then** the container (and optionally its persisted data) is stopped and removed cleanly without leaving orphaned resources.

---

### User Story 2 - Read the data dictionary to understand the warehouse (Priority: P1)

A Data Engineer, AI Engineer, or business stakeholder needs a comprehensive data dictionary document that describes every table and column in the warehouse — its meaning, data type, allowed values, and relationships — so they can write accurate queries, design future Text-to-SQL prompts, and reason about business semantics without inspecting raw DDL.

**Why this priority**: P1 because the data dictionary is a required artifact for this baseline and is the contractual bridge between the data layer (this milestone) and the AI/governance layers (later milestones). Text-to-SQL design (v1.0/1.1) and Semantic Layer business logic (v2.0) both depend on documented column semantics; delivering the dictionary now unblocks all downstream work asynchronously.

**Independent Test**: Can be verified by opening the generated data dictionary document and confirming it contains one complete, human-readable entry per column across all three tables (`Orders`, `Returns`, `People`), including a description and data type. A reviewer who has never seen the dataset should be able to answer "what does the `Discount` column mean and what kind of values does it hold?" using only the document.

**Acceptance Scenarios**:

1. **Given** the data dictionary document has been generated, **When** a reader opens it, **Then** they find a clearly structured section for each of the three tables (`Orders`, `Returns`, `People`).
2. **Given** a table section in the dictionary, **When** the reader looks at any column entry, **Then** the entry includes, at minimum: column name, business description, data type, and whether it is a key/identifier or nullable.
3. **Given** the three tables share related columns (e.g., `Order ID` appears in both `Orders` and `Returns`), **When** the reader consults the dictionary, **Then** cross-table relationships (foreign-key-like links) are documented so the reader understands how tables join.
4. **Given** a stakeholder needs to understand an aggregate metric concept (e.g., "gross sales" vs the later "net vs gross" v2.0 logic), **When** they read the relevant `Orders`/`Returns` column descriptions, **Then** the raw ingredients of that metric are documented even though the derived business logic itself is deferred to v2.0.

---

### User Story 3 - Validate reproducibility of the baseline environment (Priority: P2)

A new team member (or the same engineer on a fresh machine) needs to reproduce the entire local warehouse + data dictionary baseline from a clean clone of the repository, so the team can guarantee any contributor can get to a working, documented data warehouse with a single, deterministic procedure.

**Why this priority**: P2 because it amplifies the value of Stories 1 and 2 (a one-off working environment matters less than a reproducible one), but the one-off "it works on my machine" path is the more immediate P1. This story locks in the *deterministic* property for everyone else.

**Independent Test**: Can be verified by deleting the running environment, removing any local state, and re-running the bootstrap from a clean clone; the outcome must match Stories 1 and 2 exactly, deterministically, with no manual fix-up steps.

**Acceptance Scenarios**:

1. **Given** a clean clone of the repository on a fresh machine with Docker available, **When** a contributor runs the documented bootstrap command, **Then** they reach the same end state as Story 1 (running PostgreSQL, three populated tables) with no manual intervention.
2. **Given** the bootstrap completes successfully on the fresh machine, **When** the contributor regenerates or opens the data dictionary, **Then** the dictionary content matches the canonical committed version (or is deterministically regeneratable from the loaded schema).
3. **Given** a contributor needs to verify the baseline is healthy, **When** they run a validation/check command, **Then** a single command confirms: container up, database reachable, three tables present, all three non-empty, and dictionary file present.

---

### Edge Cases

- **What happens when Docker is not installed or not running?** The bootstrap MUST detect this prerequisite and fail fast with a clear, actionable error explaining that Docker is required before continuing, rather than starting and partially failing.
- **What happens when the port configured for PostgreSQL is already in use by another local process?** The environment MUST fail fast with a clear message, and the configuration MUST allow the port to be overridden without code changes (via environment variable / externalized config).
- **What happens when the source dataset file is missing, corrupt, or has an unexpected schema/version?** The loader MUST NOT silently load partial data; it MUST fail fast with a clear error and leave the database in a known (unloaded or rolled-back) state.
- **What happens when a table has duplicate primary keys or constraint violations in the source data?** The load MUST surface these as a clear validation error (and, if feasible, report the offending rows) rather than silently dropping or duplicating data.
- **What happens when the teardown command is run while a user has an active SQL session open?** The system MUST handle this gracefully (warning or forced disconnect with a clear message) and complete teardown without leaving orphaned resources.
- **What happens when the source data types cannot be unambiguously inferred (e.g., a code column that looks numeric but is categorical)?** The data dictionary MUST document the chosen interpretation and the load MUST apply it consistently; ambiguous cases are noted in the dictionary rather than silently guessed.

## Requirements *(mandatory)*

### Functional Requirements

#### Local Data Warehouse (PostgreSQL + Docker)

- **FR-001**: The system MUST provide a Docker-based local environment that runs PostgreSQL as a container, with deterministic bring-up configurable via a single bootstrap command.
- **FR-002**: The PostgreSQL container MUST create a dedicated database/schema distinct from the default `postgres` system database, dedicated to the Global Superstore data.
- **FR-003**: The system MUST load the Global Superstore Dataset into exactly three relational tables named `Orders`, `Returns`, and `People`, each populated with non-zero row counts from the provided source.
- **FR-004**: Each table MUST define appropriate primary keys and document inter-table relationships (e.g., `Order ID` linking `Orders` and `Returns`; `Region` linking `Orders` and `People`) so future joins and governance logic are supported.
- **FR-005**: The system MUST enforce a deterministic, reproducible load: re-running the bootstrap from a clean state MUST produce the same schema and row counts every time, with no manual fix-up.
- **FR-006**: The system MUST expose all connection parameters (host, port, database name, credentials) as externalized configuration via environment variables so the port and credentials can be overridden without code changes.
- **FR-007**: The system MUST provide a teardown command that stops and removes the container and (optionally, configurable) persisted data cleanly, leaving no orphaned resources.

#### Data Dictionary

- **FR-008**: The system MUST produce a comprehensive data dictionary document covering all three tables (`Orders`, `Returns`, `People`) and every column within each.
- **FR-009**: For every column, the dictionary MUST record: column name, business description, data type, nullability, and whether it is a key/identifier (primary or foreign-like).
- **FR-010**: For every table, the dictionary MUST record: table name, table purpose/business description, primary key, and its relationships to other tables.
- **FR-011**: The dictionary MUST document cross-table relationships explicitly, so a reader understands how the three tables join (e.g., `Order ID` in `Orders` ↔ `Returns`; `Region` in `Orders` ↔ `People`).
- **FR-012**: The dictionary MUST be regeneratable from the loaded schema, so that it stays in sync with the warehouse and can be reproduced on a fresh machine.

#### Validation & Operability

- **FR-013**: The bootstrap MUST fail fast with a clear, actionable error when Docker is unavailable, the configured port is taken, or the source dataset is missing/corrupt — never silently producing a partial environment.
- **FR-014**: The system MUST provide a single validation command that confirms: container up, database reachable, three tables present, all three non-empty, and the data dictionary file present.
- **FR-015**: The loader MUST detect and clearly report primary-key/constraint violations or schema mismatches in the source data rather than silently dropping or duplicating rows.
- **FR-016**: All baseline tooling scripts (bootstrap, teardown, validation, dictionary generation) MUST be documented in the repository with their expected inputs and outputs.

#### Architectural alignment (per project constitution)

- **FR-017**: The local environment MUST run entirely inside Docker with PostgreSQL as the only local data store; no host-installed database is required or permitted for the baseline.
- **FR-018**: All data access MUST flow through an abstracted, typed data-access layer so that, even at this baseline stage, business logic and (future) AI/Text-to-SQL components hold no engine-specific dependencies. This ensures the planned future migration to Google BigQuery requires no change to upstream components.
- **FR-019**: The data-access layer MUST expose typed contracts (typed models) at its boundaries; raw untyped payloads MUST NOT cross module boundaries.
- **FR-020**: The data model MUST be designed to support the future v1.0/1.1 Text-to-SQL scope on `Orders` and the v2.0 Semantic Layer & Row-Level Security scope on `Returns`/`People`, even though those capabilities are out of scope for this baseline (see Assumptions). This means the loaded schema must preserve the columns needed for net-vs-gross business logic and for region-based row scoping.

> **Note on governance**: Per the constitution, Data Governance (RBAC/RLS at the Semantic Layer) is a non-negotiable property of the platform. However, RLS enforcement on `People`/`Returns` is explicitly scoped to v2.0 in this roadmap. This baseline MUST NOT introduce governance logic prematurely, but the data model must trivially admit it later (FR-020). No feature without a functioning Semantic Layer may claim to provide governance.

### Key Entities *(include if feature involves data)*

- **Orders**: The transactional fact table of the Global Superstore Dataset — one row per order line, including sales, discount, profit, customer, product, and regional attributes. Central to the future v1.0/1.1 Text-to-SQL scope (primary query surface) and to v2.0 business logic (gross/net sales). Primary key: a composite of order-line identifiers (to be confirmed against the source schema at load time, per FR-004/FR-015).
- **Returns**: A table recording which orders were returned — linked to `Orders` via `Order ID`. Used in v2.0 to model business logic distinguishing net vs gross sales. Primary key: `Order ID` (or a composite including it); functions as a foreign-like link into `Orders`.
- **People**: A mapping of sales people (or regional managers) to the regions they are responsible for — effectively the region-to-person mapping table. Used in v2.0 as the basis for Row-Level Security, ensuring a user can only query data for regions assigned to them. Primary key: `Person` (or region identifier, to be confirmed against source).
- **Relationships**: `Returns.Order ID` → `Orders.Order ID` (return refers to an order); `People.Region` → `Orders.Region` (regional responsibility scopes rows in `Orders`). These relationships are documented in the data dictionary and supported by the schema, even though enforcement (RLS) is deferred to v2.0.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A contributor can bring up the local PostgreSQL data warehouse from a clean clone with a single command, and the database is queryable within 5 minutes on a standard developer laptop.
- **SC-002**: After bootstrap, a single query (or validation command) confirms the presence of exactly three tables — `Orders`, `Returns`, `People` — each containing the expected, non-zero row counts from the Global Superstore Dataset.
- **SC-003**: Re-running the bootstrap from a clean state on an already-initialized machine produces identical schema and row counts across all three tables, deterministically (100% reproducibility across runs).
- **SC-004**: The data dictionary covers 100% of tables and 100% of columns in the warehouse, with every column having at least a name, description, data type, nullability, and key/identifier flag recorded.
- **SC-005**: A previously-onboarded stakeholder can locate the definition of any given column (e.g., `Discount`, `Profit`, `Region`) in the data dictionary within 30 seconds.
- **SC-006**: The teardown command stops and fully removes the container and persisted data (when configured) with no orphaned Docker resources remaining, verified by a post-teardown resource check.
- **SC-007**: The validation command returns a single pass/fail signal that correctly reflects container-up, database-reachable, three-tables-present, all-non-empty, and dictionary-file-present within 30 seconds.
- **SC-000**: A fresh contributor, given only the repository and the constitution, can reach a "queryable local warehouse + readable data dictionary" baseline end state with no external help and at most one documented bootstrap command — establishing the foundation for all v1.x and v2.0 work.

## Assumptions

- **Source dataset format**: The relational Global Superstore Dataset is available to the project as a single file (e.g., the `Global Superstore Data.xlsx` present in the repository). The baseline assumes this file is the authoritative source; if its internal structure differs across sheets/tables, the loader treats each of the three logical tables (`Orders`, `Returns`, `People`) as a distinct sheet/range. Exact per-column types will be inferred/declared at load time and reflected in the dictionary (FR-009 / FR-015).
- **Local environment**: Docker (with Docker Compose) is the only prerequisite on the contributor's machine; no host-installed PostgreSQL or other databases are assumed or permitted for the baseline.
- **Audience**: Primary users of this baseline are Data Engineers and AI Engineers on the platform team. The data dictionary is also readable by non-technical business stakeholders (no SQL knowledge required to consume it).
- **Scope of "data access layer" in baseline**: FR-018/FR-019 require an abstracted, typed data-access layer from day one. In this baseline, "data access layer" means the read paths used by the loader, dictionary generator, and validation scripts — not a full Semantic Layer. A full Semantic Layer with RBAC/RLS is explicitly v2.0 scope.
- **Credits / PII**: The Global Superstore Dataset is a synthetic/sample dataset; no real PII is assumed. Where the dataset contains person or customer identifiers, they are treated as non-sensitive synthetic data for this baseline. Data Governance (audit logging, lineage, RBAC/RLS) is deferred to v2.0 per the roadmap.
- **Deferred milestones (context only, not scope of this spec)**: Text-to-SQL on `Orders` (v1.0/1.1) and the Semantic Layer + business logic on `Returns` + RLS on `People` (v2.0) are captured here only to frame the data model the baseline must support. Detailed requirements for those milestones will live in their own feature specs.
- **Cloud warehouse**: Google BigQuery is the future target cloud warehouse. The baseline runs on local PostgreSQL only; the abstracted data-access layer is designed for a seamless future migration but no BigQuery work is in scope for this baseline.
- **Credentials**: Default local-dev credentials are acceptable for the baseline (not a production secret), but MUST be externalized via environment variables (FR-006) and MUST NOT be hardcoded; `.env` is gitignored per the constitution.
- **Determinism**: "Reproducible" means same schema + same row counts across runs on the same source data, not byte-identical physical storage layout.
- **Inferred types**: Where the source data type of a column is ambiguous (e.g., a code that looks numeric but is categorical), the implementation will pick one consistent interpretation and document it in the data dictionary rather than ask the user at load time — recorded as an assumption, not a clarification blocker.
- **Out of scope**: Dashboard UI, natural-language query UX, prompt orchestration, model training/fine-tuning, experiment tracking, and any deployed (non-local) infrastructure are all out of scope for this baseline.
