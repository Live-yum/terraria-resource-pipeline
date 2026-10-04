# 人物原始纹理、全部帧与合成展示资源合同

日期：2026-10-02。本文保留官方 Linux server、Content 与原应用的调查证据和实现合同。下文“尚未完成”属于调查时状态；当前官方源图组合与明确近似的实现结果见[交付记录](DELIVERY.zh-CN.md)。禁止引入旧 PlayerWebsite atlas、Wiki、TEdit 或人工补图。

实施状态：最终042f8430候选已完成官方纹理绑定、消费闭包、全源像素以及真实无GPU DrawData采样，样本只作为后端私有对照。66种坐骑以真实Content尺寸重新运行官方初始化，87个非空贴图槽全部校验，4种无坐骑贴图的矿车合法保留。客户端原图组合、实际全部装备槽/20动作帧边界及普通DrawData对照35/35通过；特殊头盔、腿甲、翼与动态效果明确标注近似。当前验收与能力限制以[真实验收](REAL-VERIFICATION.zh-CN.md)和release manifest为准。

## 结论

**当前 1.4.5.8 官方 Linux server + Content 不缺人物绘制算法所在的程序集。** 服务器真实包含 `PlayerDrawLayers`、`PlayerDrawSet`、`LegacyPlayerRenderer`、`Player.PlayerFrame`、`Mount.Initialize/Draw`。因此不能用“需要官方客户端 DLL”解释当前 `composition=false`。原始纹理全部像素可由 Go 解码；坐骑帧元数据已证明可直接运行导出。尚未完成的是将纹理尺寸/资源身份绑定到运行时，并在 GPU 提交前导出有序绘制命令。

必须分别报告三种能力：`sourceTexturesComplete`（原图全量）、`frameLayoutsComplete`（游戏帧布局与动画区间）、`compositionComplete`（给定明确状态的完整组合绘制）。76 行方法/字段清单不是第二、第三种能力。完整帧也不能被站姿和14帧行走包替代。原始帧是有限资源；任意角色颜色、装备组合、连续旋转/速度、染料时间与世界状态不是一个可穷举的有限预渲染图集。

## 已取得的真实证据

探针直接读 `TerrariaServerHook/server/1458/Linux/TerrariaServer.exe`，SHA-256：`4b87890ac53d40f61db5f928693a379acf4ccbd8ed3b47eb32fb096f145df034`。使用现成 `terraria-runtime-extractor:local`/Mono，输入只读、无网络、容器 `--memory 300000000 --memory-swap 300000000`；未写产品源码。另用本机 ilspycmd 只读反编译该真实 server 的 `LegacyPlayerRenderer`、`Mount`，不是以客户端反编译目录冒充服务器证据。

