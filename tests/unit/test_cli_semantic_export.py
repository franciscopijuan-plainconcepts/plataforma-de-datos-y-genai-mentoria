"""Explicit candidate export must not replace the active model or editable files."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from src.cli import main as cli
from src.data_engineering.semantic_layer.ossie import canonicalize_ossie, import_ossie
from tests.semantic_fixtures import _build_test_dictionary, _build_test_table_defs, _canonical_model_text


@pytest.mark.parametrize("suffix", ["json", "yaml"])
def test_explicit_export_keeps_consumption_derivatives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, suffix: str,
) -> None:
    source = tmp_path / "source.xlsx"
    source.write_bytes(b"isolated source")
    legacy_json = tmp_path / "semantic_layer.json"
    legacy_md = tmp_path / "semantic_layer.md"
    candidate = tmp_path / f"candidate.ossie.{suffix}"
    dictionary = _build_test_dictionary(_build_test_table_defs())
    model = tmp_path / "canonical.ossie.json"
    raw = json.loads(canonicalize_ossie(_canonical_model_text()))
    opaque = {"vendor_name": "EXTERNAL", "data": "opaque external metadata"}
    raw["custom_extensions"].append(opaque)
    raw["datasets"][0]["fields"][0]["custom_extensions"].append(opaque)
    raw["metrics"][0]["custom_extensions"].append(opaque)
    model.write_text(json.dumps(raw))
    model_bytes = model.read_bytes()
    monkeypatch.setattr(cli, "_DEFAULT_SOURCE", source)
    monkeypatch.setattr(cli, "_OSSIE_MODEL_PATH", model)
    monkeypatch.setattr(cli, "_SEMANTIC_LAYER_JSON_PATH", legacy_json)
    monkeypatch.setattr(cli, "_SEMANTIC_LAYER_MD_PATH", legacy_md)
    monkeypatch.setattr(cli, "explore_workbook", Mock(return_value=([], [])))
    monkeypatch.setattr(cli, "infer_table_defs", Mock(return_value=_build_test_table_defs()))
    monkeypatch.setattr(cli, "generate_dictionary", Mock(return_value=dictionary))

    cli.main(["generate-semantic-layer"])
    assert legacy_json.exists() and legacy_md.exists()
    assert not candidate.exists()
    assert model.read_bytes() == model_bytes
    before = legacy_json.read_bytes()
    cli.main(["generate-semantic-layer", "--export-ossie", str(candidate)])
    assert len(import_ossie(candidate.read_text(), dictionary).metrics) == 8
    copied = json.loads(canonicalize_ossie(candidate.read_text()))
    assert opaque in copied["custom_extensions"]
    assert opaque in copied["datasets"][0]["fields"][0]["custom_extensions"]
    assert opaque in copied["metrics"][0]["custom_extensions"]
    if suffix == "yaml":
        assert not candidate.read_text().lstrip().startswith("{")
    assert legacy_json.read_bytes() == before


def test_candidate_never_overwritten(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    candidate = tmp_path / "edited.ossie.yaml"
    candidate.write_text("user-edited candidate", encoding="utf-8")
    explore = Mock(side_effect=AssertionError("Must reject before exploration"))
    monkeypatch.setattr(cli, "explore_workbook", explore)
    with pytest.raises(SystemExit) as exc:
        cli.main(["generate-semantic-layer", "--export-ossie", str(candidate)])
    assert exc.value.code == 1
    assert candidate.read_text() == "user-edited candidate"
    explore.assert_not_called()


def test_export_rejects_unknown_flags() -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["generate-semantic-layer", "--overwrite"])
    assert exc.value.code == 1
