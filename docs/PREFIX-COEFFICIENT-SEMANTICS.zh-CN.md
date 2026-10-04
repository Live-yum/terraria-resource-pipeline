# 前缀九个系数的有限源码证明

`prefix_coefficient_semantics.py` 对固定 Windows client/server 的 `Terraria.Item.TryGetPrefixStatMultipliersForItem` 做一个完整限定的 out 参数证明，不执行游戏代码。

## 证明目标与不包含的结论

目标仅为 `dmg/kb/spd/size/shtspd/mcst/crt/tagdmg/arpen` 九个最终 out 值；方法的 return bool（前缀是否有效）与第十个 out 值（价格倍率）不在本证明中。不能把 coefficientModelComplete 当成全前缀 compatibility 或整个 items 组完成。

输入全文件 hash、方法 token、精确签名和 IL hash 都固定。独立 `PrefixID.Count` 初始化器必须是“整数字面量 → 唯一 Count 字段 → ret”，无调用/局部变量/异常区域，并校验声明 stack。真实两份来源的 Count 均为 98。

## 有界数据流

方法前段只允许读取 prefix 整数、加载 byref 参数、字面量赋值和向前的整数比较/跳转。逐个枚举固定 Count 域，所有路径都必须在同一价格计算边界以空栈到达，且九个 out cell 全部已明确赋值。未知调用、对象字段读取、缺失赋值、错误 ref 类型、非有限常量、回跳和 stack 超限均拒绝。系数保留 IL 中的精确 binary32 值，没有执行浮点价格乘积或猜测人类十进制文本。

对后段做独立的 typed CFG 检查，遍历两侧分支，要求每个合流点栈形相同、全部边向前，且只有价格 out cell 可以写。可读取的 Item 字段仅为 damage/useAnimation/mana/knockBack；唯一允许的调用绑定到可信核心库身份的 `System.Math.Round(double)`，没有 ref 参数。其数值结果不求值，只用纯调用的类型与无 ref 写入性质证明九个系数不被尾段改写。

边界前提是正常方法体调用、非空正确类型 receiver、各 byref out cell 互不 alias 且不与 receiver 字段 alias、没有并发修改。构造器/类型初始化和调用方的行为另行审计；不是最终 runtime snapshot。

## 实际验证

固定 client 与 server 都产出 98 组九系数映射，两边表摘要相同；后段 typed CFG 覆盖 196 条指令。完整记录与路径保存在私有证据中，未把游戏系数表提交仓库。

`RawEvidenceProducer` 自动按实际 source hash 运行此 stage；未知源明确 UNSUPPORTED_PROFILE。API/manifest 只保存受控摘要（Count、scope、状态与证据 hash），不复制记录、IL 路径或未来私有字段。完整提取、发布、runtimeSnapshotUsable 仍保持 false。

这项证明可与独立 [饰品加成](ACCESSORY-PREFIX-SEMANTICS.zh-CN.md) 和 [初始前缀池](PREFIX-POOL-SEMANTICS.zh-CN.md) 一起使用，但仍不补齐最终 item gameplay、本地化 loader、item eligibility 组和用户环境运行结果。

```sh
PYTHONPATH=src python -m unittest discover -s tests -p test_prefix_coefficient_semantics.py -v
PYTHONPATH=src python -m unittest discover -s tests -p test_accessory_prefix_producer.py -v
```

原创 PE 测试包括 finite domain、所有 out 初始化、两条返回分支、尾段写入目标攻击、错误核心方法/身份、非有限 literal、回跳、ref 类型和 stack/预算/取消。测试不包含真实游戏数组或程序集。
