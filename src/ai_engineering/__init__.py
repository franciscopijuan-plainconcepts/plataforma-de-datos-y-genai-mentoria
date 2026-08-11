"""AI Engineering domain — Text-to-SQL pipeline (v1.0 / v1.1).

This domain is strictly isolated from Data Engineering and Data Access
(constitution Principle II). Cross-domain traffic flows only through typed
contracts in `src/contracts/` and the `QueryProvider` Protocol.

Modules:
- `llm_client`: typed wrapper around the OpenAI SDK (Forge proxy). The ONLY
  module that imports `openai` (enforced by boundary contract tests).
- `prompt_builder`: builds the LLM prompt from `DataDictionaryDocument`.
- `sql_validator`: validates LLM-generated SQL (SELECT-only, Orders only, etc.).
- `pipeline`: orchestrates the full Text-to-SQL flow.
- `evaluation`: (v1.1) lightweight sanity-check evaluation harness.

Reference: specs/002-text-to-sql-v1/contracts/text_to_sql.md
"""

from __future__ import annotations
