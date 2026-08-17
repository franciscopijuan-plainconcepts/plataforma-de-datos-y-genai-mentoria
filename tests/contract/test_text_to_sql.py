"""Contract tests for Text-to-SQL models (constitution-mandated gate).

Asserts:
- All models in `src/contracts/text_to_sql.py` are Pydantic v2 with explicit types.
- `QueryRow` accepts dynamic data.
- `TextToSqlResponse` state transitions are valid.
- `LlmConfig.from_env()` fails fast on missing key (FR-013).
- `QueryProvider` Protocol includes `execute_readonly_query`.
- Transparency: `TextToSqlResponse` always includes SQL even when rejected (FR-008).

Reference: specs/002-text-to-sql-v1/contracts/text_to_sql.md
            specs/002-text-to-sql-v1/tasks.md T013, T016
"""

from __future__ import annotations

import os

import pytest
from pydantic import BaseModel

from src.contracts.text_to_sql import (
    GeneratedSql,
    LlmConfig,
    NLQuestion,
    QueryResult,
    QueryRow,
    SampleQuestion,
    TextToSqlResponse,
    ValidationResult,
)
from src.data_access.interfaces import QueryProvider


# --- All models are Pydantic v2 ---

@pytest.mark.parametrize(
    "model_cls",
    [
        LlmConfig, NLQuestion, GeneratedSql, ValidationResult, QueryRow,
        QueryResult, TextToSqlResponse, SampleQuestion,
    ],
)
def test_models_are_pydantic_v2(model_cls: type) -> None:
    assert issubclass(model_cls, BaseModel), f"{model_cls.__name__} must be a Pydantic BaseModel"


# --- LlmConfig ---

def test_llm_config_from_env_fails_fast_on_missing_key() -> None:
    """FR-013: missing FORGE_API_KEY raises a clear error."""
    original = os.environ.pop("FORGE_API_KEY", None)
    try:
        with pytest.raises(ValueError, match="FORGE_API_KEY"):
            LlmConfig.from_env()
    finally:
        if original is not None:
            os.environ["FORGE_API_KEY"] = original


def test_llm_config_from_env_succeeds_with_key() -> None:
    original = os.environ.get("FORGE_API_KEY")
    os.environ["FORGE_API_KEY"] = "test-key"
    try:
        config = LlmConfig.from_env()
        assert config.api_key == "test-key"
    finally:
        if original is not None:
            os.environ["FORGE_API_KEY"] = original
        else:
            os.environ.pop("FORGE_API_KEY", None)


# --- QueryRow ---

def test_query_row_accepts_dynamic_data() -> None:
    row = QueryRow(data={"region": "North", "total": 1234.56})
    assert row.data["region"] == "North"
    assert row.data["total"] == 1234.56


# --- TextToSqlResponse transparency (FR-008 / US2) ---

def test_response_includes_sql_when_rejected() -> None:
    """FR-008: even rejected SQL is returned so the user can see what was generated."""
    response = TextToSqlResponse(
        question=NLQuestion(text="test"),
        generated_sql=GeneratedSql(sql="DROP TABLE Orders", model_name="test", raw_response={}),
        validation=ValidationResult(accepted=False, reason="contains forbidden keyword: DROP", sql="DROP TABLE Orders"),
        query_result=None,
    )
    assert response.generated_sql.sql == "DROP TABLE Orders"
    assert response.validation.sql == "DROP TABLE Orders"
    assert not response.validation.accepted
    assert response.validation.reason is not None
    assert response.query_result is None


def test_response_includes_sql_when_accepted() -> None:
    """FR-008: successful response includes both SQL and result."""
    response = TextToSqlResponse(
        question=NLQuestion(text="test"),
        generated_sql=GeneratedSql(sql="SELECT 1", model_name="test", raw_response={}),
        validation=ValidationResult(accepted=True, reason=None, sql="SELECT 1"),
        query_result=QueryResult(
            sql="SELECT 1",
            rows=[QueryRow(data={"count": 1})],
            row_count=1,
            latency_ms=5,
            error=None,
        ),
    )
    assert response.generated_sql.sql == "SELECT 1"
    assert response.query_result is not None
    assert response.query_result.sql == "SELECT 1"


def test_response_error_path() -> None:
    """When the pipeline fails (LLM unreachable), error is set and query_result is None."""
    response = TextToSqlResponse(
        question=NLQuestion(text="test"),
        generated_sql=GeneratedSql(sql="", model_name="test", raw_response={}),
        validation=ValidationResult(accepted=False, reason="LLM call failed", sql=""),
        query_result=None,
        error="LLM call failed: connection refused",
    )
    assert response.error is not None
    assert response.query_result is None


# --- QueryProvider Protocol has execute_readonly_query ---

def test_query_provider_has_execute_readonly_query() -> None:
    """FR-009: QueryProvider Protocol includes execute_readonly_query."""
    assert hasattr(QueryProvider, "execute_readonly_query"), (
        "QueryProvider must expose execute_readonly_query for Text-to-SQL"
    )
