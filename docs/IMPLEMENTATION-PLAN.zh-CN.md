# 真实 Terraria 资源流水线实施计划

日期：2026-10-02。本文保留实施前的架构与任务合同，下文状态为调查快照。真实Linux提取、六项目接入与离线验证的最终结果见[交付记录](DELIVERY.zh-CN.md)。

## 1. 决策与顺序

主提取器使用独立 Go CLI，标准库处理 ZIP、JSON、PNG、gzip、SHA-256、子进程；容器内固定安装的窄版 C# 助手执行游戏语义。用户已允许 Mono/.NET。不要移植整个 TerrariaServerHook，不引入 Python 服务、IL 解释器、桌面 GUI、Hook 或线上可上传的插件。图像 worker 在 `internal/xnb/` 移植当前带许可的 LZX/DXT 关键逻辑，PNG 用 Go 标准库；现有 C# 解码器只作对照。保留原算法许可、来源及修改说明。

严格顺序为：**独立 pipeline 对真实包验收 → boot 调 CLI → admin 上传/真实差异/确认 → 正式 CDN 布局与发布 → app/TerraWasm 接入及去重资源**。前一阶段退出条件未通过，不以合成示例进入下一阶段。独立阶段使用本地 bare Git 验证发布，不提前改生产 CDN。manifest 与分片合同在独立阶段冻结，正式 CDN 阶段只接入目的地与渠道。

完整目标是 ZIP 内 `server/<version>/...` 与 `Content/...` 足以产出当前 app 所需的全部游戏公共资源，并补齐用户要求的物品完整属性/说明、人物全部原始造型帧。用户已明确只保留游戏包可直接提取的数据，允许去掉无游戏来源的 Wiki/TEdit/人工展示内容，因此不迁移补充数据库。应用操作逻辑和 UI 基础保留；需要的游戏派生算法必须可由游戏来源核对。若某类仍依赖 PlayerWebsite 中间产物，独立阶段尚未完成。

## 2. 已核对的系统边界

| 现有入口 | 可复用部分 | 必须切断或补齐的依赖 |
| --- | --- | --- |
| Hook `src/Metadata/MetadataExtractionExecutor.cs:823-1044` | `ContentSamples`、`TileObjectData`、`MapHelper`、图鉴数据库初始化；ID、本地化、默认属性、研究值、paint 取样 | 同文件混入 System.Drawing、TConvert、PNG、旧 CDN 三包与时间戳。仅抽出初始化/语义方法，不复制输出整体结构 |
| Hook `src/Server/TerrariaAssemblyLoader.cs` | 原始程序集和嵌入依赖加载，无 patched copy 的路径 | 去掉 HookConfiguration、MethodPatchInstaller、工作区 `code/`、Windows GAC 搜索和吞掉必需依赖失败；只解析明确允许的运行时目录与输入程序集依赖 |
| pipeline `tools/TextureExtractor/` | 已有 XNB5/LZX、Color/DXT1/3/5、PNG 实现及 ThirdParty 许可 | 当前整文件/解压/RGBA/PNG 多次驻留、全报告列表；先限制为逐文件，后由测量决定流式改造。未知 XNB 不能 silently skip 后宣称全量 |
| app `shared/data/*`、人物/pixel 脚本 | 实际消费者字段及旧产物作为差异参考 | 多个 Python/PlayerWebsite/Wiki/TEdit 来源。编译内置目录、walk.bin、stable-rgb.bin 和图鉴包都需在最后阶段替换 |
| TerraWasm `exports.web.txt`、`src/terra_mem.c`、`src/terra_map.c` | SHA/gunzip、颜色/TXCI/icon atlas 注入、世界/玩家读写 | `.map` palette lookup/option counts、frame 分支、版本与部分主题表仍编译内置；现有颜色 setter 不等于完整资源注入 |
| boot `WorldGenerationService` 与 admin viewer 页面模式 | 持久任务、claim、超时/取消/恢复、ProcessBuilder；后端菜单/权限与页面模式 | 现有 `/infra/file/upload` 会读整个文件进 JVM；不适合此大包。CDN 配置服务没有 Git 发布能力 |

