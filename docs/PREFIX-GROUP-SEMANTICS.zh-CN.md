# PrefixLegacy.ItemSets 的有限初始化边界证明

`resource_pipeline.prefix_group_semantics.extract_prefix_group_semantics(input_path, checkpoint=None)` 只读取固定客户端或服务端 PE/CLI 数据，返回私有证明结果。输入程序集、构造器、类型初始化器和 CLR 均不执行；不加载第三方程序集。

## 已证明的范围

公开入口绑定两个已审核的 1.4.5.8 输入散列，以及各自完整 `PrefixLegacy+ItemSets..cctor` 的方法 token、IL 散列、签名、246 字节和 61 条指令。没有可由请求参数覆盖的输入散列、方法、Count 数值或依赖证明。

在所有列明的正常返回和无干扰前提下，证明完整初始化器返回时：

1. `ItemID.Count` 来自独立完整初始化器的证明。该证明包括 Count 的 Int16 类型、只读静态字段、完整 21 字节路径、`IdDictionary.Create<ItemID,Int16>` 的有限外部尾部、反射字段约束和固定回调缓存不变量。这里只使用该完成后的域，不从 group 最大 ID 或局部字面量赋值推导域。
2. `new SetFactory(Count)` 产生新对象；构造器将参数原样存入 `_size`，建立互异的空队列和锁，不泄漏 `this`。模块与 SetFactory 不存在未建模的 `.cctor`。
3. 精确 owner 上的一个 `Factory` 字段和七个 `bool[]` 字段全部覆盖。名称、签名、公开静态可变 flags、唯一字段写入和完整方法覆盖均校验。
4. 每个 recipe 创建独立 `int[]` 参数，只接受完整类型化 RVA/InitializeArray 路径或显式 Int32 元素赋值。所有最终 ID 均属于独立 Count 域；重复 ID、越界索引和未知操作拒绝。不会通过排序或去重修补输入。
5. 两个 `CreateBoolSet` 重载、`GetBoolBuffer`、分支和 finally 区域使用已有精确形状证明。构造后 bool 队列为空；七次调用均无回收/入队，空队列路径分别产生新 `bool[Count]`，随后完整填充 false 并在字面量 ID 处赋 true。七个输出互异，队列在边界仍为空。
6. 每个 recipe 起止栈为空；完整初始化器没有分支、局部变量或异常区域。调用者和全部 factory helper 的声明 maxstack 必须覆盖已匹配语法的实际需求。未匹配尾部、额外 store、错误 call 目标或栈残留一律失败。

两份实物输入均得到七个长度 6196 的新 bool 数组，合计 553 个成员条目；各组有序输入摘要一致。这里只保存这些汇总，仓库不保存提取的游戏 ID 数组。

## 结果与证据

- `groups`：以既有 consumer `GROUPS` 名称为键、保留原始输入顺序的 true 成员 ID。仅供私有边界证据；不是可直接发布的最终注册表。
- `declaredDomain`：独立完整 ItemID Count 证明及域边界。
- `groupEvidence`：字段 token、recipe/call/store 偏移、字面量摘要、typed RVA 证据、输出分配身份和队列状态。
- `methodEvidence`、`helperMethodEvidence`、`fieldEvidence`、`dischargedCalls`：方法/字段字节证据、完整覆盖和栈界限。
- `countDependencyEvidence`：同一输入上的独立 Count/ReLogic 证明、假设与范围。
- `modeledReLogicDependency`：模型采用的 ReLogic 程序集名称、版本及嵌入资源散列；顶层 `runtimeDependencyBindingVerified=false`。

证据中的 core-library 程序集身份由 metadata 校验。它们仍然是明确的语义 intrinsic 契约，未声称解析并密码学验证 framework 实现字节。

`wholeInitializerProven=true` 和 `numericSizeProven=true` 仅描述这个边界命题。`factScope=INITIALIZER_BOUNDARY`；`normalReturnGuaranteed`、`executedInput`、`runtimeSnapshotUsable`、`complete` 和 `publishable` 始终为 false。

## 必须保持的限制

- 假定独立 ItemID 初始化已正常完成，不存在重入时读取默认 Count 的情况；BeforeFieldInit 本身不能建立这个事实。
- `Factory`、七个数组字段及数组元素均可公开改变。边界执行期间排除并发/外部替换、回收、反射、unsafe/native 干扰；返回之后不保持这个快照声明。
- 继承 Count 证明对 core 反射、装箱原始整数转换、集合、委托以及私有缓存无外部篡改的前提。
- 模型中的 ReLogic 引用绑定到独立审核的嵌入字节。资源散列和 AssemblyRef 身份相符不等于证明实际 CLR/AssemblyResolve 选择了这些字节；运行时依赖解析仍未验证。
- Count 区间只是数值边界，不能单独认证区间中每个 ID 都是有效物品、材料或可获得 prefix 的成员。
- 分配、初始化和被调用操作可能抛异常；不证明终止、运行成功、最终生命周期或所有物品的实际 prefix eligibility。
- 这一步不启用真实生产适配器、完整资源组或发布权限。既有发布门禁不变。

## 回归与预算

原创 `tests/prefix_group_fixture.py` 产生独立的 PE、字段、原始 ID、队列和 helper IL。测试组合实际的原创合成 ReLogic 依赖证明和完整 Count 证明，绝不以成功标志替代外部尾部。覆盖新数组/空队列、inline/RVA/覆盖写、空输入、完整字段集、错误域、重复值、错误目标、未知副作用、分支、返回、locals/EH、类型和程序集身份、helper/caller maxstack、全局字节/指令/步骤/时间/证据预算及取消传播。

```sh
PYTHONPATH=src:tests python -m unittest discover -s tests -p 'test_prefix_group_semantics.py' -v
```

任一拒绝或预算耗尽均抛错，不返回可使用的部分 groups。公开入口不能把原创测试输入加入真实来源白名单。
