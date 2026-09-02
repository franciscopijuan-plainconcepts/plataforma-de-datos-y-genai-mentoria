"""Text-to-SQL pipeline orchestrator (v1.0).

Orchestrates the full pipeline: build prompt → call LLM → parse SQL → validate
→ execute → return typed `TextToSqlResponse`. All dependencies are injected
(typed), so the pipeline can be unit-tested with fakes.

Reference: specs/002-text-to-sql-v1/contracts/text_to_sql.md
            specs/002-text-to-sql-v1/data-model.md
"""

from __future__ import annotations

import time
import logging
from pathlib import Path
from typing import Callable, Union

from src.ai_engineering.llm_client import LlmClient
from src.ai_engineering.prompt_builder import build_prompt
from src.ai_engineering.sql_validator import validate_sql
from src.contracts.data_access import TableDef
from src.contracts.dictionary import DataDictionaryDocument
from src.contracts.semantic_layer import SemanticLayerDocument, SemanticViewer
from src.contracts.text_to_sql import (
    GeneratedSql,
    LlmConfig,
    NLQuestion,
    QueryResult,
    QueryRow,
    TextToSqlResponse,
    ValidationResult,
)
from src.data_access.interfaces import QueryProvider

# v2.1 (feature 004-metabase-integration): optional sink callback invoked at
# the end of a successful `run()`. Generic by design — the pipeline does NOT
# reference Metabase (or any other sink) directly; the CLI composition root
# injects the concrete sink (see `contracts/pipeline_integration.md`).
# The callback receives `response.query_result.sql` which is the ALREADY-
# GOVERNED SQL (post-GovernedQueryProvider, post-RLS injection) so any sink
# that sends SQL onward to e.g. Metabase preserves Principle IV NON-NEGOTIABLE.
OnQueryComplete = Callable[
    [TextToSqlResponse, Union[SemanticViewer, None]], None
]

_logger = logging.getLogger(__name__)

# Default structured log path for v1.1 per FR-014.
_LOG_PATH = Path(".artifacts") / "text_to_sql.log"


def _log_call(
    response: "TextToSqlResponse",
    latency_ms: int,
    viewer: SemanticViewer | None = None,
) -> None:
    """Log a Text-to-SQL call to the structured log (FR-014 + FR-021).

    Logs: timestamp, input question, generated SQL, validation outcome,
    result/error, latency_ms. v2.0: also logs viewer_id, regions, gov_bypass
    flag when a SemanticViewer is active.
    """
    from datetime import datetime, timezone

    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()
    q = response.question.text
    sql = response.generated_sql.sql
    accepted = response.validation.accepted
    reason = response.validation.reason or ""
    if response.query_result is not None:
        rows = response.query_result.row_count
        err = response.query_result.error or ""
    else:
        rows = 0
        err = response.error or ""
    viewer_id = viewer.viewer_id if viewer is not None else "none"
    regions = list(viewer.regions) if viewer is not None else []
    gov_bypass = (
        viewer.allows_full_access and viewer.is_local_dev
        if viewer is not None
        else False
    )
    line = (
        f"[{ts}] question={q!r} sql={sql!r} accepted={accepted} "
        f"reason={reason!r} rows={rows} error={err!r} latency_ms={latency_ms} "
        f"viewer_id={viewer_id} regions={regions!r} gov_bypass={gov_bypass}\n"
    )
    with _LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line)


