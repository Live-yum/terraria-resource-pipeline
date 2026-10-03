# Reviewed complete consumer release sets

## Authority and deployment boundary

This is an **unconfigured, fail-closed** control plane and an opt-in **original
synthetic/local Git demo**. It does not authorize real game assets, grant new
credentials, deploy a private server, modify TConvert, or write the existing CDN.
Existing raw-input, trusted coverage and real publication gates remain unchanged.

The client has a one-time code-reviewed trust anchor: the existing
`Live-yan/terraviewer-images` repository, a publisher identity, its compatible
consumer contract commit, and a minimum approval sequence. Its sole authority is
that publisher-owned repository's HTTPS `main/channels/consumer-stable.json`.
The channel does not accept origins, repository names or paths from uploads.
Hashes detect corruption; neither hashes nor `approved: true` establish approval.
The repository's existing write policy must be reviewed before configuring this
anchor. CDN branch freshness is not guaranteed; stale approvals are rejected or
leave the current complete set in use, never force a downgrade.

## Publication protocol

1. Server builds a complete, fixed group inventory and issues a review digest
   bound to exact group manifests, source binding and the current channel baseline
2. Admin reviews and explicitly confirms that exact digest; stale, incomplete,
   changed or unconfirmed reviews fail
3. Server re-reads and verifies all staged objects before writing any Git object
4. Commit immutable group manifests at `consumer/<group>/<releaseId>.json` and
   content-addressed objects; record that immutable Git revision in every pin
5. Commit a `release-sets/<releaseSetId>.json` document binding all group pins,
   exact game version, server SHA-256, client tree and consumer contract commit
6. A final commit writes the channel's monotonically increasing approvalSequence,
   immutable set revision/hash and explicit advance/rollback operation. Push the
   three-commit chain once to the local bare Git simulator and verify its head

Required groups are materials, items, player and markers. Optional worldgen
entries have exact serverVersionKey/schemaRevision plus the same game/source
binding. Unsupported server versions have no entry; 1456 must never be relabeled
as 1458. Rollback selects only previously publisher-approved history, requires a
fresh baseline-bound review, and increments the approval sequence.

`ConsumerReleaseControl` is disabled by default. `create_app(...,
consumer_demo=True)` exposes synthetic preview/review/rollback actions to the
existing loopback-only admin page. Inputs are generated from original server-owned
fixtures; staged bytes must match them exactly. These tiny objects are **transport
fixtures**, not real Terraria runtime contracts or a semantic extraction claim.
No HTTP endpoint accepts arbitrary release pins, files, coverage or approvals.

## Client behavior

A new set stages all required groups and optional version entries privately. Every
object is byte-verified and each runtime group gets its semantic validator and
same-candidate dependencies. Only one coordinator pointer exposes the complete
set. Partial fetches, invalid bindings, timeout, cancellation or semantic failures
retain the old complete set. Initial startup may activate a verified first set;
subsequent checks stage it for an explicit UI update action. A forced refresh does
not silently switch a mounted editing view. Operations retain immutable snapshots
and views keep drafts while requiring re-entry when data changes.

An app-owned control receipt, separate from object caches, remembers the approved
pointer and exact set manifest only after complete verification. Offline startup
re-verifies every group. Object-cache entries alone cannot authorize a release.
The app storage boundary is trusted like the installed app itself; this is not a
cryptographic defense against a device attacker who can rewrite application
storage or code. Cache clearing preserves the approval high-water and control
receipt, and missing objects still fail offline.

The selection check has an 8-second default budget propagated to control/group
transport timeouts. Over-budget attempts are failures; started I/O must still be
aborted/drained with memory reservations retained. A hung platform abort may take
longer to drain. No real-network/cold-device latency claim follows from synthetic
local tests. Production trust is still unset.

## Repeatable cross-runtime proof

Run the Python test suite normally. To verify the actual local Git output with
the private consumer's JavaScript byte loader without copying private data into
this repository:

```
PYTHONPATH=src python scripts/prove_consumer_release_set.py --output /tmp/new-proof
CONSUMER_RELEASE_SET_PROOF=/tmp/new-proof/proof.json node --test \
  /path/to/viewer/test/unit/resource-release-set-transport.test.mjs
```

The JS test creates an ephemeral loopback HTTP server over that bare Git tree,
then proves full activation, a last-group failure, verified prior offline restore,
explicit replacement, immutable captured old snapshots, replay rejection and an
explicit sequence-increasing rollback. Only original synthetic bytes enter the
proof. This is not permission to publish the proof or real assets remotely.

Cold startup first attempts a complete, re-verified restore from the last approved
app-owned receipt and object cache. It does not require a network request merely
to rediscover already-approved pins. Foreground checks restore/initialize that
active set before checking the fixed channel and privately staging a newer set.
A staged update does not silently replace editing data. Activation requires the
explicit update confirmation; existing editing views keep their drafts and stop
stale interactions until the user backs up and re-enters the page.
