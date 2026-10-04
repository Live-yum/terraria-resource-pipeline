# Bounded player fact adapter

This is a local, data-only bridge to `player_assembler._validate_facts`. It does
not execute Terraria, a CLR, UI constructors, `BoringSetup`, draw routines, shader
application, or vendor code. It does not alter any producer acceptance gate.

## Entry points

```python
from resource_pipeline.player_fact_adapter import adapt_observed_player_facts

facts, receipt = adapt_observed_player_facts(
    collector_document['playerObservation'],
    pe_bytes=operator_supplied_pinned_client_bytes,
    app_sources=operator_supplied_pinned_source_bytes,
    item_objects=fresh_three_role_item_objects,
    choices=derived_choice_key_mapping,
)
```

`facts` has exactly the existing assembler's `buffs`, `dyes`, `hairRules`,
`wingRules`, `selection`, and `versionLabels` keys. Insert it into a separately
derived player recipe policy; atlas/walking recipes and their verification are
not provided here. The actual choice keys are required for selected-clothes
referential checks. All three item roles are validated and their exact hashes
are bound into the receipt. These checks do not authenticate an observation or
prove the caller's item foundation is fresh.

The two component APIs are independently inspectable:

- `player_fact_source_shapes.extract_player_static_facts(pe_bytes)`
- `player_fact_app_policy.extract_player_app_policy(source_bytes)`

Both accept bytes, perform their fixed pins internally, and return a result plus
an incomplete, nonpublishable receipt. There is no runtime-selectable alternate
pin/trust profile.

## Inputs and provenance

The PE must match the fixed client SHA-256 in `SOURCE_SHA256`. The reader parses
the actual PE/CLI metadata, method bytes, backing bytes, and Constant rows:

- Hair: a closed forward, call-free integer head-decision region is evaluated
  across the consumer's bounded ID domain. Tagged output references and assigned
  locals are enforced. Every instruction in that region is checked. A separate
  exact grammar captures the complete back-hair range/exclusion tail. No Player
  instance is allocated. Neither slice claims full Player/runtime equivalence.
- Clothes: the pinned constructor prefix must allocate an Int32 array, reference
  a correctly sized local FieldRVA backing type, use the exact core
  `RuntimeHelpers.InitializeArray` signature, and store into the expected field.
  The intrinsic is recognized as data and is never invoked. Actual backing bytes
  supply the order.
- Current version: exact, unique int/string Constant rows supply current save and
  game versions. The current label must agree with the app policy's current row.
- Buff and face Counts: named pinned initializer prefixes bind the observed array
  domains. This is not proof of initializer execution or later-mutation closure.

`app_sources` contains exactly three operator-supplied source files, identified
by keys in `APP_SOURCE_PINS`. Each is checked against both a Git blob ID and a
SHA-256 tied to the existing app source commit. Only the named literal
declarations/curated expression shape are parsed; modules are never evaluated.

The app-owned policies are wing preview defaults/overrides, curated negative and
common buff selection, unlock captions/keys/minimum versions, and historical
release labels. The source files' hashes are packaged as pins; their private
literal lists are not embedded in this adapter. Old buff/dye/item/atlas/walk
payloads are not read. Clothes and hair-dye lists in old selection code are
deliberately ignored. Old hair policy source is not an input.

## Collector fragment

The nested `playerObservation` must contain exactly:

- `schemaVersion`, `gameVersion`, `sourceSha256`, `culture`, `dedServ`
- `buffCount`, `buffs`: complete positive-ID rows with `id/name/description`,
  observed from the actual Lang getters after localization stages
- `dyes`: item/registry-bound rows containing `itemId/shaderId/class/pass`, both
  observed Vector3 color fields, and observed saturation; no missing-field
  defaults are supplied
- `faceCount`, `faceSets`: six source-named complete bool arrays
- `hairShaderCount`, `hairDyeBindings`: item IDs and positive registry shader IDs
- `mainDebuff`: a full bool array, retained as separate diagnostic evidence
- `omittedFields`, `initializationVerified`, `complete`, `publishable`

