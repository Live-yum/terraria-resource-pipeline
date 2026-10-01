"""Deterministic content-addressed packs and explicit coverage reports."""
from __future__ import annotations

from dataclasses import asdict
import gzip
from io import BytesIO
from pathlib import Path
import tempfile
from PIL import Image, UnidentifiedImageError
from .adapters import validate_normalized_package
from .adapters_synthetic import SYNTHETIC_SERVER, synthetic_binding, synthetic_profile
from .contracts import package_file, validate_contract
from .models import ExtractionEnvelope, FAMILIES
from .security import PipelineError, atomic_write, canonical_json, read_json, relative_path, sha256

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
    return pack_bytes(root, raw)


def pack_bytes(root: Path, raw: bytes) -> dict:
    zipped = gzip.compress(raw, compresslevel=9, mtime=0)
    return {**put_object(root, zipped, ".json.gz"), "encoding": "gzip", "rawSha256": sha256(raw), "rawBytes": len(raw)}


def trusted_synthetic_policy(game_version: str, adapter: str, source_kind: str):
    """Only fixed, first-party policies; never build policy from upload claims."""
    if source_kind != "synthetic" or adapter != "synthetic-contract" or game_version not in ("0.0.1", "0.0.2"):
        raise PipelineError("No installed trusted coverage profile; raw/real game exports remain blocked")
    revision = int(game_version.rsplit(".", 1)[1])
    return synthetic_binding(revision), synthetic_profile(revision)


def _profile_document(profile) -> dict:
    def jsonable(value):
        if isinstance(value, dict):
            return {key: jsonable(item) for key, item in value.items()}
        if isinstance(value, (set, frozenset)):
            return sorted(jsonable(item) for item in value)
        if isinstance(value, (tuple, list)):
            return [jsonable(item) for item in value]
        return value
    return jsonable(asdict(profile))


def _proof_artifacts(base: Path, profile, report: dict) -> dict[str, bytes]:
    artifacts = {}
    specs = {spec.key: spec for spec in profile.claims}
    for claim in report["subclaims"]:
        if "evidence" not in claim:
            continue
        path = claim["evidence"]["path"]
        artifacts[path] = package_file(base, path).read_bytes()
        for row in read_json(package_file(base, path)):
            for reference in specs[claim["key"]].file_fields:
                value = row
                for part in reference.field.split("."):
                    value = value[part]
                artifacts[value["path"]] = package_file(base, value["path"]).read_bytes()
    return artifacts


def verified_object(root: Path, reference: dict) -> bytes:
    """Verify both stored and bounded decompressed content-addressed bytes."""
    path = reference.get("path", "")
    relative_path(path)
    if not path.startswith("objects/"):
        raise PipelineError("资源对象引用不在受审核目录")
    try:
        target = package_file(root, path)
    except PipelineError as exc:
        raise PipelineError("资源对象缺失或校验失败") from exc
    if target.stat().st_size != reference.get("bytes") or target.stat().st_size > 128 * 1024 * 1024:
        raise PipelineError("资源对象缺失或校验失败")
    stored = target.read_bytes()
    if sha256(stored) != reference.get("sha256") or target.name.split(".", 1)[0] != reference.get("sha256"):
        raise PipelineError("资源对象缺失或校验失败")
    if reference.get("encoding") != "gzip":
        return stored
    expected = reference.get("rawBytes")
    if type(expected) is not int or not 0 <= expected <= 64 * 1024 * 1024:
        raise PipelineError("资源对象解压容量无效")
    try:
        with gzip.GzipFile(fileobj=BytesIO(stored)) as stream:
            raw = stream.read(expected + 1)
    except (OSError, EOFError) as exc:
        raise PipelineError("资源对象解压失败") from exc
    if len(raw) != expected or sha256(raw) != reference.get("rawSha256"):
        raise PipelineError("资源对象解压哈希或容量不符")
    return raw


def manifest_references(manifest: dict) -> list[dict]:
    """All direct proof/catalog objects; image descriptors live in their index."""
    proof = manifest.get("coverageProof")
    if not isinstance(proof, dict) or proof.get("schemaVersion") != 1:
        raise PipelineError("缺少受审核的细粒度覆盖证明")
    return [*manifest["packs"].values(), manifest["strings"], manifest["images"], manifest["recordHashes"],
            proof["profile"], proof["document"], proof["catalogSource"], proof["syntheticInput"],
            *proof["artifacts"].values()]


