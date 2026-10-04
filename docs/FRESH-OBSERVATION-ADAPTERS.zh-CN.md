# 新鲜来源观察与应用策略适配

状态：本地开发，独立审查中。这里实现的是来源观察到既有消费格式的适配；并未完成真实 Windows 初始化采集，也不授予来源可信、全量完成或发布权限。

## Item 观察适配

`runtime_item_adapter.adapt_observed_items(observation)` 接收明确的 `itemCount`、独立选择的正整数 `itemDomain`、47 个实际 Item 字段及名称/持久 ID/研究值、prefix 名称/效果/pools、7 个 PrefixLegacy bool 数组、4 个 ItemID bool 数组、排序字段域和数组。

- 每个数组必须恰有 Count 个值，含 ID 0，不以缺失推断 false。
- `damageClass` 是应用排序策略：四类近战 membership 优先，然后 GunsBows、Magic、Summon，否则 4。它不是 CLR DamageClass 字段。
- `food/fish/crate` 分别来自 IsFood/IsBasicFish/IsFishingCrate，输出 0/1。
- `noAccessoryPrefix` 来自所选域中 CanGetPrefixes 为 false 的 ID，不用试加前缀猜测。
- 每个排序表必须包含 `default:-1`、有序 `overrides:[[id,value],...]` 和最终观察 `values`。重复覆盖保留最后一次写入；显式 -1 和 0 都保留 membership。拒绝最终数组与覆盖来源不一致的输入。
- 所有字段再交给既有 item assembler 完整校验；不填充缺失事实。

`sorting_priority_literals.extract_sorting_priority_literals(path)` 只接受已固定的客户端/服务器 PE 哈希，按精确 IL 语法读取 20 个 SortingPriority 字段的默认值与覆盖参数。它只证明条件性的调用点字面量来源，不证明整个初始化器、后续无修改或最终运行时状态。与运行时数组一致性检查是独立步骤。原始值仅留在操作者私有目录。

## 新 PNG 的人物应用策略

`player_texture_policy` 不读取旧 atlas、旧 walk.bin 或旧资源目录输出。输入是新解码 PNG 的根相对路径及字节；上游解码器仍须保留原始 XNB/源树绑定。

命名算法出处：GitHub repository ID 1358343481，`Live-yum/PlayerWebsite@9e09f4e3aef37befc267aa51c190bea472726c8b` 的 `src/terraria-player-renderer-core.mjs`，对应 `normalizeAssetKey`/`isPlayerTextureAsset`。该仓库之前名为 Live-yan/PlayerWebsite，ID 未变。

- `Armor/Armor_N.png` 映射为 `armor_composite_N`。
- `Accessories/Acc_HandsOn_N.png`/HandsOff 使用 `accessories_` 前缀。
- `.xnb.png` 按旧导入策略规范化；路径穿越、非法扩展、重复路径、大小写/规范化后的 key 冲突均拒绝。
- 非人物 PNG 按明确的应用选择正则排除并记录；这不是全游戏纹理覆盖证明。

人物策略出处为 `Live-yan/viewer-app@638e770db844174ff990e42e40a7e96290ff6968`：

1. `scripts/generate-player-walk.py`：14 帧、composite cells、装备/性别、头发、翅膀、气球、crop/dedup 策略。
2. `scripts/generate-player-preview.py`：12 个服装风格的 fallback、站立 composite、选择顺序、固定预览颜色。
3. `scripts/generate-player-polish-assets.py`：短竖图的逐帧平移/像素修复。

直接 crop 的 recipe 明确使用 `composition:copy`，保留 alpha=0 下的 RGB；默认 `source-over` 合成行为不变。该可选字段只扩展本地 recipe 输入，不改变三个消费角色的 wire schema。

每项策略保留固定源码 SHA-256。它们是应用呈现规则，并未被重新标记成游戏事实或完整游戏渲染保证。

### API

- `map_player_texture_paths(paths)`：返回无歧义 canonical map 和被应用排除的 PNG 路径。
- `build_player_walk_recipes(textures)`：显式 14 帧 layer recipes、实际使用源图、收据。完全透明的省略必须经过全部新帧渲染，并记录输入图哈希。
- `build_player_choice_recipes(textures)`：choice recipes、新生成的中间 PNG、收据。先合成一个 piece 再染色，避免半透明像素舍入差异；中间 PNG 的每层源矩形/哈希均保留。缺少站立部件明确列出。
- `build_player_frame_repairs(textures, walk_keys)`：新像素比较得到 translations 或 raw-RGBA repair recipes；保留透明/不在 walk 域的省略原因。
- `assemble_player_from_fresh_textures(...)`：以上策略合并到既有三个人物角色 assembler。限定 1.4.5.8，仍强制调用方提供 `facts`、三个 item objects 和非空事实来源哈希，不猜测 buffs/dyes/hairRules/wingRules/selection/versionLabels。

所有收据保持 `complete=false`、`publishable=false`、`sourceDomainVerified=false`。应用 fallback 可生成部分呈现，但不能因此确认来源域完整；缺失清单不能藏成真机测试。

## 验证与尚缺工程

原始合成 PNG 测试对照三个固定源码生成器，逐像素/geometry/压缩 payload 比较；含半透明合成、female composite、双手附件、头发、wing40、balloon、短图透明末帧、平移与修复。没有使用任何旧游戏表或 atlas 作为期望数据。

仍需完成并验证：固定 Windows 初始化观察器、实际素材全集/独立选择域、剩余人物事实采集、跨组版本/来源绑定和所选交付范围的授权策略。静态代码/原始 fixture 测试不能代替真实初始化观察。后端旧 schema-2 的 110 项来源要求没有改变。
