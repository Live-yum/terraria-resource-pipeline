# Observed Item data join (not initializer acceptance)

`resource_pipeline.observed_item_join` joins the fixed collector's complete root
observation to the existing Item adapter and assembler. It creates no producer
profile and does not change the release, backend, or 110-gate acceptance path.
No game, CLR, vendor assembly, application generator, or subprocess is executed.

## Public entry points

```python
from resource_pipeline.observed_item_join import (
    adapt_observed_item_facts, assemble_observed_item_resources,
)

# Both APIs derive static inputs internally; no caller-supplied static proof map.
inputs, join_receipt = adapt_observed_item_facts(
    raw_observation, pe_bytes=pe_bytes_or_path, app_sources=source_bytes,
)
objects, receipt = assemble_observed_item_resources(
    raw_observation, pe_bytes=pe_bytes_or_path, app_sources=source_bytes,
)
```

`pe_bytes` accepts immutable bytes or `pathlib.Path`. Only the fixed client PE
SHA-256 `960a03bff6050cf7be16dfc1a7b19e10fc2c4f8f835a6a3b135a50dd9e6ba2f3`
is accepted. A private temporary snapshot is read by the existing data-only
proof APIs and removed afterward; its contents are never executed.

`app_sources` contains exactly these two current application source files as
bytes. SHA-256 and Git blob IDs are pinned, with source attribution to verified
PR70 commit `638e770db844174ff990e42e40a7e96290ff6968`:

- `scripts/generate-player-item-rules.py`: SHA-256
  `a58392c22c8fd27c8a29c88c5f790f309b5f46fa640e8a62b210050bb3702fb7`,
  Git blob `722cc1f29a9b627c7b84c5c68e4330974b1d82ce`
- `shared/game/item-prefixes.mjs`: SHA-256
  `e73da9a4d3b167bdee4010e359ea2f1c57d71fbc24c9c32953fa47d07cb53984`,
  Git blob `2d1fccc1584f3a1c92ec6d9c835a4a7c09f5e24a`

Those source files define application representation policy. Their old generated
payloads, catalog rows, and hardcoded game-effect rows are never imported as game
facts. In particular, accessory effects come from the fresh PE method proof, not
from the old generator's numeric prefix ranges.

## Selection and source joins

The selected Item domain is the sorted set of positive named `ItemID` Constant
values, excluding any `Count` field. Every Constant row retains its name,
metadata token, Constant token, and blob-offset evidence; same-value aliases and
excluded nonpositive rows remain in the receipt. Nonliteral fields are disclosed.
Independent source Count is an upper-exclusive registry bound only.

On the pinned source this independently yields 6,195 IDs from 6,244 named literal
fields, all below Item Count 6,196. Their equality with `1..<Count` is a checked
property of these source rows, never the definition of selectable completeness.
This is a named-constant selection policy, not a proof of all selectable items.

The observation must provide exactly one record per selected ID. Missing,
duplicate, foreign, remapped (`resolvedType != requestedId`), variant, or extra
rows fail. No item row is silently dropped. Every name and persistent ID must be
observed and nonempty; `persistentIdPresent` must be true. Gameplay must contain
exactly the 47 observed fields. Their source primitive types and ranges are
checked, including exact float32 values; the existing adapter derives the four
application classification fields.

All observed registries include their full source Count domain, including ID 0:

- Seven bool prefix groups are compared with the existing whole-initializer
  boundary proofs.
- Four bool `ItemID.Sets` arrays are compared with fresh literal call-site
  recipes (`IsFood`, `IsBasicFish`, `IsFishingCrate`, `CanGetPrefixes`). Existing
  typed bool-factory and RVA readers are reused. Their factory, normal-return,
  buffer-size, and mutation preconditions remain explicit; a conditional recipe
  is not promoted to whole-initializer acceptance.
- A fifth observed `ItemID.Sets.IsAMaterial` bool array is required, Count-bound,
  source-field-bound, and retained with values and hash as diagnostic evidence.
  Its mutable registry values are not asserted equal to final `Item.material`;
  the latter remains the independently observed per-item gameplay field. Only
  the four application classification sets are projected into the adapter.
