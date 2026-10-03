"""Private, data-only intake of paired raw sources and staged real evidence.

ZIP names, uploaded manifests and user-entered version strings are hints only.
Version verification requires operator-owned pins for the actual archive bytes.
Only installed data parsers run; no assembly is executed and partial evidence never publishes.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import shutil
from typing import BinaryIO
import uuid
import time
from contextlib import ExitStack
import zipfile

from .contracts import package_file
from .security import ArchiveLimits, PipelineError, extract_zip, atomic_write
from .adapters import file_digest
from .stage_metrics import StageMetrics


def _raw_checkpoint(canceled, deadline, *, clock=None):
    """Check the deadline on every call; poll the persistent cancel file at 5 ms.

    Static data parsers may checkpoint millions of times. A filesystem stat per
    instruction adds no useful cancellation responsiveness. Final job-state
    checks and child-process cancellation remain immediate and unchanged.
    """
    clock = time.monotonic if clock is None else clock
    next_cancel_poll = float('-inf')
    def checkpoint():
        nonlocal next_cancel_poll
        now = clock()
        if now >= next_cancel_poll:
            if canceled():
                raise PipelineError('RAW_JOB_CANCELED')
            now = clock()
            next_cancel_poll = now + 0.005
        if now >= deadline:
            raise PipelineError('RAW_JOB_TIMEOUT')
    return checkpoint


@dataclass(frozen=True)
class TrustedSource:
    """Administrator configuration, not constructed from any uploaded field."""
    role: str
    archive_sha256: str
    game_version: str

    def __post_init__(self):
        if (self.role not in ("server", "client")
                or not re.fullmatch(r"[a-f0-9]{64}", self.archive_sha256)
                or not re.fullmatch(r"[0-9]+(?:\.[0-9]+){2,3}", self.game_version)):
            raise ValueError("Invalid trusted raw-source pin")


class RawInputPreflight:
    """Shares durable job records with Pipeline; all raw jobs stay unpublishable."""
    def __init__(self, pipeline, limits: ArchiveLimits = ArchiveLimits(),
                 trusted_sources: tuple[TrustedSource, ...] = (), texture_extractor=None, semantic_producer=None):
        self.pipeline = pipeline
        self.texture_extractor = texture_extractor
        self.semantic_producer = semantic_producer
        self.limits = limits
        self.trusted_sources = {}
        for source in trusted_sources:
            key = (source.role, source.archive_sha256)
            if key in self.trusted_sources:
                raise ValueError("Duplicate raw-source fingerprint policy")
            self.trusted_sources[key] = source.game_version

    def submit(self, server_file: tuple[BinaryIO, str] | None = None,
               client_file: tuple[BinaryIO, str] | None = None,
               declared_version: str | None = None) -> dict:
        if declared_version is not None:
            declared_version = declared_version.strip() or None
        if declared_version is not None and not re.fullmatch(r"[0-9]+(?:\.[0-9]+){2,3}", declared_version):
            raise PipelineError("版本提示须为 1.4.5.0 形式；提示不会作为版本证明")
        identity = str(uuid.uuid4())
        directory = self.pipeline.root / "jobs" / identity
        directory.mkdir(mode=0o700, parents=True)
        total = 0
        sources = {}
        try:
            for role, upload in (("server", server_file), ("client", client_file)):
                if upload is None:
                    continue
                stream, filename = upload
                digest = hashlib.sha256()
                count = 0
                descriptor = os.open(directory / f"{role}.zip", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as output:
                    while True:
                        chunk = stream.read(min(1024 * 1024, self.limits.archive_bytes - total + 1))
                        if not isinstance(chunk, bytes):
                            raise PipelineError("上传流必须为二进制数据")
                        if not chunk:
                            break
                        total += len(chunk)
                        count += len(chunk)
                        if total > self.limits.archive_bytes:
                            raise PipelineError("两个输入 ZIP 的合计体积超过上传限制")
                        digest.update(chunk)
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                sources[role] = {"role": role, "filename": Path(filename.replace("\\", "/")).name[:160],
                                 "archiveSha256": digest.hexdigest(), "archiveBytes": count}
            job = {"id": identity, "kind": "raw-input-preflight", "state": "UPLOADED",
                   "createdAt": datetime.now(timezone.utc).isoformat(), "filename": "真实输入配对预检",
                   "declaredVersion": declared_version, "sources": sources, "inputBytes": total,
                   "executedInput": False, "extractionComplete": False, "blockers": []}
            self.pipeline.save(job)
            return job
        except BaseException:
            shutil.rmtree(directory)
            raise

    def request_cancel(self, identity: str) -> dict:
        job = self.pipeline.get(identity)
        if job.get("kind") != "raw-input-preflight" or job["state"] not in {"UPLOADED", "EXTRACTING"}:
            raise PipelineError("Only an active raw-input job can be canceled")
        atomic_write(self.pipeline.root / "jobs" / job["id"] / "cancel.request", b"1")
        return {"id": job["id"], "cancelRequested": True, "state": job["state"]}

    def retry(self, identity: str) -> dict:
        job = self.pipeline.get(identity)
        if job.get("kind") != "raw-input-preflight" or job["state"] not in {"BLOCKED", "CANCELED", "INTERRUPTED"}:
            raise PipelineError("Only an incomplete terminal raw-input job can be retried")
        directory = self.pipeline.root / "jobs" / job["id"]
        with ExitStack() as stack:
            uploads = {}
            for role, source in job["sources"].items():
                path = package_file(directory, f"{role}.zip")
                if path.stat().st_size != source["archiveBytes"] or file_digest(path, self.limits.archive_bytes) != source["archiveSha256"]:
                    raise PipelineError("Retry source fingerprint changed")
                uploads[role + "_file"] = (stack.enter_context(path.open("rb")), source["filename"])
            return self.submit(**uploads, declared_version=job["declaredVersion"])

    @staticmethod
    def _block(code: str, message: str, role: str | None = None) -> dict:
        return {"code": code, "message": message, **({"role": role} if role else {})}

    def process(self, identity: str) -> dict:
        try:
            return self._process(identity)
        except BaseException as failure:
            # A launch, disk, or unexpected parser failure must not leave an
            # unretryable EXTRACTING record. Never expose exception text/paths.
            try:
                job = self.pipeline.get(identity)
            except (KeyError, OSError):
                raise failure
            if job.get("kind") != "raw-input-preflight" or job.get("state") != "EXTRACTING":
                raise
            directory = self.pipeline.root / "jobs" / job["id"]
            for role in ("server", "client"):
                shutil.rmtree(directory / f"{role}-texture-job", ignore_errors=True)
            shutil.rmtree(directory / "adapter-evidence", ignore_errors=True)
            job.pop("producerEvidence", None)
            job.pop("candidate", None)
            (directory / "sealed-candidate.json").unlink(missing_ok=True)
            code = "RAW_PROCESSING_FAILED" if isinstance(failure, Exception) else "RAW_PROCESSING_INTERRUPTED"
            job.update(state="BLOCKED" if isinstance(failure, Exception) else "INTERRUPTED", textures={},
                       extractionComplete=False, executedInput=False, error=code,
                       blockers=[self._block(code, code)])
            self.pipeline.save(job)
            if not isinstance(failure, Exception):
                raise
            return job

    def _process(self, identity: str) -> dict:
        with self.pipeline.lock:
            job = self.pipeline.get(identity)
            if job.get("kind") != "raw-input-preflight" or job["state"] != "UPLOADED":
                raise PipelineError("此任务不能重复预检")
            job["state"] = "EXTRACTING"
            self.pipeline.save(job)
            with StageMetrics().persist(job, self.pipeline.save) as metrics:
                return self._process_measured(identity, job, metrics)

    def _process_measured(self, identity: str, job: dict, metrics: StageMetrics) -> dict:
        # The caller holds the serial job lock through metrics finalization.
        directory = self.pipeline.root / "jobs" / identity
        blockers = []
        deadline = time.monotonic() + 120
        canceled = lambda: (directory / "cancel.request").exists()
        checkpoint = _raw_checkpoint(canceled, deadline)
        def stopped():
            if not canceled() and time.monotonic() < deadline:
                return False
            code = "RAW_JOB_CANCELED" if canceled() else "RAW_JOB_TIMEOUT"
            for role in ("server", "client"):
                shutil.rmtree(directory / f"{role}-texture-job", ignore_errors=True)
            shutil.rmtree(directory / "adapter-evidence", ignore_errors=True)
            job.pop("producerEvidence", None)
            job.pop("candidate", None)
            (directory / "sealed-candidate.json").unlink(missing_ok=True)
            job.update(state="CANCELED" if canceled() else "BLOCKED", textures={},
                       extractionComplete=False, executedInput=False, blockers=[self._block(code, code)], error=code)
            self.pipeline.save(job)
            return True
        expanded = entries = 0
        verified_inventories = {}
        for role in ("server", "client"):
            if stopped(): return job
            source = job["sources"].get(role)
            if source is None:
                blockers.append(self._block(f"MISSING_{role.upper()}_INPUT",
                    "缺少服务端 ZIP" if role == "server" else "缺少同版本客户端 Content ZIP；服务端包不能代替客户端贴图", role))
                continue
            version = self.trusted_sources.get((role, source["archiveSha256"]))
            source["versionEvidence"] = {"status": "verified" if version else "unverified", "gameVersion": version,
                "method": "operator-pinned-archive-sha256" if version else None}
            if version is None:
                blockers.append(self._block("VERSION_UNVERIFIED", "实际 ZIP 哈希尚未命中操作者审核的版本记录；文件名和上传元数据不作为证明", role))
            elif job["declaredVersion"] is not None and job["declaredVersion"] != version:
                blockers.append(self._block("DECLARED_VERSION_MISMATCH", "用户输入的版本提示与该来源的可信哈希记录不一致", role))
            extracted = directory / f"{role}-files"
            try:
                with metrics.stage(f"{role}_verify"):
                    archive = package_file(directory, f"{role}.zip")
                    if archive.stat().st_size != source["archiveBytes"]:
                        raise PipelineError("上传后原始 ZIP 字节发生变化")
                    actual_hash = hashlib.sha256()
                    actual_size = 0
                    with archive.open("rb") as raw:
                        while chunk := raw.read(1024 * 1024):
                            checkpoint()
                            actual_size += len(chunk)
                            if actual_size > source["archiveBytes"]:
                                raise PipelineError("上传后原始 ZIP 字节发生变化")
                            actual_hash.update(chunk)
                    if actual_size != source["archiveBytes"] or actual_hash.hexdigest() != source["archiveSha256"]:
                        raise PipelineError("上传后原始 ZIP 字节发生变化")
                remaining = replace(self.limits, expanded_bytes=self.limits.expanded_bytes - expanded,
                                    files=self.limits.files - entries)
                with metrics.stage(f"{role}_unpack"):
                    inventory = extract_zip(archive, extracted, remaining, checkpoint=checkpoint)
                expanded += inventory["expandedBytes"]
                entries += inventory["entryCount"]
                source["inventory"] = inventory
                source["inventoryStatus"] = "verified"
                # Separate in-memory proof from the mutable durable job
                # record; only bytes hashed by this extract_zip call qualify.
                verified_inventories[role] = tuple(dict(row) for row in inventory["files"])
            except BaseException as exc:
                # Keep the source archive and successful inventory for review;
                # partial expansion never becomes accepted evidence.
                if extracted.exists():
                    shutil.rmtree(extracted)
                if not isinstance(exc, Exception):
                    job.update(state="INTERRUPTED", error="来源预检已中断，未执行或发布；请重新提交", blockers=blockers)
                    self.pipeline.save(job)
                    raise
                source["inventoryStatus"] = "rejected"
                source["versionEvidence"] = {"status": "unverified", "gameVersion": None, "method": None}
                blockers.append(self._block("INVALID_ARCHIVE", str(exc)[:500] if isinstance(exc, PipelineError)
                                           else "ZIP 无法安全读取", role))
        # Decode raw textures automatically when present in either uploaded
        # package, including a combined server + Content ZIP. Never accept
        # game code as a converter and never treat PNG reuse as this step.
        job["textures"] = {}
        for role, source in job["sources"].items():
            if stopped(): return job
            if source.get("inventoryStatus") != "verified":
                continue
            paths = source["inventory"]["files"]
            count = sum(row["path"].lower().endswith(".xnb") for row in paths)
            if count == 0:
                job["textures"][role] = {"status": "NO_XNB_PAYLOAD", "imageCount": 0,
                                        "executedInput": False, "publishable": False}
            elif self.texture_extractor is None:
                job["textures"][role] = {"status": "CONVERTER_NOT_INSTALLED", "xnbCount": count}
            else:
                try:
                    options = {"cancel_check": canceled, "deadline": deadline} if getattr(self.texture_extractor, "supports_cancellation", False) else {}
                    with metrics.stage(f"{role}_textures"):
                        job["textures"][role] = self.texture_extractor.extract(
                            directory / f"{role}-files", directory / f"{role}-texture-job", **options)
                except PipelineError as exc:
                    job["textures"][role] = {"status": "BLOCKED", "error": str(exc),
                                            "publishable": False}
        if any(any(row["path"].lower().endswith(".xnb") for row in source.get("inventory", {}).get("files", []))
               for source in job["sources"].values()):
            if any(blocker["code"] == "MISSING_CLIENT_INPUT" for blocker in blockers):
                blockers = [blocker for blocker in blockers if blocker["code"] != "MISSING_CLIENT_INPUT"]
                blockers.append(self._block("COMBINED_TEXTURE_SOURCE_UNVERIFIED",
                    "包内发现 XNB，可自动尝试解码；仍需验证原始客户端贴图来源、版本和覆盖，不能据此认定客户端资源完整"))
        if stopped(): return job
        versions = {source["versionEvidence"]["gameVersion"] for source in job["sources"].values()
                    if source.get("versionEvidence", {}).get("status") == "verified"}
        if len(versions) > 1:
            blockers.append(self._block("SOURCE_VERSION_MISMATCH", "服务端与客户端的可信版本不一致"))
        if self.semantic_producer is None:
            blockers.append(self._block("NO_TRUSTED_ADAPTER", "未安装真实语义生产器，不能审核或发布"))
        else:
            roots = {role: directory / f"{role}-files" for role, source in job["sources"].items()
                     if source.get("inventoryStatus") == "verified"}
            try:
                from .real_producer import RawEvidenceProducer
                options = {"checkpoint": checkpoint}
                if type(self.semantic_producer) is RawEvidenceProducer:
                    options["expected_inventories"] = {role: [dict(row) for row in verified_inventories[role]]
                                                       for role in roots}
                with metrics.stage("semantic_producer"):
                    job["producerEvidence"] = self.semantic_producer.produce(roots, job["textures"],
                        directory / "adapter-evidence", **options)
                blockers.append(self._block("SEMANTIC_COVERAGE_INCOMPLETE", "静态程序集与原始纹理证据已生成；完整属性、动态说明、帧及像素规则尚未核实，不能审核发布"))
            except PipelineError:
                blockers.append(self._block("SEMANTIC_PRODUCER_REJECTED", "真实语义生产器拒绝输入，未生成可审核候选"))
        if stopped(): return job
        job.update(blockers=blockers, extractedBytes=expanded,
                   archiveEntries=entries, executedInput=False, extractionComplete=False,
                   error="；".join(blocker["message"] for blocker in blockers))
        if job.get("producerEvidence"):
            from .candidate import seal_candidate
            try:
                job["candidate"] = seal_candidate(directory, job, checkpoint=checkpoint)
            except PipelineError:
                job["blockers"].append(self._block("CANDIDATE_SEAL_REJECTED", "候选证据校验失败，不能审核发布"))
                job["error"] = "；".join(blocker["message"] for blocker in job["blockers"])
        if stopped(): return job
        job["state"] = "BLOCKED"
        self.pipeline.save(job)
        return job
