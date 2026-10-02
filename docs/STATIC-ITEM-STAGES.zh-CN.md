# Item SetDefaultsN 静态数值阶段（抽样，不是最终默认值）

这一阶段直接读取原始服务器 PE/CLI 中 `Terraria.Item.SetDefaults1` 至 `SetDefaults5` 的 IL 字节。实现是原创的有界抽象解释器：不启动 CLR、不 JIT、不 `Assembly.Load`、不反射调用游戏方法、不执行游戏 EXE/DLL、不调用 native code。IL 只作为输入数据，支持的栈/整数/字段变换由封闭规则计算。

## 结果边界

`extract_server_semantics(..., item_stage_ids=None)` 增加私有 `itemDefaultStages` 证据。默认从已观测的正整数 ItemID 字面量域选择 32 个 ID，包含域中最前的 3 个及横跨域的确定性分位点；没有足够 ID 时全部选择。每个 ID 单独分析五个方法入口，不依据外部源码的数值范围替服务器二进制决定 dispatcher。

`selection` 列出 exact sampled IDs、观测域大小、remaining IDs/count。remaining 明确为 `NOT_ANALYZED_IN_THIS_BATCH`，不是 unsupported，也不是完成。显式 `item_stage_ids` 可选择同一观测域内最多 256 个 ID；这一接口供后续自动分批使用，不要求用户手写 ID。预算中断另列未尝试的所选 ID 和 stage 对数。

- 走到 `ret` 的已知数值写入：`PROVEN_NUMERIC_STAGE_WRITES` 与 `fields`
- 未发生可证明数值写入的返回路径：`NO_PROVEN_NUMERIC_WRITES`
- 未知控制流、调用或限制：明确状态与 `diagnostic`，`fields` 为空；停点以前的事实只放入 `prefixFields`
- 未知值覆盖一个字段后，先前该字段的已知值会撤销；不会当成最终已知字段
- 非数值字段列入 `excludedFields`，不伪造成数值或补缺省值

所有记录始终 `complete=false`、`finalItemDefaults=false`。没有执行 ResetStats；初始实例字段未知，只有方法参数 ID 已知。没有解决 food gate、variant、static sets、完整 SetDefaults 后处理、动态 tooltip 等。即使某段数值分析走完，也不能称为完整 Item 默认值。

## 当前支持

- 完整界定已知 CIL opcode 的 operand 宽度及指令边界；branch/switch 目标必须指向有效指令起点
- 常量、参数、数值 locals、栈 dup/pop、明确的整数运算/比较/转换、已知条件分支及 switch
- 本对象及静态 metadata 证明的基类数值实例字段；bool、整数、单精度/双精度字面量存储
- 同 `Terraria.Item` 类型、非 virtual/native、参数/返回类型在封闭数值集合内、路径完全落入支持集合的辅助方法
- helper 由它自己的原始 IL 分析，不按函数名字替换硬编码游戏公式；每次字段写入带依赖 method token

不支持的指令可以存在于未走访 switch 分支，但仍须能安全确定指令边界。对走访到的 unsupported 指令立即停下，不猜测堆栈变化。

## 显式 unsupported

- 外部/泛型/virtual/间接调用、构造器、native/PInvoke、异常区
- 任意未知条件分支、重复走访指令的 loop、递归调用
- 初始未知实例状态和静态状态的动态求值
- 非数值 helper 参数（包括尚未证明底层类型的 enum）、数组/对象/地址操作
- 浮点运算与部分高精度转换；不假设不同 CLR/CPU 的中间浮点精度相同
- native-int 运算、溢出变体、其他未列入支持集合的 CIL 语义
- Item 或接受的基类采用 explicit layout：重叠字段无法作为独立存储证明，明确 `UNSUPPORTED_EXPLICIT_FIELD_LAYOUT`

上述字段结论是正常返回条件下的数值数据流事实；`unresolvedStaticReads` 保留静态读取 token，静态初始化、异常及其他运行时副作用没有求值，不将它们默认为已验证。本模块不是完整 CIL verifier，也不认证程序集合法、安全或游戏版本可信。任何跨阶段合成、版本授权和发布仍由外层受信策略控制。

## 证据

字段记录包含 Field token、字段 metadata 文件偏移、赋值 Method token、IL 相对偏移与文件绝对偏移、原始 IL SHA-256、调用链和依赖方法集合。method 清单保存 signature/IL hash、metadata/body/code 偏移和原始 code 长度。顶层结果绑定完整输入 SHA-256。

固定 `Item.cs` 参考为 `Live-yum/TerrariaDecompiledSource@8255d34616c780af12079425ac92a0a7aed87d71`，Git blob `d9459a969d89143b5ef26695e5b618dd85086281`。源码用于理解未解决阶段，不替代实际 IL 字节、dispatcher 或最后默认值证明。编码依据 [ECMA-335 II/III](https://ecma-international.org/wp-content/uploads/ECMA-335_6th_edition_june_2012.pdf)。

## 预算与取消

默认 32 个 ID、最多 256 个显式 ID；单方法最多 1 MiB / 250,000 条 decoded instructions；全部缓存最多 8 MiB / 1,000,000 条指令；最多 256 个方法、1,024 个实例字段、256 locals/stack、8 层调用；每个方法路径最多 20,000 步、整个 batch 最多 2,000,000 步、30 秒。任何预算耗尽都是明确 partial/unsupported 结果，剩余工作不冒充完成。

元数据索引有独立限制：最多 4,096 个 Item method 索引，字段和方法名称合计 1 MiB。字段数量在插入记录前检查，method 数量在遍历并建立索引前检查；名称字节在插入前计入预算。超过限制分别报告 `FIELD_COUNT_LIMIT`、`METHOD_INDEX_COUNT_LIMIT` 或 `METADATA_NAME_BYTE_LIMIT`，不会先分配整张超限索引后才拒绝。

整个 IL batch 共用 16 MiB 证据预算，与单个资源的 16 MiB 上限一致。计数包含 method 证据、字段名、依赖 token/调用链、静态读取、prefix/excluded 记录及最终汇总，并预留诊断空间。被覆盖或替换的记录也不退还预算，因此限制同时约束累计构建工作。达到预算立即停止为 `PARTIAL` / `TOTAL_EVIDENCE_BYTE_LIMIT`；保留已计费的事实和明确的中断位置，列出未分析的 ID/stage 对，不静默删减字段。

服务器完整原始证据的 API 出口另有 64 MiB UTF-8 canonical JSON 上限，与累计解压资源上限一致。使用字符串分块（每块最多 4,096 字符）的精确 JSON 长度预检；在成功检查前不构造完整 JSON 字符串或字节副本。CLI 写入和私有 CI 回执也在序列化前检查。超限明确拒绝 `EVIDENCE_JSON_BYTE_LIMIT`，不写入截断结果或覆盖既有证据。IL 的选择列表自身超限也明确拒绝，不能省略 required sampled/remaining IDs 以冒充完整回执。

外层 `checkpoint` 在 metadata 解析、IL decode、每一步抽象变换中继续执行，因此整个上传任务的截止/取消仍可先终止本阶段。纯合成测试涵盖取消、循环、递归、时间/步数、截断和非法 branch 目标，不运行任何原始游戏代码。

`scripts/ci_server_semantics.py` 的私有回执只保留计数、ID/数值样本、字段名哈希、token/offset/hash；不上传原始 IL、服务器或游戏文本。新增阶段必须另做固定服务器实际验收；已有 ID/语言证明不能代称此阶段已经实际通过。
