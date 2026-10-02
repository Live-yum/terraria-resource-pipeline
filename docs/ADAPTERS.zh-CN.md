# 可信适配器与 110 项覆盖合同

## 已实现与未验证的边界

本仓提供原创通用合同、管理员注册接口、Linux bubblewrap 命令计划、有界进程执行、规范包校验，以及原创合成工具的端到端测试。不包含游戏程序集、贴图、反编译代码或私有提取器实现。

默认 `AdapterRegistry()` 为空。上传者不能提供命令、工具路径、额外参数、网络选项、环境变量、可信哈希、覆盖策略或发布地址。未知游戏版本、未登记输入散列或未安装 OS sandbox 都明确阻止执行，不回退到宿主进程运行。

当前开发环境的 bubblewrap 无网络命名空间探测失败（NETLINK_ROUTE 权限限制），所以**没有完成真实 OS 隔离运行认证**。`tests/test_adapters.py` 的 `FakeToolTestBackend` 仅存在于测试文件，运行本仓原创小型 Python 生产器，验证编排/协议/故障处理；结果标记 `TEST-ONLY-NO-OS-ISOLATION`。不能把这项测试宣传为已运行 Terraria 或已证明网络隔离。

只有合成版本 0.0.1/0.0.2 有可运行的完整示例策略。真实游戏版本需要管理员独立审查、安装和固定生产器、依赖、完整 ID 域和字段语义；静态 ID/语言提取不能认证全部默认属性、动态 tooltip、地图运算或人物绘制规则。

## 对外 Python 接口

```python
from resource_pipeline.adapters_synthetic import write_synthetic_package
from resource_pipeline.adapters import validate_normalized_package

binding, profile = write_synthetic_package(private_output, revision=1)
report = validate_normalized_package(private_output, binding, profile)
assert report["complete"]
assert report["submanifestCount"] == 110
```

合成策略有 107 个必需子项，以及 3 个由服务端策略固定原因的 N/A：单独 Buff 图标、legacy pixel catalog adapter、可选图片 gallery。测试成功只证明合成合同管线；不代表现实游戏覆盖。

真实私有生产器的管理员接口：

```python
registry = AdapterRegistry()
registry.register(AdapterSpec(
    adapter_id="operator-reviewed-producer",
    adapter_version="pinned-build-id",
    profile=operator_reviewed_profile,
    tool_root=installed_private_tool,
    tool_sha256=approved_tool_tree_digest,
    command=("/usr/bin/python3", "-B", "/tool/entry.py"),
    approved_server_sha256=frozenset({approved_server_file_digest}),
    approved_client_sha256=frozenset({approved_client_tree_digest}),
    approved_metadata_sha256=frozenset({approved_metadata_tree_digest}),
    requires_client=True,
    requires_metadata=True,
))
result = TrustedAdapterRunner(registry).run(
    "operator-reviewed-producer",
    SourceInputs(game_version, server_file, client_directory, metadata_directory),
    fresh_private_job_directory,
)
```

变量必须来自操作者的服务端配置。此示例没有登记任何真实游戏版本，也不能直接照抄其示意变量运行。生产 Web API 不应暴露 `register`、`CoverageProfile` 构造或 `SandboxBackend` 注入。

## 生产器协议

注册的固定命令被追加 `--request /inputs/request.json --output /output`，通过参数数组执行，无 shell。request 只含：

- `schema_version`、服务端计算的 `binding`、固定 `profile_id`
- `/inputs/server.bin`、唯一 `/inputs/client`、`/inputs/metadata`
- 若启用 legacy shadow，则有 `/work/shadow`；工具不得依赖宿主 sibling 仓库布局

生产器只能生成新目录中的数据：

- `resource-bundle.json`：现有紧凑资源 envelope，来源角色/版本/哈希与真实输入一致
- `coverage.json`：本合同的 110 个逻辑子项及证据位置
- `evidence/*.json`：逐项行表，每行具有独立 ID 与策略规定的字段
- 规范 PNG 或明确允许的数据文件，均按引用哈希验证

`coverage.json` 的绑定包括游戏版本、source kind、server 文件 SHA256、client 内容树 SHA256、metadata 内容树 SHA256、adapter ID、adapter 版本与工具内容树 SHA256。树哈希定义为按相对 POSIX 路径排序的 `[{path,bytes,sha256}]` canonical JSON 的 SHA256；路径、内容或字节数变化都会改变哈希。输入哈希来自读取真实字节，不能取上传清单中的声明值。

metadata 根必须包含 `source-binding.json`，精确记录 `game_version`、`server_sha256`、`client_sha256`；同时整个 metadata 树必须命中管理员认可的哈希。因此旧 metadata 不能只换一个 source 标签便冒充新 server/client。声明本身不是来源认证，可信根仍是管理员审核的整包哈希。

现有服务器包没有完整客户端贴图时必须提供同版本 client Content 或已审核且有来源证明的 decoded 图输入。未经审核的 legacy 图、缩略图、UI atlas 裁图不能自动升级为 original-client 证据。

## 14 族、110 逻辑子项

`contracts.REQUIRED_SUBMANIFESTS` 是唯一枚举；逻辑子项可以共享物理文件，但每项独立校验。

