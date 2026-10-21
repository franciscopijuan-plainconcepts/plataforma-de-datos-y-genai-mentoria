# Migracion semantica a Apache Ossie

## Estado y referencia recuperable

La fuente activa es `semantic/sales.ossie.yaml`, tras la aprobacion explicita
registrada mas abajo. El CLI genera derivados y no carga definiciones desde
Python ni desde `.artifacts/semantic_layer.json`. La retirada de las definiciones
legacy fue autorizada por separado y se completo sin retirar contratos vigentes.

- Rama de trabajo: `feat/migrate-semantic-layer-to-apache-ossie`.
- Revision legacy: `f465e4832291422f396ef1794101c91965a1e395`.
- Blob Git de metricas: `69a64627d2fed3b64ad6aad65bb3d5c930d8f82f`.
- Blob Git de constructor: `89278202a2ec035aac428449d2f5a80f8d0c2d5f`.
- Blob Git del JSON semantico: `c5c2ce14352f6467c2073249852d1e363300f52a`.

Estos identificadores permiten recuperar codigo y modelo; el ensayo aislado
se describe al final. Los cambios locales del usuario,
sus secretos y los volumenes Docker no forman parte de esta referencia ni deben
sobrescribirse para revertir.

## Inventario inicial de productores y consumidores (antes de migrar)

| Superficie | Productor / fuente | Consumidor / contrato | Riesgo de migracion |
| --- | --- | --- | --- |
| Definiciones | `metrics.py` y `builder.py` de la revision legacy fijada, diccionario | [SemanticLayerDocument](../src/contracts/semantic_layer.py) | Ocho metricas, once dimensiones, dos asociaciones y supuestos sin perdida |
| JSON / Markdown | [renderizador](../src/data_engineering/semantic_layer/render.py), `generate-semantic-layer` | [CLI](../src/cli/main.py), documentacion y artefactos locales | Determinismo y compatibilidad de lectores |
| Contexto LLM | Documento y diccionario | [prompt](../src/ai_engineering/prompt_builder.py), [pipeline](../src/ai_engineering/pipeline.py) | Modelo opcional/fallback actual y formulas truncadas en prompt |
| Identidad | [People resolver](../src/data_engineering/semantic_layer/person_resolver.py), [registro YAML](../src/data_engineering/semantic_layer/registry.py) | Viewer e inyeccion en CLI | No convertir el modelo intercambiable en autoridad de acceso |
| Consulta | `ask` y `chart` via `_run_ask_pipeline` | [proveedor gobernado](../src/data_engineering/semantic_layer/governed_provider.py), [resolver](../src/data_engineering/semantic_layer/resolver.py), repositorio | Preservar restricciones antes de agregacion y SQL efectivo |
| Evaluacion | `cmd_evaluate`, fichero de preguntas | [evaluador](../src/ai_engineering/evaluation.py) | Recorrido separado, sin modelo/viewer/proveedor gobernado |
| Metabase integrado | Callback del pipeline | [cliente Metabase](../src/ai_engineering/metabase_client.py) | Publica el campo SQL del resultado, no necesariamente el ejecutado |
| Web / puente local | [app_web](../app_web.py), [ask_metabase](../ask_metabase.py) | Subproceso CLI y extraccion textual de `Generated SQL:` | Segundo publicador no cubierto inicialmente; no recibe SQL gobernado |
| Entrenamiento | [dataset](../src/mlops/dataset.py) | QueryProvider, OrderRow y features | No cambiar su firma ni introducir viewer interactivo accidentalmente |
| Prediccion | [inferencia](../src/mlops/inference.py), CLI NL | Contratos y registro MLOps | Mantener comportamiento; no depende directamente del documento semantico |
| Arranque BI | [script bootstrap](../scripts/metabase_bootstrap.py) | Rol PostgreSQL y estado Metabase local | No repetir setup ni ampliar permisos para las pruebas |

### Correspondencias a comprobar

| Legacy | Destino previsto |
| --- | --- |
| Tables / purpose | Datasets / description |
| Dimension / column / dimension_type | Fields / expression / dimension y extension propia |
| Metric / formula_sql / business_description | Metrics / expression / description |
| derives_from / aggregation / uses_returns / assumption | Perfil propio versionado |
| Relaciones con destino no unico | Extension explicita, no relacion estandar falsa |
| source_sha256 / semantic_source_sha256 | Procedencia separada del hash del modelo |
| Viewer / regiones / bypass | Configuracion de ejecucion, fuera del documento intercambiable |

