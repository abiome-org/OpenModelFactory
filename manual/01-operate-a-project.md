# 1. Operate a project

**Status: Tested now**

## Outcome

You will have a living model card, a repository-scoped factory, a bounded view
of its state, and an explicit goal for the work. An installed project's root
`AGENTS.md` contains a self-contained operator guide. In this distribution
repository, the root [`AGENTS.md`](../AGENTS.md) instead governs contributors
changing OMF itself.

## Start from the model card and versioned configuration

Install OMF into the intended project directory with the root
[`install.sh`](../install.sh), or follow the manual development installation in
the [operations runbook](../docs/operations.md). The installer preserves
existing manifests and creates repository-scoped `.omf/` runtime state. Git
stores code and versioned configuration; `.omf/` and payload data do not belong
in Git.

For a new project, complete the generated `MODEL_CARD.md` before choosing a
model architecture or training run. Define intended use, inputs and outputs,
data boundaries, baseline, benchmark target, constraints, risks, and the
evidence required for release. Keep it short and link to immutable OMF results
as they are created. For an existing project, reconcile the card with current
behavior rather than rewriting history.

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
  --objective "Improve results under the fixed evaluation protocol" \
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

Commit versioned project configuration before running a workload. Never
hand-edit `.omf/`, and never place credentials, raw sensitive data, signed URLs,
or operation payloads in goals or knowledge.

## Evidence before the next chapter

- `MODEL_CARD.md` states the initial problem, benchmark, constraints, and risks.
- `doctor.ready` is true.
- Agent context reports no unexplained blocker.
- The goal states measurable success, constraints, and a budget.
- Actions and project ownership are tied to named actors.
- Versioned project configuration is committed and the worktree is understood.
