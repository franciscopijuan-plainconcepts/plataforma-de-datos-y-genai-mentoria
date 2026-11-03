# Estado del Proyecto y Seguimiento

Documento vivo para seguimiento del estado actual, decisiones clave, riesgos y siguientes hitos de la Plataforma de Datos y GenAI.

## Migracion Ossie en la rama de trabajo (sin publicar)

La fuente semantica vigente es [semantic/sales.ossie.yaml](semantic/sales.ossie.yaml).
Se retiraron las definiciones Python redundantes; JSON/Markdown son derivados,
no fuentes alternativas. La ejecucion conserva SQL gobernado trazable y la
evaluacion compara resultados bajo el mismo alcance.
Ver [evidencia, aprobaciones y limites](docs/semantic-migration.md).
El snapshot v3.0 siguiente documenta la entrega anterior, no estas modificaciones
pendientes del commit/push/PR del usuario.

## Estado actual (snapshot)

- Fecha de actualizacion: 2026-10-30
- Branch principal: feature/diego/forecast-semantic (roadmap convergido hasta Metabase v2.1 + Sales Prediction MLOps v3.0 + Batch Forecast Seeding v3.1 + forecast semantic/accuracy/dashboard v3.3)
- Estado global: v3.0 implementado y validado; v3.1 (batch forecast seeding, sin LLM) implementado y validado end-to-end; v3.3 (métricas de forecast en Ossie, backtest fila a fila y dashboard predictivo; parcial de M5) implementado y validado end-to-end, con una pasada de revisión/QA aplicada
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
  - **Batch forecast seeding v3.1 (sin LLM)**: `src/mlops/seed_predictions.py` entrena/promueve un modelo automáticamente (solo la primera vez) y siembra la tabla `Predictions` con forecasts deterministas de los próximos meses; se ejecuta automáticamente al final de `bootstrap` (best-effort, idempotente) para que los dashboards de Metabase tengan datos sin depender de una llamada en vivo al LLM/modelo (v3.1)
  - Nuevo comando CLI v3.1: `seed-sales-predictions [--env] [--months-ahead] [--force]` (re-siembra manual) (v3.1)

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

### Entregado en v3.1 (hecho)

- **Decisión de diseño**: generar "Sales predicho para los próximos meses" para un dashboard es una tarea batch, no un trigger del LLM a petición del cliente — el LLM nunca invoca el modelo directamente (Principle I); tanto `predict-sales-nl` (pregunta ad-hoc parseada por LLM) como este seeder batch pasan por la MISMA función tipada `predict_sales()`, pero para inputs de forecast deterministas no tiene sentido pagar latencia/costo/riesgo de fallo de LLM en tiempo de siembra.
- `src/mlops/seed_predictions.py`: si no hay modelo promovido en el ambiente objetivo, entrena + promueve uno automáticamente (mejor RMSE, dev→staging→prod); extrae las 10 combinaciones `(Region, Category)` más frecuentes de `Orders` y construye un perfil "orden típica" por combinación (moda de campos categóricos restantes, mediana de `quantity`); predice `Sales` para cada perfil × los próximos N meses (default 6) y persiste cada predicción en `Predictions` vía el mismo `predict_sales()` de la CLI.
- **Idempotente**: si el run activo ya tiene filas futuras en `Predictions`, la siembra se salta (sin duplicados en `bootstrap` repetidos).
- Se ejecuta automáticamente al final de `bootstrap` (best-effort — nunca hace fallar el bootstrap); controlable con `SEED_SALES_PREDICTIONS`/`SEED_SALES_PREDICTIONS_ENV`/`SEED_SALES_PREDICTIONS_MONTHS` (`.env.example`).
- Nuevo comando `seed-sales-predictions [--env] [--months-ahead] [--force]` para re-sembrar manualmente sin un `bootstrap` completo.

### Entregado en v3.3 (hecho, parcial de M5)

