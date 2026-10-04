> 历史测量记录：Git/bare 发布是原方案，2026-10-03 已切换 AList；当前契约以 DELIVERY.zh-CN.md 与 CDN-RESOURCE-CONTRACT.zh-CN.md 为准。

# 真实输入验收记录

完整官方 1.4.5.8 服务端 + Content ZIP 668,310,970 B。Linux amd64、Docker Desktop 29.7.2、2 CPU、无网络、只读根、非 root；容器内存及 swap 同为 299,000,000 B。游戏包/产物不进入公共 CI。

## 27 族基线 attempt-2

15,123 纹理、14,451 唯一 PNG；全部源绑定齐全，67 个物品图片别名及官方 TIles_650 大小写均正确。36,692 人物计划/43,566 唯一操作；requiredMissing/error=0，exit=0。最终前缀/Buff复跑结果见下面 attempt-3/4。

| 阶段 | 耗时 ms | Go/Mono 子孙 RSS 峰值 B |
| --- | ---: | ---: |
| unpack | 5288 | 18317312 |
| identify-input | 3012 | 16191488 |
| textures | 38472 | 60096512 |
| texture-packs | 2349 | 40742912 |
| game-metadata | 122119 | 254140416 |
| metadata-packs | 15530 | 71057408 |
| rgb-candidates | 628 | 41455616 |
| rgb-srgb | 2021 | 153673728 |
| rgb-txci | 1188 | 153014272 |
| verify | 1219 | 33775616 |

总 RSS 254,140,416 B；cgroup peak 299,003,904 B（含 charged 文件缓存与限额分页取整），无 OOM。50ms RSS 与内核计费口径不同，不能互相替代。私有报告 .runtime/reports/native-attempt-2.json。该基线含后来发现的 HWM capability；最终构建将测量只留私有报告并复核确定性。

## 真实审核和本地 Git

TestRealCandidatePublication 验证完整候选/全部纹理引用，生成完整逐条差异，确认后提交并推送临时 bare，验证公共树不含私有文件或重复裸 PNG，再次审核所有域/纹理/RGB 都无变化。没有联系生产 GitHub。

| 阶段 | 耗时 ms | Go/Git 子孙 RSS 峰值 B |
| --- | ---: | ---: |
| verify-candidate | 1121 | 30429184 |
| review-initial | 39728 | 34287616 |
| publish-initial | 54461 | 82444288 |
| review-unchanged | 11608 | 58408960 |

exit=0；RSS 82,444,288 B、cgroup 299,003,904 B。报告 .runtime/reports/publication-memory-2c.json。公共对象 1,107 个、传输 72,452,270 B、安装 68,833,888 B、最大图像包 1,020,114 B。已修复 missing=[] 经 omitempty 成 nil 导致误报审核过期。后续 Git 固定单 packing 线程、32MiB window/16MiB delta cache、禁异步 auto GC，最终复跑覆盖。

## 重跑

go test ./... 与 go vet ./... 验证原创 fixture 的安全ZIP/LZX/DXT/去重/hash/确定gzip/RGB/全量差异/确认篡改及 Git crash/retry/基线。真实候选不默认读取；设置 TRP_REAL_CANDIDATE 后运行 go test ./internal/pipeline -run TestRealCandidatePublication -v，可设 TRP_REAL_MEMORY_REPORT 输出 JSON。正式测量在同一内存受限 Linux 容器，可信测试镜像需要 Git，但无需网络或凭据。

## 最终前缀/Buff闭环与重复提取 attempt-3/4

两次完整 extract 都 exit=0、ready、missing=[]，全部公共 manifest 字节相同，SHA256 **7179c9771b4eaf4ec577c86b1d0a52f7902d8b06028a2f3c8ffe987a994632a0**。新增 97 前缀效果/官方池、400 Buff描述/标志、22,829 物品有效前缀组合；28 个原生 sample.type=0 的旧 ID 和 6 个别名显式保留状态。测量只存私有 report。语言方法异常会使任务失败，不能静默输出空文本后继续发布。

