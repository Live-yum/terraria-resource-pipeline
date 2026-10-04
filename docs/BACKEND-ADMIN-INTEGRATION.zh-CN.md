# viewer-boot / viewer-admin 资源流水线接入调查

调查日期：2026-10-02。范围仅为 `C:\Users\depths\Desktop\Tdecoder\viewer-boot` 与 `viewer-admin` 的现有实现；本文件是接入设计，不代表已实现。当前 Python/FastAPI 流水线只是原型，不能把它当生产架构前提。目标环境为 Linux，可用 Docker；真实提取器以独立的低内存 Go/C++ CLI 为目标，具体命令协议须在提取器成形后锁定。Mono/.NET 是否可在容器内使用尚待确定。

实施状态：用户已确认Linux amd64和容器内Mono/.NET，旧Python原型已移除。后端真实668MB上传/提取/完整审核、Controller鉴权/租户/权限及管理页断点续传/差异分页/审批竞态均已通过；生产未推送。下文保留调查时事实，实际部署/API以 `viewer-boot/docs/resource-pipeline.md` 为准，验证见[真实验收](REAL-VERIFICATION.zh-CN.md)。

## 现有接入点与限制

| 关注点 | 已有代码与事实 | 对新流程的含义 |
| --- | --- | --- |
| 管理端上传 | `viewer-boot/yudao-module-infra/.../controller/admin/file/FileController.java` 的 `/infra/file/upload` 调 `IoUtil.readBytes(file.getInputStream())`，`FileServiceImpl.createFile(byte[])` 再上传主存储；另有 `/presigned-url` + `/create` 的对象存储直传。`viewer-admin/apps/web-antd/src/api/infra/file/index.ts` 与 `src/components/upload/use-upload.ts` 对应这两种模式，支持上传进度。 | 整包进 JVM 内存，不适合 TerrariaServer + Content 大 ZIP。预签名直传可作为远端对象存储输入，但需要 boot 可私密读取该对象、校验完成及独立任务登记；不能把 `/create` 记录视为内容可信。 |
| 上传上限 | `viewer-boot/yudao-server/src/main/resources/application.yaml` 的 multipart 默认单文件 16MB、请求 32MB；`application-local.yaml` 覆盖 100MB/120MB。 | 大包需专门的流式/分片入口或私有对象存储直传，并同步核对反向代理、容器磁盘及请求限制。只调大上限不能解决内存复制。 |
| 分片能力 | 在 infra/viewer Java 上传代码与 admin 上传组件中未找到业务分片、断点续传、合并接口；S3 客户端出现的 `chunkedEncodingEnabled(false)` 是 HTTP 传输设置，不是分片上传。 | 若真实压缩包大到无法单请求，新增最小协议：创建上传、固定偏移/序号写入临时文件、查询已收分片、按长度与 SHA-256 完成、过期清理。并发/重试都以任务 ID 与固定块校验约束。 |
| 持久任务及子进程 | `viewer-boot/yudao-module-viewer/.../service/cloudsave/WorldGenerationService.java` 已有 `@Scheduled` 轮询、SQL 条件更新 claim、单主机文件锁、`ProcessBuilder` 参数数组、独立 attempt 目录、PID+启动时间恢复校验、超时/日志限额、取消及 `@PreDestroy` 终止。配置在同包 `WorldGenerationProperties.java`，前缀 `yudao.world-generation`。 | 可照此模式新增资源流水线任务，不复用世界生成业务表或配置。Java 控制作业，调用部署时固定的 Go/C++ CLI；上传内容不得控制可执行文件或参数模板。CLI 用输入/输出路径和版本等明确参数交接，返回机器可读 manifest/差异/缺失。 |
| 其他任务先例 | `CloudSaveService.java` 以 `viewer_cloud_save` 行、私有 staging、定时 worker、状态条件更新、超时转失败和清理处理重启；DDL 在 `sql/mysql/viewer_cloud_save.sql`。`sql/viewer_world_share_upload_task.sql` 也有任务状态/重试字段，但本次未发现其对应活跃 Java worker。 | 借用状态与恢复手法，建立独立 `viewer_resource_job` 表；不要把公开资源发布塞进用户云存档任务。数据库记录输入哈希、源版本、状态、审核摘要哈希、提取器版本、操作者、Git 基线/提交及错误；大 JSON/文件留私有作业目录。 |
| 权限/鉴权 | `yudao-framework/.../web/config/WebProperties.java` 自动给 `controller.admin` 加 `/admin-api` 前缀；安全链默认 `anyRequest().authenticated()`。`viewer` 常规 CRUD 如 `CdnInfoController.java` 用 `@PreAuthorize("@ss.hasPermission('viewer:cdn-info:*')")`。`infra/file/upload` 本身没有细粒度 `@PreAuthorize`。 | 新资源 API 用 `controller.admin`，每个读/上传/审核/发布/取消动作都加专用权限；特别是上传与发布不能只依赖登录。后台展示权限 `v-access:code` 仅做 UI 控制。 |
| 管理路由/菜单 | `viewer-admin/apps/web-antd/src/router/access.ts` 从 `accessStore.accessMenus` 映射后端菜单，`views/**/*.vue` 自动进入页面映射；`router/routes/index.ts` 的静态模块不是 viewer 页的主要入口。后端 `yudao-module-system/.../controller/admin/auth/AuthController.java` 的 `/get-permission-info` 从角色查可用菜单；CDN 页面 `views/viewer/cdninfo/index.vue` 用 `v-access:code`。 | 新建 `views/viewer/resourcepipeline/index.vue`、`api/viewer/resourcepipeline/index.ts`，再在系统菜单/角色权限中登记组件路径和按钮权限；仅新增前端文件不会自动出现菜单。 |
| CDN 现状 | `viewer` 的 `CdnInfoServiceImpl.java`/`CdnInfoDO.java` 维护 URL、启用状态、测速及 Redis 最优地址缓存，没有 Git 提交/推送能力。 | Git 发布是新服务边界；不要把 Git 仓库当 `viewer_cdn_info` 记录。现有 CDN 地址可供客户端资源基址选择，但发布目标仓库、分支和凭据须由服务端私有部署配置给出。 |
| 配置/DDL | 项目已有 `@ConfigurationProperties` 类与 `yudao-server/src/main/resources/application*.yaml`；viewer 专项 SQL 位于 `viewer-boot/sql/mysql/`，如 cloud-save、world-generation。所查模块没有 Flyway/Liquibase 自动迁移证据。 | 新增 `yudao.resource-pipeline` 属性（开关、作业根目录、CLI 固定路径、超时、限额、目标仓库路径等）及独立 MySQL DDL；部署先执行 DDL 再开启 worker。凭据用私有环境变量/挂载，不入 Git、API 响应或日志。 |
| 部署 | `viewer-boot/yudao-server/Dockerfile` 基于 `eclipse-temurin:21-jre`，根 `pom.xml` 编译目标 Java 17；`script/docker/docker-compose.yml` 是 MySQL、Redis、server、admin 的 Linux 容器样例，当前 server 没有流水线工作目录/CLI/目标 Git checkout 的挂载。`viewer-admin/scripts/deploy/Dockerfile` 也存在。 | 可在现有 boot 容器内受控启动 Go/C++ CLI，或专门 worker 容器消费数据库队列；先选单 worker 最小实现。需要持久卷保存上传、attempt、manifest、Git checkout；容器重启后仍能读取。不能把开发机 `D:\Code\...` 路径写入生产配置。 |

