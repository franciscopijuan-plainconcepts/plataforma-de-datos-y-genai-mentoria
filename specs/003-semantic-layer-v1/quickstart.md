# Quickstart: Semantic Layer v1 (Governed Metrics, Dimensions & RLS)

**Feature**: 003-semantic-layer-v1
**Date**: 2026-08-17
**Related**: [spec.md](./spec.md) · [plan.md](./plan.md) · [research.md](./research.md) · [data-model.md](./data-model.md) · [contracts/](./contracts/)

> Runnable validation guide de la Semantic Layer v2.0. Cubre: generación del artifact, RLS con dos viewers distintos, y Text-to-SQL con governance enforced. Es una guía de validación — los detalles de implementación viven en `tasks.md`.

## Prerequisites

- **Baseline (v0) corriendo**: el warehouse Postgres en Docker con `Orders`/`Returns`/`People` cargados.
  ```bash
  uv run python -m src.cli.main validate
  ```
  Debe imprimir `VALIDATION PASSED`. Si falla, correr `bootstrap` primero (ver [quickstart de 001](../001-data-genai-platform-baseline/quickstart.md)).

- **Text-to-SQL v1.x funcionando**: el comando `ask` de la feature 002 debe estar disponible. Verificar:
  ```bash
  uv run python -m src.cli.main ask "What is the total sales amount?" 2>&1 | head -20
  ```
  Debe generar SQL y devolver filas (requiere `FORGE_API_KEY` seteada en `.env`).

- **`FORGE_API_KEY` in `.env`**: igual que en 002. La feature 003 no añade vars obligatorias nuevas para LLM.

- **Dependencia nueva `pyyaml`**: se agrega a `pyproject.toml`. Tras actualizar:
  ```bash
  uv sync
  ```

## Setup (one-time)

### 1. Instalar la nueva dependencia

```bash
uv sync
```

**Expected**: `pyyaml` está en el lockfile; `uv sync` termina sin errores.

### 2. Crear el archivo de viewers (local, gitignored)

```bash
cp viewers.example.yaml viewers.yaml
# Editar viewers.yaml con tus viewers locales. Ejemplo:
```

```yaml
# viewers.yaml — contenido de ejemplo
viewers:
  - id: alice
    regions:
      - Caribbean
      - Central America
    allows_full_access: false
  - id: bob
    regions:
      - Central US
    allows_full_access: false
  - id: dev
    regions: []
    allows_full_access: true  # Solo efectivo si ENV in {local, dev, test}
```

**Expected**: el archivo `viewers.yaml` existe en el root del proyecto.

### 3. Generar el Semantic Layer artifact

```bash
uv run python -m src.cli.main generate-semantic-layer
```

**Expected outcome** (spec FR-018 / SC-001):
- `.artifacts/semantic_layer.md` y `.artifacts/semantic_layer.json` se escriben.
- Se imprime un resumen con:
  - Tablas: `Orders` (fact), `Returns` (fact), `People` (governance_mapping).
  - Métricas: 8 (`gross_sales`, `net_sales`, `returned_amount`, `return_rate`, `total_profit`, `net_profit`, `avg_order_value`, `order_count`).
  - Dimensiones: 11+ (`region`, `country`, `market`, `segment`, `category`, `sub_category`, `ship_mode`, `order_priority`, `order_date`, `customer`, `product`).
  - Relaciones: 2 (Orders↔Returns por `Order ID`; Orders↔People por `Region`).

### 4. Verificar determinismo del artifact

```bash
# Primera generación
uv run python -m src.cli.main generate-semantic-layer
sha256sum .artifacts/semantic_layer.json > /tmp/sl_hash_1

# Segunda generación (sin cambios en código)
uv run python -m src.cli.main generate-semantic-layer
sha256sum .artifacts/semantic_layer.json > /tmp/sl_hash_2

# Comparar
diff /tmp/sl_hash_1 /tmp/sl_hash_2
```

**Expected** (spec FR-007 / SC-005): los hashes son idénticos — el JSON es determinista (no timestamps).

## Validation — A. RLS enforced (gov by design)

### A1. `ask` sin viewer falla rápido

```bash
uv run python -m src.cli.main ask "What is the total sales amount?"
```

**Expected** (spec FR-020 / SC-002): el comando falla con un error claro:

```
Error: Governance is non-negotiable (constitution Principle IV).
Provide --viewer <id> or, in local/dev, --allow-full-access.
```

NO se ejecuta ninguna query. No hay bypass silencioso.

### A2. `ask` con viewer scopea por región

```bash
uv run python -m src.cli.main ask --viewer alice "What is the total sales amount?"
```

**Expected** (spec FR-019 / SC-002):
- El SQL ejecutado incluye `WHERE "Region" IN ('Caribbean', 'Central America')` (en el wrapper externo).
- El resultado refleja la suma de `Sales` solo para esas regiones.
- Verificar manualmente contra PG:
  ```sql
  SELECT SUM("Sales") FROM Orders WHERE "Region" IN ('Caribbean', 'Central America');
  ```

### A3. Dos viewers devuelven resultados distintos

```bash
# Alice — regions [Caribbean, Central America]
uv run python -m src.cli.main ask --viewer alice "What is the total sales amount?" 2>&1 | tail -5

# Bob — regions [Central US]
uv run python -m src.cli.main ask --viewer bob "What is the total sales amount?" 2>&1 | tail -5
```