| 真实观察 | 含义 |
| --- | --- |
| `PlayerDrawSet.BoringSetup` IL 长24413；`LegacyPlayerRenderer.DrawPlayer_UseNormalLayers` 长494；`DrawPlayer_09_Wings` 长11053；`DrawPlayer_21_Head` 长10108 | 类型和重要绘制方法存在，具有实质方法体，非仅同名空壳 |
| `DrawPlayer` 顺序是 BoringSetup → UseNormalLayers → TransformDrawData → 可选 ScaleDrawData → RenderAllLayers | 在 RenderAllLayers 之前有明确的 CPU 绘制计划截面。不要调用完整 DrawPlayer 再尝试截屏 |
| `PlayerDrawSet` 持有 DrawDataCache、body/head/leg颜色、各染料槽、hairFrontFrame/hairBackFrame、composite前后手臂/肩/躯干矩形、遮挡/坐姿/坐骑标志 | 单纯纹理尺寸和固定层级表不足以重现全部组合 |
| `DrawData` 包含 texture、position、destinationRectangle、sourceRect、color、rotation、origin、scale、effect、shader、ignorePlayerRotation、useDestinationRectangle | 可直接映射为无 GPU 的便携 draw-operation 合同，而不需要输出每种组合的 PNG |
| `MountData` 有 totalFrames，standing/running/flying/inAir/idle/swim/dashing 的 start/count/delay，playerYOffsets、bodyFrame、playerHeadOffset、前后各四种纹理槽 | 坐骑动作帧与人物随坐骑偏移由游戏数据提供，应真实导出 |
| 先设置 Program.SavePath，再以 Main.netMode=2、Main.dedServ=true 调 Mount.Initialize，成功得到66个坐骑。抽查0/1/2/50的totalFrames为12/7/16/8；0 running=[6,6]、2 running=[11,5] | 初始化能在无纹理/GPU环境提取数值配置。必须保存/恢复全局状态，避免影响已有其他域 |
| 上述模式 textureWidth/textureHeight 均为0；若只设netMode=2但dedServ=false，真实抛出 Drill texture origin 0,0 检查错误 | 0代表服务器模式未绑定纹理，不能写入可用布局或宣称缺图；需要 Content 尺寸与纹理引用绑定 |
| `Mount.Draw` 会向传入 List<DrawData> 写层；type50还访问 QueenSlimeMount RenderTarget，并在ready时替换纹理 | 普通sprite帧与动态渲染目标需分能力，不能以基础贴图表示所有最终效果 |
| FNA Texture2D 仅 Width/Height backing fields；创建无构造CPU占位对象并设置它们后真实读出40×1120。Asset<T>有Name/State/Value及SubmitLoadedContent | 无像素、无GraphicsDevice的纹理尺寸占位方案具备基础条件；这不是完整BoringSetup执行成功的证明 |

失败探针已纠正：缺 Program.SavePath 会触发 Main 静态初始化错误；缺dedServ设置会触发上面的Drill检查。这些属于初始化上下文缺失，不属于“服务器缺客户端算法”。

## 现有消费者为何不能直接换包

`viewer-app/features/player-editor/pages/services/render/walk.mjs` 固定 `WALK_FRAMES=14`、128×112画布、70ms间隔，从walk.bin取预裁/去重帧；索引包含offset/length/crop/frame remap，且调用人工修补和CPU染料代码。`terraria-player-renderer-core.mjs` 顶部明确改编自PlayerWebsite，含固定variant fallback、盔甲遮挡ID集合与饰品层组。不能把这些未经服务器核实的集合复制到新提取器，作为官方自动生成依据。

`TerraWasm/exports.plr.txt` 的现有player接口处理PLR读取/编辑/编码，没有人物绘制计划或帧渲染ABI。后续统一资源解析需要新增最小资源查询/绘制计划消费入口；不能声称下载新JSON即可让现有WASM自动渲染。宿主仍负责网络、缓存、canvas/图像呈现。

## 最小资源合同

沿用主 manifest 的release、对象SHA/长度和按需分片，不新增独立人物总ZIP。公共数据主键使用实际游戏字段及asset路径，禁止用译名作键。

