import gc
import hashlib
import json
import math
import os
import platform
import statistics
import subprocess
import tempfile
import time
import tracemalloc
import unittest
from pathlib import Path

from lib.ui.log_filter import apply_log_filter, parse_filter_expression
from lib.ui.log_reader import BoundedLogReader, MAX_READ_BYTES


@unittest.skipUnless(os.environ.get("RUN_PHASE3_PERF") == "1", "set RUN_PHASE3_PERF=1")
class Phase3LogPerformanceTests(unittest.TestCase):
    TARGET_BYTES = 100 * 1024 * 1024
    MEASURED_ITERATIONS = 30

    @staticmethod
    def _git_value(*args):
        return subprocess.check_output(["git", *args], text=True).strip()

    def _generate_fixture(self, path):
        digest = hashlib.sha256()
        written = 0
        sequence = 0
        minimum_line = None
        maximum_line = 0
        chunk = bytearray()
        with path.open("wb") as handle:
            while written < self.TARGET_BYTES:
                marker = " drop-me" if sequence % 19 == 0 else ""
                ending = "\r\n" if sequence % 7 == 0 else "\n"
                line = (
                    f"{sequence:010d} alpha beta unicode-日本語 literal-[.*+?]"
                    f" payload={'x' * 115}{marker}{ending}"
                ).encode("utf-8")
                minimum_line = len(line) if minimum_line is None else min(minimum_line, len(line))
                maximum_line = max(maximum_line, len(line))
                chunk.extend(line)
                sequence += 1
                if len(chunk) >= 512 * 1024 or written + len(chunk) >= self.TARGET_BYTES:
                    handle.write(chunk)
                    digest.update(chunk)
                    written += len(chunk)
                    chunk.clear()
            final = f"{sequence:010d} alpha beta final-no-newline 日本語".encode("utf-8")
            handle.write(final)
            digest.update(final)
            written += len(final)
        return {
            "target_bytes": self.TARGET_BYTES,
            "generated_bytes": written,
            "line_count": sequence + 1,
            "line_size_range": [minimum_line, maximum_line],
            "sha256": digest.hexdigest(),
            "chunk_flush_bytes": 512 * 1024,
            "final_line_has_newline": False,
            "fixture_removed": False,
        }

    @staticmethod
    def _run_case(reader, path, max_lines):
        snapshot = reader.read(path, max_lines=max_lines, max_bytes=MAX_READ_BYTES)
        spec = parse_filter_expression("[alpha,beta,!drop-me]")
        result = apply_log_filter(snapshot.lines, spec)
        return snapshot, result

    def test_100_mib_tail_filter_p95_and_memory(self):
        output_path_value = os.environ.get("PHASE3_BENCHMARK_JSON")
        output_path = Path(output_path_value).resolve() if output_path_value else None
        fixture_path = None
        benchmark = {}
        fixture_spec = {}
        with tempfile.TemporaryDirectory() as temp_dir:
            fixture_path = Path(temp_dir) / "phase03-100mib.log"
            fixture_spec = self._generate_fixture(fixture_path)
            reader = BoundedLogReader()
            timing = {}
            for max_lines in (100, 1000, 5000):
                for _ in range(5):
                    self._run_case(reader, fixture_path, max_lines)
                samples = []
                result_line_count = None
                for _ in range(self.MEASURED_ITERATIONS):
                    started = time.perf_counter_ns()
                    snapshot, result = self._run_case(reader, fixture_path, max_lines)
                    samples.append((time.perf_counter_ns() - started) / 1_000_000)
                    result_line_count = len(result.lines)
                ordered = sorted(samples)
                p95 = ordered[math.ceil(0.95 * len(ordered)) - 1]
                timing[str(max_lines)] = {
                    "p95_ms": p95,
                    "median_ms": statistics.median(samples),
                    "min_ms": min(samples),
                    "max_ms": max(samples),
                    "iterations": len(samples),
                    "snapshot_lines": len(snapshot.lines),
                    "filtered_lines": result_line_count,
                }

            tracemalloc.start()
            try:
                for _ in range(20):
                    self._run_case(reader, fixture_path, 5000)
                gc.collect()
                baseline_current, _ = tracemalloc.get_traced_memory()
                for _ in range(200):
                    self._run_case(reader, fixture_path, 5000)
                gc.collect()
                final_current, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()

            memory = {
                "baseline_current_bytes": baseline_current,
                "final_current_bytes": final_current,
                "growth_bytes": final_current - baseline_current,
                "peak_bytes": peak,
                "peak_above_baseline_bytes": peak - baseline_current,
                "iterations": 200,
            }
            benchmark = {
                "fixture": fixture_spec,
                "timing": timing,
                "memory": memory,
                "thresholds": {
                    "p95_5000_ms_lt": 100.0,
                    "current_growth_bytes_lte": 2 * 1024 * 1024,
                    "peak_above_baseline_bytes_lte": 64 * 1024 * 1024,
                },
                "python": platform.python_version(),
                "platform": platform.platform(),
                "branch": self._git_value("branch", "--show-current"),
                "commit": self._git_value("rev-parse", "HEAD"),
            }

        self.assertIsNotNone(fixture_path)
        self.assertFalse(fixture_path.exists())
        fixture_spec["fixture_removed"] = True
        benchmark["fixture"] = fixture_spec
        if output_path:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(benchmark, ensure_ascii=False, indent=2), encoding="utf-8")
            (output_path.parent / "fixture-spec.json").write_text(
                json.dumps(fixture_spec, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        self.assertLess(benchmark["timing"]["5000"]["p95_ms"], 100.0)
        self.assertLessEqual(benchmark["memory"]["growth_bytes"], 2 * 1024 * 1024)
        self.assertLessEqual(benchmark["memory"]["peak_above_baseline_bytes"], 64 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
