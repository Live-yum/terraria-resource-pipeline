# Terraria 资源流水线

六项目接入、本地真实后端提取、审核发布演练和整应用离线验收的最新结果见[交付记录](docs/DELIVERY.zh-CN.md)。资源发布目标已切换为 AList /WeChat/resources；后端上传，微信客户端通过后端返回的链接直接下载，H5经后端下载以满足浏览器跨域要求。正式频道由管理页审核确认后发布。原图片仓库已恢复线上原版。

独立 Go CLI 用标准库完成安全解包、全部 XNB/PNG 纹理转换、去重压缩、元数据分片、RGB 预计算、差异审核与无凭据发布清单。固定 Mono 助手在无网络 Linux 容器内调用真实游戏逻辑；不依赖 Python、NuGet、Hook、GUI 或外部 Go 包。

输入是一个 ZIP，包含同版本完整服务端发行目录及客户端 Content/Images，可带外层文件夹。官方多平台发行包优先选 Linux。只发布游戏来源数据，Wiki/TEdit/人工补充不迁移；无页面消费者的音频、字体、effect 记录在私有 inventory。遇到新游戏结构不支持或必需资源缺失时明确失败。

## 真实验证

1.4.5.8 完整 ZIP 为 668,310,970 B。15,123 张源纹理全部转换、检查，6,195 件物品中英名称/属性/基础及默认 UI 说明、前缀/Buff、图鉴、地图/油漆、TileObjectData、NPC/人物/装备/坐骑信息均来自真实服务端和 Content。运行游戏自己的纹理初始化，并检查实际消费者的绘制依赖，公开 13,203 张逻辑纹理和 5,238 条人物绑定。36,692 条人物 DrawData 采样计划只作为后端私有对照，客户端用源帧本地组合。

当前压缩候选在 299,000,000 B 容器硬限制下完整成功，公开 25 个元数据族；最新真实后端调用 Go + Mono 合计 RSS 峰值 **250,716,160 B**，cgroup 含 charged 文件缓存峰值 **299,003,904 B**，无 OOM。上传到完整审核共264,413ms；JVM累计RSS219,009,024B另列，不计作提取器RSS。逐阶段时间、RSS、Go 堆和 cgroup 均写入私有报告，历史重复提取与旧发布方式的测量记录见 [验收记录](docs/REAL-VERIFICATION.zh-CN.md)。

当前公共对象传输 **18,331,762 B**，PNG与压缩元数据本体 **18,115,832 B**，比最初 72,525,981 B 下载量减少约 74.7%。12,669 个唯一 PNG 通过 256 个 `.zip.gz` 图像包下载；PNG 逐像素无损、SHA 去重，公共 CDN 不重复存裸图。候选 manifest SHA 为 `042f843054abf79b47aa7b85375c058887ea8a90adab4018742e819adde27ab0`。66种坐骑由真实客户端模式初始化导出87贴图槽位与尺寸，官方4种无独立图的滑轮鞋保留为空；不按名字猜坐骑层。私有上传、源程序集和实测产物不提交到本仓或公开 CI。

上述字节数是公开对象口径；客户端还下载、保存版本清单和少量状态，首次完整下载约 **18.54 MB**，完整持久安装约 **18.33 MB**。

动态世界/装备上下文说明、任意人物组合及 GPU shader/RenderTarget 不能由有限采样穷举；capabilities 明确边界。用户已选择客户端本地组合适配。资源版本与 WASM 引擎版本分开，游戏修改存档格式或绘制算法时仍可能需要更新程序。

## 构建

Go 1.23+；提取需要 Linux Docker；审核使用 Go 标准库 HTTP，发布复用 viewer-boot 的 AList 连接。已确认服务器架构为 amd64，Mono 在镜像内。

```sh
go test ./...
go vet ./...
CGO_ENABLED=0 go build -trimpath -ldflags='-s -w' -o trp ./cmd/trp
docker build -t terraria-resource-pipeline:local .
```

无法获取 Go 构建镜像时，先本地交叉构建，再用 Dockerfile.prebuilt。Windows 将 Go 加到 PATH 后运行：

```powershell
./scripts/build-linux.ps1 -Architecture amd64
```

可信宿主版 trp 与容器版使用相同源码。公开 CI 只运行原创 fixture，不下载官方游戏输入。

## 受控提取

以下 Linux 示例由管理员准备私有目录与只读上传。整个 Go + Mono 在同一受限容器；每次 output 必须是尚不存在的新 attempt。输入只读、容器无网络/Git凭据。

```sh
install -d -o 10001 -g 10001 -m 700 /srv/trp/jobs/example /srv/trp/jobs/example/tmp
# 后台已完成上传校验，upload.zip 挂载前设为 0444。
docker run --name trp-example --network none --read-only \
  --cap-drop ALL --security-opt no-new-privileges --pids-limit 128 --cpus 2 \
  --memory 299000000 --memory-swap 299000000 \
  --tmpfs /tmp:rw,nosuid,nodev,size=16m \
  --env GOMAXPROCS=2 --env TMPDIR=/job/tmp \
  --mount type=bind,src=/srv/trp/uploads/upload.zip,dst=/input.zip,readonly \
  --mount type=bind,src=/srv/trp/jobs/example,dst=/job \
  terraria-resource-pipeline:local extract \
  --input /input.zip --output /job/attempt-1 --timeout 30m
```

