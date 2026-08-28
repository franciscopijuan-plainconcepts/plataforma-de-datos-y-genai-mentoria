"""LLM client wrapper for Text-to-SQL (v1.0).

Typed wrapper around the OpenAI Python SDK, configured for the Forge proxy.
This is the ONLY module that imports `openai` — enforced by the boundary
contract test (`tests/contract/test_boundaries.py`).

Reference: specs/002-text-to-sql-v1/research.md Part D
            specs/002-text-to-sql-v1/contracts/text_to_sql.md
"""

from __future__ import annotations

import httpx
from openai import OpenAI

from src.contracts.text_to_sql import GeneratedSql, LlmConfig


class LlmClient:
    """Typed wrapper around the OpenAI SDK (Forge proxy).

    The `openai` and `httpx` imports are confined to this module (constitution
    Principle II/III). Upstream code depends on this typed interface, not on
    the OpenAI SDK directly.
    """

    def __init__(self, config: LlmConfig) -> None:
        self._config = config
        self._client = OpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            http_client=httpx.Client(verify=False),  # Forge proxy SSL (see test.ipynb)
        )

    def generate_sql(self, prompt: str) -> GeneratedSql:
        """Send the prompt to the LLM and return the generated SQL + metadata.

        Returns a `GeneratedSql` model. The `sql` field MAY be empty or invalid
        — validation happens downstream in `SqlValidator`.
        """
        response = self._client.chat.completions.create(
            model=self._config.model_name,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=self._config.max_tokens,
            temperature=self._config.temperature,
        )
        content = response.choices[0].message.content or ""
        return GeneratedSql(
            sql=content.strip(),
            model_name=self._config.model_name,
            raw_response=response.model_dump(),
        )

    def complete(self, prompt: str) -> str:
        """Send a prompt to the LLM and return the raw text response.

        Generic completion used by NL->structured-output flows that are not
        SQL generation (v3.1: NL->predict-sales, NL->chart). Callers are
        responsible for parsing/validating the returned text (typically JSON)
        into a typed contract model — this method makes no assumptions about
        the response shape.
        """
        response = self._client.chat.completions.create(
            model=self._config.model_name,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=self._config.max_tokens,
            temperature=self._config.temperature,
        )
        return (response.choices[0].message.content or "").strip()


__all__ = ["LlmClient"]
