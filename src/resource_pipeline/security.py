"""Bounded data-only archive handling; never execute an uploaded file."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import struct
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


def validate_zip_directory(source: Path, limits: ArchiveLimits, checkpoint=None) -> int:
    """Bound central metadata BEFORE ZipFile eagerly allocates all ZipInfo rows.

    Only one-disk, non-ZIP64 end records with absolute consistent offsets are
    supported. Reject ambiguous comments/trailers rather than selecting a
    different EOCD from Python's last-signature rule.
    """
    check = checkpoint or (lambda: None)
    check()
    size = source.stat().st_size
    if size > limits.archive_bytes or size < 22:
        raise PipelineError("Invalid or oversized ZIP archive")
    with source.open("rb") as stream:
        tail_bytes = min(size, 65535 + 22)
        stream.seek(size - tail_bytes)
        tail = stream.read(tail_bytes)
        index = tail.rfind(b"PK\x05\x06")
        if index < 0 or index + 22 > len(tail):
            raise PipelineError("ZIP end record is missing or ambiguous")
        _, disk, directory_disk, local_count, count, length, offset, comment = struct.unpack_from("<4s4H2IH", tail, index)
        end_offset = size - tail_bytes + index
        if index + 22 + comment != len(tail):
            raise PipelineError("ZIP trailing bytes or ambiguous end-record comment")
        if disk or directory_disk or local_count != count or count == 65535:
            raise PipelineError("Multi-disk or ZIP64 end records are unsupported")
        if count > limits.files:
            raise PipelineError("Too many archive entries")
        if length > 64 * 1024 * 1024 or offset + length != end_offset:
            raise PipelineError("ZIP central directory exceeds or conflicts with bounded layout")
        if end_offset >= 20:
            stream.seek(end_offset - 20)
            if stream.read(4) == b"PK\x06\x07":
                raise PipelineError("ZIP64 locator is unsupported")
        stream.seek(offset)
        position, actual = offset, 0
        while position < end_offset:
            check()
            header = stream.read(46)
            if len(header) != 46 or header[:4] != b"PK\x01\x02":
                raise PipelineError("Malformed ZIP central-directory record")
            name_bytes, extra_bytes, comment_bytes, disk_start = struct.unpack_from("<4H", header, 28)
            record_bytes = 46 + name_bytes + extra_bytes + comment_bytes
            if disk_start or name_bytes == 0 or position + record_bytes > end_offset:
                raise PipelineError("ZIP central-directory record exceeds its bounded region")
            actual += 1
            if actual > limits.files or actual > count:
                raise PipelineError("Too many or inconsistent archive entries")
            position += record_bytes
            stream.seek(position)
        if actual != count or position != end_offset:
            raise PipelineError("ZIP central-directory count mismatch")
    check()
    return count


def extract_zip(source: Path, destination: Path, limits: ArchiveLimits = ArchiveLimits(), *, checkpoint=None) -> dict:
    if source.stat().st_size > limits.archive_bytes:
        raise PipelineError("Archive exceeds upload limit")
    if destination.exists():
        raise PipelineError("Extraction directory must be new")
    entry_count = validate_zip_directory(source, limits, checkpoint)
    inventory = []
    seen = set()
    total = 0
    try:
        with zipfile.ZipFile(source) as archive:
            entries = archive.infolist()
            if len(entries) != entry_count:
                raise PipelineError("ZIP metadata changed after bounded prevalidation")
            if checkpoint is not None: checkpoint()
            if len(entries) > limits.files:
                raise PipelineError("Too many archive entries")
            for info in entries:
                if checkpoint is not None: checkpoint()
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
            files = set()
            for info, name in inventory:
                if checkpoint is not None: checkpoint()
                if not info.is_dir(): files.add(name.casefold())
            for _, name in inventory:
                for parent in PurePosixPath(name).parents:
                    if checkpoint is not None: checkpoint()
                    if str(parent) != "." and str(parent).casefold() in files:
                        raise PipelineError("Archive file conflicts with directory")
            destination.mkdir(parents=True)
            output = []
            actual_total = 0
            for info, name in inventory:
                if checkpoint is not None: checkpoint()
                target = destination / name
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                size = 0
                with archive.open(info) as inp, target.open("xb") as out:
                    while chunk := inp.read(1024 * 1024):
                        if checkpoint is not None: checkpoint()
                        size += len(chunk)
                        actual_total += len(chunk)
                        if size > info.file_size or actual_total > limits.expanded_bytes:
                            raise PipelineError("Archive actual size differs from header")
                        digest.update(chunk)
                        out.write(chunk)
                if size != info.file_size:
                    raise PipelineError("Truncated archive member")
                output.append({"path": name, "bytes": size, "sha256": digest.hexdigest()})
            return {"files": output, "expandedBytes": actual_total, "entryCount": entry_count}
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
