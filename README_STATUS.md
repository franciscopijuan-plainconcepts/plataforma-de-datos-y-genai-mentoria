# Estado del Proyecto y Seguimiento

Documento vivo para seguimiento del estado actual, decisiones clave, riesgos y siguientes hitos de la Plataforma de Datos y GenAI.

## Estado actual (snapshot)

- Fecha de actualizacion: 2026-09-01
- Branch principal: main (roadmap convergido hasta Metabase v2.1 + Sales Prediction MLOps v3.0)
- Estado global: v3.0 implementado y validado
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
  - Subpaquete nuevo `src/data_engineering/semantic_layer/` con builder, resolver, governed_provider, registry, metrics, render, person_resolver (v2.0)
  - Contratos nuevos en `src/contracts/semantic_layer.py` (Pydantic v2 frozen) (v2.0)
  - **Metabase Integration v2.1**: servicio de Metabase local en Docker que visualiza como cards/dashboards las consultas SQL gobernadas generadas por el pipeline (v2.1)
  - **Governed SQL Cards**: al final de cada `ask --viewer <id>` exitoso, se crea automaticamente una card en Metabase con el SQL ya gobernado (v2.1)
  - **Metabase bootstrap script**: `scripts/metabase_bootstrap.py` hace setup automatico (PG role + admin user + DB connection + colleccion + state) — idempotente (v2.1)
  - **Login-as-person**: `PeopleViewerResolver` resuelve el viewer desde la tabla People directamente (snake_case ID, nombre con acentos, o sin acentos) — sin necesidad de viewers.yaml para personas reales (v2.1)
  - **CLI**: `metabase setup|status|cards|teardown|reset-cards`; `ask --no-metabase`; `ask --session <id>` (v2.1)
  - Modulos nuevos: `src/ai_engineering/metabase_client.py` (ONLY httpx import), `src/data_access/adapters/postgres/roles.py`, `src/contracts/metabase.py` (v2.1)
  - `on_query_complete` callback en `TextToSqlPipeline` (generico; no acopla Metabase al pipeline core) (v2.1)
  - `load_dotenv` con `override=True` en CLI — variables de .env siempre toman precedencia (v2.1)
  - Fix: `SqlValidator` ahora acepta funciones PostgreSQL (`to_char`, `date_trunc`, `round`, etc.) (v2.1)
  - **MLOps v3.0**: dominio `src/mlops/` aislado con feature engineering compartido, split cronológico, `LinearRegression` + `CatBoostRegressor`, artifact registry filesystem-based, staged promotion, inferencia y logging de predicción (v3.0)
  - Nuevos comandos CLI v3.0: `train-sales-model`, `promote-sales-model`, `predict-sales` (v3.0)
  - Nuevos contratos tipados en `src/contracts/mlops.py` (Pydantic v2 frozen) (v3.0)

## Evidencias de completitud

- Artefactos funcionales:
  - [data_dictionary.md](data_dictionary.md)
  - [.artifacts/load_manifest.json](.artifacts/load_manifest.json)
  - [.artifacts/semantic_layer.json](.artifacts/semantic_layer.json)
  - `.artifacts/metabase_state.json` (runtime-generated state cache para bootstrap/status)
  - `.artifacts/mlops/registry.json` (runtime-generated, no committed)
  - `.artifacts/mlops/predict_sales.log` (runtime-generated inference log)
- Especificacion y trazabilidad:
  - [specs/001-data-genai-platform-baseline/spec.md](specs/001-data-genai-platform-baseline/spec.md)
  - [specs/002-text-to-sql-v1/spec.md](specs/002-text-to-sql-v1/spec.md)
  - [specs/003-semantic-layer-v1/spec.md](specs/003-semantic-layer-v1/spec.md)
  - [specs/004-metabase-integration/spec.md](specs/004-metabase-integration/spec.md)
  - [specs/004-sales-prediction-model/spec.md](specs/004-sales-prediction-model/spec.md)
