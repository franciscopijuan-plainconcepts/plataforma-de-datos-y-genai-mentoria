"""Canonical Ossie is mandatory and is never overwritten by a derivative command."""

import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from src.cli import main as cli
from src.contracts.dictionary import DataDictionaryDocument
from src.data_engineering.semantic_layer.ossie import VENDOR, canonicalize_ossie, conversion_report
from tests.semantic_fixtures import _build_test_dictionary, _build_test_table_defs, _canonical_model_text


@pytest.fixture()
def context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, DataDictionaryDocument]:
    dictionary = _build_test_dictionary(_build_test_table_defs())
    model = tmp_path / "sales.ossie.json"
    model.write_text(canonicalize_ossie(_canonical_model_text()))
    source = tmp_path / "source.xlsx"
    source.write_bytes(b"isolated workbook")
    monkeypatch.setattr(cli, "_OSSIE_MODEL_PATH", model)
    monkeypatch.setattr(cli, "_DEFAULT_SOURCE", source)
    monkeypatch.setattr(cli, "_SEMANTIC_LAYER_JSON_PATH", tmp_path / "derived.json")
    monkeypatch.setattr(cli, "_SEMANTIC_LAYER_MD_PATH", tmp_path / "derived.md")
    monkeypatch.setattr(cli, "explore_workbook", Mock(return_value=([], [])))
    monkeypatch.setattr(cli, "infer_table_defs", Mock(return_value=_build_test_table_defs()))
    monkeypatch.setattr(cli, "generate_dictionary", Mock(return_value=dictionary))
    return model, dictionary


@pytest.mark.parametrize("command", [
    ["ask", "sales", "--viewer", "north", "--json", "--no-metabase"],
    ["chart", "sales", "--viewer", "north"],
    ["evaluate", "--viewer", "north"],
    ["generate-semantic-layer"],
])
@pytest.mark.parametrize("invalid", ["missing", "version", "profile", "yaml", "formula"])
def test_invalid_model_stops_before_llm_or_sql(
    context: tuple[Path, DataDictionaryDocument], monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], command: list[str], invalid: str,
) -> None:
    model, _dictionary = context
    if invalid == "missing":
        model.unlink()
    elif invalid == "yaml":
        model.write_text("version: [invalid YAML")
    else:
        raw = json.loads(model.read_text())
        if invalid == "version":
            raw["version"] = "9"
        elif invalid == "profile":
            profile = json.loads(raw["custom_extensions"][0]["data"])
            profile["profile_version"] = 2
            raw["custom_extensions"][0] = {"vendor_name": VENDOR, "data": json.dumps(profile)}
        else:
            raw["metrics"][0]["expression"]["dialects"][0]["expression"] = 'SUM("Sales"'
        model.write_text(json.dumps(raw))
    llm = Mock(side_effect=AssertionError("LLM must not be constructed"))
    repo = Mock(side_effect=AssertionError("No SQL before canonical validation"))
    fallback = Mock(side_effect=AssertionError("Legacy fallback is forbidden"))
    monkeypatch.setattr("src.ai_engineering.llm_client.LlmClient", llm)
    monkeypatch.setattr(cli, "PostgresRepository", repo)
    legacy_json = model.parent / "legacy.json"
    legacy_json.write_text("invalid legacy JSON must never be read")
    monkeypatch.setattr(cli, "_SEMANTIC_LAYER_JSON_PATH", legacy_json)
    monkeypatch.setattr(cli, "_infer_orders_table_def", fallback)
    with pytest.raises(SystemExit) as exc:
        cli.main(command)
    assert exc.value.code == 1
    assert "Ossie model" in capsys.readouterr().err
    llm.assert_not_called()
    repo.assert_not_called()
    fallback.assert_not_called()


def test_editing_only_ossie_changes_derivatives_and_provenance(
    context: tuple[Path, DataDictionaryDocument], monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    model, _dictionary = context
    before = conversion_report(model.read_text()).model_sha256
    raw = json.loads(model.read_text())
    metric = next(metric for metric in raw["metrics"] if metric["name"] == "gross_sales")
    metric["expression"]["dialects"][0]["expression"] = 'SUM("Profit")'
    model.write_text(json.dumps(raw))
    edited = model.read_bytes()
    cli.main(["generate-semantic-layer"])
    derived = json.loads((tmp_path / "derived.json").read_text())
    assert derived["provenance"]["model_sha256"] != before
    assert derived["provenance"]["model_sha256"] == conversion_report(model.read_text()).model_sha256
    assert derived["provenance"]["code_revision"]
    assert isinstance(derived["provenance"]["code_dirty"], bool)
    assert next(metric for metric in derived["metrics"] if metric["name"] == "gross_sales")["formula_sql"] == 'SUM("Profit")'
    assert model.read_bytes() == edited
    assert derived["provenance"]["model_sha256"] in (tmp_path / "derived.md").read_text()
    first = (tmp_path / "derived.json").read_bytes()
    cli.main(["generate-semantic-layer"])
    assert (tmp_path / "derived.json").read_bytes() == first


def test_generated_outputs_do_not_pollute_code_dirty_provenance(
    context: tuple[Path, DataDictionaryDocument], monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = Mock(side_effect=[
        subprocess.CompletedProcess([], 0, "a" * 40, ""),
        subprocess.CompletedProcess([], 0, "", ""),
    ])
    monkeypatch.setattr("src.cli.main.subprocess.run", calls)
    _dictionary, _table, model, _text = cli._load_semantic_context()
    assert model.provenance is not None and model.provenance.code_dirty is False
    status_command = calls.call_args_list[1].args[0]
    assert status_command[:4] == ["git", "status", "--porcelain", "--"]
    assert "src" in status_command and ".artifacts" not in status_command


@pytest.mark.parametrize("error", [
    OSError("Git unavailable"),
    subprocess.CalledProcessError(1, ["git", "rev-parse", "HEAD"]),
    subprocess.TimeoutExpired(["git", "rev-parse", "HEAD"], 10),
])
def test_unavailable_git_provenance_is_reported_explicitly(
    context: tuple[Path, DataDictionaryDocument], monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], error: Exception,
) -> None:
    calls = Mock(side_effect=error)
    monkeypatch.setattr("src.cli.main.subprocess.run", calls)
    _dictionary, _table, model, _text = cli._load_semantic_context()
    assert model.provenance is not None
    assert model.provenance.code_revision is None
    assert model.provenance.code_dirty is None
    assert calls.call_args.kwargs["timeout"] == 10
    assert "WARNING: code revision is unavailable" in capsys.readouterr().out
