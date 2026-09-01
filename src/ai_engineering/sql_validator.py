"""Compatibility wrapper for the shared read-only SQL validator."""

from __future__ import annotations

from src.data_access.sql_validator import validate_sql

__all__ = ["validate_sql"]