| 最终阶段（attempt-4） | 耗时 ms | Go/Mono 子孙 RSS 峰值 B |
| --- | ---: | ---: |
| unpack | 6165 | 18325504 |
| identify-input | 3699 | 15077376 |
| textures | 43294 | 69251072 |
| texture-packs | 2674 | 40038400 |
| game-metadata | 153996 | 254263296 |
| metadata-packs | 16644 | 70991872 |
| rgb-candidates | 658 | 43970560 |
| rgb-srgb | 2196 | 154787840 |
| rgb-txci | 1321 | 153100288 |
| verify | 1392 | 39415808 |

总耗时 232,039 ms，RSS 254,263,296 B，cgroup 299,003,904 B，无 OOM。另一轮 RSS 250,404,864 B。私有报告 .runtime/reports/native-attempt-3.json 与 native-attempt-4.json；只读增量复核已完成。

最终完整候选与限定 Git packing 参数再次通过真实审核/本地 bare 发布/无变化检查，RSS 峰值 **55,496,704 B**、cgroup 298,999,808 B；阶段 verify 29,396,992 B、review 34,508,800 B、publish 37,265,408 B、无变化 review 55,496,704 B。报告 .runtime/reports/publication-memory-3.json。最终公共对象仍 1,107 个，传输 **72,525,981 B**，安装 **68,907,599 B**。

最终 go test ./...、go vet ./... 和 git diff --check 均通过；Windows trusted trp.exe、Linux amd64 trp 和最后完整 Mono 镜像已实际构建。后端/页面/app验收在各自阶段追加，未执行检查不记通过。

## viewer-boot 真实 Linux 接入

同一个 668,310,970 B 整包，经真实 ResourcePipelineService 的 4MiB 流式上传、SHA 校验、UID 10001 后端、299,000,000 B 提取容器、完整候选验证和差异审核全部通过。最终 release SHA 与独立提取一致；审核生成 212,125 条、146,038,967 B 明细，接口按字节游标读取，未将整份明细载入内存。测试没有确认或执行生产发布。

| 测量 | 字节 / 耗时 |
| --- | ---: |
| 上传耗时 | 10,145 ms |
| 上传至审核完成 | 348,944 ms |
| 提取 Go + Mono 子孙 RSS | 249,544,704 B |
| 提取 cgroup peak（含文件缓存） | 299,008,000 B |
| 宿主审核 Go RSS | 30,556,160 B |
| 测试 Java 后端 JVM HWM | 242,458,624 B |
| 提取与 JVM 各自峰值之和（保守上界，非同时采样峰值） | 492,003,328 B |

300 MB 目标适用于独立提取任务；完整服务器还需要常驻 Java 后端、数据库、Redis 等的内存。私有证据 `.runtime/reports/boot-linux-verification-2.json`。实际执行前发现并修复报告超过 2MiB 的问题：后端只流式选取摘要，跳过私有 16k 文件清单；详情不会泄露该清单。

真实 Controller/Tomcat/方法权限 HTTP 验收随后通过匿名和会员拦截、管理员细分权限、租户隔离、原始字节分片幂等及内容不一致拒绝、真实明细分页与审批三哈希校验。HTTP 夹具仅在 test 目录使用固定虚拟 token，关闭调度；正确审批只写入可丢弃 H2 队列，不会调用 Git。

## CDN 本地公共树与 HTTP 镜像验收

重整后的 release7179c977…全树校验 PASS：1107对象、15123逻辑纹理、14451唯一PNG、256 Store ZIP；传输72525981B、完整安装68907599B。全部对象SHA/长度/gzip解压长度、全部ZIP成员CRC/SHA/PNG尺寸及目录覆盖通过，坏包回归1/1通过。Node标准库检查进程HWM152567808B，私有证据 .runtime/reports/cdn-local-verification.json。

