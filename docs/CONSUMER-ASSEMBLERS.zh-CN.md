# 有限消费组装与未完成的源语义

## 当前结论

现有六个有限组装器覆盖全部 13 个角色的**派生实现**，另产出一份明确分离的 worldgen 服务配置草稿：

| 输入 | 派生输出 | 已实现的转换 |
|---|---|---|
| 独立正 ID 域、完整最终物品事实、前缀/分类注册表 | `items.catalog`、`items.rules`、`items.categories` | 七列 join、完整规则列、分类位图、排序 key 去重、弹药策略 |
| 材料基础 JSON、版本化白名单策略 | `pixel.catalog`、`pixel.rgb` | float32 调色、候选指纹、精确完整 256³ 最近色索引 |
| 原始 PNG、源文件字节、显式选择器/裁切策略、材料基础 JSON | `markers.catalog`、`markers.images` | 新 PNG 裁切、去除帧间隔、gapless 拼包、跨组原始字节 hash 绑定 |
| 完整材料命名记录、布局/shape/variant 政策 | `materials.base`、`materials.rules` | 连续 tile 域、规则 tuple、frameImportant 与消费 fallback 约束 |
| 原始 PNG、显式 14 帧图层配方、人物事实与三对象 item 基础层 | `player.presentation`、`player.walk`、`player.atlas` | 帧合成/共同裁切、去重、palette/zlib、图集、修复和跨组绑定 |
| 服务 schema/defaults、独立权威目录、应用选项政策、基础对象 | `worldgen.choices` | FieldSpec/默认值验证、目录投影与 foundation 绑定；`worldgen.options` 仅为服务草稿，不是第 14 个 release role |

这不是“13 角色已经从上传源成功提取”。本次真实源最终消费验收仍为 **0 个完整组**。现有 `build_consumer_release.py` 仍是传输打包器，不是 source→consumer assembler。新组装器不接受迁移后的 role 对象来冒充源输入，也不复制旧私有基线表。

当前仍不能把整条流水线交付成“只差用户真机测试”。缺少的 producer/策略代码和证明必须继续由开发者完成，不应要求用户手工填写未知游戏字段。

## 信任与发布边界

组装函数验证结构、域关联、输入身份和确定性转换。匹配 hash 只证明字节一致，不能证明调用方所写的游戏事实真实。`sourceHashes` 也只是保留 lineage，未经过本模块认证。

- items/materials/markers/player/worldgen 回执固定 `status=DERIVED_ONLY`、`sourceSemanticsVerified=false`、`publicationApproved=false`。
- pixel 回执固定 `sourceCompletenessEstablished=false`，含精确基础/策略/输出 hash，不签发 producer 证明。
- CLI 最后写 `assembly.json`；固定 `sourceProductionComplete=false`、`consumerReleaseReady=false`、`publicationApproved=false`，不是 release manifest。
- 回执不可代替后端 schema-2 producer/domain/policy 证书，不自动开放真实上传、发布、频道推进或回滚。
- 本次没有执行 Terraria EXE、CLR 或世界生成入口，也没有实现可执行的用户本地 runtime collector。旧 runtime 文档仍只是设计。

## items 输入与算法

`assemble_item_resources(inputs)` 的输入必须恰好包含：`gameVersion`、`itemDomain`、`records`、`prefixes`、`pools`、`groups`、`noAccessoryPrefix`、`priorities`、`sourceHashes`。

- `itemDomain` 是独立提供的、排序且不重复的正 uint16 ID 域；不能从已成功的 records 反推。
- 每个 record 必须有 `id/name/persistentId/research/gameplay`。records 与域必须精确相等，不允许丢 ID、重复 ID、重复 persistent ID。
- gameplay 的必需字段由 `ALL_FIELDS` 固定；每一项都要存在。布尔字段不接受 0/1 替代；其余字段除 knockBack 外要求 int32。目录的 maxStack/hairDye/buffType、分类 ammo 和标志有额外消费域检查。slot/createTile/createWall/mountType 不接受低于 -1 的哨兵。这里不是对游戏全部状态的可达性证明。
- 前缀零条目、全部 8 个前缀池与 7 个组必须显式存在。空列表是显式输入，不能证明真实域就是空。
- `priorities` 只接受 `SortingPriority*` 表和精确规范十进制 ID；引用必须在独立域内。
- 保留每个 item 的规则行，不用无法证实的 eligibility 启发式静默丢弃。

分类算法移植自应用 `scripts/compile-player-category-index.mjs`，SHA-256 `6b2cea2715f1237f1e99bf3ad917f6eed37798adf59256c78f857d6f694e5d7d`。分类名顺序、特殊物品 ID 的应用规则和排序顺序是**应用政策**，不伪称从游戏 DLL 动态发现。测试将原创 600 行输入同时送到这份实际 JS 算法与 Python 实现，比对完整 keys/items，并调用实际 `validateItemResourceSnapshot`。

## markers 输入与图像安全

`assemble_marker_resources` 接受原始 `material_base` 字节和独立 `material_base_sha256`、`policy`、纹理名→PNG 字节以及源文件名→源字节。

policy 固定 `schemaVersion=1`、`algorithm=marker-rgba-crop-spacing2-v1`、`gameVersion/sourceFiles/rows`。每行固定 `key/name/selector/crop`；crop 固定 `textureSha256/frameX/frameY/columns/rowHeights`。应用选择器和裁切坐标仍需有真实来源的推导与审核，本模块不会猜测它们。