class TextToSqlPipeline:
    """Orchestrates the Text-to-SQL pipeline.

    Dependencies are injected so the pipeline can be tested with fakes:
    - `dictionary` + `table_def`: the semantic context (from the baseline).
    - `llm_client`: the typed LLM wrapper.
    - `query_provider`: the data-access Protocol for executing validated SQL.
    - `llm_config`: for reproducibility (captured in `TextToSqlRequest`).
    """

    def __init__(
        self,
        dictionary: DataDictionaryDocument,
        table_def: TableDef,
        llm_client: LlmClient,
        query_provider: QueryProvider,
        llm_config: LlmConfig,
        semantic_layer: SemanticLayerDocument | None = None,
        viewer: SemanticViewer | None = None,
        on_query_complete: OnQueryComplete | None = None,
        extra_tables: dict[str, TableDef] | None = None,
    ) -> None:
        self._dictionary = dictionary
        self._table_def = table_def
        self._llm_client = llm_client
        self._query_provider = query_provider
        self._llm_config = llm_config
        # v2.0: optional semantic layer enriches the prompt with metrics/dimensions.
        # When None, build_prompt falls back to v1.x behavior (FR-016).
        self._semantic_layer = semantic_layer
        # v2.0: optional viewer used for logging governance context.
        # The actual RLS enforcement happens in GovernedQueryProvider (the
        # query_provider delegate); the pipeline only logs the viewer for audit.
        self._viewer = viewer
        # v2.1: optional sink callback invoked after a successful `run()`.
        # Best-effort: any exception thrown by the callback is swallowed and
        # logged as a warning — sinks must NOT break the pipeline (FR-013). The
        # callback receives `response.query_result.sql` (governed) so sinks
        # like Metabase preserve Principle IV NON-NEGOTIABLE by design.
        self._on_query_complete = on_query_complete
        # v3.2 (004-sales-prediction-model amendment): optional extra
        # queryable tables (e.g. `{"predictions": predictions_table_def()}`)
        # that are NOT the pipeline's primary `table_def` but the LLM may
        # still reference on their own. Threaded into both `build_prompt`
        # (schema visibility) and `validate_sql` (table/column allowlisting).
        self._extra_tables = extra_tables

    def run(self, question: NLQuestion) -> TextToSqlResponse:
        """Run the full Text-to-SQL pipeline for a single question.

        Catches all errors and surfaces them in `TextToSqlResponse.error`
        (FR-013 fail-fast for API key; other errors captured gracefully).
        """
        # 1. Build the prompt (v2.0: pass semantic_layer if available).
        prompt = build_prompt(
            question=question,
            dictionary=self._dictionary,
            table_def=self._table_def,
            semantic_layer=self._semantic_layer,
            extra_tables=self._extra_tables,
        )

        # 2. Call the LLM.
        try:
            generated: GeneratedSql = self._llm_client.generate_sql(prompt)
        except Exception as exc:
            _logger.error("LLM call failed: %s", exc)
            response = TextToSqlResponse(
                question=question,
                generated_sql=GeneratedSql(
                    sql="", model_name=self._llm_config.model_name, raw_response={}
                ),
                validation=ValidationResult(
                    accepted=False, reason="LLM call failed", sql=""
                ),
                error=f"LLM call failed: {exc}",
            )
            _log_call(response, 0, self._viewer)
            return response

        # 3. Validate the SQL.
        validation = validate_sql(generated.sql, self._table_def, self._extra_tables)

        if not validation.accepted:
            _logger.info(
                "SQL rejected: question=%r reason=%s",
                question.text,
                validation.reason,
            )
            response = TextToSqlResponse(
                question=question,
                generated_sql=generated,
                validation=validation,
                query_result=None,
            )
            _log_call(response, 0, self._viewer)
            return response

        # 4. Execute the validated SQL.
        start = time.time()
        try:
            rows: list[QueryRow] = self._query_provider.execute_readonly_query(
                generated.sql, self._table_def
            )
            latency_ms = int((time.time() - start) * 1000)
            result = QueryResult(
                sql=generated.sql,
                rows=rows,
                row_count=len(rows),
                latency_ms=latency_ms,
                error=None,
            )
        except Exception as exc:
            latency_ms = int((time.time() - start) * 1000)
            _logger.error("SQL execution failed: %s", exc)
            result = QueryResult(
                sql=generated.sql,
                rows=[],
                row_count=0,
                latency_ms=latency_ms,
                error=str(exc),
            )

        _logger.info(
            "Text-to-SQL: question=%r sql=%s rows=%d latency_ms=%d",
            question.text,
            generated.sql,
            len(rows) if not result.error else 0,
            latency_ms,
        )

        response = TextToSqlResponse(
            question=question,
            generated_sql=generated,
            validation=validation,
            query_result=result,
        )
        _log_call(response, latency_ms, self._viewer)
        # v2.1 (feature 004-metabase-integration): invoke the optional sink
        # callback at the END of a successful `run()`. Best-effort (FR-013):
        # any exception is swallowed and logged, so the pipeline never breaks
        # because Metabase (or any sink) is unavailable.
        if self._on_query_complete is not None:
            try:
                self._on_query_complete(response, self._viewer)
            except Exception as exc:  # noqa: BLE001 — sinks must be best-effort
                _logger.warning(
                    "on_query_complete callback failed (best-effort, swallowed): %s",
                    exc,
                )
        return response


__all__ = ["TextToSqlPipeline"]