本机loopback静态CDN镜像逐一HTTP下载全部1107对象并校验原始压缩字节SHA与解压长度，报告PASS、72525981B、进程HWM125677568B；report.json返回404。证据 .runtime/reports/cdn-http-verification.json。此检查没有请求生产CDN，也没有Git push。

只读Astra复核发现旧CDN还跟踪.claude本地配置，已取消跟踪但保留本地文件；CI新增完整公共路径白名单，禁止私有报告/配置/重复裸PNG进入公共仓库。旧Git历史保留。

## 用户反馈后的体积优化（完整重跑中）

逐像素无损校验14451唯一PNG，真实源PNG44933854→25314616B，减少19619238B（43.66%）；只对≤256精确RGBA颜色使用索引PNG，大小变大时保留原编码，透明度及透明像素下RGB均保留。报告 .runtime/reports/lossless-png-benchmark.txt；这是独立图片基准，不是最终ZIP/任务RSS。公共绘制样本17445037B改为只保留私有验证，不能作为客户端必需离线数据。新整包299M实测attempt-5进行中，后续填写准确下载量/内存/耗时。

完整attempt-5真实整包重跑通过ready/missing=[]，release67473766ab170344e6fe8f87b7ac505d8ccaa5d7ad0cc18c667a745e6a001171。新公共707对象/24族、15123纹理14451PNG/256包保持全源图片，下载35452482B、安装31834100B；较旧72525981B减少51.1%。RSS249577472B，cgroup299003904B，逐阶段合计216263ms；纹理解码编码58716ms，metadata134614ms，pack3968ms。全Goverify/公共白名单复制/Node全hash及ZIP验证PASS，NodeHWM147812352B。准确证据 native-attempt-5.json、cdn-local-verification-5.json。仍未完成按人物官方依赖过滤全Content，不能宣称35.45MB是必需客户端资源下限。

导出时曾误从--rm已删除容器docker cp，提取本身exit0，随后从保留的原生卷复制私有report及manifest成功，没有重做或丢失提取结果。

## 消费闭包、官方绑定与 gzip 图像包 attempt-7

最终版本1.1.0在299000000B硬限制下整包提取ready、missing=[]、exit0、无OOM。运行游戏自己的LoadTextures绑定，发型和Extra数量取实际数组/ID域，未知接口调用明确失败，成功或失败后恢复游戏静态字段及数组；删掉未被有限绘制样本引用的Extra315会精确失败。公开13203逻辑纹理/12669唯一PNG、5238人物绑定、25元数据族、728公共对象、256图像包；私有oracle不发布，PNG逐像素无损，图像包为确定性gzip包裹Store ZIP。

候选SHA：**768adc807ffc6ba4a777c92c582af0af77014bef2338a3b51746698acd2e0c68**。公共对象下载18311903B；PNG加压缩元数据安装18095973B。客户端另存210776B版本清单和少量状态，首次完整下载约18.52MB，完整持久安装约18.31MB。相较原72525981B公共对象下载量减少约74.8%。

| 阶段 | 耗时 ms | Go/Mono子孙RSS峰值 B | Go堆峰值 B |
| --- | ---: | ---: | ---: |
| unpack | 6747 | 17866752 | 12616048 |
| identify-input | 3412 | 14610432 | 8486464 |
| textures | 65082 | 51007488 | 40850392 |
| game-metadata | 150771 | 267673600 | 13542824 |
| texture-scope | 210 | 40542208 | 24555200 |
| texture-packs | 4604 | 33476608 | 20421128 |
| metadata-packs | 4109 | 74682368 | 65226464 |
| rgb-candidates | 785 | 42565632 | 27558792 |
| rgb-srgb | 1984 | 152412160 | 142877344 |
| rgb-txci | 2948 | 151977984 | 138545672 |
| verify | 974 | 27119616 | 19349496 |

