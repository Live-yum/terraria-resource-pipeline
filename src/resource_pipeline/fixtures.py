"""Original synthetic input; contains no game code, sprites or names."""
from io import BytesIO
from pathlib import Path
import tempfile
import zipfile
from .adapters_synthetic import SYNTHETIC_SERVER, write_synthetic_package
from .security import atomic_write, canonical_json, read_json


def demo_archive(revision: int = 1, incomplete: bool = False) -> bytes:
    if revision not in (1, 2):
        raise ValueError("Demo revisions are 1 and 2")
    names = {"items": "合成物品", "tiles": "合成方块", "walls": "合成墙壁", "paints": "合成油漆",
             "npcs": "合成生物", "buffs": "合成状态", "prefixes": "合成前缀", "player": "合成人物帧",
             "worldgen": "合成世界选项", "markers": "合成标记", "pixel": "合成像素查找", "map": "合成地图颜色", "locales": "合成本地化", "ids": "合成常量表"}
    archive = BytesIO()
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        write_synthetic_package(root, revision, omit_claim="walls.traits" if incomplete else None)
        envelope = read_json(root / "resource-bundle.json")
        families = envelope["families"]
        for family, records in families.items():
            for record in records:
                record["name"]["zh-Hans"] = names[family] if record["id"] == 1 else "新增合成物品"
                record["description"] = {"zh-Hans": "原创测试数据，不是游戏提取结果"}
                record["images"] = ["images/same-pixels.png" if family == "player" else "images/a.png"]
        families["tiles"][0]["attributes"].update({"mapColor": [40, 180, 150], "stable": True})
        families["walls"][0]["attributes"].update({"mapColor": [20, 90, 75], "stable": True})
        families["player"][0]["attributes"].update({"frameWidth": 8, "frameHeight": 8,
                                                    "frames": [{"x": 0, "y": 0, "durationMs": 100}]})
        if incomplete:
            del families["walls"]
            # This lie is intentional: the server's fixed profile still requires walls.
            envelope["required_families"].remove("walls")
        atomic_write(root / "resource-bundle.json", canonical_json(envelope))
        atomic_write(root / "synthetic-input.txt", SYNTHETIC_SERVER)
        for name in ("a.png", "same-pixels.png"):
            atomic_write(root / "images" / name, (root / "images/synthetic.png").read_bytes())
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as out:
            for source in sorted(root.rglob("*")):
                if source.is_file():
                    info = zipfile.ZipInfo(source.relative_to(root).as_posix(), date_time=(2020, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    out.writestr(info, source.read_bytes())
    return archive.getvalue()
