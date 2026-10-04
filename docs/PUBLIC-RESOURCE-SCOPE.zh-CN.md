# 公共资源范围与体积缩减边界

记录日期：2026-10-02。本文保留原体积分析和已确认的来源范围；消费闭包、完整精简候选与客户端离线接入已通过，最终结果和部署限制见[交付记录](DELIVERY.zh-CN.md)。

最新候选042f843…：13203逻辑纹理/12669唯一PNG、25公共族，公共对象下载18331762B，真实SDK首次下载18542798B、持久安装18327005B；完整提取RSS264871936B，真实后端重复271466496B。下列7179、6747、768adc及91d59b数字属于历史快照，最新完整记录见[真实验收](REAL-VERIFICATION.zh-CN.md)。

| 最终公共对象类别 | 字节数 |
| --- | ---: |
| gzip图片包（256个） | 11,939,248 |
| 元数据族 | 2,583,109 |
| 共享纹理目录 | 650,852 |
| RGB预计算 | 3,158,553 |
| 公共对象合计 | **18,331,762** |

唯一PNG内容11,723,318B，其中完整Tile源图4,076,488B、物品1,508,699B、Extra1,410,940B、NPC1,228,501B、Wall734,718B。图片包压缩开销净215,930B；未消费背景/启动图已由官方调用闭包排除，人物私有绘制对照未公开。可复算报告 `.runtime/reports/cdn-size-breakdown-10.json`。这包含完整离线图像与数据，不能与旧小程序只打包部分资源的大小等同。

## 已确认范围与体积基线

公共资源必须保留完整物品属性和游戏 tooltip、所有 Tile/Wall 原始图片，以及人物所有潜在源纹理与完整源帧，包括发型、盔甲、饰品、坐骑各层和状态。现有页面暂未读取某字段，不构成删掉用户明确要求内容的理由。客户端采用本地人物适配；有限绘制样本不能替代完整源帧。仅使用官方游戏来源，不恢复 Wiki、TEdit 或旧 PlayerWebsite 展示素材。

旧公共候选 manifest：`7179c9771b4eaf4ec577c86b1d0a52f7902d8b06028a2f3c8ffe987a994632a0`，游戏 1.4.5.8。以下是该快照的对象传输字节数，不是后续优化结果：

| 对象类别 | 字节数 |
| --- | ---: |
| 256 个 Stored ZIP 图片包 | 48,552,236 |
| 所有 metadata families | 20,022,496 |
| texture catalog | 766,987 |
| RGB candidates / stable / SRGB / TXCI | 3,184,262 |
| 合计 | 72,525,981 |
| 其中 player-draw-plans | 15,264,524 |
| 其中 player-draw-operations | 2,180,513 |

旧候选含 15,123 个纹理源条目、14,451 张不同 PNG、1,107 个公共对象。合计不包含 manifest/channel 文件及 HTTP 开销。plans 与 operations 合计 17,445,037 B；只移除这两族时其余对象基线约 55,080,944 B，实际结果须以重建 manifest 统计为准，不能将预算差额写成完成后的测量值。

旧应用盘点的 14 个主要游戏数据文件共 2,730,174 B，加 42 张实体 marker PNG 的 23,604 B，共 **2,753,778 B**。这个数字不含代码、WASM、通用 UI，也不含原本远端下载的物品/NPC 图片和 TXCI；它既不是整个小程序包大小，也不能直接与完整离线资源包比较。既有盘点入口见 [APP-RESOURCE-INVENTORY.zh-CN.md](APP-RESOURCE-INVENTORY.zh-CN.md) 与 [NATIVE-CDN-INVENTORY.zh-CN.md](NATIVE-CDN-INVENTORY.zh-CN.md)。

## 公共和私有边界

已确认的实现方向：提取仍执行人物计划和闭合门禁，但 plans 留在私有候选中，公共包不生成 operations。客户端本地适配不需要下载服务端采样验证记录。此项由主代理整合和验证，本文不独立声称新版全量验收通过。

完整游戏属性、tooltip、Tile/Wall 图片和人物源图属于公共需求。大体积绘制 oracle、方法扫描轨迹、IL 来源与诊断属于私有验证产物。公共可以保留紧凑的 `field + indices -> assetId` 官方绑定、原图尺寸/hash、必要帧/坐骑配置与能力状态；不能把验证样本数量当作全部动作/组合覆盖。

以下为收紧闭包之前的调查记录；最终闭包已按本文算法验证，背景和启动图仅在未被引用时排除，Gore/UI等实际依赖仍保留。

## 已实测的第一轮缩减