阶段合计241626ms，RSS峰267673600B；内核cgroup累计峰299003904B，包含文件缓存与限额分页取整。独立Go verify通过：2145ms、RSS21602304B、cgroup21876736B。报告native-attempt-7.json、verify-memory-7.json。attempt-6因测试TMPDIR未创建而失败，保留失败记录，不当成完整候选；attempt-7已独立创建专用磁盘临时目录。

Node公共全树检查全部对象SHA/长度、gzip解压长度、全部ZIP CRC/PNG SHA/尺寸/布局与覆盖通过，HWM160796672B；loopback HTTP全部728对象检查通过，HWM101392384B。zip.gz截断/尾部/声明大小门禁与历史raw ZIP兼容回归通过；Go tests/vet和Windows/Linux可信CLI构建通过。

真实候选的独立Linux审核、确认、本地bare Git发布及无变化审核再次通过，未接触生产仓库：verify1088ms/RSS31059968B，review13355ms/40562688B，publish21312ms/43855872B，unchanged7385ms/40808448B。总RSS43855872B、cgroup166346752B，证据publication-memory-7.json。首次测试启动时PowerShell拆分未加引号的-test.run参数，已改为完整带引号参数；正确启动后的43.27s检查PASS。

viewer-boot非root真实Linux上传→提取→校验→审核再次通过，SHA与独立attempt-7完全一致，证明新绑定与压缩产物确定性。135185条/51970664B完整审核明细使用字节游标；上传29257ms、全流程386303ms，提取RSS277950464B/cgroup299003904B，宿主review Go RSS26877952B，Java JVM HWM251789312B。提取与JVM峰值之和529739776B是保守上界，并非同时峰值。宿主审核所在后端容器cgroup累计峰1062342656B含上传/审核文件缓存；独立提取的300MB目标不能作为整个Java服务器的预算。证据boot-linux-verification-7.json，productionPublished=false。

SDK已用真实最终数据通过桌面Node fake WxFS完整安装、无网络冷恢复与全部25族/467分片/121978行读取；安装3.53s/HWM155983872B，冷恢复0.744s/累计HWM194609152B；逐族全量压力读取总体HWM321765376B单列，不冒充手机或单次安装峰值。持久PNG11723318B、元数据对象6372655B、清单与状态210998B，共18306971B。真实浏览器IDB/SW及全部页面迁移仍在验收，手机硬件未验证。

## 动画补齐后的真实候选81df（attempt8）

发现旧候选缺少物品动画裁帧后，调用官方 Main.InitializeItemAnimations/GetFrame(texture,0)，102动画项的 sourceRect 进入轻目录，完整动画属性进入既有纹理引用族。落星75、3581、4068、5644、食物353和普通物品1均以官方输出校验；未知类或越界直接失败，不使用碰撞框猜帧。公共对象18,330,289B，清单210,881B。真实SDK首次下载730请求18,541,255B；持久安装18,325,462B，12,669 PNG共11,723,318B。

完整668,310,970B输入再次无网络299,000,000B/no-swap容器提取ready、missing为空、无OOM。独立Go+Mono RSS278,011,904B，charged cgroup299,012,096B；各stage完整数值见私有native-attempt-8.json。独立Go校验568ms/RSS17,670,144B/cgroup20,844,544B。新镜像c4ea4d3，backend4307cb9。

后端真实Linux resume上传9,438ms，upload→extract→verify→review262,251ms；同输入重新提取生成完全相同SHA81df82a82b8e59c45256c10f0db96f0d3baedfec0bc2c2722d254bc3e65b49ef。提取RSS275,095,552B，JVM单列222,744,576B；两者独立峰值相加上界497,840,128B，不是同一时刻实测值，不声称整个Java服务器小于300MB。后端charged cgroup包括上传缓存最高1,039,163,392B。

本地bare发布完整verify/review/publish/unchanged通过35.49s、RSS41,725,952B；仍未接触生产GitHub。Node完整公共树核验RSS164,777,984B，728对象/25族/所有ZIP CRC/SHA/PNG尺寸通过，loopback HTTP全对象校验RSS101,306,368B。旧未发布候选的33个过时对象已从本地公共树移除，历史源输入/报告保留。

