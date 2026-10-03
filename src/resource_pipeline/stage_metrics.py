"""Bounded stage telemetry; no uploaded strings, paths, or source data in receipts.

RSS peaks are sampled, not allocation traces or exact cgroup high-water marks.
The process-tree sum includes live descendants and may count shared pages twice.
getrusage high-water values are explicitly lifetime values, never stage deltas.
"""
from __future__ import annotations

from contextlib import contextmanager
import math
import os
from pathlib import Path
import sys
import threading
import time
from typing import Callable

try:
    import resource
except ImportError:  # The resource module is unavailable on Windows.
    resource = None

STAGES = frozenset({
    "server_verify", "server_unpack", "client_verify", "client_unpack",
    "server_textures", "client_textures", "semantic_producer",
})
MAX_PROCESSES = 128
MAX_TASKS = 256
MAX_STAGES = 32


def _bounded_read(path: Path, limit: int) -> bytes:
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Process metadata exceeds telemetry bound")
    return data


def _linux_memory(proc: Path = Path("/proc"), pid: int | None = None) -> dict:
    """Sample bounded live-process RSS, including children of worker threads.

    Reading only /proc/PID/task/PID/children misses subprocesses spawned by the
    FastAPI worker thread. Enumerate bounded task directories for each process.
    Exited/inaccessible children make the tree sample incomplete, not zero.
    """
    pid = os.getpid() if pid is None else pid
    page_size = os.sysconf("SC_PAGE_SIZE")
    pending, seen = [pid], set()
    process_rss = None
    tree_rss = 0
    complete = True
    tasks = 0
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        if len(seen) >= MAX_PROCESSES:
            complete = False
            break
        seen.add(current)
        directory = proc / str(current)
        try:
            values = _bounded_read(directory / "statm", 256).split()
            pages = int(values[1])
            if not 0 <= pages <= (2**63 - 1) // page_size:
                raise ValueError("Invalid RSS counter")
            rss = pages * page_size
            if current == pid:
                process_rss = rss
            tree_rss += rss
            with os.scandir(directory / "task") as entries:
                for entry in entries:
                    tasks += 1
                    if tasks > MAX_TASKS:
                        complete = False
                        break
                    if not entry.name.isascii() or not entry.name.isdecimal():
                        complete = False
                        continue
                    children = _bounded_read(Path(entry.path) / "children", 4096).split()
                    for raw in children:
                        if not raw.isdigit() or len(raw) > 10 or int(raw) <= 0:
                            raise ValueError("Invalid child process counter")
                        child = int(raw)
                        if child not in seen and child not in pending:
                            if len(seen) + len(pending) >= MAX_PROCESSES:
                                complete = False
                                continue
                            pending.append(child)
        except (OSError, ValueError, IndexError):
            complete = False
    return {
        "rssBytes": process_rss,
        "processTreeRssBytes": tree_rss if process_rss is not None else None,
        "processTreeComplete": complete and process_rss is not None,
    }


def _usage() -> dict:
    if resource is None:
        # process_time measures this process's user + system CPU on Windows as
        # well as Unix. It does not include children or provide RSS counters.
        return {
            "cpuMs": time.process_time() * 1000,
            "waitedChildrenCpuMs": None,
            "processLifetimePeakRssBytes": None,
            "waitedChildrenLifetimePeakRssBytes": None,
        }
    own = resource.getrusage(resource.RUSAGE_SELF)
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    # ru_maxrss is bytes on Darwin and KiB on Linux. Unknown OS units are not
    # guessed. The production worker is Linux amd64.
    factor = 1 if sys.platform == "darwin" else 1024 if sys.platform.startswith("linux") else None
    return {
        "cpuMs": (own.ru_utime + own.ru_stime) * 1000,
        "waitedChildrenCpuMs": (children.ru_utime + children.ru_stime) * 1000,
        "processLifetimePeakRssBytes": int(own.ru_maxrss * factor) if factor else None,
        "waitedChildrenLifetimePeakRssBytes": int(children.ru_maxrss * factor) if factor else None,
    }


