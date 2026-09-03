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

Deployment success does not establish security, availability, latency, or
scale. Test those properties under the applicable site controls before treating
them as supported.
