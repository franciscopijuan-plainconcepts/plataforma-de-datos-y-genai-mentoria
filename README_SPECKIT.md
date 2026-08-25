# Spec Kit en Este Repositorio

Guia de continuidad para el equipo sobre como usamos Spec Kit en este proyecto, que se genero, donde vive cada artefacto y como seguir trabajando con el mismo flujo.

## Objetivo de esta guia

- Estandarizar como levantar nuevas funcionalidades con Spec Kit.
- Explicar la estructura real de carpetas en este repo.
- Evitar que el equipo salte pasos clave (constitucion, spec, plan, tasks).
- Dejar trazabilidad clara entre decisiones, implementacion y validacion.

## Resumen de lo que ya hicimos con Spec Kit

Se ejecutaron cuatro features completas con Spec Kit:

1. **Feature 001 (`data-genai-platform-baseline`)** — v0 baseline: PostgreSQL en Docker, data dictionary, CLI bootstrap/teardown/validate/generate-dictionary, contratos tipados, tests de contrato e integracion.
2. **Feature 002 (`text-to-sql-v1`)** — v1.0/v1.1: pipeline NL→SQL sobre Orders via Forge proxy, `ask` + `evaluate` CLI, logging estructurado, sanity-check de ~10 preguntas.
3. **Feature 003 (`semantic-layer-v1`)** — v2.0: Semantic Layer con métricas/dimensiones/relaciones + RLS por `Region` usando `People`. Satisface constitution Principle IV por primera vez.
4. **Feature 004 (`sales-prediction-model`)** — v3.0: dominio `src/mlops/` aislado con `train-sales-model`, artifact registry `.artifacts/mlops/registry.json`, staged promotion (`promote-sales-model`) e inferencia (`predict-sales`).

Cada feature siguio el flujo completo: constitution (solo 001), spec, plan + research + data-model + contracts + quickstart, tasks, implement por fases, validacion. La feature 003 introdujo:
- `src/data_engineering/semantic_layer/` subpaquete (builder, resolver, governed_provider, registry, metrics, render).
- `src/contracts/semantic_layer.py` con `SemanticLayerDocument`, `SemanticViewer`, `Metric`, `Dimension`, `SemanticRelationship`, `SemanticQueryResolverProtocol` (todos Pydantic v2 frozen).
- `GovernedQueryProvider` Decorator sobre `QueryProvider` que enforce RLS en cada call (constitution Principle IV, NON-NEGOTIABLE).
- `viewers.yaml` + registry con `pyyaml` (única dependencia nueva).
- `semantic_layer.json`/`semantic_layer.md` artifacts regenerables y deterministas.
- CLI: `generate-semantic-layer` + `ask --viewer <id>` + `--allow-full-access` (local/dev only).
- Boundary tests extendidos: `pyyaml` confined a registry; ningún caller de `execute_readonly_query` en `ai_engineering` importa el adapter directamente (RLS bypass prevention).

Para la feature 001, el detalle del flujo abajo se mantiene como referencia:

1. Constitucion de arquitectura y calidad ratificada en [.specify/memory/constitution.md](.specify/memory/constitution.md).
2. Especificacion funcional creada en [specs/001-data-genai-platform-baseline/spec.md](specs/001-data-genai-platform-baseline/spec.md).
3. Plan de implementacion generado en [specs/001-data-genai-platform-baseline/plan.md](specs/001-data-genai-platform-baseline/plan.md).
4. Artefactos de diseno completados:
- [specs/001-data-genai-platform-baseline/research.md](specs/001-data-genai-platform-baseline/research.md)
- [specs/001-data-genai-platform-baseline/data-model.md](specs/001-data-genai-platform-baseline/data-model.md)
- [specs/001-data-genai-platform-baseline/contracts/data_access.md](specs/001-data-genai-platform-baseline/contracts/data_access.md)
- [specs/001-data-genai-platform-baseline/contracts/ingestion.md](specs/001-data-genai-platform-baseline/contracts/ingestion.md)
- [specs/001-data-genai-platform-baseline/contracts/dictionary.md](specs/001-data-genai-platform-baseline/contracts/dictionary.md)
- [specs/001-data-genai-platform-baseline/quickstart.md](specs/001-data-genai-platform-baseline/quickstart.md)
5. Tareas accionables generadas y cerradas en [specs/001-data-genai-platform-baseline/tasks.md](specs/001-data-genai-platform-baseline/tasks.md).

Resultado: la plataforma ya recorrió cuatro features completas y cubre Data Engineering + AI Engineering + Semantic Governance + MLOps local reproducible.

## Estructura OpenSpec en este repo

### Carpeta .specify

