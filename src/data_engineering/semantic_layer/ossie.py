"""Pinned Ossie boundary; the SDK never crosses a domain contract."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from importlib.resources import files
import json
from pathlib import Path
import re
from typing import TypeVar

from jsonschema import Draft202012Validator
from ossie.models import (
    OssieCustomExtension,
    OssieDataset,
    OssieDialect,
    OssieDialectExpression,
    OssieDimension,
    OssieDocument,
    OssieExpression,
    OssieField,
    OssieMetric,
)
from pydantic import BaseModel
import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError
import yaml
from yaml.nodes import MappingNode

from src.contracts.data_access import ColumnDef, LogicalType, TableDef
from src.contracts.dictionary import DataDictionaryDocument
from src.data_access.sql_validator import validate_sql
from src.contracts.semantic_interchange import (
    ConversionIssue,
    ConversionReport,
    DatasetCompatibility,
    FieldCompatibility,
    MetricCompatibility,
    ModelCompatibility,
)
from src.contracts.semantic_layer import (
    Dimension,
    Metric,
    SemanticLayerDocument,
    SemanticModelProvenance,
    TableSemanticClassification,
)


OSSIE_REVISION = "8551d85b20eccbbe6ce2a38e51b8ba6309a3e05e"
OSSIE_VERSION = "0.2.0.dev0"
VENDOR = "PLATAFORMA_DATOS_GENAI"
MAX_BYTES = 2_000_000
_T = TypeVar("_T", bound=BaseModel)


class _UniqueLoader(yaml.SafeLoader):
    def construct_mapping(self, node: MappingNode, deep: bool = False) -> dict[object, object]:
        seen: set[str] = set()
        for key_node, _ in node.value:
            if key_node.tag != "tag:yaml.org,2002:str" or key_node.value in seen:
                raise ValueError(f"Duplicate or non-string YAML key: {key_node.value!r}")
            seen.add(key_node.value)
        return super().construct_mapping(node, deep=deep)


def _json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key!r}")
        result[key] = value
    return result


def read_ossie_document(path: Path) -> str:
    """Bound file reads before parsing; failures never trigger another model."""
    with path.open("rb") as source:
        payload = source.read(MAX_BYTES + 1)
    if len(payload) > MAX_BYTES:
        raise ValueError(f"Ossie document exceeds {MAX_BYTES} bytes")
    text = payload.decode("utf-8")
    _load_document(text)
    return text


def _load_document(text: str) -> OssieDocument:
    try:
        return _parse_document(text)
    except (yaml.YAMLError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError(f"Invalid Ossie document: {exc}") from exc


def _parse_document(text: str) -> OssieDocument:
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise ValueError(f"Ossie document exceeds {MAX_BYTES} bytes")
    # Aliases/anchors and excessive nesting are unnecessary for this profile.
    depth = 0
    for token in yaml.scan(text):
        if isinstance(token, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken)):
            raise ValueError("Ossie input must not contain YAML anchors or aliases")
        if isinstance(token, (yaml.tokens.FlowSequenceStartToken, yaml.tokens.FlowMappingStartToken,
                              yaml.tokens.BlockSequenceStartToken, yaml.tokens.BlockMappingStartToken)):
            depth += 1
            if depth > 64:
                raise ValueError("Ossie input nesting exceeds 64 levels")
        elif isinstance(token, (yaml.tokens.FlowSequenceEndToken, yaml.tokens.FlowMappingEndToken,
                                yaml.tokens.BlockEndToken)):
            depth -= 1
    raw = (json.loads(text, object_pairs_hook=_json_object)
           if text.lstrip().startswith("{") else yaml.load(text, Loader=_UniqueLoader))
    if not isinstance(raw, dict):
        raise ValueError("Ossie document must be an object")
    if raw.get("version") != OSSIE_VERSION:
        raise ValueError(f"Unsupported Ossie version {raw.get('version')!r}; expected {OSSIE_VERSION}")
    schema = json.loads(files(__package__).joinpath("ossie_schema/ossie-schema.json").read_text())
    errors = sorted(Draft202012Validator(schema).iter_errors(raw), key=lambda error: str(error.path))
    if errors:
        raise ValueError(f"Ossie schema: {errors[0].json_path}: {errors[0].message}")
    document = OssieDocument.from_ossie_json(json.dumps(raw, allow_nan=False))
    _unique([d.name for d in document.datasets], "dataset")
    _unique([m.name for m in document.metrics or []], "metric")
    _unique([r.name for r in document.relationships or []], "relationship")
    for dataset in document.datasets:
        _unique([f.name for f in dataset.fields or []], f"field in {dataset.name}")
    return document


def _unique(names: list[str], kind: str) -> None:
    if len(set(names)) != len(names):
        raise ValueError(f"Duplicate {kind} name")


def _extension(model: BaseModel) -> list[OssieCustomExtension]:
    return [OssieCustomExtension(vendor_name=VENDOR, data=model.model_dump_json())]


def _profile(extensions: list[OssieCustomExtension] | None, cls: type[_T]) -> _T:
    matches = [item for item in extensions or [] if item.vendor_name == VENDOR]
    if len(matches) != 1:
        raise ValueError(f"Exactly one {VENDOR} {cls.__name__} extension is required")
    return cls.model_validate(json.loads(matches[0].data, object_pairs_hook=_json_object))


def _expression(sql: str) -> OssieExpression:
    return OssieExpression(dialects=[
        OssieDialectExpression(dialect=OssieDialect.ANSI_SQL, expression=sql)
    ])


def _sql(expression: OssieExpression) -> str:
    matches = [item.expression for item in expression.dialects if item.dialect == OssieDialect.ANSI_SQL]
    if len(matches) != 1:
        raise ValueError("Platform profile requires exactly one ANSI_SQL expression")
    return matches[0]


def _logical_name(column: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", column.lower()).strip("_")


def export_ossie(layer: SemanticLayerDocument, dictionary: DataDictionaryDocument) -> str:
    """Export a typed consumption projection without changing its definitions."""
    datasets: list[OssieDataset] = []
    for table in layer.tables:
        source = next((item for item in dictionary.tables if item.name == table.name), None)
        if source is None:
            raise ValueError(f"Missing dictionary for {table.name}")
        fields: list[OssieField] = []
        for column in source.columns:
            dimension = next((d for d in layer.dimensions
                              if d.source_table == table.name and d.column == column.name), None)
            physical = exp.column(column.name, quoted=True).sql(dialect="postgres")
            fields.append(OssieField(
                name=dimension.name if dimension else _logical_name(column.name),
                expression=_expression(physical),
                description=dimension.business_description if dimension else column.business_description,
                dimension=OssieDimension(is_time=dimension.dimension_type == "temporal") if dimension else None,
                custom_extensions=_extension(FieldCompatibility(
                    physical_column=column.name,
                    dimension_type=dimension.dimension_type if dimension else None,
                )),
            ))
        keys = [_logical_name(key) for key in source.primary_key]
        datasets.append(OssieDataset(
            name=table.name, source=exp.to_identifier(table.name, quoted=True).sql(),
            description=table.purpose, fields=fields, primary_key=keys or None,
            custom_extensions=_extension(DatasetCompatibility(table_type=table.table_type)),
        ))
    metrics = [
        OssieMetric(
            name=metric.name, expression=_expression(metric.formula_sql),
            description=metric.business_description,
            custom_extensions=_extension(MetricCompatibility(
                source_table=metric.source_table, aggregation=metric.aggregation,
                derives_from=metric.derives_from, uses_returns=metric.uses_returns,
                assumption=metric.assumption,
            )),
        ) for metric in layer.metrics
    ]
    document = OssieDocument(
        name="sales", datasets=datasets, metrics=metrics, relationships=[],
        custom_extensions=_extension(ModelCompatibility(
            content_version=layer.version, source_sha256=layer.source_sha256,
            semantic_source_sha256=layer.semantic_source_sha256,
            assumptions=layer.assumptions, associations=layer.relationships,
        )),
    )
    text = document.to_ossie_json()
    import_ossie(text, dictionary)
    return canonicalize_ossie(text)


def canonicalize_ossie(text: str) -> str:
    document = _load_document(text)
    raw = document.model_dump(mode="json", by_alias=True, exclude_none=True)
    for key in ("datasets", "metrics", "relationships"):
        if key in raw:
            raw[key] = sorted(raw[key], key=lambda item: item["name"])
    for dataset in raw["datasets"]:
        if "fields" in dataset:
            dataset["fields"] = sorted(dataset["fields"], key=lambda item: item["name"])
    # Extension data is itself serialized JSON; whitespace there is not semantic.
    def normalize(value: object) -> object:
        if isinstance(value, list):
            return [normalize(item) for item in value]
        if isinstance(value, dict):
            return {
                key: json.dumps(json.loads(item), sort_keys=True, separators=(",", ":"), allow_nan=False)
                if key == "data" and isinstance(item, str) and value.get("vendor_name") == VENDOR
                else normalize(item) for key, item in value.items()
            }
        return value
    return json.dumps(normalize(raw), sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False)


def serialize_ossie_yaml(text: str) -> str:
    """Render the normalized interchange document without exposing SDK types."""
    return _load_document(canonicalize_ossie(text)).to_ossie_yaml()


def conversion_report(text: str) -> ConversionReport:
    document = _load_document(text)
    _profile(document.custom_extensions, ModelCompatibility)
    issues = [
        ConversionIssue(entity=metric.name, portability="platform-profile",
                        reason="Opaque SQL; requires the platform PostgreSQL compatibility profile")
        for metric in document.metrics or []
    ]
    profile = _profile(document.custom_extensions, ModelCompatibility)
    issues.extend(ConversionIssue(entity=association.name, portability="platform-profile",
                                  reason="Association retained outside the standard unique-key graph")
                  for association in profile.associations)
    return ConversionReport(ossie_revision=OSSIE_REVISION, issues=issues,
                            model_sha256=sha256(canonicalize_ossie(text).encode()).hexdigest())


def import_ossie(text: str, dictionary: DataDictionaryDocument) -> SemanticLayerDocument:
    document = _load_document(text)
    root = _profile(document.custom_extensions, ModelCompatibility)
    _unique([association.name for association in root.associations], "compatibility association")
    tables: list[TableSemanticClassification] = []
    dimensions: list[Dimension] = []
    known_columns = {table.name: {column.name for column in table.columns} for table in dictionary.tables}
    dataset_fields: dict[str, set[str]] = {}
    dataset_keys: dict[str, list[list[str]]] = {}
    dataset_names = {dataset.name for dataset in document.datasets}
    if dataset_names != set(known_columns):
        raise ValueError("Platform model must declare all dictionary datasets")
    for dataset in document.datasets:
        if dataset.name not in known_columns or dataset.source != exp.to_identifier(dataset.name, quoted=True).sql():
            raise ValueError(f"Unsupported source for dataset {dataset.name}")
        meta = _profile(dataset.custom_extensions, DatasetCompatibility)
        tables.append(TableSemanticClassification.model_validate({
            "name": dataset.name, "table_type": meta.table_type,
            "purpose": dataset.description or "",
        }))
        dataset_fields[dataset.name] = {field.name for field in dataset.fields or []}
        dataset_keys[dataset.name] = ([dataset.primary_key] if dataset.primary_key else []) + (dataset.unique_keys or [])
        dictionary_table = next(table for table in dictionary.tables if table.name == dataset.name)
        declared_primary = set(dictionary_table.primary_key)
        physical_by_field: dict[str, str] = {}
        for field in dataset.fields or []:
            field_meta = _profile(field.custom_extensions, FieldCompatibility)
            physical = field_meta.physical_column
            if physical not in known_columns[dataset.name] or _sql(field.expression) != exp.column(physical, quoted=True).sql(dialect="postgres"):
                raise ValueError(f"Invalid physical field {dataset.name}.{field.name}")
            physical_by_field[field.name] = physical
            if field.dimension is not None:
                if dataset.name != "Orders" or field_meta.dimension_type is None:
                    raise ValueError(f"Unsupported dimension {dataset.name}.{field.name}")
                if field.dimension.is_time != (field_meta.dimension_type == "temporal"):
                    raise ValueError(f"Inconsistent time dimension {dataset.name}.{field.name}")
                dimensions.append(Dimension(
                    name=field.name, column=physical, source_table="Orders",
                    business_description=field.description or "",
                    dimension_type=field_meta.dimension_type,
                ))
            elif field_meta.dimension_type is not None:
                raise ValueError(f"Missing dimension declaration {dataset.name}.{field.name}")
        if set(physical_by_field.values()) != known_columns[dataset.name]:
            raise ValueError(f"Incomplete physical fields for dataset {dataset.name}")
        for key in dataset_keys[dataset.name]:
            if not key or any(name not in physical_by_field for name in key):
                raise ValueError(f"Invalid key on {dataset.name}")
            if set(physical_by_field[name] for name in key) != declared_primary:
                raise ValueError(f"Unverified unique key on {dataset.name}")
    for relationship in document.relationships or []:
        if relationship.from_dataset not in dataset_fields or relationship.to not in dataset_fields:
            raise ValueError(f"Unknown dataset in relationship {relationship.name}")
        if (len(relationship.from_columns) != len(relationship.to_columns)
                or not set(relationship.from_columns) <= dataset_fields[relationship.from_dataset]
                or not set(relationship.to_columns) <= dataset_fields[relationship.to]
                or relationship.to_columns not in dataset_keys[relationship.to]):
            raise ValueError(f"Unverified relationship {relationship.name}")
        raise ValueError(
            f"Standard relationship {relationship.name} cannot be adapted without losing metadata; "
            "the platform profile supports associations in its model extension"
        )
    metrics: list[Metric] = []
    for metric in document.metrics or []:
        meta_metric = _profile(metric.custom_extensions, MetricCompatibility)
        formula = _sql(metric.expression)
        _validate_formula(formula, known_columns, metric.name)
        metrics.append(Metric(
            name=metric.name, formula_sql=formula, business_description=metric.description or "",
            source_table=meta_metric.source_table, aggregation=meta_metric.aggregation,
            derives_from=meta_metric.derives_from, uses_returns=meta_metric.uses_returns,
            assumption=meta_metric.assumption,
        ))
    _validate_dependencies(metrics)
    for association in root.associations:
        if (association.from_column not in known_columns.get(association.from_table, set())
                or association.to_column not in known_columns.get(association.to_table, set())):
            raise ValueError(f"Unknown association column: {association.name}")
    return SemanticLayerDocument(
        version=root.content_version, tables=tables, dimensions=dimensions, metrics=metrics,
        relationships=root.associations, source_sha256=root.source_sha256,
        semantic_source_sha256=root.semantic_source_sha256,
        generated_at=datetime.now(timezone.utc), assumptions=root.assumptions,
        provenance=SemanticModelProvenance(
            model_sha256=sha256(canonicalize_ossie(text).encode()).hexdigest(),
            ossie_revision=OSSIE_REVISION, source_sha256=root.source_sha256,
            semantic_source_sha256=root.semantic_source_sha256,
        ),
    )


def _validate_formula(formula: str, columns: dict[str, set[str]], name: str) -> None:
    try:
        statements = sqlglot.parse(f'SELECT {formula} FROM "Orders"', read="postgres")
    except (ParseError, TokenError, RecursionError) as exc:
        raise ValueError(f"Metric {name} contains invalid or unsupported SQL: {exc}") from exc
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        raise ValueError(f"Metric {name} must contain one scalar aggregate expression")
    tree = statements[0]
    if len(tree.expressions) != 1:
        raise ValueError(f"Metric {name} must contain exactly one expression")
    if any(value for key, value in tree.args.items() if key not in {"expressions", "from_"}):
        raise ValueError(f"Metric {name} must not alter the enclosing query")
    outer_source = tree.args.get("from_")
    if not isinstance(outer_source, exp.From) or outer_source.this != exp.to_table('"Orders"'):
        raise ValueError(f"Metric {name} must not replace the enclosing source")
    validation = validate_sql(
        tree.sql(dialect="postgres"),
        TableDef(name="Orders", description="Semantic reference validation", columns=[
            ColumnDef(name=column, logical_type=LogicalType.STRING, nullable=True)
            for column in columns["Orders"]
        ]),
    )
    if not validation.accepted:
        raise ValueError(f"Metric {name}: {validation.reason}")
    aliases = {"orders": "Orders"}
    for table in tree.find_all(exp.Table):
        match = next((key for key in columns if key.lower() == table.name.lower()), None)
        if match not in {"Orders", "Returns"} or table.db or table.catalog:
            raise ValueError(f"Metric {name} references unsupported table {table.name}")
        aliases[table.alias_or_name.lower()] = match
    for column in tree.find_all(exp.Column):
        table_name = aliases.get(column.table.lower(), "Orders") if column.table else "Orders"
        if column.table and column.table.lower() not in aliases:
            raise ValueError(f"Metric {name} references unknown qualifier {column.table}")
        if column.name not in columns[table_name]:
            raise ValueError(f"Metric {name} references unknown column {table_name}.{column.name}")
    if any(tree.find_all(exp.Star)):
        # COUNT(*) is valid; projections or qualified stars are not metric formulas.
        if not all(isinstance(star.parent, exp.Count) for star in tree.find_all(exp.Star)):
            raise ValueError(f"Metric {name} contains an unsupported star")


def _validate_dependencies(metrics: list[Metric]) -> None:
    by_name = {metric.name: metric for metric in metrics}
    visited: set[str] = set()
    active: set[str] = set()
    def visit(name: str) -> None:
        if name not in by_name:
            raise ValueError(f"Unknown metric dependency {name}")
        if name in active:
            raise ValueError(f"Cyclic metric dependency {name}")
        if name in visited:
            return
        active.add(name)
        metric = by_name[name]
        if metric.aggregation == "RATIO" and len(metric.derives_from) != 2:
            raise ValueError(f"Ratio {name} requires exactly two dependencies")
        for parent in metric.derives_from:
            visit(parent)
        active.remove(name)
        visited.add(name)
    for name in by_name:
        visit(name)