## Baseline ejecutada (2026-10-20)

El runner de tests de VS Code no encontro tests en las rutas proporcionadas.
Se uso el entorno del repositorio y pytest directamente. Este comando registra
la baseline anterior a la retirada; el test del builder ya no existe en la rama
actual (su cobertura se conserva en `tests/unit/test_semantic_model.py`):

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/unit/test_semantic_builder.py \
  tests/unit/test_semantic_resolver.py \
  tests/unit/test_sql_validator.py \
  tests/contract/test_semantic_layer.py \
  tests/contract/test_text_to_sql.py \
  tests/unit/test_cli_chart.py \
  tests/unit/test_mlops_features.py \
  tests/unit/test_metabase_client.py \
  tests/contract/test_people_viewer_resolver.py
```

Resultado tras restaurar dependencias con `uv sync --frozen --group dev`:
**113 passed, 1 failed**. El fallo previo es
`test_is_setup_complete_false_when_setup_token_present`: su mock devuelve
setup-token para todas las peticiones, pero la implementacion intenta login
y no recibe el campo id. Levantar Metabase no afecta a ese mock.

La misma seleccion sin `test_metabase_client.py` dio **98 passed** despues
del arranque realizado por el usuario. No hubo llamadas LLM ni re-bootstrap.
La caracterizacion PostgreSQL posterior dio **17 passed**: comprueba Decimal,
enteros, alias `value`, cardinalidad, nulos, conjunto sin filas y las once
dimensiones. Sigue sin acreditar equivalencia Ossie ni aislamiento del resolver.

La regresion ampliada posterior:

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/unit tests/contract --ignore=tests/unit/test_metabase_client.py
```

Resultado: **212 passed**, con cuatro avisos de deprecacion de joblib/numpy.
Se mantiene separado el fallo Metabase previo, no se corrige incidentalmente.

## Discrepancias identificadas por inspeccion

- El proveedor gobernado devuelve filas sin el SQL reescrito; el pipeline
  guarda `generated.sql` en `QueryResult.sql`. Metabase integrado confia en
  ese campo.
- El callback de publicacion se invoca tambien cuando hay error de ejecucion.
- El evaluador solo comprueba error superior antes de comparar texto y su
  composicion usa repositorio sin el decorador gobernado.
- El adaptador elimina campos con valor None: falta caracterizar la salida
  publica ante agregados nulos antes de acordar cualquier cambio.
- El puente usado por la web publica el SQL extraido de `Generated SQL:`.
  Corregir solo el cliente Metabase integrado no corrige este recorrido.
- La reescritura actual usa regex; falta evidencia contra PostgreSQL de
  filtros OR, alias y subconsultas.

Las observaciones estaticas no se consideran pruebas de regresion ejecutadas.
El usuario aprobo incluir el puente/web el 2026-10-20, manteniendo su interfaz
visual y el modo manual SQL, que queda fuera del recorrido gobernado.

### Referencias PostgreSQL aisladas

```bash
RUN_SEMANTIC_PG_TESTS=1 .venv/bin/python -m pytest -q \
  -p no:cacheprovider tests/integration/test_semantic_reference.py
```

Las tablas son TEMP, solo visibles en su conexion, con search_path limitado a
pg_temp y pg_catalog y rollback al finalizar. No se cargan datos en public.
Las formulas metadata se serializan con identificadores entrecomillados como
exige el prompt actual; no se cambia la definicion de negocio. Los duplicados
no incrementan devoluciones, se verifican las once dimensiones y los casos de
ventas cero y conjunto vacio.

La prueba de caracterizacion `test_legacy_null_projection` verifica expresamente
que el adaptador devuelve una fila sin la clave del agregado nulo. Esta conducta
publica se mantiene en la migracion; la referencia aritmetica conserva los NULL
reales de PostgreSQL para distinguirlos de ceros. Cambiarla necesita aprobacion
adicional, no se considera una mejora implicita de Ossie.

