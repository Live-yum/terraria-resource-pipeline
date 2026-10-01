"""Administrator-registered extraction adapters; uploads never choose commands.

External producers are disabled unless registered with exact source/tool pins.
The production backend uses bubblewrap and never falls back to host execution.
Only normalized data leaves a private job; publication is deliberately absent.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import time
from typing import Protocol

from .contracts import CoverageProfile, InputBinding, package_file, validate_contract
from .models import ExtractionEnvelope
from .security import PipelineError, atomic_write, canonical_json, read_json, relative_path, sha256


@dataclass(frozen=True)
class AdapterLimits:
    timeout_seconds: float = 120
    files: int = 50_000
    file_bytes: int = 128 * 1024 * 1024
    total_bytes: int = 2 * 1024 * 1024 * 1024
    memory_bytes: int = 2 * 1024 * 1024 * 1024
    processes: int = 64


def file_digest(path: Path, maximum: int = 128 * 1024 * 1024) -> str:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > maximum:
        raise PipelineError("Invalid or oversized regular input file")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def tree_inventory(root: Path, limits: AdapterLimits = AdapterLimits()) -> list[dict]:
    if root.is_symlink() or not root.is_dir():
        raise PipelineError("Input/output root must be a regular directory")
    entries: list[dict] = []
    total = 0
    seen: set[str] = set()
    for directory, directories, files in os.walk(root, followlinks=False):
        for name in [*directories, *files]:
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            relative_path(relative)
            mode = path.lstat().st_mode
            if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                raise PipelineError("Links and special files are forbidden in adapter inputs/outputs")
            key = relative.casefold()
            if key in seen or len(seen) >= limits.files:
                raise PipelineError("Adapter tree exceeds path/count policy")
            seen.add(key)
            if stat.S_ISREG(mode):
                size = path.stat().st_size
                total += size
                if size > limits.file_bytes or total > limits.total_bytes:
                    raise PipelineError("Adapter tree exceeds its byte budget")
                entries.append({"path": relative, "bytes": size, "sha256": file_digest(path, limits.file_bytes)})
    return sorted(entries, key=lambda entry: entry["path"])


def tree_digest(root: Path, limits: AdapterLimits = AdapterLimits()) -> str:
    """SHA256 of canonical [{path,bytes,sha256}], sorted by relative POSIX path."""
    return sha256(canonical_json(tree_inventory(root, limits)))


@dataclass(frozen=True)
class SourceInputs:
    game_version: str
    server: Path
    client: Path | None = None
    metadata: Path | None = None


@dataclass(frozen=True)
class AdapterSpec:
    adapter_id: str
    adapter_version: str
    profile: CoverageProfile
    tool_root: Path
    tool_sha256: str
    # Fixed server configuration, not a shell string, upload field or extra flags.
    # Tool paths use /tool, and all job paths arrive through request.json.
    command: tuple[str, ...]
    approved_server_sha256: frozenset[str]
    approved_client_sha256: frozenset[str] = frozenset()
    approved_metadata_sha256: frozenset[str] = frozenset()
    requires_client: bool = True
    requires_metadata: bool = True
    executes_game_assembly: bool = False
    # Optional private copy: legacy producers may write here, never in tool_root.
    use_tool_shadow: bool = False
    limits: AdapterLimits = AdapterLimits()


class AdapterRegistry:
    """Server-owned registry. Do not expose registration through upload/API data."""
    def __init__(self):
        self._specs: dict[str, AdapterSpec] = {}

    def register(self, spec: AdapterSpec) -> None:
        if spec.adapter_id in self._specs or not spec.command or not spec.approved_server_sha256:
            raise PipelineError("Adapter registration requires a unique name and pinned inputs")
        if spec.requires_client and not spec.approved_client_sha256:
            raise PipelineError("Client-dependent adapters require pinned client input hashes")
        if spec.requires_metadata and not spec.approved_metadata_sha256:
            raise PipelineError("Metadata adapters require pinned metadata input hashes")
        if not Path(spec.command[0]).is_absolute() or any("\x00" in arg for arg in spec.command):
            raise PipelineError("Adapter commands must use a fixed absolute executable")
        if spec.executes_game_assembly and spec.profile.source_kind == "synthetic":
            raise PipelineError("Synthetic adapters may not execute game assemblies")
        self._specs[spec.adapter_id] = spec

    def get(self, adapter_id: str) -> AdapterSpec:
        try:
            return self._specs[adapter_id]
        except KeyError as exc:
            raise PipelineError("No trusted adapter is installed for this input; raw game execution is disabled") from exc

    def describe(self) -> list[dict]:
        return [{"id": item.adapter_id, "version": item.adapter_version,
                 "gameVersion": item.profile.game_version, "profile": item.profile.profile_id,
                 "executesGameAssembly": item.executes_game_assembly}
                for item in self._specs.values()]


@dataclass(frozen=True)
class CommandPlan:
    argv: tuple[str, ...]
    cwd: Path
    env: dict[str, str]
    isolation: str


class SandboxBackend(Protocol):
    """Trusted deployment dependency, never selected or instantiated by uploads."""
    def plan(self, spec: AdapterSpec, job: Path) -> CommandPlan: ...


class BubblewrapSandbox:
    """Linux read-only mounts + separate network/PID/user namespaces.

    Runtime roots are administrator-selected, immutable, non-secret dependency
    trees. No host /home, /root, /workspace or environment secrets are exposed.
    A deployment should additionally apply a cgroup/quota to this worker.
    """
    def __init__(self, executable: Path = Path("/usr/bin/bwrap"),
                 runtime_roots: tuple[Path, ...] = (Path("/usr"), Path("/lib"), Path("/lib64"))):
        self.executable = executable
        self.runtime_roots = runtime_roots

    def plan(self, spec: AdapterSpec, job: Path) -> CommandPlan:
        if not self.executable.is_file():
            raise PipelineError("OS sandbox is unavailable; external adapter execution is disabled")
        argv = [str(self.executable), "--unshare-all", "--die-with-parent", "--new-session", "--cap-drop", "ALL",
                "--clearenv"]
        for root in self.runtime_roots:
            if root.exists():
                if not root.is_absolute() or str(root) in ("/", "/home", "/root", "/workspace"):
                    raise PipelineError("Unsafe sandbox runtime root")
                argv += ["--ro-bind", str(root), str(root)]
        # Snapshot inputs are mounted read-only. Originals are never mounted.
        argv += ["--ro-bind", str(job / "inputs"), "/inputs", "--ro-bind", str(job / "tool"), "/tool",
                 "--bind", str(job / "output"), "/output", "--bind", str(job / "work"), "/work",
                 "--proc", "/proc", "--dev", "/dev", "--remount-ro", "/dev",
                 "--bind", str(job / "work/tmp"), "/tmp", "--chdir", "/work",
                 "--remount-ro", "/"]
        environment = {"PATH": "/usr/bin:/bin", "HOME": "/work/home", "TMPDIR": "/work/tmp", "TEMP": "/work/tmp",
                       "TMP": "/work/tmp", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1", "LANG": "C.UTF-8",
                       "TERRARIA_HOOK_RUNTIME_DIRECTORY": "/work/runtime", "DOTNET_CLI_HOME": "/work/dotnet",
                       "NUGET_PACKAGES": "/work/nuget", "TERRARIA_CONTENT_ROOT": "/inputs/client"}
        for key, value in environment.items():
            argv += ["--setenv", key, value]
        argv += ["--", *spec.command, "--request", "/inputs/request.json", "--output", "/output"]
        return CommandPlan(tuple(argv), job, {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}, "bubblewrap-unshare-all")


def _copy_tree(source: Path, destination: Path, limits: AdapterLimits) -> None:
    before = tree_inventory(source, limits)
    destination.mkdir()
    # Explicit byte copies: no worktree operation or writable hardlink to originals.
    for entry in before:
        target = destination / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        with package_file(source, entry["path"]).open("rb") as read, target.open("xb") as write:
            shutil.copyfileobj(read, write, 1024 * 1024)
        target.chmod(0o500 if os.access(source / entry["path"], os.X_OK) else 0o400)
    if tree_inventory(destination, limits) != before or tree_inventory(source, limits) != before:
        raise PipelineError("Input/tool changed while taking its private snapshot")


def _limits_launcher(plan: CommandPlan, limits: AdapterLimits) -> list[str]:
    # A fresh interpreter applies POSIX limits before exec. Avoid preexec_fn in a
    # threaded web process. The launcher is fixed first-party code, not uploaded.
    code = ("import os,resource,sys; "
            f"resource.setrlimit(resource.RLIMIT_AS,({limits.memory_bytes},{limits.memory_bytes})); "
            f"resource.setrlimit(resource.RLIMIT_FSIZE,({limits.file_bytes},{limits.file_bytes})); "
            f"resource.setrlimit(resource.RLIMIT_NPROC,({limits.processes},{limits.processes})); "
            f"resource.setrlimit(resource.RLIMIT_CPU,({max(1, int(limits.timeout_seconds))},{max(2, int(limits.timeout_seconds)+1)})); "
            "resource.setrlimit(resource.RLIMIT_CORE,(0,0)); "
            "os.execv(sys.argv[1],sys.argv[1:])")
    return [sys.executable, "-I", "-S", "-c", code, *plan.argv]


def _working_size(job: Path, limits: AdapterLimits) -> None:
    # Cheap watchdog: hash and parse validation happens after the process exits.
    count = total = 0
    for root in (job / "output", job / "work"):
        for directory, directories, files in os.walk(root, followlinks=False):
            for name in [*directories, *files]:
                path = Path(directory) / name
                info = path.lstat()
                count += 1
                if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                    raise PipelineError("Adapter created a link or special output")
                if stat.S_ISREG(info.st_mode):
                    total += info.st_size
                    if info.st_size > limits.file_bytes:
                        raise PipelineError("Adapter exceeded output capacity")
                if count > limits.files or total > limits.total_bytes:
                    raise PipelineError("Adapter exceeded output capacity")


def validate_normalized_package(root: Path, binding: InputBinding, profile: CoverageProfile) -> dict:
    """A normalized package needs both the legacy-compatible envelope and proof."""
    try:
        envelope = ExtractionEnvelope.model_validate(read_json(package_file(root, "resource-bundle.json")))
    except PipelineError:
        raise
    except Exception as exc:
        raise PipelineError("Invalid normalized resource envelope") from exc
    if (envelope.game_version != binding.game_version or envelope.source_kind != binding.source_kind
            or envelope.adapter != binding.adapter_id):
        raise PipelineError("Normalized resource envelope does not match the adapter binding")
    expected = {role: value for role, value in (("server", binding.server_sha256), ("client", binding.client_sha256),
                                               ("metadata", binding.metadata_sha256)) if value is not None}
    actual = {source.role: source.sha256 for source in envelope.sources}
    if len(actual) != len(envelope.sources) or actual != expected:
        raise PipelineError("Normalized resource sources do not match actual input bytes")
    domains = {spec.key: {canonical_json(identity) for identity in spec.expected_ids} for spec in profile.claims}
    for family, claim in profile.catalog_domains.items():
        if {canonical_json(row.id) for row in envelope.families.get(family, [])} != domains[claim]:
            raise PipelineError("Consumer catalog IDs do not match the trusted coverage domain")
    for rows in envelope.families.values():
        for row in rows:
            for image in row.images:
                package_file(root, image)
    return validate_contract(root, binding, profile)


@dataclass(frozen=True)
class AdapterResult:
    package_root: Path
    binding: InputBinding
    coverage: dict
    isolation: str
    output_sha256: str


class TrustedAdapterRunner:
    def __init__(self, registry: AdapterRegistry, sandbox: SandboxBackend | None = None):
        self.registry = registry
        self.sandbox = sandbox or BubblewrapSandbox()

    def run(self, adapter_id: str, sources: SourceInputs, job: Path) -> AdapterResult:
        spec = self.registry.get(adapter_id)
        if sources.game_version != spec.profile.game_version:
            raise PipelineError("No compatible trusted adapter for this game version")
        if (spec.requires_client and sources.client is None) or (spec.requires_metadata and sources.metadata is None):
            raise PipelineError("This adapter requires matching client content and verified metadata")
        server_hash = file_digest(sources.server, spec.limits.file_bytes)
        client_hash = tree_digest(sources.client, spec.limits) if sources.client is not None else None
        metadata_hash = tree_digest(sources.metadata, spec.limits) if sources.metadata is not None else None
        if server_hash not in spec.approved_server_sha256:
            raise PipelineError("Server input fingerprint is not approved for this adapter")
        if client_hash is not None and client_hash not in spec.approved_client_sha256:
            raise PipelineError("Client input fingerprint is not approved for this adapter")
        if metadata_hash is not None and metadata_hash not in spec.approved_metadata_sha256:
            raise PipelineError("Metadata input fingerprint is not approved for this adapter")
        if tree_digest(spec.tool_root, spec.limits) != spec.tool_sha256:
            raise PipelineError("Installed adapter fingerprint mismatch")
        if sources.metadata is not None:
            metadata_binding = read_json(package_file(sources.metadata, "source-binding.json"))
            if metadata_binding != {"game_version": sources.game_version, "server_sha256": server_hash,
                                    "client_sha256": client_hash}:
                raise PipelineError("Metadata was generated from different server/client inputs")
        binding = InputBinding(game_version=sources.game_version, source_kind=spec.profile.source_kind,
                               server_sha256=server_hash, client_sha256=client_hash, metadata_sha256=metadata_hash,
                               adapter_id=spec.adapter_id, adapter_version=spec.adapter_version,
                               adapter_tool_sha256=spec.tool_sha256)
        job = job.absolute()
        if job.exists() or any(parent.is_symlink() for parent in [job, *job.parents]):
            raise PipelineError("Adapter job directory must be new and cannot traverse links")
        for source in (sources.server, sources.client, sources.metadata, spec.tool_root):
            if source is not None and (job == source.resolve() or source.resolve() in job.parents):
                raise PipelineError("Job workspace must be separate from all source roots")
        job.mkdir(parents=True)
        for folder in ("inputs", "output", "work"):
            (job / folder).mkdir()
        for folder in ("home", "tmp", "runtime", "dotnet", "nuget"):
            (job / "work" / folder).mkdir()
        try:
            with sources.server.open("rb") as read, (job / "inputs/server.bin").open("xb") as write:
                shutil.copyfileobj(read, write, 1024 * 1024)
            if file_digest(job / "inputs/server.bin", spec.limits.file_bytes) != server_hash:
                raise PipelineError("Server input changed while taking its snapshot")
            (job / "inputs/server.bin").chmod(0o400)
            for name, source in (("client", sources.client), ("metadata", sources.metadata)):
                if source is not None:
                    _copy_tree(source, job / "inputs" / name, spec.limits)
            _copy_tree(spec.tool_root, job / "tool", spec.limits)
            if tree_digest(job / "tool", spec.limits) != spec.tool_sha256:
                raise PipelineError("Tool snapshot does not match its approved fingerprint")
            for name, expected in (("client", client_hash), ("metadata", metadata_hash)):
                if expected is not None and tree_digest(job / "inputs" / name, spec.limits) != expected:
                    raise PipelineError("Input snapshot does not match its approved fingerprint")
            if spec.use_tool_shadow:
                _copy_tree(spec.tool_root, job / "work/shadow", spec.limits)
                for path in (job / "work/shadow").rglob("*"):
                    path.chmod(0o700 if path.is_dir() or os.access(path, os.X_OK) else 0o600)
            request = {"schema_version": 1, "binding": binding.model_dump(), "profile_id": spec.profile.profile_id,
                       "server": "/inputs/server.bin", "client": "/inputs/client" if sources.client else None,
                       "metadata": "/inputs/metadata" if sources.metadata else None,
                       "tool_shadow": "/work/shadow" if spec.use_tool_shadow else None}
            atomic_write(job / "inputs/request.json", canonical_json(request))
            (job / "inputs/request.json").chmod(0o400)
            plan = self.sandbox.plan(spec, job)
            self._execute(plan, spec.limits, job)
            # Even a successful producer exit cannot certify incomplete output.
            tree_inventory(job / "output", spec.limits)
            if file_digest(sources.server, spec.limits.file_bytes) != server_hash:
                raise PipelineError("Original server input changed during extraction")
            if tree_digest(spec.tool_root, spec.limits) != spec.tool_sha256:
                raise PipelineError("Original tool installation changed during extraction")
            for source, expected in ((sources.client, client_hash), (sources.metadata, metadata_hash)):
                if source is not None and tree_digest(source, spec.limits) != expected:
                    raise PipelineError("Original input changed during extraction")
            coverage = validate_normalized_package(job / "output", binding, spec.profile)
            result = AdapterResult(job / "output", binding, coverage, plan.isolation, tree_digest(job / "output", spec.limits))
            atomic_write(job / "result.json", canonical_json({"binding": binding.model_dump(), "coverage": coverage,
                         "isolation": plan.isolation, "outputSha256": result.output_sha256}))
            return result
        except PipelineError:
            atomic_write(job / "status.json", canonical_json({"state": "BLOCKED", "publishable": False}))
            raise
        except Exception as exc:
            atomic_write(job / "status.json", canonical_json({"state": "BLOCKED", "publishable": False}))
            raise PipelineError("Trusted adapter failed; private diagnostics were not exposed") from exc

    @staticmethod
    def _execute(plan: CommandPlan, limits: AdapterLimits, job: Path) -> None:
        if os.name != "posix":
            raise PipelineError("This external adapter runner requires a configured POSIX sandbox")
        process: subprocess.Popen | None = None
        try:
            # Output is discarded, not returned to a browser or retained unbounded.
            process = subprocess.Popen(_limits_launcher(plan, limits), cwd=plan.cwd, env=plan.env,
                                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       start_new_session=True, close_fds=True)
            deadline = time.monotonic() + limits.timeout_seconds
            while process.poll() is None:
                _working_size(job, limits)
                if time.monotonic() >= deadline:
                    raise PipelineError("Trusted adapter timed out; no output was approved")
                time.sleep(0.025)
            _working_size(job, limits)
            if process.returncode != 0:
                raise PipelineError("Trusted adapter or OS sandbox failed; no output was approved")
        finally:
            if process is not None:
                # Kill the process group even when its leader exited successfully:
                # an adapter must not leave descendants modifying validated output.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