真实Edge IDB安装18.839s；断网重载全25族467包121,978行与3张PNG解码9.963s，普通缓存清理后资源仍可恢复，JS heap20,696,655B。fake WxFS安装3.446s/HWM165,093,376B，冷恢复1.014s；主动读完所有族压力累计324,878,336B是桌面Node模拟压力峰值，不是手机或安装峰值。整应用H5 SW/最终MP包体仍等待人物消费者完成，不能以SDK验收替代最终页面验收。

父代理图鉴/标记消费者定向回归涵盖负NPC别名、自定义头像整图、旧存档未知进度、世界身份、卸载时目录/open/read停止、未来item ID动态检索，以及TileObjectData逐格去padding。真实546图鉴/42实体投影及全部source cell边界通过；图鉴世界缓存仅保留进度，不复制资源名称/源帧元数据。

## 人物偏移补齐后的最终候选91d59b（attempt9）

本次只在已有player-layouts族追加官方Onhand/Offhand帧偏移数组，公共传输增加71B至18,330,360B，不新增资源族。最终SHA `91d59b3cfcf78f4601ed69bfe7609e5d3a6323449f34cfd7d5266010380f1f09`。真实SDK首次网络18,541,327B、持久18,325,534B；12,669唯一PNG合计11,723,318B。

完整输入无网络299,000,000B/no-swap提取ready/missing空/无OOM，Go+Mono RSS271,470,592B、charged cgroup299,003,904B。11阶段合计244,575ms，逐阶段RSS/Go堆/cgroup/耗时见私有native-attempt-9.json。独立Go verify919ms/RSS21,229,568B/cgroup22,589,440B。

真实backend上传8,952ms、upload→extract→verify→review276,937ms，候选SHA与独立提取完全相同。提取RSS268,206,080B，JVM214,609,920B单列；两者峰值相加保守上界482,816,000B，不是同时峰值。审核RSS23,064,576B/9,547ms；后端charged cgroup1,034,371,072B包含上传缓存。没有声称整个Java服务器小于300MB。productionPublished=false。

同候选本地bare Git完整verify/review/publish/unchanged42.79s通过、RSS44,322,816B/cgroup172,662,784B，未访问生产GitHub。公共树全SHA/ZIP CRC/PNG尺寸检查166,141,952B，loopback HTTP728对象全校验123,432,960B。公共工作树已替换9；没有生产提交或推送。

fake WxFS安装9.181s/HWM165,085,184B，冷恢复0.898s；25族467包121,980行压力读取2.720s，总体压力HWM317,812,736B是桌面模拟累计值，不是安装或手机峰值。真实Edge IDB安装19.446s，断网冷恢复+全族读取10.277s；三PNG解码、普通缓存清理后恢复通过，JS heap15,700,613B。全应用H5壳离线冷进和最终MP包体仍待消费者冻结后验证。

## TerraWasm 与真实消费者回归

本地源码提交 `521765d4583a7236285e13a3852c107af2588276` 修复运行时色表天空起点及255级比例，保留浮点worldSurface；地层选择使用游戏无雪基线的dirt/rock变体，未声称实现全部环境雪量。旧编译色表路径仍按原整数规则计算。WSL原生15/15与Node82/82通过，干净隔离源码使用-Os/LTO生成Web WASM285,499B、Node306,004B，身份清单同步viewer-app；未推送。证据 `native-sky-ctest.log`、`native-sky-node-tests.log`。

