# viewer-app 共享 CDN、完整离线资源与人物本地组合迁移计划

日期：2026-10-02。本文保留迁移前的基线分析、分工和验收合同；下文旧路径及“尚未完成”描述属于调查时快照。当前实施结果与限制见[交付记录](DELIVERY.zh-CN.md)。

最新：官方闭包候选042f8430…整包及后端重复提取通过，公共对象18.33MB、真实SDK首次完整下载18.54MB；坐骑族补齐66种/87个实际贴图槽及尺寸。新TMRT/TXCI与天空色表原生源521765d已clean构建同步app，并保留用户此前流式优化。原6747/768adc及旧原生ABI描述是规划时的历史状态，不能据此覆盖当前产物。旧资源已退出构建；实际H5完整安装、关闭浏览器后断网全页面/真实存档和三种宽度全部列表上下遍历均通过。

## 当前基线与旧清单差异

- 起读时 app 为 `master / 02c473a`，存在用户未提交修改。检查过程中用户完成提交，最终基线为 **`master / 994a50d`**，`git status --short` 为空。新提交为“修复世界文件生命周期并优化流式处理与广告提示”。实施前再核对状态，不回滚用户文件处理、广告、云存档、人物面板或性能修改。
- 未找到 `viewer-app/AGENTS.md` 或下级 AGENTS.md；遵循 Tdecoder 根指令。使用 ripwire-orient 定位资源/清理边界，再按实际源码核对。
- [APP-RESOURCE-INVENTORY.zh-CN.md](APP-RESOURCE-INVENTORY.zh-CN.md) 的游戏资源依赖仍成立：内置 item/material/catalog 数据、local-catalog 分包、stable-rgb、walk.bin、choices 图、旧前缀/分类/染料/修补表仍被实际使用。
- `infrastructure/wasm/generated/terra.manifest.json` 现锁源 **`0e4ddb335cfed77d607dcfbff9cafdbea1329f64`**，包含 a11112f 之后的用户流式优化。新 TMRT ABI 尚未同步入 app。必须在此基础上同步新产物，不能拿旧 WASM 覆盖用户优化。
- `pages/user/user.vue:201` 的“清除缓存”不仅调用 assets/clear.js：还清空历史、调用 `clearMiniProgramUserFiles()` 递归删除 USER_DATA_PATH，并执行 `uni.clearStorageSync()`。这是 active 安装保护的必改点。
- `features/pixel-art/services/txci-index.js:11,586` 仍把同色组限制为 256；新 CDN 真实有 306 项，直接替换 URL 会失败。
- 当前 H5 未发现 Service Worker 注册、PWA/Workbox 配置。`local.mjs` 的同源 fetch 和 CacheStorage 图片缓存不能保证关闭浏览器后断网重新打开整个应用。
- 当前 CDN 合同已把人物 plans/oracles 私有化；旧合同描述中仍有 plans/operations 的历史段落，消费实现应以实际新 manifest 的 families 为准，不能要求这两族公开存在。

旧 72.5 MB 与 2.753778 MB 应用局部资源的比较口径见 [PUBLIC-RESOURCE-SCOPE.zh-CN.md](PUBLIC-RESOURCE-SCOPE.zh-CN.md)。父代理报告新候选 6747 约 35.45 MB，textureScope/player-texture-bindings 仍在收尾；本规划不把这个数字硬编码进 app。

## 交付范围

所有游戏 metadata、物品/NPC/Tile/Wall/人物图片与源帧、RGB/SRGB/TXCI 从同一 SHA release 读取；保留用户要求的完整物品属性与 tooltip、完整 Tile/Wall 原图及人物全部潜在源纹理。游戏来源之外的旧 atlas、Wiki/TEdit 展示补充与人工像素修补退出生产依赖。应用 UI、编辑命令、用户存档/草稿/方案、世界生成业务 schema 与 WASM 可执行代码仍随程序分发。

“我的”底部新增独立“更新资源”，不混同已有微信“检查更新”。按钮完成全部公共对象/图片下载与持久化，展示版本、已验证字节/总字节、对象进度、取消/继续、失败原因；取消、空间不足、hash 错误不破坏旧安装。不能以“已访问的页面可用”或“下载管理器已写好”代替完整迁移。

## 最小资源架构与冻结接口

沿用 `infrastructure/assets`，只增加一个 release 管理器、平台存储适配和格式校验模块，不建立通用插件系统。

建议公共接口由资源 worker 先冻结并提供内存存储测试实现：