- All 20 sorting priority arrays are compared with fresh explicit source default
  and ordered-override provenance. Repeated writes use last-write semantics;
  explicit `-1` and `0` remain sparse membership, rather than disappearing when
  equal to the default.
- Eight ordered prefix pools must exactly match the source-boundary arrays.
  Reordering, omission, duplication, and out-of-domain IDs fail.

Count, named prefix constants, prefix names, and coefficient records agree on
98 prefixes. Positive prefix names must come from complete collector
`prefixNames: [{id, name}]` observations of `Lang.prefix`/`LocalizedText.Value`.

## Prefix and research representation

The nine coefficient keys are read from the existing fresh finite out-parameter
proof. `dmg`, `kb`, `spd`, `size`, `shtspd`, and `mcst` are multipliers, neutral 1.
`crt` is percentage points; `tagdmg` and `arpen` are absolute additive values,
neutral 0. Neutral entries are omitted and exact decoded float32 values retained.

The accessory whole-method proof supplies `defense`, `maxMana`, `critBonus`,
`damageBonus`, `moveBonus`, and `meleeSpeedBonus`. Integer effects remain integer
deltas. An exact source float32(n/100) literal projects to nominal percentage n,
as defined by that proof and consumed by the pinned app renderer.

Prefix 0 must have neutral source effects. Its actual observed name is retained
in the receipt. An empty or null observed name may be represented by the app's
no-prefix caption, extracted from the pinned generator's original AST literal.
That caption is explicitly marked application-owned and `gameObserved: false`.
A nonempty observed prefix-0 name is used unchanged. Other missing names fail.

Research uses `observed-research-out-count-v1`: the integer observed out-count is
copied into the existing consumer column. `present: false` is allowed only with
an actual observed `count: 0`. Every absence remains in the receipt with its ID,
false membership, and observed zero. This is a representation rule for the
consumer's integer column, not invented research membership or a guessed cap.

## Root schema and trust boundary

The exact fixed collector root is required, including `prefixNames` and nested
`playerObservation`. The latter is bounded JSON and included in the observation
hash but is not validated or consumed by the Item join; its own adapter must
validate it separately. All roots are bounded before canonical hashing, with
cycle, Unicode, exotic-value, depth, node, string, byte, and numeric checks.

The source version, Chinese culture, Count values, ordinary game mode, difficulty,
null override, false variant flags, dedicated-server mode, and random seed are
checked. Diagnostic module hashes, initialization labels, and requested execution
mode are retained only as unaccepted observation claims.

The receipt retains source proof preconditions, source identities, Count and
Constant evidence, policy hashes, full-array comparison hashes, prefix-0 origin,
research absence, raw/normalized hashes, context, and assembler output hashes.
It always has:

- `observationAuthenticated: false`
- `runtimeInitializationVerified: false`
- `sourceSemanticsVerified: false`
- `runtimeSnapshotUsable: false`
- `complete: false`
- `publishable: false`

Remaining gaps include isolated runtime and initialization acceptance, actual
collector provenance, dependency/OS-image acceptance, mutable-state/caller
closure, proof preconditions, universal selectable-domain completeness, and
publication approval. Matching source arrays does not discharge these gaps.

## Tests

`tests/observed_item_join_fixture.py` contains a tiny, original invented fixture
for the private join seam. It is not a game observation or accepted PE profile.
`tests/test_observed_item_join.py` covers the complete synthetic data join,
source and app pin rejection, domain holes and aliases, no-drop row checks,
source array mismatches at ID 0 and positive IDs, explicit priority sentinels,
prefix scales, null/empty/observed zero names, research absence, primitive ranges,
context/false gates, missing source coefficients, malformed values and budgets.

Run:

```sh
PYTHONPATH=src:tests python -m unittest tests/test_observed_item_join.py -v
```

An optional private test inspects the pinned PE and pinned application sources
as data only. Set `TERRARIA_OBSERVED_ITEM_PE` and `TERRARIA_CONSUMER_ROOT` to enable
it. It verifies the source counts and policy pins; it does not manufacture a
runtime observation or claim an authentic end-to-end collector run.
