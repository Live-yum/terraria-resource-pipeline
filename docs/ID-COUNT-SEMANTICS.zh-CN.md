# ItemID / TileID / WallID Count 的有限静态证明

## 结论与边界

`id_count_semantics.py` 只读取 PE/CLI 元数据和 IL，不加载程序集、不启动 CLR、不执行游戏 EXE，也不生成运行时采集器。

公共入口只接受固定 Terraria 1.4.5.8 客户端和服务端输入。它证明：在明确的依赖绑定、受信核心库和无干扰条件下，各自 `.cctor` **正常返回的边界**上，`ItemID.Count`、`TileID.Count`、`WallID.Count` 分别为 6196、754、367。它们的元数据类型分别是 `Int16`、`UInt16`、`UInt16`。

Count 仅证明声明的数值上界，并不证明区间 `[0,Count)` 中的每个整数都是有效可选物品/材料，也不独立认证任何完整的消费者选择集合。ItemID 常量可含别名和域外 legacy 条目。

这不是“找到一个 literal store”或把外部调用当作无作用。完整的 21 字节初始化器及其 ReLogic 尾部均有有限语义检查。

始终保持：

- `factScope = INITIALIZER_BOUNDARY`
- `wholeInitializerProven = true`
- `independentDomainInitializerProven = true`
- `externalTailCountNonmutationProven = true`
- `normalReturnGuaranteed = false`
- `runtimeSnapshotUsable = false`
- `executedInput = false`
- `runtimeDependencyBindingVerified = false`；`modeledReLogicDependency` 明确列出被建模的程序集和嵌入哈希
- `complete = false`、`publishable = false`

不保证初始化成功、终止、进程生命周期不可变、最终运行时快照或完整资源提取。元数据中的 `initonly` 不是反射、unsafe 或外部干扰不可变性的证明。

## 固定输入绑定

公共 API：

- `extract_id_count_semantics(input_path, checkpoint=None)`
- `prove_id_count_program(p)`：复用现有 `_Program` 与累计预算；仍强制校验输入哈希。

公共入口没有自定义 profile、哈希、方法、拥有者、回调或“已完成”覆盖参数。

客户端 SHA-256：
`960a03bff6050cf7be16dfc1a7b19e10fc2c4f8f835a6a3b135a50dd9e6ba2f3`

服务端 SHA-256：
`d87e3faf08637f6be8882c63e7f11fb7e792b0230006309618473ece0f863e1e`

两者的嵌入 ReLogic SHA-256：
`e1c5dccefff5fd1c789ff712babfa1a305fced0d03c96ef30f2c14d99aa0af29`

除整文件哈希外，还核对游戏程序集名称与版本、每个 Count 方法 token、IL 哈希、签名哈希、Count 值和反射候选 literal 数量；ReLogic 的 11 个方法也单独绑定 token、IL 哈希、签名哈希。

这些哈希是来源约束。实际语义证明仍执行整方法形状、栈、元数据、回调、泛型签名和全范围缓存写入扫描，不能用哈希或普通审计标签代替。

**运行时加载边界：**模型假定游戏的 ReLogic 引用解析为本次审计的嵌入 ReLogic 字节。工具没有证明实际 CLR/AssemblyResolve 选择了这个资源，没有运行正常游戏入口，也没有审计任意程序集解析器。

## 独立 Count 初始化器

每个完整初始化器必须恰好执行：

1. 载入一个有符号 `ldc.i4` 整数
2. 写入该拥有者唯一的 `public static initonly` 原始整数 `Count` 字段
3. 调用确切 `IdDictionary.Create<TContainer,TId>()`
4. 写入确切类型的 `Search` 字段
5. `ret`

禁止额外前后语句、替代字段、未知调用、分支、局部变量、异常区间、歧义 `.cctor`、不正确方法标志和不足的声明栈大小。整数必须为正、位于字段的 Int16/UInt16 可表示域，并满足预算。因此不会借助截断把越界整数解释为 Count。

`MethodSpec` 必须绑定本拥有者的确切 TypeDef 和确切原始 TId；被调 MemberRef 的程序集、拥有者、名称、泛型数量、无参数签名和返回类型均核验。

## 反射输入的有限约束

具体容器直接继承受信 `System.Object`，不接受自定义基类、泛型拥有者或显式字段布局。

逐个检查所有公开字段：

- 唯一的 `Count` 是在调用前已经赋值的只读原始整数
- `Search` 的类型必须是确切 ReLogic `IdDictionary`，不满足原始类型过滤条件
- 其他被选择字段必须为相同原始整数类型的 `public static literal`，有唯一且类型、宽度正确的 Constant 记录
- 公开字段不能是实例字段、同名歧义字段或任意 object / 用户类型

正常参数无关的 `GetFields()` 只可能找到唯一公开 `Count` 名称。`GetFields(Public|Static)` 经过确切 FieldType 过滤，只读取 primitive literal 或已经赋值的 Count。由于对象来自具体运行时类型的反射，调用不会分派到自定义 `Type` / `FieldInfo` 子类；读取的是字段，不是属性或自定义访问器。`Convert.ToInt32` 只转换装箱 Int16/UInt16，因此不会调用游戏自定义 `IConvertible`。

