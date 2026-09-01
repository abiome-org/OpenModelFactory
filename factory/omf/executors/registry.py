"""Trusted executor-provider discovery and fail-closed binding resolution."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from omf.errors import CapabilityError, ConfigurationError, ValidationError
from omf.executors.base import Executor
from omf.executors.kubernetes import KubernetesExecutor
from omf.executors.local import LocalExecutor
from omf.executors.slurm import SlurmExecutor

ENTRY_POINT_GROUP = "omf.executors"


@dataclass(frozen=True)
class ExecutorContext:
    """Stable project and desired-state context supplied to a trusted provider factory."""

    project_root: Path
    state_root: Path
    actor: str
    config: Mapping[str, Any]
    declaration: Mapping[str, Any]


ExecutorFactory = Callable[[ExecutorContext], Executor]


@dataclass(frozen=True)
class ExecutorProvider:
    """One named provider implementation and its agent-readable configuration contract."""

    name: str
    factory: ExecutorFactory = field(repr=False, compare=False)
    description: str = ""
    capabilities: frozenset[str] = frozenset()
    config_contract: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResolvedExecutor:
    provider: ExecutorProvider
    executor: Executor
    source: str
    config: dict[str, Any]


class ExecutorRegistry:
    """Explicit provider registry; unknown or ambiguous names never fall back to local."""

    def __init__(self) -> None:
        self._providers: dict[str, tuple[ExecutorProvider, str]] = {}

    def register(self, provider: ExecutorProvider, *, source: str = "runtime") -> None:
        name = provider.name.strip()
        if not name or name != provider.name:
            raise ConfigurationError("executor provider name must be non-empty and normalized")
        if name in self._providers:
            raise ConfigurationError(
                f"duplicate executor provider: {name}",
                details={
                    "existingSource": self._providers[name][1],
                    "duplicateSource": source,
                },
            )
        try:
            Draft202012Validator.check_schema(dict(provider.config_contract))
        except SchemaError as exc:
            raise ConfigurationError(
                f"executor provider {name!r} has an invalid config contract"
            ) from exc
        self._providers[name] = (provider, source)

    def discover(self) -> None:
        """Load trusted installed providers from the ``omf.executors`` entry-point group."""
        selected = metadata.entry_points().select(group=ENTRY_POINT_GROUP)
        for entry_point in sorted(selected, key=lambda item: (item.name, item.value)):
            try:
                loaded = entry_point.load()
            except Exception as exc:
                raise ConfigurationError(
                    f"executor entry point {entry_point.name!r} could not be loaded"
                ) from exc
            if not isinstance(loaded, ExecutorProvider):
                raise ConfigurationError(
                    f"executor entry point {entry_point.name!r} did not export ExecutorProvider"
                )
            if loaded.name != entry_point.name:
                raise ConfigurationError(
                    f"executor entry point name {entry_point.name!r} does not match provider "
                    f"name {loaded.name!r}"
                )
            distribution = getattr(entry_point, "dist", None)
            distribution_name = getattr(distribution, "name", "unknown")
            self.register(
                loaded,
                source=f"entry-point:{distribution_name}:{entry_point.value}",
            )

    def catalog(self) -> dict[str, Any]:
        providers = []
        for name, (provider, source) in sorted(self._providers.items()):
            providers.append(
                {
                    "name": name,
                    "source": source,
                    "description": provider.description,
                    "capabilities": sorted(provider.capabilities),
                    "configContract": dict(provider.config_contract),
                }
            )
        return {
            "apiVersion": "omf.executor/v1alpha1",
            "entryPointGroup": ENTRY_POINT_GROUP,
            "providers": providers,
        }

    def resolve(
        self,
        name: str,
        *,
        project_root: Path,
        state_root: Path,
        actor: str,
        declaration: Mapping[str, Any],
        config: Mapping[str, Any] | None = None,
    ) -> ResolvedExecutor:
        try:
            provider, source = self._providers[name]
        except KeyError as exc:
            raise CapabilityError(
                f"unknown executor provider: {name}",
                details={"requested": name, "available": sorted(self._providers)},
                remediation=[
                    {
                        "action": "executor.list",
                        "command": "omf executor list",
                        "description": "Choose an installed provider or install a trusted plugin.",
                    }
                ],
            ) from exc
        options = dict(config or {})
        reserved = {
            "argv",
            "run_dir",
            "cwd",
            "resources",
            "timeout",
            "deny_network",
            "requires_result",
        }
        conflicts = sorted(reserved & options.keys())
        if conflicts:
            raise ValidationError(
                "executor config contains controller-owned plan fields",
                details={"fields": conflicts},
            )
        errors = sorted(
            Draft202012Validator(dict(provider.config_contract)).iter_errors(options),
            key=lambda error: tuple(str(item) for item in error.absolute_path),
        )
        if errors:
            raise ValidationError(
                f"executor config failed the {name!r} provider contract",
                details={
                    "errors": [
                        {
                            "path": "$"
                            + "".join(
                                f"[{item}]" if isinstance(item, int) else f".{item}"
                                for item in error.absolute_path
                            ),
                            "constraint": str(error.validator),
                            "message": "value does not satisfy the provider contract",
                        }
                        for error in errors
                    ]
                },
            )
        context = ExecutorContext(
            project_root=project_root,
            state_root=state_root,
            actor=actor,
            config=deepcopy(options),
            declaration=deepcopy(declaration),
        )
        executor = provider.factory(context)
        if not isinstance(executor, Executor):
            raise ConfigurationError(f"executor provider {name!r} returned an invalid adapter")
        return ResolvedExecutor(provider, executor, source, options)

    @staticmethod
    def preflight(
        resolved: ResolvedExecutor, *, required_capabilities: frozenset[str] = frozenset()
    ) -> dict[str, Any]:
        actual = resolved.executor.capabilities
        missing = sorted(required_capabilities - actual)
        issues = list(resolved.executor.preflight())
        return {
            "provider": resolved.provider.name,
            "source": resolved.source,
            "ready": not missing and not issues,
            "capabilities": sorted(actual),
            "requiredCapabilities": sorted(required_capabilities),
            "missingCapabilities": missing,
            "issues": issues,
        }


def _local_provider(_context: ExecutorContext) -> Executor:
    return LocalExecutor()


def _kubernetes_provider(context: ExecutorContext) -> Executor:
    value = context.config.get("context")
    if value is not None and not isinstance(value, str):
        raise ValidationError("kubernetes executor context must be a string")
    return KubernetesExecutor(context=value)


def _slurm_provider(context: ExecutorContext) -> Executor:
    shared = context.config.get("sharedFilesystem", False)
    if not isinstance(shared, bool):
        raise ValidationError("slurm sharedFilesystem must be a boolean")
    spec = context.declaration.get("spec", {})
    if not isinstance(spec, Mapping):
        raise ValidationError("slurm binding spec must be an object")
    resources = spec.get("resources", {})
    placement = spec.get("placement", {})
    if not isinstance(resources, dict) or not isinstance(placement, dict):
        raise ValidationError("slurm binding resources and placement must be objects")
    return SlurmExecutor(
        shared_filesystem=shared,
        binding_resources=resources,
        placement=placement,
    )


def default_executor_registry(*, discover: bool = True) -> ExecutorRegistry:
    registry = ExecutorRegistry()
    registry.register(
        ExecutorProvider(
            "local",
            _local_provider,
            "Run modules as supervised local POSIX process groups.",
            LocalExecutor().capabilities,
            {"type": "object", "additionalProperties": False},
        ),
        source="builtin",
    )
    registry.register(
        ExecutorProvider(
            "kubernetes",
            _kubernetes_provider,
            "Kubernetes Job/JobSet lifecycle adapter; module transport is not built in.",
            KubernetesExecutor().capabilities,
            {
                "type": "object",
                "properties": {
                    "context": {"type": "string"},
                    "image": {"type": "string", "description": "Immutable image digest."},
                },
            },
        ),
        source="builtin",
    )
    registry.register(
        ExecutorProvider(
            "slurm",
            _slurm_provider,
            "Slurm lifecycle adapter; module transport requires an explicit shared filesystem.",
            SlurmExecutor(shared_filesystem=True).capabilities,
            {
                "type": "object",
                "properties": {"sharedFilesystem": {"type": "boolean"}},
                "additionalProperties": False,
            },
        ),
        source="builtin",
    )
    if discover:
        registry.discover()
    return registry
