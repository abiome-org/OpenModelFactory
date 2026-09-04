from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import typer
import uvicorn
import yaml

from omf import __version__
from omf.agent import capability_catalog, initial_context
from omf.api import create_app
from omf.backups import restore_backup
from omf.config import ProjectPaths, discover_project
from omf.config import bootstrap as bootstrap_project
from omf.errors import OMFError
from omf.factory import Factory
from omf.modules import scaffold_module
from omf.schema_registry import default_registry

app = typer.Typer(no_args_is_help=True, pretty_exceptions_show_locals=False)
module_app = typer.Typer(no_args_is_help=True)
data_app = typer.Typer(no_args_is_help=True)
store_app = typer.Typer(no_args_is_help=True)
sync_app = typer.Typer(no_args_is_help=True)
resource_app = typer.Typer(no_args_is_help=True)
schema_app = typer.Typer(no_args_is_help=True)
runs_app = typer.Typer(no_args_is_help=True)
lineage_app = typer.Typer(no_args_is_help=True)
secret_app = typer.Typer(no_args_is_help=True)
api_app = typer.Typer(no_args_is_help=True)
release_app = typer.Typer(no_args_is_help=True)
experiment_app = typer.Typer(no_args_is_help=True)
deployment_app = typer.Typer(no_args_is_help=True)
operation_app = typer.Typer(no_args_is_help=True)
token_app = typer.Typer(no_args_is_help=True)
admin_app = typer.Typer(no_args_is_help=True)
agent_app = typer.Typer(no_args_is_help=True)
goal_app = typer.Typer(no_args_is_help=True)
knowledge_app = typer.Typer(no_args_is_help=True)
executor_app = typer.Typer(no_args_is_help=True)

app.add_typer(module_app, name="module")
app.add_typer(data_app, name="data")
app.add_typer(store_app, name="store")
app.add_typer(sync_app, name="sync")
app.add_typer(resource_app, name="resource")
app.add_typer(schema_app, name="schema")
app.add_typer(runs_app, name="runs")
app.add_typer(lineage_app, name="lineage")
app.add_typer(api_app, name="api")
app.add_typer(release_app, name="release")
app.add_typer(experiment_app, name="experiment")
app.add_typer(deployment_app, name="deployment")
app.add_typer(operation_app, name="operation")
app.add_typer(admin_app, name="admin")
admin_app.add_typer(token_app, name="token")
admin_app.add_typer(secret_app, name="secret")
app.add_typer(agent_app, name="agent")
app.add_typer(goal_app, name="goal")
app.add_typer(knowledge_app, name="knowledge")
app.add_typer(executor_app, name="executor")


class State:
    project: Path | None = None
    output: str = "table"
    actor: str = "local-user"


state = State()


