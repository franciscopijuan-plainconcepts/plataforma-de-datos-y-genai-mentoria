"""Agente doc -> Ossie (PoC local, no versionado).

Dado un documento de negocio (xlsx, md o txt), propone nuevas metricas para el
modelo semantico Ossie siguiendo la guia del equipo:

  documento + diccionario + Ossie actual -> la IA propone un CANDIDATO
  -> validacion con la frontera oficial (`import_ossie`, sin validacion paralela)
  -> diff + explicacion + supuestos -> aprobacion humana via PR.

Nunca sobrescribe el modelo activo: escribe `semantic/sales.ossie.candidate.yaml`.

Uso:
    uv run python propose_semantic_update.py ejemplos/notas-finanzas.xlsx
"""

from __future__ import annotations

import difflib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parent
load_dotenv(_REPO_ROOT / ".env", override=True)

_MODEL_PATH = _REPO_ROOT / "semantic" / "sales.ossie.yaml"
_CANDIDATE_PATH = _REPO_ROOT / "semantic" / "sales.ossie.candidate.yaml"
_SOURCE_XLSX = _REPO_ROOT / "Global Superstore Data.xlsx"


def _die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


# ---------- 1. leer el documento de entrada ----------

def read_input_document(path: Path) -> str:
    if not path.exists():
        _die(f"No existe el documento: {path}")
    if path.suffix.lower() in {".md", ".txt"}:
        return path.read_text(encoding="utf-8", errors="replace")
    if path.suffix.lower() == ".csv":
        import csv

        with path.open(encoding="utf-8", errors="replace", newline="") as fh:
            sample = fh.read(4096)
            fh.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",;	|")
            except csv.Error:
                dialect = csv.excel
            rows = csv.reader(fh, dialect)
            return "\n".join(
                " | ".join(c.strip() for c in r if c.strip()) for r in rows if any(r)
            )
    if path.suffix.lower() == ".xlsx":
        import openpyxl

        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
        parts: list[str] = []
        for ws in wb.worksheets:
            parts.append(f"## Hoja: {ws.title}")
            for row in ws.iter_rows(values_only=True):
                cells = [str(c).strip() for c in row if c is not None and str(c).strip()]
                if cells:
                    parts.append(" | ".join(cells))
        return "\n".join(parts)
    _die(f"Formato no soportado: {path.suffix} (usa .xlsx, .csv, .md o .txt)")
    raise AssertionError


# ---------- 2. contexto: diccionario + modelo Ossie actual ----------

def build_dictionary():
    from src.data_engineering.dictionary.generator import generate_dictionary
    from src.data_engineering.eda.explorer import explore_workbook
    from src.data_engineering.eda.schema_inferrer import infer_table_defs
    from src.data_engineering.ingestion.manifest import sha256_of_file
    from src.contracts.ingestion import SchemaInferenceResult

    tables, shared = explore_workbook(_SOURCE_XLSX)
    inference = SchemaInferenceResult(
        source_file=str(_SOURCE_XLSX),
        source_sha256=sha256_of_file(_SOURCE_XLSX),
        tables=tables,
        shared_columns=shared,
        inferred_at=datetime.now(timezone.utc),
    )
    return generate_dictionary(inference, infer_table_defs(tables))


def model_context(model_raw: dict) -> tuple[str, str, set[str]]:
    """(columnas fisicas por tabla, metricas actuales condensadas, nombres)."""
    cols_lines: list[str] = []
    for ds in model_raw.get("datasets", []):
        physical = []
        for f in ds.get("fields", []):
            expr = f["expression"]["dialects"][0]["expression"]
            physical.append(expr)
        cols_lines.append(f'- Tabla "{ds["name"]}": {", ".join(physical)}')
    met_lines: list[str] = []
    names: set[str] = set()
    for m in model_raw.get("metrics", []):
        names.add(m["name"])
        expr = m["expression"]["dialects"][0]["expression"]
        met_lines.append(f"- {m['name']}: {expr}  — {m['description']}")
    return "\n".join(cols_lines), "\n".join(met_lines), names


# ---------- 3. propuesta del LLM ----------

