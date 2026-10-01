"""Original data-only fixtures for exercising the adapter contract, not game coverage."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from PIL import Image

from .contracts import (CLAIM_KEYS, ClaimSpec, CoverageProfile, FieldSchema, FileField, ForeignKey,
                        InputBinding, REQUIRED_SUBMANIFESTS)
from .security import atomic_write, canonical_json, sha256


SYNTHETIC_SERVER = b"Original synthetic server-shaped input. This is not executable game code.\n"
SYNTHETIC_TOOL = b"resource-pipeline original data-only synthetic adapter v1\n"
OPTIONAL = {
    "buffs.icons-if-requested": "This fixture profile does not request separate buff icons",
    "pixel.legacy-pixel-catalog-adapter": "This fixture has no legacy pixel catalog",
    "pixel.optional-gallery": "Optional gallery is outside this fixture profile",
}
IMAGE_CLAIMS = frozenset({"items.images.originals", "items.images.thumbnails", "tiles.textures", "tiles.thumbnails",
                          "walls.textures", "walls.thumbnails", "npcs.icons", "markers.thumbnails",
                          "player.body-pieces", "player.hair-normal", "player.hair-hat", "player.choice-previews"})


def synthetic_profile(revision: int = 1) -> CoverageProfile:
    if revision not in (1, 2):
        raise ValueError("Synthetic revisions are 1 and 2")
    image_schema = FieldSchema("object", properties={"path": FieldSchema("string", min_length=1),
                              "sha256": FieldSchema("string", min_length=64, max_length=64),
                              "width": FieldSchema("integer"), "height": FieldSchema("integer")})
    claims = []
    for key in CLAIM_KEYS:
        fields = {"value": FieldSchema("string", min_length=1), "related_item": FieldSchema("integer")}
        files: tuple[FileField, ...] = ()
        if key in IMAGE_CLAIMS:
            fields["image"] = image_schema
            files = (FileField("image", "png"),)
        if key == "items.attributes.defaults":
            fields["defaults"] = FieldSchema("object", properties={"syntheticPower": FieldSchema("integer"),
                                                                     "syntheticStack": FieldSchema("integer")})
        if key == "items.tooltip.resolved-baseline":
            fields["context"] = FieldSchema("string", enum=("synthetic-baseline",))
            fields["template_id"] = FieldSchema("integer")
        foreign = [ForeignKey("related_item", "items.identity")]
        if key == "items.tooltip.resolved-baseline":
            foreign.append(ForeignKey("template_id", "items.tooltip.templates"))
        ids = (1, 2) if revision == 2 and key.startswith("items.") else (1,)
        claims.append(ClaimSpec(key=key, expected_ids=ids, fields=fields, allowed_origins=frozenset({"synthetic"}),
                                required=key not in OPTIONAL, not_applicable_reason=OPTIONAL.get(key),
                                foreign_keys=tuple(foreign), file_fields=files))
    return CoverageProfile(f"synthetic-fixture-v{revision}", f"0.0.{revision}", tuple(claims), "synthetic")


def synthetic_binding(revision: int = 1) -> InputBinding:
    return InputBinding(game_version=f"0.0.{revision}", source_kind="synthetic", server_sha256=sha256(SYNTHETIC_SERVER),
                        adapter_id="synthetic-contract", adapter_version="1", adapter_tool_sha256=sha256(SYNTHETIC_TOOL))


def write_synthetic_package(output: Path, revision: int = 1, binding: InputBinding | None = None,
                            omit_claim: str | None = None) -> tuple[InputBinding, CoverageProfile]:
    """Create all 110 explicitly addressed logical claims from original fixtures.

    This fixture proves the validation machinery only. It is not evidence for
    any Terraria version or for completeness of real attributes/draw behavior.
    """
    profile = synthetic_profile(revision)
    binding = binding or synthetic_binding(revision)
    output.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", (8, 8), (20, 150, 210, 255))
    pixels = BytesIO()
    image.save(pixels, format="PNG")
    png = pixels.getvalue()
    image_reference = {"path": "images/synthetic.png", "sha256": sha256(png), "width": 8, "height": 8}
    atomic_write(output / image_reference["path"], png)
    claims = []
    for spec in profile.claims:
        if spec.key == omit_claim:
            continue
        if spec.not_applicable_reason:
            claims.append({"key": spec.key, "status": "not-applicable", "reason": spec.not_applicable_reason})
            continue
        rows = []
        for identity in spec.expected_ids:
            row = {"id": identity, "value": f"Original synthetic fixture: {spec.key}", "related_item": 1}
            if spec.key in IMAGE_CLAIMS:
                row["image"] = image_reference
            if spec.key == "items.attributes.defaults":
                row["defaults"] = {"syntheticPower": revision + identity, "syntheticStack": 99}
            if spec.key == "items.tooltip.resolved-baseline":
                row.update(context="synthetic-baseline", template_id=identity)
            rows.append(row)
        content = canonical_json(rows)
        path = f"evidence/{spec.key}.json"
        atomic_write(output / path, content)
        claims.append({"key": spec.key, "status": "verified", "origin_kind": "synthetic",
                       "evidence": {"path": path, "sha256": sha256(content)}})
    atomic_write(output / "coverage.json", canonical_json({"schema_version": 1, "binding": binding.model_dump(),
                 "profile_id": profile.profile_id, "claims": claims}))
    sources = [{"role": role, "game_version": binding.game_version, "sha256": digest}
               for role, digest in (("server", binding.server_sha256), ("client", binding.client_sha256),
                                    ("metadata", binding.metadata_sha256)) if digest is not None]
    families = {family: [{"id": identity, "name": {"en-US": f"Synthetic {family} {identity}",
                         "zh-Hans": f"原创合成 {family} {identity}"},
                         "attributes": {"revision": revision if family == "items" else 1},
                         "images": [image_reference["path"]]}
                        for identity in ((1, 2) if revision == 2 and family == "items" else (1,))]
                for family in REQUIRED_SUBMANIFESTS}
    atomic_write(output / "resource-bundle.json", canonical_json({"schema_version": 1, "game_version": binding.game_version,
                 "adapter": binding.adapter_id, "source_kind": "synthetic", "sources": sources, "families": families,
                 "required_families": list(REQUIRED_SUBMANIFESTS),
                 "warnings": ["原创合成合同测试；110子项验证通过不代表任何真实游戏版本支持"]}))
    return binding, profile
