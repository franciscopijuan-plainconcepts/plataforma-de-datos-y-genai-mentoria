"""Shared typed contracts (Pydantic v2 models).

These models are the sole currency that crosses module/layer boundaries.
Raw dict / DBAPI rows MUST NOT cross these boundaries (constitution
Principle I). Engine-specific rendering happens in adapters only
(Principle III).
"""
