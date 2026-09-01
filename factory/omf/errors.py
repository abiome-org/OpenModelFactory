"""Domain errors with stable machine-readable codes."""

from __future__ import annotations

from typing import Any


class OMFError(Exception):
    """Base error presented consistently by the CLI and HTTP API."""

    code = "omf_error"
    status_code = 400

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def as_dict(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message, "details": self.details}}


class ConfigurationError(OMFError):
    code = "configuration_error"


class ValidationError(OMFError):
    code = "validation_error"


class NotFoundError(OMFError):
    code = "not_found"
    status_code = 404


class ConflictError(OMFError):
    code = "conflict"
    status_code = 409


class AuthorizationError(OMFError):
    code = "forbidden"
    status_code = 403


class IntegrityError(OMFError):
    code = "integrity_error"
    status_code = 422


class CapabilityError(OMFError):
    code = "capability_mismatch"
    status_code = 422


class ExternalSystemError(OMFError):
    code = "external_system_error"
    status_code = 502