应用Node集成夹具只包含固定91d59b版本的真实元数据/RGB，测试资源不进入构建。父代理37/37覆盖图鉴、物品/实体标记、官方源矩形、worldgen、实际TMRT/TXCI与6004像素native颜色写入。11,924,663B真实世界WLD无修改保存保持字节相同；流式/整文件MAP完整SHA同为 `940609bffc02dc2a2742eae6b4568e1a3b8e9fd72c023ecc526c88c811524cd3`，独立解析抽样验证天空与方块/墙壁油漆，不只比较两种实现。固定版本下的新MAP纠正旧天空起点及前景油漆语义，因此有意不同于旧内置色表SHA。未放松WLD字节、200MB或已有处理时间门禁；这些Node结果不代表微信设备实测。证据 `app-parent-final-9.log`。

## 官方坐骑尺寸补齐候选042f8430（attempt10）

最终release `042f843054abf79b47aa7b85375c058887ea8a90adab4018742e819adde27ab0`。先建立server初始化基线，再在已绑定真实Content尺寸的作用域内临时运行官方客户端Mount.Initialize；无论成功或异常，恢复Main、Assets、Mount静态字段及数组。66种坐骑全部初始化，87个非空贴图槽身份和实际PNG尺寸精确匹配；57–60四种Roller矿车的Empty槽合法保留，不伪造图片。Go完整性门禁与负测、真实候选87槽校验和Astra只读复核通过。较attempt9仅既有mount-layouts对象增加1,402B，其余24族、纹理目录/PNG/RGB对象SHA不变。

728公共对象18,331,762B；清单210,951B。真实SDK首次网络730请求18,542,798B，完整持久安装18,327,005B；PNG与压缩元数据本体18,115,832B，12,669唯一PNG11,723,318B。图片包11,939,248B、元数据族2,583,109B、纹理目录650,852B、RGB3,158,553B。相较初版72,525,981B减少约74.7%，没有把原小程序局部资源与完整离线资源当成相同覆盖范围。

同一668,310,970B输入，Linux amd64/no-network/no-swap/299,000,000B容器完整提取ready/missing=[]/无OOM，逐阶段记录如下。RSS是Go与Mono子孙进程采样之和；cgroup含文件缓存与分页取整。

| 阶段 | 耗时 ms | RSS峰值 B | Go堆峰值 B |
| --- | ---: | ---: | ---: |
| unpack | 7065 | 17072128 | 12454736 |
| identify-input | 3229 | 13987840 | 8203512 |
| textures | 59756 | 53829632 | 43875712 |
| game-metadata | 152638 | 264871936 | 14561744 |
| texture-scope | 223 | 39997440 | 33743496 |
| texture-packs | 4638 | 31350784 | 22590944 |
| metadata-packs | 4560 | 74256384 | 65388896 |
| rgb-candidates | 816 | 45481984 | 29660448 |
| rgb-srgb | 1985 | 151298048 | 142876336 |
| rgb-txci | 2917 | 151568384 | 138554296 |
| verify | 890 | 31227904 | 16552904 |

阶段合计238,717ms，RSS峰264,871,936B，charged cgroup299,003,904B。真实非root后端重复上传10,341ms，upload→extract→verify→review284,930ms，生成完全相同SHA；提取RSS271,466,496B/cgroup299,003,904B，Java JVM216,113,152B单列。二者各自峰值相加487,579,648B是保守上界，非同时采样；后端charged cgroup1,023,197,184B包含上传/审核文件缓存。300MB限额针对隔离提取任务，未声称整个Java服务器低于300MB。

后端verify569ms/RSS18,161,664B，review前verify601ms/19,804,160B，review9,680ms/27,049,984B；productionPublished=false。本地bare Git完整verify/review/publish/unchanged通过37.23s，RSS44,396,544B/cgroup166,326,272B；四阶段分别843/10,879/18,022/7,341ms。两个测试环境失败保留：第一次运行时镜像不含Git，第二次96MB tmpfs不足；改用真实backend镜像及磁盘TMPDIR后通过，均未被当作生产发布或OOM。

Node全树检查全部728对象SHA/长度、gzip解压长度、全部ZIP CRC/PNG SHA/尺寸与121,980元数据行通过，HWM152,109,056B。Go全仓tests/vet通过，真实候选测试再次校验66/87/4坐骑数据。可信Windows/Linux CLI与runtime/backend镜像已更新。

