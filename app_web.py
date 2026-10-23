"""Web de consultas en lenguaje natural (demo local, no versionado).

Interfaz web sobre el pipeline txt2sql del repo + el puente a Metabase:
pregunta en castellano -> SQL validado -> resultados -> grafica en Metabase.

Lanzar con:
    uv run --with streamlit streamlit run app_web.py
Abre http://localhost:8501
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

import psycopg
import streamlit as st
from dotenv import load_dotenv

from ask_metabase import (
    METABASE_URL,
    add_to_dashboard,
    ask_pipeline_for_sql,
    create_metabase_question,
    guess_display,
    resolve_database_id,
)

load_dotenv(".env")

ACCENT = "#2F69FF"  # azul Plain Concepts (extraido del propio logo)

st.set_page_config(
    page_title="Plataforma de Datos y GenAI",
    page_icon=":material/analytics:",
    layout="wide",
)

# ---------- estilos ----------

st.markdown(
    """
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&family=Inter:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap');

:root {
  --ink: #F5F6F8;
  --muted: #9AA1AB;
  --bg: #0B0D10;
  --panel: #14171C;
  --line: #242933;
  --accent: #2F69FF;
}

html, body, [data-testid="stAppViewContainer"] {
  background: var(--bg);
  font-family: 'Inter', -apple-system, 'Segoe UI', sans-serif;
  color: var(--ink);
}

/* limpiar el chrome de Streamlit */
#MainMenu, footer, [data-testid="stToolbar"], [data-testid="stDecoration"] { display: none; }
[data-testid="stHeader"] { background: transparent; }

/* sidebar */
[data-testid="stSidebar"] {
  background: var(--panel);
  border-right: 1px solid var(--line);
}
[data-testid="stSidebar"] * { font-family: 'Inter', sans-serif; }

/* titulos */
h1 {
  font-family: 'Space Grotesk', 'Inter', sans-serif !important;
  font-weight: 700 !important;
  letter-spacing: -0.02em !important;
}
h2, h3 { font-family: 'Space Grotesk', 'Inter', sans-serif !important; letter-spacing: -0.01em !important; }

.eyebrow {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 0.72rem;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--muted);
  margin-bottom: 0.35rem;
}

.section-label {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 0.72rem;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--muted);
  border-top: 1px solid var(--line);
  padding-top: 1.1rem;
  margin: 1.6rem 0 0.6rem 0;
}

/* input principal */
[data-testid="stTextInput"] input {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 10px;
  color: var(--ink);
  font-size: 1rem;
  padding: 0.7rem 0.9rem;
  transition: border-color 160ms ease, box-shadow 160ms ease;
}
[data-testid="stTextInput"] input:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px rgba(47, 105, 255, 0.22);
}

/* boton primario */
.stButton > button {
  background: var(--accent);
  color: #FFFFFF;
  border: none;
  border-radius: 10px;
  font-family: 'Inter', sans-serif;
  font-weight: 600;
  padding: 0.6rem 1.6rem;
  transition: transform 140ms ease, filter 140ms ease;
}
.stButton > button:hover { filter: brightness(1.12); transform: translateY(-1px); }
.stButton > button:active { transform: translateY(0); }

/* codigo SQL */
code, pre, [data-testid="stCode"] * { font-family: 'IBM Plex Mono', monospace !important; }

/* tracker del pipeline */
.pipeline { display: flex; align-items: center; gap: 0; margin: 1.4rem 0 0.4rem 0; }
.stage { display: flex; align-items: center; gap: 0.55rem; }
.stage .dot {
  width: 10px; height: 10px; border-radius: 50%;
  background: var(--line);
  transition: background 240ms ease;
}
.stage.active .dot { background: var(--accent); animation: pulse 1.1s ease-in-out infinite; }
.stage.done .dot { background: var(--ink); }
.stage .lbl {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 0.74rem; letter-spacing: 0.08em; text-transform: uppercase;
  color: var(--muted); white-space: nowrap;
}
.stage.active .lbl { color: var(--ink); }
.stage.done .lbl { color: var(--muted); }
.connector { flex: 0 0 56px; height: 1px; background: var(--line); margin: 0 0.8rem; }
.connector.done { background: var(--ink); }

@keyframes pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(47, 105, 255, 0.45); }
  50% { box-shadow: 0 0 0 7px rgba(47, 105, 255, 0); }
}

/* aparicion de resultados */
.fade-block { animation: rise 380ms ease-out both; }
@keyframes rise {
  from { opacity: 0; transform: translateY(10px); }
  to { opacity: 1; transform: none; }
}
@media (prefers-reduced-motion: reduce) {
  .fade-block, .stage.active .dot { animation: none; }
}

