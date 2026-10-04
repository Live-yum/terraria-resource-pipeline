# 人物效果提取最小交接

2026-10-02，最终范围已确认：用户接受原小程序的预览精度，特殊效果可明确标注近似；本轮不要求实施逐像素 GPU Effect 迁移。以下是保留给未来精度升级的只读调查与方案，不是当前必需未完成项，也不是已实现能力声明。当前以官方资源/规则组合，复用 CPU 染料近似，并修复 A0 发光合成；近似效果不得标称为官方 shader 逐像素等价。

## 已实测的输入

- `C:/Users/depths/Desktop/Tdecoder/TConvert/Content/PixelShader.xnb`：13,808 B，SHA256 `5332a581222f78e9e498efbe044162ccd82f5df286c859d5f522b4536edbc51b`。
- XNB header：`XNBw`、version 5、flags `0x80`（LZX）。使用仓库现有 TConvert LZX 解码器在内存解压得到 102,990 B。
- reader 数量 1：`Microsoft.Xna.Framework.Content.EffectReader, Microsoft.Xna.Framework.Graphics, Version=4.0.0.0, Culture=neutral, PublicKeyToken=842cf8be1de50553`；reader version 0、shared 0、primary 1。
- reader 后 Int32 长度 102,832，后接该长度 effect，余量 0。effect 头 `CF 0B F0 BC 08 02 00 00`，包含 `ArmorTwilight`、`QueenSlime` pass 名及 `FFFF0200` shader token。
- 因此是 XNA4 包装的 D3D9 Effect / `ps_2_0`，不是 DXBC 或 MGFX。MojoShader 的 effect parser 明确识别外层 `0xBCF00BCF`，按 offset 跳过后要求 `0xFEFF0901`。