- Tareas:
  - Todas las tareas de Metabase integration marcadas como completadas en [specs/004-metabase-integration/tasks.md](specs/004-metabase-integration/tasks.md)
  - Todas las tareas T001-T039 marcadas como completadas en [specs/004-sales-prediction-model/tasks.md](specs/004-sales-prediction-model/tasks.md)

## Cobertura implementada vs roadmap

### Entregado en v0 (hecho)

- Provision y operacion local del DW en PostgreSQL.
- Modelado y carga de tablas Orders, Returns, People.
- EDA previa para inferencia de esquema.
- Diccionario de datos con semantica + tipos inferidos + notas de calidad.
- Reproducibilidad baseline (bootstrap/validate/teardown estable).

### Entregado en v1.0/v1.1 (hecho)

- Pipeline Text-to-SQL: NL question → LLM → SQL validado → ejecucion → resultados tipados.
- SQL validator de solo lectura y logging estructurado en `.artifacts/text_to_sql.log`.
- Comandos CLI `ask` y `evaluate`.

### Entregado en v2.0 (hecho)

- `SemanticLayerDocument` regenerable con métricas, dimensiones y relaciones.
- `GovernedQueryProvider` con RLS por `Region` enforced en el path NL→SQL.
- `viewers.yaml` local-only + resolución de viewers desde `People`.

### Entregado en v2.1 (hecho)

- Metabase local en Docker para visualizar como cards/dashboards las consultas SQL ya gobernadas.
- `scripts/metabase_bootstrap.py` idempotente para health check, admin setup, DB connection, collection y role `metabase_readonly`.
- `ask --session <id>` para agrupar cards en dashboards y `ask --no-metabase` para saltar la publicación cuando conviene.
- `MetabaseClient` aislado en `src/ai_engineering/metabase_client.py`, manteniendo el boundary de `httpx` en un solo módulo.

### Entregado en v3.0 (hecho)

- `train-sales-model`: extrae `Orders` vía `QueryProvider`, deriva features, hace split cronológico, entrena y compara `linear_regression` vs `catboost`.
- Artifact registry local `.artifacts/mlops/` con `registry.json`, `params.json`, `metrics.json`, `data_hash.txt`, `model.joblib`/`model.cbm`.
- Reproducibilidad verificada: mismo `data_hash` + mismos hiperparámetros ⇒ mismas métricas.
- `promote-sales-model`: staged promotion `dev/staging/prod`, rechazo de direct-to-prod salvo `--force`, historial completo preservado.
- `predict-sales`: inferencia sobre modelo promovido, fallback explícito para categorías no vistas, logging en `.artifacts/mlops/predict_sales.log`. *(Amendment 2026-08-26)* además persiste cada predicción (valor predicho, fecha/hora, run_id/modelo/ambiente y todos los parámetros de input) como fila en una nueva tabla SQL `Predictions` (creada por `bootstrap`).

## Calidad tecnica actual

- Arquitectura alineada con constitucion en [.specify/memory/constitution.md](.specify/memory/constitution.md).
- Separacion por capas respetada:
  - [src/contracts](src/contracts)
  - [src/data_access](src/data_access)
  - [src/data_engineering](src/data_engineering)
  - [src/ai_engineering](src/ai_engineering)
  - [src/mlops](src/mlops)
  - [src/cli](src/cli)
- Calidad validada:
  - `uv run pytest tests/ -x` → 160 passed, 2 skipped
  - `uv run mypy --strict src/mlops src/contracts/mlops.py src/cli/main.py tests/` → 0 errores
  - Metabase quedó validado por su bootstrap idempotente, boundary tests dedicados y la publicación automática de governed SQL cards desde `ask`.

## Riesgos y puntos de atencion

1. Evolucion de esquema del archivo fuente  
Si cambia estructura o tipos del Excel, hay que revalidar inferencia, contratos, Semantic Layer y training data hash.

2. Dependencia de entorno Docker local  
Docker sigue siendo prerequisito duro para bootstrap e integracion end-to-end.

