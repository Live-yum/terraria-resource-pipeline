# 真实 bubblewrap 隔离验收

`tests/test_adapters.py` 的 fake backend 只验证编排与故障处理，不证明 OS 隔离。
独立 workflow `.github/workflows/sandbox.yml` 使用 GitHub 托管 Ubuntu 22.04 runner、
Ubuntu 官方 apt 仓库的 `bubblewrap`，以及本仓原创合成数据，运行真实隔离验收：

```sh
python -m pip install -e '.[test]'
python scripts/ci_sandbox_probe.py
```

必须已经安装 `/usr/bin/bwrap`，并且执行环境允许其创建所需命名空间。
脚本不使用 fake backend，不回退到宿主执行，不调用 sudo，不改 AppArmor、sysctl、
网络或宿主安全设置。CI 只在安装官方 apt 包时使用 sudo，实际 probe 以 runner 用户执行。

## 实际验证内容

- 默认空注册表拒绝未知 adapter，且不创建执行工作区
- 用生产 `BubblewrapSandbox.plan` 实际启动独立命名空间；启动失败保留诊断
- 通过生产 `TrustedAdapterRunner` 执行固定、散列锁定的原创工具，而不是只检查命令参数
- net、mount、PID、user 命名空间与宿主不同；只有 loopback 接口且无 IPv4 路由
- 一个由宿主先验证可连接的本地 TCP listener 无法从 sandbox 连接；随后宿主再次验证可连接
- TEST-NET-1 地址的 UDP connect 返回无网络路由，不发数据包、不使用 DNS 或公网服务
- 对 `/`、`/usr`、`/inputs`、`/tool`、`/dev` 创建目录返回 EROFS
- server、client、metadata、request 与 producer 文件的 chmod 返回 EROFS；这证明只读挂载，而不只是原文件 0400 权限
- 宿主临时哨兵及 home/root/workspace 不可见，合成环境变量不泄漏；output、work、tmp 可写，tmp 实际落在 watchdog 覆盖的 work 树
- 真实工具产出全部 110 个合成子项，通过规范包、来源绑定和完整性校验
- timeout 终止持续写入的真实 sandbox 工具，返回后 heartbeat 不再变化
- output 总字节、tmp 总字节、路径数量超限均由生产 watchdog 阻止，结果标记 BLOCKED 且无 result.json
- 内核 RLIMIT_FSIZE 对超大文件返回 EFBIG，文件不超过上限；无可发布结果
- 验证生效的 RLIMIT_AS/CPU/FSIZE/NPROC/CORE 值；各轮运行后原始 server/client/metadata/tool 的内容散列均不变

所有输入、工具和产出都在临时私有目录中生成；没有游戏资产、上传程序集、原有仓库、
外部 CDN、凭据或真实提取器。生产器只依赖 sandbox 内的系统 Python 标准库。

## 报告和失败解释

报告为 `test-results/sandbox/report.json`，Actions artifact 名为
`real-sandbox-synthetic-evidence`，保留 7 天。每项明确标记 PASS、FAIL、BLOCKED 或 NOT_RUN：

- exit 0 / PASS：本次 runner 上的全部真实测试通过
- exit 1 / FAIL：已运行的测试断言或生产 runner 行为不满足要求
- exit 2 / BLOCKED：真实 sandbox 无法启动，例如命名空间被执行环境禁止；其余未执行项仍为 NOT_RUN

FAIL 和 BLOCKED 都会让 CI job 失败。没有 `continue-on-error`、条件跳过或把权限错误记为成功。
安装失败或 probe 根本未启动时，job summary 明确显示 NOT RUN；不能因此宣称隔离通过。
当前开发容器此前出现 NETLINK_ROUTE 权限限制，必须以具体 runner 的本次报告为准。
如果 Actions 也禁止命名空间，应保留 BLOCKED，交由受权部署运维选择允许此隔离方案的环境；
不能通过放宽宿主安全设置、去掉 `--unshare-all` 或转为普通进程“修复”测试。

## 证明边界

通过只证明该次 Ubuntu runner、所安装 bubblewrap 和当前生产 runner 对原创合成工具的行为。
不等于真实 Terraria 版本支持，不验证游戏字段语义，不认证生产部署或恶意内核漏洞防护。
RLIMIT_AS/CPU/NPROC 检查值，不做内存耗尽、CPU 洪泛或 fork bomb。
文件总量/数量 watchdog 为周期性检测，可能在被终止前短暂超过阈值，不是硬磁盘 quota。
生产仍需专用低权限 worker、cgroup、磁盘配额、审核过的非秘密 runtime roots 和部署级验收。
