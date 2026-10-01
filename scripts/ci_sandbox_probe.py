#!/usr/bin/env python3
"""Real bubblewrap acceptance, with explicit PASS / FAIL / BLOCKED evidence.

No fake backend, host execution fallback, sudo, or security-setting changes.
Exit 0 means all actual isolation checks passed; 1 means a test failed; 2 means
the real sandbox could not start. Both 1 and 2 must fail CI.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import errno
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import time

from resource_pipeline.adapters import (AdapterLimits, AdapterRegistry, AdapterSpec,
    BubblewrapSandbox, SourceInputs, TrustedAdapterRunner, tree_digest)
from resource_pipeline.adapters_synthetic import SYNTHETIC_SERVER, synthetic_profile, write_synthetic_package
from resource_pipeline.security import PipelineError, canonical_json, sha256


CHECKS = ("unknown_adapter_rejected", "prepare_pinned_synthetic_inputs", "real_sandbox_startup", "network_and_mount_isolation",
          "timeout_enforced", "output_total_capacity", "tmp_total_capacity",
          "file_count_capacity", "per_file_kernel_limit", "original_sources_unchanged")


class SandboxBlocked(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def save(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def expected_rlimits(limits: AdapterLimits) -> dict:
    return {"RLIMIT_AS": [limits.memory_bytes] * 2,
            "RLIMIT_CPU": [max(1, int(limits.timeout_seconds)), max(2, int(limits.timeout_seconds) + 1)],
            "RLIMIT_FSIZE": [limits.file_bytes] * 2, "RLIMIT_NPROC": [limits.processes] * 2,
            "RLIMIT_CORE": [0, 0]}


def bootstrap(sandbox: BubblewrapSandbox, spec: AdapterSpec, root: Path) -> dict:
    """Capture startup diagnostics; execute the *same* production mount plan."""
    if not sandbox.executable.is_file():
        raise SandboxBlocked("/usr/bin/bwrap is missing; no fallback was attempted")
    job = root / "bootstrap"
    for directory in ("inputs", "tool", "output", "work/tmp"):
        (job / directory).mkdir(parents=True)
    startup = replace(spec, command=("/usr/bin/python3", "-I", "-S", "-c",
                                    "print('REAL_BUBBLEWRAP_STARTED')"))
    plan = sandbox.plan(startup, job)
    try:
        result = subprocess.run(plan.argv, cwd=plan.cwd, env=plan.env, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SandboxBlocked(f"Real bubblewrap startup unavailable: {type(error).__name__}") from error
    if result.returncode != 0 or result.stdout.strip() != "REAL_BUBBLEWRAP_STARTED":
        diagnostic = result.stderr.strip()[:4000] or "Expected sandbox startup marker was absent"
        raise SandboxBlocked(f"Real bubblewrap startup blocked (exit {result.returncode}): {diagnostic}")
    return {"backend": plan.isolation, "executed": True}


def host_listener_reachable(listener: socket.socket) -> None:
    with socket.create_connection(listener.getsockname(), timeout=2):
        connection, _ = listener.accept()
        connection.close()


def run_probe(report: dict, report_path: Path) -> None:
    active = CHECKS[0]

    def passed(name: str, **details) -> None:
        report["checks"][name] = {"status": "PASS", **details}
        save(report_path, report)
        print(f"PASS {name}", flush=True)

    try:
        with tempfile.TemporaryDirectory(prefix="resource-pipeline-sandbox-") as temporary:
            root = Path(temporary)
            server = root / "server.txt"
            server.write_bytes(SYNTHETIC_SERVER)
            sources = SourceInputs("0.0.1", server)
            unknown_job = root / "unknown-job"
            try:
                TrustedAdapterRunner(AdapterRegistry()).run("uploaded-command.exe", sources, unknown_job)
            except PipelineError as error:
                require("raw game execution is disabled" in str(error), "Unexpected unknown-adapter rejection")
            else:
                raise AssertionError("Default registry accepted an unregistered command")
            require(not unknown_job.exists(), "Unknown adapter created an execution workspace")
            passed(active, execution_started=False)

            active = "prepare_pinned_synthetic_inputs"
            limits = AdapterLimits(timeout_seconds=10)
            tool = root / "installed-original-synthetic-tool"
            tool.mkdir()
            fixture = Path(__file__).resolve().parents[1] / "tests/fixtures/sandbox/producer.py"
            shutil.copyfile(fixture, tool / "producer.py")
            write_synthetic_package(tool / "fixture")
            client = root / "client"
            client.mkdir()
            (client / "client.txt").write_text("Original synthetic client input\n")
            metadata = root / "metadata"
            metadata.mkdir()
            (metadata / "source-binding.json").write_bytes(canonical_json({
                "game_version": "0.0.1", "server_sha256": sha256(SYNTHETIC_SERVER),
                "client_sha256": tree_digest(client)}))
            sources = replace(sources, client=client, metadata=metadata)
            sentinel = root / "host-only-sentinel.txt"
            sentinel.write_text("Original synthetic host-only marker; not a credential\n")
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.bind(("127.0.0.1", 0))
                listener.listen(4)
                listener.settimeout(2)
                host_listener_reachable(listener)
                save(tool / "probe-config.json", {
                    "host_listener_port": listener.getsockname()[1], "host_sentinel": str(sentinel),
                    "host_namespaces": {name: os.readlink(f"/proc/self/ns/{name}")
                                        for name in ("net", "mnt", "pid", "user")},
                    "expected_rlimits": expected_rlimits(limits)})
                spec = AdapterSpec("ci-original-synthetic-producer", "1", synthetic_profile(), tool,
                                   tree_digest(tool), ("/usr/bin/python3", "-I", "-S", "-B", "/tool/producer.py", "isolation"),
                                   frozenset({sha256(SYNTHETIC_SERVER)}), frozenset({tree_digest(client)}),
                                   frozenset({tree_digest(metadata)}), limits=limits)
                passed(active, synthetic_only=True, tool_sha256=spec.tool_sha256)
                sandbox = BubblewrapSandbox()
                active = "real_sandbox_startup"
                passed(active, **bootstrap(sandbox, spec, root))
                initial = {"tool": tree_digest(tool), "client": tree_digest(client),
                           "metadata": tree_digest(metadata), "server": sha256(server.read_bytes())}

                def execute(mode: str, run_limits: AdapterLimits = limits):
                    registry = AdapterRegistry()
                    registry.register(replace(spec, command=(*spec.command[:-1], mode), limits=run_limits))
                    try:
                        return TrustedAdapterRunner(registry, sandbox).run(spec.adapter_id, sources, root / mode)
                    except PipelineError:
                        diagnostic = root / mode / "work/probe-error.json"
                        if diagnostic.is_file():
                            report.setdefault("synthetic_fixture_diagnostics", {})[mode] = json.loads(diagnostic.read_text())
                        raise

                active = "network_and_mount_isolation"
                old_env = os.environ.get("RESOURCE_PIPELINE_SYNTHETIC_SECRET")
                os.environ["RESOURCE_PIPELINE_SYNTHETIC_SECRET"] = "original-synthetic-marker-only"
                try:
                    result = execute("isolation")
                finally:
                    if old_env is None:
                        os.environ.pop("RESOURCE_PIPELINE_SYNTHETIC_SECRET", None)
                    else:
                        os.environ["RESOURCE_PIPELINE_SYNTHETIC_SECRET"] = old_env
                require(result.isolation == "bubblewrap-unshare-all", "A substitute backend was used")
                require(result.coverage["complete"] and result.coverage["submanifestCount"] == 110,
                        "Real producer did not return a validated synthetic package")
                evidence = json.loads((result.package_root / "isolation-probe.json").read_text())
                host_listener_reachable(listener)
                require((root / "isolation/work/tmp/writable-probe.txt").is_file(),
                        "Sandbox /tmp did not map to the watchdog-covered work directory")
                passed(active, isolation=result.isolation, synthetic_subclaims=110,
                       output_sha256=result.output_sha256, evidence=evidence)

                def rejected(mode: str, run_limits: AdapterLimits, message: str) -> tuple[Path, float]:
                    started = time.monotonic()
                    try:
                        execute(mode, run_limits)
                    except PipelineError as error:
                        require(message in str(error), f"{mode}: unexpected failure: {error}")
                    else:
                        raise AssertionError(f"{mode}: prohibited run was approved")
                    elapsed = time.monotonic() - started
                    job = root / mode
                    require((job / "work/started.json").is_file(), f"{mode}: synthetic tool never started")
                    require(not (job / "result.json").exists(), f"{mode}: failed output has approval metadata")
                    require(json.loads((job / "status.json").read_text()) == {"state": "BLOCKED", "publishable": False},
                            f"{mode}: missing fail-closed status")
                    return job, elapsed

                active = "timeout_enforced"
                job, elapsed = rejected("timeout", replace(limits, timeout_seconds=1.5), "timed out")
                require(elapsed < 8, "Timeout did not return within its bounded tolerance")
                heartbeat = (job / "work/heartbeat.txt").read_bytes()
                time.sleep(0.2)
                require((job / "work/heartbeat.txt").read_bytes() == heartbeat,
                        "Timed-out tool was still writing after runner termination")
                passed(active, configured_seconds=1.5, observed_seconds=round(elapsed, 3),
                       writes_stopped=True, publishable=False)

                for mode, check in (("output-bytes", "output_total_capacity"), ("tmp-bytes", "tmp_total_capacity")):
                    active = check
                    _, elapsed = rejected(mode, replace(limits, total_bytes=256 * 1024), "capacity")
                    passed(active, total_bytes=256 * 1024, attempted_bytes=384 * 1024,
                           observed_seconds=round(elapsed, 3), publishable=False)

                active = "file_count_capacity"
                _, elapsed = rejected("file-count", replace(limits, files=512), "capacity")
                passed(active, path_limit=512, observed_seconds=round(elapsed, 3), publishable=False)

                active = "per_file_kernel_limit"
                maximum = 128 * 1024
                job, elapsed = rejected("file-size", replace(limits, file_bytes=maximum), "no output was approved")
                require(json.loads((job / "work/file-limit.json").read_text())["errno"] == errno.EFBIG,
                        "Kernel did not return EFBIG for the per-file limit")
                size = (job / "output/over-file-budget.bin").stat().st_size
                require(size <= maximum, "File grew beyond its kernel-enforced bound")
                passed(active, maximum_bytes=maximum, observed_bytes=size,
                       observed_seconds=round(elapsed, 3), errno=errno.EFBIG, publishable=False)

                active = "original_sources_unchanged"
                final = {"tool": tree_digest(tool), "client": tree_digest(client),
                         "metadata": tree_digest(metadata), "server": sha256(server.read_bytes())}
                require(initial == final, "Original source/tool content changed")
                passed(active, content_hashes=final)
        report["status"] = "PASS"
    except SandboxBlocked as error:
        report["status"] = "BLOCKED"
        report["checks"][active] = {"status": "BLOCKED", "reason": str(error)}
    except Exception as error:
        report["status"] = "FAIL"
        report["checks"][active] = {"status": "FAIL", "reason": f"{type(error).__name__}: {error}"}
    finally:
        save(report_path, report)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=Path("test-results/sandbox/report.json"))
    args = parser.parse_args()
    report = {"schema_version": 1, "status": "RUNNING", "synthetic_only": True,
              "environment": {"platform": platform.platform(), "python": platform.python_version(),
                              "uid": os.getuid() if hasattr(os, "getuid") else None},
              "checks": {name: {"status": "NOT_RUN"} for name in CHECKS},
              "scope": "Production BubblewrapSandbox with original synthetic data; no real game certification"}
    if Path("/usr/bin/bwrap").is_file():
        try:
            version = subprocess.run(("/usr/bin/bwrap", "--version"), capture_output=True,
                                     text=True, timeout=5, check=False)
            report["environment"]["bubblewrap_version"] = version.stdout.strip()[:200]
        except (OSError, subprocess.TimeoutExpired):
            report["environment"]["bubblewrap_version"] = "unavailable"
    for key in ("ImageOS", "ImageVersion", "GITHUB_SHA", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"):
        if key in os.environ:
            report["environment"][key] = os.environ[key]
    run_probe(report, args.report)
    print(json.dumps(report, indent=2, sort_keys=True), flush=True)
    return {"PASS": 0, "FAIL": 1, "BLOCKED": 2}[report["status"]]


if __name__ == "__main__":
    sys.exit(main())