/* cabecera y pie */
.brand-row { display: flex; align-items: center; justify-content: space-between; margin-bottom: 2.2rem; }
.brand-row img { height: 34px; }
.credits {
  border-top: 1px solid var(--line);
  margin-top: 3rem; padding-top: 1rem;
  font-size: 0.78rem; color: var(--muted);
  display: flex; justify-content: space-between; align-items: center; gap: 1rem;
  flex-wrap: wrap;
}
.credits b { color: var(--ink); font-weight: 600; }
</style>
""",
    unsafe_allow_html=True,
)

# ---------- cabecera ----------

_logo_file = Path(__file__).with_name("plain_logo.svg")
_logo_b64 = base64.b64encode(_logo_file.read_bytes()).decode() if _logo_file.exists() else ""
_logo_img = (
    f'<img src="data:image/svg+xml;base64,{_logo_b64}" alt="Plain Concepts">'
    if _logo_b64
    else "<span></span>"
)

st.markdown(
    f"""
<div class="brand-row">
  <div>
    <div class="eyebrow">Mentorship Program &mdash; From GenAI Blog to GenAI Use Case</div>
    <h1 style="margin:0; font-size:2.1rem;">Plataforma de Datos y GenAI</h1>
  </div>
  {_logo_img}
</div>
""",
    unsafe_allow_html=True,
)

st.markdown(
    "Formula una pregunta sobre las ventas de Global Superstore. "
    "El modelo genera la consulta SQL, la plataforma la valida y la ejecuta "
    "sobre el warehouse, y la visualización se publica en Metabase."
)

# ---------- sidebar ----------

with st.sidebar:
    st.markdown('<div class="eyebrow" style="margin-top:0.6rem;">Gobierno de datos</div>', unsafe_allow_html=True)
    viewer = st.text_input(
        "Usuario",
        value="marilene_rousseau",
        help="Persona de la tabla People. Sus regiones asignadas delimitan los datos que puede consultar.",
    )
    st.markdown('<div class="eyebrow" style="margin-top:1.4rem;">Análisis</div>', unsafe_allow_html=True)
    explain = st.checkbox(
        "Explicar los resultados",
        value=True,
        help="Tras ejecutar la consulta, el modelo redacta una lectura de negocio de los resultados.",
    )
    st.markdown('<div class="eyebrow" style="margin-top:1.4rem;">Publicación</div>', unsafe_allow_html=True)
    publish = st.checkbox("Publicar en Metabase", value=True)
    dashboard = st.text_input(
        "Dashboard de destino",
        value="",
        placeholder="Opcional",
        help="Si se indica un nombre, cada consulta añade su gráfica a ese dashboard (se crea si no existe).",
    )
    st.markdown(
        f'<div style="margin-top:1.2rem; font-size:0.8rem;"><a href="{METABASE_URL}" '
        f'style="color:{ACCENT}; text-decoration:none;">Abrir Metabase</a></div>',
        unsafe_allow_html=True,
    )

# ---------- pregunta ----------

question = st.text_input(
    "Tu pregunta",
    placeholder="Ejemplo: evolución de las ventas mes a mes",
)
run = st.button("Consultar")

# ---------- explicacion de resultados ----------


def explain_results(question_text: str, sql_text: str, columns_out, rows_out, viewer_id: str) -> str:
    """Segunda llamada al LLM: lectura de negocio de los resultados en castellano."""
    from src.ai_engineering.llm_client import LlmClient
    from src.contracts.text_to_sql import LlmConfig

    max_rows = 40
    sample = [
        {c: str(v) for c, v in zip(columns_out, r)}
        for r in rows_out[:max_rows]
    ]
    extra = f" (mostrando {max_rows} de {len(rows_out)} filas)" if len(rows_out) > max_rows else ""
    prompt = (
        "Eres un analista de datos de la Plataforma de Datos y GenAI "
        "(dataset Global Superstore de ventas minoristas).\n"
        f"El usuario '{viewer_id}' (con Row-Level Security: solo ve los datos de sus regiones) "
        f"ha preguntado: \"{question_text}\"\n"
        f"SQL ejecutado: {sql_text}\n"
        f"Resultados{extra}: {sample}\n\n"
        "Redacta en castellano una lectura de negocio de estos resultados en 3-5 frases: "
        "qué se observa, tendencias o valores llamativos (picos, caídas, negativos) y una "
        "conclusión útil. Cita las cifras redondeadas de forma legible. No inventes datos "
        "que no estén en los resultados ni menciones el SQL. Responde solo con el texto."
    )
    client = LlmClient(LlmConfig.from_env())
    return client.complete(prompt)


# ---------- tracker ----------

_STAGES = ["Generar SQL", "Ejecutar", "Publicar"]


def _tracker(current: int) -> str:
    """current: 1-based etapa activa; > len(_STAGES) = todo completado."""
    parts: list[str] = ['<div class="pipeline">']
    for i, name in enumerate(_STAGES, start=1):
        state = "done" if i < current else ("active" if i == current else "")
        parts.append(f'<div class="stage {state}"><span class="dot"></span><span class="lbl">{name}</span></div>')
        if i < len(_STAGES):
            parts.append(f'<div class="connector {"done" if i < current else ""}"></div>')
    parts.append("</div>")
    return "".join(parts)


tracker_slot = st.empty()

# ---------- ejecucion ----------

if run and question.strip():
    tracker_slot.markdown(_tracker(1), unsafe_allow_html=True)
    try:
        sql = ask_pipeline_for_sql(question.strip(), viewer.strip())
    except SystemExit:
        tracker_slot.empty()
        st.error(
            "La consulta no se pudo generar o no superó la validación. "
            "Reformula la pregunta: la plataforma solo acepta consultas de "
            "lectura (SELECT) sobre las tablas de ventas (Orders) y de "
            "previsión de ventas (Predictions)."
        )
        st.stop()

    st.markdown('<div class="fade-block">', unsafe_allow_html=True)
    st.markdown('<div class="section-label">Consulta SQL validada</div>', unsafe_allow_html=True)
    st.code(sql, language="sql")
    st.markdown("</div>", unsafe_allow_html=True)

    tracker_slot.markdown(_tracker(2), unsafe_allow_html=True)
    conn_info = (
        f"host={os.environ.get('POSTGRES_HOST', 'localhost')} "
        f"port={os.environ.get('POSTGRES_PORT', '5432')} "
        f"dbname={os.environ.get('POSTGRES_DB', 'global_superstore')} "
        f"user={os.environ.get('POSTGRES_USER', 'plataforma')} "
        f"password={os.environ.get('POSTGRES_PASSWORD', 'plataforma_dev')}"
    )
    with psycopg.connect(conn_info) as conn, conn.cursor() as cur:
        cur.execute(sql)
        columns = [d.name for d in cur.description or []]
        rows = cur.fetchall()

    st.markdown('<div class="fade-block">', unsafe_allow_html=True)
    st.markdown(
        f'<div class="section-label">Resultados &middot; {len(rows)} filas</div>',
        unsafe_allow_html=True,
    )
    st.dataframe([dict(zip(columns, r)) for r in rows], width="stretch")

    if len(columns) >= 2 and rows:
        import pandas as pd

        chart_df = pd.DataFrame(rows, columns=columns).set_index(columns[0])
        # Postgres devuelve NUMERIC como Decimal; convertir a float para que
        # el eje sea cuantitativo y no categorico.
        chart_df = chart_df.apply(pd.to_numeric, errors="coerce").dropna(axis=1, how="all")
        if not chart_df.empty and len(chart_df.columns) >= 1:
            st.markdown('<div class="section-label">Vista rápida</div>', unsafe_allow_html=True)
            colors = ACCENT if len(chart_df.columns) == 1 else None
            if guess_display(sql) == "line":
                st.line_chart(chart_df, color=colors)
            else:
                st.bar_chart(chart_df, color=colors)
    st.markdown("</div>", unsafe_allow_html=True)

    if explain and rows:
        with st.spinner("El modelo está analizando los resultados..."):
            try:
                lectura = explain_results(question.strip(), sql, columns, rows, viewer.strip())
            except Exception:
                lectura = ""
        if lectura:
            st.markdown('<div class="fade-block">', unsafe_allow_html=True)
            st.markdown('<div class="section-label">Lectura del modelo</div>', unsafe_allow_html=True)
            st.markdown(lectura)
            st.markdown("</div>", unsafe_allow_html=True)
        else:
            st.caption("No se pudo generar la explicación esta vez (la consulta y los datos no se ven afectados).")

    if publish:
        tracker_slot.markdown(_tracker(3), unsafe_allow_html=True)
        card_id = create_metabase_question(
            question.strip().capitalize(),
            sql,
            resolve_database_id(),
            guess_display(sql),
        )
        url = f"{METABASE_URL}/question/{card_id}"
        if dashboard.strip():
            dash_id = add_to_dashboard(dashboard.strip(), card_id)
            st.markdown(
                f'<div class="fade-block" style="margin-top:0.8rem;">Gráfica publicada: '
                f'<a href="{url}" style="color:{ACCENT};">abrir en Metabase</a> &middot; '
                f'añadida al dashboard <a href="{METABASE_URL}/dashboard/{dash_id}" '
                f'style="color:{ACCENT};">{dashboard}</a></div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f'<div class="fade-block" style="margin-top:0.8rem;">Gráfica publicada: '
                f'<a href="{url}" style="color:{ACCENT};">abrir en Metabase</a></div>',
                unsafe_allow_html=True,
            )

    tracker_slot.markdown(_tracker(len(_STAGES) + 1), unsafe_allow_html=True)

# ---------- pie ----------

st.markdown(
    """
<div class="credits">
  <span>Mentorship Program &mdash; <b>From GenAI Blog to GenAI Use Case</b></span>
  <span>Dirección &middot; <b>Tomás Morales</b> &nbsp;&nbsp; Desarrollo &middot; <b>Alan Teixidó Sararols</b></span>
</div>
""",
    unsafe_allow_html=True,
)
