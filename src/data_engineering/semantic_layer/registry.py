"""Viewer registry (v2.0) — loads `SemanticViewer` entries from `viewers.yaml`.

Pure YAML parsing + typed model construction. Server of typed `SemanticViewer`
instances to the rest of the system. `pyyaml` is confined to this module
(enforced by `tests/contract/test_boundaries.py`).

Environment override:
- `SEMANTIC_VIEWERS_FILE` : path to a YAML viewer config (default: `viewers.yaml`
  in the current working directory).
- `ENV`                   : environment marker. If `ENV in {local, dev, test}`,
  a viewer with `allows_full_access=true` may bypass RLS (logged as
  `gov.bypass`). In any other environment, `allows_full_access` is forced to
  False regardless of what the YAML says — governance is NON-NEGOTIABLE
  (constitution Principle IV).

Reference: specs/003-semantic-layer-v1/research.md Part C
            specs/003-semantic-layer-v1/contracts/semantic_layer.md
            specs/003-semantic-layer-v1/tasks.md T010
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml  # pyyaml — confined to this module (boundary test enforced)

from src.contracts.semantic_layer import SemanticViewer


_LOCAL_DEV_ENVS: set[str] = {"local", "dev", "test"}
_DEFAULT_VIEWERS_PATH = Path("viewers.yaml")


def _is_local_dev() -> bool:
    """Return True if the current `ENV` is in the local-dev allowlist."""
    return os.environ.get("ENV", "").strip().lower() in _LOCAL_DEV_ENVS


def _resolve_path(path: Path | None) -> Path:
    """Resolve the viewers file path: explicit arg > env var > default."""
    if path is not None:
        return path
    env_path = os.environ.get("SEMANTIC_VIEWERS_FILE")
    if env_path:
        return Path(env_path)
    return _DEFAULT_VIEWERS_PATH


def _parse_yaml_viewers(raw: dict[str, object]) -> list[dict[str, object]]:
    """Extract the `viewers: [...]` list from the parsed YAML document.

    The expected shape:
        viewers:
          - id: alice
            regions: [Caribbean, Central America]
            allows_full_access: false

    Raises:
        ValueError: if the `viewers` key is missing or not a list.
    """
    if not isinstance(raw, dict):
        raise ValueError(
            f"Viewers YAML must be a mapping at top level; got {type(raw).__name__}."
        )
    viewers = raw.get("viewers")
    if not isinstance(viewers, list):
        raise ValueError(
            "Viewers YAML must contain a top-level `viewers:` list. "
            "See viewers.example.yaml for the format."
        )
    return viewers


def _build_viewer(raw_entry: dict[str, object]) -> SemanticViewer:
    """Convert a raw dict from YAML into a validated `SemanticViewer`.

    Enforces the `is_local_dev` gating of `allows_full_access` at load time
    so that prod configs can never bypass RLS accidentally.
    """
    if not isinstance(raw_entry, dict):
        raise ValueError(f"Viewer entry must be a mapping; got {type(raw_entry).__name__}.")
    viewer_id_raw = raw_entry.get("id")
    if not isinstance(viewer_id_raw, str) or not viewer_id_raw.strip():
        raise ValueError(
            f"Viewer entry missing `id` (must be non-empty string): {raw_entry!r}"
        )
    regions_raw = raw_entry.get("regions", [])
    if not isinstance(regions_raw, list):
        raise ValueError(
            f"Viewer {viewer_id_raw!r} `regions` must be a list; got {type(regions_raw).__name__}."
        )
    regions = [str(r) for r in regions_raw]
    allows_full_access_raw = bool(raw_entry.get("allows_full_access", False))
    is_local_dev = _is_local_dev()
    # Defense-in-depth: even if the YAML says allows_full_access=true, the
    # flag is only honored in local/dev/test environments.
    allows_full_access = allows_full_access_raw if is_local_dev else False
    return SemanticViewer(
        viewer_id=viewer_id_raw.strip(),
        regions=regions,
        allows_full_access=allows_full_access,
        is_local_dev=is_local_dev,
    )


class ViewerRegistry:
    """Loads viewers from a `viewers.yaml` file and serves them by id."""

    def load_viewers(self, path: Path | None = None) -> list[SemanticViewer]:
        """Load all viewers from the YAML file at `path` (or env/default).

        Raises:
            FileNotFoundError: if the resolved path does not exist.
            ValueError: if the file is not in the expected format.
        """
        resolved = _resolve_path(path)
        if not resolved.exists():
            raise FileNotFoundError(
                f"Viewers config not found at {resolved}. "
                "Copy viewers.example.yaml to viewers.yaml (or set SEMANTIC_VIEWERS_FILE)."
            )
        with resolved.open("r", encoding="utf-8") as f:
            try:
                raw: dict[str, object] = yaml.safe_load(f)
            except yaml.YAMLError as exc:
                raise ValueError(
                    f"Viewers YAML at {resolved} is malformed: {exc}"
                ) from exc
        if raw is None:  # empty file -> empty list
            return []
        raw_viewers = _parse_yaml_viewers(raw)
        return [_build_viewer(entry) for entry in raw_viewers]

    def get_viewer(self, viewer_id: str, path: Path | None = None) -> SemanticViewer:
        """Load viewers and return the one matching `viewer_id`.

        Raises:
            ValueError: if the viewer is not found — the error lists the
                available viewer ids so the caller can correct the request.
        """
        viewers = self.load_viewers(path)
        for v in viewers:
            if v.viewer_id == viewer_id:
                return v
        available = sorted(v.viewer_id for v in viewers)
        raise ValueError(
            f"Viewer {viewer_id!r} not found. Available viewers: {available}."
        )


__all__ = ["ViewerRegistry"]
