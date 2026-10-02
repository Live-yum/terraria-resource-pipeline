"""Original CI-only producer. This file must run inside real bubblewrap only.

It uses only the system Python standard library and original synthetic data.
No uploaded executable, game assembly, private repository, or external service
is used. The host probe registers and pins this directory before each run.
"""
from __future__ import annotations

import argparse
import errno
import json
import os
from pathlib import Path
import resource
import shutil
import socket
import sys
import time


def save(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def readonly(path: Path, *, directory: bool = False) -> int:
    """Check the mount, rather than merely a chmod(0400) source permission."""
    try:
        if directory:
            (path / "sandbox-probe-must-not-exist").mkdir()
        else:
            path.chmod(0o600)
    except OSError as error:
        require(error.errno == errno.EROFS, f"{path}: expected read-only mount, got {error.errno}")
        return error.errno
    raise RuntimeError(f"Read-only mount unexpectedly writable: {path}")


def check_isolation(output: Path) -> dict:
    config = json.loads(Path("/tool/probe-config.json").read_text())
    namespaces = {name: os.readlink(f"/proc/self/ns/{name}") for name in ("net", "mnt", "pid", "user")}
    require(all(value != config["host_namespaces"][name] for name, value in namespaces.items()),
            "Sandbox unexpectedly shares a host namespace")
    interfaces = [name for _, name in socket.if_nameindex()]
    require(set(interfaces) <= {"lo"}, "Unexpected external network interface")
    routes = Path("/proc/net/route").read_text().splitlines()[1:]
    require(not any(line.strip() for line in routes), "Unexpected IPv4 route")
    try:
        with socket.create_connection(("127.0.0.1", config["host_listener_port"]), timeout=0.5):
            pass
    except OSError as error:
        loopback_errno = error.errno
    else:
        raise RuntimeError("Sandbox reached the host-only TCP listener")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as outgoing:
        try:
            # TEST-NET-1, without sending any packet or using DNS/public services.
            outgoing.connect(("192.0.2.1", 9))
        except OSError as error:
            require(error.errno == errno.ENETUNREACH, "Unexpected network failure")
            outbound_errno = error.errno
        else:
            raise RuntimeError("Sandbox has an outbound route")
    denied = {str(path): readonly(path, directory=True)
              for path in map(Path, ("/", "/usr", "/inputs", "/tool", "/dev"))}
    denied.update({str(path): readonly(path) for path in map(Path, (
        "/inputs/server.bin", "/inputs/request.json", "/inputs/client/client.txt",
        "/inputs/metadata/source-binding.json", "/tool/producer.py"))})
    require(not Path(config["host_sentinel"]).exists(), "Host-only sentinel is exposed")
    require(not any(Path(path).exists() for path in ("/home", "/root", "/workspace")),
            "Unexpected host filesystem root is exposed")
    require("RESOURCE_PIPELINE_SYNTHETIC_SECRET" not in os.environ, "Host environment leaked")
    for root in (output, Path("/work"), Path("/tmp")):
        (root / "writable-probe.txt").write_text("original synthetic writable evidence\n")
    observed_limits = {name: list(resource.getrlimit(getattr(resource, name))) for name in (
        "RLIMIT_AS", "RLIMIT_CPU", "RLIMIT_FSIZE", "RLIMIT_NPROC", "RLIMIT_CORE")}
    require(observed_limits == config["expected_rlimits"], "POSIX resource limits differ")
    return {"namespaces": namespaces, "interfaces": interfaces, "ipv4_routes": routes,
            "host_loopback_connect_errno": loopback_errno, "outbound_route_errno": outbound_errno,
            "read_only_mount_errnos": denied, "host_sentinel_hidden": True,
            "host_environment_hidden": True, "writable_roots": ["/output", "/work", "/tmp"],
            "rlimits": observed_limits}


def normalized_fixture(request: dict, output: Path) -> None:
    shutil.copytree("/tool/fixture", output, dirs_exist_ok=True, copy_function=shutil.copyfile)
    coverage = json.loads((output / "coverage.json").read_text())
    coverage["binding"] = request["binding"]
    save(output / "coverage.json", coverage)
    bundle = json.loads((output / "resource-bundle.json").read_text())
    bundle["adapter"] = request["binding"]["adapter_id"]
    bundle["sources"] = [{"role": role, "game_version": request["binding"]["game_version"],
                          "sha256": request["binding"][role + "_sha256"]}
                         for role in ("server", "client", "metadata")
                         if request["binding"][role + "_sha256"] is not None]
    save(output / "resource-bundle.json", bundle)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("isolation", "timeout", "output-bytes", "tmp-bytes", "file-count", "file-size"))
    parser.add_argument("--request", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    request = json.loads(Path(args.request).read_text())
    output = Path(args.output)
    save(Path("/work/started.json"), {"mode": args.mode})
    if args.mode == "isolation":
        evidence = check_isolation(output)
        normalized_fixture(request, output)
        save(output / "isolation-probe.json", evidence)
        return
    if args.mode == "timeout":
        while True:
            Path("/work/heartbeat.txt").write_text(str(time.monotonic_ns()))
            time.sleep(0.05)
    if args.mode in ("output-bytes", "tmp-bytes"):
        root = output if args.mode == "output-bytes" else Path("/tmp")
        (root / "over-total-budget.bin").write_bytes(b"x" * (384 * 1024))
    elif args.mode == "file-count":
        for index in range(520):
            (output / f"count-{index:04}.txt").touch()
    elif args.mode == "file-size":
        try:
            with (output / "over-file-budget.bin").open("wb", buffering=0) as target:
                for _ in range(8):
                    target.write(b"x" * (64 * 1024))
        except OSError as error:
            save(Path("/work/file-limit.json"), {"errno": error.errno})
            sys.exit(23)
        raise RuntimeError("Per-file POSIX limit did not reject the oversized write")
    # Leave time for the production watchdog to observe each exceeded budget.
    time.sleep(10)
    raise RuntimeError("Production watchdog did not terminate oversized output")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # CI-only diagnostics for this original fixture. Production runner
        # stderr remains discarded and is never exposed through the web API.
        save(Path("/work/probe-error.json"), {"type": type(error).__name__, "message": str(error)})
        raise
