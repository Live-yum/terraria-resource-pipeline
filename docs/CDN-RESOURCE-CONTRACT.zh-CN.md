# CDN 资源合同 v1

这是 `internal/pipeline` 当前实际输出的合同。旧 Python 演示合同不参与发布。资源版本与 WASM 解析器版本分开；新游戏修改存档格式或绘制算法时，仍可能需要更新程序。

## 目录与完整性

```text
channels/stable.json                  可变指针 {"manifestSha256":"<64位小写SHA256>"}
releases/<manifestSha256>.json         不可变 manifest 原始字节
objects/<sha256前2位>/<sha256>.*       不可变公共对象
```

manifest 的 SHA-256 是 release ID；客户端先验证原始字节再解析。manifest 不包含自身哈希、时间戳、临时路径或内存测量。`schema=1`；`extractor` 是工具版本，`gameVersion` 来自真实游戏程序集。`sources.serverSha256` 与 `sources.contentTreeSha256` 绑定实际源内容，ZIP 打包时间和目录顺序不影响资源身份。完整 ZIP 的哈希和未消费文件清单只保存在私有报告。

对象引用含 `path/sha256/bytes/mediaType`，gzip 另有 `encoding="gzip"/decodedBytes`。SHA-256 针对下载的压缩字节；解压后必须核对长度和格式。所有对象路径须满足内容寻址规范，不能接受绝对路径、`..`、链接或任意 URL。渠道指针最多 4 KiB、manifest 最多 2 MiB、单对象及解压内容最多 128 MiB；图像包的压缩及解压 ZIP 均最多 16 MiB。

发布白名单只有 manifest 引用的 metadata、RGB 和图像包，以及 release/channel 文件。上传 ZIP、游戏程序集、原始 XNB、私有 NDJSON、日志、审核文件和报告均不发布。

## 数据与图片

`families` 为共享语义域，各族由 `{rows, object, first?, last?, key?}` 分片引用组成。一般族按 256 行分片，物品详情按 256 个数值 ID 固定分片；`item-index` 是完整共享轻目录，名称、槽位、研究和纹理引用集中在此。客户端不能把 256 当作所有对象的硬上限，应核对 `rows`、压缩及解压大小。名称/研究/基础与默认 UI tooltip 不作为另一个重复物品族发布。详情保留原生 gameplay 字段；`item-field-schema` 解释实际反射字段。读取 JSON 必须保留大整数的精度，不能把全部整数转成浮点数。

其余族包括 ID、语言、前缀、Buff、图鉴、地图 lookup/palette、TileObjectData、tile/wall/armor sets、油漆、NPC 帧、人物布局/纹理绑定、坐骑、染料等。不能用数组序号代替记录的 `id`，NPC 含负 ID。游戏原始 ID 域与地图布局在 `domains/mapLayout`，禁止继续使用旧游戏版本的编译常量。

`textures` 是 gzip JSON 对象：逻辑键 → `{width,height,object}`。保留来源实际大小写，消费者使用 `texture-references` 的真实绑定；物品可能复用其他物品图片，不能假定 `Item_<id>`。同一 PNG 哈希只存一份。

新候选通过 `textureScope={mode:"consumer-closure",sourceCount,selectedCount}` 标明完整源清单与实际公开消费集合。`sources.textureFiles` 仍是输入源数，不能改写成筛选后的数；公开 textures 数必须等于 selectedCount。公开 `player-texture-bindings` 提供游戏字段/索引→assetId，`capabilities.publicTextureClosure.available=true` 仅在实际上传 DLL 的潜在依赖与绑定门禁全部闭合时成立。诊断/扫描轨迹不公开；旧无 textureScope 候选仍要求完整源数与公开数相等。

`imageBundles` 按 PNG 哈希前两位分桶。一个 PNG 的下载包为 `imageBundles[png.sha256.slice(0,2)]`；ZIP 成员名就是 PNG 对象路径 `objects/xx/<sha>.png`。内部 ZIP 使用 Store、固定排序和零时间；新传输对象为标准 `.zip.gz`，mediaType=application/zip、encoding=gzip、decodedBytes=完整 ZIP 长度。gzip 用于消除重复目录/文件名开销，PNG 字节不变；消费者先验证压缩哈希再有界解压，再核对每个 ZIP 成员的路径、大小、CRC、PNG 尺寸及 SHA-256，拒绝多余/缺少/重复成员及路径逃逸。旧 raw `.zip` 仍可验证。公共资源目录不重复发布裸 PNG；解包后的 PNG 进入共享哈希目录，删除已完成安装的传输包。

