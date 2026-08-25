# Estado del Proyecto y Seguimiento

Documento vivo para seguimiento del estado actual, decisiones clave, riesgos y siguientes hitos de la Plataforma de Datos y GenAI.

## Estado actual (snapshot)

- Fecha de actualizacion: 2026-08-25
- Branch principal: main (feature implementada en `004-sales-prediction-model`)
- Estado global: v3.0 MLOps implementado y validado
- Ambito completado:
  - Warehouse local PostgreSQL en Docker (v0 baseline)
  - Ingestion desde Global Superstore Data.xlsx (v0)
  - Data dictionary generado y versionado (v0)
  - CLI baseline: bootstrap, teardown, validate, generate-dictionary (v0)
  - Text-to-SQL v1.0/v1.1: `ask`, `evaluate`, validator SQL, logging estructurado
  - Semantic Layer v2.0: artifact determinista + `generate-semantic-layer` + RLS por `Region`
  - Viewer-based governance: `ask --viewer <id>` + `--allow-full-access` (local/dev only)
  - Dominio `src/ai_engineering/` completo y boundaries reforzados
  - **MLOps v3.0**: dominio `src/mlops/` aislado con feature engineering compartido, split cronológico, `LinearRegression` + `CatBoostRegressor`, artifact registry filesystem-based, staged promotion, inferencia y logging de predicción
  - Nuevos comandos CLI v3.0: `train-sales-model`, `promote-sales-model`, `predict-sales`
  - Nuevos contratos tipados en `src/contracts/mlops.py` (Pydantic v2 frozen)

## Evidencias de completitud

- Artefactos funcionales:
  - [data_dictionary.md](data_dictionary.md)
  - [.artifacts/load_manifest.json](.artifacts/load_manifest.json)
  - [.artifacts/semantic_layer.json](.artifacts/semantic_layer.json)
  - `.artifacts/mlops/registry.json` (runtime-generated, no committed)
- Especificacion y trazabilidad:
  - [specs/001-data-genai-platform-baseline/spec.md](specs/001-data-genai-platform-baseline/spec.md)
  - [specs/002-text-to-sql-v1/spec.md](specs/002-text-to-sql-v1/spec.md)
  - [specs/003-semantic-layer-v1/spec.md](specs/003-semantic-layer-v1/spec.md)
  - [specs/004-sales-prediction-model/spec.md](specs/004-sales-prediction-model/spec.md)
- Tareas:
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

### Entregado en v3.0 (hecho)

- `train-sales-model`: extrae `Orders` vía `QueryProvider`, deriva features, hace split cronológico, entrena y compara `linear_regression` vs `catboost`.
- Artifact registry local `.artifacts/mlops/` con `registry.json`, `params.json`, `metrics.json`, `data_hash.txt`, `model.joblib`/`model.cbm`.
- Reproducibilidad verificada: mismo `data_hash` + mismos hiperparámetros ⇒ mismas métricas.
- `promote-sales-model`: staged promotion `dev/staging/prod`, rechazo de direct-to-prod salvo `--force`, historial completo preservado.
- `predict-sales`: inferencia sobre modelo promovido, fallback explícito para categorías no vistas, logging en `.artifacts/mlops/predict_sales.log`.

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

## Backlog de seguimiento (editable)

| ID | Hito | Estado | Owner | Fecha objetivo | Notas |
|---|---|---|---|---|---|
| M0 | v0 Baseline (warehouse + dictionary) | Completado | - | 2026-08-04 | Local PostgreSQL + data dictionary + CLI |
| M1 | v1.0 Text-to-SQL sobre Orders | Completado | - | 2026-08-11 | Pipeline NL→SQL→resultados tipados |
| M2 | v1.1 Hardening + Evaluation | Completado | - | 2026-08-11 | Logging + sanity-check |
| M3 | v2.0 Semantic Layer + RLS Governance | Completado | - | 2026-08-17 | SemanticLayerDocument + GovernedQueryProvider |
| M4 | v3.0 Sales Prediction Model (MLOps) | Completado | - | 2026-08-25 | Training + registry + promotion + inference |
| M5 | v3.1 MLOps Observability + Governance hardening | Pendiente | TBD | TBD | Drift monitoring, approval flow, training-path governance review |

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
Impacto: milestone M4 completado; la plataforma ya cubre Data Engineering + AI Engineering + MLOps.  
Siguiente paso: definir v3.1 para drift/monitoring y revisar gobernanza del training path batch.

## Onboarding rapido para continuar desde aqui

1. Leer [README.md](README.md).
2. Leer [README_SPECKIT.md](README_SPECKIT.md).
3. Revisar [specs/004-sales-prediction-model](specs/004-sales-prediction-model).
4. Ejecutar validacion local:
   - uv sync
   - uv run python -m src.cli.main bootstrap
   - uv run python -m src.cli.main validate
   - uv run python -m src.cli.main train-sales-model
5. Proponer siguiente feature con Spec Kit y registrar avance en este archivo.