- **Perfil Ossie con tablas del warehouse.** `import_ossie` acepta datasets que no vienen del workbook (`Predictions`, view `Forecast Accuracy`) validados contra su `TableDef`. Cada métrica declara `source_table`, su fórmula se valida FROM esa tabla, y Returns solo es accesible desde Orders.
- **Métricas de forecast y precisión** propuestas por el agente doc → Ossie (`ejemplos/metricas-forecast.md`) y revisadas a mano antes de activarlas:
  - Sobre `Predictions`: `predicted_sales` y `avg_predicted_line_sales`.
  - Sobre `Forecast Accuracy`: `backtest_actual_sales`, `backtest_predicted_sales`, `forecast_abs_error`, `forecast_wape` y `forecast_bias`.
  - El modelo queda en `content_version` 1.1.0, con 15 métricas y 5 datasets.
  - `derives_from` no puede cruzar tablas y `uses_returns` solo se permite en métricas de Orders. Las 7 métricas nuevas salieron del agente `propose_semantic_update.py` (ahora con soporte de `source_table`) y se revisaron a mano (3 renombrados).
- **Backtest fila a fila (`backtest-sales-model [--env] [--force]`, automático en `bootstrap` con `SEED_FORECAST_BACKTEST`).**
  - El forecast va con el reloj real y Orders termina en 2017, así que "el mes real que llega" se simula con el holdout cronológico del run activo, que el modelo nunca vio al entrenar.
  - Reproduce el split cronológico exacto del run; si el `data_hash` o los parámetros del split no coinciden, puntúa solo las filas estrictamente posteriores a la fecha de corte.
  - Cada línea del holdout se puntúa y se guarda en `Forecast Backtest`. La view `Forecast Accuracy` la agrega por Run ID × mes × Region × Category (Actual Sales, Predicted Sales, Abs Error, Line Count).
  - Es idempotente por `Run ID`, así que cada reentrenamiento añade su historial. Esa es la base del drift monitoring. `--force` borra las filas del run activo y lo vuelve a puntuar (p. ej. tras corregir el modelo).
  - Resultado real con los datos actuales: 10.258 líneas de holdout (jul–dic 2017), WAPE ≈ 14,1 %, sesgo ≈ −0,9 %.
  - `bootstrap` crea siempre la tabla `Forecast Backtest` y la view `Forecast Accuracy`, aunque el backtest se salte o falle.
- **Dashboard predictivo (`metabase forecast-dashboard --viewer <id> [--env] [--replace]`).**
  - Tiene 6 cards construidas desde las métricas Ossie por nombre (forecast del próximo mes = ticket medio predicho por línea, WAPE, sesgo, histórico mensual, forecast de los próximos meses y backtest previsto vs real), con SQL gobernado por RLS (Region) y ejecutado antes de publicar.
  - Idempotente: si el dashboard ya tiene cards, se salta. `--replace` lo reconstruye y solo borra las cards que creó el propio comando (marcadas con `source=forecast-dashboard` en la descripción), conservando las demás. Si falla, hace rollback de las cards que había creado.
  - Las cards fijan el `run_id` activo y filtran por `CURRENT_DATE`: tras promocionar otro run, o pasados `SEED_SALES_PREDICTIONS_MONTHS` meses desde la siembra, hay que resembrar y relanzarlo con `--replace`.
- **Preguntas en lenguaje natural (`ask`/`chart` y `app_web.py`).** `Forecast Accuracy` se consulta bajo RLS como `Orders` y `Predictions`. Con un run activo en `SEED_SALES_PREDICTIONS_ENV` (default `prod`), el prompt restringe `Predictions` a ese `"Environment"` y `"Run ID"` y `Forecast Accuracy` a ese `"Run ID"`, salvo que la pregunta compare runs o entornos de forma explícita. También explica que `Predicted Sales` es el forecast de UNA línea de pedido representativa (usar `AVG`; nunca presentar `SUM` como total mensual). Las métricas fuera de Orders se etiquetan `[FROM "<tabla>"]` en el prompt.
- **Fix Metabase v0.58.** Los dashboards se buscan por los items de la colección (el filtro antiguo devolvía 400), lo que también arregla los dashboards de `ask --session`.
- **Revisión/QA aplicada sobre v3.3.**
  - `gov_bypass` real en las descripciones de las cards (solo `true` para un viewer local/dev con acceso completo).
  - Rollback de las cards creadas si falla la publicación del dashboard; `--replace` solo toca sus propias cards.
  - Holdout exacto del run en el backtest y vista `Forecast Accuracy` siempre creada.
  - Validación de dependencias entre métricas: `derives_from` sin cruzar tablas y `uses_returns` solo en Orders.
  - Scoping por run en el prompt de `ask`/`chart` (ver arriba).
  - `Predicted Sales` con clamp a 0 (Sales no puede ser negativo) en el camino de predicción compartido por `predict-sales`/siembra y el backtest.
  - `backtest-sales-model --force` para re-puntuar el run activo.
  - `bootstrap` pasa el esquema de Orders del workbook que carga (también con `--source <file>`) a la siembra y al backtest, por lo que estos pasos best-effort nunca abortan el bootstrap.
