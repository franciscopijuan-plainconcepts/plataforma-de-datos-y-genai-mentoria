# Contract: Pipeline Integration (on_query_complete Callback)

**Feature**: 004-metabase-integration
**Date**: 2026-08-17
**Related**: [research.md](../research.md) Part D · [metabase_client.md](./metabase_client.md) · [../002-text-to-sql-v1/contracts/text_to_sql.md](../../002-text-to-sql-v1/contracts/text_to_sql.md)

> Define cómo el `TextToSqlPipeline` (feature 002) integra con Metabase SIN acoplamiento directo. El approach es un **callbackoplado `on_query_complete` inyectado opcionalmente via constructor**. El pipeline no conoce Metabase — solo invoca el callback si está presente. El CLI actúa como composition root para wire el `MetabaseClient` en el callback.

## Integration strategy: Callback injection

### `TextToSqlPipeline` modification (minimal, additive)

| Existing field (002/003) | v2.1 modification |
|---|---|
| `dictionary: DataDictionaryDocument` | Unchanged |
| `table_def: TableDef` | Unchanged |
| `llm_client: LlmClient` | Unchanged |
| `query_provider: QueryProvider` | Unchanged type, but the runtime instance puede ser `GovernedQueryProvider` (003) |
| `llm_config: LlmConfig` | Unchanged |
| `semantic_layer: SemanticLayerDocument \| None` | Unchanged (003) |
| `viewer: SemanticViewer \| None` | Unchanged (003) |
| **NEW**: `on_query_complete: OnQueryComplete \| None` | Callback opcional. Default `None` — comportamiento actual. Se invoca al final de un `ask` exitoso. |

### `OnQueryComplete` signature

```python
from typing import Callable
from src.contracts.text_to_sql import TextToSqlResponse
from src.contracts.semantic_layer import SemanticViewer

# The callback receives the full response (including the GOVERNED SQL in
# query_result.sql) and the viewer that was active for governance context.
# It MAY be None (no viewer was set, e.g., --allow-full-access path).
OnQueryComplete = Callable[
    [TextToSqlResponse, "SemanticViewer | None"],
    None,
]
```

**Why return `None`?** El callback es fire-and-forget — el pipeline no espera ni necesita respuesta del sink. Si el sink falla, el callback loguea por si mismo; el pipeline ya devolvió el response.

### Pipeline `run()` flow (with callback)

```python
def run(self, question: NLQuestion) -> TextToSqlResponse:
    response = ...  # existing flow: prompt → LLM → validate → execute → log

    # NEW v2.1: invoke the optional callback at the end of a successful run
    if self._on_query_complete is not None:
        try:
            self._on_query_complete(response, self._viewer)
        except Exception as exc:
            # Best-effort — never let a sink break the pipeline
            _logger.warning(
                "on_query_complete callback failed (best-effort, swallowed): %s",
                exc,
            )
    return response
```

**Boundary rule**: el pipeline NO importa `metabase_client.py`, `httpx`, o cualquier modulo de Metabase. Solo conoce el contract `Callable`. Boundary test confirms.

## Composition root — `src/cli/main.py`

El CLI construye el callback que enhalfa el `MetabaseClient`:

```python
def cmd_ask(question, viewer_id, ...):
    metabase_client = _build_metabase_client_if_enabled(...)  # returns None if Metabase down or disabled

    def on_query_complete(response: TextToSqlResponse, viewer: SemanticViewer | None) -> None:
        if metabase_client is None:
            return
        # The callback uses response.query_result.sql which is the GOVERNED SQL
        # (post-GovernedQueryProvider, with WHERE "Region" IN injected by the resolver).
        card = metabase_client.send_governed_query(
            response=response,
            viewer=viewer,
            session_id=session_id,  # from --session flag, may be None
        )
        if card is not None:
            _info(f"Metabase card created: id={card.id} name={card.name!r}")
        # else: send_governed_query logged a warning internally; nothing more to do.

    pipeline = TextToSqlPipeline(
        ...,
        on_query_complete=on_query_complete if not args.no_metabase else None,
    )
```

### The关键的 governance invariant

El callback recibe `response.query_result.sql` que es **el SQL gobernado** — es decir, el SQL que el `GovernedQueryProvider` le dio al `PostgresRepository` para ejecutar. Ese SQL ya pasó por el `SemanticQueryResolver.apply_rls()` y ya tiene `WHERE "Region" IN (viewer.regions)` inyectado. Por lo tanto, cuando Metabase re-ejecuta la card, ejecuta el SQL gobernado y devuelve **solo** las filas dentro del scope del viewer original (constitution Principle IV preserved).

**No path bypass**: aunque el callback reciba el SQL crudo del LLM, el contrato de la feature 003 es que `response.query_result.sql` siempre es el gobernado (lo setea el `GovernedQueryProvider` cuando delega al adapter). El callback no puede acceder al SQL pre-gobernado en runtime.

## Retry / idempotency behavior

- El callback es best-effort: si Metabase no está disponible, el pipeline NO reintenta — solo loguea y el `ask` ya devolvió resultados al usuario.
- Si el usuario corre `ask` nuevamente (even con el mismo question), se crea una NUEVA card — Metabase cards no se dedup por SQL. La card nueva se agregará al final de la colección. El usuario puede usar `metabase reset-cards` para limpiar de testing.
- Si el callback recibe un `--session <id>`, busca o crea el dashboard asociado y agrega la card; si el dashboard ya tenía la card (idempotency财政收入 más fuerte), Metabase igual permite duplicados — cosa del usuario.

## Logging extension

`.artifacts/text_to_sql.log` (extiende de 003):

```text
[2026-08-17T14:00:00Z] question="total sales" sql='SELECT SUM("Sales") FROM Orders'
    accepted=True reason='' rows=1 error='' latency_ms=210
    viewer_id=alice regions=['Caribbean','Central America'] gov_bypass=False
    metabase_card_id=42 metabase_status=created
```

- `metabase_card_id`: ID de la card creada, o `None` si Metabase no estaba disponible.
- `metabase_status`: `created` | `skipped` (Metabase down or `--no-metabase`) | `failed` (HTTP error).
- Si `gov_bypass=True` (viewer con `allows_full_access`), la card sigue siendo creada — su `description` reflecta `gov_bypass=True` para auditoría (per spec edge case "ask con --allow-full-access").

## Out of scope for this contract

- **Sync vs async integration**: el callback es sync (no threading). El `ask` blocks orando Metabase reacciona, pero con timeouts agresivos (30s HTTP timeout en `MetabaseClient`). Si necesitas concurrencia real (múltiples asks en paralelo), deferred a v3.0+.
- **Bulk batch insertion**: una `ask` → una card. Si querés "batch recover" (todas las asks pendientes de una sesión histórica), deferred a v3.0+.
- **Cards en otro store** (no Metabase): el callback es genérico (`OnQueryComplete`), podrías escribir otro sink (e.g., a Kafka, a una log file, a un webhook). v2.1 solo tiene el sink Metabase.
