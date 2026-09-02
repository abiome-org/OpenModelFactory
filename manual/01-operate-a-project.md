# 1. Operate a project

**Status: Tested now**

## Outcome

You will have a repository-scoped factory, a bounded view of its state, and an
explicit goal for the work. Read the root [`AGENTS.md`](../AGENTS.md) before
mutating a factory; an installed project receives the same control-loop guidance.

## Start from desired state

Install OMF into the intended project directory with the root
[`install.sh`](../install.sh), or use the manual development installation in the
[README](../README.md). The installer preserves existing manifests and creates
repository-scoped `.omf/` runtime state. Git stores desired state and code;
`.omf/` and payload data do not belong in Git.

Check the bounded machine interface before acting:

```sh
omf --output json agent context
omf --output json agent capabilities
omf --output json doctor
```

Use global options before the subcommand. Every mutation should use a stable
operator or service identity:

```sh
omf --actor research-agent --output json goal create improve-quality \
  --objective "Improve the frozen evaluation protocol" \
  --success "candidate exceeds the declared baseline threshold" \
  --constraint "final holdout remains inaccessible to training" \
  --budget gpuHours=100 \
  --priority 80
```

Replace the scaffold's `local-user` owner and policy actor before shared use.
Do not invent a human identity or reuse one actor for independent approval.

## Repository boundaries

| Content | Owner |
| --- | --- |
| Modules and workload intent | `modules/`, `workloads/` |
| Physical execution and placement | `bindings/` |
| Policy and deployment intent | `policies/`, `deployments/` |
| Non-sensitive manifests and fixtures | `data/`, Git |
| Data, checkpoints, packages, releases | governed artifact stores |
| Runtime metadata, events, identity, caches | `.omf/` |

Commit desired state before admitting a workload. Never hand-edit `.omf/`, and
never place credentials, raw sensitive data, signed URLs, or operation payloads
in goals or knowledge.

## Evidence before the next chapter

- `doctor.ready` is true.
- Agent context reports no unexplained blocker.
- The goal states measurable success, constraints, and a budget.
- The actor and project ownership are attributable.
- Desired state is committed and the worktree is understood.