```js
restoreResources() // 启动恢复持久 active；不依赖网络或登录
checkResourceUpdate({ signal }) // 只获取/验证 channel + manifest
installResources({ manifestSha256, signal, onProgress }) // 完整、可重试安装
prepareResources({ families, rgb, signal }) // 获取固定 release 的会话
subscribeResourceState(listener) // idle/downloading/verifying/ready/error 等

// prepareResources 返回的会话；不得隐式换 release
session.releaseSha256
session.manifest
session.readFamily(name, { id, shard }) // 分片读取，返回该 release 的数据
session.readObject(ref) // 校验压缩 bytes/SHA，按 encoding 解码并核对 decodedBytes；返回 decoded Uint8Array
session.resolveTexture(assetId) // => {path,width,height,sha256?,releaseSha256?}，path 为已校验本地 PNG 路径或 H5 Blob URL
session.release() // 解除操作/页面对版本的占用
```

URL、下载、缓存、PNG hash 与 bundle 归属只在 manager 中解释。业务模块使用稳定数值 ID 和真实 texture binding；不得再各自持有 CDN revision、TTL 或镜像下载代码。接口的异步部分止于资源准备和分片/纹理读取；完成 prepare 后，物品选择、规则校验等已有同步纯函数可以读取已准备的版本内投影。

上述接口已冻结供两个 worker 对接。`textureScope.sourceCount/selectedCount` 区分上传总数与公开选择数；完整 `player-texture-bindings` family 提供所需映射。Session 不假定所有 Content 都在公共 catalog，不以缺少未选中的游戏图片报错，也不绕过绑定去猜文件名。

版本原则：

- 已安装 active 优先，本地恢复不发必要网络请求。check update 可后台执行，但不把新 metadata 填进旧会话。
- 无完整安装时，在线 prepare 可使用一个已验证 manifest 的临时固定会话、按需下载；明确它不是“完整离线安装”，不得写 installed/active 完成标记。首次断网无安装时显示资源缺失与更新入口。
- 完整 install 在 staging 验证所有对象后才能提交 installed/active。下载期间旧会话继续可用。
- 最简单的切换策略是延迟启用到世界/PLR 编辑会话及 pending native/stream 任务结束，再失效页面投影并重新 prepare；绝不关闭未保存编辑来强制切换。下载完成但暂不能启用时显示“已下载，关闭当前存档后启用”。后台自动检查/下载可复用同一流程，无第二套更新协议。
- 所有缓存键至少含 release SHA；过期异步响应不能写入当前投影。现有 resourceEpoch 可复用，但 epoch 不是资源版本，不替代 release SHA。

## 持久安装、原子切换和图片

微信使用专属 `USER_DATA_PATH/viewer-resources-v1/`，含内容寻址 objects、按 PNG hash 存放的 images、release manifest/installed、staging journal 和 active 指针。H5 使用独立 IndexedDB 数据库保存原始 bytes/Blob 与安装记录，active 写入同一数据库事务；不把整包转 base64 放 uni storage。

安装流程：

1. 验证 channel（4 KiB 上限）与 manifest 原始字节 SHA，之后才解析。检查 schema、路径、引用/hash/大小、目标运行时能力。支持将 uint64 等不安全整数保留为十进制字符串/BigInt；先 JSON.parse 后补救已丢失精度不可接受。
2. 枚举全部唯一公共引用，按 hash 复用已验证文件。并发保持小且有界；每完成一对象持久写进度。重试恢复到已验证对象/图片，不把任意 `.part` 当成功；首版对象级断点续传即可，不必加 HTTP Range 协议。
3. gzip 核对压缩 hash、decodedBytes 和格式，逐片释放；使用现有 pako，不能同时 inflate 所有族。不得把一般族“256 行”规则套到 item-index；后者是完整轻索引。
4. imageBundles 采用标准 `.zip.gz`：Object 的 `encoding=gzip`、`decodedBytes=ZIP 字节长度`（不超过 16 MiB）、`mediaType=application/zip`；内部仍为确定性 Stored ZIP。按 catalog 的 PNG hash 前缀找包，先核对压缩 bytes/SHA，再有上限解压并核对 decodedBytes，随后严格检查 ZIP 中央目录/本地成员、Store、无加密、路径、重复/缺失/多余成员、CRC、PNG SHA 与尺寸。逐包逐图写共享 hash 目录。全部成员持久校验成功后删除传输包，installed 记录其已完成成员；断点恢复检查所需 PNG 是否仍齐全。复用 gzip codec，不引入自定义图片容器，不把多个包或全部 PNG 解码成 RGBA。父代理 256 包实测传输从 28,932,998 B 降至 25,391,145 B，解码 ZIP byte exact；以最终 manifest 为准。
5. 空间估算包含旧 active 所引用文件、候选新增唯一对象、当前下载/解包临时峰值及 manifest/journal。能查询 quota 就使用，不能查询时依赖实际写错误回滚；不宣称固定设备容量。空间不足不得删除仍被 active/正在使用版本引用的文件。
6. 写 installed 清单后，微信用临时文件+平台 rename 的持久提交协议切换 active；保留上一有效指针/安装记录用于中断恢复，不先删除旧指针。H5 用 IDB 事务。启动只接受完整 installed 对应的指针，不以 staging 更新时间选择“最新”。
7. GC 仅删除不被 active、上一保留版本、进行中安装和会话引用的对象。原 PNG/metadata 不参与图片 LRU；RGBA、Blob URL、Canvas、索引内存可以释放并从本地重建。