详细证据见同目录 [APP-RESOURCE-INVENTORY.zh-CN.md](APP-RESOURCE-INVENTORY.zh-CN.md)、[NATIVE-CDN-INVENTORY.zh-CN.md](NATIVE-CDN-INVENTORY.zh-CN.md)、[BACKEND-ADMIN-INTEGRATION.zh-CN.md](BACKEND-ADMIN-INTEGRATION.zh-CN.md)。后者“Mono/.NET 待允许”已由本次用户授权消除，运行兼容性仍须实测。

## 3. 独立 CLI 合同

建议一个二进制 `trp`，不新增 Web 服务：

```text
trp extract --input bundle.zip --output empty-attempt
trp verify --candidate empty-attempt
trp diff --candidate empty-attempt --baseline baseline-release --output review-dir
trp publish --candidate empty-attempt --review review-dir/review.json --confirmed-manifest-sha <sha256> --baseline-commit <sha> --repo checkout
```

仓库/分支、助手路径与资源预算来自部署端固定配置，不接受 ZIP 内命令、URL、适配器路径。`extract` 自动识别程序集版本、ID Count、实际能力，部署可选 profile 限制，但不要求每次新游戏版本人工登记 SHA 才执行；结构变动/必需能力缺失则阻止完整发布。`extract` 内部做 preflight，无需另一组预检服务。stdout 每行一个有限长度 JSON 进度事件（stage/done/total/messageCode），stderr 有限诊断；正式结果写磁盘。退出码：0 成功，2 输入/参数无效，3 不支持结构/格式，4 不完整，5 运行失败；超时/取消由调用者记录。失败保留 `report.json`，不生成可发布完成标志。各命令都能在无 boot/admin 情况下独立运行；首次没有 baseline 时生成初始新增 review。

输入可多一层 ZIP 顶层目录，但 server 候选、平台选择和 Content 必须唯一；同时出现 Windows/Linux 包时由受信 profile 选择平台，记录具体程序集 SHA，不靠“第一个 exe”。保留完整服务器分发依赖，不能要求用户只上传 exe。安全解包拒绝绝对/UNC/盘符路径、`..`、符号链接、重复/大小写碰撞路径、特殊文件、加密条目及数量/单文件/总解压量/压缩比越界。使用新建私有目录与实际写出字节限额，校验 CRC；失败不留下可复用成功目录。

ZIP SHA 用于上传审计；另记录 server 程序集 SHA 与 Content 规范化树 SHA（排序后的相对路径、长度、内容 SHA），避免仅因 ZIP 时间戳/压缩参数不同产生资源版本变化。游戏版本以程序集实际数据为准，目录名只作提示。Content 通常不能单靠纹理自证确切版本：保留来源声明、树 SHA、必需贴图/尺寸/ID 交叉检查，不能把“没缺文件”写成“已证明来源版本”。

输出只允许：`candidate/manifest.json`、`candidate/objects/**`、私有 `report.json`/`inventory.ndjson`、助手中间文件。manifest 最后原子落盘；对象路径由本地 hash 产生，禁止返回 attempt 外路径。发布只复制 manifest 引用的白名单对象，原始 ZIP、exe/DLL、绝对路径、日志、存档永不进入资源仓库。

## 4. C# 助手合同与真实能力

固定调用：`RuntimeExtractor --server /input/server/TerrariaServer.exe --output /output/semantics`；真正需要 Content 定位时追加 `--content /input/Content`，首版语言固定 en-US/zh-Hans。使用参数数组，协议版本写输出，不新增请求配置解释器。生产镜像锁定助手构建和运行时版本，先在真实 1458 Linux 分发与 Windows 分发中选择确实可工作的组合，不能预先声称 net48/x86 或 net10 能互换。先选可工作的无 NuGet 包 net10/Mono 路径，不为双运行时提前维护两套逻辑。

