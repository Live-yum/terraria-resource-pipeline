# TerraWasm、服务器元数据与现有 CDN 资源盘点

本文保留迁移前源码及旧CDN布局的调查快照。当前TMRT/TXCI、原生构建和新CDN验收结果见[交付记录](DELIVERY.zh-CN.md)。

核查日期：2026-10-02。范围为 `C:\Users\depths\Desktop\Tdecoder\TerraWasm`、`TerrariaServerHook` 和 `D:\Code\CLionProjects\terraviewer-images`；只读源码与现存产物。结论针对“上传同版本 TerrariaServer 和 Content ZIP 后自动发布应用资源”这一输入契约。当前已有文件不证明新版本或 Linux 容器端到端可运行。

## 一、TerraWasm 的版本数据与必须保留的边界

| 数据/用途 | 当前来源和实际入口 | 可远程化程度 |
| --- | --- | --- |
| `.wld` / `.plr` 读写 | `src/terra_wld.c`、`src/terra_plr.c` 和公开 `include/terra_world.h`、`include/terra_plr.h`；导出见 `exports.web.txt`、`exports.plr.txt` | **不可作为普通 CDN JSON 替代**。二进制文件布局、版本门槛、读写、校验仍必须在 WASM ABI 内。`terra_plr.c:1605-1693` 列出从旧版到 326 的字段/槽位门槛；`:2118` 起解析、`:3186` 起写回。世界 parser 同样含格式门槛。可独立更新引擎产物，但 CDN 数据不能改变已编译的解析代码。 |
| 地图 palette、tile/wall lookup、paint | `data/map-palette-1458.json` 经 `scripts/generate_color_data.py` 生成 `include/terra_color_data.h`（409,822 B）和 `src/terra_defaults.inc`（14,657 B）。生成器把版本 `33083`、tile 754、wall 367、背景/液体 lookup 编译进 C（`src/terra_defaults.inc:2-13`）；`src/terra_map.c:515-519,1061-1080` 使用它们决定 `.map` 类型和序列化头。`terra_color_data.h` 还编译了 tile/wall RGBA 和 30 个 paint RGB。 | **当前只有颜色表部分可注入**：`txw_set_color_tables` 在 `src/terra_mem.c:566` 接受 JS 内存指针与数量，渲染路径读取；但 `.map` lookup/option counts/版本仍在编译产物，不能只换 CDN 文件就支持新版本 `.map`。 |
| 全 RGB 像素画/marker 最近色索引 | `data/extracted/colors.generated.json` → `scripts/build_txci.py` → `scripts/terrax_color_index_v3_builder.py`。TXCI v3 的生成器使用 NumPy/SciPy；现存 `data/terraria_color_index.txci` 6,466,331 B、gzip 952,605 B，manifest 记录 20,026 候选及 SHA。`src/terra_txci.c:393` 能从内存载入；`src/terra_map.c:1095-1143` 的 `txw_set_marker_color_index` 把缓冲区装入当前 world。 | **可从 CDN 注入 TXCI v3 二进制**（导出 `exports.web.txt:35`），但生成端现有 Python/NumPy/SciPy 不符合新的“无外部依赖”要求；重写生成器必须以现有格式/查找优先级和结果作契约验证。不能把旧量化 `pixel/terraria_lut.bin` 当作 TXCI v3。 |
| tile frame、家具主题、NPC 旧名称 | `src/terra_map.c` 根据 `frame_x/y` 算地图选项；`src/terra_theme.c` 引用 `src/terra_theme_data.inc` 的家具映射；`src/terra_legacy_npc.c` 由 `scripts/generate_legacy_npc_names.py` 从 `NPCID.cs` 生成。 | 名称与图像可以远程；当前 map frame 选择、家具变换和 NPC 旧名解析是代码/编译表。版本升级要检查算法是否仍成立。 |
| icon atlas | `exports.web.txt:36-37` 的 `txw_set_icon_atlas/txw_clear_icon_atlas`；`include/terra_icon.h` 负责 ID 到 atlas index。 | 图集可远程给应用，再由 JS 注入；须维持其内存格式及 ID 映射契约。 |

`src/terra_plr.c` 顶部明确“version 字段保留为数据，布局仍相同时接受新旧版”，这不是“任意未来版本兼容”的保证。`src/terra_wld.c`、`src/terra_wld_guard.c` / `_task.c` 处理世界节区及字段，属于解析 ABI。版本升级必须用真实 `.wld`、`.plr` 往返验证，而非仅验证 CDN 包。地图 `.map` 头尤其依赖编译进来的 palette 大小与 lookup；把整个 `MapHelper` palette CDN 化需要新增注入结构及序列化的相应改动。

## 二、TerrariaServerHook 的可复用提取能力与语义执行依赖

