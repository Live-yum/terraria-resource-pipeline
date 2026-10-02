# 自动提取原始 XNB 贴图

上传原始游戏 ZIP 后，由服务端自动读取 XNB 并输出 PNG；不要求用户先在桌面手工转换，也不以已有 PNG 库代替转换测试。可以在原有双来源入口分别上传服务端/客户端 ZIP，也可以把服务端和原始 `Content/Images/*.xnb` 放在一个 ZIP 中上传到服务端入口。

## 管理员安装一次

```sh
dotnet publish tools/TextureExtractor -c Release -o /private/texture-tool
export RESOURCE_TEXTURE_TOOL_ROOT=/private/texture-tool
export RESOURCE_TEXTURE_TOOL_SHA256=$(python -c 'from pathlib import Path; from resource_pipeline.adapters import tree_digest; print(tree_digest(Path("/private/texture-tool")))')
export RESOURCE_DOTNET_PATH=/usr/share/dotnet/dotnet
uvicorn resource_pipeline.api:create_app --factory --host 127.0.0.1 --port 8765
```

需要 .NET 10、Linux bubblewrap，以及运行时位于默认只读系统挂载目录（例如 `/usr/share/dotnet`）。环境变量只能由管理员配置，上传参数不能指定转换器、命令或开关。每个任务校验工具哈希、快照输入，使用无网络命名空间、只读输入、时间/内存/文件限制，逐张验证输出 PNG 和来源哈希。隔离不可用时失败关闭，不在宿主机直接处理上传资源。

TConvert 的公开 LZX 解码组件（MIT）和 DXT 解码组件（MS-PL）已集成，保留许可和来源；XNB 数据读取、PNG 写出以及服务端集成为本项目实现。无需旧 Windows GUI、System.Drawing 或游戏程序集。解码器不会执行上传的 EXE/DLL，也不会实例化 XNB 中声明的类型。

## 状态与边界

- `TEXTURES_DECODED`：已自动解码图片，仍未完成整个游戏的语义资源适配
- `NO_XNB_PAYLOAD`：输入包没有 XNB；转换器不能从不存在的纹理字节恢复图片
- `NO_SUPPORTED_TEXTURES`：XNB 均为当前不支持的字体/声音等类型，详情列出跳过项
- `CONVERTER_NOT_INSTALLED`：管理员尚未配置固定转换器
- `BLOCKED`：无隔离、工具哈希不符、损坏/超限数据或解码失败；不允许发布部分结果

支持 XNB v5、RGBA Color、DXT1/3/5、未压缩与 LZX。LZX Intel E8 变换明确拒绝；LZ4、字体图集及声音不是当前范围。无论图片是否成功，完整语义覆盖、版本来源与发布权利仍须独立验证，原有发布门禁不会放开。

**仅含专用服务器的包可能没有客户端纹理。** 这时需要同版本原始客户端 Content，或操作者已授权的原始资源来源。它可和服务器放在同一上传包，转换全程自动完成。此功能不自动登录商店、购买/下载受限客户端，也不从旧 PNG 推断或伪造原始 XNB。

## 实际测试范围

公开 CI 全部使用原创生成的 XNB：RGBA/DXT1/3/5、LZX stored block、真正的 Huffman+LZ 匹配压缩块、18 个 DXT 调色板/透明度/非整块尺寸样例。压缩块实际小于解码数据的一半。另测损坏 Huffman 树、越界匹配、E8、损坏头和容量限制。隔离 CI 测试会调用上传入口自动转换组合包并验证像素，同时确认语义门禁仍阻止发布。

这些合成测试不等同于任何真实客户端全量资源已经转换；完整真实转换验收需要原始 XNB 输入与可信版本记录。

### 像素格式回执

每张新提取图片记录原始 `surfaceFormat`：0（Color/RGBA）、4（DXT1）、5（DXT3）、6（DXT5）。后端校验枚举并重算 `surfaceFormatCounts`。旧版固定工具缺少此字段时仍兼容，但明确计入 `unknown`，不会从 PNG 猜测原始压缩格式。此字段不改变解码逻辑、限额或发布门禁。