| 族 | 子项数 | 示例验证面 |
|---|---:|---|
| items | 13 | identity、attributes.schema/defaults、tooltip.templates/resolved、图片来源、placement/equipment |
| tiles | 12 | 多 map option、原生 layout/style/alternate/random、frame、说明来源 |
| walls | 8 | 原生 traits、多 map option、放置关系、贴图/缩略图 |
| paints | 5 | input RGB、混色语义、coating、真实支持状态 |
| npcs | 8 | signed net ID、persistent ID、bestiary membership/type、图标裁切 |
| buffs | 6 | ID 空洞、描述、正负分类、item 外键、可选图标 |
| prefixes | 5 | 0/实体 ID、multiplier、eligibility pool、例外 |
| player | 16 | 身体/装备纹理、index 映射、frame/layout/repair、dye、render capabilities |
| worldgen | 9 | 应用 schema/revision、默认值、pass/seed/chest membership、source field map |
| markers | 4 | selector、source crop、缩略图、应用分类 |
| ids | 4 | namespace、aliases、counts、unsupported types |
| locales | 5 | 两种语言、keys、fallback、unresolved references |
| map | 6 | 所有 tile/wall option、liquid/background、paint/light/coating/special case |
| pixel | 9 | stable 集合/排除、candidate 顺序与分组、SRGB/TXCI、codec、legacy/optional |

服务端 `ClaimSpec` 对每项规定：

1. `expected_ids`：管理员固定的权威 ID 域；整数与字符串 ID 不混同，可用负数；不能用表行数替代逐 ID 校验
2. `fields`：严格字段 schema，支持 object/array/enum/nullable；漏字段、多字段、类型错误均失败
3. `foreign_keys`：对实际已验证证据行的外键；不能只证明目标域里理论上存在该 ID
4. `file_fields`：路径/hash/PNG 格式和真实像素尺寸校验，拒绝父目录 symlink
5. `allowed_origins`：例如 server-static、server-runtime、client-original、decoded-client、ui-atlas-crop、texture-alias、derived、application、external-reference；synthetic 不能用于真实策略
6. `required` 与 `not_applicable_reason`：由服务端确定。上传者不能把必需项自报 N/A 或改写策略理由

consumer catalog 各族的 ID 域也绑定到同一策略，防止完整证据旁边附上缺记录的 catalog。`unsupported` 必须列明理由；实际覆盖缺失返回明确 `missingIds`。最终 `complete` 是对所有必需子项计算的输出便利值，从不接受上传的 complete 布尔值作为证明。

该通用校验器验证结构、引用和来源链，不会自动证明一个 number 是正确的 Terraria 地图颜色或 tooltip 已穷尽所有动态上下文。真实版本策略还必须固定字段语义、原生 ID 根、公式/adapter 版本、允许的上下文与明确 unsupported 行为，并通过版本针对性测试。完整 profile 应限制 originals 的 origin，不能允许 atlas crop 代替原图。

## 隔离、只读与失败行为

- 原工具/输入先做受限完整清单，随后逐字节复制到新 job；不用 git worktree、不写旧 `.git`、不用可写 hardlink。snapshot 再与认可哈希对照，防止校验与复制间内容改变
- bubblewrap 对 snapshot 输入和工具树使用 `--ro-bind`，对 output/work 使用独立写挂载；`--unshare-all` 包括网络隔离，`--clearenv` 后仅注入固定无凭据变量
- 不挂载宿主 home、root、workspace。管理员 runtime_roots 只能包含审核过的非秘密运行时依赖树，不应包含 git/CDN key、云凭据或数据库配置
- 需要旧工具自动 build/XNB cache 的写操作时，`use_tool_shadow=True` 提供私有 byte-copy shadow。管理员命令必须明确使用该目录，并独立固定实际依赖；不得让子程序猜测其它游戏版本或宿主仓库路径
- 有 wall-clock timeout、RLIMIT_CPU/AS/FSIZE/NPROC、输出文件数/总字节 watchdog；退出后杀进程组，输出/错误流丢弃，不把异常路径/秘密日志透传给 Web
- watchdog 是周期性检测，不能代替生产 cgroup/磁盘 quota；RLIMIT_NPROC 按 UID 计数且特权环境可能弱化。生产应使用专用低权限 worker、cgroup、真实磁盘配额与部署级隔离验收
- 任何进程失败、sandbox 不可用、来源/版本/hash 不符、路径逃逸、必需图片丢失或外键错误都阻止批准结果。失败保留隔离的 BLOCKED job 供受权运维诊断，不发布、不切换 current、不重试到更宽权限
- Runner 不含 publication 功能。公开发布进程只能读取重新验证过且进入 allowlist 的规范产物，不能读取 private inputs/tool/shadow；整棵 legacy 输出禁止直接发布

## Catalog / API 集成注意

`validate_normalized_package` 的 `expected_binding` 和 `profile` 必须由 server registry 与实际输入字节生成。不要解析上传 `coverage.json.binding` 后原样作为 expected 参数，也不要从上传的 required 列表创建策略。

若 catalog 将证据压缩成内容寻址对象，发布清单必须保留验证过的每一子项的证据对象/hash、profile ID、adapter 与输入绑定，consumer 才能检查来源与字段。只发布 resource-bundle、丢弃证据，然后保留 `complete=true`，会丢失合同证明。

旧原型的“14 个数组非空”只能表达小目录的结构覆盖。必须与本合同的细粒度覆盖分别显示；对无注册策略的真实包显示 unsupported/blocked，不得假装为完整提取成功。

## 验证

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_adapters.py' -v
```

测试覆盖全部子项、精确 ID 缺失、字段类型/外键、图片丢失/尺寸、artifact hash、origin 不可升级、绑定不符、symlink 父目录、上传者自报 N/A、默认拒绝 raw game、tool drift、metadata/server/client 不同源、fresh output、sandbox 缺失、timeout/容量/错误脱敏，以及 fake trusted tool 实际子进程规范化往返。
