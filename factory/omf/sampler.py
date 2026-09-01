"""Worker-count independent SHA-256 counter sampler."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from omf.errors import ValidationError


@dataclass(frozen=True)
class Source:
    revision: str
    weight: float
    size: int | None = None


@dataclass(frozen=True)
class MixSpec:
    revision: str
    sources: tuple[Source, ...]
    seed: str
    replacement: bool = True
    exhaustion: Literal["stop", "error", "wrap"] = "error"
    normalization: Literal["sum"] = "sum"

    def __post_init__(self) -> None:
        if (
            not self.sources
            or any(s.weight < 0 for s in self.sources)
            or sum(s.weight for s in self.sources) <= 0
        ):
            raise ValidationError("source weights must be non-negative with a positive sum")
        if len({s.revision for s in self.sources}) != len(self.sources):
            raise ValidationError("source revisions must be unique")


@dataclass
class Lease:
    worker: str
    start: int
    end: int
    acknowledged: bool = False


@dataclass
class SamplerState:
    mix_revision: str
    cursor: int = 0
    algorithm: str = "sha256-counter-v1"
    source_cursors: dict[str, int] = field(default_factory=dict)
    leases: list[Lease] = field(default_factory=list)
    amendments: list[dict[str, object]] = field(default_factory=list)
    redistribution_history: list[dict[str, object]] = field(default_factory=list)
    observed: dict[str, int] = field(default_factory=dict)
    delivery: str = "exact"

    def save(self, path: Path) -> None:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), sort_keys=True, separators=(",", ":")))
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path) -> SamplerState:
        value = json.loads(path.read_text())
        value["leases"] = [Lease(**x) for x in value["leases"]]
        return cls(**value)


def _amendment_index(amendment: dict[str, object]) -> int:
    value = amendment.get("effective_index")
    if not isinstance(value, int):
        raise ValidationError("invalid sampler amendment state")
    return value


class DeterministicSampler:
    def __init__(self, mix: MixSpec, state: SamplerState | None = None) -> None:
        self.mixes = {mix.revision: mix}
        self.state = state or SamplerState(mix.revision)

    def amend(self, mix: MixSpec, effective_index: int) -> None:
        if effective_index < self.state.cursor:
            raise ValidationError("amendment boundary is already consumed")
        self.mixes[mix.revision] = mix
        self.state.amendments.append({"revision": mix.revision, "effective_index": effective_index})
        self.state.amendments.sort(key=_amendment_index)

    def _mix(self, index: int) -> MixSpec:
        revision = self.state.mix_revision
        for amendment in self.state.amendments:
            amended_revision = amendment.get("revision")
            if not isinstance(amended_revision, str):
                raise ValidationError("invalid sampler amendment state")
            if index >= _amendment_index(amendment):
                revision = amended_revision
        return self.mixes[revision]

    def sample(self, index: int) -> str:
        mix = self._mix(index)
        number = (
            int.from_bytes(hashlib.sha256(f"{mix.seed}:{index}".encode()).digest(), "big") / 2**256
        )
        total = sum(source.weight for source in mix.sources)
        point = number * total
        selected = mix.sources[-1]
        running = 0.0
        for source in mix.sources:
            running += source.weight
            if point < running:
                selected = source
                break
        cursor = self.state.source_cursors.get(selected.revision, 0)
        if not mix.replacement and selected.size is not None and cursor >= selected.size:
            if mix.exhaustion == "wrap":
                cursor %= selected.size
            elif mix.exhaustion == "stop":
                raise StopIteration
            else:
                raise ValidationError(f"source exhausted: {selected.revision}")
        return selected.revision

    def lease(self, worker: str, count: int) -> Lease:
        lease = Lease(worker, self.state.cursor, self.state.cursor + count)
        self.state.cursor += count
        self.state.leases.append(lease)
        return lease

    def consume(self, lease: Lease) -> list[str]:
        values = [self.sample(i) for i in range(lease.start, lease.end)]
        for revision in values:
            self.state.source_cursors[revision] = self.state.source_cursors.get(revision, 0) + 1
            self.state.observed[revision] = self.state.observed.get(revision, 0) + 1
        return values

    def acknowledge(self, lease: Lease) -> None:
        lease.acknowledged = True

    def redistribute(self, workers: list[str]) -> None:
        self.state.redistribution_history.append({"index": self.state.cursor, "workers": workers})

    def observed_distribution(self) -> dict[str, float]:
        total = sum(self.state.observed.values())
        return {key: value / total for key, value in self.state.observed.items()} if total else {}
