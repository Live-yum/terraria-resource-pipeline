# 静态 paint 输入 RGB：独立、有限、私有证据

`resource_pipeline.paint_inputs.extract_paint_inputs(path)` 是独立的 PE/CLI
静态证据提取入口。它只读取输入文件，不加载 CLR、不执行游戏、构造函数或
外部程序集，也不读取 viewer / legacy palette。当前入口没有接入 real producer，
没有改变任何 family-complete 判定，结果只供私有核验。

目前只接受 `terraria-server-paint-input-v1` 这个已审阅 profile，以及该 profile
固定的输入 SHA-256。未知 profile、不同输入、不同程序集身份、方法签名或
IL 指纹均失败，不尝试套用其他版本。生产 profile 的参数类型实际上是
`int32`；完整核验的是人为限定的整数 0..255 域，而非宣称方法参数为 byte。
内部 `_Profile` / `_extract_bytes` 仅为原创合成夹具测试入口，不是自定义
profile 的公共扩展或受信游戏版本发现机制。

## 证据包含什么

- 唯一的 `Terraria.WorldGen.paintColor`、主程序集身份、方法与局部变量签名，
  PE/CLI 元数据偏移、方法体偏移、头与 IL 的摘要
- `Microsoft.Xna.Framework.Color` 的 TypeRef → AssemblyRef 精确绑定，
  XNA 4.0.0.0、public-key token、空 culture、flags 与所用成员的精确签名
- 所有 256 个输入的返回通道、逐次写入、对应 IL 偏移和 MemberRef token
- 仅在 R/G/B 都有明确 byte setter 写入时输出 `rgb`
- alpha 独立保留 provenance；未赋值的 R/G/B/A 都保持
  `symbolic-Color.White`，不会自行填成 255

Color 的建模是显式的、条件性的闭合 intrinsic 合约：值复制；byte setter
只替换其命名通道；White 的实际通道值未知。签名和程序集身份符合这个合约
才允许应用。未读取外部 XNA 实现，因此这不是对运行时程序集解析、绑定重定向、
外部实现或最终渲染结果的证明。

当前 profile 明确区分 0（无 paint 的 selector）、1..30（当前输入 RGB 记录）、
31（非当前 paint 的 legacy selector）和 32..255（不支持的 selector）。
非当前 selector 的方法返回路径仍被检查并保留其符号结果，但不会被当成额外的
白色 paint。0 的标签也不是这里证明的 wrapper no-op 语义。域外输入不受支持。

## 闭合 IL 与边界

仅支持 `ldarg.0`、`ldloc.0/1`、`stloc.0/1`、`ldloca.s 0`、整数常量、
`call`、`ret`、`beq.s`、`bne.un.s`。所有字节在路径核验前完成解码与检查，
包括未访问区域中的 opcode 和 call。跳转必须到后续指令边界，且不能进入
local-address / constant / setter 三指令序列的中间。只接受精确的 fat / init-locals /
无 EH 头、两个精确类型的 locals；拒绝未知头、异常区、泛型、方法重载歧义和
非预期 managed/native/instance 标志。

Color 使用不可变的内部值表示以保留 ldloc 的值复制语义。受控 local 地址带有
local generation 与预期消费偏移，只能在当前 setter 序列中使用。每次读写都有
局部变量类型、栈类型、声明的 max-stack 与 byte 值域检查；禁止地址逃逸和跨
分支使用。这里没有一般 CLR 执行器，也没有动态 helper 分派。

默认资源上限：输入 128 MiB、metadata 16 MiB / 200,000 行、累计 metadata
名称 2 MiB、累计读取签名 64 KiB、最多 8 个同名方法候选、单方法与累计方法体
各 64 KiB、累计解码 16,384 条指令、每路径 512 步、全部路径 131,072 步、
累计检查工作 500,000 次、16 个栈位置 / locals、证据 1 MiB、共享 15 秒期限。
任何限额失败都是 fatal，不能当成可选 unsupported 子结果而继续成功。

读取采用有界 regular-file / no-symlink 流程，并检查读取前后的文件身份信息。
解码和元数据扫描的失败尝试也消耗预算。逐次写入、逐路径记录、绑定证据与最终
canonical JSON 都累计计费，覆盖已被覆盖或最终证明失败的写入；先精确预算检查，
再构造最终 JSON。输入读取、哈希、解析、执行和证据序列化共享取消检查与期限；
取消异常原样传播。

## 明确未完成

map transforms、shadow/negative 的 map 行为、wall range、wrapper / light 顺序、
coatings、localized names、item associations、shader pass、纹理采样和 textured
paint rendering 都不在本 slice 内。配对相同的 RGB 输入不能证明正常与 deep paint
具有相同纹理效果；显式输入 alpha 也不是 map 输出 alpha。`paintsFamily` 始终
为 `incomplete`。

## 验证与隐私

`tests/paint_input_fixture.py` 是手写、非可执行的原创 PE/CLI 夹具；颜色只是测试
用数值，不来自游戏 palette。`tests/test_paint_inputs.py` 覆盖身份、签名、头、
局部变量、分支、opcode、地址生命周期、逐通道 provenance、256 输入与累积预算。
运行方式：

```sh
PYTHONPATH=src:tests python -m unittest discover -s tests -p 'test_paint_inputs.py' -v
```

真实游戏的输入、提取结果、源码片段、比较脚本和逐记录 proof 全留在仓库外的私有
核验目录；不加入公开 fixtures。独立来源核验固定使用
`Live-yum/TerrariaDecompiledSource` 的只读提交
`8255d34616c780af12079425ac92a0a7aed87d71`，只作为输出比较，不作为提取输入。
公开模块不硬编码真实 RGB 表，也不导入它。完成后的私有 receipt 记录实现文件、
测试、文档、真实 proof 与比较结果的 SHA-256，供独立复核。
