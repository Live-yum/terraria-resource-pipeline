# TConvert LZX component
LzxDecoder.cs, LzxBuffer.cs and HuffTable.cs are derived from public upstream
https://github.com/trigger-segfault/TConvert at commit
52cbabdbe7cda81a60f2f4ed31f4a71e8dec6043, TConvert/Extract/.
Each file's explicit MIT license notice (Anton Gustafsson, 2014–2015) is retained.
The whole desktop application is not copied or relicensed here.
LzxBuffer/HuffTable upstream blob identities match the user-referenced version.
That fork adds an empty-array initialization; we use the public upstream.
The texture parser, PNG writer, wrapper and tests are independently implemented.
No game assets, Windows UI or game assemblies are embedded.
DtxUtil.cs is derived from public TConvert blob 6836216bd6477e9426fe1986bf0a0b7381f2bfb9.
Its FNA/XNA authors' Microsoft Public License notice is retained, with MS-PL.txt
obtained from FNA-XNA/FNA/licenses/LICENSE. It is used under that file license.

Local modifications: text transport/line endings, strict Huffman tree and symbol validation, tree run bounds, LZX match bounds, explicit Intel E8 rejection and frame-loop cursor correction. These files are not byte-identical to upstream after hardening.
