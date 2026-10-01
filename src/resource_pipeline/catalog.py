"""Deterministic content-addressed packs and explicit coverage reports."""
from __future__ import annotations

import gzip
from io import BytesIO
from pathlib import Path
from PIL import Image, UnidentifiedImageError
from .models import ExtractionEnvelope, FAMILIES
from .security import PipelineError, atomic_write, canonical_json, read_json, sha256

Image.MAX_IMAGE_PIXELS = 16_000_000


def put_object(root: Path, content: bytes, suffix: str) -> dict:
    digest = sha256(content)
    key = f"objects/{digest[:2]}/{digest}{suffix}"
    target = root / key
    if target.exists():
        if sha256(target.read_bytes()) != digest:
            raise PipelineError("Corrupt existing content-addressed object")
    else:
        atomic_write(target, content)
    return {"path": key, "sha256": digest, "bytes": len(content)}


def pack_json(root: Path, value: object) -> dict:
    raw = canonical_json(value)
    zipped = gzip.compress(raw, compresslevel=9, mtime=0)
    return {**put_object(root, zipped, ".json.gz"), "encoding": "gzip", "rawSha256": sha256(raw), "rawBytes": len(raw)}


def image_object(source: Path, root: Path) -> dict:
    if source.suffix.lower() != ".png":
        raise PipelineError("Only decoded PNG assets are accepted by the pack stage")
    try:
        with Image.open(source) as original:
            if original.format != "PNG" or original.width * original.height > Image.MAX_IMAGE_PIXELS:
                raise PipelineError("Image format/dimensions exceed the contract")
            original.load()
            rgba = original.convert("RGBA")
            # Strip incidental author/time/profile metadata, without resizing or
            # lossy conversion. Same pixels deduplicate despite source metadata.
            clean = Image.new("RGBA", rgba.size)
            clean.paste(rgba)
            out = BytesIO()
            clean.save(out, format="PNG", optimize=True)
            return {**put_object(root, out.getvalue(), ".png"), "width": clean.width, "height": clean.height}
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise PipelineError("Invalid PNG asset") from exc


def build_catalog(extracted: Path, output: Path, input_digest: str) -> dict:
    candidates = list(extracted.rglob("resource-bundle.json"))
    if len(candidates) != 1:
        raise PipelineError("Need exactly one resource-bundle.json; raw game packages require an extraction adapter")
    bundle_path = candidates[0]
    envelope = ExtractionEnvelope.model_validate(read_json(bundle_path))
    base = bundle_path.parent
    output.mkdir(parents=True, exist_ok=False)
    missing = [family for family in FAMILIES if not envelope.families.get(family)]
    unresolved = []
    images = {}
    strings = []
    positions = {}

    def string_id(text):
        if text not in positions:
            positions[text] = len(strings)
            strings.append(text)
        return positions[text]

    packs = {}
    record_hashes = {}
    for family in FAMILIES:
        rows = []
        records = sorted(envelope.families.get(family, []), key=lambda record: canonical_json(record.id))
        aliases = set()
        primary = {canonical_json(record.id) for record in records}
        record_hashes[family] = {}
        for record in records:
            for alias in record.aliases:
                key = canonical_json(alias)
                if key in primary or key in aliases:
                    raise PipelineError(f"Ambiguous alias in {family}")
                aliases.add(key)
            references = []
            for name in record.images:
                file = base / name
                if not file.is_file() or file.is_symlink():
                    unresolved.append({"family": family, "id": record.id, "path": name})
                    continue
                if name not in images:
                    images[name] = image_object(file, output)
                references.append(images[name]["sha256"])
            names = {locale: string_id(text) for locale, text in sorted(record.name.items())}
            descriptions = {locale: string_id(text) for locale, text in sorted(record.description.items())}
            row = [record.id, names, descriptions, record.attributes, references, record.aliases]
            rows.append(row)
            record_hashes[family][canonical_json(record.id).decode()] = sha256(canonical_json({"record": record.model_dump(), "imageDigests": references}))
        packs[family] = {**pack_json(output, {"columns": ["id", "name", "description", "attributes", "images", "aliases"], "rows": rows}), "count": len(rows)}
    string_pack = pack_json(output, strings)
    # One image descriptor per unique PNG object, even when input paths differ.
    objects = {item["sha256"]: item for item in images.values()}
    image_pack = pack_json(output, objects)
    coverage = {"requiredFamilies": list(FAMILIES), "missingFamilies": missing, "missingImages": unresolved,
                "complete": not missing and not unresolved}
    manifest = {"schemaVersion": 1, "gameVersion": envelope.game_version, "adapter": envelope.adapter,
                "sourceKind": envelope.source_kind, "inputArchiveSha256": input_digest,
                "declaredSources": [source.model_dump() for source in envelope.sources],
                "packs": packs, "strings": string_pack, "images": image_pack,
                "coverage": coverage, "warnings": envelope.warnings,
                "counts": {"imageReferences": len(images), "uniqueImages": len(objects), "strings": len(strings)},
                "publicationScope": "synthetic-demo" if envelope.source_kind == "synthetic" else "private-export-needs-rights-review"}
    content = canonical_json(manifest)
    version = sha256(content)
    atomic_write(output / "manifest.json", content)
    atomic_write(output / "record-hashes.json", canonical_json(record_hashes))
    return {"release": version, "manifest": manifest, "recordHashes": record_hashes,
            "objectBytes": sum(p.stat().st_size for p in (output / "objects").rglob("*") if p.is_file())}


def compare_versions(previous: dict | None, current: dict) -> dict:
    changes = {}
    old = previous or {}
    for family in FAMILIES:
        before = old.get(family, {})
        after = current.get(family, {})
        changes[family] = {
            "added": sorted(set(after) - set(before)),
            "removed": sorted(set(before) - set(after)),
            "changed": sorted(key for key in set(before) & set(after) if before[key] != after[key]),
            "unchanged": sum(before[key] == after[key] for key in set(before) & set(after)),
        }
    return changes