## 最小实现边界

1. **上传与登记**：专用 `/admin-api/viewer/resource-jobs` API 接收合并 ZIP（内含指定 `server/<version>/...` 和 `Content/...`）或双来源文件。用流复制到私有 staging，边写边限字节与计算 SHA-256；上传完成后原子落盘并写任务行。先验证压缩包路径、链接、重复名、解压总量/比率、版本和必需来源；不要执行包内文件。大包是否使用分片取决于实测大小和代理限制，不先引入通用上传框架。
2. **提取作业**：单 worker 从 `queued` 条件更新为 `extracting`，为每次尝试建立新目录，执行固定 Go/C++ CLI。部署路径、资源预算、超时由服务端配置；输入只读，输出无网络。进程完成后校验退出码、协议版本、manifest 与实际文件哈希，然后生成 `review_required`。失败标记 `failed`/可重试；重启时中断旧进程并弃置未完成 attempt，不能沿用半成品。若有多实例，用数据库 lease/锁或独立 worker，不能只依赖本地文件锁。
3. **差异与确认**：对比当前 CDN Git 基线与候选 manifest，返回新增、修改、删除、缺失及每项来源证据；缺失与不完整明确阻断发布。管理员通过任务详情分页查看。确认请求带候选摘要 SHA-256 和基线提交；服务端重新核对任务状态、摘要、基线与权限，再锁定发布。不能只让前端显示确认弹窗。
4. **Git 发布**：独立 `publishing` 步骤只写白名单产物/manifest，在临时 worktree 提交并推送既定远端；先校验目标分支仍指向审核时基线，冲突退回重审。记录提交 SHA 和推送结果，推送结果不明时查询远端再决定重试，避免重复或跳过。成功后 `published`，app 通过既有 CDN/资源 manifest 更新机制取得新版本；app 端契约由其负责人另行核对。
5. **后台页面**：一页呈现上传进度、任务状态、差异四类列表、失败原因、候选摘要、确认/取消/重试/发布结果；状态由轮询获取即可。按菜单配置 `viewer:resource-job:query/upload/confirm/publish/cancel` 等权限，前后端同名。管理端 API 与页面归 `viewer-admin/apps/web-antd/src/api/viewer/resourcepipeline/` 和 `src/views/viewer/resourcepipeline/`；Java Controller/Service/DDL 归 `viewer-boot/yudao-module-viewer` 与 `viewer-boot/sql/mysql/`。

