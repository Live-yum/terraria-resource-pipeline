# SetFactory 新实例构造器：有界静态证明

`resource_pipeline.set_factory_constructor.extract_set_factory_constructor` 仅读取输入 PE/CLI bytes，不加载 CLR、不执行游戏代码。它为受支持构造器的正常返回时刻生成独立证据，不能替代完整静态初始化器或后续调用的状态证明。

## 证明内容

- 精确绑定 SetFactory 所有者、核心 Object 基类、实例构造器签名、无局部变量/异常区的完整方法体
- 每个缓存必须由匹配元素类型的核心 Queue 数组泛型参数创建；支持 bool、int、ushort、float 数组队列，绑定字段签名和所有者，拒绝重复写入、缺失初始化和未知字段
- 每次缓存和锁分配都是不同的新对象；锁必须由核心 Object 构造器创建；验证 Object 基类构造调用
- `_size` 必须完整复制构造参数；可识别精确的零值检查和 ArgumentOutOfRangeException 路径。该检查不拒绝负数，输出明确保留这一事实
- 完整 IL 形状中不能包含额外调用、字段写入、引用逸出或未知控制流。未知形状不产生构造器证书
- 完整输入 SHA256、方法 IL/签名散列、代码与元数据偏移都绑定实际输入；核心库引用身份按受信语义 intrinsic 解释，并非对外部程序集做密码学认证

成功状态为 `PROVEN_FRESH_CONSTRUCTOR_NORMAL_RETURN`。`thisEscapes=false` 指受支持方法体在受信核心库语义下没有逸出路径。它不声称其他运行时主体或反射无法获得对象。

## 与 bool 集合配方的关系

`extract_item_dispatch_sets` 的 `factory.freshConstructor` 保留该独立证明。构造器不支持时，原有条件配方可继续输出，但不会得到构造器成功状态。共享预算耗尽或取消不能变成可选证明失败而继续成功。

这里不把“构造时队列为空”外推成“每次 GetBoolBuffer 调用时队列为空”。中间 Create/Recycle 调用、Factory 引用逸出、整个 Count/Sets 初始化器副作用和后续字段修改仍需独立闭合。因此 `bufferLengthStatus=UNPROVEN_AT_CALL_SITE`、`cctorNormalReturnSnapshotUsable=false`、`runtimeSnapshotUsable=false` 保持不变，残余调用也没有被移除。

`complete`、`publishable` 和 `executedInput` 始终为 false。真实生产器所需语义集合、完整候选发布门禁及版本/散列绑定未改动。

## 验证

公开测试只使用原创合成 PE。涵盖四类队列类型、零值分支、错误核心库/字段/签名、未初始化缓存/锁、未知调用、重复写入、参数变化、错误异常类型、预算、取消和超时。

```sh
PYTHONPATH=src python -m unittest discover -s tests -p test_set_factory_constructor.py -v
```

同安装用户确认的 1.4.5.8 输入也经过只读字节解析：客户端 SHA256 `960a03bff6050cf7be16dfc1a7b19e10fc2c4f8f835a6a3b135a50dd9e6ba2f3`，服务端 SHA256 `d87e3faf08637f6be8882c63e7f11fb7e792b0230006309618473ece0f863e1e`。两者均得到上述新实例构造器证书，分别证明四个初始空队列、一个新锁和原样大小参数；未执行任一输入。原始程序集、真实集合、完整提取证据保持在私有审计目录，不提交仓库。