助手不产生 PNG、不打包 CDN、不持 Git 密钥。逐域写 NDJSON（UTF-8、无 BOM、每行限制、确定排序），最后写 `result.json`：protocol、gameVersion、assemblySha256、profile、各域行数/文件 SHA、支持/缺失能力与诊断。进程崩溃或缺少 result 即整个 attempt 失败。Go 重读并校验，不信任助手返回的路径/尺寸/计数。这里“可信助手”不代表被加载的上传程序集可信：执行必须在无网络、非 root、只读根文件系统、只读输入、独立可写输出/tmp、PID/CPU/内存/时间限额的隔离环境；不挂载 Git 凭据、宿主仓库或 Docker socket。使用固定部署 worker/runner，不给 boot 任意 Docker 命令权。

| 输出域 | 必需语义及验证 |
| --- | --- |
| `ids`、`items`、`prefixes`、`buffs`、`research` | 常量别名与实际样本域分别记录，保留废弃/不可入背包状态。Item.SetDefaults/ContentSamples 的明确字段白名单及版本字段描述，前缀适用性/效果与装备槽、buff、研究值；不能仅导出 primitive 字段就称“所有属性” |
| `localization`、`item-tooltips` | 原始语言键/模板、解析后的中英说明与明确 context。固定默认角色/难度/状态并记录；有状态动态说明用上下文参数/能力标记，不能把一次截图文本称为任意游戏状态完整说明 |
| `tiles`、`walls`、`paints`、`map` | ID、map option/lookup、颜色、完整 TileObjectData style/alternate/random/尺寸/坐标步长/anchor、frameImportant。paint 后颜色由游戏 MapHelper 取样，对高度/邻居/帧依赖分类 |
| `bestiary`、`npc-frames` | npcNetId（允许负数）、NPC type、persistentNpcId、解锁 tracker、名称/排序/类别，实际纹理 key、裁切/方向/缩放/必要颜色规则。每种变体与原图/派生图关联 |
| `player-layouts` | Content 玩家/头发/装备的原始纹理和全部帧清单，游戏可提供的 frame/槽位/层级描述；不得缩成 standing/14 walking。发型/衣服/装备组合须有游戏来源绘制配方且实际验证；若 server 不含所需客户端语义，明确该能力缺失，不用人工补充或 PlayerWebsite atlas 充当自动抽取 |
| `pixel-candidates`、`worldgen-item-domain` | 稳定 Tile/Wall/Paint 候选及 RGB/禁用原因；世界生成宝箱物品域是生成逻辑或保守、带来源的规则，不能把“所有可存放物品”误作“世界生成可掉落物品” |

前述为消费能力边界，不强制每行都有同样的复杂“证据对象”。每个域一份 producer/source/version 记录，加记录必要来源 key 即够用；异常单列。采用实际消费字段/ID/资源引用来验收，不继承原型 14 类/110 子项框架。server 初始化失败、语言缺失、特定反射字段变更必须指明域和成员，并退出不完整，不能空数组成功。

## 5. 资源清单、共享分片与字符串

使用一种资源 schema 与一个不可变 release manifest。manifest 包含：`schema:1`、gameVersion、source（server SHA、Content tree SHA）、extractor/profile 构建标识、consumerContractVersion、engineResourceAbi 范围、languages、capabilities、各域 shard 索引与对象引用。公开 manifest 不含机器路径、生成时间或 Git commit；这些置私有 report，确保相同输入和构建可字节重现。`releaseId = SHA256(manifest 原始确定字节)`，manifest 不反向包含自己的 releaseId，避免自引用。

对象统一引用 `{path, sha256, bytes, decodedBytes, mediaType, encoding}`；仅 gzip 二进制有 decodedBytes，PNG 不再 gzip。路径为 `objects/<sha前2位>/<完整sha>.<扩展名>`。JSON 使用固定键顺序、UTF-8、固定数字规则，gzip 固定 mtime/header/压缩等级。候选自身 verify 应重新计算每个对象与解压大小，不能只核对清单互相声明。

共享的是**语义域**，不是每页面一个重复总包：轻量 item index（ID/name/icon/分类）与按 ID 范围的 item detail 分开；prefix/buff、bestiary、材料布局、地图、人物、像素索引各自按需加载。ID 域采用固定范围段（初始可用 256 个 ID/段，负 NPC 单独明确范围），超限记录按结构拆分，避免一条插入令全部 shard 重排。纹理按单图存储，只有真正同时加载的小帧生成受尺寸约束的 atlas；不预先将所有图片打为一个巨包。共享原图通过 image key → object ref 引用，派生裁图/atlas 带原图和 recipe hash；相同像素以 width/height/规范 RGBA hash 去重，相同编码文件再以对象 hash 去重。单纯同 RGB 的材料不能去掉 ID/选择语义。

