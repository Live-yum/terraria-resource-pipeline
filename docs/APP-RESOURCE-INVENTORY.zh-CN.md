# viewer-app 游戏资源消费清单（2026-10-02）

本文路径和“当前”列保留迁移前快照，用于追踪来源。六项目实施结果和最终包体见[交付记录](DELIVERY.zh-CN.md)。

范围：仅按 C:/Users/depths/Desktop/Tdecoder/viewer-app 的实际运行时代码、构建脚本和同工作区现有提取产物盘点；排除 node_modules、dist、unpackage、用户存档、UI 品牌图与 WASM 可执行代码。版本主要是 Terraria 1.4.5.8。本文是新 TerrariaServer.exe + Content ZIP 流水线设计输入，不把现有生成脚本视为未来必须保留的实现。下文文件路径默认相对 Tdecoder 根目录。

## 一、按页面/共享模块追踪

| 消费者 | 实际需要的游戏资源 | 当前路径和加载方式 | 新流水线边界 |
| --- | --- | --- | --- |
| features/catalog/pages/item-page.vue 物品标记 | 世界生成宝箱可得物品 ID、中文名、图；Tile 实体标记的 ID、frame 选择、图 | shared/game/chest-item-catalog.js 从编译内置的 item-catalog-data.mjs 解码名称；worldgen-chest-items.mjs 是编译内置 ID 白名单；items/item_{id}.png 走 CDN；static/entity-markers/tile-*.png 是本地图片 | ID/名称/图/Tile frame 由统一版本目录提供；世界生成宝箱白名单属游戏逻辑提取；页面自定半径、线宽、颜色及存档草稿属应用规则 |
| features/world-editor/pages/chest-page.vue | 宝箱物品 ID/中文名、前缀 ID/中文名、物品图 | shared/game/chest-item-catalog.js、item-prefixes.mjs；getItemImageUrl → CDN 原生 image；列表和弹窗复用同一目录 | 物品和前缀可远程按需取目录；编辑命令及保存数据结构必须仍使用稳定数值 ID |
| features/player-editor/pages/components/player-panel.vue 与 pages/player/player.vue、editor.vue | 物品 ID、中文名、堆叠上限、研究需求、buff、分类/排序字段、前缀适用性/效果、装备槽、发型/衣服预览、人物全帧图 | item-catalog-data.mjs、item-rules-data.mjs、item-category-data.mjs、buff-data.mjs 编译入 JS；物品图 CDN；choices.png + virtual:player-choice-images 生成小图；walk.bin 由 walk-resource.js 从本地人物分包/H5 同源读取，walk-index.mjs 编译入 JS；渲染还读 dye-data.mjs、frame-repairs-data.mjs | 游戏元数据和原始帧可转 CDN；当前 UI 及行走 renderer 同步索引/首帧约束，远程化需设计目录加载门槛与版本一致性。PLR 编解码 WASM 和应用渲染规则不是公共图片资源 |
| features/catalog/pages/bestiary-page.vue | NPC net ID / persistentNpcId、中文/英文名、类型、图 | 首选 viewer-boot 的 /viewer/bestiary-process/searchBestiaryByPage 分页 API；网络/特定 HTTP 错误时退到本地 asset-packs/catalog/data.bin 内 index/bestiary.json；NPC 图 bestiary/npc_{npcNetId}.png CDN；有版本化 7 天 storage 缓存 | 必须保持 persistentNpcId 与 npcNetId 两套键及负 net ID；统一目录可成为 API/静态兜底的同源产物；世界解锁进度来自 .wld，不属于公共图鉴 |
| features/world-editor/pages/rule-page.vue | Tile/Wall/Paint 中文名、基色、TileObjectData style/alternate/random/frameX/Y、占格与坐标步长、frameImportant、变种/形状 | shared/data/material-catalog-data.mjs 编译入 JS；tile-material-catalog-data.mjs 编译入 JS；tile-materials.js 解码并建立索引；rule-page 用两者选择规则，尤其 frame/形状决定原生命令 | 不可仅发布 ID→名。必须保留可编辑 frame 布局和取值模式；Wiki 物种标签与 TEdit 形状不是 TerrariaServer 单独能自动推导的权威字段，应标明来源/置信度 |
| features/pixel-art/components/MaterialPairPicker.vue、pages/mappingscheme/mappingscheme.vue、pages/pixel/pixel.vue | Tile/Wall/Paint 名称、色值；稳定可写材料及最终 RGB | material-catalog-data.mjs 和 stable-tile-palette.json 编译内置；stable-pixel-mapping.js 按 MapHelper 近似语义运行时生成 17,422 组合，KD 树匹配；稳定 RGB 查表 stable-rgb.bin 由 local-resource-plugin.mjs 复制至本地分包，ensureStableRgbLookup 用 wx 文件系统/H5 同源读取 | 色表、白名单、预计算可远程；必须保留严格版本/散列绑定及可取消、有限内存下载。方案中用户映射、特殊动作和选色 UI 是应用数据 |
| features/world-write/pages/write-page.vue | 已选方案、稳定颜色查表、TXCI 精确颜色索引 | createStablePixelIndex() 获取 TXCI gzip，txci-index.js 从 CDN 多镜像下载、检查头/上限并缓存；write-page 将索引送入 TerraWasm 写入；本地 stable-rgb.bin 为近色映射优化 | TXCI 是独立重型派生产物，不能当作普通 JSON 或 PNG；线上尺寸上限压缩 32 MiB、解压 128 MiB。版本须与像素候选和 WASM 语义匹配 |
| features/world-generation/pages/config-form.vue、generate.vue | 游戏物品及材料选项，世界生成可选值、中文标签 | itemCatalog()/getMaterialCatalog() 编译内置；本页多个 config-*.json、worldgen-schema.json、game-choices.json 编译内置或由后端 schema 协作 | 物品/材料可共享目录；世界生成配置 schema 是 TerrariaServerHook 功能合同与应用文案，不等于 Content 图片提取 |
| pages/index/index.vue、features/world-viewer | 物品/NPC/Tile 标记图、世界地图色与缓存 | .wld 经 TerraWasm 解析渲染地图，物品标记用 CDN 图，Tile 实体用 static/entity-markers；marker-icon-atlas.js 把图转 RGBA 给 TerraWasm；世界缩略图来自存档运行时 | 公共标记图可远程；用户世界图片和地图结果必须按世界文件处理，不能进入静态公共资产 CDN |
| pages/saves/saves.vue、features/saves | 玩家存档预览/头像 | 走现有人物渲染和用户文件流程 | 只共享人物公共图帧；用户 PLR、历史和云存档不纳入发布目录 |

