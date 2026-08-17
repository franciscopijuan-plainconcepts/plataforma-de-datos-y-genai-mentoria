# Contract: Semantic Layer Document, Viewer & Resolver

**Feature**: 003-semantic-layer-v1
**Date**: 2026-08-17
**Related**: [research.md](../research.md) · [data-model.md](../data-model.md) · [integration.md](./integration.md)

> Define los contract models y la interfaz del Semantic Layer: el artifact `SemanticLayerDocument` (con métricas, dimensiones, relaciones), el `SemanticViewer` (contexto de gobernanza), y la `SemanticQueryResolverProtocol` (pure function que aplica RLS a un SQL). Los viven en `src/contracts/semantic_layer.py`. Ver constitution Principles I, II, IV.

## Componentes del contract

### `SemanticLayerDocument` — artifact top-level (en `src/contracts/semantic_layer.py`)

El documento que captura la capa semántica del negocio: métricas, dimensiones, relaciones. Es el output del builder y la entrada del `PromptBuilder` (para enriquecer el prompt del LLM).

| Field | Type | Semantics |
|---|---|---|
| `version` | `str` | Semver del artifact (`1.0.0` initially). Cambia cuando `metrics.py` cambia (no en cada regeneración). |
| `tables` | `list[TableSemanticClassification]` | Las 3 tablas con su clasificación (fact/dimension/governance_mapping). |
| `metrics` | `list[Metric]` | Métricas definidas (FR-003: al menos 8). |
| `dimensions` | `list[Dimension]` | Dimensiones (FR-004: al menos las 11 mínimas). |
| `relationships` | `list[SemanticRelationship]` | Las 2 relaciones (FR-005). |
| `source_sha256` | `str` | Hash del `load_manifest.json` (provenance). |
| `semantic_source_sha256` | `str` | Hash del `semantic_source.py` (provenance). |
| `generated_at` | `datetime` | Timestamp UTC — **solo va al `.md`**, NO al `.json` canonical. |
| `assumptions` | `list[str]` | Asunciones documentadas (e.g., proporcionalidad de `net_profit`). |

`viewers` NO es parte del `SemanticLayerDocument` — los viewers son runtime config (se cargan aparte en el registry).

Ver [data-model.md](../data-model.md) § 5 para los field-level details.

### `SemanticViewer` — contexto de gobernanza (en `src/contracts/semantic_layer.py`)

Define el contexto de gobernanza de un usuario activo. Se construye en runtime (per-CLI invocation) y se pasa al resolver. NO se persiste en el artifact JSON.

| Field | Type | Semantics |
|---|---|---|
| `viewer_id` | `str` | Identificador. Login-as-person: snake_case del nombre (`marilene_rousseau`); YAML fallback: ID del entry (`admin_dev`). |
| `regions` | `list[str]` | Regiones con acceso. Vacío + `allows_full_access=False` → `WHERE FALSE` (no ve nada). |
| `allows_full_access` | `bool` | Si `True`, el resolver NO filtra (solo efectivo cuando `is_local_dev=True`). |
| `is_local_dev` | `bool` | Computado: `ENV in {local, dev, test}`. Si `False`, `allows_full_access` se fuerza a `False`. |

**Resolution model (v2.0 final)**: el CLI `ask --viewer <value>` resuelve el `SemanticViewer` con la siguiente prioridad:

1. **`PeopleViewerResolver`** (default) — consulta la tabla `People` y construye el
   viewer con `viewer_id` snake_case derivado del nombre real de la persona, y
   `regions = [People.Region]`. Acepta 3 formas de lookup: `marilene_rousseau`
   (snake), `Marilène Rousseau` (con acento), `Marilene Rousseau` (sin acento).
2. **`ViewerRegistry`** (fallback `viewers.yaml`) — para escape hatches como
   `admin_dev`, roles `sales_eu`, o cuentas CI que no corresponden a una
   persona real en People.
3. **Fail-fast** — si no matchea ninguno, error claro listando las personas
   disponibles en People.

Ver [data-model.md](../data-model.md) § 4 para los field-level details + validation rules,
y [research.md](../research.md) Part C para el rationale del modelo de login-as-person.

### `SemanticQueryResolverProtocol` — interfaz del resolver (en `src/contracts/semantic_layer.py`)

