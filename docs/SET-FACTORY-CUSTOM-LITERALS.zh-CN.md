# SetFactory：非空 custom 字面量的有界证明

`set_factory_custom_literals` 将已绑定的共享 Factory 前缀延伸到一小类 `CreateCustomSet<T>` 调用。全部工作为 PE/CLI 字节解析，输入不会加载为程序集或在 CLR 中执行。

## 接受的路径

- 整个泛型方法的 IL、局部变量签名、每个分支目标和通用参数 token 必须匹配。分配、默认值填充、拆箱和结果写入使用同一个 `!!0`。泛型参数必须恰为一个无约束参数，MethodSpec 必须指向这个方法。
- T 仅接受 Boolean、Char、SByte、Byte、Int16、UInt16、Int32，或精确核心库 `Nullable<T>`。不接受 enum、用户 struct/class、开放泛型、指针、浮点或依赖平台宽度的整数。
- 核心 `System.Type` 反射调用绑定准确的所有者、名称、签名和签名内 Type/RuntimeTypeHandle 引用。核心程序集身份使用既有受信身份约定；这不是验证外部核心库实现的密码学证明。
- 对允许的 primitive，`IsPrimitive` 路径只执行准确类型的 `unbox.any`。对允许的 Nullable，泛型定义比较必须准确为 `Nullable<>`，装箱的基础值拆箱为有值 Nullable。没有数值扩大、缩小或类型间隐式转换。
- `Convert.ChangeType` 的签名和所在分支被绑定，但接受的具体 T 永远不进入这个路径。其行为未被加入通用 intrinsic 白名单。class/enum/其他 struct 的转换不被认证。
- primitive 默认值为范围内整数字面量。Nullable 默认值必须在准确类型的局部变量上执行 `initobj`，紧接着读取同一个局部变量；证据中的 null 表示无值 Nullable。这个功能不支持默认值构造器，也不支持空 payload。
- 参数是刚分配的 object[]，由有界的 dup/索引/字面量/box/stelem.ref 片段填满。每一个 box 都核对核心类型身份与数值范围，Boolean 仅接受 0/1。重复参数槽位按最后写入值解释；数组外逃、未知操作、未填槽位或越界写入被拒绝。
- key 必须是该方法明确处理的 UInt16、Int32 或 Int16 box，且非负。payload box 必须与 T 的基础类型完全相同；同值但不同 box 类型不能借助 Convert 获准。结果覆盖按 pair 原顺序记录，重复 key 保留最后写入语义。
- 结果字段必须属于当前 Sets 类型且完整元素签名相同。方法仅修改新结果数组，不修改 Factory、队列或参数。新增的零长度 bool[] 字面量发布片段也精确绑定元素类型与结果字段，不引入任何调用。

只有完整片段通过验证，才能将它的调用发生位置从残余中移除。任何失败都保留该片段和后续调用；不能跨越未知效果继续借用共享状态。正常返回条件蕴含索引在结果数组范围内，但不保证正常返回、不证明数值 Count，不产生运行时快照。

## 已验证与保留的义务

公开测试是原创合成 PE，涵盖非空/空参数、准确类型拆箱、三个 key 类型、重复 key 与参数槽位、默认值局部绑定、MethodSpec/泛型约束、核心身份、非法分支、未知调用、字段签名、预算和取消。

对提供的同安装 1.4.5.8 客户端/服务端，核对输入 SHA256 后纯静态验证的结果一致：连续前缀从 24 个分配/存储片段和 46 个调用扩展到 37 个片段和 66 个调用；新增四个 Nullable<Boolean> custom 调用。残余从 33 组 / 651 次降至 32 组 / 643 次。已知 wrapper 调用原先不在残余计数内，因此新增已证明调用总数与残余减少数不同。

新边界为 IL 1843，后续首个未证明调用为 IL 1871 的用户定义对象构造。继续扩展前必须闭合该构造器、字段及集合修改、对象别名、准确 class 型 T 与拆箱/引用行为，再证明完整调用和发布片段；不能把名字相似的构造/转换方法认作纯函数。

原始输入、真实数组数据和完整私有证据不提交公开仓库。`complete`、`publishable`、`wholeInitializerProven`、`runtimeSnapshotUsable` 仍为 false；full Terraria 尚未闭合。