| 域（NDJSON/发布分片） | 必需字段与规则 |
| --- | --- |
| `player-textures` | `id=原始Content相对路径去扩展名`、image对象引用、sourceSha256、width/height、原始RGBA/alpha语义、binding（TextureAssets字段+索引数组）。覆盖Player各skin/body部件、Hair/HairAlt、Armor/FemaleBody/Armor/Armor_*、旧/复合HandsOn/HandsOff、全部饰品/翅膀、坐骑各层及被游戏引用的Extra/GlowMask/Projectile纹理；仅靠文件前缀会漏引用 |
| `player-equipment` | Item ID→实际head/body/leg/handOn/handOff/back/front/shoe/waist/wing/shield/neck/face/balloon/beard等slot、mountType/相应召唤属性；导出ArmorIDs各嵌套Sets中的遮发/隐藏皮肤/复合帧/前后图层/配对槽等相关数组，保留实际类型、Count与索引，不写旧固定ID集 |
| `player-frame-layouts` | `id`、纹理引用、`layoutKind=explicitRects|grid`、有效rect全集、padding/步长、游戏来源方法/字段、可用动作引用。普通body/leg与复合torso/肩/手臂不是同一布局。只有游戏明确给出步长/网格才用grid；无法解释的纹理保留整图并报layout缺失，不按长宽猜帧数 |
| `player-animations` | `id`、动作/状态上下文、body/leg/wing/附属frame选择，序列、游戏tick延迟/条件、循环与方向/gravDir。站立/移动只是动作子集；保留游戏公开的跳跃/飞行/游泳/坐姿/骑乘等帧域；不得将旧14步行表当全集 |
| `mount-layouts` | id、totalFrames、完整七类动画start/count/delay/idleLoop、playerYOffsets、playerXOffset/xOffset/yOffset/bodyFrame/playerHeadOffset/heightBoost、8种texture binding、每层尺寸/rect、特殊frame选取说明。textureWidth=0不可作为有效发布值；非等分特殊布局使用显式rect |
| `player-draw-plans` | `id`、contextHash、完整输入状态或固定profile、operations按游戏顺序排列；每op包含assetId、sourceRect、position、origin、scale、rotation、flip/effect、tint RGBA、shader引用、可选destinationRect、ignorePlayerRotation。位置统一相对于人物锚点；记录裁剪与坐标舍入，防止每帧边界抖动 |
| `player-capabilities` | textures/layouts/animation/composition逐项status、未解决asset/member/state原因、输入程序集SHA和adapter版本、实际覆盖slot/动作/状态集合。图像已提取不能覆盖其他false状态 |

形状举例：`Mount.0.running`来自真实`runningFrameStart=6,runningFrameCount=6`，是逻辑帧6..11；每层sourceRect需结合该层真实高度与Mount.Draw规则计算。`PlayerDrawSet.CreateCompositeFrameRect`在当前源中是40×56单元，但应核对服务器方法及texture边界，而不是把所有人物贴图均切40×56。

按需策略：轻量目录只加载ID/名称/装备槽/资源索引；选择装备、发型、坐骑和动作后只拉所用frame-layout与原图。共享纹理与帧只存一次，裁帧是矩形引用而非重复PNG。预览缓存按解码后的RGBA预算驱逐，原图可按hash缓存。全组合预烘焙的笛卡尔积既无必要，也不满足300MB限制。

## 可直接实施的顺序

1. **立即完成确定部分**：Go对全部Content纹理生成source-key/尺寸/hash索引；C#继续导出装备slot、ArmorIDs相关Sets、Player默认frame/偏移，新增Mount数值配置。将当前player-layouts的方法清单保留为诊断，不能充当最终frame合同。Main.netMode/dedServ需try/finally恢复。
2. **建立官方asset绑定**：依据实际server的AssetInitializer/TextureAssets初始化路径记录“字段及索引→官方Content key”。同一贴图可能多槽引用；Hair代码索引与文件名+1必须显式表示；ArmorBodyComposite与旧ArmorBody/FemaleBody/ArmorArm分别保留。可以为已核实初始化模式做窄版适配，结构变化即报告不支持，不能写一个猜任意IL语义的解释器。绑定必须逐个检查Content存在和尺寸。
3. **最小无GPU可行性测试**：为少量实际asset创建仅尺寸/身份的Texture2D占位，并包装为loaded的Asset<Texture2D>；固定颜色/光照、确定随机种子、时间、方向、gravDir、player位置/动作/装备，初始化BoringSetup所需世界/玩家字段。通过反射调用BoringSetup、私有UseNormalLayers、TransformDrawData，收集DrawDataCache；绝不进入RenderAllLayers/SpriteBatch/GraphicsDevice。调用ref结构体时保留Invoke后回写值。先证明裸身站立，再头发+头盔、复合盔甲、饰品、普通坐骑；未通过前不扩成全域承诺。
4. **处理实际依赖**：BoringSetup会调用Main.instance.LoadArmor*/LoadHair等；已loaded资产使这类方法跳过Assets.Request，但仍需有效Main.instance和全部间接状态。服务器Source里该路径可见，实际运行仍需验证。纹理占位禁调用SetData/GetData/Dispose设备资源；管理其生命周期/终结器，任何访问native texture指针的分支都明确阻断。不能仅创建一个width/height对象就宣告能跑所有绘制层。
5. **实现无GPU消费**：便携draw-op可由后续TerraWasm解释并由宿主提供纹理/显示。普通sprite裁切、变换、颜色、alpha可实现CPU合成；shader与render-target另列能力。依据官方输入的算法移植可以做，人工修补/替代图不可以。现有renderer仅作用户行为参考，不能作为官方像素真值。
6. **扩大全部原始帧覆盖**：每纹理所有合法源rect、每slot、每mount动作区间都可按需列出/展示；游戏上下文样本用于验证组合，而不是把抽样数量冒充任意组合完整性。精确组合的覆盖合同需要列出状态域（standing/moving/mounted、所有可选装备槽、方向等）和尚不支持分支。