- **Limitaciones conocidas.**
  - Los perfiles de forecast solo cubren los 10 pares Region × Category más frecuentes (hoy todos Office Supplies): algunos viewers (p. ej. `marilene_rousseau`, Caribbean) ven las cards de forecast vacías; histórico y backtest sí tienen datos.
  - `Orders.Region = 'Canada'` no existe en `People` (mismatch de taxonomía preexistente, ver M6).
  - Histórico (2014–2017) y forecast (desde el próximo mes) son cards separadas por el hueco entre ambos.
  - Los umbrales de alerta de drift siguen pendientes en M5.

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
  - `uv run --frozen python -m pytest -q tests/unit tests/contract -k 'not test_is_setup_complete_false_when_setup_token_present'` → 377 passed, 1 deselected (mock Metabase defectuoso de baseline) (2026-10-30)
  - Integraciones PostgreSQL (`RUN_SEMANTIC_PG_TESTS=1 RUN_SEMANTIC_LIVE_TESTS=1`: referencia, live read-only, candidato Ossie, rollback) → 44 passed
  - `uv run --frozen python -m mypy src --follow-imports=silent` → solo 4 errores previos en `src/mlops/seed_predictions.py` (anteriores a v3.3)
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

1. Cerrar M5: umbral de alerta de drift sobre `forecast_wape` entre runs (¿umbral de negocio o estadístico?), apoyado en el historial por `Run ID` de `Forecast Accuracy`, y approval flow para la promoción de modelos.
2. Evaluar gobierno del training path si se introducen modelos por región o por tenant.
3. Resolver el mismatch `People.Region` vs `Orders.Region` heredado del Semantic Layer (`Orders.Region = 'Canada'` no existe en `People`).
4. Ampliar los perfiles de forecast más allá del top-10 Region × Category (hoy todo Office Supplies) para que todos los viewers vean las cards de forecast con datos.
5. Diseñar RBAC column-level y audit logging persistente si la plataforma pasa de CLI local a servicio compartido.
6. Mantener reproducibilidad con gates de CI sobre `pytest` + `mypy --strict`.
7. Consolidar la convivencia del doble `specs/004-*` en un proximo cleanup documental o de renombrado planificado.

## Backlog de seguimiento (editable)

| ID | Hito | Estado | Owner | Fecha objetivo | Notas |
|---|---|---|---|---|---|
| M0 | v0 Baseline (warehouse + dictionary) | Completado | - | 2026-08-04 | Local PostgreSQL + data dictionary + CLI |
| M1 | v1.0 Text-to-SQL sobre Orders | Completado | - | 2026-08-11 | Pipeline NL→SQL→resultados tipados |
| M2 | v1.1 Hardening + Evaluation | Completado | - | 2026-08-11 | Logging + sanity-check (~10 preguntas) |
| M3 | v2.0 Semantic Layer + RLS Governance | Completado | - | 2026-08-17 | SemanticLayerDocument + GovernedQueryProvider (RLS enforced, Principle IV satisfied) |
| M3.1 | v2.1 Metabase Integration | Completado | - | 2026-08-20 | Metabase + governed SQL cards + sessions + CLI ops + metabase_bootstrap.py |
| M4 | v3.0 Sales Prediction Model (MLOps) | Completado | - | 2026-08-25 | Training + registry + promotion + inference + prediction history |
| M5 | v3.1 MLOps Observability + Governance hardening | Parcial | - | TBD | Batch forecast seeding (`seed-sales-predictions`, auto en `bootstrap`) **Completado** 2026-09-02; precisión del forecast (backtest + `Forecast Accuracy` + métricas Ossie + dashboard predictivo) **Completado** 2026-10-30; alertas/umbrales de drift, approval flow y training-path governance review siguen **Pendiente** |
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