def verify_coverage_proof(root: Path, manifest: dict) -> None:
    """Recreate only normalized allowlisted evidence and rerun trusted policy.

    Publication never treats a report's own `complete` value as proof.
    """
    import json
    binding, profile = trusted_synthetic_policy(manifest["gameVersion"], manifest["adapter"], manifest["sourceKind"])
    proof = manifest["coverageProof"]
    if proof.get("binding") != binding.model_dump() or proof.get("profileId") != profile.profile_id:
        raise PipelineError("覆盖证明绑定与固定策略不一致")
    if json.loads(verified_object(root, proof["profile"])) != _profile_document(profile):
        raise PipelineError("覆盖策略对象与服务器固定策略不一致")
    source = verified_object(root, proof["syntheticInput"])
    if source != SYNTHETIC_SERVER or sha256(source) != binding.server_sha256:
        raise PipelineError("合成输入字节与固定来源不一致")
    with tempfile.TemporaryDirectory(prefix="verify-resource-proof-") as temporary:
        package = Path(temporary)
        atomic_write(package / "coverage.json", verified_object(root, proof["document"]))
        atomic_write(package / "resource-bundle.json", verified_object(root, proof["catalogSource"]))
        for name, reference in proof["artifacts"].items():
            relative_path(name)
            if name in ("coverage.json", "resource-bundle.json"):
                raise PipelineError("覆盖证据不能覆盖根清单")
            atomic_write(package / name, verified_object(root, reference))
        # Catalog images may have multiple input paths for identical pixels. The
        # originals are explicit proof artifacts, not an inferred directory copy.
        report = validate_normalized_package(package, binding, profile)
        if not report["complete"] or any(manifest["coverage"].get(key) != value for key, value in report.items()):
            raise PipelineError("细粒度覆盖证明不完整或与审核摘要不一致")
        # Record hashes are tied to the reviewed manifest, including future diff baselines.
        record_hash_bytes = verified_object(root, manifest["recordHashes"])
        if (root / "record-hashes.json").read_bytes() != record_hash_bytes:
            raise PipelineError("记录差异基线在审核后被修改")


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
    binding, profile = trusted_synthetic_policy(envelope.game_version, envelope.adapter, envelope.source_kind)
    if package_file(base, "synthetic-input.txt").read_bytes() != SYNTHETIC_SERVER:
        raise PipelineError("Synthetic source bytes do not match the fixed server-side fixture")
    expected_sources = [{"role": "server", "game_version": binding.game_version, "sha256": binding.server_sha256}]
    if [source.model_dump() for source in envelope.sources] != expected_sources:
        raise PipelineError("Declared sources do not match fixed actual synthetic input bytes")
    detailed = validate_contract(base, binding, profile)
    output.mkdir(parents=True, exist_ok=False)
    missing = [family for family in FAMILIES if not envelope.families.get(family)]
    catalog_missing = {}
    specs = {spec.key: spec for spec in profile.claims}
    for family, claim in profile.catalog_domains.items():
        expected_ids = {canonical_json(value) for value in specs[claim].expected_ids}
        actual_ids = {canonical_json(record.id) for record in envelope.families.get(family, [])}
        if actual_ids - expected_ids:
            raise PipelineError("Consumer catalog contains an ID outside its trusted profile")
        absent = [value for value in specs[claim].expected_ids if canonical_json(value) not in actual_ids]
        if absent:
            catalog_missing[family] = absent
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
                try:
                    file = package_file(base, name)
                except PipelineError:
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
    if not missing and not unresolved and not catalog_missing:
        # Complete-looking catalogs pass the exact same normalized-package
        # gate as a registered adapter and publication. Keep structured reports
        # for incomplete catalogs so review can name the absent IDs/images.
        detailed = validate_normalized_package(base, binding, profile)
    artifacts = _proof_artifacts(base, profile, detailed)
    for name in images:
        artifacts[name] = package_file(base, name).read_bytes()
    proof = {"schemaVersion": 1, "profileId": profile.profile_id, "binding": binding.model_dump(),
             "profile": pack_json(output, _profile_document(profile)),
             "document": pack_bytes(output, package_file(base, "coverage.json").read_bytes()),
             "catalogSource": pack_bytes(output, bundle_path.read_bytes()),
             "syntheticInput": pack_bytes(output, SYNTHETIC_SERVER),
             "artifacts": {name: pack_bytes(output, content) for name, content in sorted(artifacts.items())}}
    coverage = {**detailed, "requiredFamilies": list(FAMILIES), "missingFamilies": missing, "missingImages": unresolved,
                "missingCatalogIds": catalog_missing, "complete": detailed["complete"] and not missing and not unresolved and not catalog_missing}
    manifest = {"schemaVersion": 1, "gameVersion": envelope.game_version, "adapter": envelope.adapter,
                "sourceKind": envelope.source_kind, "inputArchiveSha256": input_digest,
                "declaredSources": [source.model_dump() for source in envelope.sources],
                "packs": packs, "strings": string_pack, "images": image_pack, "recordHashes": pack_json(output, record_hashes),
                "coverageProof": proof,
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
