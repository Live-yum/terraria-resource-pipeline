# 小程序共享材料发布合同（草案）

方块、墙壁、油漆只保留一份基础数据。世界编辑、地图与像素画读取同一版本；像素白名单只记录 ID，混色和 RGB 索引引用同一基础对象的原始 SHA-256。

## 原子对象组

- `materials.base`：JSON，方块/墙壁/油漆基础行
- `materials.rules`：JSON，材质、变体、帧形状与 frameImportant 规则
- `pixel.catalog`：JSON，基础 ID 白名单、筛选/混色来源与版本
- `pixel.rgb`：binary，预计算 RGB 索引的原始二进制

四项必须同时存在、下载并校验成功才能切换。清单 schemaVersion=1，gameVersion、sourceBinding、objects 和 releaseId 为固定字段。sourceBinding 保存服务端 SHA-256、Content Git 树 SHA、消费者代码 commit；这些标识本身不证明提取语义或版权许可。

releaseId 是除 releaseId 本身之外清单的规范 JSON SHA-256：键排序、紧凑分隔、UTF-8、整数长度。客户端另固定清单整体 SHA-256 和不可变 Git commit，不能把内容哈希当来源认证。每个对象固定路径、存储/原始长度与 SHA-256，支持 identity/gzip。单对象最多 32 MiB，整体解压最多 64 MiB。所有派生对象的 gameVersion 和 baseSha256 必须匹配基础层。JSON 数据不通过执行模块或脚本载入。

Python 打包入口：`scripts/build_consumer_release.py`。输入为已审查的四个私有文件，输出必须为新目录；不会发布或覆盖已有目录。默认 identity，网络层可独立压缩；gzip 输出同时校验压缩和解压对象。日志仅输出大小和哈希。

`tests/fixtures/consumer-release-golden.json` 是原创合成跨语言向量，验证 Python 打包器和小程序加载器对清单的同一理解，不含真实游戏素材。

## 部署门槛

本合同是传输与一致性校验，不会放开现有完整提取/发布门禁。首次启动没有已验证缓存时，下载失败须返回明确资源不可用错误；不能默默读取旧内置游戏数据。缓存仅允许清单固定且哈希有效的版本。新版本必须整组成功后一次切换，失败保留可识别旧快照，主动回滚仍需指定已批准版本。

现有资料迁移导出和原始 XNB 重新提取必须分别记录来源。真实资源不得进入公共仓库或公共 Actions 制品。真实私有输入验证、发布位置及小程序配置 pin 尚需完成后，迁移草案才能部署；不能以合成测试通过代替这些验收。

## 其他固定消费者组

同一打包器还接受服务端固定的组白名单，通过 `--group` 选择：

- `items`：`items.catalog`（基础）、`items.rules`、`items.categories`，均 JSON
- `player`：`player.presentation`（JSON 基础）、`player.walk`、`player.atlas`（binary）
- `worldgen`：`worldgen.choices`（JSON），在语义负载中绑定精确共享材料/物品依赖，没有本组的 baseSha256

新组使用重复的 `--object ROLE=PATH` 参数提供私有原始文件。材料原有四个命名参数仍兼容。每组只能包含规定的完整角色集合，不能混入另一组、漏角色或让上传包自定义校验规则；保持 32/64 MiB 限额。组校验由调用方显式选择，材料默认协议和黄金向量不变。

这里只验证传输与引用绑定。各组字段、ID、帧、布局、混色和跨组依赖需由对应语义适配器/消费者另行验证；独立组的通过不构成全游戏跨组原子发布证明，也不能绕过原完整发布门禁。