**Expected** (spec SC-002 / SC-003): los totales son distintos y coinciden con `SELECT SUM(Sales) WHERE Region IN (...)` directo. Si fueran iguales → hay un bypass y falla la garantía constitucional.

### A4. Viewer con `regions: []` devuelve 0 filas

```yaml
# En viewers.yaml:
  - id: nobody
    regions: []
    allows_full_access: false
```

```bash
uv run python -m src.cli.main ask --viewer nobody "What is the total sales amount?"
```

**Expected** (spec FR-014 / SC-003): el SQL ejecutado tiene `WHERE FALSE` y devuelve 0 filas. El viewer `nobody` ve cero filas, sin excepciones.

## Validation — B. Semantic Layer metricas

### B1. Text-to-SQL distingue net vs gross

```bash
uv run python -m src.cli.main ask --viewer alice "Show me net sales by region"
```

**Expected** (spec FR-015 / SC-004):
- El SQL generado hace JOIN con `Returns` (o usa `EXISTS`) para calcular net sales — NO devuelve el mismo número que "gross sales by region".
- Comparar:
  ```bash
  uv run python -m src.cli.main ask --viewer alice "Show me gross sales by region"
  ```
  Los resultados deben diferir (en regiones con devoluciones).

### B2. SQL con JOIN a Returns es aceptado; JOIN a People rechazado

```bash
# Este SQL debería ser rechazado (People no es superficie de consulta)
# Validar via un SQL de test directo contra el SqlValidator (test en tests/unit/):
uv run pytest tests/unit/test_sql_validator.py -v
```

**Expected**: el test suite valida que `Returns` join es aceptado, `People` reference es rechazado con error claro.

## Validation — C. Boundary tests

### C1. Contract tests pasan

```bash
uv run pytest tests/contract/ -v
```

**Expected**: todos pasan, incluyendo:
- `test_boundaries.py` (extendido): asserts `openai`/`httpx` confined; `psycopg` confined; `pyyaml` only in `data_engineering/semantic_layer/registry.py`; AST/grep check que ningún caller de `execute_readonly_query` en `src/ai_engineering/` invoca directo al adapter (siempre via `QueryProvider`).
- `test_semantic_layer.py` (NUEVO): Pydantic v2 conformance para todos los models; builder validation invariants (column existence, metric closure).
- `test_text_to_sql.py` (sin cambios): pipeline sigue satisfaciendo su contrato.

### C2. Unit tests del resolver

```bash
uv run pytest tests/unit/test_semantic_resolver.py -v
```

**Expected**: todos los casos de la tabla de Part A (research.md) cubiertos y pasando: viewer con una región, varias regiones, vacío, `allows_full_access`, SQL con WHERE, sin WHERE, con GROUP BY, con JOIN a Returns.

### C3. Integration tests de RLS

```bash
# Requiere Docker PG corriendo + FORGE_API_KEY
uv run pytest tests/integration/test_semantic_rls.py -v
```

**Expected** (spec SC-002 / SC-008): el test crea viewers con regiones reales del dataset (e.g., `Caribbean` vs `Central US`), corre `ask` con cada uno, y verifica que:
1. Los resultados de cada viewer coinciden con `SELECT SUM(Sales) WHERE Region IN (...)` directo sobre PG.
2. Los resultados de viewers distintos son distintos (no hay bypass).

## Validation — D. Sin firmware requirements fuera del Semantic Layer

### D1. Type-checking estricto

```bash
uv run mypy --strict src/ tests/
```

**Expected** (spec SC-007): cero errores. Todos los nuevos contract models son Pydantic v2 con tipos explícitos. Sin `Any` nuevo (salvo lo justificado heredado de 002).

### D2. San check invocación completa

```bash
# 1. Warehouse up
uv run python -m src.cli.main validate

# 2. Generar semantic layer
uv run python -m src.cli.main generate-semantic-layer

# 3. ask con viewer
uv run python -m src.cli.main ask --viewer alice "Show me net sales by region"
```

**Expected** (spec SC-006): desde clean clone (tras `bootstrap`), los tres comandos corren end-to-end exitosamente. La CLI es reproducible y resta una guía single-command.

## Validation — E. Logging de gobernanza

### E1. Log contiene viewer + gov_bypass

```bash
uv run python -m src.cli.main ask --viewer alice "What is the total sales amount?"
tail -1 .artifacts/text_to_sql.log
```

**Expected** (spec FR-021): la línea del log incluye:
- `viewer_id=alice`
- `regions=['Caribbean', 'Central America']`
- `gov_bypass=False`

### E2. Log de `--allow-full-access`

```bash
ENV=local uv run python -m src.cli.main ask --viewer dev --allow-full-access "What is the total sales amount?"
tail -1 .artifacts/text_to_sql.log
```

**Expected** (spec FR-013):
- El SQL ejecutado NO tiene filtro (no wrapping).
- El log tiene `gov_bypass=True` (logged como evento de auditoría).

## Rollback / cleanup

```bash
# Limpiar artefactos generados por esta feature
rm -f .artifacts/semantic_layer.json .artifacts/semantic_layer.md
rm -f viewers.yaml  # local config only

# El warehouse y la feature 002 siguen funcionando:
uv run python -m src.cli.main validate
uv run python -m src.cli.main ask "What is the total sales amount?"  # This will now fail-fast per A1
```

(Rollback parcial: si se quiere restaurar el comportamiento de 002 sin governance, se puede desinstalar el `GovernedQueryProvider` del CLI composition root. **Pero esto rompe la constitución** — no es una operación recomendada en producción.)
