"""Ingestion pipeline — Excel → typed contracts → data-access layer.

May import `pandas` / `openpyxl`. Converts DataFrame → validated Pydantic
models at the boundary; the DataFrame NEVER escapes this module.
"""
