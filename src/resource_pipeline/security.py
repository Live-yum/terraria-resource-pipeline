"""Bounded data-only archive handling; never execute an uploaded file."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import unicodedata
import zipfile
from dataclasses import dataclass


class PipelineError(ValueError):
    pass


@dataclass(frozen=True)
class ArchiveLimits:
    archive_bytes: int = 512 * 1024 * 1024
    expanded_bytes: int = 2 * 1024 * 1024 * 1024
    file_bytes: int = 128 * 1024 * 1024
    files: int = 50_000
    ratio: int = 300


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def read_json(path: Path, maximum: int = 64 * 1024 * 1024) -> object:
    if path.stat().st_size > maximum:
        raise PipelineError("JSON exceeds size limit")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise PipelineError("Duplicate JSON key")
            result[key] = value
        return result
    def bad_number(value):
        raise PipelineError("Non-finite JSON number")
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=pairs, parse_constant=bad_number)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PipelineError("Invalid UTF-8 JSON") from exc


def relative_path(value: str) -> str:
    # Reject rather than silently normalize traversal, alternate separators or
    # platform aliases. The output can safely be checked out on Windows too.
    if not value or "\\" in value or ":" in value or any(ord(c) < 32 for c in value):
        raise PipelineError("Unsafe archive path")
    p = PurePosixPath(value)
    pieces = value.split("/")
    if p.is_absolute() or any(x in ("", ".", "..") for x in pieces):
        raise PipelineError("Unsafe archive path")
    reserved = {"con", "prn", "aux", "nul", *[f"com{i}" for i in range(1, 10)], *[f"lpt{i}" for i in range(1, 10)]}
    for part in pieces:
        if part.rstrip(" .") != part or part.split(".")[0].casefold() in reserved or len(part) > 200:
            raise PipelineError("Nonportable archive path")
    if len(value) > 600 or unicodedata.normalize("NFC", value) != value:
        raise PipelineError("Noncanonical archive path")
    return value


def extract_zip(source: Path, destination: Path, limits: ArchiveLimits = ArchiveLimits()) -> dict:
    if source.stat().st_size > limits.archive_bytes:
        raise PipelineError("Archive exceeds upload limit")
    if destination.exists():
        raise PipelineError("Extraction directory must be new")
    inventory = []
    seen = set()
    total = 0
    try:
        with zipfile.ZipFile(source) as archive:
            entries = archive.infolist()
            if len(entries) > limits.files:
                raise PipelineError("Too many archive entries")
            for info in entries:
                name = relative_path(info.filename.rstrip("/") if info.is_dir() else info.filename)
                key = name.casefold()
                if key in seen:
                    raise PipelineError("Duplicate/case-colliding archive path")
                seen.add(key)
                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode) or stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):
                    raise PipelineError("Links and special files are forbidden")
                if info.flag_bits & 1:
                    raise PipelineError("Encrypted archives are unsupported")
                if info.file_size > limits.file_bytes:
                    raise PipelineError("Archive member exceeds size limit")
                if info.file_size > max(1, info.compress_size) * limits.ratio:
                    raise PipelineError("Archive compression ratio exceeds limit")
                total += info.file_size
                if total > limits.expanded_bytes:
                    raise PipelineError("Expanded archive exceeds limit")
                inventory.append((info, name))
            # Validate file/parent conflicts before writing anything.
            files = {name.casefold() for info, name in inventory if not info.is_dir()}
            for _, name in inventory:
                if any(str(parent).casefold() in files for parent in PurePosixPath(name).parents if str(parent) != "."):
                    raise PipelineError("Archive file conflicts with directory")
            destination.mkdir(parents=True)
            output = []
            actual_total = 0
            for info, name in inventory:
                target = destination / name
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                size = 0
                with archive.open(info) as inp, target.open("xb") as out:
                    while chunk := inp.read(1024 * 1024):
                        size += len(chunk)
                        actual_total += len(chunk)
                        if size > info.file_size or actual_total > limits.expanded_bytes:
                            raise PipelineError("Archive actual size differs from header")
                        digest.update(chunk)
                        out.write(chunk)
                if size != info.file_size:
                    raise PipelineError("Truncated archive member")
                output.append({"path": name, "bytes": size, "sha256": digest.hexdigest()})
            return {"files": output, "expandedBytes": actual_total}
    except (zipfile.BadZipFile, NotImplementedError, OSError) as exc:
        raise PipelineError("Archive cannot be safely extracted") from exc


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("wb") as out:
        out.write(data)
        out.flush()
        os.fsync(out.fileno())
    os.replace(temporary, path)