## 需要先锁定的接口契约

- CLI：`--input <只读绝对路径> --output <空 attempt 目录> --version <受信版本>` 一类固定参数；标准输出只做简短诊断，结构化结果写 JSON。协议须包含 schema 版本、提取器构建标识、双来源 SHA-256、完整性、缺失项、每个对象的相对路径/哈希/来源。只接受相对安全路径，manifest 不能引用 attempt 外文件。最终格式以 Go/C++ 实现和 app/CDN 契约共同确定。
- API：`POST upload`、`GET page/get/diff`、`POST confirm`、`POST publish`、`POST cancel/retry`；确认与发布是否合成单动作由产品交互确定，但后端必须保留服务端审核摘要与二次校验。普通用户存档 API 不参与。
- 保留空间：源 ZIP 与候选产物在审核期间持久保留；发布或取消后按配置保留审计元数据并清理磁盘。应记录清理失败并重试。体积、并发、磁盘预算要以真实 1.4.5.8 包实测定值。
- 部署：Linux 容器提供只读 CLI、持久作业卷和私有 Git 凭据/checkout。若后续需要 .NET/Mono 工具，需等允许与否确定后再纳入镜像；当前设计只要求 Go/C++ CLI。

## 验证路径

调查阶段未改动两个子仓库，也未运行构建。实现后建议先用合成小 ZIP 跑同一 API 的成功、缺失、路径穿越、重复名、压缩炸弹/大小边界和重启中断测试；用本地 bare Git 仓库验证审核摘要过期、基线变化、推送结果未知及重复发布。再用真实包做内存峰值、磁盘峰值、时长与 Linux 容器重启验收，真实资源在语义完整性未通过前不得发布。

可运行的现有检查命令（在各子仓根目录）：

```powershell
cd C:\Users\depths\Desktop\Tdecoder\viewer-boot
mvn -pl yudao-module-viewer -am test
mvn -pl yudao-server -am package -DskipTests

cd C:\Users\depths\Desktop\Tdecoder\viewer-admin
pnpm install --frozen-lockfile
pnpm --filter @vben/web-antd run typecheck
pnpm build:antd
```

`yudao-module-viewer/src/test/java` 目前主要是带 `main` 的 smoke 程序，不能把 `mvn test` 视为已自动覆盖流水线；新增非平凡任务状态逻辑时至少补一个真正可运行的状态/恢复检查。`viewer-admin` 当前上传组件只提供普通进度，分片与后台任务状态需要专页实现。完整验收还取决于真实 CLI、CDN Git 仓库/凭据、app manifest 契约和运营权限配置。
