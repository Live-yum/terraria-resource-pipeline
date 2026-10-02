"""Immutable consumer release transport, separate from semantic publication approval.

This module never upgrades a raw extraction receipt into trusted game coverage.
Only an already-reviewed deployment may pin the resulting manifest in a client.
"""
from __future__ import annotations

import gzip
import json
import zlib
import re
from pathlib import Path

from .security import PipelineError, atomic_write, canonical_json, relative_path, sha256, read_json
from .contracts import package_file

ROLES = ("materials.base", "materials.rules", "pixel.catalog", "pixel.rgb")
# Server-owned allowlists. Uploaded objects cannot define their own contract.
GROUPS = {
    "materials": (dict.fromkeys(ROLES[:3], "json") | {"pixel.rgb": "binary"}, "materials.base"),
    "items": (dict.fromkeys(("items.catalog", "items.rules", "items.categories"), "json"), "items.catalog"),
    "player": ({"player.presentation": "json", "player.walk": "binary", "player.atlas": "binary"}, "player.presentation"),
    "worldgen": ({"worldgen.choices": "json"}, None),
    "markers": ({"markers.catalog": "json", "markers.images": "binary"}, "markers.catalog"),
}


def _group(name: str):
    if not isinstance(name, str) or name not in GROUPS:
        raise PipelineError("Unknown fixed consumer resource group")
    formats, base = GROUPS[name]
    return formats, base, {role for role in formats if base is not None and role != base}

MAX_RAW_BYTES = 32 * 1024 * 1024
MAX_RELEASE_BYTES = 64 * 1024 * 1024
HEX64 = re.compile(r"^[a-f0-9]{64}$")
HEX40 = re.compile(r"^[a-f0-9]{40}$")
VERSION = re.compile(r"^[0-9]+(?:\.[0-9]+){2,3}$")


def _binding(value: dict) -> None:
    if not isinstance(value, dict) or set(value) != {"serverSha256", "clientTreeSha", "consumerCommit"}:
        raise PipelineError("Invalid consumer source binding")
    for field, pattern in (("serverSha256", HEX64), ("clientTreeSha", HEX40), ("consumerCommit", HEX40)):
        if not isinstance(value[field], str) or not pattern.fullmatch(value[field]):
            raise PipelineError("Invalid consumer source digest")


def _safe_root(root: Path) -> None:
    if any(path.is_symlink() for path in (root, *root.parents)):
        raise PipelineError("Consumer release cannot traverse a linked root")


def manifest_body(manifest: dict) -> dict:
    return {key: value for key, value in manifest.items() if key != "releaseId"}


def validate_manifest(manifest: dict, *, group: str = "materials") -> None:
    formats, base_role, derived_roles = _group(group)
    if not isinstance(manifest, dict) or set(manifest) != {"schemaVersion", "gameVersion", "releaseId", "sourceBinding", "objects"}:
        raise PipelineError("Invalid consumer release fields")
    if type(manifest["schemaVersion"]) is not int or manifest["schemaVersion"] != 1:
        raise PipelineError("Unsupported consumer release schema")
    if not isinstance(manifest["gameVersion"], str) or not VERSION.fullmatch(manifest["gameVersion"]):
        raise PipelineError("Invalid consumer game version")
    _binding(manifest["sourceBinding"])
    if manifest["releaseId"] != sha256(canonical_json(manifest_body(manifest))):
        raise PipelineError("Consumer release identity mismatch")
    objects = manifest["objects"]
    if not isinstance(objects, dict) or set(objects) != set(formats):
        raise PipelineError("Incomplete atomic consumer resource group")
    base = objects[base_role].get("rawSha256") if base_role and isinstance(objects[base_role], dict) else None
    total = 0
    for role, ref in objects.items():
        fields = {"path", "sha256", "bytes", "encoding", "format", "rawSha256", "rawBytes", "gameVersion"}
        if role in derived_roles:
            fields.add("baseSha256")
        if not isinstance(ref, dict) or set(ref) != fields:
            raise PipelineError("Invalid consumer object descriptor")
        if ref["gameVersion"] != manifest["gameVersion"] or (role in derived_roles and ref["baseSha256"] != base):
            raise PipelineError("Mixed consumer material versions")
        for field in ("sha256", "rawSha256"):
            if not isinstance(ref[field], str) or not HEX64.fullmatch(ref[field]):
                raise PipelineError("Invalid consumer object hash")
        if ref["encoding"] not in ("identity", "gzip") or ref["format"] != formats[role]:
            raise PipelineError("Unsupported consumer object encoding or format")
        for field in ("bytes", "rawBytes"):
            if type(ref[field]) is not int or not 0 < ref[field] <= MAX_RAW_BYTES:
                raise PipelineError("Consumer object exceeds size contract")
        total += ref["rawBytes"]
        relative_path(ref["path"])
        suffix = ".bin" if ref["format"] == "binary" else ".json"
        if ref["encoding"] == "gzip":
            suffix += ".gz"
        if ref["path"] != f'objects/{ref["sha256"][:2]}/{ref["sha256"]}{suffix}':
            raise PipelineError("Consumer object path is not content addressed")
        if ref["encoding"] == "identity" and (ref["sha256"] != ref["rawSha256"] or ref["bytes"] != ref["rawBytes"]):
            raise PipelineError("Identity object has inconsistent raw descriptor")
    if total > MAX_RELEASE_BYTES:
        raise PipelineError("Consumer release exceeds aggregate size contract")


