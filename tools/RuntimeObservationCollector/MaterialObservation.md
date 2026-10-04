# Bounded material observation fragment

This is original collector code for a proposed separately versioned fragment. It
is not a material catalog, a proof of complete runtime initialization, or a
validated runtime observation. All acceptance gates remain false. The current
47-field Item gameplay contract is unchanged.

## Integration contract

After the existing fixed initialization and culture/context validation, call:

    object materialObservation = CaptureMaterialObservation(samples, count, culture, context);

The enclosing collector owns root-schema integration, the closed method/field
profile, build inputs, and its output-byte budget. Add `MaterialObservation.cs`
and `MaterialObservationSelfTest.cs` to its explicit compilation list and call
`MaterialObservationSelfTest.Run()` from the original-code-only self-test path.
No game input is needed by those graph tests. Compiling the collector does not
establish that running the game or any fixed getter is safe.

The 17 pinned getter methods are LinkedAlternates, Alternates, GetStyleOverride,
SubTiles, StyleHorizontal, Style, StyleWrapLimit, StyleLineSkip, StyleMultiplier,
Width, Height, RandomStyleRange, SpecificRandomStyles, CoordinateHeights,
CoordinateWidth, CoordinatePadding, and CoordinatePaddingFix. Their profile keys
are `material` plus the property name. Material fields have separate `material.`
keys; the shared TileID Count field uses `tileId.Count` so other fragments reuse
the same token descriptor. `placeStyle` must not be added to the existing Item gameplay-field array.

The exact client SHA-256 remains
`960a03bff6050cf7be16dfc1a7b19e10fc2c4f8f835a6a3b135a50dd9e6ba2f3`.
Data-only metadata inspection supplies exact owner/name/token/signature and IL
hash descriptors. The Count bound of 754 comes from the independent pinned
TileID Count proof, not from the number of true flags or registrations.

## Primitive schema, version 1

The fragment is `pinned-material-observation-fragment`, status `PARTIAL`, and
`evidenceLevel=DERIVED_ONLY`. It carries source SHA-256, game version, active
culture, enclosing context, itemCount, tileCount, and tileObjectDataCount.
`isolationVerified`, `initializationVerified`, `sourceSemanticsVerified`,
`complete`, and `publishable` are all false.

- `frameImportant`: a cloned dense boolean array of exactly the independently
  observed and pinned tileCount (754).
- `pixelFlags`: three independently observed, cloned dense boolean arrays under
  the original source names `tileSolid`, `tileSolidTop`, and `tileSand`. Each must
  have exactly the same 754-entry TileID domain. These are fresh inputs to the
  existing stable pixel selection policy, not newly inferred classifications.
- `itemPlacements`: one row for each requested item ID 1 through itemCount−1.
  Each contains requestedId, samplePresent, resolvedType, createTile, placeStyle.
  A missing sample has samplePresent=false and null values for the latter three
  facts. A present null/incorrect-type sample fails. Signed int facts and resolved
  type mismatches are preserved. The downstream required item join must reject
  missing samples; this fragment never invents a placement or substitutes a
  requested ID for a resolved type.
  Each row also carries `englishValue` and `localizationKey`, observed separately
  for its requested ID via the pinned `Lang.GetItemName(int)` overload. Its return
  must be the exact pinned game-assembly LocalizedText type. The collector reads
  the string EnglishValue backing field and reuses the existing Key field pin;
  both retain null/empty values without trimming, synthesis, or fallback.
  This language-cache observation is independent of sample presence and resolved
  Item.type. The selected zh-Hans culture is never switched for this capture.
- `tileRoots`: one row for each tile ID, with tileId, registrationPresent, and
  nullable nodeId. `registrationPresent` distinguishes an absent trailing
  registry entry from an existing null registration. The independently observed
  registry count may be less than tileCount. Entries beyond the pinned tile
  domain fail. No absent registration is silently represented as a present null.
- `nodes`: an identity-deduplicated, zero-based, deterministic breadth-first list.
  Roots are interned in tile order. For each queued node, SubTiles are interned
  before Alternates, preserving all null slots, repeats, order, and child cycles.
  Null collections stay null; empty collections stay empty.

