"""Bundled JSON Schema registry for OMF v1alpha1 resources."""

from __future__ import annotations

import json
from copy import deepcopy
from importlib.resources import files
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from omf.canonical import load_document
from omf.errors import ValidationError
from omf.models import finalize_resource

API_VERSION = "omf.dev/v1alpha1"


class SchemaRegistry:
    """Discover and validate schemas bundled with the installed package."""

    def __init__(self) -> None:
        root = files("omf").joinpath("schemas")
        base = json.loads(root.joinpath("base.json").read_text(encoding="utf-8"))
        self._schemas: dict[str, dict[str, Any]] = {}
        for entry in root.iterdir():
            if entry.name.endswith(".json") and entry.name != "base.json":
                schema = json.loads(entry.read_text(encoding="utf-8"))
                schema["$defs"] = {**base["$defs"], **schema.get("$defs", {})}
                schema["properties"]["specDigest"] = {
                    "type": "string",
                    "pattern": "^sha256:[0-9a-f]{64}$",
                }
                kind = schema.get("x-omf-kind")
                if isinstance(kind, str):
                    self._schemas[kind] = schema

    @property
    def kinds(self) -> tuple[str, ...]:
        """Return registered kinds in stable order."""
        return tuple(sorted(self._schemas))

    def schema_for(self, kind: str) -> dict[str, Any]:
        """Return an independent copy of a kind's schema."""
        try:
            return deepcopy(self._schemas[kind])
        except KeyError as exc:
            raise ValidationError(f"unknown resource kind: {kind}") from exc

    def validate(self, resource: Any) -> dict[str, Any]:
        """Validate a resource and report every error with a JSON path."""
        if not isinstance(resource, dict):
            raise ValidationError("resource must be an object")
        if resource.get("apiVersion") != API_VERSION:
            raise ValidationError(f"unsupported apiVersion: {resource.get('apiVersion')!r}")
        schema = self.schema_for(str(resource.get("kind", "")))
        errors = sorted(
            Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(resource),
            key=lambda error: list(error.absolute_path),
        )
        if errors:
            details = {
                "errors": [
                    {
                        "path": "$"
                        + "".join(
                            f"[{item}]" if isinstance(item, int) else f".{item}"
                            for item in error.absolute_path
                        ),
                        "message": error.message,
                    }
                    for error in errors
                ]
            }
            raise ValidationError(
                f"resource failed validation ({len(errors)} error(s))", details=details
            )
        return deepcopy(resource)

    def validate_as(self, resource: Any, expected_kind: str) -> dict[str, Any]:
        """Validate a resource at a boundary that requires one exact kind."""
        value = self.validate(resource)
        if value["kind"] != expected_kind:
            raise ValidationError(f"expected {expected_kind} resource, received {value['kind']}")
        return value

    def load(self, data: str | bytes | Path) -> dict[str, Any]:
        """Load and validate YAML/JSON text or a path."""
        raw = data.read_bytes() if isinstance(data, Path) else data
        return self.validate(load_document(raw))

    def normalize(self, resource: dict[str, Any], *, actor: str, **kwargs: Any) -> dict[str, Any]:
        """Validate authoring input then return a finalized independent resource."""
        self.validate(resource)
        return finalize_resource(resource, actor=actor, **kwargs)


default_registry = SchemaRegistry()
