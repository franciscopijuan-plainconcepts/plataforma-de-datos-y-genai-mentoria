# Estado del Proyecto y Seguimiento

Documento vivo para seguimiento del estado actual, decisiones clave, riesgos y siguientes hitos de la Plataforma de Datos y GenAI.

## Estado actual (snapshot)

- Fecha de actualizacion: 2026-08-17
- Branch principal: main (feature en `003-semantic-layer-v1`)
- Estado global: v2.0 Semantic Layer + Governance implementado y validado
- Ambito completado:
  - Warehouse local PostgreSQL en Docker (v0 baseline)
  - Ingestion desde Global Superstore Data.xlsx (v0)
  - Data dictionary generado y versionado (v0)
  - CLI operativa baseline: bootstrap, teardown, validate, generate-dictionary (v0)
  - Contratos tipados, boundaries y tests de integracion (v0)
  - Text-to-SQL v1.0: pipeline NL → LLM → SQL validado → ejecucion → resultados tipados (v1.0)
  - Text-to-SQL v1.1: logging estructurado + sanity-check evaluation (~10 preguntas) (v1.1)
  - Nuevos comandos CLI v1.x: `ask <question>` y `evaluate` (v1.0/v1.1)
  - Dominio `src/ai_engineering/` (llm_client, prompt_builder, sql_validator, pipeline, evaluation)
  - `QueryProvider` Protocol extendido con `execute_readonly_query` (v1.0)
  - **Semantic Layer v2.0**: `SemanticLayerDocument` (8 métricas, 11 dimensiones, 2 relaciones) regenerable como artifact determinista (v2.0)
  - **RLS enforcement via `GovernedQueryProvider`**: ningún SQL del LLM bypassa `WHERE Region IN (viewer.regions)` (v2.0, constitution Principle IV satisfecha por primera vez)
  - **Viewer-based governance**: `viewers.yaml` + `ask --viewer <id>` + `--allow-full-access` (local/dev only) (v2.0)
  - **Prompt enrichment**: `PromptBuilder` ahora incluye bloque condensado de métricas/dimensiones/joins cuando el Semantic Layer está cargado (v2.0)
  - Nuevos comandos CLI v2.0: `generate-semantic-layer`, `ask --viewer <id>` (v2.0)
  - Logging extendido con `viewer_id`, `regions`, `gov_bypass` flag en `.artifacts/text_to_sql.log` (v2.0, FR-021)
  - Subpaquete nuevo `src/data_engineering/semantic_layer/` con builder, resolver, governed_provider, registry, metrics, render (v2.0)
  - Contratos nuevos en `src/contracts/semantic_layer.py` (Pydantic v2 frozen) (v2.0)

## Evidencias de completitud

- Artefactos funcionales:
  - [data_dictionary.md](data_dictionary.md)
  - [.artifacts/load_manifest.json](.artifacts/load_manifest.json)
- Especificacion y trazabilidad:
  - [specs/001-data-genai-platform-baseline/spec.md](specs/001-data-genai-platform-baseline/spec.md)
  - [specs/001-data-genai-platform-baseline/plan.md](specs/001-data-genai-platform-baseline/plan.md)
  - [specs/001-data-genai-platform-baseline/tasks.md](specs/001-data-genai-platform-baseline/tasks.md)
- Tareas:
  - Todas las tareas T001-T030 marcadas como completadas en [specs/001-data-genai-platform-baseline/tasks.md](specs/001-data-genai-platform-baseline/tasks.md)

## Cobertura implementada vs roadmap

### Entregado en v0 (hecho)

- Provision y operacion local del DW en PostgreSQL.
- Modelado y carga de tablas Orders, Returns, People.
- EDA previa para inferencia de esquema (sin hardcode inicial ciego).
- Diccionario de datos con semantica + tipos inferidos + notas de calidad.
- Reproducibilidad baseline (flujo bootstrap/validate/teardown estable).

### Entregado en v1.0 (hecho)

- Pipeline Text-to-SQL: NL question → LLM (Forge/OpenAI SDK) → SQL validado → ejecucion → resultados tipados.
- SQL validator: SELECT-only, Orders-only, columnas-existentes, no comentarios, single-statement.
- Prompt builder: serializa DataDictionaryDocument en formato condensado (~500-800 tokens).
- LLM client: wrapper tipado del OpenAI SDK con config por env vars (`FORGE_*`).
- Comando CLI `ask <question>`: imprime SQL generado + resultados tipados.
- Boundary test: `openai`/`httpx` confinados a `src/ai_engineering/`.

### Entregado en v1.1 (hecho)

- Logging estructurado por cada call (`.artifacts/text_to_sql.log`).
- Sanity-check evaluation: ~10 preguntas, comparacion de SQL normalizado, summary simple.
- Comando CLI `evaluate`: corre el sanity check e imprime `X / N correct`.

### Pendiente para v3.0 (siguiente)