Fecha: 2026-09-02
Bloque cerrado: Batch forecast seeding (v3.1, parcial de M5)
Cambio principal: `src/mlops/seed_predictions.py` + comando CLI `seed-sales-predictions`, integrado en `bootstrap` (best-effort, idempotente). Decisión de diseño explícita: forecast de dashboard es batch/no-LLM, no un trigger on-demand del cliente vía LLM — reutiliza `predict_sales()` sin lógica paralela.
Impacto: `Predictions` queda poblada con forecasts de los próximos 6 meses inmediatamente tras `uv run python -m src.cli.main bootstrap`, sin dependencia de Postgres/LLM en tiempo de vista del dashboard (verificado end-to-end: primera corrida siembra 60 filas, segunda corrida idempotente las salta, `seed-sales-predictions --force` re-siembra).
Siguiente paso: drift monitoring, approval flow y training-path governance review siguen pendientes (resto de M5).

Fecha: 2026-10-30
Bloque cerrado: Métricas de forecast en Ossie + precisión del forecast + dashboard predictivo (v3.3, parcial de M5)
Cambio principal: perfil Ossie con tablas del warehouse; 7 métricas nuevas generadas con el agente doc → Ossie y revisadas a mano; `backtest-sales-model` y la view `Forecast Accuracy`; `metabase forecast-dashboard`.
Impacto: el error del modelo (WAPE/sesgo) se puede consultar por NL, en la capa semántica y en Metabase, siempre bajo RLS. El historial por run permite detectar degradación entre reentrenamientos.
Siguiente paso: umbral de alerta de drift sobre `forecast_wape` entre runs (pregunta abierta del agente: ¿umbral de negocio o estadístico?), approval flow y governance del training path.

Fecha: 2026-10-30
Bloque cerrado: Revisión/QA de v3.3 (hardening)
Cambio principal: `gov_bypass` real en las cards, rollback y `--replace` acotado a las cards propias, holdout exacto en el backtest, view `Forecast Accuracy` siempre creada, validación de `derives_from`/`uses_returns`, scoping por run en el prompt de `ask`/`chart`, clamp de `Predicted Sales` a 0, `backtest-sales-model --force` y `bootstrap` robusto (esquema de Orders del workbook cargado, también con `--source`). Además, fix de la búsqueda de dashboards en Metabase v0.58.
Impacto: las respuestas NL sobre forecast y precisión no mezclan runs ni entornos, el dashboard no deja cards huérfanas y la siembra/backtest nunca abortan el bootstrap.
Siguiente paso: alerta de drift sobre `forecast_wape`, ampliar los perfiles de forecast más allá del top-10 y resolver el mismatch Canada.

## Onboarding rapido para continuar desde aqui

1. Leer [README.md](README.md).
2. Leer [README_SPECKIT.md](README_SPECKIT.md).
3. Revisar [specs/004-metabase-integration](specs/004-metabase-integration) y [specs/004-sales-prediction-model](specs/004-sales-prediction-model).
4. Ejecutar validacion local:
   - uv sync
   - uv run python -m src.cli.main bootstrap   # ahora tambien entrena/promueve y siembra `Predictions` (v3.1, best-effort)
   - uv run python -m src.cli.main validate
   - uv run python scripts/metabase_bootstrap.py
   - uv run python -m src.cli.main train-sales-model   # opcional: entrenar un run adicional manualmente
5. Proponer siguiente feature con Spec Kit y registrar avance en este archivo.