_PROMPT = """Eres el agente de mantenimiento de la capa semantica de la Plataforma \
de Datos y GenAI (dataset Global Superstore, warehouse PostgreSQL).

La capa semantica es un documento Apache Ossie. Tu trabajo: leer un documento de \
negocio nuevo y PROPONER (no imponer) nuevas metricas para el modelo.

COLUMNAS FISICAS DISPONIBLES (las unicas que existen):
{columns}

METRICAS YA DEFINIDAS (no las dupliques; puedes usarlas en derives_from):
{metrics}

DOCUMENTO DE NEGOCIO NUEVO:
---
{document}
---

REGLAS ESTRICTAS:
1. Solo metricas calculables con las columnas fisicas listadas. Si el documento \
pide algo que requiere columnas o tablas nuevas, NO lo propongas como metrica: \
ponlo en out_of_scope con su motivo (requiere ingestion/diccionario/contratos).
2. expression: UNA expresion agregada escalar en SQL PostgreSQL sobre la tabla \
"Orders", con las columnas fisicas entre comillas dobles (ej: SUM("Sales")). \
La tabla Returns solo via EXISTS (SELECT 1 FROM Returns WHERE Returns."Order ID" \
= Orders."Order ID"). Sin GROUP BY, sin WHERE, sin alias AS.
3. name en snake_case, distinto de las metricas existentes.
4. NO deduzcas una formula solo porque exista una columna: si hay ambiguedad de \
negocio (ej: si un coste ya esta incluido en "Profit"), declara el supuesto en \
"assumption" y la pregunta a negocio en "questions".
5. derives_from: solo nombres de metricas EXISTENTES de la lista (o [] si ninguna).
6. aggregation: SUM, AVG, COUNT_DISTINCT, RATIO o EXPRESSION. RATIO exige que \
derives_from tenga EXACTAMENTE 2 metricas existentes; ante la duda usa EXPRESSION.

Responde SOLO con JSON valido (sin markdown, sin comentarios):
{{"proposals": [{{"name": str, "description": str (ingles, estilo de las actuales),
"expression": str, "aggregation": str, "derives_from": [str], "uses_returns": bool,
"assumption": str o null, "rationale": str (castellano, maximo 2 frases),
"questions": [str] (castellano, max 1 pregunta corta)}}],
"out_of_scope": [{{"item": str, "reason": str}}]}}"""


def _client():
    from src.ai_engineering.llm_client import LlmClient
    from src.contracts.text_to_sql import LlmConfig

    config = LlmConfig.from_env().model_copy(update={"max_tokens": 16000})
    return LlmClient(config)


def ask_llm(columns: str, metrics: str, document: str, retries: int = 2) -> dict:
    client = _client()
    last_error = ""
    for attempt in range(retries + 1):
        raw = client.complete(_PROMPT.format(columns=columns, metrics=metrics, document=document))
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            text = text[4:] if text.startswith("json") else text
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError as exc:
            last_error = f"{exc}\n---\n{raw[:800]}"
            if attempt < retries:
                print(f"      (respuesta vacia o no-JSON del LLM, reintento {attempt + 1}...)")
    _die(f"El LLM no devolvio JSON valido tras {retries + 1} intentos: {last_error}")
    raise AssertionError


# ---------- 4. construir y validar el candidato ----------

