"""Version-pinned, evidence-based coverage; uploaded assertions are not policy.

This module is an independently written interchange contract. It contains no
game IDs, assets, private extraction code, or version-specific game semantics.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from pathlib import Path
from typing import Any, Literal

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field

from .security import PipelineError, canonical_json, read_json, relative_path, sha256


REQUIRED_SUBMANIFESTS: dict[str, tuple[str, ...]] = {
    "items": ("identity", "names", "images.originals", "images.thumbnails", "texture-aliases",
              "attributes.schema", "attributes.defaults", "tooltip.templates", "tooltip.resolved-baseline",
              "research", "placement-relations", "equipment-slots", "category-sort"),
    "tiles": ("identity", "names-provenance", "map-options", "traits", "object-layouts", "frame-important",
              "styles-alternates-randoms", "shape-frames", "item-placement-relations", "textures",
              "thumbnails", "descriptions-provenance"),
    "walls": ("identity", "names-provenance", "map-options", "traits", "item-placement-relations",
              "textures", "thumbnails", "descriptions-provenance"),
    "paints": ("identity", "input-colors", "map-color-semantics", "coatings", "localized-names"),
    "npcs": ("identity-netid", "persistent-id", "bestiary-membership", "localized-names", "bestiary-type",
             "icon-frame-plan", "icons", "description-provenance"),
    "buffs": ("identity", "localized-names", "descriptions", "positive-negative-classification",
              "item-links", "icons-if-requested"),
    "prefixes": ("identity", "localized-names", "multipliers", "eligibility-pools", "exceptions"),
    "player": ("body-pieces", "hair-normal", "hair-hat", "armor-head-body-arms-legs-composite",
               "accessory-slots", "wings", "auxiliary-textures", "skin-style-maps", "equipment-texture-maps",
               "draw-frame-layouts", "choice-previews", "walk-frames", "frame-repairs",
               "hair-dye-registrations", "armor-dye-registrations", "render-capabilities"),
    "worldgen": ("configuration-schema", "schema-revision", "defaults", "enum-choices", "localized-labels-help",
                 "pass-list", "seed-support", "chest-loot-membership", "source-field-map"),
    "markers": ("entity-selectors", "source-crops", "thumbnails", "categories"),
    "ids": ("namespace-constants", "aliases", "counts", "unsupported-types"),
    "locales": ("zh-Hans", "en-US", "keys", "fallback-report", "unresolved-references"),
    "map": ("tile-options", "wall-options", "liquids-backgrounds", "paint-semantics",
            "light-coating-semantics", "special-case-capabilities"),
    "pixel": ("stable-whitelist", "stable-exclusions", "candidate-order", "candidate-groups",
              "nearest-rgb-srgb", "txci-index", "codec-manifests", "legacy-pixel-catalog-adapter", "optional-gallery"),
}
CLAIM_KEYS = tuple(f"{family}.{part}" for family, parts in REQUIRED_SUBMANIFESTS.items() for part in parts)
ORIGIN_KINDS = frozenset({"server-static", "server-runtime", "client-original", "decoded-client",
                          "ui-atlas-crop", "texture-alias", "derived", "application", "external-reference", "synthetic"})


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class InputBinding(Strict):
    game_version: str = Field(pattern=r"^[0-9]+(?:\.[0-9]+){2,3}$")
    source_kind: Literal["synthetic", "export-bundle"]
    server_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    client_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    metadata_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    adapter_id: str = Field(pattern=r"^[a-zA-Z0-9_.-]{1,80}$")
    adapter_version: str = Field(min_length=1, max_length=80)
    adapter_tool_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class Evidence(Strict):
    path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class Claim(Strict):
    key: str
    status: Literal["verified", "unsupported", "not-applicable", "not-requested"]
    origin_kind: str | None = None
    evidence: Evidence | None = None
    reason: str | None = Field(default=None, max_length=2000)
    unsupported_fields: list[str] = Field(default_factory=list, max_length=1000)


class CoverageDocument(Strict):
    schema_version: Literal[1]
    binding: InputBinding
    profile_id: str
    claims: list[Claim] = Field(max_length=110)


@dataclass(frozen=True)
class FieldSchema:
    """Small closed schema vocabulary; policy is created by administrator code."""
    kind: Literal["string", "integer", "number", "boolean", "object", "array"]
    nullable: bool = False
    properties: dict[str, "FieldSchema"] = field(default_factory=dict)
    items: "FieldSchema | None" = None
    enum: tuple[Any, ...] | None = None
    min_length: int = 0
    max_length: int = 100_000


@dataclass(frozen=True)
class ForeignKey:
    field: str
    target_claim: str
    many: bool = False
    nullable: bool = False


@dataclass(frozen=True)
class FileField:
    field: str
    kind: Literal["png", "file"] = "file"
    max_pixels: int = 16_000_000


@dataclass(frozen=True)
class ClaimSpec:
    key: str
    expected_ids: tuple[int | str, ...]
    fields: dict[str, FieldSchema]
    allowed_origins: frozenset[str]
    required: bool = True
    not_applicable_reason: str | None = None
    foreign_keys: tuple[ForeignKey, ...] = ()
    file_fields: tuple[FileField, ...] = ()


@dataclass(frozen=True)
class CoverageProfile:
    """Never construct this from an uploaded coverage document.

    Real profiles need independently pinned authoritative ID domains, field
    schemas, provenance rules and version-specific semantic adapter tests.
    Empty domains must be deliberate administrator decisions, not omissions.
    """
    profile_id: str
    game_version: str
    claims: tuple[ClaimSpec, ...]
    source_kind: Literal["synthetic", "export-bundle"] = "export-bundle"
    # The compact consumer catalog must use these same authoritative ID domains.
    # A real deployment may choose a different logical claim per family.
    catalog_domains: dict[str, str] = field(default_factory=lambda: {
        family: f"{family}.{parts[0]}" for family, parts in REQUIRED_SUBMANIFESTS.items()})

    def __post_init__(self):
        keys = [spec.key for spec in self.claims]
        if len(keys) != len(CLAIM_KEYS) or set(keys) != set(CLAIM_KEYS):
            raise PipelineError("Coverage profile must explicitly address all 110 submanifests")
        if set(self.catalog_domains) != set(REQUIRED_SUBMANIFESTS) or any(key not in keys for key in self.catalog_domains.values()):
            raise PipelineError("Coverage profile must bind all catalog family ID domains")
        for spec in self.claims:
            if len({_id_key(value) for value in spec.expected_ids}) != len(spec.expected_ids):
                raise PipelineError("Duplicate authoritative domain ID")
            if not spec.allowed_origins or not spec.allowed_origins <= ORIGIN_KINDS:
                raise PipelineError("Unknown provenance policy")
            if not spec.fields or "id" in spec.fields:
                raise PipelineError("A claim needs an explicit non-ID field schema")
            if spec.required and spec.not_applicable_reason:
                raise PipelineError("Required claims cannot be made inapplicable")
            if self.source_kind != "synthetic" and "synthetic" in spec.allowed_origins:
                raise PipelineError("Synthetic evidence cannot certify real resources")
            for ref in spec.foreign_keys:
                if ref.target_claim not in keys:
                    raise PipelineError("Unknown foreign-key domain")


def _id_key(value: Any) -> bytes:
    if type(value) not in (int, str) or isinstance(value, str) and (not value or len(value) > 500):
        raise PipelineError("Resource IDs must be bounded integers or nonempty strings")
    return canonical_json(value)


def package_file(root: Path, name: str) -> Path:
    """Reject links in every component, including an otherwise-safe parent."""
    relative_path(name)
    if root.is_symlink():
        raise PipelineError("Package root cannot be a link")
    current = root
    for component in name.split("/"):
        current /= component
        if current.is_symlink():
            raise PipelineError("Package references cannot traverse links")
    if not current.is_file():
        raise PipelineError("Missing referenced artifact")
    return current


def _check_schema(value: Any, rule: FieldSchema) -> None:
    if value is None and rule.nullable:
        return
    kinds = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "object": dict, "array": list}
    expected = kinds.get(rule.kind)
    if expected is None or not isinstance(value, expected) or rule.kind in ("integer", "number") and type(value) is bool:
        raise PipelineError("Field type does not match the pinned schema")
    if isinstance(value, float) and not math.isfinite(value):
        raise PipelineError("Non-finite field value")
    if rule.enum is not None and not any(type(value) is type(item) and value == item for item in rule.enum):
        raise PipelineError("Field value is outside its pinned enum")
    if isinstance(value, (str, list, dict)) and not rule.min_length <= len(value) <= rule.max_length:
        raise PipelineError("Field length does not match the pinned schema")
    if rule.kind == "object":
        if set(value) != set(rule.properties):
            raise PipelineError("Object fields do not match the pinned schema")
        for key, child in rule.properties.items():
            _check_schema(value[key], child)
    if rule.kind == "array":
        if rule.items is None:
            raise PipelineError("Array policy must define its item schema")
        for item in value:
            _check_schema(item, rule.items)


def _value(row: dict, name: str) -> Any:
    current: Any = row
    for key in name.split("."):
        if not isinstance(current, dict) or key not in current:
            raise PipelineError("Reference field is absent")
        current = current[key]
    return current


def _check_file(root: Path, reference: Any, spec: FileField) -> None:
    if not isinstance(reference, dict) or not {"path", "sha256"} <= set(reference):
        raise PipelineError("File reference needs a path and content hash")
    target = package_file(root, reference["path"])
    if target.stat().st_size > 128 * 1024 * 1024 or sha256(target.read_bytes()) != reference["sha256"]:
        raise PipelineError("Referenced file hash or size mismatch")
    if spec.kind == "png":
        try:
            with Image.open(target) as image:
                if image.format != "PNG" or image.width * image.height > spec.max_pixels:
                    raise PipelineError("Image format or pixel budget mismatch")
                if reference.get("width") != image.width or reference.get("height") != image.height:
                    raise PipelineError("Image dimensions do not match its reference")
                image.verify()
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
            raise PipelineError("Invalid PNG evidence") from exc


def validate_contract(root: Path, expected_binding: InputBinding, profile: CoverageProfile) -> dict:
    """Verify actual bytes, exact per-claim IDs/fields, FK targets and PNGs.

    Incomplete mandatory coverage is a structured result. Corrupt evidence,
    false provenance, schema errors and source/adapter mismatches fail closed.
    The final completeness bit is a derived convenience, never an input.
    """
    try:
        document = CoverageDocument.model_validate(read_json(package_file(root, "coverage.json")))
    except PipelineError:
        raise
    except Exception as exc:
        raise PipelineError("Invalid coverage contract") from exc
    if document.binding != expected_binding:
        raise PipelineError("Source/metadata/adapter fingerprint binding mismatch")
    if (document.profile_id != profile.profile_id or profile.game_version != expected_binding.game_version
            or profile.source_kind != expected_binding.source_kind):
        raise PipelineError("Coverage profile/version mismatch")
    claims = {claim.key: claim for claim in document.claims}
    if len(claims) != len(document.claims) or set(claims) - set(CLAIM_KEYS):
        raise PipelineError("Unknown or duplicate coverage claim")
    results: list[dict] = []
    actual_ids: dict[str, set[bytes]] = {}
    rows_by_claim: dict[str, list[dict]] = {}
    for spec in profile.claims:
        claim = claims.get(spec.key)
        report = {"key": spec.key, "required": spec.required, "status": "missing",
                  "expectedCount": len(spec.expected_ids), "verifiedCount": 0, "missingIds": list(spec.expected_ids)}
        results.append(report)
        if claim is None:
            continue
        report.update(status=claim.status, originKind=claim.origin_kind, reason=claim.reason,
                      unsupportedFields=claim.unsupported_fields)
        if claim.status != "verified":
            if not claim.reason or claim.evidence is not None:
                raise PipelineError("Unverified claims need a reason and cannot carry verified evidence")
            if claim.status == "not-applicable" and (spec.required or claim.reason != spec.not_applicable_reason):
                raise PipelineError("Only the trusted profile can declare a claim inapplicable")
            if claim.status == "not-requested" and spec.required:
                raise PipelineError("Required claims cannot be omitted by the producer")
            continue
        if claim.origin_kind not in spec.allowed_origins or claim.evidence is None or claim.unsupported_fields:
            raise PipelineError("Verified claim has unsupported fields or disallowed provenance")
        source = package_file(root, claim.evidence.path)
        if source.stat().st_size > 64 * 1024 * 1024 or sha256(source.read_bytes()) != claim.evidence.sha256:
            raise PipelineError("Coverage evidence hash mismatch")
        rows = read_json(source)
        if not isinstance(rows, list) or len(rows) > 100_000:
            raise PipelineError("Evidence must be a bounded table of records")
        ids: set[bytes] = set()
        for row in rows:
            if not isinstance(row, dict) or set(row) != {"id", *spec.fields}:
                raise PipelineError("Record fields do not match the pinned schema")
            identity = _id_key(row["id"])
            if identity in ids:
                raise PipelineError("Duplicate ID in coverage evidence")
            ids.add(identity)
            for key, rule in spec.fields.items():
                _check_schema(row[key], rule)
            for file_spec in spec.file_fields:
                _check_file(root, _value(row, file_spec.field), file_spec)
        expected_ids = {_id_key(value) for value in spec.expected_ids}
        if ids - expected_ids:
            raise PipelineError("Evidence contains IDs outside the pinned domain")
        missing = [value for value in spec.expected_ids if _id_key(value) not in ids]
        report.update(status="incomplete" if missing else "verified", missingIds=missing, verifiedCount=len(ids),
                      evidence=claim.evidence.model_dump())
        actual_ids[spec.key] = ids
        rows_by_claim[spec.key] = rows
    for spec in profile.claims:
        for row in rows_by_claim.get(spec.key, []):
            for ref in spec.foreign_keys:
                value = _value(row, ref.field)
                if value is None and ref.nullable:
                    continue
                if ref.many and not isinstance(value, list):
                    raise PipelineError("Foreign key requires an ID list")
                for item in value if ref.many else [value]:
                    if _id_key(item) not in actual_ids.get(ref.target_claim, set()):
                        raise PipelineError("Unresolved foreign key in normalized resource data")
    required = [result for result in results if result["required"]]
    return {"profileId": profile.profile_id, "profileKind": profile.source_kind, "submanifestCount": len(results),
            "complete": all(result["status"] == "verified" for result in required),
            "verifiedRequired": sum(result["status"] == "verified" for result in required),
            "requiredCount": len(required), "subclaims": results, "binding": expected_binding.model_dump()}