官方读取证据：[FNA EffectReader](https://github.com/FNA-XNA/FNA/blob/master/src/Content/ContentReaders/EffectReader.cs)（Int32 长度 + effect bytes）；格式证据：[MojoShader effects parser](https://github.com/FNA-XNA/MojoShader/blob/master/mojoshader_effects.c)，`MOJOSHADER_compileEffect`。这些链接是调查时的官方源码，不是对当前 bundled native 的版本锁定。

## 当前可复用 native 边界

服务器 `TerrariaServerHook/server/1458/Linux/lib64/libFNA3D.so.0` SHA256：`a3df551e838ad8a5c148c225962346949950474e7615170103c340d203d314e3`。

`nm -D` 已确认导出 `MOJOSHADER_parse`、`MOJOSHADER_compileEffect`、effect 生命周期入口；binary strings 包含 `glsles` / `glsles3`。设置 `LD_LIBRARY_PATH` 为同目录后，WSL `ctypes.CDLL` 实际装载成功。`ldd` 的 SDL3 依赖由该目录自带 `libSDL3.so.0` 满足。`MOJOSHADER_changeset()` 实际返回 `???`，不能据此自动认定 ABI 版本。

最小方案仍是一个现有 Mono helper 内的解析模块，P/Invoke 上传包随附 native 库，不创建 FNA GraphicsDevice，不调用 GL 编译入口，不新增图形系统服务。生产提取依然在既有无网络、无凭据、总内存限制容器中。

官方 ABI 来源：[mojoshader.h](https://github.com/FNA-XNA/MojoShader/blob/master/mojoshader.h)，重点 `MOJOSHADER_effectShaderContext`、`MOJOSHADER_parseData`、`MOJOSHADER_effect`，以及 parse / compileEffect / deleteEffect 声明。调查时该 header 的 parse 有 **mainfn 第二参数**，不可照用旧签名。

```text
parse(profile, mainfn, tokenbuf, uint bufsize,
      swiz, uint swizcount, smap, uint smapcount, malloc, free, data)
compileEffect(tokenbuf, uint bufsize, swiz, uint swizcount,
              smap, uint smapcount, context*)

context 顺序（全为 pointer / function pointer）：
compileShader, shaderAddRef, deleteShader, getParseData,
bindShaders, getBoundShaders, mapUniformBufferMemory,
unmapUniformBufferMemory, getError,
shaderContext, malloc, free, malloc_data

compileShader(ctx, mainfn, bytes, uint length,
              swiz, uint swizcount, smap, uint smapcount) -> pointer
getParseData(shader) -> parseData*
deleteShader(ctx, shader) -> void
shaderAddRef(shader) -> void
```

Linux amd64 对上列 context 的理论布局为 13×8=104 B；必须先以真实 bundled 库做受限子进程 smoke 确认，不把理论布局写成已验证事实。Mono delegates 使用 Cdecl、保持强引用，struct Sequential/原生对齐，uint32 不得声明成平台 long。FNA.dll 自身 Effect 内包含 effect/param/pass/technique 的 interop structs，可作为该上传包的交叉证据。

本次 `ilspycmd -t Microsoft.Xna.Framework.Graphics.Effect .../Linux/FNA.dll` 已确认：effect 公共前缀依次为 `int error_count / pointer errors / int param_count / pointer parameters / int technique_count / pointer techniques / int object_count / pointer objects`；pass 为 `pointer name / uint state_count / pointer states / uint annotation_count / pointer annotations`；technique 对应 name/pass_count/passes/annotation_count/annotations。effectShader 有 `uint is_preshader`、preshader_param_count/preshader_params、param_count/parameters、sampler_count/samplers、shader pointer。只读取已核对的前缀，不能把 FNA 省略的 native 后续私有字段按托管 struct 大小复制或重新分配。

解析回调只做：compileShader 调用 `parse("glsles", mainfn, ...)` 并持有 parseData；getParseData 回传；delete 按引用计数最终调用 freeParseData。不能空指针跳过被调用的回调。绑定或 uniform 映射回调若在仅解析阶段意外触发，记录并失败。分配器要么配对使用 native 默认值，要么全程同一配对，避免 Mono/native 交叉释放。

**尚未验证**完整 compileEffect 回调链、实际 GLSL 输出、preshader 数量、浏览器/微信编译结果；本调查没有构建新 helper。

## 最小输出与门禁

1. Go 复用现有 LZX，严格读 EffectReader 包装，验证 declared 长度与结尾，把 effect bytes 传给同一 helper；不要把纹理 Decode 的 `ErrNotTexture` 改为吞掉错误。
2. helper 导出必要 pass 到 shader 的关联、转换 GLSL、参数名/类型/默认值/register 映射、sampler/image 关联及状态。按实际 Armor/Hair/Misc 消费闭包选择 pass，不发布无关整个客户端 shader 集。
3. 发现 preshader 必须报告并阻止把该 pass 标记为完整；不得丢掉 preshader 或用零默认值假装成功。未知参数类型、sampler/state、转换错误同样失败。准确支持 preshader 是后续有证据的独立范围，不能在此交接中声称已有。
4. `Program.cs ExportDyeShaders` 当前 `imageBindings=false`。至少补实际 UseImage 的 assetId、所属 effect/pass 与 sampler 关联；仅导出颜色/饱和度不足。普通图已存在不等于知道哪个 pass 用哪张图。
5. QueenSlime 必需 Misc 关联：`DyeInitializer.cs:434–436`，pass `QueenSlime`、image1=`Images/Extra_180`、image2=`Images/Extra_179`。`PlayerQueenSlimeMountTextureContent.cs:14–24`：同 Extra204 尺寸透明 RT，Extra204 白色全图，经该 pass + AlphaBlend 绘制。不要直接以 Extra204 原图宣称最终效果等价。

客户端只新增人物效果所需小模块：用 same ResourceSession 读取 pass+纹理；已有 raw-layer-canvas 的 crop/transform/cache 可复用。统一处理 XNA premultiplied blend、sampler、uniform；当前 Canvas tint 后 source-over 会丢掉 RGB 非零/A=0 的 glow，且尚不处理 op.shader。旧 `render/dyes.mjs` 自注明是参考预览 GLSL 的 CPU 近似，不能作为官方 shader 字节码替代证据。

## 无 GPU 可先完成的范围

源码根均为 `C:/Users/depths/Desktop/Tdecoder/code/`。先实现真实源图 op，再叠加 dye 效果；缺少 dye 不应导致整件装备原图被静默丢弃。

| 范围 | 官方来源与边界 |
| --- | --- |
| 11 个 hair CPU delegate | `Terraria.Initializers/DyeInitializer.cs:153–424`；item1977生命、1978魔力、1979深度、1980前54库存钱币、1981昼夜时间、1982队伍、1983水域/shimmer状态渐变、1984固定色、1985Disco、1986速度、2863照明均值。`Terraria.GameContent.Dyes/LegacyHairShaderData.cs` 明确 shaderDisabled；除2863外最后乘 lightColor。唯一此处 GPU hair 是3259 Twilight。 |
| Mount 8/35/38/45/54/61 | `Terraria/Mount.cs:5561+`：8旋转/初始无beam；35 Extra142；38 Extra151；45 glow抖动；54持物帧；61 CPU颜色。都可由现有slot/Extra实现。61颜色函数含人物名字特例，不能只实现默认HSL却称全覆盖。50基础纹理组合可做，但QueenSlime颜色RT依赖上文pass。 |
| 特殊 Head | `PlayerDrawLayers.cs:2093+`：14/56/114/158/69/180裁剪；270宽度+2；282九帧；109Extra276；292Extra300；288 mount-head Extra284；259兔子多帽/265六行多帽；TV头309屏幕状态。均可生成crop/position/tint op。`PlayerDrawSet.cs:523–640`给真实glow颜色/遮挡。 |
| 特殊 Legs | `PlayerDrawLayers.cs:1457+`：169隐去是官方；60Extra278叠层；140Extra73按16×24/18×26网格、坐骑时隐藏；其他glow、sitting拆分绘制。对应`PlayerDrawSet.cs:747–806`，不能用统一裁剪代替坐姿分片。 |
| 特殊 Wings | `PlayerDrawLayers.cs:655–1118`：22火焰多层、28+Extra38、34/39六帧、48八帧、49/50/47十一帧、51八帧、40十四行多轨道层、45光环/拖尾，都是CPU生成op；染料另应用。站立/飞行可见性沿`Player.ShouldDrawWingsThatAreAlwaysAnimated`。 |
| 翼45/猫车拖尾 | `PlayerDrawLayers.cs:2903+`，Projectile250、最多30/20历史影子。初始无历史自然不绘制；运动后按历史段长/插值产生op。不要永久禁用运动尾迹。 |

PLR 不包含完整世界的 time/light/water/depth/历史轨迹。预览应明确持有初始 scene 与动画状态并可推进；算法采用官方规则，不能假装从存档恢复了不存在的场景数据。finite plans 只核验代表状态，不作为生产任意组合状态表。

## 有界验收与交付顺序

1. Worker2 先完成上表无GPU op：两个方向、20源帧、倒重力、坐姿、同层遮挡、每个特殊分支至少一个官方oracle；增设A0有色glow背景非黑像素断言。原图/帧越界失败而非静默clip。
2. 单helper解析smoke：真实PixelShader SHA、reader/长度；每个消费pass有唯一关联；零parse errors；所有sampler存在；明确preshader结果。损坏长度/未知token负例必须失败，peak RSS仍计入全任务299MB验收。
3. 仅在确认精度范围后实现WebGL。QueenSlime、普通Armor、Twilight、一个多纹理dye逐类固定输入/时间/光照作离屏像素对照，并验证微信和H5。必须同时检查A0 glow、nearest/linear-wrap、方向/源矩形、矩阵和premultiplication，GLSL编译通过不等于像素等价。
4. 同active release离线下载包含所有选中shader对象和依赖纹理；断网冷启动验证。不得另起在线渲染后端、下载在线源码或引入运行时shader远程URL。

本次只新增该交接文档；没有修改 producer、app 或其他人的文件。
