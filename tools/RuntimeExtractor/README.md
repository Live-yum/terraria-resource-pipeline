# RuntimeExtractor

This helper loads an official `TerrariaServer.exe` in an isolated Mono container and calls its metadata initialization methods by reflection. It never calls the server's entry point. The helper uses only .NET Framework libraries and has no NuGet, MonoMod, graphics, GUI, or native hook dependency. The game assembly and its bundled FNA/DLL neighbors are read from the input mount.

## Build and run

From the repository root on Windows (PowerShell):

```powershell
docker build -t terraria-runtime-extractor:local tools/RuntimeExtractor
$serverDir = 'C:\Users\depths\Desktop\Tdecoder\TerrariaServerHook\server\1458\Linux'
$out = Join-Path $env:TEMP 'terraria-runtime-output'
New-Item -ItemType Directory -Force -Path $out | Out-Null
docker run --rm --network none --read-only --cap-drop ALL `
  --security-opt no-new-privileges --memory 256m --memory-swap 256m `
  --pids-limit 128 --tmpfs /tmp:rw,nosuid,noexec,size=32m `
  --mount "type=bind,source=$serverDir,target=/input,readonly" `
  --mount "type=bind,source=$out,target=/output" `
  terraria-runtime-extractor:local `
  --server /input/TerrariaServer.exe --output /output
python tools/RuntimeExtractor/verify_probe.py $out
```

The standalone Dockerfile copies and compiles all extractor `*.cs` sources, including `OfficialTextureBindings.cs` and `PlayerTextureClosure.cs`, like the root build. For compilation only (without running the helper or loading a game assembly), use `docker build --target build -t terraria-runtime-extractor:compile tools/RuntimeExtractor`.

Use the directory containing the Linux release `TerrariaServer.exe` and `FNA.dll`. The image is built from the official `mono:6.12` image, verified on Docker 29.7.2. No image is pushed.

## Output contract

`result.json` is written last. Protocol 1 records the game version, assembly SHA-256, each NDJSON family's relative path/count/SHA-256, ID domain counts, complete MapHelper palette ranges, capabilities, `requiredMissing` for actual missing finite outputs, nonblocking `warnings` about dynamic contexts, errors, and Linux memory measurements. The legacy `missing` field aliases `requiredMissing`. All NDJSON files use UTF-8 without BOM and one stable `id` per line.

When `TRP_TEXTURE_DIMENSIONS` points to a read-only NDJSON index inside the container, the helper also writes `texture-references` and `player-draw-plans`. Each index row has an `id` Content/Images asset key and positive integer `width`/`height` fields. Mount the index separately and set the variable to its container path. Without the index, the two-argument command above still exports metadata and marks texture coverage and player draw plans unavailable in `capabilities`/`requiredMissing`. The main pipeline supplies this private index after scanning Content XNB headers.

The families are `ids`, `localization`, `items`, `item-field-schema`, `item-tooltips`, `item-ui-tooltips`, `research`, `tiles`, `walls`, `map`, `map-palette`, `map-lookup`, `paints`, `pixel-candidates`, `tile-sets`, `wall-sets`, `tile-object-data`, `mount-layouts`, `armor-sets`, `prefixes`, `buffs`, `bestiary`, `npc-frames`, `dye-shaders`, and `player-layouts`. `map-palette` includes every `MapHelper.colorLookup` entry, including liquid and background colors; `map-lookup` includes all TileID and WallID slots, including IDs with no map option. `mapLayout` also records `Main.curRelease`. `pixel-candidates` contains map-option/paint RGB from the game's `MapTile.Create` and `MapHelper.GetMapTileXnaColor`; stability uses game flags and four `CreateMapTile` samples. An unstable candidate remains present with an exclusion reason.

Item gameplay contains supported public fields: scalar values, enum name/value, Color, Vector2, Point, Rectangle, LegacySoundStyle, and bounded primitive arrays. `item-field-schema` identifies excluded field types. Base tooltip lines come from `Lang.GetTooltip`; `item-ui-tooltips` uses `Main.MouseText_DrawItemTooltip_GetLinesInfo` after `ArmorSetBonuses.Initialize/BuildLookup` with a fixed default player, sample item, and keyboard profile. World, equipment, event, chest, and inventory context can alter in-game UI text, so those variants are not claimed. The real 1.4.5.8 probe produced nonempty UI text in both languages for all 6,195 items without reflection errors.