字符串采用每个 shard 的同伴语言表，按字符串键稳定排序（优先游戏 localization key，生成字段用 `domain/id/field`），同一表内精确值去重。记录持有字符串键/表引用，不跨全版本共享可变数字索引。`zh-Hans`/`en-US` 分开对象，缺失按明确语言 fallback 返回，报告缺失；允许详情未下载时仅加载轻索引。全局字符串大表会造成单词改动引起全部数据 churn，第一版不做。未消费的完整语言档案可以独立 shard 保留，不随首页加载。

地图/像素和人物的重型二进制按已有正确格式保留必要投影：TXCI v3 与 stable-rgb 是不同功能，先分别重建并验证，不能以“去重”删除其中一个。新增二进制 schema 仅在当前格式无法容纳必需合同的情况下引入。

## 6. 无法从 ZIP 自动取出的边界

| 类型 | 处理决策 |
| --- | --- |
| Wiki 种类名、别名、说明；TEdit shape/frame 标签 | 按用户澄清删除，不迁移 overlay/补充数据库。使用游戏原生名称和 TileObjectData/实际 frame 事实；缺乏游戏依据的展示项从客户端移除，不能把旧标签假作游戏事实 |
| 人物染料 CPU 模拟、frame repairs、渲染层级；实体 marker selector/裁切配方 | 逐项判定游戏来源。有来源且功能需要的算法作为派生适配器；仅人工美化/修补的展示可去掉。从原纹理取全部帧是必需项，组合效果另行验证，无依据的特殊效果标不支持，不用相似图蒙混 |
| 世界生成配置 UI、应用颜色/半径/排序偏好、规则预设、品牌占位图 | 应用数据/文案，留应用或配置服务；不作为游戏提取覆盖分母 |
| 世界/玩家存档、世界缩略图、用户地图、账号与分享状态 | 用户数据，留用户工作流；不写公共资源仓库 |
| `.wld/.plr/.map` 结构版本与运算代码、WASM 本体 | 引擎代码合同，需独立引擎发版/真实文件验收；资源热更新不能更改解析代码 |

以 1.4.5.8 做首个真实验收，但版本号本身不作为执行或发布白名单。新版本自动识别 Count/成员/资源并按能力合同验证；结构稳定且所有必需项验证通过即可进入正常审核，结构变化或必需能力缺失则明确失败，不能伪造未来兼容。每域记录 extracted/derived/unsupported/missing；当前消费者必需域缺失阻断完整 release。未知 XNB reader（音频/font/effect 等）若非 app 游戏资源需求，记入 input inventory 的“不消费”；已引用纹理解码失败必须失败。

## 7. 差异、审核与发布

diff 以逻辑资源键比较，产出新增/修改/删除/缺失，修改包含必要字段 old/new 与图片预览引用；区分像素变化与仅编码变化。统计对象 bytes 与网络增量，但统计数不能代替资源差异。现有旧 CDN 1456 engine manifest 已发现文件 length/hash 不一致，第一次基线需对实际 checkout 重新 inventory，记录旧清单错误；不能信旧 hash，也不能据此修饰成无变化。

`review.json` 固定绑定 candidate releaseId、baseline commit、diff SHA、consumer contract、未解决缺失计数；其 SHA 为审核摘要。boot 确认请求提交该摘要与基线，服务端重新验证并存操作者/时间。首版由固定 Git 发布目标、后台鉴权、确认绑定候选 manifest SHA/基线、TLS CDN 和对象 hash 建立发布边界；不新增签名密钥基础设施或多层 approval 文件。

发布在隔离的干净 checkout/worktree，只写 manifest 引用对象与 release 文件；核对远端分支仍为审核基线，提交/推送后记录 commit。基线变动回到重审，推送结果未知先查询远端确认再重试，不能 force push。阶段一用本地 bare repo 演练，无 CDN 凭据。发布目标最终为 `Live-yan/terraviewer-images`，本机工作副本 `D:\Code\CLionProjects\terraviewer-images` 只是部署配置实例，Linux 使用挂载路径。

