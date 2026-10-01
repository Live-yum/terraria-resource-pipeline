from __future__ import annotations

from datetime import datetime, timezone
import json
import gzip
from pathlib import Path
import shutil
import sqlite3
import subprocess
from threading import RLock
import uuid

from .catalog import build_catalog, compare_versions
from .security import ArchiveLimits, PipelineError, atomic_write, canonical_json, extract_zip, read_json, sha256


class Pipeline:
    """Local demo: private staging and a separate, real bare Git CDN simulator.

    No uploaded value can change the publication destination or command line.
    The reentrant lock serializes job/publication state in this single-worker
    demo. Production deployment must use a shared transactional queue/lease.
    """
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        self.database = self.root / "jobs.sqlite"
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, state TEXT NOT NULL, body TEXT NOT NULL)")
            # Interrupted publication needs reconciliation, not automatic
            # replay. Nothing gets pushed merely because the server restarts.
            for identity, body in db.execute("SELECT id,body FROM jobs WHERE state IN ('EXTRACTING','PUBLISHING')").fetchall():
                job = json.loads(body)
                job.update(state="INTERRUPTED", error="任务被中断，请检查已发布状态后重新提交")
                db.execute("UPDATE jobs SET state=?,body=? WHERE id=?", (job["state"], json.dumps(job), identity))
        self.remote = self.root / "cdn.git"
        self.checkout = self.root / "cdn-checkout"
        if not self.remote.exists():
            self.git(["init", "--bare", "--initial-branch=main", str(self.remote)])
        if not self.checkout.exists():
            self.git(["clone", str(self.remote), str(self.checkout)])
            self.git(["config", "user.name", "Resource pipeline demo"], self.checkout)
            self.git(["config", "user.email", "demo@example.invalid"], self.checkout)
            atomic_write(self.checkout / "README.md", b"Synthetic CDN publication simulator. No game assets.\n")
            self.git(["add", "README.md"], self.checkout)
            self.git(["commit", "-m", "Initialize synthetic CDN simulator"], self.checkout)
            self.git(["push", "origin", "main"], self.checkout)

    def connect(self):
        return sqlite3.connect(self.database, timeout=20)

    @staticmethod
    def git(arguments: list[str], cwd: Path | None = None) -> str:
        result = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *arguments], cwd=cwd, text=True,
                                encoding="utf-8", errors="replace", stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        if result.returncode:
            raise PipelineError("Local Git publication operation failed")
        return result.stdout.strip()

    def save(self, job: dict):
        with self.connect() as db:
            db.execute("INSERT INTO jobs VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET state=excluded.state,body=excluded.body",
                       (job["id"], job["state"], canonical_json(job).decode()))

    def get(self, identity: str) -> dict:
        with self.connect() as db:
            result = db.execute("SELECT body FROM jobs WHERE id=?", (identity,)).fetchone()
        if not result:
            raise KeyError(identity)
        return json.loads(result[0])

    def list(self) -> list[dict]:
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT body FROM jobs ORDER BY rowid DESC LIMIT 100")]

    def current(self) -> dict | None:
        try:
            return json.loads(self.published_file("channels/stable.json"))
        except FileNotFoundError:
            return None

    def published_file(self, path: str) -> bytes:
        from .security import relative_path
        relative_path(path)
        result = subprocess.run(["git", "--git-dir", str(self.remote), "show", f"main:{path}"],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        if result.returncode:
            raise FileNotFoundError(path)
        if len(result.stdout) > 128 * 1024 * 1024:
            raise PipelineError("Published object exceeds response limit")
        return result.stdout

    def submit(self, content: bytes, filename: str) -> dict:
        if len(content) > ArchiveLimits().archive_bytes:
            raise PipelineError("上传包超过体积限制")
        identity = str(uuid.uuid4())
        directory = self.root / "jobs" / identity
        directory.mkdir(parents=True)
        atomic_write(directory / "input.zip", content)
        job = {"id": identity, "state": "UPLOADED", "createdAt": datetime.now(timezone.utc).isoformat(),
               "filename": Path(filename.replace("\\", "/")).name[:160], "inputSha256": sha256(content), "inputBytes": len(content)}
        self.save(job)
        return job

    def process(self, identity: str) -> dict:
        with self.lock:
            job = self.get(identity)
            if job["state"] != "UPLOADED":
                raise PipelineError("此任务不能重复提取")
            job["state"] = "EXTRACTING"
            self.save(job)
            folder = self.root / "jobs" / identity
            try:
                inventory = extract_zip(folder / "input.zip", folder / "input")
                built = build_catalog(folder / "input", folder / "staged", job["inputSha256"])
                current = self.current()
                prior_hashes = json.loads(self.published_file(f"releases/{current['release']}/record-hashes.json")) if current else None
                diff = compare_versions(prior_hashes, built["recordHashes"])
                job.update(state="READY_FOR_REVIEW" if built["manifest"]["coverage"]["complete"] else "BLOCKED",
                           release=built["release"], coverage=built["manifest"]["coverage"], diff=diff,
                           gameVersion=built["manifest"]["gameVersion"], sourceKind=built["manifest"]["sourceKind"],
                           manifest=built["manifest"], extractedBytes=inventory["expandedBytes"], objectBytes=built["objectBytes"],
                           baseRelease=current["release"] if current else None)
                # Browser acknowledgment is bound to both the complete output
                # manifest and the exact displayed baseline/diff.
                job["reviewDigest"] = sha256(canonical_json({key: job[key] for key in ("inputSha256", "release", "baseRelease", "diff", "coverage")}))
            except Exception as exc:
                job.update(state="BLOCKED", error=str(exc)[:1000] if isinstance(exc, PipelineError) else "资源格式或提取失败，未发布")
            self.save(job)
            return job

    def publish(self, identity: str, review_digest: str, confirmed: bool) -> dict:
        with self.lock:
            job = self.get(identity)
            if job["state"] != "READY_FOR_REVIEW" or not confirmed or review_digest != job.get("reviewDigest"):
                raise PipelineError("必须确认当前完整变更预览；过期确认不能发布")
            current = self.current()
            if (current["release"] if current else None) != job["baseRelease"]:
                raise PipelineError("当前版本已变更，请基于新基线重新提取和审核")
            if job["sourceKind"] != "synthetic":
                raise PipelineError("公开演示只允许合成资源；真实导出需要私有部署及独立权利审核")
            staged = self.root / "jobs" / identity / "staged"
            manifest_bytes = (staged / "manifest.json").read_bytes()
            if sha256(manifest_bytes) != job["release"]:
                raise PipelineError("暂存清单在审核后被修改")
            if self.git(["status", "--porcelain"], self.checkout) or self.git(["rev-parse", "HEAD"], self.checkout) != self.git(["--git-dir", str(self.remote), "rev-parse", "main"]):
                raise PipelineError("本地发布工作区需要人工恢复，不能覆盖未确认的变更")
            manifest = json.loads(manifest_bytes)
            references = [*manifest["packs"].values(), manifest["strings"], manifest["images"]]
            image_index = staged / manifest["images"]["path"]
            if not image_index.is_file() or sha256(image_index.read_bytes()) != manifest["images"]["sha256"]:
                raise PipelineError("缺少或损坏图片索引")
            references.extend(json.loads(gzip.decompress(image_index.read_bytes())).values())
            expected_paths = set()
            for reference in references:
                file = staged / reference["path"]
                expected_paths.add(reference["path"])
                if not file.is_file() or file.is_symlink() or sha256(file.read_bytes()) != reference["sha256"] or file.stat().st_size != reference["bytes"]:
                    raise PipelineError("资源对象缺失或校验失败")
            actual_paths = {str(file.relative_to(staged)).replace('\\', '/') for file in (staged / "objects").rglob("*") if file.is_file()}
            if actual_paths != expected_paths:
                raise PipelineError("暂存目录包含未审核的多余对象")
            # Verify every staged object before it can enter the Git tree.
            for file in (staged / "objects").rglob("*"):
                if file.is_file() and (file.is_symlink() or sha256(file.read_bytes()) != file.name.split(".")[0]):
                    raise PipelineError("暂存对象校验失败")
            job["state"] = "PUBLISHING"
            self.save(job)
            try:
                for file in (staged / "objects").rglob("*"):
                    if file.is_file():
                        target = self.checkout / file.relative_to(staged)
                        if target.exists() and target.read_bytes() != file.read_bytes():
                            raise PipelineError("CDN对象发生冲突")
                        if not target.exists():
                            atomic_write(target, file.read_bytes())
                release_dir = self.checkout / "releases" / job["release"]
                atomic_write(release_dir / "manifest.json", manifest_bytes)
                atomic_write(release_dir / "record-hashes.json", (staged / "record-hashes.json").read_bytes())
                pointer = {"schemaVersion": 1, "release": job["release"], "manifest": f"releases/{job['release']}/manifest.json",
                           "manifestSha256": job["release"], "previous": job["baseRelease"], "gameVersion": job["gameVersion"]}
                atomic_write(self.checkout / "channels/stable.json", canonical_json(pointer))
                self.git(["add", "objects", "releases", "channels"], self.checkout)
                self.git(["commit", "-m", f"Publish reviewed synthetic release {job['release'][:12]}"], self.checkout)
                commit = self.git(["rev-parse", "HEAD"], self.checkout)
                self.git(["push", "origin", "main"], self.checkout)
                remote_head = self.git(["--git-dir", str(self.remote), "rev-parse", "refs/heads/main"])
                if remote_head != commit:
                    raise PipelineError("Remote commit verification failed")
                job.update(state="PUBLISHED", commit=commit, publicationDestination="local-git-cdn-simulator")
            except Exception:
                job.update(state="INTERRUPTED", error="发布未确认，请检查Git记录；不会自动重试")
                self.save(job)
                raise
            self.save(job)
            return job