真实Edge IDB安装16.38s，断网冷恢复+全部25族467分片121,980行8.13s；3PNG解码和清除普通localStorage后恢复通过。fake WxFS安装3.50s/HWM129,318,912B，冷恢复0.884s/累计HWM180,187,136B；逐族全量压力读取2.69s/累计HWM314,134,528B是桌面Node压力值，不代表手机或安装峰值。该验证覆盖SDK，整应用H5壳和最终MP包仍需消费者冻结后验收。

私有证据：native-attempt-10.json、backend-real-10.json、publication-memory-10.json、publication-10c.log、cdn-public-10.json、cdn-size-breakdown-10.json。没有GitHub push。

人物预览精度最终由用户确认：保持原小程序的预览精度，特殊效果可明确标注近似。因此独立GPU Effect交接作为未来精确实现资料保留，本轮只要求官方资源与参数完整、常规图层正常，以及已有本地特殊效果预览不中断。人物Canvas定向真实Edge验收4种翻转、90°旋转/2倍缩放、普通source-over逐字节对照、A0发光和shader callback通过；官方Extra142/151 PNG的非透明覆盖分别676/424，5次resolve与5次release一致。2项Node回归涵盖发光alpha与迟到解码释放。整应用验收仍另行记录。

## 最终整应用离线与人物视觉验收（2026-10-03）

使用最终042f8430公共树和实际H5生产输出，真实Edge持久profile从“我的→更新资源”按钮及原生确认完成18,542,798B/730请求安装，同时缓存119个带摘要的离线壳文件。关闭浏览器进程后重新启动、设置完全断网；SDK读取全部25族467分片121,980行、13,203逻辑纹理目录及3张PNG，普通清缓存后仍可恢复同一完整release。7个基础路由加2个真实世界依赖页面无pageerror；物品详情有109项默认上下文属性，实际导入3,029,192B世界生成960×274地图，图鉴40项/箱子20项展示，真实PLR“烟花”可见人物及“特殊效果近似预览”。这不是只验证SDK或页面文字。

实际DOM在320/390/768px三种宽度向下及向上遍历全部228发型、6,195物品、6,138研究、400Buff，24/24通过，最多108个节点。每项覆盖、真实底部/末行/回滚与静止抖动均检查，没有预置Vue私有数据。完整运行约13分钟是全量双向遍历测试成本；先前无日志长运行不能当成已证明的产品卡死。证据app-h5-offline-full-grid-final.json和continuous-result.json。

冷重开截图曾发现uni H5将128×112人物bitmap重设为85×75，导致裁切。共享paint在每次绘制前恢复源尺寸，包含模拟框架resize回归；修复后最终npm validate11/11通过，980项/965通过/15可选跳过，MP总2,053,766B、主包1,362,309B。随后重新生成最终H5壳并再次实际完整安装、关进程、断网验证全部族/页面/WLD/PLR，24.348s安装，离线JS heap43,121,300B，pageErrors=[]；本轮只改画布尺寸，没有重跑此前已通过的13分钟网格遍历。

再关闭并断网重开最后profile，人物画布128×112、6,111非透明像素，姓名/近似提示正确，无portrait-error或pageerror；已实际查看最终截图确认不再裁切。证据app-h5-offline-viewport-final.json、app-player-offline-final.json/png、app-p0-validation-final.json。35/35额外人物DrawData普通图层及全实际装备槽20帧源矩形检查无跳过，特殊图层仅标记近似，不再因合法特殊装备中断预览。

所有浏览器流量由私有测试拦截到本地相同CDN路径的精确字节，没有推送GitHub。微信分包预载、WxFS原子提交/空间失败/保护缓存有模拟与构建验证，但未进行实体微信手机的本次整包断网验收。生产部署/首次推送等待用户确认，后续游戏资源更新按管理页明确确认发布。
