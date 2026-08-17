# Contract: Integration with Text-to-SQL Pipeline (RLS Enforcement)

**Feature**: 003-semantic-layer-v1
**Date**: 2026-08-17
**Related**: [research.md](../research.md) Part E · [semantic_layer.md](./semantic_layer.md) · [../002-text-to-sql-v1/contracts/text_to_sql.md](../../002-text-to-sql-v1/contracts/text_to_sql.md)

> Define cómo el Semantic Layer se integra con el pipeline de Text-to-SQL de la feature 002 para enforcear governance (RLS) en CADA call. El mecanismo es un Decorator (`GovernedQueryProvider`) que envuelve el `QueryProvider` existente y aplica el resolver antes de delegar al adapter. Garantiza constitucionalmente (Principle IV, NON-NEGOTIABLE) que ningún SQL bypassa el Semantic Layer.

## Integration Strategy: Decorator (GovernedQueryProvider)

### Composition root — `src/cli/main.py`

El CLI es el composition root: construye los componentes y los wired together. El `TextToSqlPipeline` (de 002) se construye con un `QueryProvider` que YA está envuelto en el `GovernedQueryProvider`:

```python
# cli/main.py (esqueleto — el pipeline y el LlmClient no cambian de 002)
def build_query_provider(
    viewer: SemanticViewer | None,
    table_def: TableDef,
) -> QueryProvider:
    pg_repo = PostgresRepository(config=PostgresConfig.from_env())
    if viewer is None:
        # NUNCA se ejecuta una query sin viewer.
        # El pipeline invoca .execute_readonly_query solo tras validar SQL.
        # Si viewer is None, el GovernedQueryProvider conduce a ValueError en
        # la primera call — fail-fast, no bypass silencioso.
        return _UngovernedFailFastProvider(reason="No --viewer provided (governance is non-negotiable).")
    resolver = SemanticQueryResolver()
    return GovernedQueryProvider(
        delegate=pg_repo,
        resolver=resolver,
        viewer=viewer,
        table_def=table_def,
    )
```

### `GovernedQueryProvider` (en `src/data_engineering/semantic_layer/governed_provider.py`)

Implementa el `QueryProvider` Protocol envolviendo otro `QueryProvider`. La responsabilidad de gobernanza live aquí, NO en el adapter (el adapter solo ejecuta SQL).

```python
class GovernedQueryProvider:
    """Decorator over QueryProvider that enforces Semantic Layer RLS.

    Implements the QueryProvider Protocol. Every call to
    `execute_readonly_query` is intercepted: the SQL is transformed by the
    `SemanticQueryResolver` (subquery wrapping with Region IN) before being
    delegated to the wrapped QueryProvider (e.g., PostgresRepository).
    """

    def __init__(
        self,
        delegate: QueryProvider,
        resolver: SemanticQueryResolverProtocol,
        viewer: SemanticViewer,
        table_def: TableDef,
    ) -> None:
        self._delegate = delegate
        self._resolver = resolver
        self._viewer = viewer
        self._table_def = table_def

    def execute_readonly_query(
        self, sql: str, table_def: TableDef
    ) -> list[QueryRow]:
        # 1. Apply RLS — the resolver is a pure function; returns governed SQL.
        governed_sql = self._resolver.apply_rls(sql, self._viewer, table_def)
        # 2. Delegate execution with the governed SQL.
        return self._delegate.execute_readonly_query(governed_sql, table_def)
```

### `_UngovernedFailFastProvider` (en `src/data_engineering/semantic_layer/governed_provider.py`)

Defensive — si por error se construye un pipeline sin viewer (ni `allows_full_access`), la primer `execute_readonly_query` lanza清晰的 `ValueError` en vez de ejecutar SQL sin gobernanza.

```python
class _UngovernedFailFastProvider:
    """Safety net: raises on any execute_readonly_query call.

    Returned by build_query_provider when viewer is None. Prevents any
    silent execution path without governance (constitution Principle IV).
    """

    def execute_readonly_query(
        self, sql: str, table_def: TableDef
    ) -> list[QueryRow]:
        raise ValueError(
            "Governance is non-negotiable (constitution Principle IV). "
            "Provide --viewer <id> or, in local/dev, --allow-full-access."
        )
```

## Pipeline integration

### `TextToSqlPipeline` modification (en `src/ai_engineering/pipeline.py`)

La signature del constructor se extiende para aceptar opcionalmente el `SemanticViewer` (que se usa SOLO para logging, no para RLS — la RLS la hace el `GovernedQueryProvider` delegado via Dependency Injection):

| Existing field (002) | v2.0 modification |
|---|---|
| `dictionary: DataDictionaryDocument` | Unchanged |
| `table_def: TableDef` | Unchanged |
| `llm_client: LlmClient` | Unchanged |
| `query_provider: QueryProvider` | Unchanged type, but the runtime instance puede ser un `GovernedQueryProvider` |
| `llm_config: LlmConfig` | Unchanged |
| **NEW**: `semantic_layer: SemanticLayerDocument \| None` | Opcional. Si se pasa, el `build_prompt` añade el bloque métricas/dimensiones (US3). Default `None` → comportamiento 002 (fallback). |
| **NEW**: `viewer: SemanticViewer \| None` | Opcional. Si se pasa, se loguea en cada call (`.artifacts/text_to_sql.log` extendido con `viewer_id`, `regions`, `gov_bypass`). Default `None`. |