class StageMetrics:
    """One serial raw job. Sampling storage is O(stages), not O(samples)."""
    def __init__(self, interval_seconds: float = 0.05, *,
                 memory_reader: Callable[[], dict] | None = None,
                 usage_reader: Callable[[], dict] = _usage,
                 clock: Callable[[], float] = time.perf_counter):
        if (isinstance(interval_seconds, bool) or not isinstance(interval_seconds, (int, float))
                or not math.isfinite(interval_seconds) or not 0.01 <= interval_seconds <= 1):
            raise ValueError("Telemetry interval must be between 10 and 1000 ms")
        self.interval = float(interval_seconds)
        self._memory = memory_reader or (_linux_memory if sys.platform.startswith("linux") else
                                        lambda: {"rssBytes": None, "processTreeRssBytes": None,
                                                 "processTreeComplete": False})
        self._usage = usage_reader
        self._clock = clock
        self._started = clock()
        self._stages: list[dict] = []
        self._active = False
        self._finished: float | None = None
        self._result: dict | None = None

    @staticmethod
    def _counter(value) -> bool:
        return type(value) is int and 0 <= value <= 2**63 - 1

    def _sample(self, row: dict) -> None:
        try:
            sample = self._memory()
            valid = True
            for source, target in (("rssBytes", "rssPeakBytes"),
                                   ("processTreeRssBytes", "processTreeRssPeakBytes")):
                value = sample.get(source)
                if self._counter(value):
                    row[target] = max(row[target] or 0, value)
                else:
                    valid = False
            row["sampleCount"] += 1
            row["processTreeSamplingComplete"] &= sample.get("processTreeComplete") is True and valid
            if not valid:
                row["sampleErrors"] += 1
        except Exception:
            # Telemetry must not replace the original decoder/cancellation error
            # and must never copy a potentially sensitive exception message.
            row["sampleErrors"] += 1
            row["processTreeSamplingComplete"] = False

    def _read_usage(self) -> dict:
        try:
            return self._usage()
        except Exception:
            return {key: None for key in ("cpuMs", "waitedChildrenCpuMs",
                    "processLifetimePeakRssBytes", "waitedChildrenLifetimePeakRssBytes")}

    @contextmanager
    def stage(self, name: str):
        if name not in STAGES:
            raise ValueError("Unknown raw extraction stage")
        if self._active or self._finished is not None or len(self._stages) >= MAX_STAGES:
            raise RuntimeError("Stage metrics are serial, bounded, and single-use")
        self._active = True
        row = {"stage": name, "status": "SUCCEEDED", "durationMs": 0,
               "sampleCount": 0, "sampleErrors": 0, "rssPeakBytes": None,
               "processTreeRssPeakBytes": None, "processTreeSamplingComplete": True}
        stop = threading.Event()
        worker = None
        started = self._clock()
        before = self._read_usage()
        try:
            self._sample(row)
            def sample_loop():
                while not stop.wait(self.interval):
                    self._sample(row)
            worker = threading.Thread(target=sample_loop, name="raw-stage-metrics", daemon=True)
            try:
                worker.start()
            except RuntimeError:
                row["sampleErrors"] += 1
                row["processTreeSamplingComplete"] = False
            try:
                yield
            except BaseException as exc:
                row["status"] = "FAILED" if isinstance(exc, Exception) else "INTERRUPTED"
                raise
        finally:
            stop.set()
            if worker is not None and worker.ident is not None:
                worker.join()
            self._sample(row)
            ended = self._clock()
            after = self._read_usage()
            row["durationMs"] = max(0, round((ended - started) * 1000))
            for key in ("cpuMs", "waitedChildrenCpuMs"):
                a, b = after.get(key), before.get(key)
                row[key] = (max(0, round(a - b)) if type(a) in (float, int) and type(b) in (float, int)
                            and math.isfinite(a) and math.isfinite(b) else None)
            for key in ("processLifetimePeakRssBytes", "waitedChildrenLifetimePeakRssBytes"):
                value = after.get(key)
                row[key] = value if self._counter(value) else None
            self._stages.append(row)
            self._active = False

    def finish(self) -> dict:
        if self._active:
            raise RuntimeError("Cannot finish metrics during an active stage")
        if self._result is None:
            self._finished = self._clock()
            usage = self._read_usage()
            self._result = {
                "schemaVersion": 1,
                "memoryMethod": "sampled-live-process-tree-rss",
                "samplerIntervalMs": round(self.interval * 1000),
                "durationMs": max(0, round((self._finished - self._started) * 1000)),
                "stages": [dict(row) for row in self._stages],
            }
            for key in ("processLifetimePeakRssBytes", "waitedChildrenLifetimePeakRssBytes"):
                value = usage.get(key)
                self._result[key] = value if self._counter(value) else None
        return {**self._result, "stages": [dict(row) for row in self._result["stages"]]}

    @contextmanager
    def persist(self, job: dict, save: Callable[[dict], None]):
        """Use inside the job lock, so terminal/error receipts retain metrics."""
        try:
            yield self
        finally:
            job["metrics"] = self.finish()
            save(job)