## 二、源与派生链

1. 权威游戏源：TerrariaServerHook/server/1458/Windows/TerrariaServer.exe 经 TerrariaServerHook/src/Metadata/MetadataExtractionExecutor.cs 输出 metadata.generated.json、runtime_assets.generated.json、bestiary.generated.json、bestiary.json、chest_items.generated.json、chest_items.json、game_data.json、viewer_resources.json、tile_object_data.generated.json、tile_frame_flags.generated.json 等。decodeAssets/output/viewer-1458/metadata 中现存 viewer_resources.json 为 12,846,110 字节，含 schemaVersion/gameVersion/ids/languages/items；items 行有 ID、内部名、中英名、gameplay、research、alias/deprecated。PlayerWebsite/assets/catalog/terraria-1.4.5.8/items 分片另有 tooltip；当前 viewer-app 的 item-catalog-data.mjs 只保留 id、name、pid/internalName、required/research、maxStack、buffType，故“所有物品属性/说明”尚未被应用消费，也未被此目录完整发布。
2. Content 图：decodeAssets/src/decode_assets/viewer_resources.py 可从 TConvert PNG 或 Content XNB 输出 public 目录，映射 Item_*/Tiles_*/Wall_*/NPC_*、人物纹理等；extract_bestiary_icons.py 依据 NPC 状态、纹理路径、染色/透明度派生图标。现有 decodeAssets/output/viewer-1458/Images 文件数：Item 6,134（2.02 MiB）、NPC 838（3.37 MiB）、Tiles 765（7.65 MiB）、Wall 367（3.98 MiB）、Player 545（0.87 MiB）、其他 5,366（19.39 MiB）；这是本机输出目录统计，不代表 CDN 全量清单或唯一 SHA 数。公共 CDN 使用 Live-yan/terraviewer-images 固定 commit，items 的 revision 独立于 common。
3. 紧凑物品目录：scripts/generate-player-catalog.py 从 PlayerWebsite 的导出 items 分片和旧 research metadata 生成 shared/data/item-catalog-data.mjs（109,680 字节）；scripts/generate-player-categories.py 从同一分片加反编译 ItemID/PrefixLegacy 生成 item-category-data.mjs（37,851 字节）；scripts/generate-player-item-rules.py 从 PrefixLegacy、Item.cs、PrefixID、本地化生成 item-rules-data.mjs（20,488 字节）。三者含部分重复 item 属性/ID，未来应共用一个标准化抽取输入，按用途投影发布。
4. 材料目录：shared/data/material-catalog-data.mjs 24,907 字节；scripts/generate-material-catalog.mjs 结合 TerrariaServerHook 的 TileObjectData、viewer_resources、frame flags，TEdit tile-frame 参考和 wiki/tile_ids_merged.json，生成 tile-material-catalog-data.mjs 202,745 字节。含 materials、variants、shapes、frameImportant；materials 包含 Tile ID、style、alternate、random、frame、宽高、坐标宽/行高/间隔、对应 Item ID。Wiki subId 名称只作兼容别名；树/仙人掌/棕榈的 frame 只是形状，物种由别处决定；“exact/layout/auto”影响原生写入。TEdit 许可在 viewer-app/licenses/TEdit-MS-PL.txt，不能把其内容冒充原始游戏数据。
5. 稳定颜色：scripts/generate-stable-tile-palette.py 从指定 SHA 的服务器程序集 IL、stable_single_surfaces.generated.json、viewer_resources.json 核对生成 stable-tile-palette.json 120,208 字节，保守保留 261 Tile、301 Wall、30 Paint（并非全材料）；scripts/generate-stable-rgb.mjs + stable-rgb-transform.cpp 对 17,422 候选和 2^24 RGB 预计算，stable-rgb.bin 1,604,861 字节，解压 7,017,045 字节，1,350,977 runs；详见 features/pixel-art/data/README.md。墙、负漆、深漆、隐形涂层的语义不同，不能仅依赖原始 paint RGB。另有 TerraWasm/data/terraria_color_index.txci.gz 经 CDN 给像素写入使用，和 stable-rgb.bin 不是同一个索引。
6. 人物：scripts/generate-player-preview.py 从 PlayerWebsite 1.4.5.8 player-atlas/manifest.json 裁 standing 帧及 228 发型 + 12 衣服选项，生成 choices.png（26,332 字节）、choices.mjs；scripts/player-choice-images.mjs 构建时裁成 240 张小图并生成 virtual module，微信构建还发出 choice-images 文件。scripts/generate-player-walk.py 从同一 atlas/manifest 导出 14 行走帧的 walk.bin（498,674 字节）及 walk-index.mjs（27,872 字节）。人物只从本地素材包异步读 walk.bin，当前没有 CDN。dye-data.mjs（13,405 字节）、frame-repairs-data.mjs（11,276 字节）及 dyes.mjs/renderer 的 CPU 效果与特殊修补属于派生规则；不能承诺拿到原始贴图就自动复现全部游戏角色画法。
7. Tile 实体标记：shared/game/entity-marker-catalog.js 的 key/中文名/Tile frame selector 为手工维护游戏规则；scripts/extract-entity-marker-images.mjs 从 TConvert 的 Tiles_{id}.png 按 TileObjectData/TileDrawing 逻辑裁切 42 张 static/entity-markers/tile-*.png（合计约 42 KiB），scripts/entity-marker-images.manifest.json 记录原图及结果 SHA。和完整 Tiles 纹理有重复，但小图供列表首帧/地图标记。未来可在发布期派生并远程化，仍保留 selector 语义和版本校验。
8. 图鉴：服务端紧凑 bestiary.json 为 546 行、72,353 字节；应用再压入 asset-packs/catalog/data.bin（13,620 字节，解压 72,390 字节），与 CDN index/bestiary.json 和后端分页 API 三路重复。NPC 图还可能涉及负 net ID 和同 texture 的不同着色，路径简单替换不足以正确生成。

