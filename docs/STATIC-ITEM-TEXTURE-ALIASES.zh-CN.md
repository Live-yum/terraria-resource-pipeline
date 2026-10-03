# 物品图片别名的有界静态证据

`resource_pipeline.item_texture_aliases.extract_item_texture_aliases` 接受程序集原始 `bytes` 和 Content 根目录相对路径的 NDJSON 清单。它不加载程序集、不调用 CLR、不执行游戏代码，也不接入真实生产器或发布路径。公开测试只使用原创的微型 PE/CLI、整数表和清单；真实输入与提取后的别名表必须保存在私有目录。

API 示例：

```python
from resource_pipeline.item_texture_aliases import extract_item_texture_aliases

result = extract_item_texture_aliases(
    server_bytes,
    content_inventory_ndjson,
    expected_input_sha256=operator_selected_sha256,
    checkpoint=job.check_cancelled,
)
```

`expected_input_sha256` 是调用者选择的输入固定值，不是可信版本认证。函数自行计算程序集和完整清单的 SHA-256，并附带方法 IL、签名、字段 RVA 数据的散列与文件偏移。

## 证明的具体范围

成功状态为 `PROVEN_CONDITIONAL_ITEM_TEXTURE_ALIASES`：

1. 绑定 `ItemID.Count`、`ItemID.Sets.Factory`、`TextureCopyLoad` 和 `TextureAssets.Item` 的元数据所有者、字段签名及唯一的直接初始化写入。三个相关初始化器不允许分支、提前返回或目标字段地址逃逸；这不等于证明其所有外部调用的副作用
2. 识别 `CreateIntSet(-1, pairs)` 的初始化切片。验证新的 `int[]`、核心库 `RuntimeHelpers.InitializeArray(Array, RuntimeFieldHandle)` 身份与签名、显式布局数据类型和唯一的 FieldRVA。表长度必须为偶数，并且与布局字节数完全相同
3. 验证整个 `CreateIntSet` 控制流：拒绝奇数参数；将返回缓冲区每个位置填为默认值；按顺序将每对整数写入对应位置。因此重复 key 使用最后一次写入，后续 `-1` 可以恢复直接加载
4. 验证 `GetIntBuffer` 的直接分配形状或包含 `Queue<int[]>`、Monitor 和单个 finally 的完整池化形状。旧缓冲区内容会被覆盖；不假定缓存为空或证明构造器及回收生命周期
5. 验证 `LoadTextures` 开头完整物品循环：从 0 开始，每次加 1；有别名时复制当前已写入的先前物品槽；否则请求 `Images/Item_<id>`。绑定泛型 `LoadAsset<Texture2D>` 的签名及其向 `IAssetRepository.Request` 原样传递名称和请求模式的实现。其余 IL 不得跳回物品循环，或直接再次访问被证明的物品/别名字段

只接受上述封闭形状。方法、字段、外部核心类型或签名歧义，错误分支、异常区、RVA 布局、参数或未知实现都会产生明确的 `ALIAS_*` 状态，清空别名结果，不回退到硬编码真实版本表。

## 顺序覆盖计算

输入清单的 `path` 必须相对于 Content 根目录。只有精确的 `Images/Item_<十进制 ID>.xnb` 被计为直接图片；`Sounds/Item_...`、嵌套同名文件、`Content/Images/...` 以及带前导零的编号均不计入。实际上传归档的 `Content/` 前缀应由调用方在确定根目录后移除；模块不猜测或剥离任意前缀。

覆盖计算按消费者的升序赋值顺序进行，而不是按别名图的可达性进行：

- 复制目标必须小于当前编号，且目标槽已成功解析
- 指向自身或未来编号，即使图最终通向一个存在的文件，也标为 `UNSUPPORTED_NONPRIOR_ALIAS`
- 基础图片不存在时，该槽及依赖它的后续别名均保持未解析
- 别名项即使具有自己的直接文件，也按消费者代码复制目标槽
- 输出同时保留原始有序 pairs、重复 key、有效别名数、缺失直接图片数、被别名补齐的数量、最终图片 ID 和链长

清单中的 Git blob ID、大小和模式只作为清单声明保留；模块没有读取或校验实际 XNB/PNG 像素。

## 条件、限制与后续闭合

证据要求初始化切片被执行并正常返回、缓冲区足够长、三个静态字段在消费者入口仍保持所记录的值、消费者正常完成，并且外部资源管理器按名称解释请求。清单与程序集的版本对应关系也须另行认证。

`complete`、`publicationAllowed`、`executedInput`、`runtimeStateCertified` 和 `imageBytesVerified` 始终为 `false`。即使 `logicalCoverageSatisfied=true`，也只是这些明确条件下的逻辑映射覆盖，不能用作完整适配器或资源发布授权。

要将固定原版配置用于正式图片映射，还需独立证明：SetFactory 构造器正确建立缓存/锁和 `_size`；该工厂实例在所需阶段的缓存回收与共享状态；别名数组全部读写及引用逃逸；Count/Item 槽的写入和调用先后顺序；匹配客户端 Content 版本和真实图片字节。反射、运行时修改器和任意模组不属于此原版闭合目标。

## 限额与验证

`ItemTextureAliasLimits` 限制输入、清单行/总字节/总行数、ID 域、pair 数、单方法/累计 IL 字节、累计指令、处理步骤、输出证据字节及墙钟时间。解码调用现有的有界元数据/IL 读取器。检查点贯穿扫描、解码、表处理、覆盖计算和证据序列化；调用方取消异常原样传播，包括使用 `PipelineError` 的取消。

```sh
PYTHONPATH=src python -m unittest discover -s tests -p test_item_texture_aliases.py -v
```

合成测试覆盖完整 PE 绑定、池化缓冲区及 finally、重复 key/最后写入、前向/自身引用、缺失根图片、多跳链、直接文件被别名覆盖、路径混淆、元数据身份/签名/布局、分支和工厂逻辑变异、输入固定散列、限额和取消。

RVA 背景类型还必须拒绝接口、抽象类型与泛型参数。累计 IL 字节和指令在读取前收费，拒绝的工作不使计数越过限额；追加 work 回执后的最终 JSON 遍历也检查同一取消/时间预算。最终遍历超限会撤回别名表和覆盖结果，不能泄漏成功状态或抛出未分类的大小异常。
