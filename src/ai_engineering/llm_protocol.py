"""Structural protocol for text-completion clients (v3.1).

`nl_predict.py` and `nl_chart.py` depend on this Protocol instead of the
concrete `LlmClient` so they can be unit-tested with lightweight fakes (no
network, no `openai` import) while `LlmClient` (the only module allowed to
import `openai`, per the boundary contract test) satisfies it structurally.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class TextCompleter(Protocol):
    """Anything that can turn a prompt into raw text."""

    def complete(self, prompt: str) -> str:
        """Return the raw text completion for `prompt`."""
        ...


__all__ = ["TextCompleter"]