## Frontera Ossie y evidencia inicial

- SDK, esquema, LICENSE y NOTICE fijados al SHA
  `8551d85b20eccbbe6ce2a38e51b8ba6309a3e05e`, version `0.2.0.dev0`.
- La conversion conserva SQL como `ANSI_SQL` opaco, dependiente del perfil
  PostgreSQL `PLATAFORMA_DATOS_GENAI` version 1; no promete portabilidad universal.
- Las extensiones ajenas se conservan opacas en la representacion de intercambio.
  El contrato de consumo no interpreta esas extensiones ni el contexto AI como acceso.
- El perfil conserva las asociaciones en su extension; no declara `Order ID`
  unico. Una relacion estandar que no se pueda adaptar sin perdida se rechaza.
- La validacion rechaza claves repetidas, aliases/anchors YAML, documentos mayores
  de 2 MB, profundidad mayor de 64, versiones incompatibles y referencias invalidas.
- Primera verificacion Python 3.13.14: **22 passed** en
  `tests/unit/test_ossie.py`; mypy estricto sin errores en los dos modulos nuevos.
- Pendiente obligatorio 2.1: ejecutar instalacion y pruebas en Python minimo 3.11.
  La descarga automatica fallo primero por confianza de certificados y despues,
  usando certificados del sistema, por conexion interrumpida durante la descarga.
  No se desactivo TLS, no se cambio el SHA ni se da esta comprobacion por pasada.
  El usuario indico continuar usando su entorno existente de uv (Python 3.13.14).
  Esta fue la situacion antes de acordar el entorno de aceptacion actual y
  registrar la aprobacion posterior; no se eleva el minimo ni se certifica 3.11.

### Exportacion explicita

```bash
uv run --frozen python -m src.cli.main generate-semantic-layer \
  --export-ossie /ruta/a/un/candidato-nuevo.ossie.json
```

El directorio destino debe existir. El archivo se crea exclusivamente: si ya
existe, el comando falla antes de explorar el workbook o modificar derivados.
El comando carga la fuente canonica y genera derivados; la exportacion opcional
es una copia normalizada, nunca otra fuente activa.
Imprime SHA semantico y numero de limitaciones del perfil. Las extensiones
`.yaml`/`.yml` seleccionan YAML real; los demas destinos usan JSON Ossie.

Las funciones publicas de la frontera son `export_ossie(layer, dictionary)`,
`import_ossie(text, dictionary)`, `canonicalize_ossie(text)` y
`conversion_report(text)` y `serialize_ossie_yaml(text)`; usan contratos de la
plataforma, no modelos del SDK.
El esquema se lee de recursos locales sin descargarlo en runtime.

Validacion con el entorno del usuario:

```bash
uv run --frozen python -m pytest -q -p no:cacheprovider \
  tests/unit/test_ossie.py tests/unit/test_cli_semantic_export.py \
  tests/contract/test_boundaries.py
uv run --frozen python -m mypy src/contracts/semantic_interchange.py \
  src/data_engineering/semantic_layer/ossie.py src/cli/main.py \
  --follow-imports=silent
```

Resultado inicial: **50 passed**, tipado estricto sin errores. Incluye claves primarias
verificadas, rechazo de asignaciones de acceso en el perfil y proteccion de
candidatos editados. Esta comprobacion inicial no acreditaba por si sola
aislamiento ni cambio de autoridad; ver la evidencia posterior.

## Discrepancia detectada y decision aprobada: devoluciones y region

Durante la sustitucion de regex por AST se comprobo que filtrar todas las
lecturas de `Returns` por su propia `Region` no conserva las formulas legacy:
estas deciden la devolucion exclusivamente por `Order ID` usando `EXISTS`.

Una consulta de solo lectura al almacen levantado encontro **5 lineas de Orders**
con devolucion por `Order ID` pero sin devolucion coincidente en su misma Region.
No se alteraron tablas, datos, roles ni tarjetas. Por tanto, restringir ese
`EXISTS` por `Returns.Region` cambiaria cifras de negocio de viewers reales,
aunque las fixtures iniciales de regiones consistentes pasan.

