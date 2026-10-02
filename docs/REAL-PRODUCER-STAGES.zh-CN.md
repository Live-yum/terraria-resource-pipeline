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