## 三、现有分发与重构约束

- infrastructure/assets/catalog.mjs 固定公共资源仓库、common/item revision、路径白名单和三个 jsDelivr 镜像；image-source.js 的物品/NPC 图直接给原生 image，resource-image.vue 在错误时按镜像重试。native-image-source.mjs 把旧 URL 规范化并只记成功镜像，不为每个图额外 downloadFile；H5/微信图片由平台缓存。URL 和 manifest 使用版本地址，缺图有本地 fallback。
- 小目录走 infrastructure/assets/local.mjs + asset-packs/catalog/data.bin：微信 require 本地分包 ready.js 后读文件，H5 同源 fetch；构建插件 scripts/local-resource-plugin.mjs 验证 SHA、复制 catalog 与 stable-rgb.bin；scripts/resource-delivery-policy.mjs 清理误入构建的 CDN 大图/TXCI。若目标是“所有游戏数据都 CDN 按需加载”，这两个本地包和所有编译内置的大目录是当前明确改造点。
- 异步远程化不能直接把 ESM 数据 import 改成 URL：player-panel 顶层同步 itemCatalog()，material-catalog、tile-materials、stable-pixel-mapping、世界生成表单也同步求值。需在页面入口加载后再初始化选择器，并让版本/失败态可见。可保留极小的枚举、格式校验、用户草稿和 WASM ABI 为编译依赖。
- 资源合同至少要有游戏版本、源服务器 SHA、Content ZIP SHA、提取器版本、语言、稳定 ID/别名、每个文件 SHA/尺寸、关联的原图与裁帧规则、异常/缺失记录。目录的 item.id、tile.id、wall.id、npc.netId/persistentNpcId、player texture key 和 frame 坐标是跨页面连接键；不要以中文名或图片文件名作主键。
- 不属于游戏公共资源的静态项：static/imgs/default.png/chest.png/portrait-guide.png、logo、static/pixel-art UI 图、世界生成页面文案、规则预设、用户标记颜色/半径、用户世界/人物存档与地图快照；WASM 是代码而非可热替换资源。若统一 CDN 要连这些也迁，需另立产品/部署要求。
- 无外部依赖、低内存提取器可按输入文件流式处理并逐件发布清单；不要为“全量物品属性/说明”误用当前 109 KB 的编辑目录，它并不含 tooltip。只有后续页面实际消费的字段才需加载到客户端；全量规范化档案可在 CDN 以分片/按 ID 查询的形式存在。

