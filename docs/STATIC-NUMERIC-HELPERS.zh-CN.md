# 已验证枚举参数与同程序集纯整数辅助方法

本增量仅扩展 `SetDefaults1`–`SetDefaults5` 的有界数值数据流分析。它读取 PE/CLI 元数据和 IL 字节作为数据，不启动 CLR、不加载程序集、不调用游戏代码。结果继续为 `stage-only`、`complete=false`、`finalItemDefaults=false`。正常返回的阶段写入不能替代 `ResetStats`、food/variant 分派、静态初始化或外层 `SetDefaults` 后处理。

## 枚举类型的证明条件

只在方法参数、返回值或局部变量的直接 `ELEMENT_TYPE_VALUETYPE` 签名引用本程序集 TypeDef 时尝试解析。任意 struct、TypeSpec、泛型参数、引用类型和外部枚举都不会自动转为整数。

接受条件全部成立时才使用底层整数语义：

- sealed、非 interface、非嵌套、非泛型，采用自动布局且不声明方法
- 直接继承 `System.Enum` TypeRef；ResolutionScope 必须是外部 AssemblyRef，名称和公钥 token 必须匹配受限 corelib 集合（mscorlib、System.Private.CoreLib、System.Runtime）
- 恰好一个实例字段，名称 `value__`，字段 flags 为 public/specialname/rtspecialname，签名恰好为一个合法整数类型
- 底层类型仅 `i1/u1/i2/u2/i4/u4/i8/u8`；bool、char、native int/uint、浮点、对象、指针及复合类型明确拒绝
- 其他字段只能是该枚举自身类型的 public/static/literal/has-default 常量，字段名称不得重复
- 不存在相关 FieldLayout 或 ClassLayout 记录，也没有额外实例存储

本地定义的同名 `System.Enum`、错误 AssemblyRef scope、错误公钥 token 或普通值类型都不满足条件。这是元数据形状和引用身份检查，不是程序集签名认证或完整 CLI 合法性验证。枚举常量值不参与辅助方法计算。

枚举参数沿用既有 CIL 栈与存储转换规则：小整数在进入数值计算前按其位宽截断并做相应符号扩展；i8/u8 必须来自 i8 栈值。证据保存枚举 TypeDef、基类 TypeRef、corelib AssemblyRef、底层字段的 token/偏移及签名 SHA-256。

## 同程序集纯整数方法的证明条件

此前仅解析 Item 所属的方法。新增路线按实际 call 指令的 MethodDef token，惰性定位同一程序集中的唯一声明类型；不解析 MemberRef、MethodSpec、外部程序集或任意名称匹配的替代实现。

新路线只接受非嵌套、非 interface、非泛型声明类型中的普通 static 方法：方法自身也不能带泛型参数。参数和返回值必须是裸 primitive 整数签名，不接受 void、浮点、引用、byref、修饰符或枚举别名。禁止 virtual、P/Invoke、native/runtime/internal-call、同步及其他未支持的 MethodDef/Impl flags；要求有效 IL RVA，不接受异常区域。

解码以后，检查整个方法的每一条指令，包括没有走到的分支。只允许参数/整数常量/数值局部变量、封闭的整数运算与转换、栈操作、分支、switch 和 return。不允许字段或静态字段读写、字符串/对象/数组/地址操作、浮点指令、call/calli/callvirt/jmp、构造器或其他 opcode。方法名字不决定语义；计算来自其实际 IL。已知路径随后仍受原解释器的栈、类型、分支、循环与返回规则检查。

原 Item 方法的既有支持范围没有改为“整个函数纯整数”要求；其当前已访问路径仍是独立的阶段证据。新路线的更严格 gate 用于证明额外类型中的辅助方法没有字段或调用依赖。

## 资源限制和失败结果

所有默认限额保持不变：单 batch 2,000,000 步、16 MiB 累计证据、30 秒；单路径、方法数、IL 字节/指令、栈、locals、调用深度与总预算仍共用原实例。新增的元数据扫描累计受既有 4,096 索引额度约束；惰性加入的辅助方法也计入方法索引和方法缓存上限。枚举检查的所有字段计入 1,024 字段额度，相关名称计入既有 1 MiB 名称预算。

辅助方法和枚举证明都有原始 token/offset/hash 证据并计入累计证据预算。失败的方法缓存其明确的拒绝结果，避免每个 ID 反复解码。任何失败不会把前缀写入冒充正常返回事实；`fields` 保持空，已知前缀仅保留在 `prefixFields`。超限、取消和未知路径仍明确报告，不自动放宽预算。

## 验证范围

新增测试只使用原创合成 PE/CLI、数值和方法体，不包含游戏原始 IL、游戏字符串或游戏数据。测试覆盖各整数宽度边界、假枚举、额外/重叠存储、泛型声明类型和方法、错误签名/flags/RVA/异常区、未访问分支中的副作用或调用、恶意或截断元数据，以及方法、元数据、名称、字节、指令、步数、调用深度、证据、时间和取消限制。

完整正 ID 域验收是独立的只读诊断：每批 64 个 ID，保留每个 ID 的五个阶段状态，并在 300 秒总截止内运行，所有批次仍使用默认限额。这不会把 6,195 个 ID 接入生产构建，也不会把完整阶段覆盖宣称为最终默认值覆盖。
