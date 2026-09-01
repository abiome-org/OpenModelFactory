<!-- BEGIN OMF OPERATOR GUIDE -->
# Operating this Open Model Factory

This directory is an OMF desired-state workspace. Git records code, modules,
workloads, bindings, policies, and deployment intent. Governed stores hold data,
checkpoints, model packages, and releases. `.omf/` holds local runtime state,
identity, metadata, logs, and caches; never edit it by hand or commit it.

This uppercase root file uses the [AGENTS.md standard](https://agents.md/).
When a subtree contains another `AGENTS.md`, its conflicting instructions take
precedence there; retain non-conflicting guidance from this root file.

Use `.venv/bin/omf` directly when the virtual environment is not activated.
Pass global options before the subcommand. Mutating work must use a stable,
attributable actor rather than a shared or invented human identity:

```sh
.venv/bin/omf --project . --actor <agent-id> --output json <command>
```

Verify the installed environment before beginning work:

```sh
.venv/bin/omf --project . --output json doctor
```

Before the first mutation, replace the scaffold's `local-user` owner and policy
actor with the real operator or service identity when they differ.

## Required control loop

1. **Observe.** Start every session and refresh after any conflict or external
   change:

   ```sh
   .venv/bin/omf --project . --output json agent context
   .venv/bin/omf --project . --output json agent capabilities
   ```

   Treat context facts, blockers, action preconditions, and immutable evidence
   as authoritative. Recommendations are deterministic options, not permission
   to execute. Use `recentEvents.cursor` with `agent context --since <cursor>`
   for bounded incremental observation.

2. **State intent.** Create a goal with measurable success criteria,
   constraints, budget, and priority before expensive or multi-step work:

   ```sh
   .venv/bin/omf --project . --actor <agent-id> --output json goal create quality \
     --objective "Improve held-out quality" \
     --success "accuracy >= 0.90" \
     --constraint "rights approved" \
     --budget gpuHours=100 \
     --priority 80
   ```

3. **Plan and preflight.** Use `--plan` or `--dry-run` whenever the capability
   catalog says it is supported. Before compute, inspect exact provider code and
   preflight the binding against the workload:

   ```sh
   .venv/bin/omf --project . --output json executor list
   .venv/bin/omf --project . --output json executor preflight \
     bindings/local.yaml --workload workloads/<workload>.yaml
   ```

   An unknown, unready, or capability-incomplete provider must fail closed.
   Never substitute local execution or weaken isolation to make a run proceed.

4. **Execute one attributable change.** Keep scientific intent in `workloads/`
   and physical resources, placement, transport, and provider configuration in
   `bindings/`. Commit desired state before workload admission; dirty source is
   denied by default. Validate and contract-test modules before running them:

   ```sh
   .venv/bin/omf --project . --output json module validate \
     modules/<module>/module.yaml
   .venv/bin/omf --project . --output json module test \
     modules/<module>/module.yaml
   .venv/bin/omf --project . --actor <agent-id> --output json run \
     workloads/<workload>.yaml --binding bindings/<binding>.yaml
   ```

5. **Verify evidence.** A successful submit is not a successful workload.
   Inspect terminal state, outputs, lineage, and evaluation evidence:

   ```sh
   .venv/bin/omf --project . --output json runs status <run-id>
   .venv/bin/omf --project . --output json lineage show run:<run-id>
   .venv/bin/omf --project . --actor <agent-id> --output json evaluate run/<run-id>
   ```

6. **Accrete knowledge.** Record only bounded claims backed by immutable
   evidence. Correct a claim with `--supersedes`; do not rewrite history. Update
   goal status with the exact observed `statusVersion`:

   ```sh
   .venv/bin/omf --project . --actor <agent-id> --output json knowledge record result \
     --category observation \
     --claim "evaluation/42 measured accuracy 0.91" \
     --confidence 0.99 \
     --evidence evaluation/42 \
     --goal-ref goal/quality
   .venv/bin/omf --project . --actor <agent-id> --output json goal status quality \
     --state satisfied --expected-version <version> \
     --reason "success criterion met by evaluation/42"
   ```

## Data, synchronization, release, and deployment

- Every imported dataset needs explicit rights. Prefer `register` for governed
  in-place data and `copy` for content admitted to the local store. Verify the
  resulting immutable snapshot.
- Configure holding sites through symbolic secret references. Plan every sync;
  sync is additive and must never imply deletion.

  ```sh
  .venv/bin/omf --project . --actor <agent-id> --output json data add <source> \
    --name <dataset> --mode copy --rights data/<rights>.yaml
  .venv/bin/omf --project . --output json data verify <dataset>
  .venv/bin/omf --project . --actor <agent-id> --output json sync push \
    dataset/<dataset> --to <store> --plan
  ```

- Release promotion requires current vulnerability evidence covering the model
  and admitted modules, passing evaluation and lineage, independent approvals,
  and an allow policy decision. Never fabricate or self-approve evidence.
- Before deployment, verify the signed release. Observe deployment status and
  use its exact `statusVersion` for rollback; refresh context after a stale
  compare-and-set response.
- Back up metadata with `omf backup`, artifact stores through their replication
  mechanism, and `.omf/identity` through the authorized secret backup process.
  A metadata backup without the matching signing identity is not a recoverable
  trust domain.

## Safety and economy

- Never place credentials, private keys, tokens, signed URLs, raw sensitive
  samples, prompts, model payloads, or operation/event payloads in Git, logs,
  errors, goals, knowledge, or agent context. Reference governed identities and
  digests instead.
- Do not bypass rights, budget, policy, approval, isolation, vulnerability,
  promotion, or compare-and-set gates. Stop on an unexplained blocker.
- Prefer bounded context, incremental event cursors, sync plans, provider
  preflight, small representative evaluations, and checkpoint reuse before
  spending data movement or accelerator time.
- Local execution is complete. Built-in Slurm requires an explicitly shared
  filesystem and cannot enforce network denial. Built-in Kubernetes provides
  scheduler lifecycle only, not complete module transport. Do not infer backend
  or scale conformance from a provider name or accepted job.
- Never claim security, air-gap, cluster, federation, scale, or frontier
  conformance without the measured signed scenario evidence required by
  the factory's governed specification.
<!-- END OMF OPERATOR GUIDE -->