- RBAC column-level fino (ocultar columnas individuales por rol) — declarado pero no enforced en v2.0.
- Resolucion del mismatch de taxonomia `People.Region` (Eastern/Western Canada) vs `Orders.Region` (Canada) — conservador en v2.0; la consolidación es v3.0+.
- Sistema de autenticación real (OIDC/JWT) — fuera de scope en v2.0 (viewer viene de config local).
- Audit logging persistente y lineage indexable — logging básico sí, sistema completo no.
- Migración a Google BigQuery.

## Calidad tecnica actual

- Arquitectura alineada con constitucion en [.specify/memory/constitution.md](.specify/memory/constitution.md).
- Separacion por capas respetada:
  - [src/contracts](src/contracts)
  - [src/data_access](src/data_access)
  - [src/data_engineering](src/data_engineering)
  - [src/cli](src/cli)
- Tests disponibles:
  - contrato en [tests/contract](tests/contract)
  - integracion en [tests/integration](tests/integration)

## Riesgos y puntos de atencion

1. Evolucion de esquema del archivo fuente
Si cambia estructura o tipos del Excel, hay que revalidar inferencia, contratos y manifest.

2. Dependencia de entorno Docker local
Incidencias de Docker afectan bootstrap/integracion; conviene estandarizar prerequisitos en onboarding.

3. Calidad semantica para Text-to-SQL
La siguiente fase requiere definiciones de negocio mas estrictas para evitar SQL incorrecto aunque sea sintacticamente valido.
**Resuelto en v2.0**: el `SemanticLayerDocument` ahora formaliza 8 métricas (incl. net_sales, return_rate, net_profit) con fórmulas SQL canonizadas, y el `PromptBuilder` las inyecta en el prompt del LLM.

4. Gobernanza aun diferida
**Resuelto en v2.0**: RLS por `Region` está enforced por el `GovernedQueryProvider` decorator en cada `execute_readonly_query` call. Ningún SQL del LLM bypassa la governance (constitution Principle IV satisfecha). RBAC column-level queda para v3.0+.

5. Mismatch de taxonomía `People.Region` (24 valores, divide Canada en Eastern/Western) vs `Orders.Region` (23 valores, Canada unificado)
**Nuevo riesgo en v2.0**: el resolver es conservador — un viewer scoped a `Eastern Canada` no matchea filas de `Orders.Region = 'Canada'` (devuelve 0 filas). La consolidación es v3.0+ scope.

## Siguiente plan de ejecucion recomendado

1. Abrir nueva feature Spec Kit para v1.0 Text-to-SQL (scope minimo).
2. Definir contratos de consulta segura (entrada NL, SQL generado, validaciones).
3. Implementar pipeline minimo:
  - prompt/control de contexto
  - generacion SQL
  - validacion previa a ejecucion
  - ejecucion controlada sobre Orders
4. Agregar metricas de calidad:
  - porcentaje de consultas correctas
  - tasa de consultas bloqueadas por seguridad
  - tiempo de respuesta promedio
5. Cerrar con quickstart y tests de integracion de extremo a extremo.

## Backlog de seguimiento (editable)

| ID | Hito | Estado | Owner | Fecha objetivo | Notas |
|---|---|---|---|---|---|
| M0 | v0 Baseline (warehouse + dictionary) | Completado | - | 2026-08-04 | Local PostgreSQL + data dictionary + CLI |
| M1 | v1.0 Text-to-SQL sobre Orders | Completado | - | 2026-08-11 | Pipeline NL→SQL→resultados tipados |
| M2 | v1.1 Hardening + Evaluation | Completado | - | 2026-08-11 | Logging + sanity-check (~10 preguntas) |
| M3 | v2.0 Semantic Layer + RLS Governance | Completado | - | 2026-08-17 | SemanticLayerDocument + GovernedQueryProvider (RLS enforced, Principle IV satisfied) |
| M4 | v3.0 RBAC column-level + People.Region taxonomy + Audit | Pendiente | TBD | TBD | Resolución de mismatch Canada; auth real; audit persistente |

## Rutina de mantenimiento de este documento

Actualizar este archivo al cerrar cada bloque relevante:

- cierre de una feature Spec Kit
- cambios de alcance/roadmap
- decisiones arquitectonicas con impacto
- incidentes de calidad o reproducibilidad

Formato sugerido para entradas rapidas al final de cada iteracion:

```text
Fecha:
Bloque cerrado:
Cambio principal:
Impacto:
Siguiente paso:
```

## Onboarding rapido para continuar desde aqui

1. Leer [README.md](README.md).
2. Leer [README_SPECKIT.md](README_SPECKIT.md).
3. Revisar feature baseline en [specs/001-data-genai-platform-baseline](specs/001-data-genai-platform-baseline).
4. Ejecutar validacion local:
   - uv sync
   - uv run python -m src.cli.main bootstrap
   - uv run python -m src.cli.main validate
5. Proponer siguiente feature con Spec Kit y registrar avance en este archivo.