El `run(question)` method:
1. Build prompt (con `semantic_layer` si está presente).
2. Call LLM.
3. Validate SQL con `SqlValidator` (sin cambio — sigue aceptando `Orders`-only + joins a `Returns` ahora que el LLM ve las métricas que lo necesitan; el `SqlValidator` debe ajustarse mínimamente para aceptar SQL con JOIN a `Returns`, ver abajo).
4. **NO aplicar RLS aquí** — eso ya lo hizo el `GovernedQueryProvider` en el delegate. Llama `query_provider.execute_readonly_query(sql, table_def)` y el wrapper aplica RLS automáticamente.
5. Logger extiende el output con `viewer_id`, `regions`, `gov_bypass` flag.
6. Return `TextToSqlResponse`.

### SqlValidator minimal adjustment (en `src/ai_engineering/sql_validator.py`)

El `SqlValidator` de 002 rechazaba cualquier SQL que refencie `Returns` o `People` (Orders-only). En v2.0, ALLOWAMOS `Returns` como JOIN válido (para métricas derivadas como `net_sales` — el supuesto del spec). `People` SIGUE siendo rechazado — no es superficie de consulta del LLM.

| Change | Before (002) | After (v2.0) |
|---|---|---|
| Table whitelist | `Orders` only | `Orders` + `Returns` (JOIN para métricas derivadas). `People` still rejected. |
| Column whitelist | Orders columns | Orders columns + Returns columns (when `Returns` referenced). |
| Comment | Unchanged | Unchanged |

Esto se documentará en `tasks.md` como una modificación mínima al `SqlValidator`.

## End-to-end flow (v2.0 with RLS)

```
NLQuestion (user input)
        │
        ▼
PromptBuilder.build_prompt(question, dictionary, table_def, semantic_layer)  ──► str
        │  (semantic_layer condensa métricas/dimensiones/joins; +~400 tokens)
        ▼
LlmClient.generate_sql(prompt)  ──► GeneratedSql
        │
        ▼
SqlValidator.validate(generated_sql, table_def)  ──► ValidationResult
        │  (now allows Orders + Returns JOIN; still rejects People)
        │
        ├─ [rejected] → TextToSqlResponse(validation=rejected, query_result=None)
        │
        ▼ [accepted]
GovernedQueryProvider.execute_readonly_query(sql, table_def)  ──► list[QueryRow]
        │
        │  1. SemanticQueryResolver.apply_rls(sql, viewer, table_def)
        │     → SELECT * FROM ({sql}) AS _gov WHERE "Region" IN ('R1','R2',...)
        │       (or WHERE FALSE if viewer.regions = []; or unchanged if
        │        viewer.allows_full_access AND viewer.is_local_dev, logged
        │        as gov.bypass)
        │
        ▼
PostgresRepository.execute_readonly_query(governed_sql, table_def)  ──► list[QueryRow]
        │  (existing PG adapter — executes the governed SQL as-is)
        ▼
QueryResult(sql_governed, rows, row_count, latency_ms, error)
        │
        ▼
TextToSqlResponse(question, generated_sql, validation, query_result, viewer_id, gov_bypass)
```

## Boundary enforcement map

| Principle | Enforced by |
|---|---|
| Principle IV (RLS non-negotiable) | (1) `GovernedQueryProvider` envuelve `QueryProvider` en composition root (CLI). (2) `_UngovernedFailFastProvider` raise si no hay viewer. (3) AST/grep boundary test (`test_boundaries.py`) check: ningún caller de `execute_readonly_query` en `src/ai_engineering/` invoca directo a `PostgresRepository` (siempre vía `QueryProvider` inyectado). (4) Integration test dos-viewers (`test_semantic_rls.py`) comprueba resultados distintos → cualquier bypass se traduce en mismos resultados → test falla. |
| Principle II (layered separation) | `ai_engineering` importa solo `src/contracts/semantic_layer.py` + `src/contracts/text_to_sql.py` + `src/data_access/interfaces.py` (Protocolos); NO importa `data_engineering.semantic_layer.*` directamente. La inyección del `GovernedQueryProvider` en el pipeline la hace `cli/main.py`. |
| Principle III (no engine leakage) | `SemanticQueryResolver` es pure string transform, no `psycopg`. `PostgresRepository` (adapter) no contiene lógica de governance. El `GovernedQueryProvider` es engine-neutral (envuelve `QueryProvider`, no `PostgresRepository`-concrete). El futuro `BigQueryRepository` no requiere cambio al resolver. |

## Logging extension (`.artifacts/text_to_sql.log`)

Cada `pipeline.run` loguea (extiende el formato de 002 FR-014):

```text
[2026-08-17T12:34:56Z] question="total sales" sql='SELECT SUM("Sales") FROM Orders'
    accepted=True reason='' rows=1 error='' latency_ms=210
    viewer_id=alice regions=['Caribbean','Central America'] gov_bypass=False
```

- `viewer_id`: el ID del viewer activo, o `None` (que entonces falla antes). En el happy path siempre non-None.
- `regions`: lista serializada de las regiones del viewer.
- `gov_bypass`: True solo si `viewer.allows_full_access=True` AND `viewer.is_local_dev=True` (logged como evento de auditoría puntuales).

## Out of Scope for This Integration Contract

- **RBAC column-level** — declarado pero no enforceado.
- **Audit logging persistente** — el `.artifacts/text_to_sql.log` es log de dev; no es un sistema audit-grade.
- **SQL multi-table sobre `People`** — `People` stays out of LLM surface; only the registry consumes `People` (via `viewers.yaml` static config) for the viewer→regions mapping.
- **Multi-turn conversation context** — fuori scope (igual que en 002).
