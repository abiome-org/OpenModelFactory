# Contributing

Open Model Factory changes must preserve model neutrality, immutable identity,
and the local-to-federated lifecycle boundaries in
[`docs/architecture.md`](docs/architecture.md). Resource contracts live in
`factory/omf/schemas/`; executable guarantees live in `tests/`.

1. Create a focused branch and add tests that fail before the change.
2. Run `make test-all`; changes must maintain the configured branch-coverage
   threshold.
3. Do not commit generated `.omf` state, credentials, payload data, or unsigned
   benchmark claims.
4. Update schemas, models, CLI/API behavior, documentation, and compatibility
   notes together for contract changes. Persisted or wire-format changes require
   canonical round-trip and migration coverage, including additive `v1alpha1`
   changes.
5. Explain security, data-rights, recovery, compatibility, and scale
   implications in the review when relevant.
6. Update [`ROADMAP.md`](ROADMAP.md) only when evidence changes a release
   criterion or known limitation.

By contributing, you agree that your contribution is licensed under Apache-2.0.
Use the security process in `SECURITY.md` for vulnerabilities.