## 四、已确认的未覆盖与验收风险

1. 当前 CDN item manifest 最大 ID 为 6195，但本机 PNG 仅 6,134 个 Item_ 文件；需用 manifest 对 alias、负 ID、废弃 ID、缺图逐项校验，不可假设 ID 1..6195 全有图。item image revision 与 common revision 不同，跨版本混用会导致数据/图错位。
2. 当前应用没有展示物品完整属性和 tooltip 的页面。新流水线必须从 runtime Item.SetDefaults 加本地化 tooltip 生成，却不能用“当前页面无引用”判断无需抽取。服务端 viewer_resources.items 有 gameplay/research，但现有紧凑客户端表是有损投影。
3. GameData 的 Tile 映射可能同一 ID 多个 map option，末项不是 Tile 名；material-catalog 生成器明确避开此问题。frameImportant 的当前期望是 412 个 ID；特殊 Tile 物种和 TEdit/Wiki shape 标签要按出处分别标注，服务端静态反射无法覆盖全部。
4. 稳定白名单有意识排除 frame/高度/特殊 MapHelper 分支，不等于全 Tile/Wall/RGB。预计算要与 MapHelper 单精度/负漆规则和候选排序一致，并受压缩/解压内存上限约束；17,422 候选有大量重复 RGB，不能按 RGB 去重后丢失材料选择。
5. 人物当前仅预生成 standing 选择图和 14 walking 帧包，原 atlas 的全部造型、装备图层、动画状态、染料、发光/修补规则未完整成为可查询 CDN schema。“人物所有造型帧”应以 Content 原纹理和 manifest 为全量基础，当前 walk.bin 只能作为兼容派生视图。
6. 图鉴 API 返回 npcType 等字段，静态 compact JSON 用 t/name_zh 等短键；消费层能兼容部分别名，但新 schema 需统一两条路径，保存世界进度仍按 persistentNpcId。NPC 图的类型/net ID/特殊贴图变体须逐项核验。
7. 不建议新提取器直接复刻现有 Python 输出细节：现有脚本有人工表、Wiki、TEdit、PlayerWebsite atlas、反编译源码和应用规则。先界定“游戏可机械提取的事实”“基于游戏源计算的派生”“外部参考/人工标签”“应用业务规则”，再为后两者决定保留、迁移或拒绝。

## 五、优先核对入口

- 服务器元数据：TerrariaServerHook/src/Metadata/MetadataExtractionExecutor.cs；TerrariaServerHook/tests/Test-MetadataExtraction.ps1。
- Content 与图：decodeAssets/src/decode_assets/viewer_resources.py、extract_bestiary_icons.py；viewer-app/scripts/extract-entity-marker-images.mjs。
- 客户端公共目录：viewer-app/shared/game/item-catalog.js、material-catalog.js、chest-item-catalog.js；viewer-app/infrastructure/assets/catalog.mjs、local.mjs、request.mjs；viewer-app/infrastructure/media/image-source.js、native-image-source.mjs。
- 材料/像素：viewer-app/scripts/generate-material-catalog.mjs、generate-stable-tile-palette.py、generate-stable-rgb.mjs；viewer-app/features/world-editor/pages/services/tile-materials.js；viewer-app/features/pixel-art/services/stable-pixel-mapping.js、stable-rgb-lookup.mjs、txci-index.js。
- 人物：viewer-app/scripts/generate-player-catalog.py、generate-player-categories.py、generate-player-item-rules.py、generate-player-preview.py、generate-player-walk.py、player-choice-images.mjs；viewer-app/features/player-editor/pages/services/render/walk.mjs。