逐项验证实际源/纹理 hash，再解码；每张纹理只解码一次。PNG 检查体积、像素预算、chunk/CRC、IHDR/PLTE/IDAT/IEND 顺序、未知关键块、动画和尾随字节。输出是 RGBA PNG，宽度为 columns×16，按 2 像素源帧间隔裁切，大小不超过 128×128/64 KiB；图包不超过 4 MiB。材料 tile 域必须从零连续，不根据最大值补洞。名称按实际 JS 的 128 个 UTF-16 code units 限制，拒绝无效 surrogate。

## 私有草稿 CLI

安装依赖后运行：

```sh
python -m pip install -e '.[test]'
PYTHONPATH=src python scripts/assemble_consumer_draft.py --group items --input-root /PRIVATE/item-input --output /PRIVATE/item-draft
PYTHONPATH=src python scripts/assemble_consumer_draft.py --group markers --input-root /PRIVATE/marker-input --base-sha256 EXACT_BASE_SHA256 --output /PRIVATE/marker-draft
PYTHONPATH=src python scripts/assemble_consumer_draft.py --group pixel --input-root /PRIVATE/pixel-input --base-sha256 EXACT_BASE_SHA256 --output /PRIVATE/pixel-draft
PYTHONPATH=src python scripts/assemble_consumer_draft.py --group materials --input-root /PRIVATE/material-input --output /PRIVATE/material-draft
PYTHONPATH=src python scripts/assemble_consumer_draft.py --group player --input-root /PRIVATE/player-input --output /PRIVATE/player-draft
PYTHONPATH=src python scripts/assemble_consumer_draft.py --group worldgen --input-root /PRIVATE/worldgen-input --output /PRIVATE/worldgen-draft
```

输入布局：

- items：`item-facts.json`，符合上述完整模型。
- markers：`materials.base.json`、`policy.json`、`textures/Tiles_ID.png`、`sources/` 下 policy 声明的源文件。
- pixel：`materials.base.json`、`policy.json`，策略格式见 [PIXEL-ASSEMBLER.zh-CN.md](PIXEL-ASSEMBLER.zh-CN.md)。
- materials：`material-records.json`、`policy.json`，命名结构见 [materials-assembly.md](materials-assembly.md)。
- player：`policy.json`、item 三角色 `.json`、`textures/` 中被配方精确引用的 PNG，见 [PLAYER-ASSEMBLER.zh-CN.md](PLAYER-ASSEMBLER.zh-CN.md)。
- worldgen：`worldgen-input.json`、`foundations.json`（items/materials 的 gameVersion/releaseId/baseSha256）、`items.catalog.json`、`materials.base.json`，见 [worldgen-assembly.md](worldgen-assembly.md)。服务草稿写入 `serviceArtifacts`，不伪装成消费发布角色。

输出目录必须是新的、不能经过 symlink、必须在 pipeline checkout 外。所有成功计算完成后才创建输出；`assembly.json` 是最终完成标记。仅输出统计到 stdout。输入/输出保持私有，不提交原始游戏字节，也不把它们上传公共 Actions artifact。

这些命令用于开发者的已核实输入转换和对照，不是让用户凭空构造事实的真机验收步骤。

## 精确剩余开发缺口

1. **items 三角色**：完整 Item.SetDefaults 最终字段 producer；独立选择域、有效本地化、persistent/research join；前缀效果/池/组/排除和 SortingPriority 注册表的最终源证据。当前静态前缀写入/局部 baseline 不能顶替完整初始化值。
2. **materials.base/materials.rules**：派生组装器已实现；仍缺 tile/wall/paint 的最终名称/地图颜色、frameImportant、TileObjectData 布局完整 producer，以及独立审核的 variant/shape 应用政策。paint 输入 RGB 不等于最终地图颜色。
3. **pixel 两角色**：本次派生算法完成；仍依赖上一项真实材料输入和经过审核的应用白名单。不能凭真实 PNG 猜地图颜色。
4. **markers 两角色**：本次 PNG/拼包算法完成；还缺当前版本选择器与裁切政策的来源推导、独立完整材料域。旧迁移 catalog 不是本次提取证据。
5. **player 三角色**：walk/图集/修复及完整 presentation 组装已实现；仍缺 equipment/frame/层顺序与局部修复政策、buff/dye/hair/wing/selection/versionLabels 的 producer/审核闭环。显式配方不是自动获得真实映射的功能。
6. **worldgen.choices**：有限 schema/选项组装已实现；仍缺当前服务端配置 schema/revision/目录/FieldSpec/labels/允许 pass 的权威来源接入与固定。不能通过执行真实世界生成弥补未知 UI schema。
7. **发布前**：上述全域 source 证明、后端独立 schema-2 验证、跨组绑定与原子 release-set 都通过后，才进入真实服务鉴权/CDN/读回与用户设备渲染验收。

## 验证命令

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
# 可选真实应用源码对照；仍只使用原创合成输入，不需要游戏程序：
PYTHONPATH=src TERRARIA_CONSUMER_ROOT=/PATH/TO/viewer-app PIXEL_ASSEMBLER_APP_ROOT=/PATH/TO/viewer-app python -m unittest discover -s tests -p 'test_*assembler.py' -v
```

标准 CI 跑所有纯 Python 拒绝/确定性/域/像素测试；实际 app differential 是显式启用的额外验证，未提供环境变量时显示 skipped，不能把 skip 写成已运行。
