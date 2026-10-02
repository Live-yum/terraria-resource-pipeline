# 服务端 / 客户端双来源预检

这条流程只接收和检查输入，不提取游戏资源、不执行上传程序集或脚本，也不发布任何对象。当前没有真实生产器连接到这个入口；即使输入都完整且版本可信，任务仍以 `BLOCKED` 和 `NO_TRUSTED_ADAPTER` 结束。合成演示是另外的入口，不会被用作真实输入的回退结果。

## 浏览器与 API

页面的“真实输入配对预检”允许分别上传服务端 ZIP 和同版本客户端 Content ZIP，也可以先上传一份查看缺失项。

`POST /api/raw-jobs` 接收 multipart 字段：

- `server_file`：可选服务端 ZIP
- `client_file`：可选客户端 Content ZIP
- `declared_version`：可选版本提示，例如 `1.4.5.0`

接口返回 HTTP 202 与持久任务 ID。`GET /api/jobs` 返回任务摘要；`GET /api/jobs/{id}` 返回完整的逐文件清单。未公开本机路径选择接口，不能通过文件名或表单让服务器读取任意宿主文件。

每份来源保留实际接收字节的 `archiveSha256`、`archiveBytes`、显示文件名、独立的 `versionEvidence` 和验证后 `inventory`。清单包含相对路径、文件字节数、文件 SHA-256 和展开总量。这里的清单“已验证”只说明安全 ZIP 解包与字节哈希成立，不能解释成内容正确或游戏资源覆盖完整。任务明确返回 `executedInput=false`、`extractionComplete=false`。

## 版本证据

文件名、目录中的版本号、表单提示和包内 JSON 都不是可信版本证据。默认版本策略为空，因此普通上传会显示 `VERSION_UNVERIFIED`。

部署操作者可以在 Python 服务配置中注入 `TrustedSource(role, archive_sha256, game_version)`；只有实际 ZIP SHA-256 与角色同时命中独立审核的记录，才显示 `operator-pinned-archive-sha256`。这只是精确包的版本认证，不能认证解压内容足以完成全资源提取。重新压缩同一内容会改变 ZIP 哈希，需要重新审核，不能自动采用包内声明更新认可记录。Web API 不提供登记或修改此策略的入口。

当前预检不启动 .NET 静态检查器。未来如使用静态程序集版本信息，必须由固定的第一方/操作者审核工具只读解析；它本身不能证明客户端 Content 的来源和同版本对应关系，更不能执行程序集来猜测版本。

## 限额与失败行为

- 两份 ZIP 合计最多 512 MiB；每次读取最多 1 MiB，以真实读取量计费，不信任声明大小
- 两包安全展开合计最多 2 GiB、最多 50,000 个目录/文件条目，单文件最多 128 MiB，压缩率上限 300
- 校验危险路径、大小写冲突、父子冲突、链接、特殊文件、重复项、ZIP 损坏；在解包前重新核对持久源 ZIP 的大小和 SHA-256
- 上传保存到唯一任务目录；POSIX 目录权限 0700，原始 ZIP 权限 0600，不以可写硬链接共享输入
- 第二份上传失败时，清理本次任务的全部半成品，不碰其它任务；正常提交失败不会留下已排队任务
- 解包失败清理该来源的部分输出；已安全完成的另一份清单可以保留，以便明确查看问题
- 中断任务标为 `INTERRUPTED`；服务重启不自动运行或发布。重复处理同一任务会拒绝，主动重新提交则分配不同任务目录
- 原始输入、清单和任务库只在私有工作目录中；公开 `/cdn/` 只读取已发布 Git 对象，真实预检任务无法进入发布状态

主要阻塞码包括 `MISSING_SERVER_INPUT`、`MISSING_CLIENT_INPUT`、`VERSION_UNVERIFIED`、`SOURCE_VERSION_MISMATCH`、`DECLARED_VERSION_MISMATCH`、`INVALID_ARCHIVE`、`NO_TRUSTED_ADAPTER`。界面直接显示中文原因，完整字段和文件清单在折叠详情中按需读取。

## 后续真实提取验收

先取得合法且同版本的服务端与客户端输入，独立登记版本根与来源哈希，再接入已审核且 OS 隔离验收通过的真实生产器。之后还要完成字段语义、完整 ID 域、外键、图片来源及消费页面验收。双 ZIP 预检通过不能替代其中任何一步。

详见 [资源设计与真实验收](RESOURCE-DESIGN.zh-CN.md) 和 [可信适配器合同](ADAPTERS.zh-CN.md)。