CDN 正式目录建议 `objects/**`、`releases/<releaseId>/manifest.json`、`channels/stable.json`（最小版本指针、manifest SHA、兼容范围）。只有 channel 可变，其余 hash 内容不可变；去掉三包 hash/alias 双份和重复 latest。对象/CDN 可用性探测通过后再提升 channel，app 下载失败继续已验证版本。渠道更新与缓存传播不是 Git push 的原子延伸，客户端要容忍短时缺对象。允许破坏旧目录，但旧 app 切换窗口必须明确；不默默制造两个长期合同。当前使用中的旧 release 不做自动回收，保留/回滚策略明确后再 GC。

## 8. 可分派职责与阶段出口

所有执行者只修改自己的范围，保留其他人的未提交改动。合同负责人先冻结 CLI/result/manifest 示例，再并行实现互不冲突的模块。`go.mod` 由主代理独占，纹理 worker 不自行修改。下面是建议文件归属，不要求为了目录表创建空架子。

| 阶段/负责人 | 文件责任 | 验收出口 |
| --- | --- | --- |
| 1A Go 主程序 | `go.mod`、`cmd/trp/`、`internal/input/`、`internal/artifact/`、`internal/review/`、`internal/publish/`、相关 Go tests | 合包安全解包、受控助手协议、逐图/逐 shard 处理、确定输出、真实差异、本地 Git 确认发布与恢复通过；无 Python 运行依赖 |
| 1B C# 语义助手 | `tools/RuntimeExtractor/`、其原创 fixture/check；只读 Hook 参考 | 对真实 1458 在目标 Linux 容器输出上表必需域；中英、动态说明、地图、NPC/人物规则完整性检查；不依赖 GUI/GAC/工作区 code/旧 atlas |
| 1C 图像/派生工程 | `internal/xnb/`、`internal/derived/` 与相关 Go tests/ThirdParty notices；只读 `tools/TextureExtractor/` | Go LZX/DXT/PNG、原图全帧、游戏来源特殊 NPC/marker/人物派生；stable-rgb/TXCI 与旧正确参考逐值比较，随机/边界检验真实游戏颜色；预算内完成 |
| 1D 整合负责人 | `contracts/` 中少量合同样例、`scripts/verify-real.*`、`Dockerfile`/运行脚本、文档、必要 CI | 所有真实必需域和实际 app 消费引用闭合；上传 ZIP 是唯一私有数据输入。先实现证明再删除 Python 原型/旧重复脚本，公开 CI 不上传游戏资源 |
| 2 boot | `yudao-module-viewer` resource job Controller/Service/Properties/DO/Mapper 与 SQL | 专用私有大包流式上传、持久作业 claim/重启/取消/超时/清理、固定 CLI、候选 verify、分页差异与权限；大文件不整包进 JVM |
| 3 admin | `src/api/viewer/resourcepipeline/`、`src/views/viewer/resourcepipeline/`、菜单权限配置 | 真 ZIP → 实际提取进度 → 真实分页差异/图片 → 确认 → 发布结果；确认过期/缺失/失败可见，不能确认假数据 |
| 4 CDN/发布 | terraviewer-images 新对象/manifest/channel 与发布配置 | 确认绑定正确基线，真实 Git 提交与推送，镜像内容校验、提升/回滚；删除旧重复目录按已批准迁移执行 |
| 5 TerraWasm + app（先 ABI 后消费） | TerraWasm 资源 ABI/解析/地图表，app `infrastructure/assets/` 与各消费入口，生成/打包脚本 | 统一资源解压/校验/解析由 TerraWasm 负责；JS 只负责网络/磁盘缓存/界面协调。全部重资源按需下载、版本原子切换、小程序产物无旧大表/图包，真实存档往返/渲染无回归 |

