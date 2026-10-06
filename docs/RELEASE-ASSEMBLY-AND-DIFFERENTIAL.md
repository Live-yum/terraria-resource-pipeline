# Resource / engine assembly gate and real-game evidence

This change does not publish resources, merge PRs, change authority, or install
software. The 256 SHA-prefix image buckets and all resource schemas stay intact.

## Exact SRGB / TXCI workspace

`CubeWorkspace` owns one `int32` distance cube and one `uint16` label cube:
96 MiB rather than 128 MiB. Labels are safe because `maxCandidates == 65534`;
validity still comes from the distance infinity sentinel. The 24-bit RGB cube,
integer lower-envelope intersections, duplicate-site handling, unpainted priority,
wall-preference priority, and B/G/R TXCI distance tie ranks are unchanged.

Both SRGB passes and the following TXCI build reuse those same arrays serially.
`Release()` drops references after the final stage. This is not a global pool and
must not be shared concurrently. Disk scratch ownership / cleanup is unchanged.

The extraction report now separates exact tracked workspace bytes from sampled
Go heap / RSS. Inner phases cover transform, spool, runs/bricks and output. RSS
is sampled, not a guaranteed exact maximum; cgroup memory.peak is cumulative.
The Go regression compares all 16,777,216 cells with the pre-change transform,
including duplicate sites, midpoint ties and the maximum supported label. That
reference is an **algorithm regression**, not an executed Terraria game oracle.

## Release assembly identity

`python3 scripts/verify-release-assembly.py identity.json --evidence-root DIR`
requires the full tuple:

- appCommit (40 hex), engineSourceCommit (40 hex), extractorCommit (40 hex)
- gameAssemblySha256, resourceManifestSha256, builtinDescriptorSha256 (64 hex)
- authorityId plus canonical decimal-string sequence (never a JS number)
- both wld/plr engine manifests, exact ABI descriptions, wrapper/Wasm bytes,
  and separately captured identities from the loaded engines
- clean app build / extractor identity, app-embedded manifest hashes, the exact
  game assembly and resource/builtin manifests
- a complete executed game-versus-native differential report and its bytes

The `evidence` object maps `resourceManifest`, `builtinDescriptor`, `gameAssembly`,
`extraction`, `appBuild`, `authority`, and `differentialReport` to paths below DIR.
Each `engines` entry has `featureSet`, `manifest`, `artifactRoot`, `abi`, and
`runtimeIdentity`. `appBuild` has `sourceCommit`, `dirty:false`,
`builtinDescriptorSha256`, `engineManifestSha256:{wld:...,plr:...}`. Extraction
writes `.private/extraction-identity.json` with its embedded VCS identity. Docker builds can pass SOURCE_COMMIT/SOURCE_CLEAN only after a trusted clean-checkout preflight; both default to unknown/false. An
unknown/dirty build is usable for local investigation but cannot pass this gate.
Do not supply a source commit from a release version or function presence.

The verifier validates a captured approval snapshot, including its canonical
state digest and revocations. It does **not** authorize a publication or replace
the backend's current authority fence / revocation recheck. An older builtin
and a newer remote manifest may have distinct content hashes; both are recorded.
Assembly consistency is not cryptographic signing or proof that supplied build
attestations are trustworthy. Run producers and the verifier in the same trusted
build pipeline, in addition to existing platform artifact/ABI validators.

## Real-game differential run

`python3 scripts/run-game-differential.py --config CONFIG --evidence-root DIR --output NEW_DIR`
executes independent adapters without giving either the other's expected output.
Each case binds input and dependent fixture bytes by SHA-256, records adapter
stderr and normalized output bytes, and compares them. It fails closed when any
required category or identity is missing. Required categories:

1. map-header: name, ID, dimensions, liquid/gradient counts, tile/wall options
2. map-color-paint: precise palette/paint results, especially paint 29/30,
   walls, no paint, variant boundaries and all four liquid types
3. missing-tile: an in-domain tile whose palette entry is absent, with wall,
   liquid and background fallbacks (a null game tile is a separate semantic case)
4. background-rle: empty / missing-palette repeated tiles crossing surface,
   rock and underworld rows; native adapter really encodes and decodes an RLE run
5. player-conversion: original and native-converted real `.plr` bytes loaded by
   both implementations, with core fields and complete serialized inventory/equipment/banks (58 inventory slots; the transient mouse slot is not serialized)