完整真实整包重跑 `attempt-5` 已通过，候选 `67473766ab170344e6fe8f87b7ac505d8ccaa5d7ad0cc18c667a745e6a001171`。公共绘制 plans/operations 留在私有验证中；所有 15,123 个源纹理条目和 14,451 张唯一图片仍保留，PNG 使用精确 RGBA 的无损索引编码，仅采用不增大体积的结果。

| 新候选类别 | 字节数 |
| --- | ---: |
| 图片包（含 ZIP 目录与成员开销） | 28,932,998 |
| 其中唯一 PNG 内容 | 25,314,616 |
| metadata families | 2,577,459 |
| texture catalog | 757,763 |
| RGB candidates / stable / SRGB / TXCI | 3,184,262 |
| 总下载量 | **35,452,482** |
| 安装后文件量 | **31,834,100** |

提取全任务 Go + Mono RSS 峰值 249,577,472 B，cgroup peak 299,003,904 B；耗时 216,263 ms，ready/missing=[]。全部公共对象、图片成员与 HTTP 下载已验证，不涉及生产发布或 viewer-app 修改。

新图片按唯一 SHA 计数：Background 5,209,197 B、SplashScreens 2,998,296 B、Tiles 4,075,728 B、Item 1,508,523 B、NPC 1,113,045 B、Wall 734,718 B。跨类别共用图片单列组合类别，避免重复计算。背景与启动图的约 8.2 MB 说明还有筛选空间，但只有潜在依赖闭包证明未消费后才能删除；这些数字不是最终可删除量。私有可复算报告 `.runtime/reports/cdn-size-breakdown-5.json`。

因此 **35.45 MB 是保留全部 Content 图片的第一轮精简实测，不是客户端必需资源下限**。后续需要按本篇的来源闭包过滤集合、再重建包和验证，不能拿旧分包大小或有限人物样本直接裁掉完整需求。

## 不依赖绘制样本的纹理选择算法

1. 基础集合直接保留完整 Item、Tiles、Wall 原图及已确认 bestiary/world marker/pixelart 消费引用；物品别名采用真实 texture-references，不假定每个 ID 都有独立 `Item_ID` 文件。
2. 从实际上传 server DLL 读取 IL。使用 `System.Reflection.Emit.OpCode/OperandType` 解码单/双字节 opcode、全部 operand 长度和 switch 表，通过 Module.ResolveField/Method/String 解析引用。不要扫描任意字节中的 token 或用反编译源码作为线上输入。
3. 根覆盖 PlayerDrawLayers、PlayerDrawSet、LegacyPlayerRenderer、Mount.Initialize/Draw 及其游戏绘制助手；Mount 其他方法也检查纹理引用。跟进真实 call/callvirt/ldftn 绘制依赖、渲染目标生产类及染料初始化。未知虚调用目标、动态请求和无法解析的方法必须记录为未闭合，不能当无纹理依赖。
4. **按字段保守保留整族**：引用 `TextureAssets.X` 后保留其全部实际绑定资产，不计算动态数组索引的可达集合。Extra、GlowMask、Projectile 等整族先保留；避免构造通用 IL 解释器，也不以有限状态计划做筛选全集。
5. 对选中字段解析官方初始化绑定。可用窄适配识别数组循环、Hair 文件编号 +1、Item 别名、Players 变种复制以及坐骑固定路径；Count 从上传程序集读取。绑定模式改变时报告不支持，不要求每个相同结构的新版本手工登记整包 SHA。只在方法里找到同一 ldstr 不能证明字段关联正确。
6. AssetInitializer.LoadTextures 是选中字段的绑定来源，不是把整个方法所有加载图片加入闭包的理由。对直接 Assets.Request/UseImage 的字面量、已支持的字符串组合解析实际路径；无法闭合时禁止发布精简包。
7. Go 先按已证明 assetId 过滤 catalog，再按实际 PNG hash 去重并重建 ZIP 与 manifest。图片完整像素与源帧不裁短；不能删 ZIP 成员后继续使用旧 hash。

实现职责可限制在新增 `tools/RuntimeExtractor/PlayerTextureClosure.cs`、Program.cs 的调用/NDJSON 输出，以及 Go 公共对象选择边界。私有报告记录程序集 SHA、根/已扫描方法、字段与路径绑定、选中 key、未解析原因；公共只保存消费映射与必要能力信息。

## 源码证据与容易漏掉的边界

以下路径相对于 Tdecoder 根目录，仅作为已检查版本的设计证据；发布前必须以真实上传 DLL 的结果验证：