def _json_pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def build_consumer_release(output: Path, *, game_version: str, source_binding: dict,
                           objects: dict[str, bytes], compress: bool = False, group: str = "materials") -> dict:
    """Package data-only reviewed inputs for one fixed group; no publication approval.

    Source binding records evidence identity, not proof of runtime semantics.
    JSON bytes are retained exactly; no private assets are fixtures in this repo.
    """
    formats, base_role, derived_roles = _group(group)
    if set(objects) != set(formats):
        raise PipelineError("All atomic group objects are required")
    for role, raw in objects.items():
        if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_RAW_BYTES:
            raise PipelineError("Invalid consumer input bytes")
        if formats[role] == "json":
            try:
                json.loads(raw, object_pairs_hook=_json_pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))
            except (ValueError, UnicodeError) as exc:
                raise PipelineError("Invalid consumer JSON input") from exc
    base_hash = sha256(objects[base_role]) if base_role else None
    manifest = {"schemaVersion": 1, "gameVersion": game_version, "sourceBinding": source_binding, "objects": {}}
    stored_objects = {}
    for role, raw in objects.items():
        stored = gzip.compress(raw, mtime=0) if compress else raw
        digest = sha256(stored)
        fmt = formats[role]
        suffix = (".bin" if fmt == "binary" else ".json") + (".gz" if compress else "")
        path = f"objects/{digest[:2]}/{digest}{suffix}"
        ref = {"path": path, "sha256": digest, "bytes": len(stored), "encoding": "gzip" if compress else "identity",
               "format": fmt, "rawSha256": sha256(raw), "rawBytes": len(raw), "gameVersion": game_version}
        if role in derived_roles:
            ref["baseSha256"] = base_hash
        manifest["objects"][role] = ref
        stored_objects[path] = stored
    manifest["releaseId"] = sha256(canonical_json(manifest))
    validate_manifest(manifest, group=group)
    _safe_root(output)
    if output.exists():
        raise PipelineError("Consumer release output must be new")
    output.mkdir(parents=True)
    for path, raw in stored_objects.items():
        atomic_write(output / path, raw)
    atomic_write(output / "manifest.json", canonical_json(manifest))
    return manifest


def verify_consumer_release(root: Path, *, manifest_sha256: str, group: str = "materials") -> dict[str, bytes]:
    """Re-read a pinned release; bound gzip output before JSON/use. No network."""
    _safe_root(root)
    path = package_file(root, "manifest.json")
    if path.stat().st_size > 256 * 1024:
        raise PipelineError("Consumer manifest exceeds size limit")
    if sha256(path.read_bytes()) != manifest_sha256:
        raise PipelineError("Consumer manifest pin mismatch")
    manifest = read_json(path, 256 * 1024)
    validate_manifest(manifest, group=group)
    result = {}
    for role, ref in manifest["objects"].items():
        path = package_file(root, ref["path"])
        if path.stat().st_size != ref["bytes"]:
            raise PipelineError("Consumer object size mismatch")
        stored = path.read_bytes()
        if sha256(stored) != ref["sha256"]:
            raise PipelineError("Consumer object hash mismatch")
        try:
            if ref["encoding"] == "gzip":
                decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
                raw = decoder.decompress(stored, ref["rawBytes"] + 1)
                if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
                    raise PipelineError("Consumer gzip must contain exactly one complete member")
            else:
                raw = stored
        except (OSError, EOFError, zlib.error) as exc:
            raise PipelineError("Invalid compressed consumer object") from exc
        if len(raw) != ref["rawBytes"] or sha256(raw) != ref["rawSha256"]:
            raise PipelineError("Consumer raw object mismatch")
        if ref["format"] == "json":
            try:
                json.loads(raw, object_pairs_hook=_json_pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))
            except (ValueError, UnicodeError) as exc:
                raise PipelineError("Invalid consumer JSON object") from exc
        result[role] = raw
    return result