阶段 1A/1B/1C 仅在协议确定后并行，不能在不知道助手输出时猜下游。1D 可复用已有脚本，但真实性检查不能只跑合成 fixtures。低内存先取单 job、单纹理解码、单域生成；只存 hash/索引小表，原像素与压缩结果落盘/流式输出，不同时持有整包或全部 RGBA。全任务硬限制为 300,000,000 字节，覆盖 Go、Mono、子进程与容器计入内存；禁止按进程各给 300MB。逐阶段记录内存峰值、输入规模、耗时与临时磁盘；超过即失败并定位，不提高预算掩盖问题。顺序执行释放前阶段内存，减少整体峰值。

阶段 5 的资源 ABI 应提供一次候选打开/验证、按域/ID读取或注册、release激活/关闭的最小接口；网络与缓存仍留宿主。已有 `_tx_gunzip`、`_terra_sha256_*` 和颜色/TXCI/atlas setters 先复用，不另造并行 JS parser。新地图资源需包含完整 lookup/option/palette/版本并验证边界；算法本身随 WASM 更新。页面同步 `itemCatalog()` 等必须改为加载完成后初始化，避免半新目录半旧图片。按页惰性加载与内存回收验证通过后，才能删除本地重资源，不靠“请求失败时偷用旧打包表”掩盖缺漏。

## 9. 必跑验收

1. **真实独立重建**：同一 1458 server+Content 合包，在空工作目录/全新 Linux 容器运行，无 Tdecoder、PlayerWebsite、Python、GUI/GAC 隐式挂载；记录输入 SHA、profile/runtime build、实际耗时/RSS/磁盘。重复两次 release/object SHA 相同；改变 ZIP 顺序/时间戳但不改内容仍得到相同 release。
2. **全量覆盖**：由 runtime 实際 ID/样本域、Content 全纹理 inventory 和 app 消费字段建立可执行检查。每个当前需要的 ID/variant/frame/语言/图引用有值或合理的显式排除；完整发布缺失为零。检查负 NPC、alias/deprecated item、最大 ID/空白项、研究/buff/前缀、MapHelper 多 option、TileObjectData alternate/random、人物所有原图/所需预览。不能用旧 PNG 文件数等同有效 ID 数。
3. **真实语义与像素**：与现有 Hook/独立游戏取样对照；默认物品属性/中英 tooltip、特殊 NPC 裁图/颜色、人物站姿/行走/染料代表样张、paint/墙/负漆/深漆/隐形语义必须验。无图形等价证据时标失败而非仅比 manifest。旧产物只有经来源核对才可作为 oracle；旧 engine 错误 manifest 禁作 oracle。
4. **纹理与资源边界**：真实 XNB 编码统计，加 Color/DXT1/3/5、透明像素、非4倍尺寸、LZX边界/损坏 fixture；未知 reader 分类、尺寸上限、路径穿越、大小写重名、炸弹/超额、截断、坏 CRC，均可预测失败且无半成功 manifest。保留已复制算法的原文件许可。
5. **真实差异/发布**：同源重跑无差异；定向改一个语义值/一张图、删一项产生正确逐条差异；缺必需源阻断。审核后篡改对象/manifest、过期摘要/基线、并发发布、进程终止、push结果不明可恢复且不重复推送；本地 bare Git 验证完成后才换正式远端。
6. **boot/admin**：真实大小上传内存与反向代理限额、权限、重启中断/取消/超时、磁盘不足、分页差异与图片审核；后端校验确认绑定值。现有 Maven smoke 不证明这些新增状态机路径，留最小可运行集成检查。
7. **app/WASM**：H5/小程序首装空缓存、第二次命中、断网已缓存、未缓存失败、错误 manifest/object hash、断点中止、磁盘不足、跨镜像同release、更新中途重启与回滚。每页只拉所需 shard，内存/包大小实测。真实 `.wld/.plr` 读写往返与 `.map` 渲染/格式校验；不支持引擎ABI/游戏版本保留旧版本并明确提示。

验收材料保存为本机/私有 artifact 与简短报告，不把游戏程序集、素材或上传包放进公开 CI。无需新增通用证据平台、通用插件系统或消息队列；单机持久任务、受控 CLI 与上述少量协议足够。只有真实吞吐或多节点恢复需求出现时再扩大部署结构。
