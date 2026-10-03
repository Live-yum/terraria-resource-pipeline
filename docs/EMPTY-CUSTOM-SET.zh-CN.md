# CreateCustomSet 空参数路径：有界静态效果

`resource_pipeline.set_factory_empty_custom.extract_empty_custom_set` 只解析 PE/CLI bytes，不加载 CLR、不运行游戏。该证明专门处理 `CreateCustomSet<T>(defaultValue, pairs)` 的 `pairs` 为非 null、长度为 0 的条件路径。

## 已证明的范围

- 精确绑定泛型方法签名、局部变量、`_size` 字段、泛型元素类型、实际 IL 与元数据散列。
- 长度奇偶检查进入正常路径；分配全新的 `T[_size]`，逐个复制默认值，然后返回这个数组。引用类型或结构中的引用仍然是浅复制，不承诺对象深拷贝。
- 初始计数器为 0，空 pairs 的长度为 0；末尾 `blt` 条件不成立，反射、转换、拆箱与非空索引循环不可达。这一区域只解码并绑定散列，不赋予已证明语义。
- 可达路径没有外部调用，不写 Factory、缓存或 pairs，不使 receiver 逸出。内存不足、负大小、CLR 类型约束等仍然可能阻止正常返回；该证明不保证成功执行。
- 在无异常区、无跳转的 `ItemID.Sets` 初始化器中，另外识别紧邻调用的 `ldc.i4.0; newarr System.Object` 参数片段，绑定 MethodSpec 和实例化签名散列。这只证明正常分配返回后的空参数，未证明调用可达、receiver、默认值或当时 `_size`。

`extract_item_dispatch_sets` 在 `factory.emptyCustomSet` 中保留该独立证据。未知形状保持未解决；共享预算、取消与证据大小限制继续 fail closed。所有原有残余调用保留，`complete`、`publishable`、`runtimeSnapshotUsable` 和整个初始化器闭合标志仍为 false。

## 为什么不能外推到整个初始化器

客户端第一个 Factory 方法调用就是空参数 `CreateCustomSet<T>`，早于 bool/int set 调用。这条方法路径不访问池，但后续调用前的 Factory 引用、其他调用副作用、Count 变化以及完整缓存生命周期还没有全部证明。`CreateIntSet` 与 `CreateFloatSet` 的实际实现仍通过可出队的 pooled getter；非空 `CreateCustomSet` 则包含反射、转换、拆箱和不同索引类型。不能按方法名忽略这些行为。

## 验证

原创合成 PE 测试覆盖不可达未知调用/字段写入、可达额外调用、计数器与分支变异、错误字段/泛型/局部签名、非空参数、错误数组元素、调用方跳转、异常区、预算与取消。没有真实游戏代码或提取结果进入公开测试。

同安装用户确认的 1.4.5.8 客户端 SHA256 `960a03bff6050cf7be16dfc1a7b19e10fc2c4f8f835a6a3b135a50dd9e6ba2f3` 和服务端 SHA256 `d87e3faf08637f6be8882c63e7f11fb7e792b0230006309618473ece0f863e1e` 均以静态字节验证得到此条件路径证书，每个输入各识别两个空参数片段。两者的完整初始化器残余仍为 34 组、663 次调用，未减少或宣称已完成全部提取。真实输入和完整证据仅保留在私有审计目录。