`rgb` 保留 compact candidates、stableCandidates、SRGB、TXCI 四种用途。candidate 每行顺序为 `[kind,type,variant,paint,r,g,b,flags]`，`kind=0` tile、`1` wall。SRGB 结果索引指向 **stableCandidates 的紧凑顺序**。TXCI v3 保存同色组内全部材料，不能截断为 256；1.4.5.8 实际最大组有 306 项。

官方人物绘制样本 `player-draw-plans` 保留在私有提取目录，用于核对客户端合成算法，不作为公共客户端下载数据；不发布由其拆分的 `player-draw-operations`。样本覆盖统计和限制仍在 manifest 能力声明中，不能将有限样本当作任意人物组合的完整算法。

## 审核和发布

`trp snapshot --source-url <后端完整/file端点> --output <新目录>` 获取已发布频道、清单和引用对象，逐个验证压缩字节哈希。这是后台快照接口；端点不含查询参数，CLI 使用 `path` 参数请求白名单相对路径。只有真实 404 是首发；频道原始字节 SHA 是审批基线身份。

`trp review --candidate ... --baseline ... --output <新目录>/review.json` 生成不可覆盖的 schema 2 摘要与完整 `review-changes.ndjson`。摘要绑定 `candidateManifestSha256`、`baselineManifestSha256`（首发空）、`baselineChannelSha256`（首发 `absent`）及明细 SHA/长度/记录数。确认提交审核文件原始字节 `reviewSha256`，旧 schema 1 Git 审批失效。

`trp prepare-publication` 重算审核、验证候选与完整明细，输出确定性 objects/release/channel allowlist，不执行远端写入。viewer-boot 复用已有 AList 连接，文件流上传 `/WeChat/resources`，等待任务并读回验证；仅最后更新 `channels/stable.json`。

微信客户端匿名请求 `/admin-api/viewer/resources/download-link?path=<白名单相对路径>`，响应为 `{code:0,data:{url:"可信HTTPS签名直链"}}`；此接口只查AList元数据，不读取文件正文。客户端不带AList凭据，直接从返回的链接读取原始压缩字节，仍校验大小、SHA和有界解压。签名地址失效时重新取链接，不作为缓存键或release身份持久保存。

真实百度直链无法通过H5浏览器CORS检查，用户明确选择H5固定使用`/admin-api/viewer/resources/file?path=...`字节代理；该接口也供后台快照/校验。微信不自动回退/file，H5也不先尝试直链再失败回退。两平台资源版本、缓存键、hash和离线合同相同。禁止HTTP自动解码使压缩字节SHA失配、凭据泄漏和私人路径访问；微信需配置API和实际直链主机的合法域名。

后端持久发布日志记录目标、审批、基线、阶段与 AList 任务；请求结果未知时保持阻塞，不能以一次读回旧频道推断任务失败。单个受管 publisher 使用共享 working-directory 锁；远端频道被其它程序改动会使原审批过期。源码与提取容器无需 Git 或 AList 凭据，原图片 GitHub 仓库已恢复旧版，不再参与此流程。

## 全量离线安装

“我的 → 更新资源”下载该 manifest 的 **全部**公共对象及所有图片，不能只把打开过的页面算作完整安装。按哈希复用已有文件；逐包解开，避免同时持有全部图像或全部族。完成全部校验后写持久 `installed` 清单，最后原子替换 active 指针；指针与版本清单须存放在专门的受保护资源目录，普通清缓存不得删除。断网、空间不足、下载中断、hash/格式不符均保留原 active 版本，重试复用已经验证的文件。

离线集不得进入普通图片 LRU。切换后才可清理不再被 active/待安装版本引用的对象；退出应用再打开也要从持久指针恢复。按需读取分片和解压保持运行内存可控。各资源操作须等待同一 release 的所需分片就绪，页面基础结构和本地存档操作不等待全量资源，避免新名称配旧图片。完整安装后，本地存档解析/编辑、预览、地图、像素画等本地功能不发起必要网络请求；登录、云存档及分享等服务仍按原有在线协议工作。
