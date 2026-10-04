# 数据式像素组装器

`src/resource_pipeline/pixel_assembler.py` 从已经独立审核的 `materials.base` 和版本化应用策略派生 `pixel.catalog`、`pixel.rgb`。实现只使用 Python 标准库，不加载游戏程序集，不启动 CLR，不调用游戏初始化器，也不需要编译器。

## 严格边界

- 本模块解决的是像素候选展开、指纹和最近 RGB 索引的派生，不是游戏地图颜色的提取或真实性证明。
- 输入的 `verified_base_sha256` 必须与原始基础 JSON 字节相符。这个检查只证明输入身份；操作者仍须独立验证基础颜色、版本、来源与发布权利。
- 策略必须是服务端维护并审核的策略，不能把上传内容自称的策略当作授权。应用提交绑定记录在证据中，模块不会擅自联网推断某个提交或白名单已获批准。
- 输出证据始终为 `sourceCompletenessEstablished: false`。生成成功不能关闭未解决的地图颜色、材质规则或其他真实来源语义门禁。
- 本模块不生成、补齐或批准 `materials.base` / `materials.rules`；消费者仍须以完整原子组审核、打包和验证。这里只返回两个像素角色，不写磁盘、不提交、不发布。
- 测试只有原创合成数据；没有复制旧私有目录、白名单、固定 RGB 索引或真实游戏资产。

## 输入和 API

```python
from resource_pipeline.pixel_assembler import ALGORITHM, assemble_pixel_resources

result = assemble_pixel_resources(
    reviewed_base_bytes,
    game_version="1.2.3.4",
    verified_base_sha256=reviewed_base_raw_sha256,
    policy={
        "schemaVersion": 1,
        "policyId": "reviewed-app-policy-v1",
        "gameVersion": "1.2.3.4",
        "consumerCommit": reviewed_app_commit_40hex,
        "algorithm": ALGORITHM,
        "tileIds": reviewed_ordered_tile_ids,
        "wallIds": reviewed_ordered_wall_ids,
        "paintIds": reviewed_ordered_paint_ids,
    },
)
# result.objects: {"pixel.catalog": JSON bytes, "pixel.rgb": binary bytes}
# result.catalog / result.rgb / result.evidence: structured access
```

当前唯一算法标识是 `terraria-app-rgb-squared-f32-v1`。策略 schema、算法、游戏版本、完整应用提交、策略名称和三组有序白名单均校验。白名单不能为空，不能重复，必须属于基础层；油漆 ID 仅允许 1–30。基础层只接受 `tiles`、`walls`、`paints`，每行 `[id, name, "#rrggbb", optionalLabel]`，ID 不重复且处于 uint16 范围，颜色必须是六位十六进制。

输出证据绑定原始基础字节、规范化策略、应用提交、候选指纹、目录字节及完整 RGB 字节的 SHA-256。证据不包含运行时间等不确定字段，因此相同输入得到相同输出。

## 与应用一致的候选与最近色语义

实现对应应用以下公开算法合同文件，不依赖旧资源数据表：

- `shared/game/material-resource-contract.mjs`
- `shared/data/color.js`
- `scripts/generate-stable-rgb.mjs`
- `scripts/stable-rgb-transform.cpp`

候选展开顺序为 `[无漆, ...paintIds] × [...tileIds, ...wallIds]`。白名单给定顺序原样保留，不会自动按 ID 排序或合并同色候选。颜色混合逐步匹配 JavaScript `Math.fround` 与 `Math.floor`，包括 29 号漆的通道交换后值及 30 号漆墙壁减半行为。

指纹是以下候选记录数组按 JavaScript `JSON.stringify` 等价格式生成的 ASCII 字节 SHA-256；记录属性顺序固定为 `rgb`、`paint`、`wall`、`order`。不能用会按键名排序的通用规范 JSON 代替。

最近色距离是 RGB 三通道平方欧氏距离。距离相同时：

1. 无漆优先
2. 仅在墙壁偏好索引中，墙壁优先
3. 原候选顺序优先

墙壁偏好是同距离的排序规则，不是“只允许墙壁”的过滤。重复 RGB 的不同材料和不同漆候选保留各自索引。

## 精确算法与 SRGB 格式

采用三维可分离平方距离变换。整数抛物线下包络直接处理相交点，恰好等距时用上述总排序决定边界，不使用浮点几何、近似最近邻或随机采样生成索引。

先在有候选的蓝色平面内计算红、绿两个方向的距离；最后沿蓝色轴直接合并两种偏好的包络片段。不存在对 16,777,216 个颜色逐一遍历所有候选的笛卡尔积，也不需要保存最终完整颜色立方体。测试中的有限尺寸入口仅用于独立穷举；公共 API 固定生成完整 256³ 索引。

二进制布局严格使用小端序：

- 12 字节头：`SRGB`，uint32 候选数，uint32 游程数
- 65,537 个 uint32 目录项，首项为 0，末项为总游程数
- 每条 `(R,G)` 扫描线沿 B 递增，至少一条游程且最后终点为 255
- 每个游程 5 字节：uint8 蓝色终点、uint16 普通候选、uint16 墙壁偏好候选

连续相同候选对合并为一个游程。输出覆盖 65,536 条扫描线和全部 16,777,216 个 RGB 颜色。

## 资源限制与失败行为

默认限制如下；调用方可以调低，不能超过实现硬上限：

- 基础 JSON 16 MiB；解析前检查容器深度、数量、字符串字节数，再验证行和字段
- 基础条目 131,072 行；候选最多 65,535
- 单个 SRGB 输出最多 32 MiB，与消费者传输合同一致
- 工作内存保守估算 384 MiB，包含占用的蓝色平面、输出拼接、候选和基础输入估算
- 累计工作计数 3 亿；墙钟 180 秒，最多可配置 600 秒

`checkpoint` 可用于外层取消。预算和取消会在解析及各包络批次之间检查。超过体积、内存估算、工作量或时间限制均抛出异常；调用方不会收到部分索引或伪造回退颜色。内存预算是算法分配量的保守估算，不是 OS 级 RSS 限制；生产作业仍应使用现有进程/容器内存限制。

## 验证

普通单测（无需应用仓库、Node、编译器或真实游戏输入）：

```sh
PYTHONPATH=src python -m unittest discover -s tests -p test_pixel_assembler.py -v
```

覆盖独立暴力最近色参考的有限立方体穷举、随机整数包络、恰好等距、同色候选、无漆与墙壁排序、完整扫描线布局、完整颜色空间抽样、所有合成候选颜色、确定性、指纹顺序以及输入/策略/预算/取消拒绝。

实际应用跨语言验证：

```sh
PIXEL_ASSEMBLER_APP_ROOT=/path/to/reviewed/app \
PYTHONPATH=src python -m unittest discover -s tests -p test_pixel_assembler.py -v
```

这个明确启用的测试在临时目录只写原创 JSON，调用真实应用的基础原子组 validator、候选指纹及 7,680 组颜色混合向量，随后运行应用已有 Node/C++ 生成器，并逐字节比较整个 SRGB 输出。编译器只用于该独立测试参考，不属于 Python 生产组装器。测试没有设置应用路径时明确标记 skipped；不能把未运行的跨仓验证声称为通过。