El usuario aprobo `preserve-order-exists`: filtrar las lecturas de Orders antes
de agregar, conservando el EXISTS de Returns solo cuando el AST demuestra una
igualdad obligatoria de Order ID con una lectura externa autorizada de Orders.
Se comprueban los ambitos, no solo el texto del alias. Una rama OR no constituye
vinculo obligatorio; las demas subconsultas de Returns se rechazan. Lecturas
directas y joins de Returns se restringen por su propia Region.
No se normalizaron regiones ni se cambiaron formulas.

## Recorrido gobernado y consumidores

- `PreparedQuery` captura SQL generado, SQL efectivo, viewer y hash de modelo
  disponible. Preparacion y ejecucion estan separadas y no usan `last_sql`.
- `QueryProvider` conserva su firma para People/MLOps. El pipeline interactivo
  requiere `GovernedExecutionProvider`, no un adaptador directo.
- `QueryResult.sql` coincide con el enviado al ejecutor, tambien ante fallo de
  ejecucion. Si falla la preparacion no se atribuye un SQL como ejecutado.
- El validador AST conserva SELECT/funciones permitidas y comprueba fuentes,
  columnas por ambito y aliases. No habilita CTEs, UNION, otras tablas ni fuentes
  arbitrarias. Se revalida el SQL transformado completo.
- Regiones vacias siguen devolviendo cero filas, incluidos agregados; los nulos
  del adaptador conservan su forma legacy caracterizada. No se cambia ese contrato.
- `ask`, `chart` y `evaluate` usan una composicion comun y una carga de modelo por
  operacion. `evaluate` exige viewer o bypass local explicito y no publica tarjetas.
- La evaluacion compara resultados, tipos, posiciones de columnas y multiplicidad;
  ignora aliases de presentacion y orden de filas. La igualdad SQL solo se informa
  como diagnostico. `expected_sql` permite referencias ejecutables con case
  correcto sin eliminar `expected_sql_normalized`; si falta se usa este ultimo,
  y un error de referencia cuenta como fallo.
- Solo se invoca el sink tras ejecucion exitosa. Su indisponibilidad se registra
  sin ocultar el resultado de consulta. El cliente BI tambien rechaza publicaciones
  sin viewer, sin SQL efectivo o con errores.
- `ask --json --no-metabase` produce una respuesta tipada en stdout y progreso en
  stderr; rechazo/error da estado no cero. El puente conserva su funcion publica,
  consume `query_result.sql` y no reintenta rechazos ni duplica tarjetas.
- El modo manual `ask_metabase.py --sql` sigue sin gobernanza y muestra aviso.
  La interfaz visual no se modifica.

### Limites de publicacion

Las tarjetas contienen un **snapshot regional**, no autorizacion dinamica de
usuarios BI ni revocacion posterior. No se usan tarjetas de bypass como evidencia
de aislamiento. La prueba real verifica el rol ya configurado
`metabase_readonly`, sin modificar sus privilegios ni conexiones.

## Matriz de evidencia (actualizada tras activar autoridad)

| Requisito / escenario | Evidencia |
| --- | --- |
| Estructura, perfiles, versiones, duplicados, extensiones y determinismo | `tests/unit/test_ossie.py` |
| Exportacion explicita y no sobrescritura, JSON/YAML | `tests/unit/test_cli_semantic_export.py` |
| SDK/parser confinados y contratos sin imports cruzados | `tests/contract/test_boundaries.py`, mypy |
| Fuentes/tablas/columnas/alias fuera de alcance | `tests/unit/test_sql_validator.py` |
| OR, EXISTS, scopes y correlacion obligatoria | `tests/unit/test_semantic_resolver.py`, `tests/integration/test_semantic_reference.py` |
| SQL efectivo, preparacion sin ejecucion y sin doble reescritura | `tests/unit/test_governed_pipeline.py` |
| Errores de ejecucion/validacion/referencia y SQL distinto equivalente | `tests/unit/test_governed_evaluation.py` |
| JSON CLI, puente y composicion comun de evaluate | `tests/unit/test_cli_governed_output.py` |
| Ocho metricas globales/once dimensiones, cuatro filtros, tipos y nulos | `tests/integration/test_semantic_reference.py` |
| Preservacion de ocho metricas en cada region del almacen actual | `tests/integration/test_semantic_live_readonly.py` (transaccion READ ONLY) |
| Extraccion MLOps real conserva QueryProvider, cardinalidad y Decimal | `tests/integration/test_semantic_live_readonly.py` |
| Tarjeta real, SQL persistido, reejecucion y rol BI existente | `tests/integration/test_semantic_metabase.py` |
| Candidato frente a workbook/diccionario y claves PK reales | `tests/integration/test_ossie_candidate.py` |
| Schema/LICENSE/NOTICE incluidos identicos en distribucion | `uv build --wheel` e inspeccion del wheel |
| Entorno uv/Python 3.13.14 indicado por el usuario | Instalacion, ejecucion y mypy; otros runtimes no certificados |
| Cambio de autoridad y fallo canonico sin fallback | `tests/unit/test_cli_ossie_authority.py` |
| Retirada del legado | Aprobacion independiente registrada; consumidores migrados y definiciones Python retiradas |