La interfaz typed que el `GovernedQueryProvider` (en `src/data_engineering/semantic_layer/`) consume. El resolver es una pure function — no DB, no LLM, no side effects aside from governance-bypass logging.

```python
@runtime_checkable
class SemanticQueryResolverProtocol(Protocol):
    """Applies Row-Level Security to a validated SELECT SQL.

    Pure function: no DB calls, no LLM, no state. The caller MUST have
    already validated the SQL via `SqlValidator` before calling this method.
    """

    def apply_rls(
        self,
        sql: str,
        viewer: SemanticViewer,
        table_def: TableDef,
    ) -> str:
        """Inject the `Region IN (viewer.regions)` predicate into the validated SQL.

        Implementation (final v2.0 — see research.md Part A): INJECTS the
        `WHERE "Region" IN (...)` predicate directly into the LLM SQL —
        ANDing any existing WHERE, or introducing a new WHERE before
        GROUP BY / ORDER BY / LIMIT. This is robust for aggregation
        queries like `SELECT SUM("Sales") FROM Orders` (whose outer
        projection doesn't expose Region, breaking the initial subquery-
        wrapping approach).
        If the viewer has no regions and `allows_full_access=False`,
        returns SQL that produces 0 rows (the path uses subquery wrapping
        with `WHERE FALSE` because there's no projection dependency).
        If `allows_full_access=True` and `is_local_dev=True`, logs a
        `gov.bypass` event and returns the SQL unfiltered.
        """
        ...
```

**Boundary rule**: el resolver NO importa `psycopg`, `openai`, ni adapter internals. Es puro. Unit-testeable sin DB ni LLM. El boundary test lo enforce.

### `Metric`, `Dimension`, `SemanticRelationship`, `TableSemanticClassification`

Ver [data-model.md](../data-model.md) § 1–3, § 6 para los field-level details. Todos son Pydantic v2 frozen, con validation rules documentadas (column existence en `DataDictionaryDocument`, metric reference closure, etc.).

## Builder — `SemanticLayerBuilder` (en `src/data_engineering/semantic_layer/builder.py`)

Implementa la construcción del `SemanticLayerDocument` desde fuentes existentes. NO importa adapters ni `psycopg` ni `openai`.

| Method | Input | Output | Semantics |
|---|---|---|---|
| `build(dictionary: DataDictionaryDocument, semantic_source_sha256: str, source_sha256: str) -> SemanticLayerDocument` | `DataDictionaryDocument` + hashes de provenance | `SemanticLayerDocument` | Construye el artifact desde las fuentes existentes. Las métricas y relaciones se definen en `metrics.py` (hard-coded). El builder valida que cada `formula_sql`/`from_column`/`to_column` référencia columnas que existen en `dictionary` (FR-006: fail fast). |

**Validation rules** (FR-006 — build-time):
- Cada `Metric.formula_sql` se parsed regex para extraer identifiers quoted con double-quotes (`"..."`); cada uno MUST existir en `dictionary` (Orders columns, or `Returns`/`People` columns si son cross-table).
- Cada `SemanticRelationship.from_column` y `.to_column` MUST existir en `dictionary`.
- Cada `Metric.derives_from[*]` MUST existir en el list de métricas del document que se está construyendo.
- No duplicados (por `name` / por tuple `(from_table, from_column, to_table, to_column)`).

**Boundary rule**: el builder es puro (no LLM, no DB). Accepta un `DataDictionaryDocument` y dos hashes; devuelve un `SemanticLayerDocument`. Lanza `ValueError` si alguna validation falla. El builder importa solo de `src/contracts/` (no `psycopg`, no `openai`).

## Renderer — `SemanticLayerRenderer` (en `src/data_engineering/semantic_layer/render.py`)

Serializa el `SemanticLayerDocument` a los dos artefactos. Pure function pair.

| Method | Input | Output | Semantics |
|---|---|---|---|
| `render_json(document) -> str` | `SemanticLayerDocument` | `str` | Canonical JSON. `model_dump(exclude={"generated_at", "viewers"}, exclude_none=True)` + `json.dumps(..., sort_keys=True, indent=2, ensure_ascii=False)`. Determinista. |
| `render_markdown(document) -> str` | `SemanticLayerDocument` | `str` | Human-readable Markdown con `generated_at`, hashes, métricas con fórmulas completas, relaciones, asunciones. No determinista en timestamp pero sí en contenido semántico. |

