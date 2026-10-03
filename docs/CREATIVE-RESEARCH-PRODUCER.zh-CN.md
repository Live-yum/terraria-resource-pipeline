# 创造研究模型接入真实生产器

`RawEvidenceProducer` 为每个安全清单中的 `TerrariaServer.exe` 生成独立的
`adapter-evidence/server-N-research.json`。原始定义表、逐行来源、持久 ID 覆盖对和
方法证明只保存在私有证据文件，不进入任务回执、列表、轮询或审核响应。

`version-adapter-manifest.json` 的 `researchEvidence` 将输入 SHA-256、证据文件路径、
证据文件 SHA-256 与明确白名单的摘要绑定；摘要仅包含范围、条件、计数和哈希。
`familyCoverage.items.researchSubcapabilities` 通过同一来源和证据哈希引用该结果，
能力名为 `creativeResearchConditionalModel`。`researchModelComplete` 只表示该局部模型
在所列条件下完成；族本身仍 `complete=false`。

只有[已审查模型](CREATIVE-RESEARCH-MODEL.zh-CN.md)中精确的 Windows 输入哈希可以
调用已安装的研究提取器，平台由该哈希绑定，绝不由目录名、扩展名或上传声明推断。
显式采用 `culture=invariant` 的 ASCII 类别模型，`runtimeCultureVerified=false`：
运行时调用 `ToLower`，启动线程的实际区域设置仍未核实。其他哈希生成
`UNSUPPORTED_PROFILE`、`input.platform=unknown`，不产生研究表；即使通用静态检查器
拒绝输入，也保留此明确的不支持结果。合法检查器的默认语言选择与自定义调用签名不变。

同一次外层取消/超时检查贯穿研究提取、序列化和最终来源复核；模型自身的时限不重启
上传任务时限。私有研究证据限 8 MiB，摘要另限 64 KiB，服务器输入仍最多四个且受原有
文件/清单限制。来源 SHA 或长度不符、提取失败或预算超限都阻止生成适配清单。
所有来源树仍在最终写清单前重新散列；失败遗留的私有证据不构成成功回执。

新增原创合成集成测试覆盖默认生产器、安装帮助器替身、HTTP 上传和各类回执、多个输入、
未知配置、来源更改、取消/超时、错误脱敏和输出预算。真实二进制只可在私有验证中作为
数据读取，不运行 EXE、DLL、CLR 或游戏初始化器，也不把真实资源放入测试或公开输出。

物品 defaults、dynamicTooltips 等缺口、完整族语义、双来源版本与发布门禁均保持原样。
此接入不会生成可发布完整物品集，原始上传任务仍 BLOCKED、extractionComplete=false、
publishable=false。