Comandos reproducibles con el entorno uv existente:

```bash
uv run --frozen python -m pytest -q -p no:cacheprovider tests/unit tests/contract \
  -k 'not test_is_setup_complete_false_when_setup_token_present'
RUN_SEMANTIC_PG_TESTS=1 RUN_SEMANTIC_LIVE_TESTS=1 \
  RUN_SEMANTIC_METABASE_TESTS=1 uv run --frozen python -m pytest -q \
  -p no:cacheprovider tests/integration/test_semantic_reference.py \
  tests/integration/test_semantic_live_readonly.py \
  tests/integration/test_semantic_metabase.py tests/integration/test_ossie_candidate.py \
  tests/integration/test_semantic_rollback.py
uv run --frozen python -m mypy src --follow-imports=silent
openspec validate migrate-semantic-layer-to-apache-ossie --strict
```

La prueba Metabase crea exclusivamente una tarjeta temporal con nombre UUID y
la elimina en `finally`, tanto si pasa como si falla. El servidor normaliza el
payload antiguo a MBQL por etapas; se comprueba el SQL almacenado en esa forma.
Las integraciones MLOps historicas que ejecutan bootstrap/teardown y borran
artefactos del usuario **no se ejecutan sobre este entorno compartido**. La
extraccion se comprueba con una alternativa de solo lectura, y entrenamiento,
prediccion y graficos mediante sus contratos y unidades existentes.

El candidato `semantic/sales.ossie.yaml` tiene SHA semantico
`c78b56ea1c7dfbc6cf857e52a2406261e45004b63163af521e5887912b31eb60`.
Contiene ocho metricas, once dimensiones, tres datasets y diez limitaciones
de compatibilidad (ocho metricas y dos asociaciones).
Se genero inicialmente sin sobrescribir los derivados locales del usuario.
Tras la aprobacion, es la unica fuente editable y los derivados se regeneraron.
La auditoria actual identifica el SHA canonico Ossie; no confundirlo con el hash
del documento legacy que se utilizo en la etapa previa.

### Resultado consolidado

- Seleccion completa previa a autoridad: **339 passed, 1 deselected**, sin
  omisiones en las integraciones nuevas habilitadas. El unico caso excluido fue
  el mock Metabase defectuoso de baseline, vuelto a ejecutar separadamente y
  confirmado con el mismo error de token; no se atribuye a la migracion.
- Mypy estricto: **78 archivos comprobados sin errores** (src y pruebas nuevas).
- Tras el ultimo ajuste de guardia de tipos: **84 passed** en SQL, resolver,
  pipeline gobernado y fronteras.
- OpenSpec estricto y `git diff --check`: validos.
- No se invoco al LLM para estas comprobaciones. No se modificaron datos de
  negocio, roles, esquema fisico ni tarjetas preexistentes.
- Trabajo sin commits ni push en la rama
  `feat/migrate-semantic-layer-to-apache-ossie`.

Estos resultados consolidaron la etapa previa (26/36 tareas en ese momento).
La aprobacion posterior es explicita, no se infiere del resultado de las pruebas.

## Aprobacion para activar Ossie

Tras presentar los 339 casos exitosos, tipado, OpenSpec y comprobacion BI real,
el usuario respondio `approve_ossie_authority=true`. Reitero conservar su
entorno uv existente; no se cambia de ambiente ni se eleva el minimo Python.
La aceptacion de esta entrega se ejecuta en Python 3.13.14; 3.11 no se presenta
como certificado. Se ajusta 2.1 a ese entorno de aceptacion.