stderr 是逐阶段 JSON 事件，stdout 是结果。退出 0=ready、4=incomplete、1=失败、2=参数错误。每次 .private/report.json 保存源 inventory、缺失、阶段时间、Go 堆、50ms /proc 子孙 RSS 合计及内核 cgroup peak；Mono 另报实际进程 VmHWM。采样可能漏过瞬时尖峰，容器硬限制约束总占用。Windows 直接运行只有 Go 堆测量，不能当全流程内存验收。

ZIP 上限为上传 2 GiB、展开 4 GiB、单文件 1 GiB、50,000 条目、中央目录 16 MiB。路径逃逸、重复/大小写冲突、链接、加密、CRC/长度错误、越界均失败。纹理和公共对象另有尺寸/展开预算。

## 审核与确认发布

viewer-boot 使用已配置的 AList 连接，将内容寻址对象写入 `/WeChat/resources`。微信客户端先请求同一后端域名的 `/admin-api/viewer/resources/download-link?path=...`，获得 `{code:0,data:{url}}`，再直接下载可信HTTPS签名链接的文件；后端只传小段链接元数据。AList token和密码只留后端，签名链接不写入持久资源身份。公开路径只允许频道、release 和 SHA 对象，不开放私有存档或任务文件。

真实百度直链的H5浏览器跨域测试出现CORS拦截；用户明确选择H5使用`/admin-api/viewer/resources/file?path=...`字节代理，微信固定使用直链。该平台分支不是下载失败后偷偷回退；两者共享SHA校验和离线安装。`/file`也保留给后台Go快照与校验。微信开发者工具已真实验证默认UA下载及SHA，部署还需配置API和下载主机的合法域名。

```sh
trp verify --candidate /srv/trp/jobs/example/attempt-1 --memory-report /srv/trp/jobs/example/verify-memory.json
trp snapshot --source-url https://www.terrariav.xyz/admin-api/viewer/resources/file \
  --output /srv/trp/jobs/example/baseline-1 \
  --memory-report /srv/trp/jobs/example/baseline-memory.json
mkdir /srv/trp/jobs/example/review-1
trp review --candidate /srv/trp/jobs/example/attempt-1 --baseline /srv/trp/jobs/example/baseline-1 \
  --output /srv/trp/jobs/example/review-1/review.json \
  --memory-report /srv/trp/jobs/example/review-1/memory.json
# 管理员查看摘要和完整 review-changes.ndjson，确认 reviewSha256。
trp prepare-publication --candidate /srv/trp/jobs/example/attempt-1 \
  --baseline /srv/trp/jobs/example/baseline-1 \
  --review /srv/trp/jobs/example/review-1/review.json \
  --approve-review-sha256 <确认的审核SHA256> \
  --output /srv/trp/jobs/example/publication-plan.json \
  --memory-report /srv/trp/jobs/example/prepare-memory.json
```

`snapshot` 输出必须是新目录，只有明确的频道 404 才表示首次发布。超时、鉴权失败与坏数据不会成为空基线。审核 schema 2 绑定候选、基线 manifest 和原始频道字节 SHA；旧 Git 审批必须重新审核。CLI 不写远端，也不使用 Git。

后端复验清单与审批，流式上传 objects、release，读回核对后最后更新频道。持久日志与共享工作目录锁用于中断恢复；未知异步任务结果必须阻止后续发布，不能盲目重发频道。正式存储应只由一个受管 publisher 更新；不依赖 AList 提供分布式 CAS。原始 ZIP、程序集、审核与日志不进入公开资源目录。

## 项目接入

实际 [CDN/全量离线合同](docs/CDN-RESOURCE-CONTRACT.zh-CN.md)、[页面资源清单](docs/APP-RESOURCE-INVENTORY.zh-CN.md)、[后端/管理页调查](docs/BACKEND-ADMIN-INTEGRATION.zh-CN.md)、[原生/CDN调查](docs/NATIVE-CDN-INVENTORY.zh-CN.md)、[人物来源合同](docs/PLAYER-RESOURCE-CONTRACT.zh-CN.md)。

独立提取器、viewer-boot、viewer-admin、AList 发布协议和 app/TerraWasm 已接入。小程序“我的 → 更新资源”持久下载所有资源，全部校验后原子激活；失败保留旧版，活动离线集不受普通缓存清理/LRU影响。生产部署和审核发布步骤见[交付记录](docs/DELIVERY.zh-CN.md)；微信实体手机整包断网验收仍未执行。

internal/xnb 和 internal/derived 保留算法许可/来源。tools/RuntimeExtractor/verify_probe.py 仅为可选开发验收，不是生产依赖。旧 Python/FastAPI 合成演示已删除，可从 Git 历史查阅。
