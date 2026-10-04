# 饰品前缀加成：真实源的有限字段变换

`accessory_prefix_semantics.py` 对固定 Windows 1.4.5.8 client/server 的 `Terraria.Player.GrantPrefixBenefits` 做完整方法形状证明。这个方法为 778 IL 字节，不调用其他方法；其语法只有按 Item.prefix 判断的分支和对 Player 字段加字面量。它不需要加载游戏程序集或运行 Player。

## 精确接受条件

- 输入 SHA-256、owner/name、method token、签名 SHA-256 和完整 IL SHA-256 均绑定已审查 profile。
- 实例方法必须是 `void (Terraria.Item)`，无局部变量、无异常区域、唯一末尾 ret；声明 MaxStack 至少 3。
- Item.prefix 必须是 Item 自有的非静态 byte 字段。每个条件只接受该同一字段与唯一 byte 范围整数字面量的比较；未命中分支必须准确跳到下一条件或最终 ret。
- 更新只能是 `this.field = this.field + literal`，read/store 必须同 token、同 Player owner、同 int32 或 float32 类型；重复字段名、显式重叠布局、只读/静态/特殊存储、额外调用/写入/跳转均拒绝。
- 条件内只允许已定义消费分组：defense、maxMana、三个相同 crit delta、四个相同 damage delta、moveSpeed、meleeSpeed。不接受缺一类、值不一致或未知字段。
- int32 加法明确按 unchecked 语义建模。浮点值保留真实 binary32 字面量；百分数投影 n 仅在源字面量逐字节等于 `float32(n/100)` 时允许，不任意四舍五入。
- 非空、类型正确、无并发字段修改的 receiver/argument 为前提；不把增量误称为最终 Player 属性。

全方法覆盖后，全部 256 个 byte prefix 输入都有明确效果：已列条件执行一组字段增量，其余没有变更。参数别名、内存破坏、外部并发和装备是否可使用此前缀不在证明范围。

## 已核实的真实结果

对已提供的两个固定 PE，仅以字节解析核对：各产生 19 组消费增量、12 个绑定字段，两端投影 SHA-256 相同。真实行和完整证据保存在私有 job evidence 中，不提交仓库。

这替换的是旧生成脚本中的饰品前缀硬编码加成块。weapon-prefix multiplier、前缀池/组/eligibility、Item.SetDefaults 和整个 items 组仍需独立完成。

## 自动生产接入

`RawEvidenceProducer` 在 server/client 源验证后自动尝试此固定模型。未知 source hash 输出 UNSUPPORTED_PROFILE，绝不根据路径或上传标志选择模型。支持源的证明失败会使作业失败，不静默回退。

完整结果写到私有 `server-N-accessory-prefix.json` / `client-N-accessory-prefix.json`；adapter manifest 与 API 只保留受控摘要、数量和证据 hash，不带前缀值、字段表或未来新增的私有字段。

`methodModelComplete=true` 仅代表这一个有限字段变换。`complete/publishable/runtimeSnapshotUsable` 与整个资源组完成/发布门禁仍为 false，不能把本证据冒充完整 producer certificate。

## 验证

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_accessory_prefix*.py' -v
```

测试使用原创 PE：完整 typed case、分支目标/未知调用/错误 receiver/store/重复域/field layout/MaxStack/浮点百分比变异、预算与取消；生产接入验证源身份/角色、私有字段不进入摘要、错误不生成 adapter manifest，以及完整发布门禁仍关闭。
