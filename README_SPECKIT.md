# Spec Kit en Este Repositorio

Guia de continuidad para el equipo sobre como usamos Spec Kit en este proyecto, que se genero, donde vive cada artefacto y como seguir trabajando con el mismo flujo.

## Objetivo de esta guia

- Estandarizar como levantar nuevas funcionalidades con Spec Kit.
- Explicar la estructura real de carpetas en este repo.
- Evitar que el equipo salte pasos clave (constitucion, spec, plan, tasks).
- Dejar trazabilidad clara entre decisiones, implementacion y validacion.

## Resumen de lo que ya hicimos con Spec Kit

Se ejecuto el flujo completo para la feature baseline de la plataforma de datos:

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

Resultado: baseline v0 implementado con warehouse local PostgreSQL + data dictionary + CLI + contratos tipados + tests de contrato e integracion.

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
- Implementacion vive en [src](src).
- Calidad y regresion viven en [tests](tests).
- Runtime local de BD vive en [docker/docker-compose.yml](docker/docker-compose.yml).
- Evidencias generadas:
  - [data_dictionary.md](data_dictionary.md)
  - [.artifacts/load_manifest.json](.artifacts/load_manifest.json)

Regla practica: no implementar nada que no este trazado en spec/plan/tasks, salvo fixes necesarios de estabilidad que luego se documenten.

## Convenciones acordadas en esta baseline

- Tipado estricto en Python y contratos Pydantic para cruces entre capas.
- Separacion clara de responsabilidades (data_engineering, data_access, contracts, cli).
- Codigo de engine PostgreSQL aislado en adapters.
- Sin adelantar alcance de v1.x o v2.0 dentro de baseline.
- Pruebas de contrato para boundaries + pruebas de integracion contra PostgreSQL real en Docker.

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

Si aparece un problema similar, revisar primero contratos, adapter PostgreSQL, loader y tests de boundary.

## Referencias internas recomendadas

- [README.md](README.md)
- [README_STATUS.md](README_STATUS.md)
- [.specify/memory/constitution.md](.specify/memory/constitution.md)
- [specs/001-data-genai-platform-baseline/spec.md](specs/001-data-genai-platform-baseline/spec.md)
- [specs/001-data-genai-platform-baseline/plan.md](specs/001-data-genai-platform-baseline/plan.md)
- [specs/001-data-genai-platform-baseline/tasks.md](specs/001-data-genai-platform-baseline/tasks.md)
- [specs/001-data-genai-platform-baseline/quickstart.md](specs/001-data-genai-platform-baseline/quickstart.md)
