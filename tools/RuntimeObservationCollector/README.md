# Fixed Windows consumer-data observation collector

Status: **PARTIAL, original source implemented; compile NOT_RUN locally; game/vendor execution NOT_RUN.**

This is a concrete diagnostic collector for one exact Terraria client, not a generic reflection launcher and not a new pipeline/backend trust profile. It implements fixed initialization calls, bounded loops, exact field reads and primitive JSON output. Source descriptors were checked against the pinned PE as data. Neither those checks nor a future successful invocation establish correct initialization. Every observation retains `initializationVerified: false`, `sourceSemanticsVerified: false`, `complete: false`, and `publishable: false`.

The existing 110-resource publication policy is unchanged. The fragment is not a schema-2 producer receipt and cannot be submitted directly as complete `runtime_item_adapter` input.

## What is implemented

- Only client SHA-256 `960a03bff6050cf7be16dfc1a7b19e10fc2c4f8f835a6a3b135a50dd9e6ba2f3`, assembly Terraria 1.4.5.8. No alternate source, user-chosen method, assembly name, token, type, expression, serialized object or invocation argument is accepted.
- 65 fixed method descriptors, including exact metadata signatures and IL hashes; 162 fixed game field descriptors plus three fixed XNA Vector3 fields and one fixed XNA Color field with exact tokens/signatures. `fixed-profile.json` and `FixedProfile.cs` are deliberately redundant, cross-checked by original Python tests.
- All 6,195 positive requested IDs from independently pinned `ItemID.Count == 6196`; 47 named actual Item fields, genuine `Item.Name`, ContentSamples persistent-ID dictionary entries, and the actual research-cap API's bool/out-int result.
- All seven PrefixLegacy boolean arrays, all eight prefix pools, six ItemID sets in raw version2 (the adapter's four plus diagnostic material and Deprecated), and all 20 SortingPriority arrays. Arrays retain ID zero and must have the full observed Count. Prefix pool members are range-checked.
- Names, research absence and requested-ID versus resolved-type differences are preserved explicitly. No missing Item property becomes zero/false, no name/persistent-ID is synthesized, and absent research membership is not silently promoted to a complete consumer value.
- All priority final values are retained, including -1 and 0. Explicit override provenance must be joined from the separate source-literal producer. Dropping all -1 values is forbidden.
- Explicit user opt-in plus Windows x86/Microsoft CLR4 gates, exact input/dependency hashes, locked read-only source handles, local/no-reparse paths and a new temp-only output directory. An existing output directory is rejected.
- Windows Job resource limits: one process, 1 GiB process memory, 60 seconds user CPU. A separate original-code watchdog exits after 120 seconds or more than 64 threads. Job setup failure aborts. Controlled output is at most 64 MiB, JSON depth 12, string length 4,096 and array length 65,536. These are engineering ceilings, **not proven sufficient runtime limits**.
- Only original primitive/container JSON is accepted. No arbitrary properties, vendor `ToString()`, general object serialization, type tags, deserialization, binary formatter or user-controlled reflection selection.

## Initialization boundary and exact source order

`boundary-evidence.json` retains data-only method hashes and relevant source instruction offsets. The code follows the Item-relevant subsequence rather than invoking broad game startup:

1. Program static initializer; set its SavePath to the disposable output directory **before** Main static initialization.
2. Main static initializer; then set `dedServ=true`, `netMode=2`, a deterministic original API `UnifiedRandom(0)`, and the source-equivalent local-player bootstrap.
3. Load embedded Simplified Chinese localization via `LanguageManager.Instance.SetLanguage("zh-Hans")`, then `Lang.InitializeLegacyLocalization`. Run ContentSamples.Initialize (the normal Main.Initialize calls it at IL244, before Initialize_AlmostEverything at IL386).
4. TileObjectData.Initialize (AlmostEverything IL91); CreativeItemSacrificesCatalog.Initialize (IL278).
5. Main.Initialize_TileAndNPCData1 (IL469), Initialize_TileAndNPCData2 (IL474), Initialize_Items (IL479).
6. The source loop IL484–542: Projectile IDs 1..<Count, real constructor/SetDefaults, set projHostile when hostile and projHook when aiStyle==7. This fixes a genuine missing prerequisite for Item.SetDefaults. It does not default these arrays from guesses.
7. Recipe.SetupRecipeGroups (IL544), ArmorSetBonuses.Initialize/BuildLookup (IL554/559), ItemID.Sets.PostSetupContent (IL564), TileID.Sets.PostSetupContent (IL569).
8. Item-relevant `DyeInitializer.LoadArmorDyes` then `LoadHairDyes`, the first two calls of source DyeInitializer.Load (AlmostEverything IL579). ContentSamples.DyeShaderIDs.Initialize follows (IL584).
9. The source Recipe allocation loop (IL589–615), SetupRecipes (IL617), and FixItemsAfterRecipesAreAdded (IL622).
10. PrefixLegacy ItemSets/Prefixes initializers, then exact primitive observation.

Recipe.SetupRecipes' tail calls UpdateWhichItemsAreMaterials, UpdateWhichItemsAreCrafted and UpdateMaterialFieldForAllRecipes. FixItemsAfterRecipesAreAdded calls Item.Refresh(false); the verified branch shape means this forces non-air samples through SetDefaults again, rather than skipping an unchanged variant. Thus this implementation includes the post-recipe material refresh instead of emitting initial all-false material flags.

ArmorShaderData.UseImage and HairShaderData.UseImage branch around Assets.Request when dedServ is true. ShaderData's constructor stores a shader reference/pass name without dereferencing the shader. This supports the selected dye-table approach; it does **not** prove all shader subclasses or transitive initializers have no effects.

No `Program.Main`, `Main.Initialize`, `Initialize_AlmostEverything`, `Main` instance construction, game loop, content/texture loader, LoadMisc, Netplay.Initialize, NetworkInitializer.Load, BoringSetup or player drawing method is directly invoked. Excluded unrelated subsequence roots include UI/creative sorting, conditional-dialogue registration, shop setup and rendering startup. Their irrelevance to every observed field has **not** been fully proven. Unexpected dependencies, failed type initialization or missing arrays abort without a success observation. No fallback stubs or exception-swallowed defaults are used.

### Main initialization is not inert

The dedServ write cannot prevent the preceding Main type initializer. The exact pinned initializer constructs renderer helpers, GameTime, RasterizerState and graphics BlendState, and has a conservative path through Netplay/RemoteServer/TcpSocket to TcpClient creation. Avoiding game entry does not prove absence of graphics/native/socket effects. The collector does not invoke a full renderer, but cannot promise that renderer helper construction is avoided.

Likewise XNA core/Graphics are mixed-mode x86 binaries. Module startup can run native CRT code as soon as they load. AssemblyLoad hash checks are binding diagnostics, not a pre-execution security monitor. Job resource limits are not network/filesystem isolation. The `--isolated-windows-x86` flag is an acknowledgement of external prerequisites; it does not create or verify them.

## Required external execution environment

Actual game/vendor execution requires a separate explicit user approval and a prepared disposable Windows VM/Sandbox with:

- Microsoft .NET Framework 4.8 in an x86 process, compatible genuine XNA dependencies, and an independently reviewed/pinned Windows/.NET image baseline.
- No network interface or equivalent externally enforced deny-all egress, no credentials/tokens, no host profile, clipboard, drive, desktop or writable shared-folder access. The input is read-only; only a disposable temp/output area is writable. Limits on the VM's CPU, memory and lifetime apply independently of the in-process watchdog.
- Inputs independently hash-verified before launch, and OS/CLR/native module changes and unexpected children/denied I/O audited outside the collector. Use a clean snapshot for each run. The collector may fail when containment denies a native/socket effect; never weaken the boundary automatically.
- No automatic installers, runtime download, EULA acceptance, credential setup, framework replacement, MonoGame/FNA substitution or permission/security changes. Those are not performed by this code.

The implementation's four non-OS native pins and three XNA managed pins come from the official Microsoft XNA 4.0 Refresh MSI data-only audit (official page: https://www.microsoft.com/en-us/download/details.aspx?id=27598). Presence/hash verification is not vendor signature verification, license review or an assertion that these historical runtime versions are safe for general deployment. Nothing here redistributes vendor bytes.

Managed fallback binding accepts only the five explicitly pinned embedded resources and three pinned XNA assemblies. Unlisted Steam/Rail/SteelSeries/audio dependencies fail closed rather than being fetched or loaded by this resolver. XNA Core/Graphics explicitly declare `Microsoft.VisualC, Version=10.0.0.0, Culture=neutral, PublicKeyToken=b03f5f7f11d50a3a`. The platform allowlist accepts exactly this identity only from the GAC and records its loaded bytes. Microsoft documents Microsoft.VisualC.dll as a backward-compatibility .NET Framework assembly ([official API reference](https://learn.microsoft.com/en-us/dotnet/api/microsoft.visualc.iscxxreferencemodifier?view=netframework-4.8.1)). Availability and the exact implementation on the future VM remain untested; no download, alternative identity or invented byte pin is supplied. Actual loaded framework/native hashes are diagnostic evidence; **OS/runtime baseline acceptance remains outstanding**.

## Original-code-only build and self-test

On Windows with the built-in Microsoft Framework compiler:

```powershell
powershell -NoProfile -File tools\RuntimeObservationCollector\Build.ps1 -SelfTest
```

This compiles ten original C# files against only standard System/System.Core libraries, targets x86, and runs only `--self-test`. No NuGet restore, network, game/vendor reference or source binary is needed. The self-test branch returns before path handling, Windows Job setup, game reflection/binding and game/dependency loads. The tests cover primitive serialization/escaping and exact ordering, numeric narrowing and exact Single-to-double JSON promotion, explicit rejection of arbitrary objects/getters/ToString, nonfinite values, bad Unicode, depth/array/string/output limits, hash mismatches, opt-in argument shape and fixed descriptor uniqueness.

The project targets .NET Framework 4.8; source syntax remains compatible with the built-in CLR4 C# compiler. `App.config` enables the legacy activation policy needed for the exact old mixed-mode runtime. The builder emits original executable/config files in its local `bin` directory only.

The authoring Linux environment has no csc, mcs, dotnet or Mono compiler. Local compilation and C# self-test execution are **NOT_RUN**. A future Windows CI self-test is only original-code verification; it must not be described as Terraria initialization acceptance.

## Later explicitly authorized observation command

Prepare the input directory with exactly:

- Terraria.exe with the fixed client hash
- Microsoft.Xna.Framework.dll, Microsoft.Xna.Framework.Game.dll, Microsoft.Xna.Framework.Graphics.dll
- msvcr100.dll, msvcp100.dll, d3dx9_41.dll, X3DAudio1_7.dll

All hashes are in fixed-profile.json. Embedded managed DLLs are obtained only from the pinned PE's named resources when requested. Do not copy them beside the executable or add extra input files. Windows system dependencies come from the externally approved VM image.

Only after the external isolation and separate runtime authorization exist, within that disposable environment:

```powershell
.\RuntimeObservationCollector.exe --observe-pinned-client --i-understand-this-executes-game-code --isolated-windows-x86 --input-and-new-output C:\ReadOnlyInput "$env:TEMP\terraria-observation-unique-new-directory"
```

This is documentation, not current permission to run it. The program writes one `observation.partial.json` only after every selected stage and output validation returns. A failure yields a nonzero exit and the last fixed STAGE identifier on stderr, with no success artifact. Never retry by skipping the failed initializer, widening reflection, suppressing hash checks or changing the expected input.

## Acceptance work still required

1. Compile and pass original-code-only Windows self-tests.
2. Obtain approval and external isolation, confirm actual loader/native behavior, and fix any diagnosed initializer prerequisite using exact source evidence.
3. Validate the initializer subsequence against all 47 field-use dependencies, normal-world/variant context, genuine localization and research presence semantics. The current world flags (including infectedSeed) are reported and must be false; actual GameMode/Difficulty/expert/master/Mechdusa properties, world-data presence, nullable difficulty override and selected culture are recorded, and every observed Item Variant must be null; no complete state proof is inferred from those ten flags alone.
4. Run independent isolated reruns and compare the normalized observations (not nondeterministic environment receipts); verify no missing requested rows, preserved resolved-type/absence differences, correct post-recipe material/dye values and full registry domains against independently derived static facts.
5. Join the independently selected Item domain, explicit priority override provenance, complete prefix facts and source hashes before `runtime_item_adapter` validation. Resolve deprecated/air IDs and absent research membership under an explicit producer contract. Do not turn this diagnostic fragment into a trusted receipt.

Data-only pin recheck:

```sh
PYTHONPATH=src python tools/RuntimeObservationCollector/audit_fixed_profile.py --client /path/to/pinned/Terraria.exe
PYTHONPATH=src python -m unittest discover -s tests -p test_runtime_observation_collector.py -v
```

The receipt records `requestedExecutionMode` as an operator declaration and `isolationVerified=false`. The process cannot attest that the external VM/network/host-directory boundary was established. A command-line flag never establishes that boundary.


## Player observation extension

The `playerObservation` fragment additionally records bounded buff name/description API results, initialized armor-shader parameters with an exact four-class allowlist, six complete face boolean registries, and positive hair-dye item/shader links. Buff and face counts are observed; the joining adapter independently checks their pinned source shapes. Source strings are preserved, not replaced with fallback names. Shader ID values must match freshly captured Item fields. Vector3 components are read only through exact Core assembly/field signature bindings; no arbitrary object traversal or shader Apply occurs.

`dyes.image` is explicitly omitted: dedicated-server initialization does not retain that source asset name, and the current CPU consumer does not read the optional field. Main.debuff is recorded separately, not substituted for the application's curated negative-buff policy. All arrays are copied into the observation snapshot. Source-owned game facts, app-owned selection/rendering/history policies, and actual runtime acceptance remain separate.

The new player extension's compilation and game execution are NOT_RUN until its own exact-head Windows CI and explicit isolated runtime acceptance. Earlier PR8 compilation covers only the prior Item-only version. The optional data-only audit argument `--xna-core PATH` checks the four SDK field pins; without it, gameMetadataPinsVerified can be true while metadataPinsVerified and xnaFieldPinsVerified remain false.

Prefix names are captured from the exact pinned Lang.prefix LocalizedText array after the same zh-Hans initialization. IDs include zero, and observed empty text is retained; no display label is invented by the collector.


## Version2 material/map fragments

The raw collector document is now schemaVersion2, kind
`pinned-consumer-data-observation-fragment`. This is independent of the legacy
backend schema2 producer-certificate format. The Item join explicitly supports
both original raw version1 and version2; new diagnostic fragments do not become
material consumer proof through the Item-only join.

`materialObservation` records fresh placeStyle links, complete frame-important
and tileSolid/tileSolidTop/tileSand arrays, and a bounded identity-deduplicated
TileObjectData graph. It preserves signed style/random inputs, missing versus
registered-null roots, and override presence without invoking delegates. It
rejects the source StyleHorizontal null-module self-recursion path. It does not
invoke extent getters that mutate cached/liquid state; see MaterialObservation.md.

`mapObservation` invokes only the pinned MapHelper.Initialize entry, whose tail
calls Lang.BuildMapAtlas, after existing Item/player/material stages. It records
all tile/wall option indexes, exact packed source RGBA and final localized legend
name/key (including null/empty cases). Zero-option entries remain explicit. The
capture does not collapse options, choose generic names, paint pixels, select
stable candidates or claim complete renderer/map semantics. It snapshots arrays
and reads one exact XNA Color packedValue field from the pinned vendor assembly.

Original self-tests cover graph ordering/identity/cycles/bounds and map color
byte order, empty option rows and out-of-range lookup rejection. Those tests
still run before any game loading, OS Job installation or runtime opt-in path.
The expanded game initialization and captures remain NOT_RUN until separately
authorized isolated Windows acceptance. App option/name/shape/alias/marker
policies and material/pixel source joins remain unfinished engineering.

Material placement rows also retain source LocalizedText EnglishValue/localizationKey through one exact Lang.GetItemName call and one pinned backing string field. Missing or empty English values remain explicit, with no language switch or synthesized caption.
