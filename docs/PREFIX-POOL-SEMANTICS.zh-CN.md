# 前缀池初始化边界的有限证明

`prefix_pool_semantics.extract_prefix_pool_semantics(input_path, checkpoint=None)`
只把指定程序集当作 PE/CLI 数据读取，不加载 CLR，不运行游戏 EXE、类型初始化器或游戏方法。
公开入口只接受代码内固定的 Windows 1.4.5.8 客户端和服务端源 SHA-256，并分别核对两个
`.cctor` 的 MethodDef token、完整签名及 IL SHA-256。文件名、上传声明或调用者提供的
profile 都不能替代来源验证；不支持通过参数添加 profile。

## 已闭合的局部事实

- 独立验证 `Terraria.ID.PrefixID.Count` 的完整初始化器：唯一整数常量、精确的只读
  `int32` 静态字段存储、唯一末尾返回，没有隐藏调用或额外指令。
- 完整验证 `Terraria.GameContent.Prefixes.PrefixLegacy+Prefixes..cctor`，全部八个字段
  的所有者、名称、签名和标志须与消费接口 `item_assembler.POOLS` 相符。
- 每一行都来自新建 `int32[]`，通过严格校验的核心库 `RuntimeHelpers.InitializeArray`
  和 RVA 字节，或有限的逐元素整数存储，得到完整数组；只向当前初始化器所有者的对应字段
  发布一次。未知效果、别名读取、分支、外部存储、额外或缺失字段、重复存储全部拒绝。
- RVA 路径验证静态字段、值类型、显式布局、打包大小、精确字节数、唯一 RVA 位置以及
  核心库类型身份；逐元素路径验证索引范围。原始顺序完整保留，不排序、不去重，重复 ID
  拒绝，所有最终元素必须落在独立 Count 域内。
- 验证声明的栈深度：Count 至少 1，RVA 数组路径至少 3，逐元素存储路径至少 4。
  方法字节数、指令、工作量、时间、字面量和证据大小都有界，取消会原样向上传播。

两个实际源均成功产生八行前缀池、195 个有序元素、97 个不同引用 ID，独立 Count 域大小
为 98。两源的这组初始化边界数组相同。这只是提取统计，不包含任何原始数组或游戏表。

## 明确不证明的内容

结果状态是 `PROVEN_PREFIX_POOL_INITIALIZER_BOUNDARY`，事实范围为
`INITIALIZER_BOUNDARY`。`wholeInitializerProven=true` 只表示支持的初始化器完整效果已
被覆盖，不能解释为初始化必然成功或完整游戏语义已经闭合。

数组及其公开静态字段以后仍可被修改，结果不能直接充当任意后续时刻的运行时快照。
结果始终保留 `normalReturnGuaranteed=false`、`runtimeSnapshotUsable=false`、
`complete=false` 和 `publishable=false`。前缀名称、效果、物品适用性、最终物品默认值、
完整消费组及发布权限仍需要各自独立的证明和门禁。

返回的 `pools` 和 `poolEvidence` 是私有源事实，供后续证明组合使用；不要直接写入公开
日志、版本库或发布目录。公开报告只需使用 `stats`、来源角色、摘要和状态。

## 原创测试

`tests/prefix_pool_fixture.py` 从零构造原创 PE/CLI 元数据和小型数组，不包含游戏字节或
抄录的游戏表。测试覆盖顺序、缺失/重复/越界、错误字段与核心库身份、畸形布局与 RVA、
完整方法覆盖、混合 RVA/逐元素覆盖、声明栈深度、预算、取消及公开 profile 拒绝。

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_prefix_pool_semantics.py' -v
```
