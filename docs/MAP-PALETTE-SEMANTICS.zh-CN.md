# 有限地图调色板模型与明确的停止边界

`map_palette_semantics.py` 对固定 Windows 1.4.5.8 客户端/服务端的 `MapHelper.Initialize` 做数据式有限模型。它不会加载游戏或 XNA 程序集、执行 CLR、启动图形设备或世界生成。

## 已证明的有限目标

在独立 Count 条件模型、已审计 XNA Color 值运算、正常初始化及无外部干扰前提下，模型到达最后一次 `Lang.BuildMapAtlas` 调用**之前**的边界：

- 所有 tile/wall Count 区间都有记录，包括零选项条目；没有从成功行反推域。
- 每条包含 lookup、optionCount 和全部已选择选项的 packed Color。R 在最低 8 位，随后 G/B/A；这里保留完整四通道值，不私自选择单个目录颜色。
- 所有目标索引、选项数量、数组引用和目标颜色必须确定。数组对象身份与别名在堆中保留；通过另一引用写入目标单元同样会影响最终检查。
- 天空/地形梯度中的未建模浮点运算保留为有类型的未知 scalar/Color。未知值进入索引、控制条件、数组大小或最终目标单元即拒绝，不能因为它最初来自“辅助层”就忽略其别名影响。
- 任何未知指令/调用、来源或类型/签名不符、栈不足、越界、整数溢出、明确的整数除零、预算超限都拒绝。未实现的任意 CLI 算法不会被执行或放行。

公共入口仅接受固定源 SHA 和固定官方 XNA 核心 DLL SHA，不能上传自定义 profile 或完成断言。真实两份来源的目标记录相同：754 个 tile、367 个 wall；分别 1064、357 个选项。203,572 个有限模型步骤，最大栈 7。768 个非目标 palette 单元仍明确未知。

这不证明地图说明/名称，也不证明 legend 调用及之后不会改写数组。输出明确保持：

- `factScope=PRE_LEGEND_BOUNDARY`
- `targetPaletteModelComplete=true`
- `wholeInitializerProven=false`
- `legendTailNonmutationProven=false`
- `runtimeDependencyBindingVerified=false`
- `materialBaseReady=false`
- `complete/publishable/runtimeSnapshotUsable=false`

这些条件事实可用来交叉核对后续有界运行时观察，不能单独填入最终 materials 发布证书。

## XNA 值运算，而非 native 启动证明

`xna_color_semantics.py` 从 Microsoft 官方 XNA 4 分发的固定核心 DLL 字节检查 15 个完整方法体：单 UInt32 值布局、整数 RGB 构造与 clamp、四通道 getter、alpha setter、值相等、乘法和四个命名颜色。

Color 值和 managed-address receiver 严格分开；不能把地址作为 by-value Color 参数，也不能把值作为实例 getter 的地址。核心 UInt32.Equals 的普通纯值契约被明确假定，不允许未知回调。

乘法先把精确 binary32 literal 乘以 2^16，再夹取/截断为 Q16 因子，然后进行有界无符号通道运算。2^16 是指数平移，不引入 mantissa 舍入；溢出会落到相同 clamp 端点。每个字节乘积小于 2^32。地图 wrapper 在乘法后恢复原始 alpha。测试包括全部 256 字节值、极小/极大/负 scale、随机通道及独立 Fraction 参考。

这不证明 XNA 混合模式模块/CRT 初始化安全、成功或真实运行时 loader 解析。官方 DLL 仅作为数据读取，未安装或执行。模型中的依赖绑定仍是明确前提。

## 私有使用

先自行取得并核验官方 XNA 核心字节。当前固定 SHA-256：
`38e7093f52d7474bbc6256906519781a1210d7da50a1c667b52716fcf49ca130`。

```sh
PYTHONPATH=src python scripts/extract_map_palette.py \
  --source /PRIVATE/Terraria.exe \
  --xna /PRIVATE/Microsoft.Xna.Framework.dll \
  --output /PRIVATE/new-map-proof
```

输出必须是仓库外的新目录，不能经过 symlink。完整记录只进入私有 `map-palette-proof.json`；stdout/summary 只含数量、摘要和明确关闭的完成/发布标志。不要提交这些私有记录、游戏源文件或 vendor DLL。

此命令是数据式模型，不是让用户执行游戏的 collector，也不是“只剩真机验收”的全链路交付。

## 原创测试和剩余生产闭环

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_*palette*.py' -v
PYTHONPATH=src python -m unittest discover -s tests -p test_xna_color_semantics.py -v
```

原创 PE 测试覆盖别名、未知数据污染目标/控制/索引、类型与域、XNA/Core 身份、来源封闭、栈/异常、边界缺失及输出安全；不依赖真实游戏/微软二进制。

下一步以有界 collector 取得明确 profile 的最终运行时事实，补齐实际消费字段、名称、配方与应用政策。不要扩展成证明所有世界状态。逐角色缺口、110 项旧合同与新的 scope 讨论见 [生产就绪矩阵](SOURCE-PRODUCER-READINESS.zh-CN.md)。
