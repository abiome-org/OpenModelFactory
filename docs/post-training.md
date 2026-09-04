# Post-training with verifiable rewards

The repository ships no reinforcement-learning workload, trajectory store, or
orchestration for it. This page is the integration outline for building one
out of modules and governed data; it contains no commands because none exist
yet for this path.

## Roles

```text
base checkpoint
      |
      v
actor/inference module ---> isolated environment ---> observation
      |                           |
      |                           v
      +<--- bounded action --- reward verifier
      |                           |
      v                           v
trajectory artifact -------> learner/trainer module
                                  |
                                  v
                           candidate checkpoint
                                  |
                                  v
                    independent evaluation verifier
```

Each role is a versioned module or a governed dataset:

- **Environment:** observation and action schemas, transition rules, reset,
  seeds, limits, terminal reasons, and replay state.
- **Actor:** the exact base model state, inference policy, permitted actions,
  and sampling configuration.
- **Reward verifier:** reward task revision, hidden logic boundary, score
  schema, invalid-action treatment, and tamper resistance.
- **Trajectory:** immutable observations, actions, rewards, terminations,
  policy revision, environment revision, and redaction policy.
- **Learner:** objective, batching, regularization, optimizer state, checkpoint
  boundaries, and budget.
- **Independent evaluation verifier:** distinct holdout tasks and a verifier
  that never supplied reward or selection signal.

## Boundaries

Treat model-generated actions as untrusted. Deny network by default, isolate
reward and hidden-test logic from actor-visible inputs, enforce CPU, memory,
and time limits through the binding, and give the actor no control-plane,
store, or deployment credentials. Synthetic trajectories inherit upstream data
rights and must declare their synthetic origin. A reward task, verifier, or
metric used during learning is development evidence and cannot serve as the
final evaluation.

## Sequence

1. Contract-test environment reset, step determinism, and terminal behavior.
2. Contract-test actor requests and bounded actions without learning.
3. Validate reward computation against adversarial outputs in isolation.
4. Commit trajectories as content-addressed artifacts with complete lineage.
5. Train from a fixed base checkpoint and trajectory revision.
6. Commit atomic candidate checkpoints including random-number state.
7. Evaluate with the separately governed protocol from the
   [evaluation page](evaluation.md).
8. Exercise interruption, restart, cancellation, and verifier-failure paths.

Call this path operational only after one unchanged workload demonstrates
module transport, environment isolation, trajectory completeness, checkpoint
recovery, and independent evaluation end to end.
