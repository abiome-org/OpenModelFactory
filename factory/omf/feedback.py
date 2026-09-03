"""Governed feedback staging; accepted records never auto-train or deploy."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
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
    collected_by: str
    approved_for_training: bool = False


@dataclass(frozen=True)
class FeedbackTrainingExport:
    source_revision: str
    records: tuple[dict[str, Any], ...]
    approved_by: str


class FeedbackCollector:
    def __init__(self, spec: FeedbackSpec, *, collected_by: str) -> None:
        if not spec.purpose or not spec.rights_basis or spec.retention_days <= 0:
            raise ValidationError("feedback requires purpose, rights basis, and positive retention")
        if not collected_by.strip():
            raise ValidationError("feedback collector identity is required")
        self.spec = spec
        self.collected_by = collected_by
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
                key: "[REDACTED]" if key in self.spec.redacted_fields else deepcopy(value)
                for key, value in record.items()
            }
        )
        return True

    def materialize(self) -> FeedbackDataset:
        records = tuple(deepcopy(record) for record in self._accepted)
        lineage = {
            "deployment": self.spec.deployment_revision,
            "release": self.spec.release_revision,
        }
        revision = sha256_digest(
            {"lineage": lineage, "records": records, "collectedBy": self.collected_by}
        )
        return FeedbackDataset(
            revision,
            records,
            lineage,
            self.collected_by,
        )


def approve_and_export_for_training(
    dataset: FeedbackDataset, *, approver: str
) -> FeedbackTrainingExport:
    if not approver.strip() or approver == dataset.collected_by:
        raise ValidationError("feedback training export requires a different named approver")
    expected = sha256_digest(
        {
            "lineage": dataset.lineage,
            "records": dataset.records,
            "collectedBy": dataset.collected_by,
        }
    )
    if expected != dataset.revision:
        raise ValidationError("staged feedback revision failed integrity verification")
    return FeedbackTrainingExport(
        source_revision=dataset.revision,
        records=tuple(deepcopy(record) for record in dataset.records),
        approved_by=approver,
    )
