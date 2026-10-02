# 服务端静态语义证据

`resource_pipeline.server_semantics.extract_server_semantics(Path(...))` 是原创、纯数据读取器，依据 [ECMA-335 II.22–25](https://ecma-international.org/wp-content/uploads/ECMA-335_6th_edition_june_2012.pdf) 解析 PE/CLI 元数据。它不启动服务器、不加载程序集、不调用任何游戏方法、不解析外部依赖，也不会把程序集标记为安全可执行。

## 可提取范围

- PE32 / PE32+ 中的优化 CLI `#~` 表、字符串/Blob 堆与嵌入 ManifestResource
- Assembly 表的版本声明及 `Terraria.Main` 中可用的字面量版本字段
- `Terraria.ID`（包含嵌套类型）的有符号/无符号整数常量、同值别名与单独列出的 Count 常量
- 名称符合 `Terraria.Localization.Content.<locale>[.<part>].json` 的 UTF-8 JSON；支持 JSON 注释/尾逗号和具有明确 framing 的 gzip、zlib 压缩
- 每个常量的 Field/Constant 元数据 token、文件偏移、Blob 偏移；每个资源的文件偏移、输入/解压内容 SHA-256

输入 SHA-256 与程序集版本只能证明“这些字节包含这个声明”。它们不是来源真实性、客户端版本配对或完整 ID 域的认证。结果始终 `PARTIAL`、`complete=false`、`publishable=false`，不会注册可信适配器。

## 桥接输出

- `input`: SHA-256 与输入字节数
- `assembly`、`gameVersionEvidence`: 版本及其元数据位置，明确 `trusted=false`
- `idFamilies`: items/tiles/walls/paints/npcs/buffs/prefixes/player 等分组中的 `records`、`countConstants` 与不支持字段
- `localization`: 按 locale 的字符串字典与资源证据；原始文本只能留在私有任务目录
- `coverage`: 所有资源家族的提取计数与缺口；任何家族都不视为完整

静态常量不等同于全部有效游戏对象。非字面量、动态字段、item defaults、装备绘制、地图着色语义、世界生成规则、运行时工具提示/回退/插值与客户端纹理均不补造。缺少 XNB 不会被服务端文件或常量替代。

## 限制与拒绝

默认最多 128 MiB 输入、64 MiB 元数据、100 万条元数据行、64 MiB 资源区、单资源/解压 16 MiB、合计解压 64 MiB、50 万语言键、64 层 JSON。限制均来自调用方的受信配置，不取自上传清单。拒绝截断、越界、重复表关联、元数据流重叠、压缩炸弹/拼接流、符号链接、非字符串语言值和有歧义的重复语言键。不支持非优化 `#-`、pointer tables、raw-deflate 猜测及外部程序集资源加载。

## 本地私有调用

```sh
PYTHONPATH=src python -m resource_pipeline.server_semantics \
  /private/input/TerrariaServer.exe /private/output/server-semantics.json
```

输出使用私有临时文件原子替换；命令拒绝源文件覆写和符号链接目标。不得将真实服务器、语言字符串或完整提取 JSON 提交到公开仓库。

## 私有 CI 实证

`scripts/ci_server_semantics.py` 仅在明确的私有来源仓库 Actions 中运行，验证固定源 commit、文件大小、Git Blob SHA-1 和输入前后 SHA-256。完整结果仅存在进程内存中；上传材料仅包含计数、数字样本、字段/文本哈希与偏移，不包含真实语言文本或服务器二进制。真正的来源运行和公开仓库合成测试是两个独立证明；合成通过不能代称真实数据验证通过。

公开测试由原创非可执行 PE 字节夹具构造，不包含游戏二进制、游戏文本或旧查看器导出。运行：

```sh
PYTHONPATH=src python -m unittest discover -s tests -p test_server_semantics.py -v
```
