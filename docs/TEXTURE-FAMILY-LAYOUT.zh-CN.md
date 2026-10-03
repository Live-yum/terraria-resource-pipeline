# 主纹理与辅助纹理覆盖

族统计保留 `decodedImages` 作为所有同族文件名匹配的解码数量，同时分别报告
`decodedBaseImages`、`decodedAuxiliaryImages` 和 `unlocatedNameMatches`。
三个数量相加必须等于 `decodedImages`，不会删除已解码文件或更改其哈希。

只有直接位于精确大小写 `Images` 目录中的匹配文件获得 `BASE_TEXTURE_LAYOUT`。
`Images` 下更深层的文件获得 `AUXILIARY_TEXTURE_LAYOUT`；无法定位到此目录布局的
同名文件获得 `UNLOCATED_NAME_MATCH`。压缩包外层目录可以保留，裸文件名不再被当作
主纹理布局的证据。

`idsWithoutDirectImage` 仅使用主纹理布局中的 ID。TileOutlines、Bestiary 等目录里的
同名文件不能填补主图缺口。私有族纹理表保留每个文件及其 `textureRole`，便于后续
按真实游戏加载器规则建立对应关系。

这是布局分类，不是加载器调用、复制映射、帧语义或运行时正确性的证明。所有完整性
和发布门禁仍保持关闭。测试覆盖主图、辅助图、相同 basename、大小写不符和无目录
输入，确认辅助图不会填补主图缺口。