- [.specify/templates](.specify/templates): templates base para spec, plan, tasks, checklist, constitution.
- [.specify/scripts/bash](.specify/scripts/bash): scripts de soporte del flujo.
  - [create-new-feature.sh](.specify/scripts/bash/create-new-feature.sh): crea feature nueva numerada.
  - [setup-plan.sh](.specify/scripts/bash/setup-plan.sh): prepara plan.md para la feature actual.
  - [setup-tasks.sh](.specify/scripts/bash/setup-tasks.sh): prepara contexto de tasks y valida prerequisitos.
  - [check-prerequisites.sh](.specify/scripts/bash/check-prerequisites.sh): validaciones previas del flujo.
- [.specify/memory](.specify/memory): memoria de gobernanza compartida (constitucion vigente).
- [.specify/workflows](.specify/workflows): configuracion de workflows del sistema.

### Carpeta specs

- [specs](specs): contiene una carpeta por feature.
- [specs/001-data-genai-platform-baseline](specs/001-data-genai-platform-baseline): feature baseline ya completada.
  - spec.md: que problema resolvemos, alcance, requisitos funcionales, criterios de exito.
  - plan.md: arquitectura, contexto tecnico, chequeo constitucional, estructura objetivo.
  - research.md: decisiones tecnicas y findings de EDA.
  - data-model.md: entidades, columnas, relaciones y tipado logico.
  - contracts/: contratos inter-modulo para data access, ingestion y dictionary.
  - quickstart.md: guia de ejecucion/validacion end-to-end.
  - tasks.md: plan de ejecucion granular por fases y por user story.

## Flujo recomendado para nuevas features

Este es el flujo que deberia repetir cualquier compañero para mantener consistencia:

1. Definir o ajustar reglas de arquitectura en la constitucion si cambia el marco.
2. Crear spec de la nueva feature con alcance claro y medible.
3. Generar plan tecnico con decisiones, estructura y chequeo de constitucion.
4. Generar tasks accionables y ordenadas por dependencias.
5. Implementar por fases, cerrando checkboxes de tasks en cada bloque.
6. Validar con tests y comandos reales de CLI.
7. Actualizar documentacion de estado y roadmap.

## Comandos utiles de soporte (scripts locales)

Los scripts de [.specify/scripts/bash](.specify/scripts/bash) permiten preparar artefactos de forma deterministica.

```bash
# Crear una nueva feature (directorio en specs/)
bash .specify/scripts/bash/create-new-feature.sh "descripcion corta de la feature"

# Preparar plan.md de la feature activa
bash .specify/scripts/bash/setup-plan.sh --json

# Validar prerequisitos y preparar contexto de tareas
bash .specify/scripts/bash/setup-tasks.sh --json
```

Nota: en este repo se trabajo principalmente desde los comandos conversacionales de Spec Kit (/speckit.constitution, /speckit.specify, /speckit.plan, /speckit.tasks, /speckit.implement), y los scripts se usaron como soporte de preparacion/verificacion.

## Como se mapea Spec Kit al codigo fuente

- Spec y contratos viven en [specs/001-data-genai-platform-baseline](specs/001-data-genai-platform-baseline).
- Implementacion vive en [src](src), ahora con `src/mlops/` como tercer dominio de ingeniería además de `data_engineering` y `ai_engineering`.
- Calidad y regresion viven en [tests](tests).
- Runtime local de BD vive en [docker/docker-compose.yml](docker/docker-compose.yml).
- Evidencias generadas:
  - [data_dictionary.md](data_dictionary.md)
  - [.artifacts/load_manifest.json](.artifacts/load_manifest.json)

Regla practica: no implementar nada que no este trazado en spec/plan/tasks, salvo fixes necesarios de estabilidad que luego se documenten.

## Convenciones acordadas en esta baseline

- Tipado estricto en Python y contratos Pydantic para cruces entre capas.
- Separacion clara de responsabilidades (data_engineering, ai_engineering, mlops, data_access, contracts, cli).
- Codigo de engine PostgreSQL aislado en adapters.
- Sin adelantar alcance de features posteriores (v1.x no introdujo Semantic Layer; v2.0 lo entregó completo).
- Pruebas de contrato para boundaries + pruebas de integracion contra PostgreSQL real en Docker.
- **v2.0 (Semantic Layer)**: `pyyaml` (única dependencia nueva en v2.0) confined a `src/data_engineering/semantic_layer/registry.py` (boundary test enforced).
- **v2.0 (Governance)**: ningún caller de `execute_readonly_query` en `src/ai_engineering/` puede importar el adapter directo; siempre debe recibir un `QueryProvider` (idealmente un `GovernedQueryProvider` wrapper) para que RLS aplique. `GovernedQueryProvider` es el single enforcement point de Principle IV.
- **v2.0 (Viewer config)**: `viewers.yaml` es local-only (gitignored); el template committed es `viewers.example.yaml`. `SEMANTIC_VIEWERS_FILE` overridea el path; `ENV` gatea `allows_full_access` (solo local/dev/test).
- **v2.0 (Determinismo)**: `semantic_layer.json` excluye `generated_at` (timestamp va solo al `.md`) para ser byte-determinista entre regeneraciones (SC-005).
- **v3.0 (MLOps)**: `scikit-learn` y `catboost` quedan confinados a `src/mlops/` por boundary tests; `src/mlops/` nunca importa `psycopg` ni internals de `data_engineering`/`ai_engineering`.
- **v3.0 (Artifact registry)**: `.artifacts/mlops/registry.json` es la fuente de verdad del tracking/promoción; cada run vive en `.artifacts/mlops/models/<model_name>/<run_id>/` con `params.json`, `metrics.json`, `data_hash.txt` y el artifact serializado.
- **v3.0 (Reproducibilidad)**: el training usa `QueryProvider` + SQL validator compartido + hash determinista del `FeatureSet`; mismas entradas producen mismas métricas.

