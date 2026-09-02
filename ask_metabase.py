"""Puente Text-to-SQL -> Metabase (demo local, no versionado).

Flujo "usuario de negocio": pregunta en lenguaje natural -> el pipeline `ask`
del repo (LLM de Forge + capa semantica + RLS) genera y valida el SQL -> este
script crea una "question" en Metabase con ese SQL y devuelve la URL para verla.

Uso:
    # Flujo completo (requiere FORGE_API_KEY en .env):
    uv run python ask_metabase.py "ventas totales por categoria" --viewer marilene_rousseau

    # Solo el tramo SQL -> Metabase (para probar sin la API key de Forge):
    uv run python ask_metabase.py --sql "SELECT \"Category\", SUM(\"Sales\") FROM \"Orders\" GROUP BY 1" --name "Prueba puente"

Config en .env (junto a las FORGE_*):
    METABASE_URL=http://localhost:3000        (opcional, este es el default)
    METABASE_API_KEY=mb_...                   (obligatoria: Admin -> Claves de API)
    METABASE_DB_NAME=Global Superstore        (opcional: display name de la BD en Metabase)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parent
load_dotenv(_REPO_ROOT / ".env")

METABASE_URL = os.environ.get("METABASE_URL", "http://localhost:3000").rstrip("/")
METABASE_API_KEY = os.environ.get("METABASE_API_KEY", "")
METABASE_DB_NAME = os.environ.get("METABASE_DB_NAME", "Global Superstore")


def _die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def _mb_request(method: str, path: str, payload: dict | None = None) -> dict | list:
    """Peticion autenticada a la API REST de Metabase.

    La base interna (H2) de Metabase puede tener el contador de IDs
    desincronizado tras un apagado brusco: cada INSERT choca con un ID ya
    ocupado y devuelve 500 "primary key violation". Cada intento avanza el
    contador en 1, asi que reintentamos hasta superar el hueco de IDs usados.
    """
    for attempt in range(60):
        req = urllib.request.Request(
            f"{METABASE_URL}{path}",
            method=method,
            headers={"x-api-key": METABASE_API_KEY, "Content-Type": "application/json"},
            data=json.dumps(payload).encode() if payload is not None else None,
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode()
            if exc.code == 500 and "primary key violation" in body.lower():
                print(f"      (ID interno ocupado, reintento {attempt + 1}...)")
                continue
            _die(f"Metabase respondio {exc.code} en {path}: {body[:300]}")
        except urllib.error.URLError as exc:
            _die(f"No se pudo conectar a Metabase en {METABASE_URL}: {exc.reason}")
    _die("Agotados los reintentos contra la base interna de Metabase.")
    raise AssertionError  # unreachable


def resolve_database_id() -> int:
    """Busca el id de la BD conectada en Metabase por display name (o dbname)."""
    raw = _mb_request("GET", "/api/database")
    databases = raw.get("data", raw) if isinstance(raw, dict) else raw
    for db in databases:
        if db.get("name", "").strip().lower() == METABASE_DB_NAME.strip().lower():
            return int(db["id"])
    for db in databases:  # fallback: por nombre fisico de la BD postgres
        if db.get("details", {}).get("dbname") == "global_superstore":
            return int(db["id"])
    _die(
        f"No encontre la base {METABASE_DB_NAME!r} en Metabase. "
        f"Bases visibles: {[d.get('name') for d in databases]}"
    )
    raise AssertionError


def ask_pipeline_for_sql(question: str, viewer: str, _retries: int = 1) -> str:
    """Ejecuta el `ask` de Paco (LLM + validacion + RLS) y extrae el SQL generado.

    El LLM a veces devuelve SQL vacio de forma puntual; en ese caso (y solo en
    ese) se reintenta una vez antes de rendirse.
    """
    cmd = [sys.executable, "-m", "src.cli.main", "ask", question, "--viewer", viewer]
    result = subprocess.run(
        cmd, cwd=_REPO_ROOT, capture_output=True, text=True, timeout=180
    )
    output = result.stdout + result.stderr

    # SQL vacio o rechazado: fallo transitorio tipico del LLM -> reintentar.
    empty_sql = bool(re.search(r"Generated SQL:\s*\nValidation:", result.stdout))
    rejected = "Validation: REJECTED" in result.stdout
    if (empty_sql or rejected or result.returncode != 0) and _retries > 0:
        print("      (SQL vacio o rechazado, reintentando la generacion...)")
        return ask_pipeline_for_sql(question, viewer, _retries=_retries - 1)

    if result.returncode != 0:
        _die(f"El pipeline `ask` fallo:\n{output}")
    if rejected:
        reason = re.search(r"Reason: (.+)", result.stdout)
        _die(f"El validador rechazo el SQL: {reason.group(1) if reason else output}")

    # El SQL puede venir en varias lineas: capturar desde "Generated SQL: "
    # hasta la linea "Validation:" (re.S para que . cruce saltos de linea).
    match = re.search(r"Generated SQL: (.+?)\nValidation:", result.stdout, re.S)
    if not match:
        _die(f"No encontre 'Generated SQL:' en la salida del pipeline:\n{output}")
    if "Validation: ACCEPTED" not in result.stdout:
        _die(f"El validador rechazo el SQL generado:\n{output}")
    return " ".join(match.group(1).split()).strip()


def guess_display(sql: str) -> str:
    """Heuristica simple de visualizacion segun la forma del SQL."""
    lowered = sql.lower()
    if "date_trunc" in lowered or "order date" in lowered:
        return "line"
    if "group by" in lowered:
        return "bar"
    return "table"


def create_metabase_question(name: str, sql: str, database_id: int, display: str) -> int:
    card = _mb_request(
        "POST",
        "/api/card",
        {
            "name": name,
            "type": "question",
            "display": display,
            "visualization_settings": {},
            "dataset_query": {
                "type": "native",
                "database": database_id,
                "native": {"query": sql, "template-tags": {}},
            },
        },
    )
    return int(card["id"])  # type: ignore[index]


def add_to_dashboard(dashboard_name: str, card_id: int) -> int:
    """Busca (o crea) un dashboard por nombre y le añade la question al final."""
    listing = _mb_request("GET", "/api/dashboard")
    dashboards = listing.get("data", listing) if isinstance(listing, dict) else listing
    dash_id: int | None = None
    for d in dashboards:
        if d.get("name", "").strip().lower() == dashboard_name.strip().lower():
            dash_id = int(d["id"])
            break
    if dash_id is None:
        created = _mb_request("POST", "/api/dashboard", {"name": dashboard_name})
        dash_id = int(created["id"])  # type: ignore[index]

    detail = _mb_request("GET", f"/api/dashboard/{dash_id}")
    dashcards = detail.get("dashcards", []) if isinstance(detail, dict) else []
    next_row = max((dc["row"] + dc["size_y"] for dc in dashcards), default=0)
    slim = [
        {
            "id": dc["id"],
            "card_id": dc.get("card_id"),
            "row": dc["row"],
            "col": dc["col"],
            "size_x": dc["size_x"],
            "size_y": dc["size_y"],
            "series": dc.get("series", []),
            "parameter_mappings": dc.get("parameter_mappings", []),
            "visualization_settings": dc.get("visualization_settings", {}),
        }
        for dc in dashcards
    ]
    slim.append(
        {
            "id": -1,
            "card_id": card_id,
            "row": next_row,
            "col": 0,
            "size_x": 12,
            "size_y": 8,
            "series": [],
            "parameter_mappings": [],
            "visualization_settings": {},
        }
    )
    _mb_request("PUT", f"/api/dashboard/{dash_id}", {"dashcards": slim})
    return dash_id


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question", nargs="?", help="Pregunta en lenguaje natural")
    parser.add_argument("--viewer", help="Persona de la tabla People (RLS), p.ej. marilene_rousseau")
    parser.add_argument("--sql", help="Saltarse el LLM y publicar este SQL directamente")
    parser.add_argument("--name", help="Nombre de la question en Metabase")
    parser.add_argument("--display", choices=["table", "bar", "line"], help="Tipo de grafica")
    parser.add_argument("--dashboard", help="Añadir la question a este dashboard (se crea si no existe)")
    args = parser.parse_args()

    if not METABASE_API_KEY:
        _die("Falta METABASE_API_KEY en .env (crearla en Metabase: Admin -> Claves de API).")

    if args.sql:
        sql, name = args.sql, args.name or "Question generada por el puente"
    elif args.question:
        if not args.viewer:
            _die("El pipeline exige gobierno: pasa --viewer <persona> (p.ej. marilene_rousseau).")
        print(f"[1/3] Preguntando al LLM: {args.question!r} (viewer={args.viewer})...")
        sql = ask_pipeline_for_sql(args.question, args.viewer)
        name = args.name or args.question.capitalize()
        print(f"      SQL generado y validado:\n      {sql}")
    else:
        parser.print_help()
        sys.exit(1)

    print("[2/3] Creando la question en Metabase...")
    database_id = resolve_database_id()
    display = args.display or guess_display(sql)
    card_id = create_metabase_question(name, sql, database_id, display)

    url = f"{METABASE_URL}/question/{card_id}"
    print(f"[3/3] Listo — grafica '{display}' creada: {url}")

    if args.dashboard:
        dash_id = add_to_dashboard(args.dashboard, card_id)
        print(f"      Añadida al dashboard '{args.dashboard}': {METABASE_URL}/dashboard/{dash_id}")


if __name__ == "__main__":
    main()