微信图片展示使用本地 PNG 文件路径，H5 使用限量 Blob URL 并在视图释放时 revoke。`resource-image` 保留 recycled-node generation/token 检查，但新增异步 assetId 解析，过期 resolve 不得覆盖新绑定。物品别名从 texture-references/item-index 获取，不拼 Item_ID；图鉴使用 npc-frames 的源图/矩形/缩放/颜色语义，不把整张 NPC sheet 拉伸当图标。Tile marker 从官方 sheet 与游戏 frame 数据裁图，替代 static/entity-markers。

## 清理与 H5 应用壳

必须共同修改 `assets/clear.js`、`storage/temp-file.js` 和 `pages/user/user.vue`：普通清缓存跳过资源目录、安装数据库和 active 指针；不要直接 clearStorageSync 后丢掉资源索引。资源指针放独立持久目录/IDB，减少对通用 storage 的依赖；对现有用户文件清理语义保留明确文案，不顺带删除 active 游戏安装。

H5 本轮可以一并实现离线壳，但必须增加代码和部署验收：由构建输出生成同源 precache 清单，包括入口 HTML、全部本地功能懒加载 JS/CSS、WLD/PLR WASM 与基础 UI；注册版本化 Service Worker，navigation fallback 到该构建的入口。API、登录、云存档和用户数据不加入壳缓存；不要热切换仍有未保存编辑的页面。检查实际 BASE_URL/scope 与 HTTPS（或 localhost）环境。只写 IDB manager 时，必须把“关闭后重开 H5 离线”记为未完成，不能用浏览器偶然 HTTP 缓存验收。

微信代码分包也属于离线可用条件：第一次资源完整安装时，要确认所有本地功能所需代码分包已可离线打开（人物编辑/PLR WASM 等）；游戏资源迁出后删除 local-catalog 数据分包，但不误删仍需要的功能代码分包。微信或浏览器主动清除整个应用存储属于平台外部事件，启动检测安装损坏后提示恢复，不伪报 ready。

## 所有消费者的准备门槛

不能只在 App.onLaunch await 一次：模块 import 可能更早执行同步目录求值。应把 import-time 计算改为 prepare 后的惰性工厂/缓存，以页面状态显示 loading/error/retry，再创建依赖它的子组件。