该检查不把 literal 最大值、数量、组成员数或纹理文件数当作 ID 域。literal 的名称和值不进入本模块的输出。

## ReLogic 整方法与回调效应

覆盖以下 11 个方法的每条语句和所有控制流分支：

- 泛型 `Create<TContainer,TId>()`
- `Create(Type,Type)`
- `IdDictionary` 构造器
- 捕获类构造器、类型谓词、字段动作
- 单例初始化器、单例构造器、Count 名谓词、pair value 与 key 选择器

具体作用：

- 泛型入口取得两种确切 RuntimeType，然后转交到非泛型入口
- 创建新的捕获对象
- 用确切 Count 名谓词查询 Count，读取并转换原始值
- 创建新的 `IdDictionary` 和 `Dictionary<string,int>`
- 用确切类型谓词过滤公开静态字段；将结果列表逐项交给确切字段动作
- 字段动作读取装箱原始值；按字典的 Count 上界筛选；只写入新字典
- value/key 选择器只读取 `KeyValuePair<string,int>` 的值与键
- 建立新的反向 `Dictionary<int,string>`，写入新 IdDictionary，返回

构造器完整覆盖，验证对象字段不重叠、只初始化预期实例字段。捕获对象和单例类是嵌套 private sealed，字段和方法集合确切。所有成员、委托、List、Dictionary、Enumerable、KeyValuePair 的泛型类型参数、参数顺序、class/value 区别、拥有者和完整方法签名都核验。本地伪装成核心库的同名类型不能通过检查。

完整形状加上按控制流求解的栈高度检查，确保各分支栈连接一致、所有指令可达、返回栈正确、声明 maxstack 足够。未知语句、未知回调和未知签名直接拒绝。

## 缓存委托闭包

四个缓存字段是单例 `<>9` 和三个懒缓存委托。仅看到 `ldftn` 不足以证明缓存非空时的回调身份。

工具对嵌入 ReLogic 的**所有 RVA-bearing 方法**做有预算的完整 IL operand 扫描，包含该调用路径外的代码：

- 解码每个方法，不跳过解码失败、非法头、非法本地签名、非法异常区间或未知 opcode
- 解析可能指向同一字段的 MemberRef 别名
- 在组合 metadata token 之前验证原始 RID 范围，防止大 RID 位运算别名
- 每个直接写入必须属于已经整方法证明的四个确切 store
- 禁止字段取地址、`ldtoken` 暴露缓存句柄及错误的实例字段操作

固定真实嵌入包有 958 个被扫描方法、46,287 字节 IL。四个直接写入分别建立确切单例或确切回调。因此在初值为 null、只有这些直接写入、无间接干扰的模型中，缓存非空也只能含有对应固定回调。

这个 operand 扫描只证明四字段直接写入/地址使用的闭包，**不证明整个 ReLogic 的任意方法都安全**，也不是通用 CLI 验证器。反射、unsafe、native、插桩和外部修改是明确排除条件。

## 受信条件

- 正常返回的 CLI 初始化与分配，包括编译器单例的初始化
- 游戏的 ReLogic 引用绑定到已审计嵌入字节
- 初始化观测期间 Count 字段不受并发/外部修改
- 私有缓存和新对象不受反射、unsafe、native、插桩或外部修改
- 核心库 AssemblyRef 是预期身份，执行普通 CLI/核心库语义
- Object/Dictionary/List/委托构造、RuntimeType/FieldInfo 元数据和原始字段读取的普通契约
- LINQ/List 只调用已证明的回调；string/int 默认比较器和原始转换遵守普通核心语义

工具核验这些核心引用的名称、版本、culture、标志和 public key token。未解析或加密绑定机器上的框架文件；不声称验证了框架字节。

异常（分配失败、转换、重复字典键等）不会被忽略为成功。证明是“若正常返回，则边界事实成立”，不推出“必然正常返回”。

## 组合和测试

返回 `domains` 以 `ItemID`、`TileID`、`WallID` 为键。每项含 `owner`、`fieldToken`、`count`、完整 Count 方法证据和原始反射输入证据。组初始化器可用相同解析器/预算独立完成本证明，再绑定相同的 Count 字段及方法；不可用发现的 literal store 或调用者布尔断言代替。

内部测试入口 `prove_id_dictionary_dependency` 返回专用证书类型；`prove_id_count_initializer` 组合该实际证明。公共入口自己提取和核验依赖，不接受调用者传入证书或完成断言。

`tests/id_count_fixture.py` 为原创的小型 PE 构造器；它使用虚构 literal、独立 token 布局和原创错误字符串，不包含游戏字节或游戏数据表。回归覆盖完整方法尾部/前部注入、错误回调、错误 generic/Core 身份、本地伪装类型、缓存额外写入/地址/别名、无法解码的隐藏方法、Count 域和 primitive 元数据、栈/局部变量/方法标志、RID 别名、预算与取消，以及公共 profile 拒绝。

运行：

```sh
PYTHONPATH=src python -m unittest discover -s tests -p test_id_count_semantics.py -v
```

真实 PE 私有验证结果应保存在仓库外。本模块不输出组数组、不发布数据、不修改生产提取器，也不扩大任何外部写入授权。