def metric_block(p: dict) -> dict:
    data = {
        "aggregation": p.get("aggregation", "EXPRESSION"),
        "assumption": p.get("assumption"),
        "derives_from": p.get("derives_from", []),
        "execution_dialect": "postgres",
        "portability": "platform-profile",
        "profile_version": 1,
        "source_table": "Orders",
        "uses_returns": bool(p.get("uses_returns", False)),
    }
    return {
        "name": p["name"],
        "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": p["expression"]}]},
        "description": p["description"],
        "custom_extensions": [{
            "vendor_name": "PLATAFORMA_DATOS_GENAI",
            "data": json.dumps(data, sort_keys=True, separators=(",", ":")),
        }],
    }


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    doc_path = Path(sys.argv[1])

    from src.data_engineering.semantic_layer.ossie import (
        conversion_report,
        import_ossie,
        read_ossie_document,
        serialize_ossie_yaml,
    )

    print(f"[1/5] Leyendo documento de negocio: {doc_path.name}")
    document_text = read_input_document(doc_path)

    print("[2/5] Cargando modelo Ossie activo + diccionario del warehouse...")
    active_text = read_ossie_document(_MODEL_PATH)
    model_raw = yaml.safe_load(active_text)
    dictionary = build_dictionary()
    columns, metrics, existing = model_context(model_raw)

    print("[3/5] El agente analiza el documento y redacta su propuesta...")
    result = ask_llm(columns, metrics, document_text)
    proposals = [p for p in result.get("proposals", []) if p.get("name") not in existing]
    out_of_scope = result.get("out_of_scope", [])
    if not proposals and not out_of_scope:
        print("El agente no encontro nada que proponer.")
        return

    print(f"[4/5] Construyendo candidato con {len(proposals)} metrica(s) nueva(s)...")

    # Bucle agentico: construir -> validar con la frontera OFICIAL -> si la
    # plataforma rechaza, devolver el error al LLM para que corrija (max 2).
    candidate_text = ""
    for fix_round in range(3):
        base = yaml.safe_load(active_text)
        for p in proposals:
            base["metrics"].append(metric_block(p))
        candidate_text = yaml.safe_dump(base, sort_keys=False, allow_unicode=True, width=100)
        try:
            import_ossie(candidate_text, dictionary)
            break
        except Exception as exc:
            if fix_round == 2:
                _die(f"El candidato NO supera la validacion oficial tras 3 intentos:\n{exc}")
            print(f"      La plataforma rechazo el candidato: {exc}")
            print(f"      El agente corrige su propuesta (ronda {fix_round + 1})...")
            fix_prompt = (
                "Tus metricas propuestas fueron RECHAZADAS por el validador oficial "
                f"de la plataforma con este error:\n  {exc}\n\n"
                f"Propuestas actuales:\n{json.dumps(proposals, ensure_ascii=False)}\n\n"
                "Corrige SOLO lo necesario para superar la validacion, manteniendo la "
                "intencion de negocio. Recuerda: RATIO exige exactamente 2 metricas "
                "existentes en derives_from; ante la duda usa EXPRESSION. Responde SOLO "
                "con el JSON corregido con el mismo esquema {\"proposals\": [...], "
                "\"out_of_scope\": [...]}."
            )
            raw = _client().complete(fix_prompt)
            text = raw.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                text = text[4:] if text.startswith("json") else text
            try:
                fixed = json.loads(text.strip())
                proposals = [p for p in fixed.get("proposals", []) if p.get("name") not in existing]
                out_of_scope = fixed.get("out_of_scope", out_of_scope)
            except json.JSONDecodeError:
                print("      (correccion no-JSON, reintento con las mismas propuestas)")
    report = conversion_report(candidate_text)
    candidate_pretty = serialize_ossie_yaml(candidate_text)
    _CANDIDATE_PATH.write_text(candidate_pretty, encoding="utf-8")

    print("[5/5] Candidato validado. Resumen:\n")
    print("=" * 72)
    for p in proposals:
        print(f"METRICA PROPUESTA: {p['name']}")
        print(f"  formula    : {p['expression']}")
        print(f"  descripcion: {p['description']}")
        print(f"  por que    : {p.get('rationale', '-')}")
        if p.get("assumption"):
            print(f"  SUPUESTO   : {p['assumption']}")
        for q in p.get("questions", []):
            print(f"  PREGUNTA A NEGOCIO: {q}")
        print("-" * 72)
    for o in out_of_scope:
        print(f"FUERA DE ALCANCE: {o.get('item')}\n  motivo: {o.get('reason')}")
        print("-" * 72)

    # Diff canonico (ambos lados normalizados por la plataforma).
    active_pretty = serialize_ossie_yaml(active_text)
    diff = list(difflib.unified_diff(
        active_pretty.splitlines(), candidate_pretty.splitlines(),
        fromfile="sales.ossie.yaml (activo)", tofile="sales.ossie.candidate.yaml",
        lineterm="", n=2,
    ))
    print(f"\nDIFF ({max(len(diff) - 4, 0)} lineas):")
    for line in diff[:120]:
        print(line)

    print("\n" + "=" * 72)
    print(f"Candidato escrito en: {_CANDIDATE_PATH.relative_to(_REPO_ROOT)}")
    print(f"Hash del candidato  : {report.model_sha256[:16]}...")
    print("""
SIGUIENTES PASOS (aprobacion humana — el agente NO activa nada):
  1. Revisar supuestos y preguntas de arriba con negocio.
  2. git checkout -b semantic/propuesta-<tema>
  3. Reemplazar semantic/sales.ossie.yaml por el candidato (y actualizar
     content_version deliberadamente en la PR).
  4. uv run python -m src.cli.main generate-semantic-layer
  5. Probar resultados con asks reales y abrir la PR con esta explicacion.""")


if __name__ == "__main__":
    main()