The fragment must declare dedicated-server mode and Chinese culture, with the
three acceptance/completeness flags false. `omittedFields` must be exactly
`["dyes.image"]`. Dedicated-server `UseImage` does not retain that asset, and the
current CPU consumer does not read it; the adapter omits the optional field and
records why rather than inventing an empty image name.

Armor candidates must exactly cover positive dye fields in the bound item rules.
The finite full game class whitelist is checked before projecting short class
names. Hair-dye IDs must be unique, contiguous, complete and agree with catalog
column 5. Their order comes from shader IDs, not sorted item IDs; zero is explicitly
an application no-dye sentinel. Explicit item `hairDye == 0` is not a positive
registry binding. Missing or conflicting data fails closed.

The curated negative-buff policy is not replaced by `Main.debuff`. The receipt
records independent counts/hashes and whether the two domains happen to agree.
Historical labels and wing placement remain app policy. No historical labels or
renderer coordinates are claimed to be freshly observed game facts.

## Verification and boundary

Run original fixture and adversarial tests with:

```sh
PYTHONPATH=src:tests python -m unittest tests/test_player_fact_adapter.py -v
```

Fixtures contain newly authored miniature PE/CLI files, method branches, source
literals and observation rows. They contain no copied game data or baseline
resource payloads. Tests cover FieldRVA reads, wrong sizes and duplicate Constants,
closed branch targets, unreachable calls, literal-expression rejection, byte pin
rejection, registry-to-item binding, missing observed fields, explicit omission,
and false trust/completeness flags.

Successful static extraction means only those bytes matched and those narrow
shapes were parsed. `executedInput`, `observationAuthenticated`,
`runtimeInitializationVerified`, `sourceSemanticsVerified`, `complete`, and
`publishable` remain false in the adapter receipt. An actual authorized collector
run and the unchanged downstream source/initialization/release gates remain
separate requirements. This work provides no new backend trust profile.

## Joined private draft command

`scripts/assemble_observed_draft.py` joins the fixed observation directly to the
three Item roles. Supplying all three player options additionally joins the
nested player observation, fresh PNG recipes and the Item foundation to the
three player roles. It does not accept pre-normalized invented gameplay rows.

```sh
PYTHONPATH=src python scripts/assemble_observed_draft.py \
  --observation /private/observations/observation.json \
  --client /private/source/Terraria.exe \
  --item-app-source-root /private/app-at-638e770db844174ff990e42e40a7e96290ff6968 \
  --player-app-source-root /private/app-at-c6cf0cb381b85d6f934c023ce68cfc206ef7b326 \
  --textures-root /private/fresh-pngs \
  --texture-inventory /private/inventories/player-textures.json \
  --output /private/drafts/new-item-player-draft
```

Both application source roots refer to `Live-yan/viewer-app` code checkouts at
the stated immutable commits. Each adapter verifies its exact source blob pins;
it neither imports application modules nor reads old resource payloads. The
texture inventory is a JSON array of unique relative source PNG paths. Path
traversal and symlinks are rejected; input bytes and file counts are bounded.
Output must be new, outside the pipeline checkout and all input roots.

The client PE is only parsed as data by this command. Obtaining a genuine
observation remains a separately authorized Windows runtime step. `assembly.json`
is written last as a private draft marker, with observation authentication,
source completeness, release readiness and publication approval all false.
It is not a release manifest or a producer certificate. Materials, markers,
pixel and worldgen source adapters are not supplied by this command.

`observed_player_assembly.assemble_observed_player_resources` is the equivalent
Python player-only entry point. It derives actual choice keys from fresh PNGs,
feeds those keys into fact validation, and binds the repeated choice derivation
receipt to the final assembly; caller-supplied arbitrary choice domains are not
accepted by this integrated entry point.