3. Calidad semantica para Text-to-SQL  
Mitigada en v2.0 por el Semantic Layer, pero la calidad de prompts/metricas sigue siendo superficie a vigilar.

4. Gobernanza del training path batch  
**Diferido y aceptado por scope**: Principle IV sigue satisfecho en el path NL→SQL, pero el job batch `train-sales-model` no opera con un viewer/RLS porque entrena sobre el dataset global completo. Esto queda documentado como decisión de alcance, no regresión.

5. Observabilidad MLOps incompleta a futuro  
**Nueva deuda explícita v3.1+**: ya existe logging de predicción (input/output/latencia), pero drift monitoring, alertas y aprobación humana formal siguen pendientes.

## Siguiente plan de ejecucion recomendado

1. Definir feature v3.1 para observabilidad MLOps (drift, alertas, reporting de producción).
2. Evaluar gobierno del training path si se introducen modelos por región o por tenant.
3. Resolver el mismatch `People.Region` vs `Orders.Region` heredado del Semantic Layer.
4. Diseñar RBAC column-level y audit logging persistente si la plataforma pasa de CLI local a servicio compartido.
5. Mantener reproducibilidad con gates de CI sobre `pytest` + `mypy --strict`.
6. Consolidar la convivencia del doble `specs/004-*` en un proximo cleanup documental o de renombrado planificado.

## Backlog de seguimiento (editable)

| ID | Hito | Estado | Owner | Fecha objetivo | Notas |
|---|---|---|---|---|---|
| M0 | v0 Baseline (warehouse + dictionary) | Completado | - | 2026-08-04 | Local PostgreSQL + data dictionary + CLI |
| M1 | v1.0 Text-to-SQL sobre Orders | Completado | - | 2026-08-11 | Pipeline NL→SQL→resultados tipados |
| M2 | v1.1 Hardening + Evaluation | Completado | - | 2026-08-11 | Logging + sanity-check (~10 preguntas) |
| M3 | v2.0 Semantic Layer + RLS Governance | Completado | - | 2026-08-17 | SemanticLayerDocument + GovernedQueryProvider (RLS enforced, Principle IV satisfied) |
| M3.1 | v2.1 Metabase Integration | Completado | - | 2026-08-20 | Metabase + governed SQL cards + sessions + CLI ops + metabase_bootstrap.py |
| M4 | v3.0 Sales Prediction Model (MLOps) | Completado | - | 2026-08-25 | Training + registry + promotion + inference + prediction history |
| M5 | v3.1 MLOps Observability + Governance hardening | Pendiente | TBD | TBD | Drift monitoring, approval flow, training-path governance review |
| M6 | v3.2 RBAC column-level + People.Region taxonomy + Audit | Pendiente | TBD | TBD | Resolución de mismatch Canada; auth real; audit persistente |

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

### Iteracion cerrada

Fecha: 2026-08-25  
Bloque cerrado: Feature 004 `sales-prediction-model`  
Cambio principal: se entrego el dominio `src/mlops/` con training reproducible, staged promotion e inferencia CLI.  
Impacto: milestone M4 completado; la plataforma ya cubre Data Engineering + AI Engineering + Metabase self-service + MLOps.  
Siguiente paso: definir v3.1 para drift/monitoring y revisar gobernanza del training path batch.

## Onboarding rapido para continuar desde aqui

1. Leer [README.md](README.md).
2. Leer [README_SPECKIT.md](README_SPECKIT.md).
3. Revisar [specs/004-metabase-integration](specs/004-metabase-integration) y [specs/004-sales-prediction-model](specs/004-sales-prediction-model).
4. Ejecutar validacion local:
   - uv sync
   - uv run python -m src.cli.main bootstrap
   - uv run python -m src.cli.main validate
   - uv run python scripts/metabase_bootstrap.py
   - uv run python -m src.cli.main train-sales-model
5. Proponer siguiente feature con Spec Kit y registrar avance en este archivo.
