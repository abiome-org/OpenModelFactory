"""Governed feedback staging; accepted records never auto-train or deploy."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from omf.canonical import sha256_digest
from omf.errors import ValidationError


@dataclass(frozen=True)
class FeedbackSpec:
    deployment_revision: str
    release_revision: str
    allowed_fields: frozenset[str]
    purpose: str
    rights_basis: str
    redacted_fields: frozenset[str] = frozenset()
    residency: str | None = None
    retention_days: int = 30
    filters: tuple[Callable[[dict[str, Any]], bool], ...] = ()


@dataclass(frozen=True)
class FeedbackDataset:
    revision: str
    records: tuple[dict[str, Any], ...]
    lineage: dict[str, str]
    approved_for_training: bool = False


class FeedbackCollector:
    def __init__(self, spec: FeedbackSpec) -> None:
        if not spec.purpose or not spec.rights_basis or spec.retention_days <= 0:
            raise ValidationError("feedback requires purpose, rights basis, and positive retention")
        self.spec = spec
        self._accepted: list[dict[str, Any]] = []
        self.rejections: list[dict[str, str]] = []

    def collect(
        self, record: dict[str, Any], *, consent: bool = True, residency: str | None = None
    ) -> bool:
        reason = None
        if set(record) - self.spec.allowed_fields:
            reason = "field_not_allowed"
        elif not consent:
            reason = "consent_or_rights_missing"
        elif self.spec.residency and residency != self.spec.residency:
            reason = "residency_mismatch"
        elif any(not check(record) for check in self.spec.filters):
            reason = "quality_or_poisoning_filter"
        if reason:
            # Deliberately retain no values, only a stable audit fingerprint.
            self.rejections.append({"reason": reason, "recordDigest": sha256_digest(record)})
            return False
        self._accepted.append(
            {
                key: "[REDACTED]" if key in self.spec.redacted_fields else value
                for key, value in record.items()
            }
        )
        return True

    def materialize(self) -> FeedbackDataset:
        records = tuple(dict(record) for record in self._accepted)
        revision = sha256_digest({"spec": self.spec.deployment_revision, "records": records})
        return FeedbackDataset(
            revision,
            records,
            {"deployment": self.spec.deployment_revision, "release": self.spec.release_revision},
        )
