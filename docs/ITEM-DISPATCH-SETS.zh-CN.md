# Item 分派 bool 集合：有限静态证据

`resource_pipeline.item_dispatch_sets.extract_item_dispatch_sets(data, limits=..., checkpoint=...)`
只读取 PE/CLI 元数据、IL 和 FieldRVA 原始字节，不加载程序集、不执行游戏代码，也不进入现有 producer。
该模块复用 `item_texture_aliases` 的有界元数据、IL 形状和核心库身份检查工具。

## 证明范围

目标是 `ItemID.Sets` 的 `IsFood`、`Deprecated`、`IsDrill`、`IsChainsaw` 四个初始化配方。
支持的形状必须精确匹配，未知重载、控制流、签名或副作用保持 unsupported。

- 用完整签名和 MethodDef 绑定两个 `CreateBoolSet` 重载。单参数重载必须传入 `false`；双参数重载先覆盖返回缓冲区全部元素，再按字面量 ID 顺序写入默认值的补值。重复 ID 是重复赋值，不是切换已有布尔值
- 检查 `GetBoolBuffer` 的闭合分配形状，或带唯一小型 finally 区的 Monitor/Queue 形状。验证 `Boolean`、`Queue<bool[]>`、Monitor 方法的核心库引用身份；不会假定缓存一定为空
- 从 `Count` 的唯一直接整数写入记录声明域，绑定 Sets 初始化器开头的 Factory 构造及唯一存储；不把声明域误当作任意运行时读取时的 Count 值
- 对每个目标检查唯一直接写入、无目标字段地址逃逸、整个初始化器无分支和唯一末尾返回；局部片段必须是 Factory、整数长度、`newarr int`、`dup`、FieldRVA token、已验证 `InitializeArray`、已验证 bool 重载、目标存储
- FieldRVA 必须唯一，字段类型必须是以核心 `ValueType` 为基类且无字段/方法的显式布局类型，唯一 ClassLayout 的 packing=1 且字节数精确等于元素数×4。保留数据 RVA、文件偏移、元数据偏移和 SHA256；逐项按 CLI 小端 int32 解码并检查 `[0, Count)` 域
- RVA 类型不得为接口、抽象类型或带泛型参数的类型。可选消费者的形状不支持可以单列，但任何工作预算超限必须撤回全部集合证据，不能转成成功的局部结果
- 保留重复项和原始顺序，同时输出排序去重的覆盖 ID。可识别显式 true/false 默认值，不把“覆盖 ID”一律称为 true 集合
- 尽可能从实际 `Item.SetDefaults` 提取 `集合[this.type]` 的五指令读/分支绑定；仅报告方法 token、读位置和分支目标，不声称证明分派、执行路径或最终字段

## 条件与阻塞

成功状态是 `PROVEN_CONDITIONAL_INITIALIZER_RECIPES`，每个集合是 `PROVEN_INITIALIZER_RECIPE`。
这证明对应调用正常返回时的数组填充规则及其字面量输入。要在声明域内使用它，仍须证明返回 bool 缓冲区足够长；返回长度、后续 Factory 缓存状态和整个初始化器的残余调用副作用不在此模块的闭合证明内。`factory.freshConstructor` 可独立证明新实例构造正常返回时的状态，详见 [构造器证明](SET-FACTORY-CONSTRUCTOR.zh-CN.md)，不能外推为随后集合调用的缓存状态。

`residualEffects.unmodeledCalls` 逐个记录未建模的 Count/Sets/Factory 构造调用，含 caller/target token、首个 IL 偏移及次数。这些调用一律标为 `UNSUPPORTED_EFFECT_SUMMARY`。它们不会因为在别处、没有直接写目标字段、或名字看似无害而被视作纯函数。

因此两个快照层次均明确不可用：

- `cctorNormalReturnSnapshotUsable=false`：初始化语句处的局部规则不等于整个类型初始化器正常返回时的全局表
- `runtimeSnapshotUsable=false`：静态数组内容和字段引用可在运行时改变；本模块不排除后续修改

输出始终 `complete=false`、`finalItemDefaults=false`、`publishable=false`、`executedInput=false`。
不能把该条件证据直接作为完整 Item defaults，不能绕过发布门禁。它是后续实际 SetDefaults 分派证明的前置材料。

## 资源边界与测试

不可变 bytes 输入受文件大小上限约束；输入哈希由实际字节计算。限制包含每方法/累计 IL 字节、累计解码指令、元数据/形状检查步数、字面量个数/字节、声明域、累计证据构造大小、最终 JSON 字节及墙钟。取消回调在解析、解码、遍历和证据计算过程中持续执行，原始取消异常向调用方传播。失败结果不保留可用集合；未知版本不会回退到已知游戏表。

`tests/dispatch_fixture.py` 生成原创微型 PE/CLI 数据，使用自定义小域与数组，公开测试不带游戏程序集、真实集合或反编译源码。测试覆盖重载、重复项、true 默认、消费者绑定、未知副作用、错误 IL/核心库/FieldRVA/域、字段地址、资源限制、取消与超时。真实输入的精确表、完整证据、源码逐项比较和冻结清单应只保存在私有审计目录。