The pinned decompiled reference is read-only at
`Live-yum/TerrariaDecompiledSource@8255d34616c780af12079425ac92a0a7aed87d71`.
`MapHelper.InternalSaveMap` writes format 326; TerraWasm writes 33083
(compressed bit plus release 315). Terraria's loader accepts both routes.
Whole MAP files are therefore **not** expected to be byte-identical. The adapter
compares the semantic header fields above; actual wire format/revision are retained
as diagnostics. Color/RLE/player results are exact, with no tolerance or masking.
A matching gameVersion string is not a substitute for this differential run.

Game adapter (compile using an already available Mono 6.12 / .NET Framework toolchain):

```
mcs -langversion:7.2 -optimize+ -r:System.Web.Extensions \
  -out:RuntimeExtractor.exe tools/RuntimeExtractor/*.cs
```

`gameAdapter` is `["mono","/absolute/RuntimeExtractor.exe","--oracle"]`.
It uses the actual supplied assembly's `CreateMapTile`, `GetMapTileXnaColor`,
`InternalSaveMap` and `LoadPlayer` methods; it does not copy their formulas.
`nativeAdapter` starts with `["python3","/absolute/scripts/native-game-adapter.py",
"--engine-root","/clean/TerraWasm","--build-dir","/private/native-build"]`.
It builds an exact clean checkout with installed GCC/zlib and calls real native
MAP/PNG functions. TMRT bytes must have the manifest digest at header bytes 64..95.
The source-native test build is not a Web/WeChat Wasm performance measurement.

CONFIG contains the full identity tuple, `gameAssembly`, `gameAdapter`,
`nativeAdapter`, `timeoutSeconds`, and a nonempty `cases` array. Each case has
`id`, `category`, `input`, `inputSha256`, `gameMethod`, `nativeEntryPoint`, and
`dependencies` mapping relative TMRT/PLR paths to exact SHA-256 digests.
Paths are private local inputs; no copyrighted game assembly/fixture is committed.
Map input JSON has `resourceManifestSha256`, `tmrt`, `tmrtSha256`, bounded
`width`/`height`, `ground`, `rock`, `worldId`, `worldName`, and `rows`.
Each row has y, active, type, frameX, frameY, wall, liquidAmount,
liquidType (native 1..4), tileColor, wallColor, invisibleBlock/invisibleWall,
fullbrightBlock/fullbrightWall. `background-rle` takes one row repeated over
height. Player input has original/converted relative `.plr` paths and a field
selection matching the game adapter. Converted bytes must be generated by the
app's real target-version conversion + native encode/readback workflow first.
An official game assembly only writes its current format; historical downgrade
loss policies remain separate app tests and must not be called game save oracles.

No real game assembly, actual player fixture, Go compiler, Mono or .NET runtime
was available in the implementation workspace. Real extraction/differential
execution therefore remains **unrun**. Python synthetic tests validate the
protocol gates only; no synthetic fixture is presented as real-game evidence.

## Opt-in Wasm experiments

`wasm-experiment-plan.py` enumerates 16/32/64 MiB, -O3/-Oz and scalar/SIMD.
It does not change CMake defaults. Before even planning eligible builds, each
compiler/SIMD variant needs exact Wasm/link-map/stack-analysis evidence hashes,
staticDataEnd, stackBytes, heapBase, minimumWorkspaceBytes and guardBytes.
The proof must be for the exact clean source commit. The planner reads actual immutable Wasm exports `__data_end`, `__stack_low`, `__stack_high`, `__heap_base`, rejects mismatched claimed bounds, computes the maximum acyclic call-graph stack, and rejects unresolved indirect calls, recursion and dynamic stack frames. Diagnostic builds must explicitly export those linker globals; existing defaults are unchanged. Failed proof blocks that
experiment; lowering initial memory without proof is not supported. Execution
requires `--execute` and already installed Emscripten/CMake.

For each built variant run the actual differential suite and measure first result,
p95 step time/input latency, growth count, peak tracked memory, and separate
Android/iOS/Web device memory. Host-native timings or accounting are not device
acceptance. SIMD must remain an opt-in capability experiment until platform
support and correctness are measured.

## Commands and verification classification

```
go test ./...
go vet ./...
python3 -m unittest discover -s scripts/tests -v
python3 -m py_compile scripts/*.py
```

The existing CI Go job runs its usual suite plus Python protocol tests. Missing
real fixtures must not be converted into a green release acceptance result.
