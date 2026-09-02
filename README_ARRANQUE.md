# Cómo levantar todo esto (chuleta rápida)

Guía de andar por casa para arrancar la plataforma entera en mi máquina:
warehouse + pipeline txt2sql + Metabase + la web de consultas.

## Requisitos (ya instalados, solo la primera vez)

- Docker Desktop
- [uv](https://docs.astral.sh/uv/) (gestiona Python y dependencias)
- Un `.env` en la raíz del repo con `FORGE_API_KEY` y `METABASE_API_KEY`
  (copiar de `.env.example` y rellenar)

## Arranque del día a día

**1. Abrir Docker Desktop** y esperar a que esté verde.

**2. Levantar los contenedores** (Postgres con los datos + Metabase):

```bash
docker start plataforma_postgres metabase
```

**3. (Opcional) comprobar que el warehouse está sano:**

```bash
uv run python -m src.cli.main validate
```

Tiene que acabar en `VALIDATION PASSED`.

**4. Arrancar la web** (se queda corriendo en esa terminal):

```bash
uv run --with streamlit streamlit run app_web.py
```

Y ya está todo:

| Qué | Dónde |
|---|---|
| Web de consultas | http://localhost:8501 |
| Metabase | http://localhost:3000 (darle ~1 min tras arrancar) |

> Si llevas días sin tocar el proyecto, un `git pull` antes de arrancar,
> que Diego y Paco suben cosas.

## Probar que funciona

En la web, preguntar por ejemplo:

- `ventas netas por categoría` ← la buena: usa las definiciones de negocio
  (join con devoluciones vía EXISTS)
- `evolución de las ventas mes a mes`
- `países donde perdemos dinero`
- `número de pedidos por año` ← se ve el `COUNT(DISTINCT "Order ID")` de la
  capa semántica

O por CLI sin la web:

```bash
uv run python -m src.cli.main ask --viewer marilene_rousseau "ventas por categoria"
```

El `--viewer` es una persona real de la tabla People: sus regiones limitan
lo que ve (RLS). Cambiando de usuario cambian los números.

## Apagarlo todo

Ctrl+C en la terminal de Streamlit y:

```bash
docker stop plataforma_postgres metabase
```

## Si algo se tuerce

**El puerto 8501 está ocupado** (proceso huérfano de otra sesión):

```bash
powershell -Command "Get-NetTCPConnection -LocalPort 8501 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }"
```

**La web dice que no encuentra la base / Metabase no responde**: casi
siempre es que Docker Desktop no está arrancado o los contenedores están
parados → volver al paso 1.

**El LLM devuelve SQL vacío alguna vez**: la web ya reintenta sola una vez;
si aun así falla, relanzar la consulta y listo.
