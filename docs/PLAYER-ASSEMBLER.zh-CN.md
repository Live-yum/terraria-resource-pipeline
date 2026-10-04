# 显式帧配方的人物消费组装

`player_assembler.py` 实现 `player.presentation`、`player.walk`、`player.atlas` 三个角色的派生，输入是原始 PNG、明确的帧配方、人物事实/应用政策和完整的三角色 item 基础层。

这不是 Terraria 渲染器、最终帧语义提取器或任意代码执行器。不会根据文件名猜装备槽、男女变体、层顺序或动画帧。缺少当前版本配方时仍是开发缺口，不能让用户手填未知映射后视为真实源验收。

## API 与封闭输入

`assemble_player_resources(policy=..., textures=..., item_objects=..., checkpoint=...)` 返回 `(objects, receipt)`。三个参数都是内存数据；没有反射、网络、shell 或游戏执行入口。

policy 必须恰好具有以下字段：

- `schemaVersion: 1`、`algorithm: "player-rgba-recipes-v1"`、`gameVersion`
- `sourceHashes`：命名证据 SHA-256，只保留 lineage，不当作已认证证据
- `itemHashes`：`items.catalog/items.rules/items.categories` 精确原始字节 SHA-256
- `walk`：有序行 `{key, canvas: [width,height], frames: [14 个 layer 列表]}`
- `choices`：`{columns, rows: [{key, layers}]}`，key 仅允许 hair/clothes ID；每格 40×56
- `repairs`：`{translations: {walkKey: [14 个 [x,y]]}, textures: [walk 配方]}`；修复图高及纵向范围不超过 56
- `facts`：完整 `buffs/dyes/hairRules/wingRules/selection/versionLabels`

每个 layer 必须显式给出：

```json
{
  "texture": "Original.png",
  "textureSha256": "由实际输入 PNG 得到的 SHA-256",
  "source": [0, 0, 16, 16],
  "destination": [3, 4],
  "tint": [255, 255, 255, 255]
}
```

纹理名称是安全相对 PNG 路径；纹理字节必须精确匹配 hash。source 是 x/y/width/height，禁止越过原图。destination 可负数，明确表示被 canvas 裁切的部分。tint 的四个通道均是显式 uint8。支持 8-bit RGBA 逐通道乘色；默认采用有序 source-over alpha 合成。layer 可选 `composition: "copy"` 执行无 mask RGBA 拷贝，保留 alpha=0 下的 RGB，用于已明确来源的直接 crop 策略；也可显式写 `"source-over"`。其他值拒绝。不接受自定义 shader、表达式、插件或推断回退。

人物事实按实际 `player-presentation-facts-contract.mjs` 的字段与域验证。永久增强字段名和存档版本范围属于应用现有 save-codec 权限政策，资源不能扩展它们。item 三个角色除 hash 外还执行完整消费结构验证；坏 JSON、空名字、错版本规则、不完整分类域不能借新 hash 通过。

## 二进制与图集

- 每个 walking texture 恰好 14 帧，共用 alpha 非透明边界；完全透明配方会拒绝，不静默删除域条目。
- 帧按首次出现顺序去重，生成 14 项 frame map。颜色不超过 256 时使用 uint16LE palette count + RGBA palette + uint8 indexes；否则 `00 00` 后跟原始 RGBA。
- 使用 zlib level 9，无损读回核对。相同压缩字节可 alias；不同段按首次出现顺序 gapless 拼接。
- 索引元组为 `[offset,length,width,height,cropX,cropY,originalWidth,originalHeight,frameMap]`。
- choices 按政策行顺序铺入 RGBA PNG，不能用旧 atlas 字节作为新产物。
- repairs 复用 frame crop/dedup，但压缩内容是原始 RGBA，没有 walk 的 palette-size 头或调色板；严格绑定现有 walk key，翻译位移固定为 14 对。

## 有界性

政策在任何规范序列化之前限制循环、深度 24、结构节点 300,000 和文本/最终 JSON 16 MiB；拒绝非有限数字、非法 Unicode 和非 JSON 类型。

纹理压缩输入总量不超过 128 MiB，单张不超过 32 MiB；复用严格 PNG chunk/CRC/像素界限检查，只缓存两张解码纹理。总解码/合成工作不超过 128M 像素。每层最多 512×512、每帧至多 64 层、walk 域至多 8192；可以使用 checkpoint 取消。每个最终可展开纹理至多 2 MiB，atlas 的滤波扫描线至多 2 MiB，完整组不超过 64 MiB。

这些是显式算法预算，不是 OS 进程内存隔离。生产作业仍应运行在现有有界进程/容器中。任何失败不返回部分对象。

## 证据与未完成工作

回执绑定规范政策、item 三基础对象、每张实际纹理和全部输出 SHA-256；固定 `DERIVED_ONLY`、`sourceSemanticsVerified=false`、`publicationApproved=false`。

仍需真实源 producer 或单独审核的应用政策提供：完整装备→纹理域、帧矩形与顺序、性别/衣服变体、hair/wing/dye/buff 最终值、层顺序、特殊效果与修复、selection/versionLabels。一次通过消费 validator 不证明这些事实正确；传入 sourceHashes 也不认证任意配方。

## 原创测试

```sh
PYTHONPATH=src python -m unittest discover -s tests -p test_player_assembler.py -v
TERRARIA_CONSUMER_ROOT=/PATH/TO/viewer-app PYTHONPATH=src python -m unittest discover -s tests -p test_player_assembler.py -v
```

覆盖 fresh RGBA 像素、共同裁切、14 帧去重、palette/真 RGBA 两路径、二进制 alias、修复和 item hash；坏政策、源域、坐标、JSON、数字、循环、深度与取消拒绝。实际应用 validator 和有界 inflate 路径已用原创输入验证。不复制真实游戏图片、旧私有基线或已导出的玩家 catalog。