Node fields are nodeId, subTiles, alternates, linkedAlternates, width, height,
coordinateWidth, coordinateHeights, coordinatePadding, paddingFixX, paddingFixY,
style, styleMultiplier, styleHorizontal, styleWrapLimit, styleLineSkip,
randomStyleRange, specificRandomStyles, and hasGetStyleOverride. All numeric
source facts remain signed, including duplicates and negative random values.
Point16 padding fixes are read as signed short fields and promoted to int.
`hasGetStyleOverride` is an explicit unsupported-semantics flag: no delegate is
serialized or invoked.

## EnglishValue source meaning

Fresh pinned IL inspection establishes that `LocalizedText.get_EnglishValue`
returns its nullable string backing field directly. `LocalizedText.SetValue`
updates that field only while LanguageManager exists and its active culture is
GameCulture.DefaultCulture; the pinned GameCulture initializer chooses en-US as
that default. This observes the retained source field and does not promise that
all cache entries were initialized, that default culture is immutable, or that
every value is a usable translation. Missing or empty values remain explicit.

`Lang.GetItemName(int)` uses its cache or LocalizedText.Empty for the positive,
bounded requested IDs collected here. Its negative-ID remapping branch is not
entered. The extra runtime descriptor is `materialItemName` for that exact
method; the extra primitive field descriptor is `localizedText.EnglishValue`.
No language loading, culture switching, formatting, or English-value getter call
is introduced. App policy can match these fresh English strings to fresh
zh-Hans Item names, with its own explicit ambiguity/absence handling.

## Getter and graph safety boundaries

The exact getter set has no field writes or calls to renderer/world-placement
APIs in its inspected direct local-call closure. This is not transitive
initializer safety, successful runtime initialization, or a BCL/platform proof.
No user-selectable reflection or arbitrary property enumeration is supported.
Runtime node, list, module, array, and scalar types are checked exactly.

Most recursive getters use the static `_baseObject` when their own module is
missing. Before any selected getter runs, the fixed base object and its required
modules must have exact types and be nonnull. The pinned `get_StyleHorizontal`
has a distinct hazardous branch: when its own style module is null, it calls
itself on the same object. Every reachable node must have its own nonnull style
module before that getter is called. Failing these guards aborts collection;
the collector does not infer a fallback or conceal a recursion cycle.

Child-reference cycles are valid captured graph references and do not cause
recursive expansion. Object identity uses ReferenceEquals and
RuntimeHelpers.GetHashCode, never vendor Equals, GetHashCode, or ToString.

Engineering bounds, not proven real-data maxima:

- 65,536 registry entries, nodes, and entries in each child/integer array
- 1,000,000 root/child reference slots in aggregate, counting nulls
- 1,000,000 coordinate-height integers and 1,000,000 random-style integers
- 32 MiB encoded fragment, within the enclosing collector's separate budget

CoordinateFullWidth and CoordinateFullHeight are deliberately excluded: they
can calculate and mutate caches and liquid-placement state. No GetTileData,
CalculatePlacementStyle, Place, CanPlace, rendering, or texture loading is
needed by this fragment. A separate original arithmetic join can derive extents
and frame origins from the observed inputs, with checked C#-compatible arithmetic
and explicit unsupported geometry/random/override decisions.

## Verification limits

The accompanying Python tests are source-contract checks. The C# self-test seam
covers identity deduplication, child self-cycles, root and child ordering,
registered-null versus absent roots, null versus empty collections, preservation
of signed values and duplicates, detached arrays, preflight rejection before
capture, cumulative limits, and a 2,048-node nonrecursive chain. Report C# compile
and self-test results separately from those source checks. No game execution is
part of this implementation verification.

This fragment omits final map option colors/names; separate MapObservation captures them but does not establish the localized selection policy. Still absent:
reviewed ordered shape and compatibility-alias policy, marker selector/crop
policy, fresh texture provenance, and runtime/initialization acceptance. None of
those can be inferred merely from a successful bounded graph capture.