真实入口 `src/Application/Program.cs:16` → `src/Metadata/MetadataExtractionExecutor.cs:19-69`。`TerrariaMetadataExtractor` 在 `:823-827` 以 `usePatchedServerCopy:false` 加载上传的 `TerrariaServer.exe`，通过 `src/Server/TerrariaAssemblyLoader.cs:31-96` 的 `Assembly.LoadFrom` 和邻近 DLL/嵌入 DLL 解析运行，不必启动正常世界生成，不修改服务器程序集。与普通 hook worldgen 的 patch 路径有区别。

输出覆盖：tile/wall ID、英语和简体中文名、MapHelper 颜色及 lookup、`tile_object_data`、`tile_frame_flags`、`runtime_assets`、paint 名称/颜色及 paint 后颜色、稳定单格表面、宝藏箱可存物品和前缀、bestiary/NPC 信息与 PNG、`game_data.json`、`viewer_resources.json`。`WriteMiniProgramCdnPack`（`:220-291`）生成 engine/UI/search 三包、hash 与别名、`manifest.json` / `latest.json` / `upload-manifest.json`。`BuildViewerResources`（`:955-999`）另收集 `Terraria.ID` 常量、程序集嵌入的 `Terraria.Localization.Content.*zh-Hans/en-US.json`、每件样本物品的公开 primitive gameplay 属性、研究数及弃用标记。该字段是**样本默认属性**，并非仅 `ItemID` 常量。

“不执行 .NET，只用 Go/C++ 静态读取 PE/IL 和 XNB”能可靠取出程序集资源 JSON、公开 literal ID 常量、XNB 原像素与明确的文件名；却无法仅靠元数据表完整得到派生值。现有 extractor 的 `EnsureBootstrapped()`（`:1004-1044`）执行 `Main.Initialize_TileAndNPCData1/2`、`TileObjectData.Initialize`、`ContentSamples.Initialize`、`MapHelper.Initialize`，还造 `Main.player`、切换语言。反编译依据 `code/Terraria.ID/ContentSamples.cs:837-883` 显示它循环调用每件 `Item.SetDefaults`、NPC/Projectile `SetDefaults` 并填研究覆盖；`code/Terraria/Item.cs:48666` 的 `SetDefaults` 是巨大分支；`code/Terraria.Map/MapHelper.cs:209` 由代码填 palette；`code/Terraria.ObjectData/TileObjectData.cs:2111` 由代码构造多样式 frame。要在无 CLR 条件下复刻这些值，等同维护版本相关的 IL 解释器或手译数万行游戏语义，升级风险高。静态“读字段值”不等于运行完成后的字段值。

语言资源已含 tooltip 文本键；`code/Terraria/Lang.cs:193,540-551` 经 `ItemTooltip.FromLanguageKey` 建 tooltip 缓存。现有 `viewer_resources.json` **导出原始语言 JSON，但没有每物品解析后 tooltip/说明的独立规范字段**。需要确定 UI 是消费原始键，还是额外执行 `Lang.GetTooltip`/动态格式化；不能把“中文名已有”误写成“完整物品说明已适配”。同理 bestiary 是调用游戏数据库填充而非简单静态 ID 枚举（Hook `:1838` 还重建排序）。`runtime_assets` 和 paint 候选对 `MapHelper.GetMapTileXnaColor` 运行时取样（`:2430-2522`）；tile frame 由 `TileObjectData` 的坐标及 `MapHelper` 反推（`:2608-2950`），复杂帧可选外部 `--frame-reference`，故仅上传 server+Content 的最终覆盖率应单独验收。

NPC 图像输出从 `Images/NPC_{id}.xnb` 和 bestiary 自定义贴图选取，既用 Content 又用游戏逻辑给出的 frame/方向/缩放；见 `:1316-1399,1465-1568`。`System.Drawing` 与 TConvert 的 XNB/LZX 源在 Hook 项目中使用（`TerrariaServerHook.csproj:35-42`）。因此这个实现能作**语义和样张 oracle**，但不是独立 Go/C++、无外部依赖的生产提取器。

Linux/Docker：`TerrariaServerHook.csproj:4-11` 默认 `net48/x86`，依赖 `MonoMod.RuntimeDetour`、`System.Text.Json`，引用 `System.Drawing`；`:20-21` 的 Linux Framework reference pack 只解决**编译引用**，不证明 Mono 运行。`ServerLocator.cs:129-181` 会优先寻找 Linux `TerrariaServer.exe`，但 `TerrariaAssemblyLoader.cs:123-147` 的 GAC 备用路径硬写 Windows。已有源码中没有证明 Linux 容器对 `MapHelper` 初始化、`System.Drawing` 和全部本地依赖的成功运行记录。用户已允许 Docker 中执行游戏逻辑：建议以一次真实同版本 ZIP 的 Mono/.NET 冒烟提取（包括地图色、中文、物品属性、bestiary 图像）作为可行性门槛；生产 Go/C++ 主程序可只负责 ZIP 输入、校验、提取、原子发布，调用受控的窄版 CLR helper 输出语义 JSON。若严格“生产容器零 CLR”，就必须接受上述语义覆盖缺口或承担 IL 解释工程。

