"""Bounded, regex-free Draft 2020-12 subset for untrusted evaluation schemas.

Supports types, literal enum/const, numeric/string/collection bounds, properties,
required/additionalProperties/propertyNames, items/prefixItems and boolean
combinators. Only acyclic #/$defs/name or #/definitions/name refs are supported;
refs are expanded under the same complexity budget before jsonschema sees them.
Formats, regex, recursive/dynamic refs, custom vocabularies, uniqueItems,
contains, conditionals and unevaluated/dependent schemas are deliberately absent.
"""
import math
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry

_KEYWORDS = {
    "$schema", "$defs", "definitions", "$ref", "title", "description", "$comment",
    "type", "enum", "const", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
    "multipleOf", "minLength", "maxLength", "minItems", "maxItems", "minProperties",
    "maxProperties", "required", "properties", "additionalProperties", "propertyNames",
    "items", "prefixItems", "allOf", "anyOf", "oneOf", "not",
}
_SINGLE_SCHEMAS = {"additionalProperties", "propertyNames", "items", "not"}
_SCHEMA_LISTS = {"prefixItems", "allOf", "anyOf", "oneOf"}
_DIALECT = "https://json-schema.org/draft/2020-12/schema"


def _bound_json(value: Any, *, schema: bool = False) -> None:
    """Iterative limits run before serialization, recursion or library validation."""
    max_depth, max_nodes, max_bytes, max_width = (16, 2048, 65536, 128) if schema else (32, 4096, 262144, 1024)
    stack = [(value, 0)]
    nodes = size = 0
    while stack:
        node, depth = stack.pop()
        nodes += 1
        size += 1
        if depth > max_depth or nodes > max_nodes:
            raise ValueError("JSON schema/input exceeds nesting or complexity limit")
        if isinstance(node, dict):
            if len(node) > max_width:
                raise ValueError("JSON schema/input exceeds property limit")
            for key, item in node.items():
                if not isinstance(key, str) or len(key) > max_bytes:
                    raise ValueError("JSON schema/input contains invalid or oversized keys")
                size += len(key.encode("utf-8"))
                stack.append((item, depth + 1))
        elif isinstance(node, list):
            if len(node) > max_width:
                raise ValueError("JSON schema/input exceeds collection limit")
            stack.extend((item, depth + 1) for item in node)
        elif isinstance(node, str):
            if len(node) > max_bytes:
                raise ValueError("JSON schema/input exceeds size limit")
            size += len(node.encode("utf-8"))
        elif type(node) is int:
            if node.bit_length() > 1024:
                raise ValueError("JSON schema/input exceeds numeric limit")
            size += (node.bit_length() + 7) // 8
        elif type(node) is float:
            if not math.isfinite(node):
                raise ValueError("JSON schema/input requires finite numbers")
        elif node is not None and type(node) is not bool:
            raise ValueError("JSON schema/input contains unsupported values")
        if size > max_bytes:
            raise ValueError("JSON schema/input exceeds size limit")


def schema_validator(schema: dict[str, Any]) -> Draft202012Validator:
    _bound_json(schema, schema=True)
    count = 0

    def expand(node, depth=0, active=()):
        nonlocal count
        count += 1
        if count > 256 or depth > 16:
            raise ValueError("JSON schema exceeds expanded complexity limit")
        if type(node) is bool:
            return node
        if not isinstance(node, dict):
            raise ValueError("JSON subschemas must be objects or booleans")
        if set(node) - _KEYWORDS:
            raise ValueError("Unsupported JSON schema keyword; regex and advanced schemas are disabled")
        if "$schema" in node and node["$schema"] not in {_DIALECT, _DIALECT + "#"}:
            raise ValueError("Only JSON Schema Draft 2020-12 is supported")
        result = {}
        for key, value in node.items():
            if key in {"$defs", "definitions", "properties"}:
                if not isinstance(value, dict):
                    raise ValueError("JSON schema definitions/properties must be objects")
                children = {name: expand(child, depth + 1, active) for name, child in value.items()}
                if key == "properties":
                    result[key] = children
            elif key in _SINGLE_SCHEMAS:
                result[key] = expand(value, depth + 1, active)
            elif key in _SCHEMA_LISTS:
                if not isinstance(value, list):
                    raise ValueError("JSON schema combinators/items must be lists")
                result[key] = [expand(child, depth + 1, active) for child in value]
            elif key != "$ref":
                result[key] = value
        if "$ref" in node:
            ref = node["$ref"]
            if not isinstance(ref, str) or not ref.startswith(("#/$defs/", "#/definitions/")):
                raise ValueError("Only local JSON schema definition references are supported")
            parts = ref.split("/")
            if len(parts) != 3 or ref in active:
                raise ValueError("JSON schema reference is recursive or unsupported")
            name = parts[2].replace("~1", "/").replace("~0", "~")
            definitions = schema.get(parts[1], {})
            if not isinstance(definitions, dict) or name not in definitions:
                raise ValueError("JSON schema reference target is missing")
            target = expand(definitions[name], depth + 1, (*active, ref))
            result = {"allOf": [result, target]}
        return result

    expanded = expand(schema)
    _bound_json(expanded, schema=True)
    Draft202012Validator.check_schema(expanded)
    return Draft202012Validator(expanded, registry=Registry())


def schema_matches(validator: Draft202012Validator, value: Any) -> bool:
    _bound_json(value)
    return validator.is_valid(value)
