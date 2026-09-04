# 3. Bring and partition data

**Status: Tested now**

The copy and register paths are tested. Mount and stream registration exist,
but connector-specific consumption remains an extension boundary.

## Define roles before importing bytes

Define train, development, reward, and final-holdout roles before inspecting
candidate results. Keep each role as a distinct immutable snapshot with explicit
rights and access policy. A filename such as `test.jsonl` is not an access
boundary or an immutable identity.

A rights declaration should cover license, consent or other lawful basis,
training and redistribution permission, attribution, sensitivity, retention,
residency, export restrictions, and intended use as applicable. Never infer
permission from technical accessibility.

## Choose an ingestion mode

| Mode | Current behavior |
| --- | --- |
| `copy` | Imports bytes into the local content-addressed store and creates a copyable manifest |
| `register` | Leaves bytes in place, records hashes and metadata, and detects later drift |
| `mount` | Records a binding-provided path with the same local drift checks |
| `stream` | Records a credential-free URI and requires a cursor/version policy |

Examples:

```sh
omf --actor research-agent --output json data add data/source/train \
  --name pretrain-v1 --mode copy --rights data/rights/pretrain.yaml
omf --output json data verify pretrain-v1

omf --actor research-agent --output json data add /governed/read-only/corpus \
  --name registered-corpus-v1 --mode register \
  --rights data/rights/corpus.yaml
omf --output json data verify registered-corpus-v1
```

Use a new dataset name for a changed observed boundary instead of silently
reusing an experiment input. Verification of registered or mounted data fails
when size, modification time, or SHA-256 digest drifts.

## Replicate copied data

Only copied data currently has an OMF artifact manifest that `sync` can
replicate. Plan first, transfer only missing chunks, and verify the destination:

```sh
omf --actor research-agent --output json store add secondary \
  --driver filesystem --endpoint .omf/secondary-store
omf --actor research-agent --output json sync push dataset/pretrain-v1 \
  --to secondary --plan
omf --actor research-agent --output json sync push dataset/pretrain-v1 \
  --to secondary
```

Filesystem and S3-compatible stores are implemented. Store credentials use
secret references; they never belong in manifests. Sync is additive and does
not imply deletion.

## Mixture boundary

There is no mixture resource. Implement mixture delivery in a versioned module
and record its exact source snapshots, weights, seed, and state as outputs and
lineage; do not claim exact mixture replay.

## Evidence before the next chapter

- Every source has a declared role and rights record.
- `data verify` succeeds for every snapshot used by the workload.
- Training, reward, development, and final holdout assets are separate.
- Replicas are identified by the same manifest digest as the source artifact.
