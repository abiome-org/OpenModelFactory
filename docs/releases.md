# Releases and deployments

A `Release` is a signed manifest binding a run's model artifact, state,
admitted sources, data rights, evaluation, vulnerability evidence, SBOM, and
promotion decision. Deployments consume releases; aliases point at them.

## Evidence

```sh
omf release evidence run/<run-id> > vulnerability-report.yaml
```

The skeleton lists every artifact digest the release must have scanned: the
aggregate model artifact and each admitted module source. Fill `scanner`
(`name` and `version`), `databaseRevision`, `findings`, and `waivers` from the
site's scanner. Each finding carries `id`, `severity`, and `status`; an open
`high` or `critical` finding blocks promotion unless its id is waived.
`generatedAt` must carry a timezone. OMF stores the report as an immutable
artifact and binds its summary into the release.

## Creating and promoting

```sh
omf --actor release-operator release create run/<run-id> \
  --name affine-v1 --intended-use "affine regression demo" \
  --vulnerability-report vulnerability-report.yaml \
  --approval independent-reviewer --promote --alias candidate
omf release list
omf release show affine-v1
```

Promotion moves the alias only when every gate passes:

- the run succeeded with exactly one aggregate model artifact;
- its evaluation passed and its compatibility check passed;
- every admitted dataset still allows its training or evaluation uses under its newest revision;
- lineage for the run is complete;
- the factory signing identity is valid;
- the vulnerability report covers every required subject with no blocking
  finding;
- at least one approval names an identity other than the promoting actor;
- the project policy allows `release.create` and `release.promote`.

A release without `--promote` is still created and signed; it can be promoted
later by creating a new release from the same run with the evidence in place.
The alias move is guarded by the alias version observed inside the rights
lock, so two concurrent promotions cannot silently overwrite each other.
`release show` prints the release resource and the aliases that point at it.

## Deployments

```yaml
apiVersion: omf.dev/v1alpha1
kind: DeploymentSpec
metadata:
  name: affine-service
spec:
  releaseRef: release/affine-v1
  extensions:
    form: service
    port: 8090
```

```sh
omf --actor deployment-operator deploy deployments/affine-service.yaml
omf deployment list
omf deployment status affine-service
omf deployment cancel affine-service
omf deployment rollback affine-service --expected-version <status-version>
```

`deploy` verifies the release signature, requires its recorded promotion
decision to be `allow`, applies the manifest as an immutable revision, and
launches it through the executor named in `extensions.executor` (default
`local`, options in `extensions.executorConfig`). Forms:

| Form | Behavior |
| --- | --- |
| `edge` | Packages the release; nothing runs |
| `service` without `command` | Serves the release through its model package's inference adapter |
| `service`, `batch`, `actor`, `control` with `command` | Runs the given argv under the executor |

A served release restores the adapter source admitted with the run, checks
its environment digest against the admitted one, loads the model state the
package declared, and listens on `extensions.host` and `extensions.port`
(default `127.0.0.1:8090`). `GET /healthz` reports the release revision and
request counters; `POST /v1/infer` with `{"inputs": {...}}` validates the
inputs against the package input signature, runs one module exchange, and
validates the outputs against the output signature. Error responses carry
codes and request ids, never request values.

Status changes are compare-and-set on `statusVersion`: read the current
status, pass its version to `rollback`, and refresh after a stale-version
error instead of retrying blindly. Rollback relaunches the previous immutable
deployment revision.
