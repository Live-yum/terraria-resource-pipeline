# Materials two-role projection

`resource_pipeline.materials_assembler.assemble_material_resources(records=..., policy=...)`
returns `(objects, receipt)`. Objects contain only fresh `materials.base` and
`materials.rules` canonical JSON bytes. Inputs are normalized named records, not
old catalogs or raw archives. This is an assembly boundary, not a Terraria source
extractor. It runs no game code, reads no filesystem, and publishes nothing.

## Closed input schema (v1)

All keys below are required; additional keys are rejected. Numbers are strict
integers (booleans rejected), IDs/frames/style/item fields are in 0..65535.
Text is nonempty, valid Unicode and at most 1024 UTF-16 units.

`records`:

- `schemaVersion`: 1
- `gameVersion`: dotted numeric version, three or four components
- `tiles`, `walls`, `paints`: nonempty arrays, each at most 65,536 records
- Each record: `id`, `name`, `color` (`#RRGGBB`), `internalName` (text or null)
- Tile records additionally require `frameImportant` (boolean)

IDs must be unique per domain; tiles must cover contiguous IDs starting at zero.
This structural rule does **not** prove the highest tile ID is the game's final
ID. Walls/paints need not start at zero. No omitted name, color, flag or ID is
invented. Output base rows are `[id,name,lowercaseHex,internalName]`, sorted by ID.

`policy`:

- `schemaVersion`: 1; `gameVersion`: exact match with records
- `consumerCommit`: 40 lowercase hex characters
- `policyId`: 1–128 lowercase ASCII letters/digits/dot/underscore/hyphen,
  starting with a letter or digit
- `algorithm`: `viewer-materials-projection-v1`
- `materials`, `shapes`: nonempty arrays; `variants`: array (possibly empty)
- Each policy array is limited to 65,536 entries

A material has exactly `tileId`, `style`, `alternate`, `random`, `name`, `frameX`,
`frameY`, `layout`, `itemId`. A layout is null or exactly `width`, `height`,
`coordinateWidth`, `coordinateHeights`, `padding`. Dimensions and coordinate
heights/widths are positive; padding is nonnegative; heights has exactly `height`
elements. The aggregate heights budget is one million values. For null layout,
both frames must be null. Otherwise both frames must be integers.

Output materials are `[tileId,style,alternate,random,name,frameX,frameY,width,
height,coordinateWidth,coordinateHeights,padding,null,null,itemId]`. Slots 12/13
are explicitly unused placeholders: the app ignores them and they are not
claimed as game facts. The compound key is
`tileId:style:alternate:random:itemId`; duplicates are rejected. `itemId` is
structurally validated only; an independent producer must prove its item link.

A shape has exactly `tileId`, `name`, `frameX`, `frameY`, `layout`, `mode`.
Mode is `exact`, `layout` or `auto`. `frameX` is always an integer. `frameY` may
be null only in exact mode. Null layout is legal only in exact mode. Output is
`[tileId,name,frameX,frameY,width,height,coordinateWidth,coordinateHeights,
padding,mode]`. The nullable exact-frame rule matches the actual consumer.

A variant has exactly `tileId`, `subId` (text), `name`, `layoutKey` (text or null).
Explicit keys must reference a material of the **same** tile. Each tile/subId
pair is unique. An unbound alias requires an existing material or shape for that
tile, so the consumer does not build an alias from an absent layout. Null keys
retain the consumer's explicit type-only/auto compatibility semantics.

All rule list orders are preserved: they control selection, shape index keys
and first-shape fallback. The assembler does not sort away this policy. A framed
material with no layout, or tile 171 (the client's existing special case), must
have a same-tile shape; otherwise the client would throw at projection time.
`frameImportant` is emitted as the complete dense boolean array.

Input/output serialized objects are bounded to 32 MiB each; cumulative text has
a stricter 16 MiB bound. Oversized, malformed, sparse, mismatched and ambiguous
inputs fail before any output is returned. Caller file readers must also bound
bytes and reject duplicate JSON keys before constructing these dictionaries.

## Pixel and marker binding

The receipt records exact canonical records/policy SHA-256, each output's bytes
and SHA-256, and `materialBaseSha256`. `materials.rules` also explicitly binds
that base hash. Pass the fresh base bytes and this digest as
`verified_base_sha256` to `assemble_pixel_resources`; the name means byte
identity verification, **not** semantic verification. Pixel policy is a separate
reviewed input; changing base bytes requires rebuilding its dependent outputs.
The marker assembler uses the same fresh base bytes/hash.

## Proof boundary and actual sources

The receipt always says `DERIVED_ONLY`, `sourceSemanticsVerified=false`, and
`publicationApproved=false`. Digest agreement, passing the app validator, or
passing an assembly CLI does not authorize release or satisfy source coverage.
Still required independently:

1. Complete authoritative tile/wall/paint domains and localized-name provenance.
2. Final map-option selection and colors, paint inputs, actual map/shader/coating
   behavior (paint input RGB alone is insufficient).
3. Final frame-important flags, TileObjectData layouts, style/alternate/random
   choices and verified item-placement relationships.
4. Separately reviewed app shape, variant, compatibility and precedence policy.

This implementation was compared with the actual client's
`shared/game/material-resource-contract.mjs`, `shared/game/material-catalog.js`,
and `features/world-editor/pages/services/tile-materials.js`, plus the consumer
field ledger. The validator's two-role shapes and runtime fallback were read;
no private catalog, images, or game tables were copied into fixtures.

## Validation

Fixtures are small, independently invented records. Run:

```sh
PYTHONPATH=src python -m unittest discover -s tests -p test_materials_assembler.py -v
TERRARIA_CONSUMER_ROOT=/path/to/viewer-app PYTHONPATH=src \
  python -m unittest discover -s tests -p test_materials_assembler.py -v
```

The optional real-app oracle derives pixel outputs from the assembled base,
checks the shared digest and candidate fingerprint, and feeds all four generated
roles into the client's material validator and catalog projection; it also rejects
a stale candidate fingerprint after base mutation. The optional oracle requires
the app's installed Node dependencies and uses its own alias loader. It validates synthetic interoperability,
not real Terraria completeness or source-to-consumer acceptance.
