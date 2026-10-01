"""Original synthetic input; contains no game code, sprites or names."""
from io import BytesIO
import zipfile
from PIL import Image
from .models import FAMILIES
from .security import canonical_json, sha256


def demo_archive(revision: int = 1, incomplete: bool = False) -> bytes:
    if revision not in (1, 2):
        raise ValueError("Demo revisions are 1 and 2")
    image = Image.new("RGBA", (16, 16), (40, 180, 150, 255))
    png = BytesIO()
    image.save(png, format="PNG")
    names = {"items": "合成物品", "tiles": "合成方块", "walls": "合成墙壁", "paints": "合成油漆",
             "npcs": "合成生物", "buffs": "合成状态", "prefixes": "合成前缀", "player": "合成人物帧",
             "worldgen": "合成世界选项", "markers": "合成标记", "pixel": "合成像素查找", "map": "合成地图颜色", "locales": "合成本地化", "ids": "合成常量表"}
    families = {family: [{"id": 1, "name": {"zh-Hans": name, "en-US": "Synthetic " + family},
                         "description": {"zh-Hans": "原创测试数据，不是游戏提取结果"},
                         "attributes": {"revision": revision if family == "items" else 1},
                         "images": ["images/a.png" if family != "player" else "images/same-pixels.png"]}]
                for family, name in names.items()}
    families["tiles"][0]["attributes"].update({"mapColor": [40, 180, 150], "stable": True})
    families["walls"][0]["attributes"].update({"mapColor": [20, 90, 75], "stable": True})
    families["player"][0]["attributes"].update({"frameWidth": 16, "frameHeight": 16, "frames": [{"x": 0, "y": 0, "durationMs": 100}]})
    if revision == 2:
        families["items"].append({"id": 2, "name": {"zh-Hans": "新增合成物品"}, "attributes": {}, "images": ["images/a.png"]})
    if incomplete:
        del families["walls"]
    envelope = {"schema_version": 1, "game_version": f"0.0.{revision}", "adapter": "synthetic-v1",
                "source_kind": "synthetic", "sources": [{"role": "metadata", "game_version": f"0.0.{revision}", "sha256": sha256(b"synthetic input")}],
                "families": families, "required_families": list(FAMILIES), "warnings": ["此包仅用于公开演示"]}
    archive = BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as out:
        for name, content in {"resource-bundle.json": canonical_json(envelope), "images/a.png": png.getvalue(), "images/same-pixels.png": png.getvalue()}.items():
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            out.writestr(info, content)
    return archive.getvalue()