为步骤2/3传入尺寸索引时，可增可选 `--texture-index <Go产出的只读索引>`；它只是asset key/尺寸/hash，不含像素、不含可执行代码。不要让C#重新解码整套XNB。Go读取header/索引与Mono阶段顺序执行，随后释放Mono再逐图解码；全任务 cgroup硬限制300,000,000B，记录阶段memory.current/peak、最大图预算与输出量。

## 限制与最小缺失输入判断

目前没有证据表明需要另一个客户端程序集，因此**现阶段不新增上传输入**。官方server已携带FNA和嵌入ReLogic依赖；它们加Content即可提供已确认的方法与原始贴图。反射调用报空引用/GraphicsDevice错误，首先是初始化或执行边界问题，不能自动归因为缺DLL。

确实存在需要额外实现的算法：发型/头盔遮挡、复合手臂旋转、长衣/坐姿裁片、坐骑替换人物层、光照、shader染料、动态RenderTarget。尤其Queen Slime坐骑已在真实server反编译中看到RenderTarget访问；一张原始sprite不能等价于该动态结果。可以完整发布其原始像素和frame布局，同时明确`compositionComplete=false`，不能默默补相似效果。

若未来特定server版本被裁掉所需方法，报告必须精确列出缺失类型/成员与输入程序集SHA，再要求**同游戏版本、同平台可装载的官方客户端主程序集及其未随server提供的依赖**，而不是索要旧atlas或补充库。若缺的是TextureAssets引用到的Content文件，只需补同版本该官方Content文件。现有完整1458输入不满足这两种新增输入条件。

## 验收检查

- 输入不含PlayerWebsite/Wiki/TEdit，原始纹理清单与实际官方Content逐文件闭合；每个绑定、slot和sourceRect有合法对象/边界，全帧不止14行走帧。
- 坐骑实际Count与配置数一致；七类序列边界、playerYOffsets长度、所有有内容的前后/发光层正确；复核0/1/2/50与特殊矿车/钻机。
- 裸身→所有发型→盔甲新旧帧→各饰品槽→坐骑→组合状态逐步真实运行，不把method inventory当通过。对有方向/gravDir/男女/皮肤变种/复合手臂的状态保留对照。
- 记录真实draw-op与游戏方法输出的一致性；shader/render-target不支持项显式可见。首次CPU合成至少验证透明混合、旋转原点、flip、剪裁、前后层顺序和坐骑偏移。
- 两次同输入/状态输出hash一致；随机/时间固定且写入context。相同资源只存一份对象；首个预览只下载必要shard/纹理。
- 全任务300,000,000B限制下运行，超额失败；无GPU、无网络、无GUI、无图像解码在Mono、无公开游戏素材上传。未知布局可查看原图，但不能宣称完整布局/合成发布已通过。

该调查合同已落实为真实提取与客户端原图组合。DrawData采样仅私有对照，不发布预渲染组合全集；普通图层一致性已验证，GPU和动态效果遵循用户允许近似的范围，不声称逐像素匹配全部游戏效果。
