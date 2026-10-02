# Terraria 资源流水线

独立的中文上传、提取、变更审核和 CDN 更新演示。**当前是持续开发中的原型，不是完整游戏版本适配器。** 原有 Tdecoder 及其子仓库保持只读，本仓没有游戏程序集、贴图、用户存档或私有项目源码。

## 已可运行的合成端到端流程

1. 前端下载原创合成 V1/V2 ZIP，或上传同格式输入
2. 后端限制 ZIP 路径、链接、重复名称、解压体积和比例
3. 按服务端固定策略校验 14 类、110 个资源子项的 ID 域、字段 schema、外键、图片与来源证据；当前可执行策略只包含原创合成版本
4. 图片无损规范化，跨页面/版本按内容散列去重；资源目录和共享字符串表分别 gzip 压缩
5. 展示新增、修改、删除、缺失资源及输出摘要
6. 用户确认当前摘要后，真实创建 Git 提交并推送到**本地 bare Git CDN 模拟仓库**
7. 客户端验证清单/对象哈希、按需加载，更新失败保留原版本；IndexedDB 持久缓存有容量限制，刷新后可恢复已验证版本并离线读取已缓存资源

不完整资源、过期确认、审核后篡改和未审核对象会阻止发布。上传原始游戏包不会执行其中的程序集或脚本，也不会被假装标记为完整提取成功。

## 本地运行

需要 Python 3.12+ 与 Git。

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test]'
uvicorn resource_pipeline.api:create_app --factory --host 127.0.0.1 --port 8765
```

打开 `http://127.0.0.1:8765`。私有上传、任务库和模拟 CDN 都在 `.runtime/`，已从 Git 排除。演示仅绑定回环地址，不能直接当公网管理后台部署；生产需要接入既有认证、权限、配额和独立作业服务。

```sh
python -m unittest discover -s tests -v
node --check web/app.js && node --check web/cache.js
```

## 真实输入配对预检

页面另有服务端 ZIP / 客户端 Content ZIP 双上传入口，记录各来源真实 SHA-256 与安全文件清单，按两包合计执行上传、解包和条目限额。缺少客户端、版本未验证、版本不符和缺少可信适配器会显示明确中文阻塞项。原始 ZIP 不执行、不发布，也不会回退为合成成功。

版本提示与包内元数据不作为证明；默认未登记真实来源哈希、未接入真实生产器。即使操作者登记了同版本源包，当前入口也只做预检。详见 [双来源预检](docs/RAW-PREFLIGHT.zh-CN.md) 和 [资源设计与真实验收](docs/RESOURCE-DESIGN.zh-CN.md)。

## 已有 PNG 输出预检

已有桌面工具提取的 PNG ZIP 可独立检查图片哈希、尺寸和像素重复，不执行上传代码。此入口仍不能证明真实版本、语义覆盖或发布权利；暂未接入网页上传/发布。见 [私有 PNG 导入检查](docs/DECODED-IMAGES.zh-CN.md)。

## 静态程序集检查器

`tools/AssemblyInspector` 使用 .NET `PEReader` 与 `System.Reflection.Metadata`，只读取 ID 常量和中英嵌入本地化 JSON，不执行类型初始化或上传代码。公开 CI 用带抛异常初始化器的原创合成程序集证明这一点。

```sh
dotnet run --project tools/AssemblyInspector -- /private/path/TerrariaServer.exe /private/output/metadata.json
```

静态 ID 和语言资源**不等于**运行时物品默认属性、动态 tooltip、地图规则和人物绘制语义。检查结果明确 `complete=false` 并列出尚未覆盖的内容，不能直接用于完整发布。

## 接下来必须完成的真实输入适配

- 将已整理的资源消费清单与 110 子项协议绑定到真实版本的 ID、字段和值语义；目前合成校验不能证明真实游戏提取完整
- 服务端包与同版本客户端 Content 的双输入与双源哈希
- 已安装可信提取工具的独立、无网络、只读输入适配；绝不由上传文件指定命令
- 物品分片详情、动态说明上下文、图鉴持久标识、人物帧来源、地图多选项色与像素 RGB 索引
- 原图/图集裁图/别名来源区分、真实资源发布权利审核
- 生产作业恢复、管理员主动回滚、真实资源与多用户权限的完整浏览器验收

当前合成浏览器验收包括 V1/V2 上传、差异审核、确认后发布、刷新后缓存恢复、已缓存资源离线读取、下载内容损坏时保留旧版本和窄屏布局。尚未下载的资源离线不可用。完整游戏语义不在这些测试的证明范围内。

本仓不会自动推送既有 `terraviewer-images`。真实发布需要操作者在私有环境配置目的地和已有授权；公开 Actions 仅处理合成测试数据。

详见 [范围与边界](docs/SCOPE.zh-CN.md) 与 [可信适配器协议](docs/ADAPTERS.zh-CN.md)。

## 参考

- [FastAPI 文件上传](https://fastapi.tiangolo.com/tutorial/request-files/)
- [.NET PEReader](https://learn.microsoft.com/en-us/dotnet/api/system.reflection.portableexecutable.pereader)
- [MonoGame 内容管线](https://docs.monogame.net/articles/getting_to_know/whatis/content_pipeline/index.html)