## Como continuar con la siguiente feature (playbook de equipo)

1. Crear nueva carpeta de feature en [specs](specs) usando numeracion incremental.
2. Redactar spec con alcance minimo viable y exclusiones explicitas.
3. Ejecutar plan y documentar decisiones en research/data-model/contracts.
4. Generar tasks con criterios de done verificables.
5. Implementar en lotes pequenos y validar en cada lote.
6. Actualizar [README_STATUS.md](README_STATUS.md) con:
- estado actual
- riesgos nuevos
- decisiones tomadas
- siguientes hitos

## Checklist rapido para PRs guiadas por Spec Kit

- [ ] Existe spec.md para la feature.
- [ ] Existe plan.md y paso de constitution check.
- [ ] Existen contracts y quickstart coherentes.
- [ ] tasks.md refleja el trabajo real (checkboxes al dia).
- [ ] Tests de contrato/integracion actualizados y pasando.
- [ ] README de estado actualizado.

## Errores comunes que este repo ya resolvio

- Mismatch entre aliases de columnas del Excel y nombres internos de modelos.
- Inserciones SQL usando nombres de campo en lugar de nombres de columna reales.
- Bootstrap no idempotente (duplicados de PK en re-ejecucion).
- Fugas de dependencias de infraestructura fuera de sus adapters.

### En v2.0 (Semantic Layer):

- **`_gov` alias reservado**: el `SemanticQueryResolver` usa `_gov` como alias externo del subquery wrapping. El LLM nunca generara este alias (el `SqlValidator` de 002 lo garantiza implicitamente), pero se mantiene defensive.
- **Mismmatch `People.Region` (Eastern/Western Canada) vs `Orders.Region` (Canada)**: el resolver es conservador — un viewer scoped a `Eastern Canada` no matchea filas de `Orders` (devuelve 0 filas). La consolidacion es v3.0+ scope.
- **`Returns.Order ID` tiene 63 duplicados**: la fórmula de `returned_amount`/`net_sales` usa `EXISTS (SELECT 1 FROM Returns WHERE Returns."Order ID" = Orders."Order ID")`, NO un JOIN directo (que duplicaría filas de Orders).
- **`allows_full_access` fuera de local/dev**: el `ViewerRegistry` fuerza `allows_full_access=False` si `ENV` no esta en `{local, dev, test}` (defense-in-depth — no basta con el yaml para escapar governance en prod).

Si aparece un problema similar, revisar primero contratos, adapter PostgreSQL, loader y tests de boundary.

### En v3.0 (MLOps):

- **Boundary vs validator compartido**: `src/mlops/` no puede depender de `src.ai_engineering/`; por eso el SQL validator se extrajo a `src/data_access/sql_validator.py` y `ai_engineering/sql_validator.py` quedó como wrapper de compatibilidad.
- **SQL quoted identifiers**: la extracción de `Orders` debe usar `"Orders"` / `"Order Date"` / `"Row ID"` porque PostgreSQL creó la tabla con identifiers quoted y case-sensitive.
- **Fallback de categorías no vistas**: la señal `used_fallback_encoding` depende del vocabulario persistido junto al artifact; no alcanza con confiar en que sklearn/CatBoost no lancen excepción.
- **Registry todo-o-nada**: cualquier escritura parcial debe quedar fuera de `registry.json`; el manifiesto es el contrato visible, no solo la existencia del directorio del run.

## Referencias internas recomendadas

- [README.md](README.md)
- [README_STATUS.md](README_STATUS.md)
- [.specify/memory/constitution.md](.specify/memory/constitution.md)
- [specs/001-data-genai-platform-baseline/spec.md](specs/001-data-genai-platform-baseline/spec.md)
- [specs/001-data-genai-platform-baseline/plan.md](specs/001-data-genai-platform-baseline/plan.md)
- [specs/001-data-genai-platform-baseline/tasks.md](specs/001-data-genai-platform-baseline/tasks.md)
- [specs/001-data-genai-platform-baseline/quickstart.md](specs/001-data-genai-platform-baseline/quickstart.md)