| 当前入口 | 必须迁移的消费行为 |
| --- | --- |
| shared/game/item-catalog.js、chest-item-catalog.js、item-prefixes.mjs | item-index 轻目录；完整 items 详情按 ID 分片；prefix 实际适用集/效果；新增或改物品详情入口展示全属性与默认 tooltip，不只缩略索引 |
| shared/game/material-catalog.js、world-editor/pages/services/tile-materials.js、tile-rules.js、rule-page.vue | 从 tiles/walls/paints/map/TileObjectData 建版本内索引；保留 style/alternate/random/坐标布局，去掉 Wiki/TEdit 才有的展示行；未知帧保留明确自定义坐标入口 |
| MaterialPairPicker.vue、pages/pixel/pixel.vue、pages/mappingscheme/mappingscheme.vue、mapping-scheme-store.js | 删除顶层 getMaterialCatalog/getStablePixelCandidates 依赖；候选顺序直接来自新 compact stableCandidates，不再生成旧白名单顺序 |
| stable-rgb-lookup.mjs、stable-pixel-mapping.js、txci-index.js、world-write/pages/write-page.vue | SRGB 读取同 release 的 stableCandidates 顺序；TXCI 放开 256 截断并核对完整 offset/尾界；取消旧独立 revision/本地分包/重复下载 |
| catalog/pages/item-page.vue、services/worldgen-item-catalog.js、bestiary.ts/bestiary-data.js、bestiary-page.vue | 游戏静态目录以 CDN 为主并完整离线；用户世界解锁数据仍来自世界/业务接口。后端分页失败不能成为离线目录启动的前置超时；保留负 NPC/netId/persistentId |
| world-generation/pages/config-form.vue、generate.vue | 顶层材料/物品选项在 prepare 后建立；业务配置 schema 保留，在线生成服务无需伪装离线 |
| player-panel.vue、selection.mjs、inventory-sort.mjs、item-categories.mjs | selection.mjs 现在 import 时遍历 itemCatalog；必须改成会话初始化。物品分类、buff、发型/服装选择和前缀不再读 packed data |
| pages/index/index.vue、world-viewer/services/world-session-runtime.js、marker-icon-atlas.js | 在任何同步/协作/stream open 之前准备并安装相同 release 的 TMRT；marker 图也来自同一会话 |
| pages/saves/saves.vue、player save-preview.js | 离线历史角色预览走新原图组合；不能绕回 walk.bin 或依赖网络图 |

轻索引可驻留；items 完整 gameplay/tooltip 只读当前分片，禁止把所有巨大嵌套详情永久建成 Map。同步查找器在未准备时返回明确状态/受控错误，不悄悄回退旧编译数据。资源就绪失败不得影响用户文件保存/撤销。

## 人物本地组合与 TMRT 的实施边界

人物旧 `walk.mjs` 固定 14 帧，读取 walk-index 和 walk.bin；renderer-core 明确来自旧 PlayerWebsite，另有 frame-repairs 的嵌入像素。更换下载地址不能完成本地原图组合。

consumer worker 应以新 `player-texture-bindings`、armor-sets、items 装备槽、player-layouts、mount-layouts 与原始 PNG 为输入；保持 codec/session/edit/history 逻辑，重写资源解码/取帧边界和需变动的绘制规则。游戏可直接提供的 ID/遮挡/槽位集合必须来自新资源；源矩形、前后层、复合身体与双手、姿态/坐骑等算法依据当前官方 PlayerDrawLayers/PlayerDrawSet/Mount 适配。原生 PNG 解码可用平台 Canvas/Image 得到当前所需图层，或现有可用解码器；不要把 node-only pngjs 直接带进微信服务上下文。

有限 plans 在测试中作为私有 oracle：裸身、所有发型、男女/变种、各盔甲/饰品槽、复合 body/hands、方向、源帧和坐骑分支逐项对比。它们不是生产 renderer 的状态查表。原图浏览/全部合法源帧与组合预览分别验收，不把未知源 sheet 简化成 14 行走帧。GPU shader/动态 render target 仍按 capabilities 说明准确限制；不能由保留原图推导“所有动态效果已复现”。当前 bindings/schema 未冻结前，可以保留 UI/组合接口准备工作，但不得猜字段或宣布完整合成完成。

TMRT 使用已实现的 `_txw_set_map_runtime`：主代理先同步包含最近 native 修复的新干净源码产物及 manifest，保留 0e4ddb/a11112f 优化。consumer worker 在 `terra-runtime.js` 增加有界桥接方法，并从同 release 的 map/map-lookup/map-palette/paints 组装 TMRT。安装发生在任何 world/stream/pending open 前；传入 bridge buffer 后可释放，native 已复制持久化。换版必须等待 world 与 pending tasks 全部关闭；TXCI 和本地 UI metadata 不得提前切到另一 release。可把紧凑 TMRT 缓存为按 release 派生的本地数据，无需公共 CDN 再重复一套 palette。

## 两名 worker 的明确所有权

两名 worker 均保留其他人修改，不递归委派。以下边界先冻结，再并行。

**Worker 1：资源安装/持久存储/我的/图片边界。** 拥有 `infrastructure/assets/*`（新增 release manager、store、ZIP/格式校验）、`infrastructure/media/{image-source.js,native-image-source.mjs,resource-image-loader.mjs,image-cache.js}`、`components/resource-image/resource-image.vue`、`infrastructure/storage/temp-file.js`、`pages/user/user.vue`、`App.vue`、H5 service worker/注册/生成壳清单及自己的测试。复用现有 resourceEpoch/token/请求合并，但替换旧 revision/path 规则；新增通用 byte SHA，不误用仅支持 ASCII 的 `infrastructure/wasm/sha256.mjs`。

