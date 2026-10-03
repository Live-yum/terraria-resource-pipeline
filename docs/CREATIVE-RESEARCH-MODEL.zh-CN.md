# 创造模式研究数量：静态条件模型

`resource_pipeline.research_semantics` 是独立的只读、仅数据语义模块，尚未接入生产器、网页旧格式数据或 CDN 发布。它保留一份研究定义表 `baseCounts` 和一份持久 ID 覆盖表 `persistentIdOverrides`，不会为每个消费者复制整张表。

该模型的 `researchModelComplete=true` 只表示下述固定输入、初始状态与文化假设下的研究规则已完成提取。顶层 `complete=false`、`publishable=false`、`executedInput=false` 始终保留；没有执行 EXE、DLL、CLR、反射或 LINQ，也没有完成全部物品默认属性或创造菜单定义。

## 固定版本与来源

当前仅支持 `windows-server-research-v1`，平台参数必须为 `windows`，完整输入 SHA-256 必须为：

`d87e3faf08637f6be8882c63e7f11fb7e792b0230006309618473ece0f863e1e`

其他二进制或平台返回 `UNSUPPORTED_PROFILE`，不返回研究表。来源参考是 `Live-yum/TerrariaDecompiledSource` 提交 `8255d34616c780af12079425ac92a0a7aed87d71`：

- `Terraria.GameContent.Creative/CreativeItemSacrificesCatalog.cs`，Git blob `92588219aee5537119650f416dea615495114add`
- `Terraria.ID/ContentSamples.cs`，Git blob `728464f8e9bcf6ee2f617851dab32a4a43a62188`

源码只是行为参考。模块还验证二进制中的字段、签名、调用关系、控制流形状、分支路径和数组初始化。整个文件的散列用来限制适用范围，不能单独证明算法等价。

## 明确的条件

- 新建且清空的研究目录，覆盖表处于新建状态，并已应用 `FillResearchItemOverrides`
- 采用显式 `culture='invariant'` 场景，对 ASCII 类别使用与 invariant/en-US 一致的 A–Z 转 a–z 映射；这不表示二进制调用了 `ToLowerInvariant`
- 不模拟修改后的运行时字典、任意程序集、文化切换或玩家研究进度

实际 TSV 以大写类别为主，包含 `I`。二进制调用的是文化相关的 `String.ToLower()`；土耳其语等文化的结果不能用普通 ASCII 小写代替。当前模块没有证明服务器启动线程文化，因此状态为 `EXTRACTED_CONDITIONAL_MODEL`。选择输出语言并不证明执行线程文化。非 invariant 参数明确拒绝，不静默回退。

## 二进制与名称证据

`ItemID` 静态构造器必须完整符合：先将立即整数写入公开静态只读 short `Count`，再调用 `IdDictionary.Create<ItemID, short>()`，最后存储 `Search`。模型不从最大 ID 推断 Count。

嵌入的 ReLogic 依赖固定于 SHA-256 `e1c5dccefff5fd1c789ff712babfa1a305fced0d03c96ef30f2c14d99aa0af29`。其反射/LINQ 路径是固定版本的人工审计契约，输出记录相关方法散列、偏移和具体控制流；这不是通用 CLR 反射解释器。审计规则为：

1. 读取公开的 `Count` 字段；若存在但为零则抛错
2. 使用 `Public | Static` 枚举，保留字段类型恰好等于 short 的字段
3. 仅加入转换为 Int32 后小于 Count 的值，允许负数
4. 使用原始 `FieldInfo.Name` 和默认字符串比较器；区分大小写，不解析数字字符串，不裁剪名称
5. 名称字典调用 `Add`，反向字典也要求唯一 ID

固定输入中，除已在静态构造器中绑定的 Count 外，所有符合条件的 short 字段都是元数据字面量；模型逐个验证并记录字段与值偏移。所有纳入的名称和 ID 唯一，所以不依赖反射枚举顺序。

`Initialize` 的前后控制流验证覆盖：清空表、嵌入资源名、CRLF/CR/LF 拆行、行首注释、tab 分列、至少三列、名称查询成功门禁、ToLower 调用、排除标记、字典赋值和行循环。类别分派采用封闭的标量与分支解释，验证 FNV 辅助方法，并记录每个支持类别的实际 IL 路径；未知类别通向抛错块。该逻辑不调用输入方法。

## 行、覆盖与查询规则

- 只跳过以 `//` 开头的行；前导空白不裁剪
- 不足三列或名称不存在的行被忽略；多余列不参与计算
- 空类别与 `a` 为 50；`b/c/d` 为 25/5/1；`e` 跳过；`f/g/h/i/j/k/l/m/n/o` 为 2/3/10/15/30/99/100/200/20/400
- 未知的已识别物品类别抛错；它不是默认 50
- 同一 ID 后出现的有效赋值覆盖之前的值；后出现的 `e` 不删除先前值
- 覆盖初始化只接受封闭的直线 int32 数组语法。内联数组必须按顺序完全填充；RVA 数组需要精确大小的字段布局与数据范围。无法识别的调用、分支、尾随指令或不完整数组均拒绝
- 辅助方法按数组顺序执行 `overrides[source] = target`；后写入生效
- 查询只应用一次覆盖，然后查询基础定义。覆盖链与环不递归展开

`lookup_research_count` 同时返回 `itemId`、`definitionId`、`available`、`count`、`overridden` 与 `hasOwnDefinition`。消费者能够查到研究数量，不表示消费者自己的 ID 是可显示的研究定义。模块不据此推断创造菜单、可见性、解锁状态或其他物品属性。

## 私有验收与输出

固定样本的独立源码对照得到：6,244 个精确字面量名称，6,242 条已识别 TSV 行，6,241 个已识别 ID，6,138 个最终基础定义，10 个覆盖调用，16 对覆盖；全部逐值比较无差异。源表保留一次重复行，103 条排除行，6,139 次有效赋值。

共享表的规范 JSON SHA-256：

- 基础定义：`bc1431adf6e22221773381ffb494d4ab51c98ecece522086fdf1e29abce37716`
- 覆盖表：`bed33416e7f46df85e21d7a0b0d5945fc671bf3e872881baa29a5cc75451d1f5`

完整模型含真实字段名称、ID、数量、数组与偏移，只在私有审计目录生成。`research_summary(model)` 采用显式白名单，只返回状态、条件、来源身份、统计计数与散列，不带基础表、名称证据或覆盖映射。摘要不是公开发布授权。

公共测试全部是原创合成输入：覆盖三种换行、注释与列边界、精确名称、负数与别名、重复赋值与排除、所有类别、文化拒绝、未知类别、字节/行/名称/证据预算、内联和 RVA 数组、错误调用和不完整数组、单跳链与环、定义与查询可用性的区别、平台拒绝及摘要脱敏。

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_research_semantics.py' -v
```


## 入口预算与摘要边界

文件入口在读取之前建立截止时间；分块读取、元数据解析、类型枚举、模型验证与返回前检查共享这一截止时间。复用受限普通文件读取器，拒绝符号链接和非普通文件，并检测读取期间的修改。独立目录解析与覆盖初始化器入口也检查其墙钟预算。

不支持的 profile 结果、独立覆盖初始化器结果和完整模型都必须通过 `evidence_bytes` 检查；连诊断信封都放不下时抛出预算错误，不返回超限结果。摘要对来源对象、统计计数及表散列使用嵌套白名单；计数必须是非负整数，散列必须是 SHA-256 十六进制字符串。这是已验证模型的摘要接口，不是任意输入文本的通用脱敏器。