**Boundary rule**: pure functions. No DB, no LLM, no file I/O (los callers escriben el output a disco).

## Registry — `ViewerRegistry` (en `src/data_engineering/semantic_layer/registry.py`)

Loads viewers from `viewers.yaml` (path override via `SEMANTIC_VIEWERS_FILE`).
**Nota (v2.0 final)**: este es el fallback cuando `PeopleViewerResolver` no
matchea una persona. El flujo de resolución completo del CLI es:

1. `PeopleViewerResolver.resolve(viewer_value)` — busca en People (default).
2. `ViewerRegistry.get_viewer(viewer_id)` — busca en YAML (fallback).
3. Fail-fast si ninguno matchea.

See [research.md](../research.md) Part C for the login-as-person model details.

| Method | Input | Output | Semantics |
|---|---|---|---|
| `load_viewers(path)` | `Path \| None` (default: `viewers.yaml` o `SEMANTIC_VIEWERS_FILE`) | `list[SemanticViewer]` | Parsea el YAML, construye `SemanticViewer` models. Para cada viewer, computa `is_local_dev = ENV in {local, dev, test}` y si `allows_full_access=True` pero `is_local_dev=False`, lo flipa a `False`. |
| `get_viewer(viewer_id, path)` | `viewer_id` + path | `SemanticViewer` | Devuelve el viewer matching `viewer_id`. Raise `ValueError` si no existe, listando los IDs disponibles. |

**Boundary rule**: importa `pyyaml`. No DB, no LLM. Pure (file I/O es unavoidable para load config, pero no muta state).

## Resolver Implementation — `SemanticQueryResolver` (en `src/data_engineering/semantic_layer/resolver.py`)

Implementa `SemanticQueryResolverProtocol` via predicate injection (research.md Part A, versión final tras corrección del subquery wrapping original).

| Method | Input | Output | Semantics |
|---|---|---|---|
| `apply_rls(sql, viewer, table_def) -> str` | `str` SQL validado, `SemanticViewer`, `TableDef` | `str` SQL gobernado | Si `viewer.allows_full_access` → loguea `gov.bypass` y devuelve SQL original. Si `viewer.regions = []` → `SELECT * FROM ({sql}) AS _gov WHERE FALSE` (subquery wrap aquí sí es OK porque no hay proyección externa). Else → **inyecta** `AND "Region" IN ('R1', 'R2', ...)` en el WHERE existente, o `WHERE "Region" IN (...)` antes de GROUP BY/ORDER BY/LIMIT si no hay WHERE. |

**Boundary rule**: pure function. No DB, no LLM, no file I/O. El log de government-bypass va a un logger injected (no abre files directo). El `table_def` se usa para sanity-check (la tabla principal es `Orders`, que tiene `Region`).

## Column / identifier conventions

- **Double-quote identifiers** en todas las fórmulas SQL y relaciones (e.g., `"Region"`, `"Order ID"`, `"Sales"`). PostgreSQL es case-sensitive con quoting; el dataset usa title-case. Consistente con el prompt del `SqlValidator` de 002.
- **`Region`** es la columna anchor de RLS, existe en `Orders`, `Returns`, y `People`. El resolver SIEMPRE usa `"Region"` quoted.
- **`_gov`** es el alias externo stable reservado por el resolver — el `SqlValidator` existente (002) no lo bloquea porque nunca aparece en SQL del LLM, pero se mantiene defensive.

## Out of Scope for This Contract

- **RBAC column-level enforcement** — declarado como field en `TableSemanticClassification` pero no enforceado. v3.0+.
- **Audit logging completo y lineage indexable** — logging básico de `gov.bypass` sí, sistema completo no.
- **Multi-tenant / namespaces** — un solo tenant; el `viewer_id` es string local.
- **Sistema de autenticación real (OIDC/JWT)** — el viewer se construye desde config local YAML.

Ver [integration.md](./integration.md) para cómo el resolver se compone con el `QueryProvider` existente (Decorator pattern) y cómo el `GovernedQueryProvider` intercepta el SQL en el pipeline.