def _version(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def main(
    project: Path | None = typer.Option(None, "--project", "-p", help="OMF project directory"),
    output: str = typer.Option("table", "--output", "-o", help="table, json, or yaml"),
    actor: str = typer.Option("local-user", "--actor", help="Attributable local actor"),
    version: bool = typer.Option(False, "--version", callback=_version, is_eager=True),
) -> None:
    del version
    if output not in {"table", "json", "yaml"}:
        raise typer.BadParameter("output must be table, json, or yaml")
    state.project, state.output, state.actor = project, output, actor


def _paths() -> ProjectPaths:
    return discover_project(state.project)


@contextmanager
def _factory() -> Iterator[Factory]:
    factory = Factory(_paths(), actor=state.actor)
    try:
        yield factory
    finally:
        factory.close()


def _row(item: dict[str, Any]) -> dict[str, Any]:
    metadata = item.get("metadata")
    if isinstance(metadata, dict) and "kind" in item:
        return {
            "kind": item["kind"],
            "name": metadata.get("name"),
            "revision": str(metadata.get("revision", ""))[:19],
            "createdAt": metadata.get("createdAt"),
        }
    return {key: value for key, value in item.items() if not isinstance(value, dict)}


def _table(rows: list[dict[str, Any]]) -> str:
    columns = list(dict.fromkeys(key for row in rows for key in row))
    cells = [[str(row.get(column, "")) for column in columns] for row in rows]
    widths = [
        max(len(column), *(len(line[index]) for line in cells))
        for index, column in enumerate(columns)
    ]
    lines = [columns, *cells]
    return "\n".join(
        "  ".join(cell.ljust(width) for cell, width in zip(line, widths, strict=True)).rstrip()
        for line in lines
    )


def _emit(value: Any) -> None:
    if state.output == "json":
        typer.echo(json.dumps(value, sort_keys=True, indent=2, default=str))
    elif isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
        if state.output == "yaml":
            typer.echo(yaml.safe_dump(value, sort_keys=True), nl=False)
        else:
            typer.echo(_table([_row(item) for item in value]))
    elif isinstance(value, dict | list):
        typer.echo(yaml.safe_dump(value, sort_keys=True), nl=False)
    else:
        typer.echo(value)


def _load_value(path: Path) -> Any:
    return yaml.safe_load(path.read_text())


def _handle(function: Any) -> None:
    try:
        _emit(function())
    except OMFError as exc:
        if state.output in {"json", "yaml"}:
            _emit(exc.as_dict())
        else:
            typer.secho(f"{exc.code}: {exc.message}", fg="red", err=True)
            if exc.details:
                typer.echo(yaml.safe_dump(exc.details, sort_keys=True), nl=False, err=True)
            for item in exc.remediation:
                typer.echo(f"next: {item.get('command', item['description'])}", err=True)
        raise typer.Exit(code=1) from exc


@app.command()
def bootstrap(
    profile: str = typer.Option("local", help="Bootstrap profile"),
    plan: bool = typer.Option(False, "--plan", "--dry-run", help="Show changes only"),
) -> None:
    _handle(lambda: bootstrap_project(_paths(), profile=profile, plan=plan))


@app.command()
def doctor() -> None:

    def run() -> dict[str, Any]:
        with _factory() as factory:
            return factory.doctor()

    _handle(run)


@executor_app.command("list")
def executor_list() -> None:

    def run() -> dict[str, Any]:
        with _factory() as factory:
            return factory.executor_catalog()

    _handle(run)


@executor_app.command("preflight")
def executor_preflight(
    binding: Path,
    workload: Path | None = typer.Option(None, "--workload"),
) -> None:

    def run() -> dict[str, Any]:
        with _factory() as factory:
            return factory.executor_preflight(binding, workload_path=workload)

    _handle(run)


@agent_app.command("capabilities")
def agent_capabilities() -> None:
    _handle(capability_catalog)


@agent_app.command("context")
def agent_context(
    focus: str | None = typer.Option(None, "--focus", help="Filter bounded detail by term"),
    limit: int = typer.Option(20, "--limit", min=1, max=100),
    since: str | None = typer.Option(None, "--since", help="Increment from an event cursor"),
    max_bytes: int = typer.Option(65_536, "--max-bytes", min=16_384, max=1_048_576),
) -> None:

    def run() -> dict[str, Any]:
        paths = _paths()
        if not paths.database.exists():
            return initial_context(
                paths, focus=focus, limit=limit, since=since, max_bytes=max_bytes
            )
        with Factory(paths, actor=state.actor) as factory:
            return factory.agent.context(focus=focus, limit=limit, since=since, max_bytes=max_bytes)

    _handle(run)


def _budget_values(values: list[str] | None) -> dict[str, float]:
    result: dict[str, float] = {}
    for value in values or []:
        key, separator, raw = value.partition("=")
        if not separator or not key:
            raise typer.BadParameter("--budget must be KEY=NUMBER")
        try:
            parsed = float(raw)
        except ValueError as exc:
            raise typer.BadParameter("--budget must be KEY=NUMBER") from exc
        if parsed < 0:
            raise typer.BadParameter("--budget values cannot be negative")
        result[key] = parsed
    return result


@goal_app.command("create")
def goal_create(
    name: str,
    objective: str = typer.Option(..., "--objective"),
    success: list[str] | None = typer.Option(None, "--success"),
    constraint: list[str] | None = typer.Option(None, "--constraint"),
    budget: list[str] | None = typer.Option(None, "--budget", help="KEY=NUMBER; repeatable"),
    priority: int = typer.Option(50, "--priority", min=0, max=100),
    parent_ref: str | None = typer.Option(None, "--parent-ref"),
    resource_ref: list[str] | None = typer.Option(None, "--resource-ref"),
    run_id: list[str] | None = typer.Option(None, "--run-id"),
) -> None:

    def run() -> dict[str, Any]:
        with _factory() as factory:
            return factory.agent.create_goal(
                name,
                objective=objective,
                success_criteria=success or [],
                constraints=constraint,
                budget=_budget_values(budget),
                priority=priority,
                parent_ref=parent_ref,
                scope={"resourceRefs": resource_ref or [], "runIds": run_id or []},
            )

    _handle(run)


@goal_app.command("list")
def goal_list(
    state_filter: str | None = typer.Option(None, "--state"),
    focus: str | None = typer.Option(None, "--focus"),
    limit: int = typer.Option(20, "--limit", min=1, max=100),
) -> None:
    def run() -> dict[str, Any]:
        with _factory() as factory:
            return factory.agent.list_goals(state=state_filter, focus=focus, limit=limit)

    _handle(run)


@goal_app.command("status")
def goal_status(
    name: str,
    status_state: str = typer.Option(..., "--state"),
    expected_version: int = typer.Option(..., "--expected-version", min=0),
    reason: str = typer.Option(..., "--reason"),
) -> None:

    def run() -> dict[str, Any]:
        with _factory() as factory:
            return factory.agent.set_goal_status(
                name,
                state=status_state,
                expected_version=expected_version,
                reason=reason,
            )

    _handle(run)


@knowledge_app.command("record")
def knowledge_record(
    name: str,
    category: str = typer.Option(..., "--category"),
    claim: str = typer.Option(..., "--claim"),
    confidence: float = typer.Option(..., "--confidence", min=0, max=1),
    evidence: list[str] | None = typer.Option(None, "--evidence"),
    goal_ref: list[str] | None = typer.Option(None, "--goal-ref"),
    resource_ref: list[str] | None = typer.Option(None, "--resource-ref"),
    run_id: list[str] | None = typer.Option(None, "--run-id"),
    tag: list[str] | None = typer.Option(None, "--tag"),
    supersedes: list[str] | None = typer.Option(None, "--supersedes"),
    expires_at: str | None = typer.Option(None, "--expires-at"),
) -> None:

    def run() -> dict[str, Any]:
        with _factory() as factory:
            return factory.agent.record_knowledge(
                name,
                category=category,
                claim=claim,
                confidence=confidence,
                evidence=[{"ref": item} for item in evidence or []],
                scope={
                    "goalRefs": goal_ref or [],
                    "resourceRefs": resource_ref or [],
                    "runIds": run_id or [],
                    "tags": tag or [],
                },
                supersedes=supersedes,
                expires_at=expires_at,
            )

    _handle(run)


@knowledge_app.command("list")
def knowledge_list(
    include_inactive: bool = typer.Option(False, "--all"),
    focus: str | None = typer.Option(None, "--focus"),
    limit: int = typer.Option(20, "--limit", min=1, max=100),
) -> None:
    def run() -> dict[str, Any]:
        with _factory() as factory:
            return factory.agent.list_knowledge(
                active_only=not include_inactive, focus=focus, limit=limit
            )

    _handle(run)


@schema_app.command("list")
def schema_list() -> None:
    _emit({"apiVersion": "omf.dev/v1alpha1", "kinds": default_registry.kinds})


@schema_app.command("show")
def schema_show(kind: str) -> None:
    _handle(lambda: default_registry.schema_for(kind))


@schema_app.command("validate")
def schema_validate(path: Path) -> None:
    _handle(lambda: default_registry.load(path))


@resource_app.command("apply")
def resource_apply(path: Path) -> None:
    def run() -> dict[str, Any]:
        with _factory() as factory:
            return factory.apply_resource_file(path)

    _handle(run)


@resource_app.command("list")
def resource_list(kind: str | None = typer.Option(None)) -> None:
    def run() -> list[dict[str, Any]]:
        with _factory() as factory:
            return factory.list_resources(kind=kind)

    _handle(run)


def _module_paths(path: Path | None) -> list[Path]:
    if path is not None:
        return [path]
    paths = sorted(_paths().root.glob("modules/**/module.yaml"))
    if not paths:
        raise typer.BadParameter("no modules/**/module.yaml manifests found")
    return paths


@module_app.command("init")
def module_init(directory: Path, name: str | None = typer.Option(None, "--name")) -> None:
    def run() -> dict[str, Any]:
        with _factory() as factory:
            return factory.validate_module(scaffold_module(directory, name))

    _handle(run)


@module_app.command("validate")
def module_validate(path: Path | None = typer.Argument(None)) -> None:
    def run() -> list[dict[str, Any]]:
        with _factory() as factory:
            return [factory.validate_module(item) for item in _module_paths(path)]

    _handle(run)


@module_app.command("test")
def module_test(
    path: Path | None = typer.Argument(None),
    binding: Path | None = typer.Option(
        None,
        "--binding",
        help="Binding whose executor and dependency options run the fixtures (default: local).",
    ),
) -> None:
    def run() -> list[dict[str, Any]]:
        with _factory() as factory:
            return [factory.test_module(item, binding_path=binding) for item in _module_paths(path)]

    _handle(run)


@data_app.command("add")
def data_add(
    source: str,
    name: str = typer.Option(..., "--name"),
    mode: str = typer.Option("copy", "--mode"),
    sample_schema: str = typer.Option("application/octet-stream", "--sample-schema"),
    cursor: str | None = typer.Option(None, "--cursor"),
    rights: Path | None = typer.Option(None, "--rights", help="YAML/JSON rights declaration"),
) -> None:
    def run() -> dict[str, Any]:
        rights_value: dict[str, Any] | None = None
        if rights is not None:
            loaded = yaml.safe_load(rights.read_text())
            if not isinstance(loaded, dict):
                raise typer.BadParameter("--rights must contain a YAML/JSON object")
            rights_value = loaded
        with _factory() as factory:
            return factory.add_data(
                source,
                name=name,
                mode=mode,
                rights=rights_value,
                sample_schema=sample_schema,
                cursor_policy={"cursor": cursor} if cursor else None,
            )

    _handle(run)


@data_app.command("list")
def data_list() -> None:
    def run() -> list[dict[str, Any]]:
        with _factory() as factory:
            return factory.list_resources(kind="DatasetSnapshot")

    _handle(run)


@data_app.command("verify")
def data_verify(name: str) -> None:
    def run() -> dict[str, Any]:
        with _factory() as factory:
            return {"name": name, "valid": factory.verify_data(name)}

    _handle(run)


@data_app.command("revoke")
def data_revoke(
    name: str,
    reason: str = typer.Option(..., "--reason", help="Non-sensitive revocation reason"),
) -> None:
    def run() -> dict[str, Any]:
        with _factory() as factory:
            return factory.revoke_data(name, reason=reason)

    _handle(run)


@store_app.command("add")
def store_add(
    name: str,
    driver: str = typer.Option(..., "--driver"),
    endpoint: str = typer.Option(..., "--endpoint"),
    secret_ref: str | None = typer.Option(None, "--secret-ref"),
    plan: bool = typer.Option(False, "--plan", "--dry-run"),
) -> None:
    def run() -> dict[str, Any]:
        with _factory() as factory:
            return factory.add_store(
                name,
                driver=driver,
                endpoint=endpoint,
                secret_ref=secret_ref,
                plan=plan,
            )

    _handle(run)


@store_app.command("list")
def store_list() -> None:
    def run() -> list[dict[str, Any]]:
        with _factory() as factory:
            return factory.list_resources(kind="ArtifactStore")

    _handle(run)


def _sync(
    asset: str,
    source: str,
    destination: str,
    direction: str,
    concurrency: int,
    plan: bool,
) -> dict[str, Any]:
    with _factory() as factory:
        return factory.sync(
            asset,
            source=source,
            destination=destination,
            direction=direction,
            concurrency=concurrency,
            plan=plan,
        )


@sync_app.command("push")
def sync_push(
    asset: str,
    destination: str = typer.Option(..., "--to"),
    source: str = typer.Option("local", "--from"),
    concurrency: int = typer.Option(4, min=1, max=256),
    plan: bool = typer.Option(False, "--plan", "--dry-run"),
) -> None:
    _handle(lambda: _sync(asset, source, destination, "push", concurrency, plan))


@sync_app.command("pull")
def sync_pull(
    asset: str,
    source: str = typer.Option(..., "--from"),
    destination: str = typer.Option("local", "--to"),
    concurrency: int = typer.Option(4, min=1, max=256),
    plan: bool = typer.Option(False, "--plan", "--dry-run"),
) -> None:
    _handle(lambda: _sync(asset, destination, source, "pull", concurrency, plan))


@app.command("run")
def run_workload(
    workload: Path,
    binding: Path = typer.Option(Path("bindings/local.yaml"), "--binding"),
    detach: bool = typer.Option(False, "--detach"),
) -> None:
    def run() -> dict[str, Any]:
        with _factory() as factory:
            if detach:
                operation = factory.create_run_operation(workload, binding)
                log_path = factory.paths.state / "operations" / f"{operation['id']}.log"
                with log_path.open("ab") as log:
                    subprocess.Popen(
                        [
                            sys.executable,
                            "-m",
                            "omf.run_worker",
                            "--project",
                            str(factory.paths.root),
                            "--operation",
                            operation["id"],
                        ],
                        stdin=subprocess.DEVNULL,
                        stdout=log,
                        stderr=log,
                        close_fds=True,
                        start_new_session=True,
                    )
                return operation
            return factory.run(workload, binding)

    _handle(run)


@runs_app.command("list")
def runs_list() -> None:
    def run() -> list[dict[str, Any]]:
        with _factory() as factory:
            return factory.list_runs()

    _handle(run)


@runs_app.command("status")
def runs_status(run_id: str) -> None:
    def run() -> dict[str, Any]:
        with _factory() as factory:
            return factory.run_status(run_id.removeprefix("run/"))

    _handle(run)


@app.command("evaluate")
def evaluate(subject: str) -> None:

    def run() -> dict[str, Any]:
        with _factory() as factory:
            return factory.evaluate(subject)

    _handle(run)


@release_app.command("create")
def release_create(
    run_id: str,
    name: str = typer.Option(..., "--name"),
    intended_use: str = typer.Option(..., "--intended-use"),
    limitation: list[str] | None = typer.Option(None, "--limitation"),
    promote: bool = typer.Option(False, "--promote"),
    alias: str = typer.Option("candidate", "--alias"),
    approval: list[str] | None = typer.Option(None, "--approval"),
    vulnerability_report: Path | None = typer.Option(None, "--vulnerability-report"),
    evaluation: str | None = typer.Option(None, "--evaluation"),
) -> None:
    def run() -> dict[str, Any]:
        with _factory() as factory:
            return factory.create_release(
                run_id,
                name=name,
                intended_use=intended_use,
                limitations=limitation,
                promote=promote,
                alias=alias,
                approvals=approval,
                vulnerability_report=vulnerability_report,
                evaluation_ref=evaluation,
            )

    _handle(run)


@release_app.command("evidence")
def release_evidence(run_id: str) -> None:
    def run() -> dict[str, Any]:
        with _factory() as factory:
            return factory.release_evidence(run_id)

    _handle(run)


@release_app.command("list")
def release_list() -> None:
    def run() -> list[dict[str, Any]]:
        with _factory() as factory:
            return factory.list_releases()

    _handle(run)


@release_app.command("show")
def release_show(name: str) -> None:
    def run() -> dict[str, Any]:
        with _factory() as factory:
            return factory.show_release(name.removeprefix("release/"))

    _handle(run)


@experiment_app.command("create")
def experiment_create(
    name: str,
    baseline: str = typer.Option(..., "--baseline"),
    candidate: str = typer.Option(..., "--candidate"),
    metric: str = typer.Option(..., "--metric"),
    direction: str = typer.Option("maximize", "--direction"),
) -> None:
    def run() -> dict[str, Any]:
        with _factory() as factory:
            return factory.create_experiment(
                name=name,
                baseline_ref=baseline,
                candidate_ref=candidate,
                metric=metric,
                direction=direction,
            )

    _handle(run)


@app.command("deploy")
def deploy(path: Path) -> None:

    def run() -> dict[str, Any]:
        with _factory() as factory:
            return factory.deploy(path)

    _handle(run)


@deployment_app.command("list")
def deployment_list() -> None:
    def run() -> list[dict[str, Any]]:
        with _factory() as factory:
            return factory.list_deployments()

    _handle(run)


@deployment_app.command("status")
def deployment_status(name: str) -> None:
    def run() -> dict[str, Any]:
        with _factory() as factory:
            return factory.deployment_status(name)

    _handle(run)


@deployment_app.command("cancel")
def deployment_cancel(name: str) -> None:
    def run() -> dict[str, Any]:
        with _factory() as factory:
            return factory.cancel_deployment(name)

    _handle(run)


@deployment_app.command("rollback")
def deployment_rollback(
    name: str, expected_version: int = typer.Option(..., "--expected-version", min=1)
) -> None:
    def run() -> dict[str, Any]:
        with _factory() as factory:
            return factory.rollback_deployment(name, expected_version=expected_version)

    _handle(run)


@operation_app.command("list")
def operation_list(state_filter: str | None = typer.Option(None, "--state")) -> None:
    def run() -> list[dict[str, Any]]:
        with _factory() as factory:
            return factory.operations.list(state=state_filter)

    _handle(run)


@operation_app.command("get")
def operation_get(operation_id: str) -> None:
    def run() -> dict[str, Any]:
        with _factory() as factory:
            return factory.operations.get(operation_id)

    _handle(run)


@operation_app.command("reconcile")
def operation_reconcile(operation_id: str) -> None:

    def run() -> dict[str, Any]:
        with _factory() as factory:
            return factory.execute_run_operation(operation_id)

    _handle(run)


@token_app.command("create")
def token_create(
    actor: str = typer.Option(..., "--actor"),
    scope: list[str] | None = typer.Option(None, "--scope"),
    expires_at: str | None = typer.Option(None, "--expires-at"),
) -> None:

    def run() -> dict[str, Any]:
        with _factory() as factory:
            token, principal = factory.api_tokens.create(
                actor=actor, scopes=set(scope or ["read"]), expires_at=expires_at
            )
        return {
            "token": token,
            "tokenId": principal.token_id,
            "actor": principal.actor,
            "scopes": sorted(principal.scopes),
            "expiresAt": principal.expires_at,
        }

    _handle(run)


@token_app.command("list")
def token_list() -> None:
    def run() -> list[dict[str, Any]]:
        with _factory() as factory:
            return factory.api_tokens.list()

    _handle(run)


@token_app.command("revoke")
def token_revoke(token_id: str) -> None:
    def run() -> dict[str, Any]:
        with _factory() as factory:
            factory.revoke_api_token(token_id)
        return {"tokenId": token_id, "revoked": True}

    _handle(run)


@lineage_app.command("show")
def lineage_show(
    subject: str,
    direction: str = typer.Option("upstream"),
    max_depth: int = typer.Option(100, min=1, max=1000),
) -> None:
    def run() -> list[dict[str, Any]]:
        with _factory() as factory:
            return factory.lineage_query(subject, direction=direction, max_depth=max_depth)

    _handle(run)


@secret_app.command("set")
def secret_set(
    name: str,
    purpose: str = typer.Option(...),
    value: str = typer.Option(...),
    expected_version: int | None = typer.Option(None, "--expected-version", min=1),
) -> None:
    def run() -> dict[str, Any]:
        with _factory() as factory:
            version = factory.secrets.put(name, value, purpose, expected_version=expected_version)
            return {"name": name, "purpose": purpose, "version": version}

    _handle(run)


@secret_app.command("list")
def secret_list() -> None:
    def run() -> list[dict[str, Any]]:
        with _factory() as factory:
            return factory.secrets.list()

    _handle(run)


@admin_app.command("backup")
def backup(destination: Path) -> None:

    def run() -> dict[str, Any]:
        with _factory() as factory:
            return factory.backup(destination)

    _handle(run)


@admin_app.command("restore")
def restore(
    source: Path,
    expected_key_id: str | None = typer.Option(None, "--expected-key-id"),
) -> None:
    _handle(lambda: restore_backup(_paths(), source, expected_key_id=expected_key_id))


@api_app.command("serve")
def api_serve(
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(8080, min=1, max=65535),
) -> None:
    uvicorn.run(create_app(_paths()), host=host, port=port, log_level="info")


if __name__ == "__main__":
    app()
