import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from resource_pipeline.stage_metrics import StageMetrics, _linux_memory, MAX_STAGES


def usage(own=9000, children=7000, cpu=1):
    return {"cpuMs": cpu, "waitedChildrenCpuMs": cpu,
            "processLifetimePeakRssBytes": own,
            "waitedChildrenLifetimePeakRssBytes": children}


def memory(rss=1024, tree=2048, complete=True):
    return {"rssBytes": rss, "processTreeRssBytes": tree, "processTreeComplete": complete}


class StageMetricsTests(unittest.TestCase):
    def test_stage_peak_is_not_the_lifetime_high_water(self):
        samples = iter([memory(200, 500), memory(100, 300)])
        meter = StageMetrics(memory_reader=lambda: next(samples), usage_reader=usage)
        with meter.stage("server_unpack"):
            pass
        row = meter.finish()["stages"][0]
        self.assertEqual(row["rssPeakBytes"], 200)
        self.assertEqual(row["processTreeRssPeakBytes"], 500)
        self.assertEqual(row["processLifetimePeakRssBytes"], 9000)
        self.assertEqual(row["waitedChildrenLifetimePeakRssBytes"], 7000)
        self.assertEqual(row["sampleCount"], 2)
        self.assertTrue(row["processTreeSamplingComplete"])

    def test_second_stage_does_not_inherit_first_stage_sample_peak(self):
        current = [memory(800, 900)]
        meter = StageMetrics(memory_reader=lambda: current[0], usage_reader=usage)
        with meter.stage("server_verify"):
            pass
        current[0] = memory(200, 300)
        with meter.stage("client_verify"):
            pass
        first, second = meter.finish()["stages"]
        self.assertEqual(first["rssPeakBytes"], 800)
        self.assertEqual(second["rssPeakBytes"], 200)
        self.assertEqual(second["processLifetimePeakRssBytes"], 9000)

    def test_cpu_and_duration_are_deltas(self):
        readings = iter([usage(cpu=12), usage(cpu=19), usage(cpu=25)])
        clock = iter([1.0, 1.1, 1.25, 1.3])
        meter = StageMetrics(memory_reader=memory, usage_reader=lambda: next(readings), clock=lambda: next(clock))
        with meter.stage("client_unpack"):
            pass
        result = meter.finish()
        self.assertEqual(result["durationMs"], 300)
        self.assertEqual(result["stages"][0]["durationMs"], 150)
        self.assertEqual(result["stages"][0]["cpuMs"], 7)

    def test_exception_is_preserved_without_private_text(self):
        meter = StageMetrics(memory_reader=memory, usage_reader=usage)
        secret = ValueError("/private/source.zip secret")
        with self.assertRaises(ValueError) as error:
            with meter.stage("semantic_producer"):
                raise secret
        self.assertIs(error.exception, secret)
        result = meter.finish()
        self.assertEqual(result["stages"][0]["status"], "FAILED")
        self.assertNotIn("secret", json.dumps(result))
        self.assertNotIn("/private", json.dumps(result))

    def test_interrupt_is_recorded_and_reraised(self):
        meter = StageMetrics(memory_reader=memory, usage_reader=usage)
        with self.assertRaises(KeyboardInterrupt):
            with meter.stage("server_textures"):
                raise KeyboardInterrupt()
        self.assertEqual(meter.finish()["stages"][0]["status"], "INTERRUPTED")

    def test_missing_rss_is_not_fabricated_as_zero(self):
        meter = StageMetrics(memory_reader=lambda: memory(None, None, False), usage_reader=usage)
        with meter.stage("client_textures"):
            pass
        row = meter.finish()["stages"][0]
        self.assertIsNone(row["rssPeakBytes"])
        self.assertIsNone(row["processTreeRssPeakBytes"])
        self.assertEqual(row["sampleErrors"], 2)
        self.assertFalse(row["processTreeSamplingComplete"])

    def test_failed_reader_does_not_break_operation(self):
        def fail():
            raise OSError("private error text")
        meter = StageMetrics(memory_reader=fail, usage_reader=fail)
        with meter.stage("server_verify"):
            pass
        row = meter.finish()["stages"][0]
        self.assertEqual(row["status"], "SUCCEEDED")
        self.assertIsNone(row["cpuMs"])
        self.assertIsNone(row["processLifetimePeakRssBytes"])
        self.assertEqual(row["sampleErrors"], 2)
        self.assertNotIn("private", json.dumps(row))

    def test_invalid_rss_types_are_not_receipts(self):
        for value in (-1, True, float("nan"), "100", 2**63):
            with self.subTest(value=value):
                meter = StageMetrics(memory_reader=lambda: memory(value, value), usage_reader=usage)
                with meter.stage("server_verify"):
                    pass
                row = meter.finish()["stages"][0]
                self.assertIsNone(row["rssPeakBytes"])
                self.assertFalse(row["processTreeSamplingComplete"])

    def test_stage_name_cannot_contain_uploaded_text(self):
        meter = StageMetrics()
        with self.assertRaises(ValueError):
            with meter.stage("/private/game.zip"):
                pass

    def test_nested_stages_are_rejected_without_leaving_worker(self):
        meter = StageMetrics(memory_reader=memory, usage_reader=usage)
        with meter.stage("server_verify"):
            with self.assertRaises(RuntimeError):
                with meter.stage("client_verify"):
                    pass
        self.assertEqual(len(meter.finish()["stages"]), 1)

    def test_finish_is_stable_and_defensively_copied(self):
        meter = StageMetrics(memory_reader=memory, usage_reader=usage)
        with meter.stage("server_verify"):
            pass
        expected = meter.finish()
        modified = meter.finish()
        modified["stages"][0]["rssPeakBytes"] = 0
        modified["stages"].clear()
        self.assertEqual(meter.finish(), expected)
        with self.assertRaises(RuntimeError):
            with meter.stage("client_verify"):
                pass

    def test_cannot_finish_an_active_stage(self):
        meter = StageMetrics(memory_reader=memory, usage_reader=usage)
        with meter.stage("server_verify"):
            with self.assertRaises(RuntimeError):
                meter.finish()

    def test_stage_count_is_bounded(self):
        meter = StageMetrics(memory_reader=memory, usage_reader=usage)
        for _ in range(MAX_STAGES):
            with meter.stage("server_verify"):
                pass
        with self.assertRaises(RuntimeError):
            with meter.stage("server_verify"):
                pass

    def test_invalid_intervals(self):
        for value in (0, -1, 0.001, 1.1, True, "0.05", float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                StageMetrics(value)

    def test_persist_retains_metrics_after_error(self):
        meter = StageMetrics(memory_reader=memory, usage_reader=usage)
        job, saved = {"state": "EXTRACTING"}, []
        with self.assertRaises(ValueError):
            with meter.persist(job, lambda value: saved.append(dict(value))):
                with meter.stage("server_unpack"):
                    raise ValueError("decode rejected")
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["metrics"]["stages"][0]["status"], "FAILED")

    def test_sampler_runs_and_stops_without_recording_a_sample_history(self):
        called = threading.Event()
        def read():
            if threading.current_thread().name == "raw-stage-metrics":
                called.set()
            return memory()
        meter = StageMetrics(0.01, memory_reader=read, usage_reader=usage)
        with meter.stage("server_verify"):
            self.assertTrue(called.wait(2))
        result = meter.finish()
        self.assertGreaterEqual(result["stages"][0]["sampleCount"], 3)
        self.assertNotIn("samples", result["stages"][0])
        self.assertFalse(any(t.name == "raw-stage-metrics" for t in threading.enumerate()))

    def test_thread_start_failure_is_observable_not_a_job_failure(self):
        meter = StageMetrics(memory_reader=memory, usage_reader=usage)
        with patch("threading.Thread.start", side_effect=RuntimeError("no thread capacity")):
            with meter.stage("server_verify"):
                pass
        row = meter.finish()["stages"][0]
        self.assertEqual(row["status"], "SUCCEEDED")
        self.assertEqual(row["sampleErrors"], 1)
        self.assertFalse(row["processTreeSamplingComplete"])


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux procfs measurements")
class LinuxMemoryTests(unittest.TestCase):
    def make_proc(self, root, pid, rss, task_children):
        directory = root / str(pid)
        directory.mkdir(parents=True)
        (directory / "statm").write_text(f"10000 {rss} 1 0 0 0 0\n")
        for tid, children in task_children.items():
            task = directory / "task" / str(tid)
            task.mkdir(parents=True)
            (task / "children").write_text(" ".join(map(str, children)))

    def test_includes_child_created_by_a_non_main_worker_thread(self):
        with tempfile.TemporaryDirectory() as temporary:
            proc = Path(temporary)
            self.make_proc(proc, 101, 10, {101: [], 102: [201]})
            self.make_proc(proc, 201, 20, {201: [301]})
            self.make_proc(proc, 301, 30, {301: []})
            measured = _linux_memory(proc, 101)
            self.assertEqual(measured["rssBytes"], 10 * os.sysconf("SC_PAGE_SIZE"))
            self.assertEqual(measured["processTreeRssBytes"], 60 * os.sysconf("SC_PAGE_SIZE"))
            self.assertTrue(measured["processTreeComplete"])

    def test_missing_child_is_marked_incomplete(self):
        with tempfile.TemporaryDirectory() as temporary:
            proc = Path(temporary)
            self.make_proc(proc, 101, 10, {101: [999]})
            result = _linux_memory(proc, 101)
            self.assertFalse(result["processTreeComplete"])
            self.assertEqual(result["processTreeRssBytes"], result["rssBytes"])

    def test_excessive_children_are_bounded(self):
        with tempfile.TemporaryDirectory() as temporary:
            proc = Path(temporary)
            self.make_proc(proc, 101, 10, {101: list(range(1000, 1200))})
            with patch("resource_pipeline.stage_metrics.MAX_PROCESSES", 2):
                result = _linux_memory(proc, 101)
            self.assertFalse(result["processTreeComplete"])

    def test_excessive_threads_are_bounded(self):
        with tempfile.TemporaryDirectory() as temporary:
            proc = Path(temporary)
            self.make_proc(proc, 101, 10, {101: [], 102: []})
            with patch("resource_pipeline.stage_metrics.MAX_TASKS", 1):
                self.assertFalse(_linux_memory(proc, 101)["processTreeComplete"])

    def test_malformed_or_large_proc_metadata_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            proc = Path(temporary)
            self.make_proc(proc, 101, 10, {101: []})
            for content in ("bad", "1 -1", "1 " + "9" * 260):
                (proc / "101" / "statm").write_text(content)
                self.assertIsNone(_linux_memory(proc, 101)["rssBytes"])

    def test_real_linux_process_can_be_sampled(self):
        result = _linux_memory()
        self.assertGreater(result["rssBytes"], 0)
        self.assertGreaterEqual(result["processTreeRssBytes"], result["rssBytes"])


if __name__ == "__main__":
    unittest.main()
