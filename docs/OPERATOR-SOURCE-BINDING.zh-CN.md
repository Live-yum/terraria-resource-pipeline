# 操作者确认的精确客户端来源绑定

这一步把操作者已明确确认的 `Terraria.exe + Images + Fonts` 同安装关系，绑定到实际读取的字节。它不是厂商数字签名、完整游戏安装验证、语义提取完成或发布批准。服务端版本策略仍须独立审核。

## 信任边界

`ClientInstallationAttestation` 是独立审核的本地管理员配置，不能从上传 ZIP、表单或包内 JSON 创建。Web API 不提供登记接口，默认可信来源仍为空。仓库内的 Windows 1.4.5.8 配置记录本次操作者确认，范围仅限固定提交中的客户端 EXE、Images 和 Fonts。修改提交、目录树、EXE 指针或实际字节，均须重新核实并审核配置。

离线工具执行以下检查，绝不运行输入代码：

1. 按既有 ZIP 安全限制解包固定提交的来源归档；只接受新的私有工作目录。
2. 从实际文件重新计算 Git blob/tree SHA-1，验证整个 Content 目录、Images 和 Fonts 子树。仅支持普通文件模式 `100644`，拒绝链接、特殊文件、未物化的资源 LFS 指针与不受 Git 身份覆盖的空目录。
3. 验证 `Terraria.exe` 的精确 LFS 指针文本及 Git blob SHA-1。单独读取真实 PE 载荷，以大小和 SHA-256 对应指针；133 字节指针不能当成 EXE。
4. 仅把已确认的 EXE、Images、Fonts 放入新客户端 ZIP；重读每个打包文件并核对内容指纹，再测量最终 ZIP SHA-256。不会复制转换器代码、旧 PNG、shader 或音频库到该包。
5. 输出私有 `source-binding.json`，绑定操作者声明摘要、固定来源提交、Git 树、实际 EXE、原始归档及生成包的实际哈希。输出可用于独立审核后配置现有 `TrustedSource`，不会自行修改服务策略。

```sh
PYTHONPATH=src python scripts/bind_client_source.py \
  --source-archive /private/pinned-source.zip \
  --client-executable /private/Terraria.exe \
  --operator-policy docs/source-attestations/windows-1.4.5.8.json \
  --output /private/new-client-binding
```

配置仍是 `TrustedSource(role, archive_sha256, game_version)` 的原合同。精确客户端包 pin 不能用于 server 角色；重新压缩或增改一个文件后不能沿用旧 ZIP pin。不要把单个确认过的客户端包当成服务端与客户端的合并来源。既有 `COMBINED_TEXTURE_SOURCE_UNVERIFIED` 和完整语义门禁完全保留。

## 本次实际字节核实（2026-10-03）

来源 `Live-yum/TConvert@1bf78d08842574d959cec35d566fe95ddeef28f8`：

- Content Git tree：`fc6090241a3b58e6d64a33b23fef48e96c95916f`
- Images：15,123 个文件，31,296,829 字节，树 `0458b4d36d8debec31015c482283686f9aef6450`
- Fonts：5 个文件，27,161,592 字节，树 `8be391c0a56ea71e124295ac58bd421ef3f105ad`
- 真实 EXE：26,597,888 字节，SHA-256 `960a03bff6050cf7be16dfc1a7b19e10fc2c4f8f835a6a3b135a50dd9e6ba2f3`
- 下载归档：109,829,844 字节，SHA-256 `9e4c9c9a110e8ca1cb8eb52e08eb770768650e61cba5520de49406dd9d9b1953`
- 本次生成的私有包：15,129 个文件，展开 85,056,309 字节；ZIP 67,445,913 字节，SHA-256 `cf22bd36dc1ffbf87da2e935b76b066e79a17181cf5881cba00f75b35760a5e7`

以上归档哈希是本次运行的观测结果，不是预填的版本证据或默认服务配置。压缩库/实现改变时生成包哈希可能不同，必须重新计算并核验。实际数据、EXE、XNB、ZIP 和逐文件输出未提交到代码仓库。