Worker 1 还拥有 `vite.config.ts`、`pages.json`、`package.json` 及旧资源打包脚本的最终调整。等 Worker 2 发出已移除依赖清单后，移除 `localResourcePlugin`、`playerChoiceImagePlugin`、VERIFIED_WASM_ARTIFACTS 中的 walk.bin、local-catalog 分包，以及已失效检查脚本；把检查替换为“禁止重资源重新打入包”的新断言，不删除仍有效的 WASM/分包/架构检查。Worker 2 不直接改这些共享构建文件。

**Worker 2：消费层/人物 renderer/TMRT 接入。** 拥有 `shared/game/*`、消费用 `shared/data/*` 删除迁移、上表所有 features/pages（除 pages/user）、player-editor 的目录/choices/render/save-preview 路径、pixel-art 数据/服务、`infrastructure/wasm/terra-runtime.js` 及直接相关 runtime contract、对应单元/集成测试。通过 Worker 1 冻结的会话接口取数据。按最小需求导出 field-aware 投影，删除旧 packed 元数据与修补像素；向 Worker 1 汇报需要删除的构建依赖，不共改 manager/media/构建入口。

**主代理：** 同步新 WLD WASM/manifest（generated 文件与 terra-manifest-browser.mjs），整合部署 CDN 地址、完成全链设备/浏览器验收、处理跨 worker 冲突和提交。`features/world-viewer/services/world-session-runtime.js`、write-page.vue、player-panel.vue 在用户提交中有实质修改，Worker 2 只做 prepare gate/资源绑定相关增量，不能换回旧文件。

## 依赖顺序与验收

1. 主代理冻结新公共 manifest/textureScope/player-texture-bindings schema 与真实样例；确认全物品、Tile/Wall、人物源集门禁通过。没有这一项，不实施猜测式纹理选择。
2. Worker 1 先完成会话接口、内存 fake store 与对象/ZIP validator；Worker 2 基于该接口改目录投影和 prepare gate。manager 安装与消费者随后可并行，人物组合按 bindings 逐步落地。
3. 主代理同步新 WLD WASM；Worker 2 接 TMRT/TXCI 并验证同 release。有效 foreground、pending task 切换门禁等 native 修复不能丢失。
4. 人物所有旧 atlas/walk/修补依赖和各页面旧元数据依赖清除后，Worker 1 移除分包/构建复制并增加产物禁入检查。没有这一步，不算小程序瘦身完成。
5. 同时完成 H5 壳；微信验证首次完整安装后关闭/重启、所有本地功能与代码分包冷进入均离线可用。
6. 故障矩阵：取消/重试、对象 hash 错、ZIP 缺/重复成员、decodedBytes 错、写入空间失败、安装中杀进程、active 提交中断；旧 release 必须持续可用。新安装完整后重启能恢复，再清普通缓存仍能离线运行。
7. 版本矩阵：下载时继续旧世界操作；pending open/stream 存在时启用延迟；全部关闭后一次切换，页面/图片/metadata/TMRT/TXCI 同步失效并重准备，旧响应不串版。
8. 功能矩阵：物品全属性/tooltip/前缀/研究/buff、箱子、完整 Tile/Wall/帧选择、图鉴负 ID/解锁、地图/marker、像素映射完整 TXCI 306 组、PLR 全编辑/头像/装备/人物原图帧。在线账号/云存档仍按在线能力处理。
9. 内存登记接入 `registerRuntimeMemory/assertRuntimeMemoryAvailable`；只保留小索引和当前分片/纹理，安装 ZIP/gzip 工作区串行释放，内存回收不得删安装文件或用户编辑。
10. 主代理运行适当单元/合同/集成测试与 `npm run validate`、H5/微信生产构建、包体检查；修订旧“必须存在 choices/walk/local-resource”的测试为新缺包/冷启动合同，不能直接删测试绕过。记录瘦身前后主包/子包、公共传输、安装落盘与峰值内存分别多少。

上述依赖与验收合同已用于实施。H5冷离线壳和人物源图本地组合已完成实际浏览器验证；特殊效果按用户确认明确标注近似。尚未执行微信实体手机整包断网验收和生产部署/首次推送，具体证据见交付记录。
