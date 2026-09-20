"""Frozen JSON Schemas for the request and result, and a structural compatibility check.

The frozen files under `docs/uc4/schema/` are what external consumers (a future MCP adapter, other
services) code against. `signature` reduces a JSON Schema to what matters for compatibility
(property names, types, required sets, enums, constraints) so the check is stable across Pydantic
versions and cosmetic changes, and `diff_signatures` classifies a difference as BREAKING (needs a
major version) or ADDITIVE (needs a minor version and a changelog entry).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .schemas import ClassificationRequest, ClassificationResult
from .schemas.common import SCHEMA_VERSION

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "docs" / "uc4" / "schema"
MODELS = {
    "classification-request": ClassificationRequest,
    "classification-result": ClassificationResult,
}
_CONSTRAINTS = (
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
    "minLength", "maxLength", "minItems", "maxItems", "pattern",
)  # fmt: skip


def frozen_filename(name: str, version: str = SCHEMA_VERSION) -> str:
    return f"{name}.v{version.split('.')[0]}.json"


def export_schema(name: str) -> dict[str, Any]:
    schema = MODELS[name].model_json_schema()
    schema["$id"] = f"https://dataguard.example/uc4/schema/{frozen_filename(name)}"
    schema["x-schema-version"] = SCHEMA_VERSION
    return schema


def _type_repr(prop: dict[str, Any]) -> Any:
    """A hashable-ish, cosmetic-free description of one property's type."""
    if "$ref" in prop:
        return {"ref": prop["$ref"].rsplit("/", 1)[-1]}
    out: dict[str, Any] = {}
    for key in ("type", "const", "enum", "format"):
        if key in prop:
            out[key] = prop[key]
    for key in _CONSTRAINTS:
        if key in prop:
            out[key] = prop[key]
    for key in ("anyOf", "oneOf", "allOf"):
        if key in prop:
            out[key] = sorted(
                (_type_repr(p) for p in prop[key]), key=lambda x: json.dumps(x, sort_keys=True)
            )
    if "items" in prop:
        out["items"] = _type_repr(prop["items"])
    if "additionalProperties" in prop:
        ap = prop["additionalProperties"]
        out["additionalProperties"] = ap if isinstance(ap, bool) else _type_repr(ap)
    if "prefixItems" in prop:
        out["prefixItems"] = [_type_repr(p) for p in prop["prefixItems"]]
    return out


def _object_sig(schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "properties": {k: _type_repr(v) for k, v in sorted(schema.get("properties", {}).items())},
        "required": sorted(schema.get("required", [])),
        "additionalProperties": schema.get("additionalProperties", True),
    }


def signature(schema: dict[str, Any]) -> dict[str, Any]:
    """Root and every `$defs` model, by name."""
    sig = {"$root": _object_sig(schema)}
    for name, sub in sorted(schema.get("$defs", {}).items()):
        if sub.get("type") == "object" or "properties" in sub:
            sig[name] = _object_sig(sub)
        else:  # enums and other scalar definitions
            sig[name] = _type_repr(sub)
    return sig


def digest(schema: dict[str, Any]) -> str:
    blob = json.dumps(signature(schema), sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def diff_signatures(frozen: dict[str, Any], current: dict[str, Any]) -> tuple[list[str], list[str]]:
    """(breaking, additive) differences going from the frozen schema to the current models."""
    breaking: list[str] = []
    additive: list[str] = []
    for model in sorted(set(frozen) | set(current)):
        if model not in current:
            breaking.append(f"{model}: model removed")
            continue
        if model not in frozen:
            additive.append(f"{model}: new model")
            continue
        f, c = frozen[model], current[model]
        if "properties" not in f or "properties" not in c:
            if f != c:
                breaking.append(f"{model}: definition changed")
            continue
        for prop in sorted(set(f["properties"]) | set(c["properties"])):
            if prop not in c["properties"]:
                breaking.append(f"{model}.{prop}: property removed")
            elif prop not in f["properties"]:
                (breaking if prop in c["required"] else additive).append(
                    f"{model}.{prop}: property added"
                    + (" (REQUIRED)" if prop in c["required"] else "")
                )
            elif f["properties"][prop] != c["properties"][prop]:
                breaking.append(f"{model}.{prop}: type or constraints changed")
        for prop in sorted(set(c["required"]) - set(f["required"])):
            if prop in f["properties"]:
                breaking.append(f"{model}.{prop}: became required")
        for prop in sorted(set(f["required"]) - set(c["required"])):
            if prop in c["properties"]:
                additive.append(f"{model}.{prop}: no longer required")
        if f["additionalProperties"] != c["additionalProperties"]:
            breaking.append(f"{model}: additionalProperties changed")
    return breaking, additive


def write_frozen(out_dir: Path | str = SCHEMA_DIR) -> list[Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for name in MODELS:
        path = out / frozen_filename(name)
        path.write_text(
            json.dumps(export_schema(name), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        written.append(path)
    return written


def check_frozen(schema_dir: Path | str = SCHEMA_DIR) -> dict[str, dict[str, Any]]:
    """Compare the frozen files with the current models. Returns per-schema findings."""
    report: dict[str, dict[str, Any]] = {}
    for name in MODELS:
        path = Path(schema_dir) / frozen_filename(name)
        if not path.exists():
            report[name] = {"missing": True, "breaking": [], "additive": []}
            continue
        frozen = json.loads(path.read_text(encoding="utf-8"))
        breaking, additive = diff_signatures(signature(frozen), signature(export_schema(name)))
        report[name] = {
            "missing": False,
            "breaking": breaking,
            "additive": additive,
            "frozen_digest": digest(frozen),
            "current_digest": digest(export_schema(name)),
            "frozen_version": frozen.get("x-schema-version"),
        }
    return report
