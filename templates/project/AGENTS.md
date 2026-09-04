<!-- BEGIN OMF OPERATOR GUIDE -->
# Operating this Open Model Factory

This guide is for people and agents operating an installed OMF project. Begin
with `MODEL_CARD.md`: it records the intended use, interface, benchmark targets,
data boundaries, and release risks. Keep it current as evidence changes, but do
not treat prose as proof that a target passed.

Git holds model code and versioned project configuration. Artifact stores hold
data, checkpoints, model payloads, and releases. `.omf/` holds local runtime
state, identity, metadata, and logs; never edit or commit it.

The project starts runnable: `workloads/example-from-scratch.yaml` trains and
evaluates the affine example in `modules/examples/` against
`data/fixtures/affine.jsonl`. Run it before changing anything to see one whole
loop, then replace the example with the real model.

This uppercase root file follows the [AGENTS.md standard](https://agents.md/).
When a subtree has another `AGENTS.md`, its conflicting instructions take
precedence there.

Use `.venv/bin/omf` directly when the environment is not activated. Put global
options before the subcommand, and tie mutations to a stable, named actor:

```sh
.venv/bin/omf --project . --actor <agent-id> --output json <command>
```

## Work loop

1. **Read intent and status.** Review `MODEL_CARD.md`, then inspect bounded
   machine state instead of guessing from files or unbounded logs:

   ```sh
   .venv/bin/omf --project . --output json doctor
   .venv/bin/omf --project . --output json agent context
   .venv/bin/omf --project . --output json agent capabilities
   ```

2. **State measurable work.** For expensive or multi-step work, create a goal
   with a success measure, constraints, and budget. Recommendations are options,
   not authorization.

3. **Change one traceable input.** Keep executable behavior in `modules/`, stage
   graphs in `workloads/`, benchmark definitions in `evaluations/`, and physical
   resources and provider options in `bindings/`. Commit versioned input before
   running it.

4. **Validate and preflight.** Never substitute local execution when a provider
   is unavailable or incomplete.

   ```sh
   .venv/bin/omf --project . --output json module validate \
     modules/<module>/module.yaml
   .venv/bin/omf --project . --output json module test \
     modules/<module>/module.yaml
   .venv/bin/omf --project . --output json executor preflight \
     bindings/<binding>.yaml --workload workloads/<workload>.yaml
   ```

5. **Run and benchmark.** A successful submit is not a successful workload.
   Inspect terminal state, outputs, lineage, and immutable evaluation results;
   compare the candidate with the pinned baseline before updating the model
   card or selecting another change.

   ```sh
   .venv/bin/omf --project . --actor <agent-id> --output json run \
     workloads/<workload>.yaml --binding bindings/<binding>.yaml
   .venv/bin/omf --project . --output json runs status <run-id>
   .venv/bin/omf --project . --output json lineage show run:<run-id>
   .venv/bin/omf --project . --actor <agent-id> --output json evaluate run/<run-id>
   ```

6. **Record the decision.** Record only findings backed by immutable evidence.
   Correct a finding with `--supersedes` rather than rewriting history. Use the
   observed status version for guarded updates:

   ```sh
   .venv/bin/omf --project . --actor <agent-id> --output json goal status <goal> \
     --state satisfied --expected-version <version> --reason <evidence-ref>
   ```

Repeat the loop whenever model code, data, training, evaluation, or deployment
intent changes. Continuous integration should run the same validation,
compatibility, evaluation, and policy paths used for release decisions.

## Operating rules

- Every imported dataset needs explicit rights. Keep training, development,
  reward, and final evaluation data separate. Plan sync before moving payloads;
  sync is additive and never implies deletion.
- Release promotion requires passing evaluation and compatibility results,
  complete lineage, valid rights and signatures, current vulnerability evidence,
  and independent approval. Never fabricate or self-approve evidence.
- Use deployment `statusVersion` with `--expected-version` for rollback. Refresh
  status after a stale-version response rather than retrying blindly.
- Never place credentials, private keys, tokens, signed URLs, sensitive samples,
  prompts, model payloads, or operation/event payloads in Git, logs, errors,
  goals, findings, or agent context. Refer to governed identities and digests.
- Do not bypass rights, budget, policy, approval, isolation, vulnerability,
  promotion, or compare-and-set checks. Stop on an unexplained blocker.
- Back up metadata with `omf admin backup`, artifacts through store replication, and
  `.omf/identity` through the authorized secret process. All three are needed
  to recover the same trust history.
- Built-in local execution supports the module protocol only under its reported
  dependency and isolation limits. A plugin provider name or accepted job does
  not prove support; require direct tests of transport, cancellation, restart,
  recovery, and scale.
<!-- END OMF OPERATOR GUIDE -->