- `code/Terraria.DataStructures/PlayerDrawLayers.cs`：除 Player/Armor/Accessories/Hair/Wings 外，还引用 Gem、ItemFlame、Frozen、IceBarrier、FlyingCarpet、JackHat、Beetle 等。
- `code/Terraria.Graphics.Renderers/LegacyPlayerRenderer.cs:158` 调用 Main.DrawProj；`PlayerDrawLayers.cs:4353,4386` 调用 Main.DrawProjDirect。不能只检查四个根类型的直接 TextureAssets 引用。
- `code/Terraria/Main.cs:28374` 的 DrawProjDirect 直接引用 Chain 系列、Chains、GemChain、FishingLine、EyeLaserSmall、LightDisc、GolfBallOutline、Npc、WireUi 等。它还调用绘制助手，必须继续闭合。
- `code/Terraria.Initializers/AssetInitializer.cs:604,723–756,877`：`Pulley -> Images/PlayerPulley`、`Beetle -> Images/BeetleOrb`；坐骑包含 `Mount_BeeWings`、`Mount_UFOGlow`、`Mount_Glow_Drill*` 等。现有 PlayerDrawing.Arrays 和“字段名等于文件名”均不足以提供完整绑定。
- `AssetInitializer.cs:601` 将 WireUi 绑定到 `Images/UI/Wires_*`。因此不能整体排除 `UI/` 目录；只排除闭包未引用的具体资源。
- `code/Terraria.Initializers/PlayerDataInitializer.cs:27` 的 LoadVariant 按 variant/piece 构造 Player 路径，另有复制与覆盖。不能只用一个男女前缀列表。
- `code/Terraria.Initializers/DyeInitializer.cs:101,147` 使用 `Images/Misc/noise`；431、435、436 的 HallowBoss/QueenSlime 使用 Extra_156、Extra_180、Extra_179。
- `code/Terraria.GameContent/PlayerRainbowWingsTextureContent.cs`、`PlayerQueenSlimeMountTextureContent.cs`、`PlayerTitaniumStormBuffTextureContent.cs` 分别引用 Extra_171、Extra_204、Projectile_908。渲染目标本身不是源 PNG，但其基础图片必须进入集合；保留基础图不代表动态效果已经实现。

## 失败规则和验收

- 未知纹理字段、绑定模式、动态 Request、未解析调用或所需 Content 缺失：精简公共包失败，保留私有诊断，不静默发布部分集合。可继续保留全量候选用于调查。
- Tile/Wall 和物品要求的图片/属性/tooltip 完整；人物各字段、全部坐骑层均有可解析映射。布局未知时保留整图并标注未知，不能按长宽猜帧或退回固定 14 帧。
- 验证计划全部引用是公共集合的子集；这只是回归检查，不能替代静态潜在依赖闭包。
- 回归特例必须包含 Pulley、BeetleOrb、Drill 六层、QueenSlime、noise、WireUi、复合躯干与双手饰品、Player 复制变种。
- 用未知字段/动态路径/不可解析 IL 夹具验证失败关闭；给正确 decoder 测试 switch、双字节 opcode 和各种 operand，避免读取错位漏依赖。
- 同输入两次集合与对象 hash 一致；重建后报告公共/私有、图片/元数据、传输/解码字节分别多少。继续全任务 300,000,000 B 硬限制验收。
- 在间接助手闭包证明没有引用前，不声称 Background/Gore/Splash 等整类已经可删；这一步是当前待完成工作。

## 发布前独立覆盖复验（2026-10-04）

`verify` 在哈希/对象复验之外，独立要求运行时必需族的公开投影齐全且非空：研究与两类 tooltip 已合并进 `items`，`player-draw-plans` 为私有对照，公开投影额外要求 `item-index`。每个公开 pack 的实际 JSON 行数须匹配声明，族内身份不得重复，`items` 与 `item-index` 身份集合须一致。未知可选族可以保留，不要求私有族独立公开；schema 和 manifest 格式不变。

公开消费关系 `texture-references`、`player-texture-bindings`、`mount-layouts` 的具体 `assetId` 与 `item-index.texture` 必须在公开纹理目录中存在；已赋值坐骑槽不得缺少资产。唯一无图引用例外是官方明确不绘制的 `Wall:0` 哨兵（Wall/0、`not-drawn-sentinel`、空资产）。`npc-frames` 包括源目录的 Bestiary 图片信息，不以这些信息强制扩展已验收的消费闭包。以上 gate 由 extract、review、snapshot、prepare-publication 共用；重新计算缺族 manifest、release 和 stable 指针哈希不能绕过它。

此次已对既有 `042f843054abf79b47aa7b85375c058887ea8a90adab4018742e819adde27ab0` 公共候选执行复验，并对其临时副本删去 `buffs`、重算 release/stable 哈希，确认 verify/snapshot/review 拒绝。未加载或运行 Terraria，也未构造或真实提取新的 `server/Linux + Content/Images` 单 ZIP；精简输入的首次真实重验仍待执行。
