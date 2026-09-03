# 8. Add RLVR post-training

**Status: Extension blueprint**

OMF exposes environment, inference, verifier, sampler, checkpoint, policy-state,
and evaluation contracts. The repository does not yet ship an end-to-end RLVR
workload, trajectory store, or CLI orchestration path. This chapter is an
integration outline, not a runnable recipe.

## Required role separation

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

Define each role as a versioned module or governed asset:

- **Environment:** observation/action schemas, transition rules, reset, seeds,
  limits, terminal reasons, and replay state.
- **Actor:** exact base model state, inference policy, tool/action permissions,
  and sampling configuration.
- **Reward verifier:** reward task revision, hidden logic boundary, score schema,
  invalid-action treatment, and anti-tampering controls.
- **Trajectory:** immutable observations, actions, rewards, terminations, policy
  revision, environment revision, and redaction policy.
- **Learner:** objective, batching, clipping/regularization, optimizer state,
  sampler state, checkpoint boundaries, and budget.
- **Independent evaluator:** distinct holdout tasks and verifier that never
  supplied reward or selection signal.

## Safety boundary

Treat model-generated actions as untrusted. Deny network by default, isolate
reward and hidden-test logic from actor-visible inputs, enforce CPU/memory/time
limits, and provide no control-plane, artifact-store, or deployment credentials
to the actor. Retain non-sensitive protocol failures without leaking verifier
secrets.

Synthetic trajectories inherit upstream data rights and require explicit
synthetic origin. A reward task, verifier, or metric used during learning is
development evidence and cannot serve as independent final evaluation.

## Integration sequence

1. Contract-test environment reset/step determinism and terminal behavior.
2. Contract-test actor requests and bounded actions without learning.
3. Isolate and validate reward computation against adversarial outputs.
4. Commit trajectories as content-addressed artifacts with complete lineage.
5. Train from a fixed base checkpoint and trajectory/sampler revision.
6. Commit atomic candidate checkpoints including RNG and sampler state.
7. Evaluate with the separately governed protocol from chapter 4.
8. Exercise interruption, restart, cancellation, and verifier-failure paths.

Do not call this path operational until one unchanged workload demonstrates
module transport, environment isolation, trajectory completeness, checkpoint
recovery, and independent evaluation end to end.
