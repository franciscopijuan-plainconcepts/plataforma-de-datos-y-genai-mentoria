"""Text-to-SQL contract models (Pydantic v2).

These models are the sole currency that crosses the AI Engineering boundary.
The AI Engineering domain (`src/ai_engineering/`) depends on these models
and the `QueryProvider` Protocol — it does NOT import `openai`, `psycopg`,
or `pandas` directly (constitution Principles I, II, III).

Reference: specs/002-text-to-sql-v1/contracts/text_to_sql.md
            specs/002-text-to-sql-v1/data-model.md
"""

from __future__ import annotations

import os
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.contracts.data_access import TableDef


class LlmConfig(BaseModel):
    """Configuration for the LLM client, loaded from environment variables.

    All values come from `FORGE_*` env vars (see `.env.example`). The API key
    is required — `from_env()` raises if missing (FR-013 fail-fast).
    """

    model_config = ConfigDict(frozen=True)

    api_key: str
    base_url: str = "https://forge.plainconcepts.com/v1"
    model_name: str = "glm-5-2"
    max_tokens: int = 4096
    temperature: float = 0.0

    @classmethod
    def from_env(cls) -> "LlmConfig":
        """Build config from `FORGE_*` environment variables.

        Raises `ValueError` if `FORGE_API_KEY` is missing or empty (FR-013).
        """
        api_key = os.environ.get("FORGE_API_KEY", "")
        if not api_key.strip():
            raise ValueError(
                "FORGE_API_KEY is missing or empty. Set it in .env "
                "(see .env.example). The system cannot call the LLM without it."
            )
        return cls(
            api_key=api_key,
            base_url=os.environ.get(
                "FORGE_BASE_URL", "https://forge.plainconcepts.com/v1"
            ),
            model_name=os.environ.get("FORGE_MODEL_NAME", "glm-5-2"),
            max_tokens=int(os.environ.get("FORGE_MAX_TOKENS", "4096")),
            temperature=float(os.environ.get("FORGE_TEMPERATURE", "0.0")),
        )


class NLQuestion(BaseModel):
    """The user's natural-language input (Spanish or English)."""

    model_config = ConfigDict(frozen=True)

    text: str = Field(min_length=1)


class GeneratedSql(BaseModel):
    """The SQL string produced by the LLM, plus metadata for reproducibility.

    The `sql` field MAY be empty or invalid — validation happens downstream
    in `SqlValidator`. The `raw_response` captures the full LLM response for
    debugging; `Any` is justified because the OpenAI SDK response shape is
    dynamic and not statically typed.
    """

    model_config = ConfigDict(frozen=True)

    sql: str
    model_name: str
    # SDK response shape is dynamic; Any is justified here.
    raw_response: dict[str, Any]


class ValidationResult(BaseModel):
    """The outcome of validating the generated SQL."""

    model_config = ConfigDict(frozen=True)

    accepted: bool
    reason: str | None = None  # None if accepted; rejection reason if not
    sql: str  # the SQL that was validated (for transparency per FR-008)


class QueryRow(BaseModel):
    """A single row from a read-only query result.

    Text-to-SQL results are often aggregations (SUM, COUNT, etc.) that don't
    map to `OrderRow`, so this model holds dynamic column->value pairs.
    `Any` is justified: result columns are inherently dynamic (aggregations,
    aliases, expressions). The adapter coerces DB types to JSON-safe values.
    """

    model_config = ConfigDict(frozen=True)

    data: dict[str, Any]  # column_name -> value


class QueryResult(BaseModel):
    """The executed result of a validated SQL query."""

    model_config = ConfigDict(frozen=True)

    sql: str  # the exact SQL that was executed (FR-008)
    rows: list[QueryRow]
    row_count: int
    latency_ms: int
    error: str | None = None  # None if successful; the DB error message if failed


class TextToSqlRequest(BaseModel):
    """A typed model capturing the full request for reproducibility.

    Constitution Principle V (reproducible MLOps): capturing the prompt +
    config makes each Text-to-SQL call traceable and reproducible.
    """

    model_config = ConfigDict(frozen=True)

    question: NLQuestion
    prompt: str  # the full prompt sent to the LLM
    llm_config: LlmConfig


class TextToSqlResponse(BaseModel):
    """The full typed response returned to the caller (FR-008).

    State transitions:
    - If `error` is non-None: the pipeline failed before producing a result
      (LLM unreachable, API key missing).
    - If `validation.accepted` is False: `query_result` is None (SQL rejected).
    - If `query_result.error` is non-None: SQL executed but DB returned error.
    - If `query_result` is non-None and `query_result.error` is None: success.
    """

    model_config = ConfigDict(frozen=True)

    question: NLQuestion
    generated_sql: GeneratedSql
    validation: ValidationResult
    query_result: QueryResult | None = None
    error: str | None = None  # top-level error (LLM connection failed, etc.)


class SampleQuestion(BaseModel):
    """A sample evaluation item (v1.1 sanity-check only).

    The `expected_sql_normalized` is the expected SQL, normalized (lowercase,
    whitespace-collapsed) for comparison with the generated SQL.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    question: str
    expected_sql_normalized: str


__all__ = [
    "LlmConfig",
    "NLQuestion",
    "GeneratedSql",
    "ValidationResult",
    "QueryRow",
    "QueryResult",
    "TextToSqlRequest",
    "TextToSqlResponse",
    "SampleQuestion",
]
