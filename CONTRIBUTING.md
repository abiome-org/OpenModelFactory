# Contributing

Open Model Factory changes must preserve model neutrality, immutable identity,
and the local-to-federated lifecycle contract in `SPEC.md`.

1. Create a focused branch and add tests that fail before the change.
2. Run `make test-all`; changes must maintain the configured branch-coverage
   threshold.
3. Do not commit generated `.omf` state, credentials, payload data, or unsigned
   benchmark claims.
4. Update schemas and compatibility notes for contract changes. Additive
   `v1alpha1` changes still require migration and round-trip tests.
5. Explain security, data-rights, recovery, and scale implications in the
   review when relevant.

By contributing, you agree that your contribution is licensed under Apache-2.0.
Use the security process in `SECURITY.md` for vulnerabilities.