Modelo aprobado: `semantic/sales.ossie.yaml`, SHA
`c78b56ea1c7dfbc6cf857e52a2406261e45004b63163af521e5887912b31eb60`.
La pareja recuperable anterior sigue siendo la revision y blobs indicados al
inicio de este documento. Esta aprobacion autoriza 6.3, **no** la retirada
legacy (7.3), commits ni push.

## Activacion y procedencia canonica

`_load_semantic_context` carga y valida la fuente antes de resolver identidad,
instanciar LLM o ejecutar SQL. Ausencia, version desconocida, perfil incompatible
o YAML invalido fallan en ask, chart, evaluate y generate-semantic-layer.
No se consulta el JSON derivado ni se invoca un constructor legacy.

La proyeccion incluye procedencia tipada: SHA semantico, revision Ossie, hashes
de origen, revision Git disponible y flag de cambios locales de codigo.
El flag considera solo rutas ejecutables/manifiestos, no los propios derivados:
regenerar no cambia su procedencia por ensuciar `.artifacts`.
Si Git no esta disponible se informa y registra revision desconocida; no se
inventa una revision limpia. Un commit con flag dirty no representa por si solo
los bytes exactos del codigo local.

El contexto se transmite a PreparedQuery, QueryResult, auditoria y derivados;
Metabase conserva hash y revision en la descripcion. Se comprobo con una copia
temporal del modelo editada, LLM sustituido por respuesta determinista y consulta
real regional: el nuevo hash coincide con el del modelo cargado y llega al
resultado cuyo SQL coincide con el capturado por el ejecutor.

Dos ejecuciones reales de `generate-semantic-layer` dieron el mismo SHA del JSON
derivado (`d58e2104d121d3a843e3f0f39554b216deebd6e6bed98273d0e6ab7942619280`)
y dejaron intactos los bytes del YAML de entrada.

## Reversion aislada y limites

`tests/integration/test_semantic_rollback.py` extrae con `git archive` solamente
src y el modelo JSON de la revision recuperable a un directorio temporal.
Un subproceso usa ese codigo y comprueba que sus ocho definiciones coinciden con
su modelo; se comparan resultados PostgreSQL temporales con el modelo Ossie
activo para cuatro alcances y se vuelve a cargar la pareja anterior con bytes
identicos. El modelo canonico se carga antes y despues del ensayo: conserva
contenido, hash y metricas.
No se cambia HEAD, main, el worktree actual, datos publicos, volumenes ni tarjetas.

Para revertir un despliegue se debe seleccionar explicitamente esa **pareja**
codigo/modelo previa, no copiar solo el JSON legacy encima del modelo Ossie.
Separar el despliegue anterior del directorio activo, comprobar sus referencias
y configurar su arranque de forma coordinada; no arrancar dos stacks sobre los
mismos puertos ni ejecutar bootstrap/teardown como mecanismo de reversion.
La pareja anterior conserva los defectos legacy documentados de regex,
evaluacion y SQL publicado: este ensayo no certifica esas rutas como corregidas.

### Inventario y aprobacion de retirada

- `builder.py` y `metrics.py`: retirados del runtime; ningun consumidor activo
  los importa. Solo el subproceso de reversion usa el metrics del src historico.
- `_SEMANTIC_SOURCE_PATH` del CLI: constante sin uso eliminada.
- Pruebas de builder, contratos y fixtures: migradas a la fuente Ossie o a
  referencias legacy fijadas/aisladas, sin copiar formulas como segunda autoridad.
- El JSON/Markdown derivado conserva consumidores y no se elimina.
- `SemanticLayerDocument`, viewers, QueryProvider y el diccionario de datos se
  conservan como contratos/funciones vigentes; no son definiciones redundantes.

Presentados estos limites, 362 pruebas y el ensayo aislado, el usuario selecciono
`approve` para `approve_legacy_retirement`. Esa aprobacion autoriza retirar
builder/metrics y la constante sin uso y adaptar sus pruebas; no autoriza commits
ni push. Los consumidores de codigo se han adaptado al modelo canonico; las
comparaciones legacy leen exclusivamente el JSON historico fijado en Git.
El import `metrics` del subproceso de reversion pertenece solo al src archivado,
no al runtime actual.