当前来源缺少 Sounds；音乐 wave bank、shader 与音频 bank 也不在确认/生成范围。回执明确列为排除项并固定 `fullAssetCoverage=false`。这不能被表述为“全客户端资源提取完成”。

## 来源确认后仍未解决的语义

用实际客户端与已固定服务器字节进行静态读取，声明版本同为 1.4.5.8；66 个观察到的数字 ID 组、84 个嵌入本地化资源哈希一致。这只能支持字节层的比较，不自动证明运行时规则等价。

实际客户端的 item texture alias 分析，以此次核实的 Images 清单为输入，可条件性解析 6,196/6,196 逻辑物品槽位：6,134 个直接纹理文件、67 个别名，解释了 62 个缺少直接文件名的槽位，零未解析槽位，最长链为 2。该结果没有执行像素解码，也没有证明静态初始化后的运行时状态。

下一步应先补齐 SetFactory 构造、缓冲区/缓存生命周期和整个初始化器副作用证明。两个实际 EXE 的四组 dispatch 配方相同，但各保留 34 组未建模调用关系、663 次调用；不能由已解析的局部赋值推断完整初始化器无副作用。之后再组合 Item.SetDefaults dispatch 与后处理。

当前物品阶段仅分析 6,195 个正 ID 中的 32 个，保留 1 个递归调用阻塞、6,163 个未分析 ID；fresh baseline 证明 109 个原始字段，另有 3 个未知字段。命名映射仍有 48 个负物品 ID 与 66 个负 NPC ID 缺口；所选每种语言有 182 个动态说明模板和 5 个 copy-evidence 缺口。已有条件性研究模型覆盖 6,138 个基础定义和 16 个 persistent-ID override，不能把该子能力说成完全缺失，也不能据此称物品语义完整。

所有完整消费合同仍未验收；材料、帧、paint/pixel、NPC/buff/prefix 行为、玩家图层/装备及世界生成/marker 等语义继续待核实。任务继续 `BLOCKED`，`extractionComplete=false`、`publishable=false`。

## 配对预检与当前环境边界

服务端沿用已审核来源链：`Live-yum/TerrariaServerHook@d6c7944d1f190884dcdb3d77c04c19a246d2eada` 的 `server/1458/Windows/TerrariaServer.exe`，Git blob `ea00e07394ba51c64d1d36e27f4c176c81898581`、26,028,032 字节，SHA-256 `d87e3faf08637f6be8882c63e7f11fb7e792b0230006309618473ece0f863e1e`。来源提交/path/blob/大小在已有 CI 静态读取配置中固定，SHA-256 同时用于已审核 research/locale/paint profile。本次由这些核实字节生成服务端 ZIP，实际 7,980,620 字节，SHA-256 `5cd19523455253ad82e963d68b1775279016002f85130ba997ef518f8f81b4f0`；不是依据客户端元数据相等推断服务端信任。

本次私有双 ZIP 预检在登记两份实际归档 pin 后，保留语义不完整阻塞。来源绑定与部分证据封存不能使候选可审核或可发布。

当前云环境的真实 sandbox probe 被内核限制阻止：bubblewrap 0.12.0 启动时报 `loopback: Failed to create NETLINK_ROUTE socket: Operation not permitted`。未修改安全设置、未绕过隔离，也未执行游戏输入。真实像素解码、PNG 校验及运行时隔离/内核限额检查为 NOT_RUN；没有把 CI 的合成测试当成本次真实解码结果。

安全解包与仅仅读取 XNB 头的规划已通过：15,128 个 XNB 均为 Windows v5，其中 15,105 个带 LZX 标志、23 个未压缩。现有策略规划 25 批，声明展开工作量 2,425,684,990 字节，低于原 4 GiB 上限。这是头部/预算规划，不是实际像素计数或转换成功证明。
