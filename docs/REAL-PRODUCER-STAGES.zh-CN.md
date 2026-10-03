# 真实双包生产器阶段

`POST /api/raw-jobs` 现在把安全展开的服务端/Content 原始目录交给内置
`RawEvidenceProducer`，没有运行 EXE/DLL，没有使用旧小程序表格作为新版本结果。
静态 PE/CLI 解析器读取程序集、ID 常量与嵌入语言 JSON，私有作业保留偏移、哈希和字段证据。
已有沙箱 XNB 解码回执按原始文件名归入物品、方块、墙、NPC、Buff、人物和头发图片证据。
每个作业生成 `adapter-evidence/version-adapter-manifest.json` 与分族记录。

这是已接入的真实数据阶段，不是完整语义适配器。程序集自述版本仍为未独立认证的版本证据。
默认属性、动态说明、前缀兼容、油漆变换、帧/图层映射与像素候选/RGB仍逐项列为缺口；
图像缺少同编号文件不自动当作别名。所有族目前 incomplete，作业仍 BLOCKED、不可发布。
后续必须把每个缺口补为有同版本来源证据的生产规则和回归，再生成可审核候选。
不能删除此门禁，或用上传者自报完整、旧内置数据、合成 fixture 代替。

原 admin/boot 对接接口：
- `GET /api/jobs/{id}?compact=true`：压缩清单数组为计数，供轮询
- `GET /api/raw-jobs/{id}/review`：真实作业的状态、逐族缺口、差异及 reviewable
- `POST /api/raw-jobs/{id}/publish`：审核摘要/确认检查；真实发布身份未配置时明确拒绝

新版纹理预检支持完整解压长度之后精确五零字节的 XNA 终止标记；非零尾部及额外尾字节仍拒绝。
失败仅暴露白名单阶段、异常类型和退出码，不输出源字节或私有路径。

## 客户端静态元数据

生产器分别枚举服务端目录的 `TerrariaServer.exe` 与客户端目录的 `Terraria.exe`
（包含 `Content/Terraria.exe`）。每种至多四个程序集；读取前核对实际 SHA-256，
客户端同时核对字节数，结束时重验全部源目录。Git LFS 指针不是 PE，不能替代实际文件。
程序集与证据输出均有大小限制，只读取字节，从不加载或执行游戏程序集。

`clientMetadata` 指向私有 `client-N.json`，包括 `sourceRole=client`、实际输入哈希/大小、
CLI Assembly 版本和文件偏移、ID 常量及逐资源原始本地化文档。保留资源哈希、偏移和
属性序号，不重复膨胀每个字符串的投影。客户端不调用服务端 Item 默认值、研究模型或
本地化加载器规则；原始文档的诊断投影不代表运行时回退、复制或插值行为已验证。

`serverClientComparisons` 比较程序集声明版本、ID 数值集合（不含 Count）和同名语言资源
哈希，始终为 `INFORMATIONAL_ONLY` / `trusted=false`。声明版本不一致新增
`SERVER_CLIENT_DECLARED_VERSION_MISMATCH` 阻塞；一致也不能认证来源、平台或安装目录。
客户端证据不会被合并进服务端的语义覆盖计数。

没有新建上传来源声明或可信 Git/LFS 固定值。原有操作员归档 SHA-256 固定值预检、
双源校验和组合包纹理来源门禁不变；即使同版本，作业仍 BLOCKED、complete=false、
不可审核发布。客户端 EXE、资源和私有原始证据不得提交到代码仓库或 CI artifact。
