"""Validate a real RuntimeExtractor result without extra dependencies."""

import hashlib
import json
import sys
from pathlib import Path


def main(root: Path) -> None:
    result = json.loads((root / "result.json").read_text(encoding="utf-8"))
    assert result["protocol"] == 1
    assert result["gameVersion"] == "1.4.5.8"
    assert result["idDomains"]["ItemID"] == 6196
    assert result["idDomains"]["TileID"] == 754
    assert result["idDomains"]["WallID"] == 367
    assert result["mapLayout"]["lookupCount"] == result["families"]["map-palette"]["count"]
    assert result["mapLayout"]["curRelease"] == 326

    for name, entry in result["families"].items():
        path = root / entry["path"]
        assert path.parent == root and path.suffix == ".ndjson", name
        digest = hashlib.sha256()
        count = 0
        with path.open("rb") as stream:
            for raw in stream:
                digest.update(raw)
                row = json.loads(raw)
                assert "id" in row, name
                count += 1
        assert count == entry["count"], name
        assert digest.hexdigest() == entry["sha256"], name

    def find(family: str, wanted: object) -> dict:
        with (root / result["families"][family]["path"]).open(encoding="utf-8") as stream:
            return next(row for line in stream if (row := json.loads(line))["id"] == wanted)

    iron = find("items", 1)
    assert iron["name"] == {"en-US": "Iron Pickaxe", "zh-Hans": "铁镐"}
    assert iron["gameplay"]["pick"] == 40 and iron["research"] == 1
    iron_ui = find("item-ui-tooltips", 1)
    assert any("40% pickaxe power" in line for line in iron_ui["en-US"]["lines"])
    assert any("40%" in line for line in iron_ui["zh-Hans"]["lines"])
    assert iron_ui["en-US"]["error"] is None and iron_ui["zh-Hans"]["error"] is None
    assert result["capabilities"]["defaultUiTooltips"]["failedLocales"] == 0
    assert find("tile-sets", 4)["sets"]["Main.tileFrameImportant"] is True
    torch = find("tile-object-data", "4:0:0:0")
    assert torch["coordinateFullWidth"] == 22 and torch["coordinateFullHeight"] == 22
    assert torch["styleWrapLimit"] == 6 and torch["frameX"] == 0
    assert result["capabilities"]["tileObjectData"]["frameImportantWithData"] == 389
    assert len(result["capabilities"]["tileObjectData"]["frameImportantWithoutData"]) == 23
    rudolph = find("mount-layouts", 0)
    assert rudolph["fields"]["totalFrames"] == 12
    assert rudolph["fields"]["flyingFrameStart"] == 6
    assert result["capabilities"]["mountLayouts"]["populated"] == 66
    assert result["capabilities"]["armorSets"]["domains"] == 16
    assert not result["capabilities"]["armorSets"]["omittedFields"]
    assert find("armor-sets", "Head:38")["sets"]["HidesHead"] is True
    assert find("armor-sets", "Body:85")["sets"]["IncludeCapeFrontAndBack"] == {
        "backCape": 20, "frontCape": 7
    }
    assert find("armor-sets", "Wing:1")["sets"]["Stats"]["FlyTime"] == 100
    assert find("armor-sets", "RocketBoots:6")["domainCountSource"] == "public ID constants max + 1 (no Count field)"
    assert result["capabilities"]["prefixes"]["count"] == 97
    assert result["capabilities"]["buffs"]["count"] == 400
    assert result["capabilities"]["items"]["effectivePrefixPairs"] > 20_000
    assert find("items", 1)["prefixPool"] == "PrefixesForSwords"
    assert 81 in find("items", 1)["eligiblePrefixes"]
    assert find("items", 1)["sampleType"] == 1
    assert find("items", 226)["sampleType"] == 227
    assert find("items", 226)["sampleStatus"] == "alias"
    assert find("items", 2772)["sampleType"] == 0
    assert find("items", 2772)["sampleStatus"] == "unavailable"
    assert find("items", 2772)["eligiblePrefixes"] == []
    assert find("prefixes", 81)["stats"] == {
        "dmg": 1.15, "kb": 1.15, "spd": 0.9, "size": 1.1, "crt": 5
    }
    assert find("prefixes", 65)["accessoryEffects"]["statDefense"] == 4
    poison = find("buffs", 20)
    assert poison["description"]["en-US"] == "Slowly losing life"
    assert poison["mainFlags"]["debuff"] and poison["mainFlags"]["buffNoSave"]
    assert poison["sets"]["BuffTimeIsExtendedWithGameDifficulty"]
    providers = result["capabilities"]["bestiary"]
    assert providers["unlockRequirements"] and not providers["unknownProviders"]
    assert providers["providerCounts"] == {
        "CommonEnemyUICollectionInfoProvider": 412,
        "CritterUICollectionInfoProvider": 71,
        "GoldCritterUICollectionInfoProvider": 13,
        "HighestOfMultipleUICollectionInfoProvider": 8,
        "SalamanderShellyDadUICollectionInfoProvider": 3,
        "TownNPCUICollectionInfoProvider": 39,
    }
    assert find("bestiary", -10)["unlockRule"]["thresholds"] == {
        "portrait": 1, "stats": 10, "drops": 25, "dropRates": 50
    }
    assert find("bestiary", 35)["unlockRule"]["kind"] == "highest-of-multiple"
    assert find("bestiary", 442)["unlockRule"]["globalGoldNpcNetIds"]
    assert find("bestiary", 494)["worldConditional"] is True
    assert find("npc-frames", -65)["npcType"] == 235
    assert find("npc-frames", -65)["frameCount"] == 3
    assert result["capabilities"]["npcFrames"]["missingAssets"] == 0
    assert result["capabilities"]["dyeShaders"]["armorCount"] == 120
    assert result["capabilities"]["dyeShaders"]["hairCount"] == 12
    assert find("dye-shaders", "armor:1007")["pass"] == "ArmorColored"
    assert find("dye-shaders", "hair:1977")["delegateMethodIlSha256"]
    assert find("items", 1007)["gameplay"]["dye"] > 0
    assert find("items", 1977)["gameplay"]["hairDye"] > 0
    if "texture-references" in result["families"]:
        coverage = result["capabilities"]["textureReferences"]
        assert find("npc-frames", 7)["assetId"] == "UI/Bestiary/NPCs/NPC_7"
        assert coverage["itemTextureCopies"] == 67
        assert coverage["domains"]["Wall"]["excludedSentinel"] == 1
        assert find("texture-references", "Wall:0")["status"] == "not-drawn-sentinel"
        if coverage["indexedAssets"] >= 15_000:
            assert coverage["missingCount"] == 0 and not result["requiredMissing"]
            assert find("texture-references", "Item:3665")["assetId"] == "Item_48"
            assert find("texture-references", "Tile:650")["assetId"] == "TIles_650"
    if "player-draw-plans" in result["families"]:
        assert "peakRssBytes" not in result["capabilities"]["playerDrawPlans"]
        draw_stage = next(s for s in result["metrics"]["stages"] if s["stage"] == "player-draw-plans")
        assert draw_stage["drawPeakRssBytes"] > 0
        assert result["families"]["player-draw-plans"]["count"] > 0
        with (root / result["families"]["player-draw-plans"]["path"]).open(encoding="utf-8") as stream:
            plan = json.loads(next(stream))
        assert plan["operations"] and all(operation["assetId"] for operation in plan["operations"])
    assert find("pixel-candidates", "tile:0:0:0")["color"] == {"r": 151, "g": 107, "b": 75}
    assert find("pixel-candidates", "tile:0:0:1")["color"] == {"r": 151, "g": 0, "b": 0}
    assert find("paints", 1)["color"] == {"r": 255, "g": 0, "b": 0, "a": 255}
    assert result["metrics"]["peakRssBytes"] < 256 * 1024 * 1024
    print("verified", len(result["families"]), "families, peak RSS", result["metrics"]["peakRssBytes"])


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
