# Worldgen service and choice assembly (derived only)

`resource_pipeline.worldgen_assembler.assemble_worldgen_resources(inputs,
foundations=...)` returns `(objects, receipt)` without writing or publishing.

## Two artifacts, only one publication role

- `worldgen.choices`: actual viewer and backend consumer role.
- `worldgen.options`: service-response-shaped artifact (`enabled`, `versions`,
  `schema`), **not** a resource manifest role. Do not register or publish it through
  the existing consumer release role registry. The live options endpoint remains
  a separate service contract.

Both are canonical UTF-8 JSON bytes. Dictionary insertion order is irrelevant;
arrays retain caller-specified presentation order. The assembler neither sorts
away policy ordering nor fills unknown game facts. Inputs are not modified.

## Independent normalized input contract

The input object has exactly these keys:

- `gameVersion`: dotted numeric version (3 or 4 components).
- `serverVersionKey`: 3–8 ASCII digits, included in service `versions`.
- `serviceOptions`: exact `{enabled, versions, schema}` service response.
  `schema` has exact `{revision, root, types}`; revision is a supplied SHA-256
  string. Root settings refers to `HookConfiguration`; modules is an object.
  All references must resolve in the explicitly supplied types registry.
- `defaults`: explicit submit-ready partial configuration, checked against the
  service root/types and the viewer's depth, collection, range, enum, read-only,
  string and key restrictions. Empty defaults means no submitted overrides;
  it does **not** establish complete server defaults. This API does not synthesize
  omitted defaults. The hash binds the supplied values in the receipt; neither
  output wire contract has a defaults field.
- `authoritativeCatalogs`: independently supplied full typed catalogs for
  `items`, `tiles`, `walls`, `paints` (numeric `[id,label]` rows) and `ores`,
  `depths`, `environments`, `biomes`, `rooms`, `styles`, `generationStyles`,
  `seeds`, `passes` (string `[key,label]` rows). They must not be derived by
  taking the selected output rows and calling them independent authority.
- `choices`: exact `{gameChoices, catalogs, configOptions, choiceLabels,
  groupLabels}`. Four game selections each have exact `{ids,names}`; names
  is a bounded optional list of `[id,label]` overrides. Nine named catalogs
  use `[key,label]` rows. Every selected identity must exist in its independent
  catalog. `passes` and `seeds` must cover their full independent domain,
  matching backend exact-domain projections. Other catalogs may be subsets.
  Numeric selections must additionally occur in their supplied foundation.
  `configOptions` is exactly `{biomes,passes}`; keys refer to selected catalog
  entries and nested object FieldSpecs use inline `fields`, never service refs.
  Choice labels map keys to labels. Group labels map keys to exact
  `{label,help,icon}` objects.
- `sourceHashes`: nonempty bounded map of provenance labels to SHA-256 strings.
  These are recorded assertions, **not authenticated input source bytes**.

`foundations` has exactly `items` and `materials`. Each entry has exact
`{gameVersion,releaseId,baseSha256,bytes}`. `bytes` must be actual bounded UTF-8
JSON bytes for items.catalog/materials.base. Their SHA-256 is recomputed and
must match `baseSha256`; versions must match the selected game version. Item
columns, identity ordering, persistent IDs and scalar domains are checked.
Material identity/name/color row shape is checked. Numeric selections are
joined by identity, never by array position. The supplied release ID is checked
for hash syntax only: authenticating its manifest/source binding belongs to the
publication verifier. The assembler does not infer a game version from versionless
base catalog bytes or assert that caller-supplied version labels are authentic.

## FieldSpec and resource bounds

Supported kinds: object, map, array, boolean, integer, number, string, any.
Unknown or kind-incompatible properties are rejected (stricter than today's
viewer accepting irrelevant unknown properties). Optional label/help/readOnly
are typed; enums are nonempty unique strings; min/max must be finite ordered
safe numbers. Service objects require a known `ref`; choice configuration
objects require inline `fields`. Arrays/maps require a recursive `item`;
map keyKind is string or integer. Recursive type references are permitted like
the consumer; actual supplied defaults remain depth-bounded.

Traversal is bounded before serialization: depth 32, service schema 40,000
nodes, whole normalized input/choices 200,000 nodes, and 16 MiB cumulative text.
Foundation bytes and output bytes each have a 16 MiB cap; foundation traversal
has a 1,000,000-node cap. Catalogs max 20,000 rows; ID range 0–65535;
name overrides max 2,048. Foundation catalogs may cover 65,535 items or 65,536
material IDs. Strings use JavaScript UTF-16 code-unit limits. Booleans do not
count as integers; nonfinite/unsafe numbers, surrogate strings, prototype keys,
control-character keys, cyclic input containers and duplicate foundation JSON
keys are rejected. A bounded parser rejects malformed UTF-8 and JSON.

## Provenance boundaries and acceptance

Schema revision is bound exactly, not recomputed with an invented algorithm.
Service schema/default extraction and identity completeness still require an
independent producer and review. The schema is service configuration truth;
Terraria ID catalogs are game-domain facts; selections, localized overrides,
labels, help, icons and inline editor FieldSpecs are application policy.
Hash equality provides byte integrity only. No private baseline data, game
execution or reference-output copying is used.

The receipt always says `DERIVED_ONLY`, `sourceSemanticsVerified=false`, and
`publicationApproved=false`, and lists missing service/catalog/policy/foundation
proof. Do not use it as producer coverage evidence or sealed publication approval.
Fresh authenticated server inputs and reviewed policy are still needed for real
end-to-end acceptance. Current tests use original small fixtures, not a real
Terraria world-generation baseline.

Run focused tests:

```sh
PYTHONPATH=src python -m unittest discover -s tests -p test_worldgen_assembler.py -v
TERRARIA_CONSUMER_ROOT=/path/to/viewer-app PYTHONPATH=src \
  python -m unittest discover -s tests -p test_worldgen_assembler.py -v
```

The optional real-viewer test invokes `validateGenerationOptions`,
`validateGenerationConfig`, and `validateWorldGenerationSnapshot` from the
actual viewer source against the generated original fixture. Passing establishes
consumer structural compatibility, not complete source semantics or publication.

An optional backend oracle is also part of the test module. Set
`TERRARIA_BOOT_CONTRACT_JAVA` to the current backend `ConsumerRoleContract.java`
and `TERRARIA_BOOT_CLASSPATH` to its already available dependency/compiled-class
classpath (expand jar paths rather than passing compiler wildcards). It compiles
that current source with annotation processing disabled into a temporary folder,
then validates the generated original choices via `ConsumerRoleContract.validate`.
No backend service or publication is started. With both optional oracle contexts
available, all ten tests passed on 2026-10-04, including malformed-node type
mutation checks and actual-viewer default-depth boundary checks (14 nested arrays
under settings.extra accepted, 15 rejected; root values begin at depth 1). This remains fixture-only structural acceptance.