`items` also records the actual `ContentSamples.ItemsByType` sample type and status (`direct`, `alias`, or `unavailable`). An ID can retain a localized legacy name while its current sample has `type=0`; prefix eligibility for that ID is then empty. `rollablePrefixes` comes from the sample's `Item.GetRollablePrefixes`, and `eligiblePrefixes` checks each candidate with `Item.TryGetPrefixStatMultipliersForItem`. `prefixes` records the game's localized names, official pool membership, reduced natural chance, stat/value multipliers, and accessory effects from `Player.GrantPrefixBenefits`. `buffs` records localized names/descriptions, nine indexed `Main` flags and indexed `BuffID.Sets` fields. Dynamic buff text handlers are identified by class, not executed in an invented world or player context. These families report current game behavior without substituting historical static app tables.

`tile-sets` and `wall-sets` contain their indexed `ID.Sets` fields, including `Main.tileFrameImportant`. `tile-object-data` derives placement styles, alternates, random variants and frame origins through the game's `TileObjectData.GetTileData/CalculatePlacementStyle`. It covers 389 of 754 TileID slots and 389 of 412 frame-important IDs; the other 23 frame-important IDs have no `TileObjectData` definition and may use specialized drawing code. This family does not assert complete frame selection for them. `mount-layouts` contains all 66 `Mount.Initialize` configurations and their frame interval/offset data; server-mode texture slots and dimensions are unbound, so source rectangles require Content dimensions and client draw context. `player-draw-plans` captures official `DrawData` before SpriteBatch submission for sampled profiles when Content dimensions are supplied; dynamic shaders, render targets, and every possible player state are outside its capability. Bestiary rows include tracker types and exact provider rules from private runtime fields: kill thresholds and quick unlock, sight/chat checks, Gold Critter's global sighting gate, and Highest-of-Multiple child rules. Salamander/Shelly/Crawdad also depend on the current world's `NPC.cavernMonsterType`; this is recorded as a conditional capability. Unknown providers are explicit and enter `requiredMissing`. `npc-frames` maps negative net IDs with `NPCID.FromNetId`, records the mapped frame count, and resolves custom Bestiary textures before ordinary NPC assets against actual Content keys. NPC frame counts and draw offsets remain metadata, not rendered sprites. Player frame rectangles, hand offsets and renderer method inventory alone do not describe complete layered animation frames.

`dye-shaders` reads the game's Armor and Hair registries after `DyeInitializer.LoadArmorDyes/LoadHairDyes` run once before `ContentSamples.Initialize`. Each row records the item ID, shader ID/class/pass, color, secondary color, saturation and opacity from the live shader object. Legacy Hair delegates also record their method name and IL hash; their player/world-dependent output is not precomputed. Dedicated mode suppresses `UseImage` binding and no GPU passes run, so dynamic shader images are not claimed as bound. `LoadMisc` is not called because this family covers item dyes, not miscellaneous screen effects.

`armor-sets` streams 1,106 IDs across all 16 `ArmorIDs` nested domains, retaining each domain's declared `Count` source and all public indexed `Sets` fields. It also serializes the game's compound `WingStats` and cape pairing structs. `RocketBoots` has no declared `Count`, so its seven ID slots are explicitly marked as derived from the highest public constant. No game `Sets` field is silently omitted.

`texture-references` checks actual client binding rules from `AssetInitializer.LoadTextures` and the runtime `ItemID.Sets.TextureCopyLoad` array against every key in the supplied dimensions index. It emits one row per Item/NPC/Tile/Wall ID with the requested key, resolved source ID, actual Content key, and status. The current 1.4.5.8 probe resolved all 6,196 items (including 67 shared-texture IDs), 697 NPCs, 754 tiles, and 366 drawable walls. Wall ID 0 is excluded because `WallDrawing` skips `wall <= 0`. The official tile 650 file is spelled `TIles_650.xnb`; lookup ignores case but records that exact spelling for file access. Case-insensitive collisions in the index fail extraction, and truly missing referenced images enter `requiredMissing` with their ID/key.

The actual 1.4.5.8 Linux server completed with 27 families and the complete 15,123-image dimensions index under the 256 MiB Docker hard limit. The full run emitted 36,692 player draw plans with zero errors and zero `requiredMissing` entries. `result.json.metrics.stages` keeps every measured stage, including the player drawing peak; process and cgroup measurements are excluded from public capabilities. The random seed is fixed at zero for repeatable extraction.