## Resultado tras activar autoridad (antes del cierre final)

- Seleccion conjunta de unidades, contratos y cinco integraciones nuevas:
  **362 passed, 1 deselected**; las integraciones PostgreSQL, almacen real,
  Metabase, candidato y reversion estuvieron habilitadas, sin skips.
- Se excluye solo el mismo mock Metabase defectuoso del baseline, no una prueba
  de aceptacion nueva. Persisten cuatro avisos previos de joblib/NumPy.
- Tras reforzar la reversion para cargar el modelo canonico real antes/despues:
  **22 passed** en autoridad y reversion.
- Mypy estricto sobre src y pruebas nuevas: **80 archivos sin errores**.
- OpenSpec estricto y whitespace comprobados; HEAD y main conservan la revision
  recuperable inicial. No se ha creado commit, push ni PR.
- Avance: **32/36**; falta la aprobacion de retirada, su implementacion y el
  cierre final de integracion/revision despues de esa retirada.

## Cierre de implementacion: 36/36

- Autoridad y retirada aprobadas por separado y registradas. Se eliminaron
  builder/metrics y la constante legacy; los contratos y los derivados siguen
  disponibles. La unica referencia ejecutable a metrics en las pruebas pertenece
  al codigo historico del subproceso aislado, no al src actual.
- Seleccion final: **370 passed, 1 deselected**, con las cinco integraciones
  nuevas habilitadas y sin skips. Incluye aritmetica independiente, comparacion
  contra el JSON historico, todas las dimensiones, aislamiento regional,
  extraccion MLOps, hash hasta ejecucion, tarjeta real y reversion.
- El unico deselected sigue siendo el mock Metabase de baseline. Reejecutado
  separadamente al cierre, conserva exactamente el error
  `Metabase login response missing 'id' (token).`; no se oculta como exito ni se
  cambia su implementacion de setup/login. Quedan cuatro avisos previos NumPy.
- Mypy estricto: **82 archivos sin errores**, incluidos helpers, pruebas nuevas
  y contratos adaptados. OpenSpec estricto y `git diff --check` validos.
- La revision final detecto y corrigio diagnosticos de SQL de metricas mal
  formado y validacion de asociaciones de compatibilidad duplicadas o con
  propiedades desconocidas. Se reprodujeron los fallos antes de corregirlos y
  quedaron cubiertos por pruebas, sin cambiar definiciones validas.
- La copia/exportacion conserva metadatos de extensiones ajenas en modelo,
  campo y metrica, tanto en JSON como en YAML.
- Regeneracion real repetida tras la retirada: JSON identico, SHA
  `d58e2104d121d3a843e3f0f39554b216deebd6e6bed98273d0e6ab7942619280`.
  YAML sin modificaciones, SHA fisico
  `f17b21e941c01a48ef142178e965f493e474fb951ac97ce353fad6ff71f189d8`.
- Revisado el alcance: sin cambios de formulas, motor, esquema fisico,
  privilegios, entrenamiento/prediccion o interfaz visual. Las limitaciones de
  portabilidad, nulos legacy y snapshot BI se mantienen explicitadas.
- Todo permanece sin commit, push ni PR en
  `feat/migrate-semantic-layer-to-apache-ossie`, sin upstream a main.
  Los cambios previos del usuario no se revierten. El usuario debe incluir
  tambien los archivos nuevos sin seguimiento al preparar su commit.

Comando conjunto utilizado al cierre:

```bash
RUN_SEMANTIC_PG_TESTS=1 RUN_SEMANTIC_LIVE_TESTS=1 \
RUN_SEMANTIC_METABASE_TESTS=1 uv run --frozen python -m pytest -q \
  -p no:cacheprovider tests/unit tests/contract \
  tests/integration/test_semantic_reference.py \
  tests/integration/test_semantic_live_readonly.py \
  tests/integration/test_semantic_metabase.py \
  tests/integration/test_ossie_candidate.py \
  tests/integration/test_semantic_rollback.py \
  -k 'not test_is_setup_complete_false_when_setup_token_present'
```
