# Security policy

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Use GitHub's private
security-advisory flow for this repository and include affected versions,
reproduction steps, impact, and any known mitigations. Maintainers should
acknowledge a complete report within three business days and coordinate a fix
and disclosure timeline with the reporter.

## Security boundary

OMF treats module, dataset, release, and federation input as untrusted. Local
process resource limits are defense-in-depth, not a VM-grade sandbox. Expose the
HTTP API only behind site-managed TLS and identity controls. Never commit `.omf`,
private keys, API tokens, cloud credentials, or data payloads.

Supported releases receive security fixes. Until a stable release exists, only
the current `main` revision is supported. Security behavior is supported only
where the test suite and named deployment controls exercise it directly.