人物预览不是现有 Hook 的输出。当前它没有搜索/输出 `Player_*` 或 `Hair_*` XNB 的逻辑；只把 `Terraria.Player` 用于初始化环境。`TConvert/Content/Images` 确实含 `Player_*.xnb` 等客户端贴图，因此上传的 Content ZIP **可以供像素提取**，无需为文件字节另加 `Terraria.exe`；然而原始贴图不提供最终站姿 40×56 裁切、层级、肤色/衣服组合的完整配方。现有 CDN `player/1.4.5.8/choices-v1/manifest.json` 明记 240 张预览来源是 `PlayerWebsite@9e09f4e…` 的人物图集；`scripts/build-player-choices.py:14-35` 从固定 640×840 atlas 裁 228 个 hair 和 12 个 clothes，**不是从 server+Content ZIP 可重建的路径**。要让新版本 ZIP 独立生成同等预览，需实现/复用对应客户端绘制选择与图层规则，并以新版本 atlas/真实截图对照；服务器侧能否完全给出这些规则尚未验证。NPC bestiary 已有 server runtime 的 frame 取样先例，不代表 player 预览已有相同能力。

## 三、现存 CDN 的目录、发布及重复

`terraviewer-images/index/resources-v1.json` 是目前资源目录入口：物品 `items/item_{id}.png`、箱子/bestiary JSON、`pixel/game_data.json`、`pixel/terraria_color_index.txci.gz`、人物选择图；政策是客户端固定不可变 Git commit、回退镜像使用同一 revision，用户存档与 WASM 不在资源清单。`index/items-1.4.5.8.json` 和 `ITEM_COVERAGE.md` 记录 1..6195 的 PNG 覆盖、旧图与新裁切的出处；资源登记中 `legacyGameVersion:null`，所以旧图不能声称都从 1.4.5.8 Content 自动导出。`sources/player-choices-v1` 存图集压缩源，CI `player-resources.yml` 重建 240 PNG 与 manifest。`terraria-data/1456/manifest.json` 另有一套旧版 Hook 生成的静态三包，目录与 `index/resources-v1.json` 是两个体系。

1456 manifest 约定 `packs/{engine,ui,search}-pack.<sha12>.json` 为 `public, max-age=31536000, immutable`；同时生成无 hash 的 alias，`manifest.json` / `latest.json` 内容相同（Hook `:480-555`）。现存三包 alias 与 hash 文件逐对字节相同，重复约 **4.13 MB**（engine 2,096,910 B、ui 868,077 B、search 1,364,908 B，合计 4,329,895 B）。但现存 manifest 对 engine 声称 `bytes:2,086,545`、SHA 前缀 `84f3...`，磁盘两份 engine 实测 **2,096,910 B、SHA 前缀 `bba5...`**；这是发布完整性故障，不能把 1456 manifest 当作可信 hash 清单。ui/search 与各自 manifest hash 前缀匹配。新发布流程应在暂存目录重算每个实际文件的长度与 SHA，再一次性切换入口；没有旧兼容要求时可去掉 alias/重复 `latest`，保留单一可变入口和 hash 文件。

当前目录汇总：约 7,052 PNG / 5.20 MiB，704 JPG / 13.29 MiB，21 JSON / 10.56 MiB，单个 TXCI gzip 0.91 MiB，旧 LUT 0.44 MiB。`items/` 约 6,198 文件但仅 2.07 MiB，主要成本更像请求数；`terraria-data/1456/packs` 六文件约 8.26 MiB，三份 alias 占一半。`pixel/game_data.json` 约 499 KB、`pixel/terraria_lut.bin` 461 KB、TXCI gzip 953 KB，和 1456 engine/UI 包有部分语义重叠；是否删某个文件须先统一 app 当前取用路径，不能仅凭名称判为字节重复。

建议的最小交付边界：单一版本清单记录输入 TerrariaServer/Content ZIP SHA、游戏版本、资源模式与每文件 SHA/长度；Go/C++ 负责流式解 ZIP、XNB/PNG 静态图、manifest 及原子发布；语义部分首先复用一个经过 Linux 容器验证的窄版 CLR 提取 helper，把输出对照现有 Hook 样张。TerraWasm 的 `.wld`/`.plr` 格式和 `.map` 编译 lookup 依旧随 WASM 发版并用真实存档验证；可注入颜色/TXCI/icon atlas 再从版本 CDN 取。人物预览与动态 tooltip 作为明确验收项，不能以“有 Content XNB”和“有语言 JSON”自动宣告完成。
