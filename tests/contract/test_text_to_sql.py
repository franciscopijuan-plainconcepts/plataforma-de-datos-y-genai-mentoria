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


# --- v2.0 (feature 003): PromptBuilder with SemanticLayerDocument ---

def test_build_prompt_with_semantic_layer_includes_metrics() -> None:
    """FR-015: when a SemanticLayerDocument is provided, the prompt includes
    metric names so the LLM can distinguish net vs gross (US3)."""
    from datetime import datetime, timezone

    from src.ai_engineering.prompt_builder import build_prompt
    from src.contracts.data_access import ColumnDef, LogicalType, TableDef
    from src.contracts.dictionary import (
        DataDictionaryDocument,
        DictionaryEntry,
        RelationshipEntry,
        TableDictionary,
    )
    from src.contracts.semantic_layer import (
        Dimension,
        Metric,
        SemanticLayerDocument,
        SemanticRelationship,
        TableSemanticClassification,
    )
    from src.data_engineering.semantic_layer.metrics import get_metrics

    # Minimal dictionary + table_def.
    orders_table_dict = TableDictionary(
        name="Orders",
        kaggle_label="Transactional Logs",
        purpose="Orders table.",
        primary_key=["Row ID"],
        relationships=[
            RelationshipEntry(
                from_column="Region", to_table="People", to_column="Region",
                cardinality="N:1",
            ),
        ],
        columns=[
            DictionaryEntry(
                name="Sales", business_description="Sales amount.",
                logical_type=LogicalType.DECIMAL,
                postgres_type="NUMERIC(12,4)", nullable=False,
                is_key=False, key_kind=None,
                allowed_values=None, min_value=None, max_value=None,
                unique_count=0, data_quality_notes=[],
            ),
        ],
    )
    dictionary = DataDictionaryDocument(
        generated_at=datetime(2026, 8, 17, tzinfo=timezone.utc),
        source_file="test", source_sha256="sha",
        tables=[orders_table_dict],
    )
    table_def = TableDef(
        name="Orders",
        columns=[
            ColumnDef(name="Sales", logical_type=LogicalType.DECIMAL,
                      precision=12, scale=4, nullable=False),
        ],
        description="Orders.",
    )
    # Minimal semantic layer with one metric so the prompt includes it.
    semantic_layer = SemanticLayerDocument(
        version="1.0.0",
        tables=[
            TableSemanticClassification(
                name="Orders", table_type="fact", purpose="Orders.",
            ),
        ],
        metrics=[
            Metric(
                name="gross_sales",
                business_description="Gross sales revenue.",
                formula_sql='SUM("Sales")',
                source_table="Orders",
                aggregation="SUM",
            ),
        ],
        dimensions=[
            Dimension(
                name="region", column="Region", source_table="Orders",
                business_description="Geographic region.",
                dimension_type="geographic",
            ),
        ],
        relationships=[],
        source_sha256="sha",
        semantic_source_sha256="sem-sha",
        generated_at=datetime(2026, 8, 17, tzinfo=timezone.utc),
        assumptions=[],
    )
    question = NLQuestion(text="Show me gross sales by region")
    prompt = build_prompt(question, dictionary, table_def, semantic_layer)
    # The prompt must include the metric name + formula.
    assert "gross_sales" in prompt
    assert 'SUM("Sales")' in prompt
    # The prompt must include the dimension.
    assert "Region" in prompt
    # The prompt must use the v2 rules (allow Returns JOIN).
    assert "Returns" in prompt


def test_build_prompt_fallback_without_semantic_layer() -> None:
    """FR-016: when semantic_layer=None, the prompt falls back to v1.x behavior
    (no metrics block; Rules forbid Returns references)."""
    from datetime import datetime, timezone

    from src.ai_engineering.prompt_builder import build_prompt
    from src.contracts.data_access import ColumnDef, LogicalType, TableDef
    from src.contracts.dictionary import (
        DataDictionaryDocument,
        DictionaryEntry,
        RelationshipEntry,
        TableDictionary,
    )

    orders_table_dict = TableDictionary(
        name="Orders",
        kaggle_label="Transactional Logs",
        purpose="Orders table.",
        primary_key=["Row ID"],
        relationships=[],
        columns=[
            DictionaryEntry(
                name="Sales", business_description="Sales amount.",
                logical_type=LogicalType.DECIMAL,
                postgres_type="NUMERIC(12,4)", nullable=False,
                is_key=False, key_kind=None,
                allowed_values=None, min_value=None, max_value=None,
                unique_count=0, data_quality_notes=[],
            ),
        ],
    )
    dictionary = DataDictionaryDocument(
        generated_at=datetime(2026, 8, 17, tzinfo=timezone.utc),
        source_file="test", source_sha256="sha",
        tables=[orders_table_dict],
    )
    table_def = TableDef(
        name="Orders",
        columns=[
            ColumnDef(name="Sales", logical_type=LogicalType.DECIMAL,
                      precision=12, scale=4, nullable=False),
        ],
        description="Orders.",
    )
    question = NLQuestion(text="Show me total sales")
    prompt = build_prompt(question, dictionary, table_def, semantic_layer=None)
    # No semantic layer block present.
    assert "Semantic Layer" not in prompt
    assert "gross_sales" not in prompt
    # v1.x rules (Returns forbidden).
    assert "Do not reference Returns" in prompt
