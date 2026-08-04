"""Abstracted, engine-agnostic data-access layer (Repository/Provider pattern).

Upstream code depends only on the Protocols in `interfaces.py` and the
typed contract models in `src/contracts/data_access.py`. Engine-specific
code is confined to `adapters/<engine>/` (constitution Principle III).
"""
