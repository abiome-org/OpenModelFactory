# 10. Release and deploy

**Status: Conditional**

Release and deployment require legitimate evaluation, vulnerability, rights,
lineage, signature, policy, and independent-approval evidence. The local edge
packaging path is implemented; production serving and infrastructure remain
site integrations.

## Promotion prerequisites

Before promotion, require:

- a succeeded run with exactly one aggregate model artifact;
- passing immutable evaluation evidence for that run;
- complete dataset, module, run, and artifact lineage;
- rights declarations for every registered dataset;
- valid factory signing identity;
- current scanner evidence covering the model and admitted module digests;
- no unwaived high or critical finding;
- approval by an identity other than the promoting actor.

Do not manufacture scanner output or list the same actor under another label.
The external scanner report format is documented in the
[operations runbook](../docs/operations.md).

With real evidence available:

```sh
omf --actor release-operator --output json release create <run-id> \
  --name candidate-v1 \
  --intended-use "declared approved use" \
  --vulnerability-report reports/vulnerabilities.yaml \
  --approval independent-reviewer \
  --promote
```

Promotion is denied when evidence is missing or invalid. A signed release binds
model/state artifacts, workload and binding provenance, data rights, evaluation,
vulnerabilities, limitations, intended use, risk decision, SBOM, and rollback
information.

Create a deployment manifest referencing the promoted release, review its
provider and routing intent, then apply and observe it:

```sh
omf --actor deployment-operator --output json deploy deployments/candidate.yaml
omf --output json deployment status candidate
```

For rollback, first read the exact current `statusVersion`, then use it as the
compare-and-set guard. Refresh status after a stale-version response rather than
retrying blindly.

## Serve a release locally

A `service` deployment that names no `command` serves the release through the
model package's inference adapter. OMF restores the adapter source admitted
with the release's run, checks that its environment digest matches the
admitted one, loads the model state the package declared, and starts a local
HTTP worker on `extensions.host` and `extensions.port` (default
`127.0.0.1:8090`). The deployment status reports the `endpoint`.

```yaml
apiVersion: omf.dev/v1alpha1
kind: DeploymentSpec
metadata:
  name: affine-service
  namespace: local/my-factory
spec:
  releaseRef: release/affine-v1
  runtime: omf.module/v1
  routing: {}
  extensions:
    form: service
    port: 8090
    requestTimeoutSeconds: 60
```

`GET /healthz` reports the release revision, model package, and request
counters. `POST /v1/infer` with `{"inputs": {...}}` validates the inputs
against the package input signature, runs one `omf.module/v1` exchange with the
adapter and the release state, validates the outputs against the output
signature, and returns them with the release revision. The adapter runs with
network denial when the executor can enforce it; the worker owns the endpoint.
Error responses carry codes and request identifiers, never request values. A
`batch`, `actor`, or `control` deployment still requires an explicit command.

Deployment success does not establish security, availability, latency, or
scale. Test those properties under the applicable site controls before treating
them as supported.
